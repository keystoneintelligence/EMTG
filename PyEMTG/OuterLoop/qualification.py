"""Exhaustive finite-space qualification against canonical Pareto truth."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
import itertools
import json
import math
import statistics
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .archive import ArchiveEntry, ParetoArchive
from .campaign import Campaign
from .config import GeneSpec
from .genome import GenomeSchema, sample_gene
from .model import Genotype, HiddenGeneSlot, JourneyGenome
from .randomness import random_stream
from .storage import atomic_write_json


def gene_domain(spec: GeneSpec, maximum_values: int = 100) -> tuple[Any, ...]:
    if spec.fixed is not None:
        return (spec.fixed,)
    if spec.kind == "choice":
        return spec.choices
    if spec.kind == "boolean":
        return (False, True)
    if spec.kind == "integer":
        values = tuple(range(int(spec.lower), int(spec.upper) + 1))  # type: ignore[arg-type]
    else:
        count = int((spec.upper - spec.lower) / spec.resolution) + 1  # type: ignore[operator]
        values = tuple(
            str((spec.lower + index * spec.resolution).normalize())  # type: ignore[operator]
            for index in range(count)
        )
    if len(values) > maximum_values:
        raise ValueError(f"gene domain has {len(values)} values; maximum is {maximum_values}")
    return values


def _gene_assignments(specs: Mapping[str, GeneSpec]) -> tuple[dict[str, Any], ...]:
    names = tuple(sorted(specs))
    domains = [gene_domain(specs[name]) for name in names]
    return tuple(dict(zip(names, values)) for values in itertools.product(*domains)) if names else ({},)


def _journey_variants(schema: GenomeSchema) -> tuple[JourneyGenome, ...]:
    search = schema.search
    journey_assignments = _gene_assignments(search.journey_genes)
    phase_assignments = _gene_assignments(search.phase_genes)
    output = []
    for values in journey_assignments:
        for flyby_count in range(search.min_flybys, search.max_flybys + 1):
            for bodies in itertools.product(search.flyby_bodies, repeat=flyby_count):
                for active_phase_values in itertools.product(phase_assignments, repeat=flyby_count + 1):
                    flybys = tuple(
                        HiddenGeneSlot(
                            index < flyby_count,
                            {
                                "flyby_body": bodies[index]
                                if index < flyby_count
                                else (search.flyby_bodies[0] if search.flyby_bodies else None)
                            },
                        )
                        for index in range(search.max_flybys)
                    )
                    phases = []
                    for index in range(search.max_flybys):
                        phase_values = active_phase_values[index] if index < flyby_count else phase_assignments[0]
                        phases.append(HiddenGeneSlot(index < flyby_count, phase_values))
                    phases.append(HiddenGeneSlot(True, active_phase_values[-1]))
                    output.append(JourneyGenome(True, values, flybys, tuple(phases)))
    return tuple(output)


def enumerate_genotypes(
    schema: GenomeSchema,
    *,
    maximum_architectures: int = 100000,
) -> Iterator[Genotype]:
    search = schema.search
    mission_assignments = _gene_assignments(search.mission_genes)
    journey_variants = _journey_variants(schema)
    inactive_template = JourneyGenome(
        False,
        _gene_assignments(search.journey_genes)[0],
        tuple(
            HiddenGeneSlot(False, {"flyby_body": search.flyby_bodies[0] if search.flyby_bodies else None})
            for _ in range(search.max_flybys)
        ),
        tuple(HiddenGeneSlot(False if index < search.max_flybys else True, _gene_assignments(search.phase_genes)[0]) for index in range(search.max_flybys + 1)),
    )
    produced = 0
    for mission in mission_assignments:
        for journey_count in range(search.min_journeys, search.max_journeys + 1):
            for active_journeys in itertools.product(journey_variants, repeat=journey_count):
                produced += 1
                if produced > maximum_architectures:
                    raise ValueError(
                        f"enumeration exceeds maximum_architectures={maximum_architectures}"
                    )
                mission_values = dict(mission)
                if search.activation_mode in {"count", "tags_and_count"}:
                    mission_values["number_of_journeys"] = journey_count
                journeys = [*active_journeys]
                journeys.extend(inactive_template for _ in range(search.max_journeys - journey_count))
                yield Genotype(mission_values, tuple(journeys))


@dataclass(frozen=True)
class ExhaustiveReport:
    total_genotypes: int
    unique_phenotypes: int
    duplicate_genotypes: int
    cache_hits_before_run: int
    structural_or_filtered: int
    evaluator_executions: int
    feasible: int
    pareto_size: int
    pareto_candidate_ids: tuple[str, ...]
    recovered_candidate_ids: tuple[str, ...]
    pareto_recall: float | None
    runtime_seconds: float


@dataclass(frozen=True)
class ProductionGateAssessment:
    trials: int
    median_pareto_recall: float
    exact_front_recoveries: int
    exact_recovery_interval: tuple[float, float]
    passed: bool


def assess_production_trials(
    recalls: Sequence[float], exact_recoveries: Sequence[bool], *, confidence: float = 0.95
) -> ProductionGateAssessment:
    """Assess the ten-trial gate and report a Wilson binomial interval."""
    if len(recalls) != len(exact_recoveries) or not recalls:
        raise ValueError("recalls and exact_recoveries require equal non-empty lengths")
    if any(not 0.0 <= value <= 1.0 for value in recalls):
        raise ValueError("Pareto recall values must be between zero and one")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between zero and one")
    trials = len(recalls)
    successes = sum(bool(value) for value in exact_recoveries)
    z = statistics.NormalDist().inv_cdf(0.5 + confidence / 2.0)
    proportion = successes / trials
    denominator = 1.0 + z * z / trials
    center = (proportion + z * z / (2.0 * trials)) / denominator
    radius = z * math.sqrt(
        proportion * (1.0 - proportion) / trials + z * z / (4.0 * trials * trials)
    ) / denominator
    median = float(statistics.median(recalls))
    return ProductionGateAssessment(
        trials,
        median,
        successes,
        (max(0.0, center - radius), min(1.0, center + radius)),
        trials >= 10 and median >= 0.90 and successes >= 8,
    )


def qualify_exhaustively(
    campaign: Campaign,
    *,
    maximum_architectures: int = 100000,
    evolution_trial: int = 0,
) -> ExhaustiveReport:
    # Qualification has its own transactional state.  Sharing only the
    # content cache preserves reuse without allowing an exhaustive run to
    # overwrite the evolutionary checkpoint.
    main_run = campaign.config.run_directory
    evaluator_settings = dict(campaign.config.evaluator)
    evaluator_settings["cache_directory"] = str(campaign.cache.root)
    qualification_config = replace(
        campaign.config,
        run_directory=main_run / "qualification" / f"work-trial-{evolution_trial}",
        evaluator=evaluator_settings,
        checkpoint_every=max(100, campaign.config.checkpoint_every),
    )
    qualification_campaign = Campaign(qualification_config)
    recovered_ids = tuple(
        sorted(
            {
                record["result"].candidate_id
                for record in campaign.store.archive_records(
                    campaign.fidelity, trial=evolution_trial,
                    comparison_context_id=campaign._comparison_context_id(evolution_trial),
                )
            }
        )
    )
    genotypes = list(
        enumerate_genotypes(
            qualification_campaign.schema,
            maximum_architectures=maximum_architectures,
        )
    )
    unique = {}
    for slot, genotype in enumerate(genotypes):
        candidate = qualification_campaign._candidate(
            genotype,
            trial=evolution_trial,
            generation=-1,
            slot=slot,
            operators=("exhaustive_enumeration",),
        )
        unique.setdefault(candidate.candidate_id, candidate)
    candidates = list(unique.values())
    role = f"exhaustive_{qualification_campaign.fidelity}"
    qualification_campaign.store.save_candidates(evolution_trial, -1, role, candidates)
    requests = [qualification_campaign._request(candidate) for candidate in candidates]
    cache_hits = sum(
        qualification_campaign.cache.get(request.evaluation_key) is not None
        for request in requests
    )
    complete, used = qualification_campaign._evaluate_phase(evolution_trial, -1, role, None, 0)
    if not complete:
        raise RuntimeError("unbounded exhaustive evaluation ended incomplete")
    qualification_campaign.store.checkpoint(
        {
            "status": "qualification_complete",
            "trial": evolution_trial,
            "generation": -1,
            "role": role,
        }
    )
    evaluated = qualification_campaign._evaluated(
        qualification_campaign.store.load_candidates(evolution_trial, -1, role)
    )
    archive = ParetoArchive()
    for entry in evaluated:
        if all(math.isfinite(value) for value in entry.individual.objectives):
            archive.update(ArchiveEntry(entry.result, entry.individual.objectives, -1))
    pareto_ids = tuple(sorted({entry.result.candidate_id for entry in archive.entries()}))
    recall = (
        len(set(pareto_ids).intersection(recovered_ids)) / len(pareto_ids)
        if pareto_ids and recovered_ids
        else None
    )
    structural = sum(
        entry.result.status.value.endswith("invalid") or "filtered" in entry.result.status.value
        for entry in evaluated
    )
    report = ExhaustiveReport(
        total_genotypes=len(genotypes),
        unique_phenotypes=len(candidates),
        duplicate_genotypes=len(genotypes) - len(candidates),
        cache_hits_before_run=cache_hits,
        structural_or_filtered=structural,
        evaluator_executions=used,
        feasible=sum(entry.result.feasible for entry in evaluated),
        pareto_size=len(pareto_ids),
        pareto_candidate_ids=pareto_ids,
        recovered_candidate_ids=recovered_ids,
        pareto_recall=recall,
        runtime_seconds=sum(entry.result.runtime_seconds for entry in evaluated),
    )
    output = main_run / "qualification" / f"exhaustive-trial-{evolution_trial}-{campaign.fidelity}.json"
    atomic_write_json(output, report.__dict__)
    return report


def qualify_trials(
    campaign: Campaign, *, maximum_architectures: int = 100000,
    require_production_gate: bool = True,
) -> tuple[tuple[ExhaustiveReport, ...], ProductionGateAssessment]:
    reports = tuple(
        qualify_exhaustively(
            campaign,
            maximum_architectures=maximum_architectures,
            evolution_trial=trial,
        )
        for trial in range(campaign.config.algorithm.trials)
    )
    recalls = [float(report.pareto_recall or 0.0) for report in reports]
    exact = [
        report.pareto_recall == 1.0 and report.pareto_size > 0
        for report in reports
    ]
    assessment = assess_production_trials(recalls, exact)
    if not require_production_gate:
        assessment = replace(
            assessment,
            passed=bool(reports) and all(
                report.pareto_recall == 1.0 and report.feasible > 0
                for report in reports
            ),
        )
    atomic_write_json(
        campaign.config.run_directory / "qualification" / "production-gate.json",
        {
            "schema_version": 3,
            "reports": [report.__dict__ for report in reports],
            "assessment": assessment.__dict__,
            "gate_kind": "production" if require_production_gate else "local",
        },
    )
    return reports, assessment
