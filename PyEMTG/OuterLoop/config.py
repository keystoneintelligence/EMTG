"""Versioned, declarative campaign configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import json
import shutil
from pathlib import Path
from collections.abc import Iterator
from typing import Any, Mapping

from .gene_names import canonicalize_mission_genes


class ConfigError(ValueError):
    pass


def _strict(data: Mapping[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ConfigError(f"unknown {where} field(s): {', '.join(unknown)}")


BUDGET_FIELDS = {
    "inner_loop", "mbh_max_run_time", "mbh_max_trials", "mbh_max_not_improve",
    "nlp_max_run_time", "nlp_major_iterations", "feasibility_tolerance",
    "optimality_tolerance", "nlp_solver_type", "quiet_nlp",
}


def _validate_budget(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{where} must be an object")
    data = dict(value)
    _strict(data, BUDGET_FIELDS, where)
    mode = str(data.get("inner_loop", "mbh")).lower()
    if mode not in {"mbh", "nlp", "trialx"}:
        raise ConfigError(f"{where}.inner_loop is unsupported: {mode}")
    for name in (
        "mbh_max_run_time", "mbh_max_trials", "mbh_max_not_improve",
        "nlp_max_run_time", "nlp_major_iterations", "feasibility_tolerance",
        "optimality_tolerance",
    ):
        if name in data and float(data[name]) <= 0:
            raise ConfigError(f"{where}.{name} must be positive")
    if "nlp_solver_type" in data and int(data["nlp_solver_type"]) not in {0, 2}:
        raise ConfigError(f"{where}.nlp_solver_type must be SNOPT (0) or IPOPT (2)")
    return data


@dataclass(frozen=True)
class ValidatedConfig(Mapping[str, Any]):
    """Immutable typed boundary for a validated configuration section."""

    values: Mapping[str, Any] = field(default_factory=dict)

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)


@dataclass(frozen=True)
class EvaluatorConfig(ValidatedConfig):
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvaluatorConfig":
        allowed = {
            "type", "problem", "body_scores", "hardware_scores", "flyby_penalty",
            "base_mass", "mass_cost_scale", "infeasible_above_cost",
            "timeout_seconds", "environment",
            "journey_template_index", "journey_templates", "expand_phase_genes",
            "constraint_migration_allowlist",
            "ephemeris_source_override",
            "cache_directory", "budget", "inner_trials", "supported_phase_types",
            "check_ephemeris_coverage",
        }
        _strict(data, allowed, "evaluator")
        kind = str(data.get("type", "synthetic"))
        if kind not in {"synthetic", "emtg"}:
            raise ConfigError(f"unknown evaluator type: {kind}")
        if "environment" in data and not isinstance(data["environment"], Mapping):
            raise ConfigError("evaluator.environment must be an object")
        values = dict(data)
        if "budget" in values:
            values["budget"] = _validate_budget(values["budget"], "evaluator.budget")
        if int(data.get("inner_trials", 1)) < 1:
            raise ConfigError("evaluator.inner_trials must be positive")
        return cls(values)


@dataclass(frozen=True)
class FidelityConfig(ValidatedConfig):
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FidelityConfig":
        _strict(data, {"name", "rank", "budget", "promote_count", "promote_fraction", "seed_converter"}, "fidelity")
        if not data.get("name") or not isinstance(data.get("budget", {}), Mapping):
            raise ConfigError("fidelity requires a name and object budget")
        if int(data.get("promote_count", 0)) < 0:
            raise ConfigError("fidelity.promote_count cannot be negative")
        values = dict(data)
        values["budget"] = _validate_budget(values.get("budget", {}), "fidelity.budget")
        return cls(values)


@dataclass(frozen=True)
class SeedConfig(ValidatedConfig):
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SeedConfig":
        allowed = {
            "inventory", "folders", "warm_population", "warm_archive", "warm_run_directory",
            "warm_each_trial", "legacy_gene_mapping", "candidates", "allow_fidelity_transfer",
            "include_infeasible", "distance_weights", "converter", "target_xdescriptions",
            "external_provider", "family_ranking", "quality_ranking",
            "qualification_seed_set",
        }
        _strict(data, allowed, "seeds")
        if int(data.get("candidates", 1)) < 1:
            raise ConfigError("seeds.candidates must be positive")
        provider = data.get("external_provider")
        if provider is not None:
            if not isinstance(provider, Mapping):
                raise ConfigError("seeds.external_provider must be an object")
            _strict(provider, {"type", "command", "timeout_seconds", "environment"}, "seeds.external_provider")
            command = provider.get("command")
            if not isinstance(command, (list, tuple)) or not command or not all(isinstance(v, str) and v for v in command):
                raise ConfigError("seeds.external_provider.command must be a non-empty string array")
        return cls(dict(data))


@dataclass(frozen=True)
class ResonanceConfig(ValidatedConfig):
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResonanceConfig":
        _strict(data, {"enabled", "ratios", "universe_file", "central_body", "minimum_turning_degrees", "replacement_probability"}, "resonance")
        probability = float(data.get("replacement_probability", 0.0))
        if not 0.0 <= probability <= 1.0:
            raise ConfigError("resonance.replacement_probability must be between zero and one")
        return cls(dict(data))


@dataclass(frozen=True)
class WorkerConfig:
    count: int = 4
    infrastructure_retries: int = 1
    backend: str = "local"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkerConfig":
        _strict(data, {"count", "infrastructure_retries", "backend"}, "workers")
        result = cls(
            count=int(data.get("count", 4)),
            infrastructure_retries=int(data.get("infrastructure_retries", 1)),
            backend=str(data.get("backend", "local")),
        )
        if result.count < 1 or result.infrastructure_retries < 0:
            raise ConfigError("workers count must be positive and retries nonnegative")
        if result.backend != "local":
            raise ConfigError("only the local worker backend is supported in core")
        return result


@dataclass(frozen=True)
class AssetConfig(ValidatedConfig):
    @classmethod
    def from_dict(cls, data: Mapping[str, Any], root: Path) -> "AssetConfig":
        _strict(
            data,
            {"executable", "universe_folder", "hardware_path", "capabilities_file", "brief_executable"},
            "assets",
        )
        values = {
            key: str((root / str(value)).resolve())
            for key, value in data.items()
            if value is not None
        }
        return cls(values)


def _validated_records(values: Any, *, kind: str, allowed: set[str], require_type: bool = False) -> tuple[ValidatedConfig, ...]:
    if not isinstance(values, (list, tuple)):
        raise ConfigError(f"{kind} must be an array")
    output = []
    for index, raw in enumerate(values):
        if not isinstance(raw, Mapping):
            raise ConfigError(f"{kind}[{index}] must be an object")
        _strict(raw, allowed, f"{kind}[{index}]")
        if require_type and not raw.get("type"):
            raise ConfigError(f"{kind}[{index}].type is required")
        output.append(ValidatedConfig(dict(raw)))
    return tuple(output)


@dataclass(frozen=True)
class GeneSpec:
    kind: str
    fixed: Any = None
    choices: tuple[Any, ...] = ()
    lower: Decimal | None = None
    upper: Decimal | None = None
    resolution: Decimal | None = None

    @property
    def variable(self) -> bool:
        return self.kind != "fixed"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], where: str) -> "GeneSpec":
        _strict(data, {"kind", "value", "choices", "lower", "upper", "resolution"}, where)
        kind = str(data.get("kind", "choice"))
        if kind not in {"fixed", "choice", "integer", "decimal"}:
            raise ConfigError(f"{where}.kind is unsupported: {kind}")
        fixed = data.get("value") if kind == "fixed" else None
        choices = tuple(data.get("choices", ()))
        lower = Decimal(str(data["lower"])) if data.get("lower") is not None else None
        upper = Decimal(str(data["upper"])) if data.get("upper") is not None else None
        resolution = Decimal(str(data["resolution"])) if data.get("resolution") is not None else None
        if kind == "fixed" and ("value" not in data or data.get("value") is None):
            raise ConfigError(f"{where}.value is required for a fixed gene")
        if kind == "choice" and not choices:
            raise ConfigError(f"{where}.choices must be non-empty")
        if fixed is None and kind in {"integer", "decimal"}:
            if lower is None or upper is None or lower > upper:
                raise ConfigError(f"{where} requires ordered lower/upper bounds")
        if kind == "decimal" and fixed is None and (resolution is None or resolution <= 0):
            raise ConfigError(f"{where} requires a positive resolution")
        return cls(kind, fixed, choices, lower, upper, resolution)


@dataclass(frozen=True)
class SearchConfig:
    max_journeys: int
    min_journeys: int = 1
    max_flybys: int = 0
    min_flybys: int = 0
    fixed_start: str | None = None
    fixed_final: str | None = None
    chain_journeys: bool = True
    activation_mode: str = "tags"
    mission_genes: Mapping[str, GeneSpec] = field(default_factory=dict)
    journey_genes: Mapping[str, GeneSpec] = field(default_factory=dict)
    phase_genes: Mapping[str, GeneSpec] = field(default_factory=dict)
    flyby_bodies: tuple[str, ...] = ()
    repairs: tuple[str, ...] = ()

    @property
    def repair_policy(self) -> str:
        """Compatibility view used by the internal campaign pipeline."""
        compact = "compact" in self.repairs
        group = "group_replace" in self.repairs
        if compact and group:
            return "compact_group_replace"
        if compact:
            return "compact"
        if group:
            return "group_replace"
        return "reject"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SearchConfig":
        _strict(
            data,
            {
                "max_journeys", "min_journeys", "max_flybys", "min_flybys",
                "fixed_start", "fixed_final", "chain_journeys", "activation_mode",
                "mission_genes", "journey_genes", "phase_genes", "flyby_bodies",
                "repairs",
            },
            "search",
        )
        max_journeys = int(data.get("max_journeys", 1))
        min_journeys = int(data.get("min_journeys", 1))
        max_flybys = int(data.get("max_flybys", 0))
        min_flybys = int(data.get("min_flybys", 0))
        if not 1 <= min_journeys <= max_journeys:
            raise ConfigError("search journey limits are inconsistent")
        if not 0 <= min_flybys <= max_flybys:
            raise ConfigError("search flyby limits are inconsistent")
        mode = str(data.get("activation_mode", "tags"))
        if mode not in {"tags", "count", "tags_and_count"}:
            raise ConfigError("search.activation_mode must be tags, count, or tags_and_count")
        repairs = tuple(str(value) for value in data.get("repairs", ()))
        supported_repairs = {"compact", "reconnect_endpoints", "group_replace", "clamp_bounds"}
        unknown_repairs = sorted(set(repairs) - supported_repairs)
        if unknown_repairs or len(set(repairs)) != len(repairs):
            raise ConfigError(f"search.repairs contains unsupported or duplicate names: {unknown_repairs}")

        def genes(name: str) -> dict[str, GeneSpec]:
            raw = data.get(name, {})
            if not isinstance(raw, Mapping):
                raise ConfigError(f"search.{name} must be an object")
            if name == "mission_genes":
                try:
                    raw = canonicalize_mission_genes(raw)
                except ValueError as error:
                    raise ConfigError(f"search.{name}: {error}") from error
            return {
                str(key): GeneSpec.from_dict(value, f"search.{name}.{key}")
                for key, value in raw.items()
            }

        flyby_bodies = tuple(str(body) for body in data.get("flyby_bodies", ()))
        if max_flybys and not flyby_bodies:
            raise ConfigError("search.flyby_bodies is required when max_flybys is nonzero")
        mission_genes = genes("mission_genes")
        journey_genes = genes("journey_genes")
        phase_genes = genes("phase_genes")
        if "fidelity" in phase_genes or "fidelity" in journey_genes:
            raise ConfigError("fidelity is an evaluation dimension and cannot be a journey/phase gene")
        if "periapse_burn_enabled" in mission_genes or "periapse_burn_enabled" in phase_genes:
            raise ConfigError("periapse_burn_enabled is supported only at EMTG journey scope")
        return cls(
            max_journeys=max_journeys,
            min_journeys=min_journeys,
            max_flybys=max_flybys,
            min_flybys=min_flybys,
            fixed_start=data.get("fixed_start"),
            fixed_final=data.get("fixed_final"),
            chain_journeys=bool(data.get("chain_journeys", True)),
            activation_mode=mode,
            mission_genes=mission_genes,
            journey_genes=journey_genes,
            phase_genes=phase_genes,
            flyby_bodies=flyby_bodies,
            repairs=repairs,
        )


@dataclass(frozen=True)
class NSGA2Config:
    population_size: int = 40
    generations: int = 20
    tournament_size: int = 2
    crossover_probability: float = 0.9
    mutation_probability: float = 0.2
    stall_generations: int = 20
    improvement_tolerance: float = 1.0e-6
    trials: int = 1
    extra_elites: int = 0
    stall_reference: tuple[float, ...] = ()
    stall_epsilon: float = 1.0e-3

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NSGA2Config":
        _strict(data, set(cls.__dataclass_fields__), "algorithm")
        values = {key: data[key] for key in data}
        if "stall_reference" in values:
            values["stall_reference"] = tuple(float(value) for value in values["stall_reference"])
        result = cls(**values)
        if result.population_size < 2 or result.generations < 0:
            raise ConfigError("algorithm population/generation values are invalid")
        if result.tournament_size < 2 or result.tournament_size > result.population_size:
            raise ConfigError("algorithm.tournament_size is invalid")
        for name in ("crossover_probability", "mutation_probability"):
            if not 0.0 <= getattr(result, name) <= 1.0:
                raise ConfigError(f"algorithm.{name} must be between zero and one")
        if result.trials < 1 or result.extra_elites < 0:
            raise ConfigError("algorithm trials/elites are invalid")
        if result.stall_epsilon <= 0:
            raise ConfigError("algorithm.stall_epsilon must be positive")
        return result


@dataclass(frozen=True)
class ObjectiveConfig:
    name: str
    direction: str | None = None
    scale: float = 1.0
    units: str | None = None
    source: str | None = None
    missing_policy: str | None = None
    penalty: float | None = None
    valid_for_infeasible: bool | None = None

    @classmethod
    def from_value(cls, value: str | Mapping[str, Any]) -> "ObjectiveConfig":
        if isinstance(value, str):
            return cls(value)
        _strict(value, {"name", "direction", "scale", "units", "source", "missing_policy", "penalty", "valid_for_infeasible"}, "objective")
        direction = value.get("direction")
        result = cls(
            str(value["name"]), str(direction) if direction is not None else None,
            float(value.get("scale", 1.0)), value.get("units"), value.get("source"),
            value.get("missing_policy"),
            float(value["penalty"]) if value.get("penalty") is not None else None,
            (bool(value["valid_for_infeasible"])
             if value.get("valid_for_infeasible") is not None else None),
        )
        if (result.direction not in {None, "minimize", "maximize"} or result.scale <= 0
                or result.missing_policy not in {None, "reject", "penalize"}):
            raise ConfigError(f"invalid objective definition: {result.name}")
        if result.missing_policy == "penalize" and result.penalty is None:
            raise ConfigError(f"objective {result.name} requires penalty when missing_policy is penalize")
        return result


@dataclass(frozen=True)
class CampaignConfig:
    schema_version: str
    source_path: Path
    base_case: Path | None
    run_directory: Path
    root_seed: int
    search: SearchConfig
    objectives: tuple[ObjectiveConfig, ...]
    algorithm: NSGA2Config = NSGA2Config()
    evaluator: EvaluatorConfig = EvaluatorConfig()
    assets: AssetConfig = AssetConfig()
    operators: Mapping[str, float] = field(default_factory=dict)
    constraints: tuple[ValidatedConfig, ...] = ()
    groups: tuple[ValidatedConfig, ...] = ()
    prefilters: tuple[ValidatedConfig, ...] = ()
    fidelities: tuple[FidelityConfig, ...] = ()
    seeds: SeedConfig = SeedConfig()
    workers: WorkerConfig = WorkerConfig()
    checkpoint_every: int = 1
    resonance: ResonanceConfig = ResonanceConfig()
    templates: ValidatedConfig = ValidatedConfig()
    resources: ValidatedConfig = ValidatedConfig()
    cache: ValidatedConfig = ValidatedConfig()
    checkpoints: ValidatedConfig = ValidatedConfig()
    outputs: ValidatedConfig = ValidatedConfig()

    @classmethod
    def from_file(cls, path: str | Path) -> "CampaignConfig":
        source = Path(path).resolve()
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ConfigError(f"cannot read campaign configuration: {error}") from error
        if not isinstance(data, Mapping):
            raise ConfigError("campaign configuration must be a JSON object")
        return cls.from_dict(data, source)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], source_path: str | Path = "campaign.json") -> "CampaignConfig":
        allowed = {
            "schema_version", "base_case", "run_directory", "root_seed", "search",
            "objectives", "algorithm", "evaluator", "assets", "operators", "constraints", "groups",
            "prefilters", "fidelities", "seeds", "workers", "checkpoint_every",
            "resonance", "templates", "resources", "cache", "checkpoints", "outputs",
        }
        _strict(data, allowed, "campaign")
        if data.get("schema_version") != "outerloop/v2":
            raise ConfigError("schema_version must be 'outerloop/v2'")
        source = Path(source_path).resolve()
        root = source.parent
        base_value = data.get("base_case")
        base_case = (root / str(base_value)).resolve() if base_value else None
        run_value = data.get("run_directory", "outerloop-run")
        run_directory = (root / str(run_value)).resolve()
        objectives = tuple(ObjectiveConfig.from_value(value) for value in data.get("objectives", ()))
        if not objectives:
            raise ConfigError("at least one objective is required")
        evaluator = data.get("evaluator", {"type": "synthetic"})
        if not isinstance(evaluator, Mapping):
            raise ConfigError("evaluator must be an object")
        evaluator = dict(evaluator)
        if evaluator.get("cache_directory"):
            evaluator["cache_directory"] = str((root / str(evaluator["cache_directory"])).resolve())
        assets = AssetConfig.from_dict(data.get("assets", {}), root)
        worker_data = data.get("workers", {})
        if not isinstance(worker_data, Mapping):
            raise ConfigError("workers must be an object")
        workers = WorkerConfig.from_dict(worker_data)
        checkpoint_every = int(data.get("checkpoint_every", 1))
        if checkpoint_every < 1:
            raise ConfigError("checkpoint_every must be positive")
        fidelity_values = tuple(FidelityConfig.from_dict(value) for value in data.get("fidelities", ()))
        if fidelity_values:
            ranks = sorted(int(value.get("rank", -1)) for value in fidelity_values)
            names = [str(value.get("name", "")) for value in fidelity_values]
            if ranks != list(range(len(fidelity_values))) or any(not name for name in names) or len(set(names)) != len(names):
                raise ConfigError("fidelities require unique names and contiguous ranks from zero")
            if any(not isinstance(value.get("budget", {}), Mapping) for value in fidelity_values):
                raise ConfigError("each fidelity budget must be an object")
        seed_settings = dict(data.get("seeds", {}))
        for path_key in ("inventory", "warm_population", "warm_archive", "warm_run_directory"):
            if seed_settings.get(path_key):
                seed_settings[path_key] = str((root / str(seed_settings[path_key])).resolve())
        if seed_settings.get("folders"):
            if not isinstance(seed_settings["folders"], (list, tuple)):
                raise ConfigError("seeds.folders must be an array")
            seed_settings["folders"] = [
                str((root / str(value)).resolve()) for value in seed_settings["folders"]
            ]
        provider_settings = seed_settings.get("external_provider")
        if (
            isinstance(provider_settings, Mapping)
            and isinstance(provider_settings.get("command"), (list, tuple))
            and provider_settings.get("command")
        ):
            provider_settings = dict(provider_settings)
            command = list(provider_settings["command"])
            first = Path(command[0])
            if not first.is_absolute() and ("/" in command[0] or "\\" in command[0]):
                command[0] = str((root / first).resolve())
            provider_settings["command"] = command
            seed_settings["external_provider"] = provider_settings
        operator_weights = {str(k): float(v) for k, v in data.get("operators", {}).items()}
        if any(weight < 0 for weight in operator_weights.values()) or (
            operator_weights and not any(weight > 0 for weight in operator_weights.values())
        ):
            raise ConfigError("operator weights must be nonnegative with at least one positive weight")
        constraints = _validated_records(
            data.get("constraints", ()), kind="constraints",
            allowed={"name", "metric", "lower", "upper", "scale", "units", "missing_behavior"},
        )
        for index, constraint in enumerate(constraints):
            if not constraint.get("name") or not constraint.get("metric"):
                raise ConfigError(f"constraints[{index}] requires name and metric")
            if float(constraint.get("scale", 1.0)) <= 0:
                raise ConfigError(f"constraints[{index}].scale must be positive")
            if constraint.get("missing_behavior", "reject") not in {"reject", "ignore"}:
                raise ConfigError(f"constraints[{index}].missing_behavior is unsupported")
        prefilters = _validated_records(data.get("prefilters", ()), kind="prefilters", allowed={
            "type", "strict", "heuristic", "audit_fraction", "allow", "value", "metric", "lower", "upper",
            "bodies", "pairs", "limits", "groups", "factor", "maximum_departure_c3",
            "maximum_arrival_c3", "maximum_degrees", "minimum_mass", "provider",
        }, require_type=True)
        supported_filters = {
            "successive_duplicate", "maximum_flybys", "numeric_envelope", "minimum_flight_time",
            "c3_envelope", "inclination_bandpass", "allowed_bodies", "forbidden_bodies",
            "forbidden_pairs", "allowed_successive", "maximum_repeats", "mandatory_destinations",
            "required_groups", "forbidden_groups", "delivered_mass_heuristic", "flyby_altitude",
            "lambert_provider", "patched_conic_provider",
        }
        for index, prefilter in enumerate(prefilters):
            if prefilter["type"] not in supported_filters:
                raise ConfigError(f"prefilters[{index}].type is unsupported: {prefilter['type']}")
            fraction = float(prefilter.get("audit_fraction", 0.05))
            if not 0.0 <= fraction <= 1.0:
                raise ConfigError(f"prefilters[{index}].audit_fraction must be between zero and one")
            if prefilter["type"].endswith("_provider") and not prefilter.get("provider"):
                raise ConfigError(f"prefilters[{index}].provider is required")
            if prefilter["type"].endswith("_provider"):
                provider = prefilter.get("provider")
                if not isinstance(provider, Mapping):
                    raise ConfigError(f"prefilters[{index}].provider must be an object")
                _strict(provider, {"command", "timeout_seconds", "environment"}, f"prefilters[{index}].provider")
                command = provider.get("command")
                if not isinstance(command, (list, tuple)) or not command or not all(isinstance(v, str) and v for v in command):
                    raise ConfigError(f"prefilters[{index}].provider.command must be a non-empty string array")
                resolved_command = list(command)
                first = Path(resolved_command[0])
                if not first.is_absolute() and ("/" in resolved_command[0] or "\\" in resolved_command[0]):
                    resolved_command[0] = str((root / first).resolve())
                provider = dict(provider)
                provider["command"] = resolved_command
                prefilter.values["provider"] = provider
        templates = dict(data.get("templates", {}))
        _strict(templates, {"journeys", "inherit_trial_vectors", "constraint_migration_allowlist"}, "templates")
        if templates.get("inherit_trial_vectors") is True:
            raise ConfigError("templates.inherit_trial_vectors is unsupported; trial vectors are never inherited automatically")
        if "journeys" in templates and (
            not isinstance(templates["journeys"], Mapping)
            or any(not str(name) or int(index) < 0 for name, index in templates["journeys"].items())
        ):
            raise ConfigError("templates.journeys must map logical names to nonnegative base journey indices")
        resources = dict(data.get("resources", {}))
        _strict(resources, {"cpu_seconds", "memory_bytes", "processes_per_worker", "worker_memory_bytes"}, "resources")
        if any(float(value) <= 0 for value in resources.values()):
            raise ConfigError("resource limits must be positive")
        cache = dict(data.get("cache", {}))
        _strict(cache, {"directory", "explain_misses"}, "cache")
        if cache.get("directory"):
            cache["directory"] = str((root / str(cache["directory"])).resolve())
        checkpoints = dict(data.get("checkpoints", {}))
        _strict(checkpoints, {"every", "directory"}, "checkpoints")
        if int(checkpoints.get("every", checkpoint_every)) < 1:
            raise ConfigError("checkpoints.every must be positive")
        if checkpoints.get("directory"):
            checkpoints["directory"] = str((root / str(checkpoints["directory"])).resolve())
        outputs = dict(data.get("outputs", {}))
        _strict(outputs, {"directory", "legacy", "default_fidelity", "event_table"}, "outputs")
        if outputs.get("directory"):
            outputs["directory"] = str((root / str(outputs["directory"])).resolve())
        return cls(
            schema_version="outerloop/v2",
            source_path=source,
            base_case=base_case,
            run_directory=run_directory,
            root_seed=int(data.get("root_seed", 0)),
            search=SearchConfig.from_dict(data.get("search", {})),
            objectives=objectives,
            algorithm=NSGA2Config.from_dict(data.get("algorithm", {})),
            evaluator=EvaluatorConfig.from_dict(evaluator),
            assets=assets,
            operators=operator_weights,
            constraints=constraints,
            groups=_validated_records(data.get("groups", ()), kind="groups", allowed={
                "name", "members", "minimum_visits", "maximum_visits", "score_per_member",
                "completion_bonus", "members_to_score", "distinct_members", "score_cap",
                "target_role", "as_constraint",
            }),
            prefilters=prefilters,
            fidelities=fidelity_values,
            seeds=SeedConfig.from_dict(seed_settings),
            workers=workers,
            checkpoint_every=int(checkpoints.get("every", checkpoint_every)),
            resonance=ResonanceConfig.from_dict(data.get("resonance", {})),
            templates=ValidatedConfig(templates),
            resources=ValidatedConfig(resources),
            cache=ValidatedConfig(cache),
            checkpoints=ValidatedConfig(checkpoints),
            outputs=ValidatedConfig(outputs),
        )

    def validate_paths(self, *, require_executable: bool = True) -> list[str]:
        errors: list[str] = []
        evaluator_type = str(self.evaluator.get("type", "synthetic"))
        if evaluator_type == "emtg":
            if self.base_case is None or not self.base_case.is_file():
                errors.append("base_case does not exist")
            executable = self.assets.get("executable")
            if require_executable and (not executable or not Path(str(executable)).expanduser().is_file()):
                errors.append("assets.executable does not exist")
            for name in ("universe_folder", "hardware_path"):
                value = self.assets.get(name)
                if not value or not Path(str(value)).is_dir():
                    errors.append(f"assets.{name} does not exist")
            for name in ("capabilities_file", "brief_executable"):
                value = self.assets.get(name)
                if value and not Path(str(value)).is_file():
                    errors.append(f"assets.{name} does not exist")
            if "timeout_seconds" not in self.evaluator:
                errors.append("evaluator.timeout_seconds is required for EMTG")
        elif evaluator_type != "synthetic":
            errors.append(f"unknown evaluator type: {evaluator_type}")
        provider = self.seeds.get("external_provider")
        if provider:
            command = provider.get("command", ())
            executable = str(command[0]) if command else ""
            if executable and not Path(executable).is_file() and shutil.which(executable) is None:
                errors.append("seeds.external_provider.command executable does not exist")
        return errors

    def resolved_dict(self) -> dict[str, Any]:
        def genes(values: Mapping[str, GeneSpec]) -> dict[str, Any]:
            return {
                key: {
                    "kind": value.kind,
                    "value": value.fixed if value.kind == "fixed" else None,
                    "choices": list(value.choices),
                    "lower": str(value.lower) if value.lower is not None else None,
                    "upper": str(value.upper) if value.upper is not None else None,
                    "resolution": str(value.resolution) if value.resolution is not None else None,
                }
                for key, value in values.items()
            }
        return {
            "schema_version": self.schema_version,
            "source_path": str(self.source_path),
            "base_case": str(self.base_case) if self.base_case else None,
            "run_directory": str(self.run_directory),
            "root_seed": self.root_seed,
            "search": {
                "max_journeys": self.search.max_journeys,
                "min_journeys": self.search.min_journeys,
                "max_flybys": self.search.max_flybys,
                "min_flybys": self.search.min_flybys,
                "fixed_start": self.search.fixed_start,
                "fixed_final": self.search.fixed_final,
                "chain_journeys": self.search.chain_journeys,
                "activation_mode": self.search.activation_mode,
                "mission_genes": genes(self.search.mission_genes),
                "journey_genes": genes(self.search.journey_genes),
                "phase_genes": genes(self.search.phase_genes),
                "flyby_bodies": list(self.search.flyby_bodies),
                "repairs": list(self.search.repairs),
            },
            "objectives": [vars(value) for value in self.objectives],
            "algorithm": vars(self.algorithm),
            "evaluator": dict(self.evaluator),
            "assets": dict(self.assets),
            "operators": dict(self.operators),
            "constraints": [dict(value) for value in self.constraints],
            "groups": [dict(value) for value in self.groups],
            "prefilters": [dict(value) for value in self.prefilters],
            "fidelities": [dict(value) for value in self.fidelities],
            "seeds": dict(self.seeds),
            "workers": vars(self.workers),
            "checkpoint_every": self.checkpoint_every,
            "resonance": dict(self.resonance),
            "templates": dict(self.templates),
            "resources": dict(self.resources),
            "cache": dict(self.cache),
            "checkpoints": dict(self.checkpoints),
            "outputs": dict(self.outputs),
        }
