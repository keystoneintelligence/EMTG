"""Explicit JSON record conversion without dynamic evaluation."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping

from .gene_names import canonicalize_mission_genes

from .model import (
    CandidateRecord,
    EvaluationResult,
    ScoredEvaluationResult,
    Genotype,
    HiddenGeneSlot,
    JourneyGenome,
    JourneyPhenotype,
    MissionPhenotype,
    OperatorRecord,
    PhasePhenotype,
    RepairRecord,
    RepairStatus,
)


def genotype_to_dict(value: Genotype) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "mission": dict(value.mission),
        "journey_slots": [
            {
                "active": journey.active,
                "values": dict(journey.values),
                "flyby_slots": [
                    {"active": slot.active, "values": dict(slot.values)}
                    for slot in journey.flyby_slots
                ],
                "phase_slots": [
                    {"active": slot.active, "values": dict(slot.values)}
                    for slot in journey.phase_slots
                ],
            }
            for journey in value.journey_slots
        ],
    }


def genotype_from_dict(data: Mapping[str, Any]) -> Genotype:
    if data.get("schema_version", 3) != 3:
        raise ValueError("genotype schema is incompatible; use fresh schema-3 state")
    def slot(value: Mapping[str, Any]) -> HiddenGeneSlot:
        return HiddenGeneSlot(bool(value["active"]), dict(value.get("values", {})))
    return Genotype(
        canonicalize_mission_genes(data.get("mission", {})),
        tuple(
            JourneyGenome(
                bool(journey["active"]),
                dict(journey.get("values", {})),
                tuple(slot(value) for value in journey.get("flyby_slots", ())),
                tuple(slot(value) for value in journey.get("phase_slots", ())),
            )
            for journey in data.get("journey_slots", ())
        ),
    )


def phenotype_to_dict(value: MissionPhenotype) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "mission": dict(value.mission),
        "journeys": [
            {
                "departure": journey.departure,
                "arrival": journey.arrival,
                "flybys": list(journey.flybys),
                "values": dict(journey.values),
                "phases": [
                    {"target": phase.target, "values": dict(phase.values)}
                    for phase in journey.phases
                ],
            }
            for journey in value.journeys
        ],
        "repair_status": value.repair_status.value,
        "repairs": [asdict(repair) for repair in value.repairs],
        "point_group": dict(value.point_group),
        "resonance": dict(value.resonance),
    }


def phenotype_from_dict(data: Mapping[str, Any]) -> MissionPhenotype:
    if data.get("schema_version", 3) != 3:
        raise ValueError("phenotype schema is incompatible; use fresh schema-3 state")
    return MissionPhenotype(
        mission=canonicalize_mission_genes(data.get("mission", {})),
        journeys=tuple(
            JourneyPhenotype(
                departure=str(journey["departure"]),
                arrival=str(journey["arrival"]),
                flybys=tuple(str(value) for value in journey.get("flybys", ())),
                values=dict(journey.get("values", {})),
                phases=tuple(
                    PhasePhenotype(str(phase["target"]), dict(phase.get("values", {})))
                    for phase in journey.get("phases", ())
                ),
            )
            for journey in data.get("journeys", ())
        ),
        repair_status=RepairStatus(data.get("repair_status", RepairStatus.UNCHANGED.value)),
        repairs=tuple(RepairRecord(**repair) for repair in data.get("repairs", ())),
        point_group=dict(data.get("point_group", {})),
        resonance=dict(data.get("resonance", {})),
    )


def candidate_to_dict(value: CandidateRecord) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "individual_id": value.individual_id,
        "genotype": genotype_to_dict(value.genotype),
        "phenotype": phenotype_to_dict(value.phenotype),
        "generation": value.generation,
        "trial": value.trial,
        "parents": list(value.parents),
        "operators": list(value.operators),
        "seeds": dict(value.seeds),
        "mutation_history": [asdict(record) for record in value.mutation_history],
    }


def candidate_from_dict(data: Mapping[str, Any]) -> CandidateRecord:
    if data.get("schema_version", 3) != 3:
        raise ValueError("candidate schema is incompatible; use fresh schema-3 state")
    return CandidateRecord(
        individual_id=str(data["individual_id"]),
        genotype=genotype_from_dict(data["genotype"]),
        phenotype=phenotype_from_dict(data["phenotype"]),
        generation=int(data["generation"]),
        trial=int(data.get("trial", 0)),
        parents=tuple(str(value) for value in data.get("parents", ())),
        operators=tuple(str(value) for value in data.get("operators", ())),
        seeds={str(key): int(value) for key, value in data.get("seeds", {}).items()},
        mutation_history=tuple(
            OperatorRecord(
                operator=str(value["operator"]),
                rng_seed=int(value["rng_seed"]),
                affected_paths=tuple(value.get("affected_paths", ())),
                before=dict(value.get("before", {})),
                after=dict(value.get("after", {})),
                no_op=bool(value.get("no_op", False)),
            )
            for value in data.get("mutation_history", ())
        ),
    )


def result_to_dict(value: EvaluationResult) -> dict[str, Any]:
    return value.to_dict()


def result_from_dict(data: Mapping[str, Any]) -> EvaluationResult:
    if any(key in data for key in ("objectives", "constraints", "campaign_feasible", "scoring_context")):
        return ScoredEvaluationResult.from_dict(data)
    return EvaluationResult.from_dict(data)


def scored_result_from_dict(data: Mapping[str, Any]) -> ScoredEvaluationResult:
    return ScoredEvaluationResult.from_dict(data)
