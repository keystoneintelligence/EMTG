"""Campaign-level convergence, diversity, and execution accounting."""

from __future__ import annotations

from collections import Counter
import itertools
import math
from pathlib import Path
from typing import Any, Sequence

from .nsga2 import NSGA2Individual, exact_hypervolume_2d
from .storage import CampaignStore, atomic_write_json


def _lcs(left: Sequence[str], right: Sequence[str]) -> int:
    previous = [0] * (len(right) + 1)
    for left_value in left:
        current = [0]
        for index, right_value in enumerate(right, 1):
            current.append(
                previous[index - 1] + 1
                if left_value == right_value
                else max(previous[index], current[-1])
            )
        previous = current
    return previous[-1]


def architecture_distance(left: Any, right: Any) -> float:
    left_sequence = tuple(
        body for journey in left.journeys for body in journey.sequence
    )
    right_sequence = tuple(
        body for journey in right.journeys for body in journey.sequence
    )
    maximum = max(len(left_sequence), len(right_sequence), 1)
    sequence_distance = 1.0 - _lcs(left_sequence, right_sequence) / maximum
    journey_distance = abs(len(left.journeys) - len(right.journeys)) / max(
        len(left.journeys), len(right.journeys), 1
    )
    phase_left = tuple(
        phase.values.get("phase_type", journey.values.get("phase_type"))
        for journey in left.journeys
        for phase in journey.phases
    )
    phase_right = tuple(
        phase.values.get("phase_type", journey.values.get("phase_type"))
        for journey in right.journeys
        for phase in journey.phases
    )
    phase_maximum = max(len(phase_left), len(phase_right), 1)
    phase_distance = 1.0 - _lcs(tuple(map(str, phase_left)), tuple(map(str, phase_right))) / phase_maximum
    return 0.6 * sequence_distance + 0.2 * journey_distance + 0.2 * phase_distance


def summarize_run(run_directory: str | Path) -> dict[str, Any]:
    store = CampaignStore(run_directory)
    records = [
        record
        for record in store.generation_records()
        if not str(record["role"]).startswith("exhaustive_")
    ]
    proposed = [
        record
        for record in records
        if record["role"] == "offspring"
        or (record["role"] == "parents" and record["generation"] == 0)
        or str(record["role"]).startswith("promotion_")
    ]
    unique_results = {}
    for record in records:
        result = record.get("result")
        if result is not None:
            unique_results.setdefault(result.evaluation_key, result)
    statuses = Counter(result.status.value for result in unique_results.values())
    unique_phenotypes = {record["candidate"].candidate_id for record in proposed}
    archives = store.archive_records()
    by_fidelity: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    for record in archives:
        by_fidelity.setdefault(
            (record["comparison_context_id"], int(record["trial"]), record["fidelity"]), []
        ).append(record)
    archive_summaries = {}
    for (context_id, trial, fidelity), values in sorted(by_fidelity.items()):
        candidates = [record["candidate"] for record in values if record.get("candidate")]
        distances = [
            architecture_distance(left.phenotype, right.phenotype)
            for left, right in itertools.combinations(candidates, 2)
        ]
        objective_count = len(values[0]["objectives"]) if values else 0
        hypervolume = None
        if objective_count == 2:
            objectives = [tuple(map(float, record["objectives"])) for record in values]
            reference = tuple(
                max(value[index] for value in objectives)
                + max(1.0, abs(max(value[index] for value in objectives))) * 0.1
                for index in range(2)
            )
            hypervolume = exact_hypervolume_2d(
                [NSGA2Individual(str(index), objective) for index, objective in enumerate(objectives)],
                reference,
            )
        archive_summaries[f"{context_id}:trial-{trial}:{fidelity}"] = {
            "comparison_context_id": context_id,
            "trial": trial,
            "fidelity": fidelity,
            "size": len(values),
            "feasible": sum(record["result"].feasible for record in values),
            "mean_pairwise_architecture_distance": sum(distances) / len(distances) if distances else 0.0,
            "minimum_pairwise_architecture_distance": min(distances) if distances else 0.0,
            "hypervolume_2d_dynamic_reference": hypervolume,
        }
    seeded = [result for result in unique_results.values() if result.provenance.get("seed_attempts")]
    unseeded = [result for result in unique_results.values() if not result.provenance.get("seed_attempts")]
    total_proposals = len(proposed)
    report = {
        "schema_version": 3,
        "checkpoint": store.load_checkpoint(),
        "proposals": total_proposals,
        "unique_phenotypes": len(unique_phenotypes),
        "duplicate_rate": 1.0 - len(unique_phenotypes) / total_proposals if total_proposals else 0.0,
        "unique_evaluation_contexts": len(unique_results),
        "deduplicated_or_cached_associations": max(0, total_proposals - len(unique_results)),
        "status_counts": dict(sorted(statuses.items())),
        "feasible_rate": statuses.get("feasible", 0) / len(unique_results) if unique_results else 0.0,
        "total_recorded_runtime_seconds": sum(result.runtime_seconds for result in unique_results.values()),
        "seeded_evaluations": len(seeded),
        "seeded_feasible_rate": sum(result.feasible for result in seeded) / len(seeded) if seeded else None,
        "unseeded_feasible_rate": sum(result.feasible for result in unseeded) / len(unseeded) if unseeded else None,
        "archives": archive_summaries,
        "note": "Seed-rate differences are observational; causal seed benefit and parallel scaling require matched campaigns.",
    }
    atomic_write_json(Path(run_directory) / "campaign-summary.json", report)
    return report
