"""Restartable, completion-order-independent evolutionary campaign."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from pathlib import Path
import threading
from typing import Any, Mapping, Sequence

from .archive import ArchiveEntry, ParetoArchive
from .analytics import summarize_run
from .canonical import content_hash, source_manifest
from .config import CampaignConfig, ObjectiveConfig
from .evaluator import (
    JOURNEY_GENE_ADAPTERS,
    MISSION_GENE_ADAPTERS,
    AlternativeSeedEvaluator,
    EMTGEvaluator,
    Evaluator,
    RepeatedEvaluator,
    SyntheticEvaluator,
)
from .fidelity import promote_diverse_nondominated
from .genome import GenomeSchema, TopologyError, decode_genotype, random_genotype, stratify_genotype
from .model import (
    CandidateRecord,
    EvaluationRequest,
    EvaluationResult,
    EvaluationStatus,
    ScoredEvaluationResult,
    MissionPhenotype,
    OperatorRecord,
    RepairRecord,
    RepairStatus,
)
from .nsga2 import (
    NSGA2Engine,
    NSGA2Individual,
    exact_hypervolume_2d,
    rank_population,
    tournament_select,
)
from .objectives import (
    ConstraintDefinition,
    ConstraintRegistry,
    ObjectiveDefinition,
    ObjectiveRegistry,
    default_objective_registry,
)
from .operators import OperatorDefinition, OperatorRegistry, default_operator_registry, point_group_mutation
from .physics import C3EnvelopeScreen, HohmannTimeScreen
from .randomness import derive_seed, deterministic_id, random_stream
from .resonance import ResonanceRatio, resonance_metadata, resonance_mutation
from .rules import (
    PointGroup,
    UniverseCatalog,
    attach_point_group_metadata,
    inclination_separation,
    repair_point_groups,
)
from .seeds import (
    ExternalSeedProvider, SeedArtifact, SeedFingerprint, SeedInventory,
    default_converter_registry,
)
from .serde import genotype_from_dict, genotype_to_dict, phenotype_to_dict
from .storage import CampaignStore, EvaluationCache, atomic_write_json
from .workers import LocalWorkerBackend, RetryPolicy, WorkerBackend


@dataclass(frozen=True)
class CampaignOutcome:
    complete: bool
    trial: int
    generation: int
    new_evaluations: int
    archive_size: int
    checkpoint: str


@dataclass(frozen=True)
class EvaluatedCandidate:
    candidate: CandidateRecord
    result: ScoredEvaluationResult
    individual: NSGA2Individual


DEFAULT_OPERATOR_WEIGHTS = {
    "activation": 1.0,
    "insertion": 1.0,
    "deletion": 1.0,
    "flyby_replacement": 2.0,
    "swap": 1.0,
    "timing": 1.0,
    "hardware": 1.0,
    "phase_type": 1.0,
    "dsm_count": 1.0,
    "generic_gene": 1.0,
    "journey_crossover": 1.0,
    "subsequence_crossover": 2.0,
    "phase_crossover": 1.0,
}


class Campaign:
    def __init__(
        self,
        config: CampaignConfig,
        *,
        evaluator: Evaluator | None = None,
        backend: WorkerBackend | None = None,
        objective_registry: ObjectiveRegistry | None = None,
        operator_registry: OperatorRegistry | None = None,
    ):
        self.config = config
        self._source_manifest = source_manifest(Path(__file__).resolve().parents[2])
        self.schema = GenomeSchema(config.search)
        self._active_fidelity = self._fidelity_names()[0]
        self.store = CampaignStore(
            config.run_directory, config.checkpoints.get("directory")
        )
        cache_directory = config.cache.get(
            "directory", config.evaluator.get("cache_directory", config.run_directory / "cache")
        )
        self.cache = EvaluationCache(cache_directory)
        self.evaluator = evaluator or self._make_evaluator()
        self.backend = backend or LocalWorkerBackend(
            config.workers.count,
            RetryPolicy(config.workers.infrastructure_retries),
        )
        self.objectives = objective_registry or default_objective_registry()
        self.constraints = ConstraintRegistry()
        self.operators = operator_registry or default_operator_registry()
        self.engine = NSGA2Engine(config.algorithm)
        self.archives: dict[tuple[str, int, str], ParetoArchive] = {}
        self.cancel_event = threading.Event()
        self._results_since_checkpoint = 0
        self.point_groups = tuple(
            PointGroup.from_dict({key: value for key, value in group.items() if key != "as_constraint"})
            for group in config.groups
        )
        self.group_constraints = {
            str(group["name"])
            for group in config.groups
            if bool(group.get("as_constraint", False)) or group.get("target_role") == "mandatory"
        }
        if self.point_groups and "point_group" not in self.operators.names():
            self.operators.register(
                OperatorDefinition(
                    "point_group",
                    ("flyby",),
                    mutation=lambda schema, genotype, rng: point_group_mutation(
                        schema, genotype, rng, self.point_groups
                    ),
                )
            )
        self._resonance_catalog: UniverseCatalog | None = None
        self._resonance_ratios: tuple[ResonanceRatio, ...] = ()
        if bool(config.resonance.get("enabled", False)):
            ratios = config.resonance.get("ratios", ((1, 1), (2, 1), (3, 2)))
            self._resonance_ratios = tuple(
                ResonanceRatio(int(value[0]), int(value[1])) for value in ratios
            )
            universe_file = config.resonance.get("universe_file")
            if universe_file:
                path = Path(str(universe_file))
                if not path.is_absolute():
                    path = config.source_path.parent / path
                self._resonance_catalog = UniverseCatalog.from_file(path)
            else:
                emtg = self._emtg_evaluator()
                if emtg is None:
                    raise ValueError("resonance extension requires universe_file or an EMTG evaluator")
                central = str(config.resonance.get("central_body", emtg.builder._base.Journeys[emtg.builder.journey_template_index].journey_central_body))
                self._resonance_catalog = emtg.builder.catalog(central)
            self.operators.register(
                OperatorDefinition(
                    "resonance",
                    ("flyby", "resonance"),
                    mutation=lambda schema, genotype, rng: resonance_mutation(
                        schema,
                        genotype,
                        rng,
                        self._resonance_catalog,  # type: ignore[arg-type]
                        self._resonance_ratios,
                        replace_existing=(
                            rng.random() < float(self.config.resonance.get("replacement_probability", 0.0))
                        ),
                    )[0],
                )
            )
        inventory_path = config.seeds.get("inventory")
        self.seed_inventory = SeedInventory.from_file(inventory_path) if inventory_path else SeedInventory()
        discovered, discovery_records = SeedInventory.discover(config.seeds.get("folders", ()))
        for seed in discovered.values():
            self.seed_inventory.add(seed)
        if discovery_records:
            self.store.set_metadata("seed_folder_discovery", list(discovery_records))
        self._harvest_stored_seeds()
        self._register_custom_objectives()
        self._register_constraints()
        self._validate_configuration()
        self._initialize_store()
        for trial in range(self.config.algorithm.trials):
            for fidelity in self._fidelity_names():
                self._active_fidelity = fidelity
                context_id = self._comparison_context_id(trial)
                self.archives[(context_id, trial, fidelity)] = ParetoArchive(
                    ArchiveEntry(result, objectives, generation)
                    for result, objectives, generation in self.store.load_archive(
                        context_id, trial, fidelity
                    )
                )
        self._active_fidelity = self._fidelity_names()[0]

    def _make_evaluator(self) -> Evaluator:
        settings = dict(self.config.evaluator)
        evaluator_type = str(settings.pop("type", "synthetic"))
        settings.pop("cache_directory", None)
        settings.pop("budget", None)
        inner_trials = int(settings.pop("inner_trials", 1))
        if evaluator_type == "synthetic":
            base: Evaluator = SyntheticEvaluator(settings)
            base = RepeatedEvaluator(base, inner_trials) if inner_trials > 1 else base
            return AlternativeSeedEvaluator(base) if int(self.config.seeds.get("candidates", 1)) > 1 else base
        if evaluator_type == "emtg":
            if self.config.base_case is None:
                raise ValueError("EMTG evaluator requires base_case")
            assets = dict(self.config.assets)
            base = EMTGEvaluator(
                base_case=self.config.base_case,
                executable=assets["executable"],
                run_directory=self.config.run_directory,
                timeout_seconds=float(settings.pop("timeout_seconds")),
                universe_folder=assets.get("universe_folder"),
                hardware_path=assets.get("hardware_path"),
                environment=settings.pop("environment", None),
                journey_template_index=int(settings.pop("journey_template_index", 0)),
                expand_phase_genes=bool(settings.pop("expand_phase_genes", True)),
                brief_executable=assets.get("brief_executable"),
                ephemeris_source_override=(
                    int(settings.pop("ephemeris_source_override"))
                    if settings.get("ephemeris_source_override") is not None
                    else None
                ),
                resource_limits=dict(self.config.resources),
                journey_templates=settings.pop(
                    "journey_templates", self.config.templates.get("journeys", {})
                ),
                constraint_migration_allowlist=settings.pop(
                    "constraint_migration_allowlist",
                    self.config.templates.get("constraint_migration_allowlist", ()),
                ),
            )
            base = RepeatedEvaluator(base, inner_trials) if inner_trials > 1 else base
            return AlternativeSeedEvaluator(base) if int(self.config.seeds.get("candidates", 1)) > 1 else base
        raise ValueError(f"unknown evaluator type {evaluator_type}")

    def _register_custom_objectives(self) -> None:
        known = set(self.objectives.names())
        for selected in self.config.objectives:
            if selected.name in known:
                continue
            direction = selected.direction or "minimize"
            name = selected.name
            self.objectives.register(
                ObjectiveDefinition(
                    name,
                    direction,
                    "user-defined",
                    f"metric:{name}",
                    lambda result, metric=name: _metric_float(result.metrics.get(metric)),
                )
            )

    def _register_constraints(self) -> None:
        seen = set()
        for raw in self.config.constraints:
            allowed = {"name", "metric", "lower", "upper", "scale", "units", "missing_behavior"}
            unknown = set(raw) - allowed
            if unknown:
                raise ValueError(f"unknown constraint fields: {', '.join(sorted(unknown))}")
            name = str(raw["name"])
            if name in seen:
                raise ValueError(f"duplicate constraint {name}")
            seen.add(name)
            metric = str(raw.get("metric", name))
            lower = _metric_float(raw.get("lower"))
            upper = _metric_float(raw.get("upper"))
            scale = float(raw.get("scale", 1.0))
            if lower is None and upper is None:
                raise ValueError(f"constraint {name} requires lower or upper")
            if lower is not None and upper is not None and lower > upper:
                raise ValueError(f"constraint {name} has reversed bounds")
            if scale <= 0:
                raise ValueError(f"constraint {name} scale must be positive")

            def extract(result: EvaluationResult, metric_name=metric, low=lower, high=upper) -> float | None:
                value = _metric_float(result.metrics.get(metric_name))
                if value is None:
                    return None
                violation = 0.0
                if low is not None:
                    violation += max(0.0, low - value)
                if high is not None:
                    violation += max(0.0, value - high)
                return violation

            self.constraints.register(
                ConstraintDefinition(name, str(raw.get("units", "user-defined")), extract, scale)
            )

    def _validate_configuration(self) -> None:
        errors = self.config.validate_paths()
        qualification_set = self.config.seeds.get("qualification_seed_set")
        if qualification_set:
            if not isinstance(qualification_set, Mapping) or not qualification_set.get("name") or not isinstance(qualification_set.get("seeds"), (list, tuple)) or not qualification_set.get("seeds"):
                errors.append("seeds.qualification_seed_set requires a name and non-empty seeds")
            elif int(self.config.evaluator.get("inner_trials", 1)) != len(qualification_set["seeds"]):
                errors.append("evaluator.inner_trials must equal qualification_seed_set length")
        unknown_operators = set(self.config.operators) - set(self.operators.names())
        if unknown_operators:
            errors.append(f"unknown operators: {', '.join(sorted(unknown_operators))}")
        emtg_evaluator = self._emtg_evaluator()
        if emtg_evaluator is not None:
            mission_supported = {
                "number_of_journeys", "number_of_flybys", "flight_time", "departure_c3",
                "final_arrival_c3", "arrival_declination_bounds", "entry_interface_velocity",
                "spacecraft_configuration", "duty_cycle",
            } | set(MISSION_GENE_ADAPTERS)
            journey_supported = {
                "departure_destination", "arrival_destination", "template_index", "journey_template",
                "dsm_count", "central_body", "periapse_burn_enabled", "arrival_c3",
                "arrival_declination_bounds", "entry_interface_velocity",
            } | set(JOURNEY_GENE_ADAPTERS)
            for name in self.config.search.mission_genes:
                if name not in mission_supported:
                    errors.append(f"no current EMTG mission adapter for gene {name}")
            for name in self.config.search.journey_genes:
                if name not in journey_supported:
                    errors.append(f"no current EMTG journey adapter for gene {name}")
            for name in self.config.search.phase_genes:
                if name not in {"phase_type", "dsm_count", "impulses_per_phase"}:
                    errors.append(f"no current EMTG phase adapter for gene {name}")
            capability_path = self.config.assets.get("capabilities_file")
            if capability_path is None:
                capability_path = emtg_evaluator.executable.parent / "solver_capabilities.json"
            capability_file = Path(capability_path)
            if not capability_file.is_file():
                errors.append("solver_capabilities.json is required for EMTG pre-launch validation")
            else:
                try:
                    capabilities = __import__("json").loads(capability_file.read_text(encoding="utf-8"))
                except (OSError, ValueError) as error:
                    errors.append(f"cannot parse solver capabilities: {error}")
                    capabilities = {}
                budgets = [dict(value.get("budget", {})) for value in self.config.fidelities]
                if not budgets:
                    budgets = [dict(self.config.evaluator.get("budget", {}))]
                for budget in budgets:
                    mode = str(budget.get("inner_loop", "mbh")).lower()
                    if mode not in {"mbh", "nlp", "trialx"}:
                        errors.append(f"unsupported inner_loop mode {mode}")
                    solver = int(budget.get("nlp_solver_type", emtg_evaluator.builder._base.NLP_solver_type))
                    if solver == 0 and not bool(capabilities.get("snopt", False)):
                        errors.append("SNOPT was selected but is unavailable in solver_capabilities.json")
                    elif solver == 2 and not bool(capabilities.get("ipopt", False)):
                        errors.append("IPOPT was selected but is unavailable in solver_capabilities.json")
                    elif solver not in {0, 2}:
                        errors.append(f"unsupported NLP solver type {solver}")
                supported_transcriptions = capabilities.get("supported_phase_types")
                if supported_transcriptions is not None:
                    selected_types = set()
                    for scope in (self.config.search.journey_genes, self.config.search.phase_genes):
                        spec = scope.get("phase_type")
                        if spec:
                            selected_types.update((spec.fixed,) if spec.fixed is not None else spec.choices)
                    unavailable = selected_types - {int(value) for value in supported_transcriptions}
                    if unavailable:
                        errors.append(f"unsupported transcription phase types: {sorted(unavailable)}")
            for name in (
                "launch_vehicle",
                "power_system",
                "electric_propulsion_system",
                "chemical_propulsion_system",
                "spacecraft_configuration",
            ):
                spec = self.config.search.mission_genes.get(name)
                if spec is None:
                    continue
                values = (spec.fixed,) if spec.fixed is not None else spec.choices
                for value in values:
                    try:
                        emtg_evaluator.builder.hardware_catalog.validate_choice(name, value)
                    except ValueError as error:
                        errors.append(str(error))
        if len(self.config.objectives) >= 4:
            self.store.set_metadata(
                "many_objective_warning",
                "NSGA-II crowding can perform poorly with four or more objectives; results must not be treated as strong many-objective convergence evidence.",
            )
        if errors:
            raise ValueError("invalid campaign configuration: " + "; ".join(errors))

    def _emtg_evaluator(self) -> EMTGEvaluator | None:
        evaluator: Any = self.evaluator
        while isinstance(evaluator, (RepeatedEvaluator, AlternativeSeedEvaluator)):
            evaluator = evaluator.base
        return evaluator if isinstance(evaluator, EMTGEvaluator) else None

    def _initialize_store(self) -> None:
        resolved = self.config.resolved_dict()
        identity = content_hash(resolved, prefix="outerloop-campaign-config-v3")
        previous = self.store.get_metadata("configuration_identity")
        if previous is not None and previous != identity:
            raise ValueError("run directory contains a different resolved campaign configuration")
        self.store.set_metadata("configuration_identity", identity)
        previous_source = self.store.get_metadata("source_manifest")
        if previous_source is not None and previous_source.get("content_hash") != self._source_manifest.get("content_hash"):
            raise ValueError(
                "run directory was created by different OuterLoop/adapter source content; choose a fresh run directory"
            )
        self.store.set_metadata("source_manifest", self._source_manifest)
        self.store.set_metadata("resolved_configuration", resolved)
        atomic_write_json(self.config.run_directory / "resolved-config.json", resolved)

    @property
    def fidelity(self) -> str:
        return self._active_fidelity

    def archive_for(self, trial: int, fidelity: str | None = None) -> ParetoArchive:
        selected = fidelity or self.fidelity
        previous = self._active_fidelity
        self._active_fidelity = selected
        try:
            context_id = self._comparison_context_id(trial)
        finally:
            self._active_fidelity = previous
        return self.archives.setdefault((context_id, trial, selected), ParetoArchive())

    def _fidelity_names(self) -> tuple[str, ...]:
        if not self.config.fidelities:
            return ("full",)
        return tuple(
            str(value["name"])
            for value in sorted(self.config.fidelities, key=lambda value: int(value.get("rank", 0)))
        )

    @property
    def budget(self) -> Mapping[str, Any]:
        if self.config.fidelities:
            selected = next(value for value in self.config.fidelities if str(value["name"]) == self.fidelity)
            return dict(selected.get("budget", {}))
        return dict(self.config.evaluator.get("budget", {}))

    def _comparison_context_id(self, trial: int) -> str:
        qualification_set = self.config.seeds.get("qualification_seed_set")
        seed_policy = (
            {"qualification_seed_set": qualification_set}
            if qualification_set
            else {"trial": trial, "derivation": "root/trial/phenotype/fidelity/repeat"}
        )
        return content_hash(
            {
                "evaluator": self.evaluator.context_identity(),
                "source_manifest": self._source_manifest,
                "fidelity": self.fidelity,
                "budget": self.budget,
                "seed_policy": seed_policy,
                "seed_provider": self.config.seeds.get("external_provider"),
                "seed_converter": self.config.seeds.get("converter"),
                "objectives": [vars(value) for value in self.config.objectives],
                "constraints": [dict(value) for value in self.config.constraints],
                "groups": [dict(value) for value in self.config.groups],
            },
            prefix="outerloop-comparison-context-v3",
        )

    def _candidate(
        self,
        genotype: Any,
        *,
        trial: int,
        generation: int,
        slot: int,
        parents: tuple[str, ...] = (),
        operators: tuple[str, ...] = (),
        seeds: Mapping[str, int] | None = None,
        mutation_history: tuple[OperatorRecord, ...] = (),
    ) -> CandidateRecord:
        try:
            repairs = self.config.search.repairs
            decode_policy = "compact" if "compact" in repairs else "reject"
            phenotype = decode_genotype(
                self.schema, genotype, repair_policy=decode_policy, repairs=repairs
            )
            if self.point_groups:
                phenotype = attach_point_group_metadata(phenotype, self.point_groups)
                if "group_replace" in repairs:
                    phenotype = repair_point_groups(phenotype, self.point_groups)
            if self._resonance_catalog is not None:
                phenotype = replace(
                    phenotype,
                    resonance=resonance_metadata(
                        phenotype,
                        self._resonance_catalog,
                        self._resonance_ratios,
                        minimum_turning_degrees=float(
                            self.config.resonance.get("minimum_turning_degrees", 0.0)
                        ),
                    ),
                )
        except Exception as error:
            genotype_hash = content_hash(genotype, prefix="invalid-genotype-v3")
            phenotype = MissionPhenotype(
                {"invalid_genotype": genotype_hash},
                (),
                RepairStatus.REJECTED,
                (RepairRecord("genotype", None, None, str(error)),),
            )
        return CandidateRecord(
            individual_id=deterministic_id(
                self.config.root_seed, "trial", trial, "generation", generation, "slot", slot
            ),
            genotype=genotype,
            phenotype=phenotype,
            generation=generation,
            trial=trial,
            parents=parents,
            operators=operators,
            seeds=dict(seeds or {}),
            mutation_history=mutation_history,
        )

    def _initial_population(self, trial: int) -> list[CandidateRecord]:
        warm = self._warm_genotypes() if trial == 0 or bool(self.config.seeds.get("warm_each_trial", False)) else []
        output = []
        for slot in range(self.config.algorithm.population_size):
            if slot < len(warm):
                genotype, source_id = warm[slot]
                output.append(
                    self._candidate(
                        genotype,
                        trial=trial,
                        generation=0,
                        slot=slot,
                        parents=(source_id,),
                        operators=("warm_start",),
                        seeds={"genome": derive_seed(self.config.root_seed, "warm", trial, slot)},
                    )
                )
            else:
                output.append(
                    self._candidate(
                        stratify_genotype(
                            self.schema,
                            random_genotype(
                                self.schema,
                                random_stream(self.config.root_seed, "initial", trial, slot),
                            ),
                            slot,
                        ),
                        trial=trial,
                        generation=0,
                        slot=slot,
                        seeds={"genome": derive_seed(self.config.root_seed, "initial", trial, slot)},
                    )
                )
        return output

    def _warm_genotypes(self) -> list[tuple[Any, str]]:
        output: list[tuple[Any, str]] = []
        for key in ("warm_population", "warm_archive"):
            path_value = self.config.seeds.get(key)
            if not path_value:
                continue
            path = Path(path_value)
            if path.suffix.lower() == ".nsgaii":
                from .legacy import read_legacy_nsgaii

                mapping = self.config.seeds.get("legacy_gene_mapping")
                if not isinstance(mapping, Mapping) or not mapping:
                    raise ValueError("legacy warm populations require seeds.legacy_gene_mapping")
                population = read_legacy_nsgaii(path)
                for record_index, record in enumerate(population.records):
                    genotype = random_genotype(
                        self.schema,
                        random_stream(self.config.root_seed, "legacy-warm", key, record_index),
                    )
                    mission = dict(genotype.mission)
                    for column, gene in mapping.items():
                        if column not in record.values or str(gene) not in self.config.search.mission_genes:
                            raise ValueError(f"legacy gene mapping {column}->{gene} is invalid")
                        spec = self.config.search.mission_genes[str(gene)]
                        raw = record.values[column]
                        mission[str(gene)] = int(raw) if spec.kind == "integer" else float(raw) if spec.kind == "decimal" else raw
                    output.append((replace(genotype, mission=mission), f"legacy:{path.name}:{record_index}"))
                continue
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                data = __import__("json").loads(line)
                candidate_data = data.get("candidate", data)
                genotype = genotype_from_dict(candidate_data["genotype"])
                source_id = str(candidate_data.get("individual_id", f"{path.name}:{line_number}"))
                # Decode now so incompatibility is a validation error rather
                # than a population of structurally invalid placeholders.
                decode_genotype(self.schema, genotype, repair_policy="reject")
                output.append((genotype, source_id))
        warm_run = self.config.seeds.get("warm_run_directory")
        if warm_run:
            records = CampaignStore(warm_run).archive_records()
            for record in records:
                if record.get("candidate") is not None:
                    output.append((record["candidate"].genotype, record["candidate"].individual_id))
                if record.get("candidate") is not None:
                    self._harvest_seed(record["candidate"], record["result"])
        # Preserve file/archive order while removing canonical genotype duplicates.
        unique = {}
        for genotype, source_id in output:
            unique.setdefault(content_hash(genotype, prefix="warm-genotype-v3"), (genotype, source_id))
        return list(unique.values())[: self.config.algorithm.population_size]

    def _harvest_stored_seeds(self) -> None:
        for record in self.store.generation_records():
            candidate = record["candidate"]
            result = record.get("result")
            if result is not None:
                self._harvest_seed(candidate, result)

    def _harvest_seed(self, candidate: CandidateRecord, result: EvaluationResult) -> None:
        descriptions = result.metrics.get("xdescriptions")
        vector = result.metrics.get("decision_vector")
        if not result.feasible or not descriptions or not vector or len(descriptions) != len(vector):
            return
        seed = SeedArtifact.create(
            result.artifacts.get("emtg", result.evaluation_key),
            candidate.phenotype,
            descriptions,
            vector,
            True,
            objective=_metric_float(result.metrics.get("emtg_objective")),
            fidelity=result.fidelity,
            family=result.candidate_id,
            metadata={
                "evaluation_key": result.evaluation_key,
                "generation": candidate.generation,
                "trial": candidate.trial,
                "options_path": result.artifacts.get("options"),
                "mission_path": result.artifacts.get("emtg"),
            },
        )
        self.seed_inventory.add(seed)

    def _initial_guesses(self, candidate: CandidateRecord) -> Mapping[str, Any] | None:
        count = int(self.config.seeds.get("candidates", 1))
        eligible = SeedInventory(
            seed
            for seed in self.seed_inventory.values()
            if (
                seed.metadata.get("generation") is None
                or int(seed.metadata["generation"]) < candidate.generation
                or int(seed.metadata.get("trial", candidate.trial)) < candidate.trial
            )
            and (
                bool(self.config.seeds.get("allow_fidelity_transfer", False))
                or seed.fidelity == self.fidelity
            )
        )
        selected = eligible.select(
            candidate.phenotype,
            count=max(1, count),
            include_infeasible=bool(self.config.seeds.get("include_infeasible", False)),
            weights=self.config.seeds.get("distance_weights"),
            family=candidate.candidate_id if bool(self.config.seeds.get("family_ranking", True)) else None,
        )
        converter = str(self.config.seeds.get("converter", "exact_descriptions"))
        if converter == "same_transcription_shape":
            converter = "same_shape_body_substitution"
        converter_impl = default_converter_registry().get(converter)
        target_descriptions = self.config.seeds.get("target_xdescriptions")
        alternatives = []
        considerations = []
        for seed in selected:
            compatibility = converter_impl.compatibility(seed, candidate.phenotype, target_descriptions)
            considerations.append({
                "seed_id": seed.seed_id,
                "accepted": compatibility.compatible,
                "reason": compatibility.reason,
                "converter": converter,
            })
            if not compatibility.compatible:
                continue
            converted_seed = seed
            if converter not in {"exact_descriptions", "same_shape_body_substitution"}:
                try:
                    converted_seed = converter_impl.convert(
                        seed, candidate.phenotype, target_descriptions
                    )
                except Exception as error:
                    considerations[-1]["accepted"] = False
                    considerations[-1]["reason"] = f"conversion failed: {error}"
                    continue
            alternatives.append({
                "seed_id": converted_seed.seed_id,
                "xdescriptions": list(converted_seed.xdescriptions),
                "decision_vector": list(converted_seed.decision_vector),
                "source": converted_seed.source,
                "converter": converter,
            })
        self.store.set_metadata(f"seed_considerations_{candidate.individual_id}", considerations)
        alternatives.extend(self._external_seed_guesses(candidate))
        if not alternatives:
            return None
        return {"alternatives": alternatives} if len(alternatives) > 1 else alternatives[0]

    def _external_seed_guesses(self, candidate: CandidateRecord) -> list[Mapping[str, Any]]:
        provider_config = self.config.seeds.get("external_provider")
        if not provider_config:
            return []
        command = provider_config.get("command")
        if not isinstance(command, list) or not command:
            raise ValueError("seeds.external_provider.command must be a non-empty argument list")
        request_data = {
            "schema_version": 3,
            "provider_type": provider_config.get("type", "external"),
            "candidate_id": candidate.candidate_id,
            "phenotype": phenotype_to_dict(candidate.phenotype),
            "fidelity": self.fidelity,
        }
        provider = ExternalSeedProvider(
            command,
            timeout_seconds=float(provider_config.get("timeout_seconds", 300.0)),
            environment=provider_config.get("environment"),
        )
        identity = content_hash(
            {"provider": provider.identity(), "request": request_data}, prefix="outerloop-seed-provider-request-v3"
        )
        cache_path = self.config.run_directory / "provider-cache" / f"{identity}.json"
        if cache_path.is_file():
            response = __import__("json").loads(cache_path.read_text(encoding="utf-8"))
        else:
            response = provider.generate(
                request_data, self.config.run_directory / "provider-work", self.cancel_event
            )
            atomic_write_json(cache_path, response)
        if response.get("schema_version") != 3:
            raise ValueError("external seed provider response must use schema_version 3")
        unknown_response = set(response) - {"schema_version", "seeds", "diagnostics"}
        if unknown_response:
            raise ValueError(f"unknown external seed provider response fields: {sorted(unknown_response)}")
        raw_seeds = response.get("seeds", ())
        if not isinstance(raw_seeds, list):
            raise ValueError("external seed provider response.seeds must be a list")
        guesses = []
        for index, raw in enumerate(raw_seeds):
            if not isinstance(raw, Mapping):
                raise ValueError(f"external seed {index} must be an object")
            unknown_seed = set(raw) - {
                "seed_id", "xdescriptions", "decision_vector", "source", "converter"
            }
            if unknown_seed:
                raise ValueError(f"external seed {index} has unknown fields: {sorted(unknown_seed)}")
            descriptions = raw.get("xdescriptions", ())
            vector = raw.get("decision_vector", ())
            if not descriptions or len(descriptions) != len(vector):
                raise ValueError(f"external seed {index} has inconsistent descriptions/vector")
            guesses.append({
                "seed_id": raw.get("seed_id", content_hash(raw, prefix="external-seed-v3")),
                "xdescriptions": list(descriptions),
                "decision_vector": [float(value) for value in vector],
                "source": raw.get("source", provider_config.get("type", "external")),
                "converter": raw.get("converter", "provider_native"),
            })
        return guesses

    def _request(self, candidate: CandidateRecord) -> EvaluationRequest:
        qualification_set = self.config.seeds.get("qualification_seed_set")
        if qualification_set:
            if not isinstance(qualification_set, Mapping) or not qualification_set.get("name") or not qualification_set.get("seeds"):
                raise ValueError("seeds.qualification_seed_set requires name and non-empty seeds")
            inner_seed_set = tuple(
                derive_seed(
                    self.config.root_seed, "qualification-inner", qualification_set["name"],
                    candidate.candidate_id, int(value), bits=31,
                )
                for value in qualification_set["seeds"]
            )
            seed = inner_seed_set[0]
        else:
            seed = derive_seed(
                self.config.root_seed,
                "inner",
                candidate.trial,
                candidate.candidate_id,
                self.fidelity,
                0,
                bits=31,
            )
            inner_seed_set = (seed,)
        context = {
            "evaluator": self.evaluator.context_identity(),
            "execution_semantics": content_hash(
                {
                    "search": self.config.resolved_dict()["search"],
                    "prefilters": self.config.prefilters,
                },
                prefix="outerloop-execution-semantics-v3",
            ),
            "source_manifest": self._source_manifest,
            "case_generation_version": 3,
            "parser_version": 3,
            "inner_seed_set": inner_seed_set,
            "comparison_context_id": self._comparison_context_id(candidate.trial),
        }
        return EvaluationRequest(candidate, self.fidelity, seed, self.budget, self._initial_guesses(candidate), context)

    def _screen(self, request: EvaluationRequest) -> EvaluationResult | None:
        phenotype = request.candidate.phenotype
        if phenotype.repair_status is RepairStatus.REJECTED or not phenotype.journeys:
            reason = phenotype.repairs[0].reason if phenotype.repairs else "decoded topology is empty"
            return EvaluationResult(
                request.evaluation_key,
                request.candidate.candidate_id,
                EvaluationStatus.STRUCTURALLY_INVALID,
                request.fidelity,
                failure_reason=reason,
            )
        for name in ("total_flight_time_bounds", "flight_time_bounds"):
            bounds = phenotype.mission.get(name)
            if bounds is not None and (
                not isinstance(bounds, (list, tuple))
                or len(bounds) != 2
                or float(bounds[0]) < 0.0
                or float(bounds[0]) > float(bounds[1])
            ):
                return EvaluationResult(
                    request.evaluation_key,
                    request.candidate.candidate_id,
                    EvaluationStatus.STRICT_FILTERED,
                    request.fidelity,
                    failure_reason=f"{name} is not an ordered nonnegative pair",
                )
        emtg_evaluator = self._emtg_evaluator()
        if emtg_evaluator is not None:
            supported = set(self.config.evaluator.get("supported_phase_types", (2, 3, 4, 5, 6, 7, 8, 11)))
            try:
                for journey_index, journey in enumerate(phenotype.journeys):
                    template = emtg_evaluator.builder._base.Journeys[emtg_evaluator.builder.journey_template_index]
                    central = str(journey.values.get("central_body", template.journey_central_body))
                    catalog = emtg_evaluator.builder.catalog(central)
                    catalog.body_index(journey.departure)
                    catalog.body_index(journey.arrival)
                    for body in journey.flybys:
                        catalog.flyby_index(body)
                    for phase in journey.phases:
                        phase_type = int(phase.values.get("phase_type", journey.values.get("phase_type", template.phase_type)))
                        if phase_type not in supported:
                            raise ValueError(f"phase type {phase_type} is unavailable")
                    departure_type = int(journey.values.get("departure_type", template.departure_type))
                    arrival_type = int(journey.values.get("arrival_type", template.arrival_type))
                    final_phase_type = int(
                        journey.phases[-1].values.get(
                            "phase_type", journey.values.get("phase_type", template.phase_type)
                        )
                    )
                    if journey_index == 0 and departure_type in {3, 4}:
                        raise ValueError("the first journey cannot depart with a flyby boundary")
                    if arrival_type in {3, 5} and final_phase_type not in {2, 3, 4, 5, 11}:
                        raise ValueError(
                            f"arrival type {arrival_type} requires a compatible low-thrust terminal phase"
                        )
                if bool(self.config.evaluator.get("check_ephemeris_coverage", True)):
                    launch = float(
                        phenotype.mission.get(
                            "launch_epoch", emtg_evaluator.builder._base.launch_window_open_date
                        )
                    )
                    flight_bounds = phenotype.mission.get(
                        "total_flight_time_bounds",
                        phenotype.mission.get(
                            "flight_time_bounds",
                            emtg_evaluator.builder._base.total_flight_time_bounds,
                        ),
                    )
                    if isinstance(flight_bounds, (list, tuple)):
                        duration = float(flight_bounds[-1])
                    else:
                        duration = float(flight_bounds)
                    spice_ids = []
                    for journey in phenotype.journeys:
                        template = emtg_evaluator.builder._base.Journeys[emtg_evaluator.builder.journey_template_index]
                        central = str(journey.values.get("central_body", template.journey_central_body))
                        catalog = emtg_evaluator.builder.catalog(central)
                        spice_ids.extend(
                            catalog.body(body).spice_id
                            for body in journey.sequence
                            if body in catalog.bodies
                        )
                    missing = emtg_evaluator.ephemeris_coverage().missing(
                        spice_ids, launch, launch + duration
                    )
                    if missing:
                        raise ValueError(
                            f"SPK coverage is missing for body IDs {missing} over MJD {launch}..{launch + duration}"
                        )
            except Exception as error:
                return EvaluationResult(
                    request.evaluation_key,
                    request.candidate.candidate_id,
                    EvaluationStatus.STRICT_FILTERED,
                    request.fidelity,
                    failure_reason=str(error),
                )
        for definition in self.config.prefilters:
            filter_type = str(definition.get("type", ""))
            heuristic = bool(definition.get(
                "heuristic",
                not bool(definition.get("strict", filter_type in {"maximum_flybys", "time_bounds"})),
            ))
            filter_metrics: Mapping[str, Any] = {}
            if filter_type in {"minimum_flight_time", "c3_envelope", "inclination_bandpass", "flyby_altitude"}:
                if emtg_evaluator is None:
                    raise ValueError(f"prefilter {filter_type} requires an EMTG universe")
                template = emtg_evaluator.builder._base.Journeys[emtg_evaluator.builder.journey_template_index]
                central_bodies = {
                    str(journey.values.get("central_body", template.journey_central_body))
                    for journey in phenotype.journeys
                }
                if len(central_bodies) != 1:
                    reason = f"{filter_type} does not combine journeys with different central bodies"
                else:
                    catalog = emtg_evaluator.builder.catalog(next(iter(central_bodies)))
                    if filter_type == "minimum_flight_time":
                        screen = HohmannTimeScreen(float(definition.get("factor", 0.25)))
                        accepted, reason, filter_metrics = screen.screen(phenotype, catalog)
                    elif filter_type == "c3_envelope":
                        screen = C3EnvelopeScreen(
                            float(definition["maximum_departure_c3"]) if definition.get("maximum_departure_c3") is not None else None,
                            float(definition["maximum_arrival_c3"]) if definition.get("maximum_arrival_c3") is not None else None,
                        )
                        accepted, reason, filter_metrics = screen.screen(phenotype, catalog)
                    elif filter_type == "inclination_bandpass":
                        maximum = float(definition["maximum_degrees"])
                        separations = [
                            inclination_separation(catalog.body(left), catalog.body(right))
                            for journey in phenotype.journeys
                            for left, right in zip(journey.sequence, journey.sequence[1:])
                            if left in catalog.bodies and right in catalog.bodies
                        ]
                        observed = max(separations, default=0.0)
                        accepted = observed <= maximum
                        reason = None if accepted else f"inclination separation {observed:.6g} exceeds {maximum} degrees"
                        filter_metrics = {"maximum_inclination_separation": observed}
                    else:
                        violations = []
                        for journey in phenotype.journeys:
                            for phase, body in zip(journey.phases, journey.flybys):
                                altitude = phase.values.get("flyby_altitude")
                                minimum = catalog.body(body).minimum_flyby_altitude
                                if altitude is not None and float(altitude) < minimum:
                                    violations.append((body, float(altitude), minimum))
                        accepted = not violations
                        reason = None if accepted else f"flyby altitude is below universe minimum: {violations}"
                        filter_metrics = {"flyby_altitude_violations": violations}
                    if accepted:
                        reason = None
            elif filter_type == "delivered_mass_heuristic":
                estimate = phenotype.mission.get("delivered_mass_estimate")
                minimum = float(definition.get("minimum_mass", 0.0))
                reason = (
                    f"estimated delivered mass {estimate} is below {minimum}"
                    if estimate is not None and float(estimate) < minimum else None
                )
                filter_metrics = {"estimated_delivered_mass": estimate, "minimum_mass": minimum}
            elif filter_type in {"lambert_provider", "patched_conic_provider"}:
                response = self._external_prefilter(candidate=request.candidate, definition=definition)
                reason = None if bool(response["accepted"]) else str(response.get("reason") or f"{filter_type} provider rejected candidate")
                filter_metrics = dict(response.get("metrics", {}))
            else:
                reason = self._prefilter_reason(phenotype, definition)
            if reason is None:
                continue
            audit_fraction = float(definition.get("audit_fraction", 0.05))
            audit_rng = random_stream(
                self.config.root_seed, "audit", request.candidate.candidate_id, filter_type
            )
            if heuristic and audit_rng.random() < audit_fraction:
                self.store.set_metadata(
                    f"heuristic_audit_{request.evaluation_key}",
                    {
                        "prefilter": filter_type,
                        "reason": reason,
                        "audit_fraction": audit_fraction,
                        "screen_metrics": dict(filter_metrics),
                    },
                )
                continue
            return EvaluationResult(
                request.evaluation_key,
                request.candidate.candidate_id,
                EvaluationStatus.HEURISTIC_FILTERED if heuristic else EvaluationStatus.STRICT_FILTERED,
                request.fidelity,
                failure_reason=reason,
                metrics=filter_metrics,
                provenance={"prefilter": filter_type, "heuristic": heuristic, "audit_fraction": audit_fraction},
            )
        return None

    def _external_prefilter(
        self, *, candidate: CandidateRecord, definition: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        provider_config = definition.get("provider")
        if not isinstance(provider_config, Mapping):
            raise ValueError("external prefilter provider configuration is missing")
        provider = ExternalSeedProvider(
            provider_config["command"],
            timeout_seconds=float(provider_config.get("timeout_seconds", 300.0)),
            environment=provider_config.get("environment"),
        )
        request = {
            "schema_version": 3,
            "protocol": "outerloop/prefilter/v1",
            "filter_type": definition["type"],
            "candidate_id": candidate.candidate_id,
            "phenotype": phenotype_to_dict(candidate.phenotype),
        }
        identity = content_hash(
            {"provider": provider.identity(), "request": request},
            prefix="outerloop-prefilter-provider-v3",
        )
        path = self.config.run_directory / "provider-cache" / "prefilters" / f"{identity}.json"
        if path.is_file():
            response = __import__("json").loads(path.read_text(encoding="utf-8"))
        else:
            response = provider.generate(
                request, self.config.run_directory / "provider-work" / "prefilters", self.cancel_event
            )
            atomic_write_json(path, response)
        if not isinstance(response, Mapping) or response.get("schema_version") != 3:
            raise ValueError("external prefilter response must be a schema-3 object")
        unknown = set(response) - {"schema_version", "accepted", "reason", "metrics"}
        if unknown or not isinstance(response.get("accepted"), bool) or not isinstance(response.get("metrics", {}), Mapping):
            raise ValueError(f"invalid external prefilter response fields: {sorted(unknown)}")
        return response

    @staticmethod
    def _prefilter_reason(phenotype: MissionPhenotype, definition: Mapping[str, Any]) -> str | None:
        filter_type = str(definition.get("type", ""))
        if filter_type == "successive_duplicate" and not bool(definition.get("allow", False)):
            for journey in phenotype.journeys:
                for left, right in zip(journey.sequence, journey.sequence[1:]):
                    if left == right:
                        return f"successive repeat of {left}"
        elif filter_type == "maximum_flybys":
            count = sum(len(journey.flybys) for journey in phenotype.journeys)
            if count > int(definition["value"]):
                return f"flyby count {count} exceeds {definition['value']}"
        elif filter_type == "numeric_envelope":
            name = str(definition["metric"])
            if name in phenotype.mission:
                value = float(phenotype.mission[name])
                if definition.get("lower") is not None and value < float(definition["lower"]):
                    return f"{name} is below its envelope"
                if definition.get("upper") is not None and value > float(definition["upper"]):
                    return f"{name} exceeds its envelope"
        elif filter_type in {"minimum_flight_time", "c3_envelope"}:
            # These filters need an evaluator universe and are handled in the
            # instance-aware branch below.
            return None
        elif filter_type == "allowed_bodies":
            allowed = set(map(str, definition.get("bodies", ())))
            found = {
                body for journey in phenotype.journeys for body in journey.sequence
            }
            unexpected = sorted(found - allowed)
            if unexpected:
                return f"bodies are outside the allowed set: {unexpected}"
        elif filter_type == "forbidden_bodies":
            forbidden = set(map(str, definition.get("bodies", ())))
            found = {
                body for journey in phenotype.journeys for body in journey.sequence
            }
            blocked = sorted(found.intersection(forbidden))
            if blocked:
                return f"forbidden bodies were selected: {blocked}"
        elif filter_type in {"forbidden_pairs", "allowed_successive"}:
            pairs = {
                (str(value[0]), str(value[1])) for value in definition.get("pairs", ())
            }
            for journey in phenotype.journeys:
                for pair in zip(journey.sequence, journey.sequence[1:]):
                    if filter_type == "forbidden_pairs" and pair in pairs:
                        return f"forbidden succession {pair[0]}->{pair[1]}"
                    if filter_type == "allowed_successive" and pair not in pairs:
                        return f"succession {pair[0]}->{pair[1]} is not allowed"
        elif filter_type == "maximum_repeats":
            limits = {str(key): int(value) for key, value in definition.get("limits", {}).items()}
            visits = [body for journey in phenotype.journeys for body in journey.sequence[1:]]
            for body, limit in limits.items():
                if visits.count(body) > limit:
                    return f"{body} exceeds repeat limit {limit}"
        elif filter_type == "mandatory_destinations":
            required = set(map(str, definition.get("bodies", ())))
            visits = {body for journey in phenotype.journeys for body in journey.sequence[1:]}
            missing = sorted(required - visits)
            if missing:
                return f"mandatory destinations were not visited: {missing}"
        elif filter_type == "required_groups":
            for name in map(str, definition.get("groups", ())):
                if name not in phenotype.point_group:
                    return f"required group {name} is undefined"
                if not bool(phenotype.point_group[name].get("complete", False)):
                    return f"required group {name} is incomplete"
        elif filter_type == "forbidden_groups":
            for name in map(str, definition.get("groups", ())):
                if name in phenotype.point_group and int(phenotype.point_group[name].get("visits", 0)) > 0:
                    return f"forbidden group {name} was visited"
        elif filter_type:
            raise ValueError(f"unknown prefilter type {filter_type}")
        return None

    def _individual(self, candidate: CandidateRecord, result: ScoredEvaluationResult) -> NSGA2Individual:
        objectives, missing = self.objectives.extract(result, self.config.objectives)
        status = result.status
        if missing and status is EvaluationStatus.FEASIBLE:
            status = EvaluationStatus.OUTPUT_INCOMPLETE
        if len(objectives) != len(self.config.objectives):
            objectives = tuple(math.inf for _ in self.config.objectives)
        return NSGA2Individual(
            candidate.individual_id,
            objectives,
            status,
            result.aggregate_violation,
            payload=candidate,
        )

    def _enrich_result(
        self, candidate: CandidateRecord, result: EvaluationResult
    ) -> ScoredEvaluationResult:
        metrics = dict(result.metrics)
        phenotype = candidate.phenotype
        metrics["number_of_journeys"] = len(phenotype.journeys)
        metrics["number_of_flybys"] = sum(len(journey.flybys) for journey in phenotype.journeys)
        metrics["runtime"] = result.runtime_seconds
        if phenotype.point_group:
            metrics["point_group_value"] = sum(
                float(value.get("score", 0.0)) for value in phenotype.point_group.values()
            )
            metrics["point_groups"] = phenotype.point_group
        if phenotype.resonance:
            metrics["resonance"] = phenotype.resonance
        architectural_metrics = {
            "launch_epoch": "launch_epoch",
            "beginning_of_life_power": "beginning_of_life_power",
            "power_at_1_AU": "beginning_of_life_power",
            "bus_power": "bus_power",
            "duty_cycle": "thruster_duty_cycle",
            "number_of_electric_propulsion_systems": "number_of_thrusters",
            "dry_mass_margin": "dry_mass_margin",
            "normalized_aggregate_control": "normalized_aggregate_control",
        }
        for gene, metric in architectural_metrics.items():
            if gene in phenotype.mission:
                metrics.setdefault(metric, phenotype.mission[gene])
        for gene, metric in (
            ("launch_vehicle", "launch_vehicle_preference"),
            ("electric_propulsion_system", "thruster_preference"),
        ):
            value = phenotype.mission.get(gene)
            spec = self.config.search.mission_genes.get(gene)
            if value is not None:
                metrics[f"selected_{gene}"] = value
                if spec and value in spec.choices:
                    metrics.setdefault(metric, spec.choices.index(value) + 1)
        enriched = replace(result, metrics=metrics)
        audit = self.store.get_metadata(f"heuristic_audit_{result.evaluation_key}")
        if audit:
            metrics["heuristic_audit"] = {
                **audit,
                "false_rejection": result.status is EvaluationStatus.FEASIBLE,
                "evaluated_status": result.status.value,
            }
            provenance = dict(enriched.provenance)
            provenance["heuristic_audit"] = metrics["heuristic_audit"]
            enriched = replace(enriched, metrics=metrics, provenance=provenance)
        objective_values: dict[str, float | None] = {}
        missing_objectives = []
        for selected in self.config.objectives:
            definition = self.objectives.definition(selected.name)
            valid_for_infeasible = (
                definition.valid_for_infeasible
                if selected.valid_for_infeasible is None
                else selected.valid_for_infeasible
            )
            value = (
                definition.extractor(enriched)
                if enriched.status is EvaluationStatus.FEASIBLE or valid_for_infeasible
                else None
            )
            objective_values[selected.name] = value
            policy = selected.missing_policy or definition.missing_behavior
            if value is None and policy == "penalize":
                objective_values[selected.name] = selected.penalty
            elif value is None:
                missing_objectives.append(selected.name)
        constraint_values: dict[str, float | None] = {}
        missing_constraints = []
        for raw in self.config.constraints:
            name = str(raw["name"])
            definition = self.constraints.definition(name)
            value = definition.extractor(enriched)
            constraint_values[name] = value
            if value is None:
                missing_constraints.append(name)
        for group_name in sorted(self.group_constraints):
            group = phenotype.point_group.get(group_name, {})
            constraint_values[f"point_group:{group_name}"] = float(group.get("violation", 0.0))
        available = [
            (
                float(value) / self.constraints.definition(name).scale
                if not name.startswith("point_group:") else float(value)
            )
            for name, value in constraint_values.items()
            if value is not None
        ]
        outer_violation = sum(available)
        # EMTG's feasible classification already applies its configured NLP
        # tolerances. Preserve the parsed worst violation in the raw record,
        # but do not reclassify a solver-feasible point merely because that
        # diagnostic is a small positive number.
        aggregate = 0.0 if result.status is EvaluationStatus.FEASIBLE else result.solver_violation
        if available:
            aggregate = (aggregate or 0.0) + outer_violation
        status = enriched.status
        failure_reasons = [enriched.failure_reason] if enriched.failure_reason else []
        if missing_objectives and status is EvaluationStatus.FEASIBLE:
            reject_missing_objective = any(
                next(
                    selection.missing_policy or self.objectives.definition(name).missing_behavior
                    for selection in self.config.objectives if selection.name == name
                ) == "reject"
                for name in missing_objectives
            )
            if reject_missing_objective:
                status = EvaluationStatus.OUTPUT_INCOMPLETE
                failure_reasons.append(
                    f"missing objective metrics: {', '.join(missing_objectives)}"
                )
        if missing_constraints and status is EvaluationStatus.FEASIBLE:
            reject_missing = any(
                str(raw.get("missing_behavior", "reject")) == "reject"
                for raw in self.config.constraints
                if str(raw["name"]) in missing_constraints
            )
            if reject_missing:
                status = EvaluationStatus.OUTPUT_INCOMPLETE
                failure_reasons.append(
                    f"missing constraint metrics: {', '.join(missing_constraints)}"
                )
        elif outer_violation > 0.0 and status is EvaluationStatus.FEASIBLE:
            status = EvaluationStatus.OUTER_CONSTRAINT_INFEASIBLE
        objective_metadata = {}
        for selected in self.config.objectives:
            definition = self.objectives.definition(selected.name)
            objective_metadata[selected.name] = {
                "direction": selected.direction or definition.direction,
                "units": selected.units or definition.units,
                "source": selected.source or definition.source,
                "scale": selected.scale,
                "missing_policy": selected.missing_policy or definition.missing_behavior,
                "penalty": selected.penalty,
                "valid_for_infeasible": (
                    definition.valid_for_infeasible
                    if selected.valid_for_infeasible is None else selected.valid_for_infeasible
                ),
            }
        scoring_context = {
            "schema_version": 3,
            "comparison_context_id": self._comparison_context_id(candidate.trial),
            "trial": candidate.trial,
            "objectives": objective_metadata,
            "constraints": [dict(value) for value in self.config.constraints],
            "groups": [dict(value) for value in self.config.groups],
        }
        return ScoredEvaluationResult.from_raw(
            result,
            metrics=metrics,
            provenance=enriched.provenance,
            objectives=objective_values,
            status=status,
            failure_reason="; ".join(failure_reasons) if failure_reasons else None,
            constraints=constraint_values,
            aggregate_violation=aggregate,
            campaign_feasible=(status is EvaluationStatus.FEASIBLE and (aggregate or 0.0) <= 0.0),
            objective_metadata=objective_metadata,
            scoring_context=scoring_context,
        )

    def _evaluated(
        self, rows: Sequence[tuple[CandidateRecord, EvaluationResult | None]]
    ) -> list[EvaluatedCandidate]:
        output = []
        for candidate, result in rows:
            if result is None:
                raise ValueError("population contains unevaluated candidates")
            if not isinstance(result, ScoredEvaluationResult):
                result = self._enrich_result(candidate, result)
            output.append(EvaluatedCandidate(candidate, result, self._individual(candidate, result)))
        return output

    def _evaluate_phase(
        self,
        trial: int,
        generation: int,
        role: str,
        max_new_evaluations: int | None,
        already_used: int,
    ) -> tuple[bool, int]:
        rows = self.store.load_candidates(trial, generation, role)
        requests: dict[str, EvaluationRequest] = {}
        positions: dict[str, list[int]] = {}
        used = already_used
        for position, (candidate, existing) in enumerate(rows):
            if existing is not None:
                continue
            request = self._request(candidate)
            positions.setdefault(request.evaluation_key, []).append(position)
            requests.setdefault(request.evaluation_key, request)

        pending: list[EvaluationRequest] = []
        for key, request in sorted(requests.items()):
            result = self.store.evaluation(key) or self.cache.get(key)
            if result is None:
                result = self._screen(request)
            if result is not None:
                raw_result = result.raw() if isinstance(result, ScoredEvaluationResult) else result
                result = self._enrich_result(request.candidate, raw_result)
                self._harvest_seed(request.candidate, result)
                self.store.save_result(trial, generation, role, positions[key], result)
                if result.status not in {EvaluationStatus.PENDING, EvaluationStatus.RUNNING, EvaluationStatus.CANCELLED}:
                    self.cache.put(raw_result, request.context)
                self._checkpoint_evaluation(trial, generation, role)
            else:
                pending.append(request)

        if max_new_evaluations is not None:
            remaining = max_new_evaluations - used
            if remaining <= 0:
                return False, used
            batch = pending[:remaining]
        else:
            batch = pending
        if batch:
            used += len(batch)
            if hasattr(self.backend, "evaluate_stream"):
                completed = self.backend.evaluate_stream(batch, self.evaluator, self.cancel_event)  # type: ignore[attr-defined]
            else:
                completed = enumerate(self.backend.evaluate(batch, self.evaluator, self.cancel_event))
            for batch_index, result in completed:
                request = batch[batch_index]
                raw_result = result
                if raw_result.status not in {EvaluationStatus.CANCELLED, EvaluationStatus.PENDING, EvaluationStatus.RUNNING}:
                    self.cache.put(raw_result, request.context)
                result = self._enrich_result(request.candidate, raw_result)
                self._harvest_seed(request.candidate, result)
                self.store.save_result(
                    trial, generation, role, positions[request.evaluation_key], result
                )
                self._checkpoint_evaluation(trial, generation, role)
        complete = len(batch) == len(pending) and not self.cancel_event.is_set()
        return complete, used

    def cancel(self) -> None:
        """Request cooperative cancellation of campaign and child processes."""
        self.cancel_event.set()

    def _checkpoint_evaluation(self, trial: int, generation: int, role: str) -> None:
        self._results_since_checkpoint += 1
        if self._results_since_checkpoint >= self.config.checkpoint_every:
            self.store.checkpoint(
                {
                    "status": "evaluating",
                    "trial": trial,
                    "generation": generation,
                    "role": role,
                }
            )
            self._results_since_checkpoint = 0

    def _breed(self, parents: Sequence[EvaluatedCandidate], trial: int, generation: int) -> list[CandidateRecord]:
        ranked = rank_population([entry.individual for entry in parents])
        weights = {**DEFAULT_OPERATOR_WEIGHTS, **dict(self.config.operators)}
        if self.point_groups:
            weights.setdefault("point_group", 1.0)
        if self._resonance_catalog is not None:
            weights.setdefault("resonance", 1.0)
        crossover_weights = {
            name: value for name, value in weights.items() if self.operators.get(name).crossover is not None
        }
        mutation_weights = {
            name: value for name, value in weights.items() if self.operators.get(name).mutation is not None
        }
        statistics: dict[str, dict[str, int]] = {}
        output: list[CandidateRecord] = []
        elites = sorted(ranked, key=lambda item: (item.rank, -item.crowding_distance, item.candidate_id))[: self.config.algorithm.extra_elites]
        for slot in range(self.config.algorithm.population_size):
            if slot < len(elites):
                parent = elites[slot].payload
                output.append(
                    self._candidate(
                        parent.genotype,
                        trial=trial,
                        generation=generation,
                        slot=slot,
                        parents=(parent.individual_id,),
                        operators=("extra_elite",),
                    )
                )
                continue
            selection_rng = random_stream(self.config.root_seed, "selection", trial, generation, slot)
            left = tournament_select(ranked, selection_rng, self.config.algorithm.tournament_size).payload
            right = tournament_select(ranked, selection_rng, self.config.algorithm.tournament_size).payload
            genotype = left.genotype
            applied: list[str] = []
            history: list[OperatorRecord] = []
            crossover_rng = random_stream(self.config.root_seed, "crossover", trial, generation, slot)
            if crossover_rng.random() < self.config.algorithm.crossover_probability:
                operator = self.operators.choose(crossover_weights, crossover_rng, crossover=True)
                before = genotype
                for _attempt in range(4):
                    proposed = operator.crossover(self.schema, left.genotype, right.genotype, crossover_rng)  # type: ignore[misc]
                    genotype = proposed
                    if proposed != before:
                        break
                applied.append(operator.name)
                history.append(_operator_record(
                    operator.name,
                    derive_seed(self.config.root_seed, "crossover", trial, generation, slot),
                    before,
                    genotype,
                ))
            mutation_rng = random_stream(self.config.root_seed, "mutation", trial, generation, slot)
            if mutation_rng.random() < self.config.algorithm.mutation_probability:
                operator = self.operators.choose(mutation_weights, mutation_rng)
                before = genotype
                for _attempt in range(4):
                    proposed = operator.mutation(self.schema, before, mutation_rng)  # type: ignore[misc]
                    genotype = proposed
                    if proposed != before:
                        break
                applied.append(operator.name)
                history.append(_operator_record(
                    operator.name,
                    derive_seed(self.config.root_seed, "mutation", trial, generation, slot),
                    before,
                    genotype,
                ))
            candidate = self._candidate(
                    genotype,
                    trial=trial,
                    generation=generation,
                    slot=slot,
                    parents=(left.individual_id, right.individual_id),
                    operators=tuple(applied),
                    seeds={
                        "selection": derive_seed(self.config.root_seed, "selection", trial, generation, slot),
                        "crossover": derive_seed(self.config.root_seed, "crossover", trial, generation, slot),
                        "mutation": derive_seed(self.config.root_seed, "mutation", trial, generation, slot),
                    },
                    mutation_history=tuple(history),
                )
            output.append(candidate)
            for record in history:
                counts = statistics.setdefault(
                    record.operator,
                    {"proposed": 0, "effective": 0, "no_op": 0, "rejected": 0},
                )
                counts["proposed"] += 1
                counts["effective"] += 0 if record.no_op else 1
                counts["no_op"] += 1 if record.no_op else 0
                counts["rejected"] += 1 if candidate.phenotype.repair_status is RepairStatus.REJECTED else 0
        for operator_name, counts in sorted(statistics.items()):
            self.store.increment_operator(
                trial, generation, operator_name, **counts
            )
        return output

    def _update_archive(self, evaluated: Sequence[EvaluatedCandidate], generation: int) -> None:
        by_trial: dict[int, list[EvaluatedCandidate]] = {}
        for entry in evaluated:
            by_trial.setdefault(entry.candidate.trial, []).append(entry)
        for trial, values in by_trial.items():
            archive = self.archive_for(trial)
            for entry in values:
                if all(math.isfinite(value) for value in entry.individual.objectives):
                    archive.update(ArchiveEntry(entry.result, entry.individual.objectives, generation))
            context_id = self._comparison_context_id(trial)
            self.store.archive_replace(
                context_id,
                trial,
                self.fidelity,
                [(entry.result, entry.objectives, entry.generation) for entry in archive.entries()],
            )

    def _archive_total(self) -> int:
        return sum(len(archive.entries()) for archive in self.archives.values())

    def _promotion_candidates(
        self,
        source_fidelity: str,
        target_fidelity: str,
        trial: int,
        generation: int,
    ) -> list[CandidateRecord]:
        source_entries = self.archive_for(trial, source_fidelity).entries()
        target_config = next(
            value for value in self.config.fidelities if str(value["name"]) == target_fidelity
        )
        default_count = max(
            1,
            math.ceil(
                len(source_entries) * float(target_config.get("promotion_fraction", 0.25))
            ),
        )
        count = int(target_config.get("promote_count", default_count))
        individuals = [
            NSGA2Individual(
                entry.result.evaluation_key,
                entry.objectives,
                entry.result.status,
                entry.result.aggregate_violation,
                payload=entry,
            )
            for entry in source_entries
        ]
        selected = promote_diverse_nondominated(individuals, min(count, len(individuals)))
        output: list[CandidateRecord] = []
        for slot, individual in enumerate(selected):
            source_entry = individual.payload
            found = self.store.find_candidate(
                source_entry.result.candidate_id, source_fidelity, trial
            )
            if found is None:
                continue
            source_candidate = found[0]
            output.append(
                CandidateRecord(
                    individual_id=deterministic_id(
                        self.config.root_seed,
                        "promotion",
                        source_fidelity,
                        target_fidelity,
                        source_candidate.candidate_id,
                        slot,
                    ),
                    genotype=source_candidate.genotype,
                    phenotype=source_candidate.phenotype,
                    generation=generation,
                    trial=trial,
                    parents=(source_candidate.individual_id,),
                    operators=(f"promote:{source_fidelity}->{target_fidelity}",),
                    seeds={
                        "promotion": derive_seed(
                            self.config.root_seed,
                            "promotion",
                            source_fidelity,
                            target_fidelity,
                            slot,
                        )
                    },
                )
            )
        return output

    def _run_promotions(
        self,
        *,
        trial: int,
        evolution_generation: int,
        start_rank: int = 1,
        max_new_evaluations: int | None,
        used: int,
    ) -> CampaignOutcome:
        names = self._fidelity_names()
        for rank in range(start_rank, len(names)):
            source_fidelity, target_fidelity = names[rank - 1], names[rank]
            self._active_fidelity = target_fidelity
            promotion_generation = evolution_generation + rank
            role = f"promotion_{target_fidelity}"
            if not self.store.load_candidates(trial, promotion_generation, role):
                self.store.save_candidates(
                    trial,
                    promotion_generation,
                    role,
                    self._promotion_candidates(
                        source_fidelity,
                        target_fidelity,
                        trial,
                        promotion_generation,
                    ),
                )
            checkpoint = {
                "status": "promoting",
                "trial": trial,
                "generation": promotion_generation,
                "evolution_generation": evolution_generation,
                "role": role,
                "promotion_rank": rank,
                "fidelity": target_fidelity,
                "archive_size": self._archive_total(),
            }
            self.store.checkpoint(checkpoint)
            complete, used = self._evaluate_phase(
                trial,
                promotion_generation,
                role,
                max_new_evaluations,
                used,
            )
            if not complete:
                self.store.checkpoint(checkpoint)
                return CampaignOutcome(
                    False,
                    trial,
                    promotion_generation,
                    used,
                    self._archive_total(),
                    str(self.store.checkpoint_path),
                )
            evaluated = self._evaluated(
                self.store.load_candidates(trial, promotion_generation, role)
            )
            source_by_candidate = {
                entry.result.candidate_id: entry.result.evaluation_key
                for entry in self.archive_for(trial, source_fidelity).entries()
            }
            for entry in evaluated:
                self.store.record_promotion(
                    source_fidelity,
                    target_fidelity,
                    source_by_candidate.get(entry.result.candidate_id),
                    entry.result,
                )
            self._update_archive(evaluated, promotion_generation)
        self.store.checkpoint(
            {
                "status": "complete",
                "trial": trial,
                "generation": evolution_generation,
                "role": "parents",
                "archive_size": self._archive_total(),
                "termination": "evolution_then_fidelity_confirmation",
                "confirmed_fidelity": names[-1],
            }
        )
        summarize_run(self.config.run_directory)
        return CampaignOutcome(
            True,
            trial,
            evolution_generation,
            used,
            self._archive_total(),
            str(self.store.checkpoint_path),
        )

    def _stall_state(self, trial: int, generation: int) -> tuple[bool, Mapping[str, Any]]:
        entries = self.archive_for(trial).entries()
        objectives = [entry.objectives for entry in entries if entry.result.feasible]
        history = list(self.store.get_metadata(f"stall_history_{trial}", []))
        if not objectives:
            indicator = None
        elif len(self.config.objectives) == 1:
            indicator = -min(value[0] for value in objectives)
        elif len(self.config.objectives) == 2:
            reference = self.store.get_metadata(f"stall_reference_{trial}")
            if reference is None:
                configured = self.config.algorithm.stall_reference
                if configured:
                    reference = list(configured)
                else:
                    reference = [
                        max(value[index] for value in objectives) + max(1.0, abs(max(value[index] for value in objectives))) * 0.1
                        for index in range(2)
                    ]
                self.store.set_metadata(f"stall_reference_{trial}", reference)
            individuals = [NSGA2Individual(str(index), tuple(value)) for index, value in enumerate(objectives)]
            indicator = exact_hypervolume_2d(individuals, tuple(reference))
        else:
            epsilon = self.config.algorithm.stall_epsilon
            cells = {
                tuple(math.floor(value / epsilon) for value in objectives_value)
                for objectives_value in objectives
            }
            indicator = float(len(cells))
        best = max((item["best"] for item in history if item.get("best") is not None), default=None)
        improved = indicator is not None and (
            best is None or indicator > best + self.config.algorithm.improvement_tolerance
        )
        best = indicator if improved else best
        last_improved = generation if improved else (history[-1].get("last_improved", 0) if history else 0)
        state = {"generation": generation, "indicator": indicator, "best": best, "last_improved": last_improved}
        history.append(state)
        self.store.set_metadata(f"stall_history_{trial}", history)
        stalled = generation - int(last_improved) >= self.config.algorithm.stall_generations
        return stalled, state

    def run(self, *, max_new_evaluations: int | None = None) -> CampaignOutcome:
        checkpoint = self.store.load_checkpoint()
        used = 0
        if checkpoint is None:
            trial, generation, role = 0, 0, "parents"
            initial = self._initial_population(trial)
            self.store.save_candidates(trial, generation, role, initial)
            self.store.checkpoint({"status": "evaluating", "trial": trial, "generation": generation, "role": role})
        elif checkpoint.get("status") == "complete":
            return CampaignOutcome(True, int(checkpoint["trial"]), int(checkpoint["generation"]), 0, int(checkpoint.get("archive_size", 0)), str(self.store.checkpoint_path))
        else:
            trial = int(checkpoint["trial"])
            generation = int(checkpoint["generation"])
            role = str(checkpoint.get("role", "parents"))
            if checkpoint.get("status") == "promoting":
                self._active_fidelity = str(checkpoint["fidelity"])
                return self._run_promotions(
                    trial=trial,
                    evolution_generation=int(checkpoint["evolution_generation"]),
                    start_rank=int(checkpoint["promotion_rank"]),
                    max_new_evaluations=max_new_evaluations,
                    used=0,
                )
            if checkpoint.get("status") == "generation_complete":
                stall = checkpoint.get("stall", {})
                stalled = (
                    generation - int(stall.get("last_improved", generation))
                    >= self.config.algorithm.stall_generations
                )
                if generation >= self.config.algorithm.generations or stalled:
                    if trial + 1 < self.config.algorithm.trials:
                        trial += 1
                        generation, role = 0, "parents"
                        self.store.save_candidates(
                            trial, generation, role, self._initial_population(trial)
                        )
                        self.store.checkpoint(
                            {
                                "status": "evaluating",
                                "trial": trial,
                                "generation": generation,
                                "role": role,
                            }
                        )
                    elif len(self._fidelity_names()) > 1:
                        return self._run_promotions(
                            trial=trial,
                            evolution_generation=generation,
                            max_new_evaluations=max_new_evaluations,
                            used=0,
                        )
                    else:
                        self.store.checkpoint(
                            {
                                "status": "complete",
                                "trial": trial,
                                "generation": generation,
                                "role": "parents",
                                "archive_size": self._archive_total(),
                                "termination": "stall" if stalled else "generation_limit",
                            }
                        )
                        summarize_run(self.config.run_directory)
                        return CampaignOutcome(
                            True,
                            trial,
                            generation,
                            0,
                            self._archive_total(),
                            str(self.store.checkpoint_path),
                        )
                else:
                    current = self._evaluated(
                        self.store.load_candidates(trial, generation, "parents")
                    )
                    generation += 1
                    role = "offspring"
                    self.store.save_candidates(
                        trial, generation, role, self._breed(current, trial, generation)
                    )
                    self.store.checkpoint(
                        {
                            "status": "evaluating",
                            "trial": trial,
                            "generation": generation,
                            "role": role,
                        }
                    )

        while trial < self.config.algorithm.trials:
            if self.cancel_event.is_set():
                self.store.checkpoint({"status": "interrupted", "trial": trial, "generation": generation, "role": role, "reason": "cancelled"})
                return CampaignOutcome(False, trial, generation, used, self._archive_total(), str(self.store.checkpoint_path))
            complete, used = self._evaluate_phase(
                trial, generation, role, max_new_evaluations, used
            )
            if not complete:
                self.store.checkpoint({"status": "interrupted", "trial": trial, "generation": generation, "role": role})
                return CampaignOutcome(False, trial, generation, used, self._archive_total(), str(self.store.checkpoint_path))

            current = self._evaluated(self.store.load_candidates(trial, generation, role))
            self._update_archive(current, generation)
            if role == "offspring":
                prior = self._evaluated(self.store.load_candidates(trial, generation - 1, "parents"))
                survivor_individuals = self.engine.survive(
                    [entry.individual for entry in prior], [entry.individual for entry in current]
                )
                chosen: list[EvaluatedCandidate] = []
                available = [*prior, *current]
                for survivor in survivor_individuals:
                    matches = [entry for entry in available if entry.candidate.individual_id == survivor.candidate_id]
                    if not matches:
                        raise RuntimeError(f"selected individual {survivor.candidate_id} is missing")
                    match = matches[0]
                    chosen.append(match)
                    available.remove(match)
                self.store.save_candidates(trial, generation, "parents", [entry.candidate for entry in chosen])
                accepted: dict[str, int] = {}
                self.store.save_population_results(
                    trial, generation, "parents", [entry.result for entry in chosen]
                )
                for entry in chosen:
                    if entry.candidate.generation == generation:
                        for operator_name in entry.candidate.operators:
                            accepted[operator_name] = accepted.get(operator_name, 0) + 1
                for operator_name, count in sorted(accepted.items()):
                    self.store.increment_operator(
                        trial, generation, operator_name, accepted=count
                    )
                current = chosen
                role = "parents"

            stalled, stall_state = self._stall_state(trial, generation)
            self.store.checkpoint({
                "status": "generation_complete",
                "trial": trial,
                "generation": generation,
                "role": "parents",
                "archive_size": self._archive_total(),
                "stall": stall_state,
            })
            if generation >= self.config.algorithm.generations or stalled:
                trial += 1
                if trial >= self.config.algorithm.trials:
                    if len(self._fidelity_names()) > 1:
                        return self._run_promotions(
                            trial=trial - 1,
                            evolution_generation=generation,
                            max_new_evaluations=max_new_evaluations,
                            used=used,
                        )
                    self.store.checkpoint({
                        "status": "complete",
                        "trial": trial - 1,
                        "generation": generation,
                        "role": "parents",
                        "archive_size": self._archive_total(),
                        "termination": "stall" if stalled else "generation_limit",
                    })
                    summarize_run(self.config.run_directory)
                    return CampaignOutcome(True, trial - 1, generation, used, self._archive_total(), str(self.store.checkpoint_path))
                generation, role = 0, "parents"
                initial = self._initial_population(trial)
                self.store.save_candidates(trial, generation, role, initial)
                self.store.checkpoint({"status": "evaluating", "trial": trial, "generation": generation, "role": role})
                continue

            offspring_generation = generation + 1
            offspring = self._breed(current, trial, offspring_generation)
            self.store.save_candidates(trial, offspring_generation, "offspring", offspring)
            generation, role = offspring_generation, "offspring"
            self.store.checkpoint({"status": "evaluating", "trial": trial, "generation": generation, "role": role})

        raise AssertionError("campaign loop ended unexpectedly")

    @classmethod
    def resume(cls, checkpoint: str | Path, **kwargs: Any) -> "Campaign":
        checkpoint_path = Path(checkpoint).resolve()
        run_directory = checkpoint_path.parent if checkpoint_path.is_file() else checkpoint_path
        if checkpoint_path.is_file():
            try:
                payload = __import__("json").loads(checkpoint_path.read_text(encoding="utf-8"))
                run_directory = Path(payload.get("run_directory", run_directory)).resolve()
            except (OSError, ValueError):
                pass
        resolved_path = run_directory / "resolved-config.json"
        if not resolved_path.is_file():
            raise FileNotFoundError("resolved-config.json is missing from the run directory")
        data = __import__("json").loads(resolved_path.read_text(encoding="utf-8"))
        # Resolved paths are absolute, so using the stored source path preserves
        # the original path semantics without consulting a mutable source file.
        source_path = data.pop("source_path", resolved_path)
        config = CampaignConfig.from_dict(data, source_path)
        return cls(config, **kwargs)


def _metric_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _operator_record(name: str, seed: int, before: Any, after: Any) -> OperatorRecord:
    left = genotype_to_dict(before)
    right = genotype_to_dict(after)
    affected: list[str] = []
    before_values: dict[str, Any] = {}
    after_values: dict[str, Any] = {}

    def visit(a: Any, b: Any, path: str) -> None:
        if isinstance(a, Mapping) and isinstance(b, Mapping):
            for key in sorted(set(a) | set(b)):
                visit(a.get(key), b.get(key), f"{path}.{key}" if path else str(key))
        elif isinstance(a, list) and isinstance(b, list):
            for index in range(max(len(a), len(b))):
                visit(
                    a[index] if index < len(a) else None,
                    b[index] if index < len(b) else None,
                    f"{path}[{index}]",
                )
        elif a != b:
            affected.append(path)
            before_values[path] = a
            after_values[path] = b

    visit(left, right, "")
    return OperatorRecord(
        operator=name,
        rng_seed=seed,
        affected_paths=tuple(affected),
        before=before_values,
        after=after_values,
        no_op=not affected,
    )
