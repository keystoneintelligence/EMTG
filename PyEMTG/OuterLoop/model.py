"""Typed records shared by the search and EMTG boundary."""

from __future__ import annotations

from dataclasses import InitVar, asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from .canonical import content_hash


class EvaluationStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    STRUCTURALLY_INVALID = "structurally_invalid"
    STRICT_FILTERED = "strict_filtered"
    HEURISTIC_FILTERED = "heuristic_filtered"
    EXECUTION_FAILED = "execution_failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    OUTPUT_INCOMPLETE = "output_incomplete"
    INFRASTRUCTURE_FAILED = "infrastructure_failed"
    CONFIGURATION_FAILED = "configuration_failed"
    EMTG_INFEASIBLE = "emtg_infeasible"
    OUTER_CONSTRAINT_INFEASIBLE = "outer_constraint_infeasible"
    FEASIBLE = "feasible"


class RepairStatus(str, Enum):
    UNCHANGED = "valid_without_repair"
    REPAIRED = "repaired"
    REJECTED = "rejected"


@dataclass(frozen=True)
class HiddenGeneSlot:
    active: bool
    values: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JourneyGenome:
    active: bool
    values: Mapping[str, Any] = field(default_factory=dict)
    flyby_slots: tuple[HiddenGeneSlot, ...] = ()
    phase_slots: tuple[HiddenGeneSlot, ...] = ()


@dataclass(frozen=True)
class Genotype:
    mission: Mapping[str, Any] = field(default_factory=dict)
    journey_slots: tuple[JourneyGenome, ...] = ()


@dataclass(frozen=True)
class PhasePhenotype:
    target: str
    values: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JourneyPhenotype:
    departure: str
    arrival: str
    flybys: tuple[str, ...] = ()
    values: Mapping[str, Any] = field(default_factory=dict)
    phases: tuple[PhasePhenotype, ...] = ()

    @property
    def sequence(self) -> tuple[str, ...]:
        return (self.departure, *self.flybys, self.arrival)


@dataclass(frozen=True)
class RepairRecord:
    path: str
    before: Any
    after: Any
    reason: str


@dataclass(frozen=True)
class MissionPhenotype:
    mission: Mapping[str, Any]
    journeys: tuple[JourneyPhenotype, ...]
    repair_status: RepairStatus = RepairStatus.UNCHANGED
    repairs: tuple[RepairRecord, ...] = ()
    point_group: Mapping[str, Any] = field(default_factory=dict)
    resonance: Mapping[str, Any] = field(default_factory=dict)

    @property
    def identity(self) -> str:
        # Repair and point-group provenance are campaign semantics, not mission
        # architecture.  Only explicitly selected resonance choices participate;
        # computed opportunities/scores do not.
        resonance_choices = {
            key: value
            for key, value in self.resonance.items()
            if key in {"selected", "architecture"}
        }
        return content_hash(
            {
                "mission": self.mission,
                "journeys": self.journeys,
                "resonance": resonance_choices,
            },
            prefix="emtg-outerloop-phenotype-v3",
        )

    @property
    def sequence_text(self) -> str:
        return " | ".join(" -> ".join(journey.sequence) for journey in self.journeys)


@dataclass(frozen=True)
class CandidateRecord:
    individual_id: str
    genotype: Genotype
    phenotype: MissionPhenotype
    generation: int
    trial: int = 0
    parents: tuple[str, ...] = ()
    operators: tuple[str, ...] = ()
    seeds: Mapping[str, int] = field(default_factory=dict)
    mutation_history: tuple["OperatorRecord", ...] = ()

    @property
    def candidate_id(self) -> str:
        return self.phenotype.identity


@dataclass(frozen=True)
class OperatorRecord:
    operator: str
    rng_seed: int
    affected_paths: tuple[str, ...]
    before: Mapping[str, Any]
    after: Mapping[str, Any]
    no_op: bool = False


@dataclass(frozen=True)
class ArtifactRef:
    role: str
    sha256: str
    path: str
    size_bytes: int


@dataclass(frozen=True)
class ComparisonContext:
    comparison_context_id: str
    trial: int
    fidelity: str


@dataclass(frozen=True)
class EvaluationRequest:
    candidate: CandidateRecord
    fidelity: str
    evaluation_seed: int
    budget: Mapping[str, Any] = field(default_factory=dict)
    initial_guess: Mapping[str, Any] | None = None
    context: Mapping[str, Any] = field(default_factory=dict)

    @property
    def evaluation_key(self) -> str:
        inner_seed_set = self.context.get("inner_seed_set", (self.evaluation_seed,))
        return content_hash(
            {
                "schema_version": 3,
                "phenotype_id": self.candidate.phenotype.identity,
                "fidelity": self.fidelity,
                "inner_seed_set": inner_seed_set,
                "budget": self.budget,
                "initial_guess": self.initial_guess,
                # The context is intentionally retained whole.  Its producers
                # provide the named execution, template, source, asset, platform,
                # and parser manifests; retaining unknown future fields is safer
                # than accidentally omitting an identity-affecting input.
                "context": self.context,
            },
            prefix="emtg-outerloop-evaluation-v3",
        )


@dataclass(frozen=True)
class EvaluationResult:
    """Raw, campaign-independent execution result.

    Instances of this type are the only values admitted to the content cache.
    Objective selection and outer constraint scoring live in
    :class:`ScoredEvaluationResult`.
    """
    evaluation_key: str
    candidate_id: str
    status: EvaluationStatus
    fidelity: str
    solver_violation: float | None = None
    # Constructor-only compatibility for pre-v2 callers. It is folded into the
    # raw solver violation and never serialized as campaign scoring.
    aggregate_violation: InitVar[float | None] = None
    metrics: Mapping[str, Any] = field(default_factory=dict)
    failure_reason: str | None = None
    runtime_seconds: float = 0.0
    artifacts: Mapping[str, str] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self, aggregate_violation: float | None) -> None:
        if self.solver_violation is None and aggregate_violation is not None:
            object.__setattr__(self, "solver_violation", aggregate_violation)

    @property
    def feasible(self) -> bool:
        return self.status is EvaluationStatus.FEASIBLE

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["schema_version"] = 3
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvaluationResult":
        values = dict(data)
        schema = values.pop("schema_version", 3)
        if schema != 3:
            raise ValueError("evaluation result schema is incompatible; use fresh schema-3 state")
        values["status"] = EvaluationStatus(values["status"])
        return cls(**values)


@dataclass(frozen=True)
class ScoredEvaluationResult(EvaluationResult):
    """A raw result associated with one campaign's scoring semantics."""

    objectives: Mapping[str, float | None] = field(default_factory=dict)
    constraints: Mapping[str, float | None] = field(default_factory=dict)
    aggregate_violation: float | None = None
    campaign_feasible: bool = False
    objective_metadata: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    scoring_context: Mapping[str, Any] = field(default_factory=dict)
    raw_snapshot: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        # ``aggregate_violation`` is a persisted scoring field in this subtype.
        pass

    @property
    def feasible(self) -> bool:
        return self.campaign_feasible

    @classmethod
    def from_raw(
        cls,
        raw: EvaluationResult,
        **scoring: Any,
    ) -> "ScoredEvaluationResult":
        data = raw.to_dict()
        data.pop("schema_version", None)
        data["raw_snapshot"] = raw.to_dict()
        data["status"] = EvaluationStatus(data["status"])
        data.update(scoring)
        return cls(**data)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ScoredEvaluationResult":
        values = dict(data)
        schema = values.pop("schema_version", 3)
        if schema != 3:
            raise ValueError("scored result schema is incompatible; use fresh schema-3 state")
        values["status"] = EvaluationStatus(values["status"])
        return cls(**values)

    def raw(self) -> EvaluationResult:
        if self.raw_snapshot:
            return EvaluationResult.from_dict(self.raw_snapshot)
        return EvaluationResult(
            evaluation_key=self.evaluation_key,
            candidate_id=self.candidate_id,
            status=self.status,
            fidelity=self.fidelity,
            solver_violation=self.solver_violation,
            metrics=self.metrics,
            failure_reason=self.failure_reason,
            runtime_seconds=self.runtime_seconds,
            artifacts=self.artifacts,
            provenance=self.provenance,
        )


def status_severity(status: EvaluationStatus) -> int:
    """Stable ordering used only when no meaningful constraint distance exists."""
    order = {
        EvaluationStatus.FEASIBLE: 0,
        EvaluationStatus.EMTG_INFEASIBLE: 1,
        EvaluationStatus.OUTER_CONSTRAINT_INFEASIBLE: 1,
        EvaluationStatus.HEURISTIC_FILTERED: 2,
        EvaluationStatus.STRICT_FILTERED: 3,
        EvaluationStatus.STRUCTURALLY_INVALID: 4,
        EvaluationStatus.OUTPUT_INCOMPLETE: 5,
        EvaluationStatus.CONFIGURATION_FAILED: 6,
        EvaluationStatus.INFRASTRUCTURE_FAILED: 7,
        EvaluationStatus.EXECUTION_FAILED: 8,
        EvaluationStatus.TIMED_OUT: 9,
        EvaluationStatus.CANCELLED: 10,
        EvaluationStatus.RUNNING: 11,
        EvaluationStatus.PENDING: 12,
    }
    return order[status]


def total_violation(values: Sequence[float | None]) -> float | None:
    available = [max(0.0, float(value)) for value in values if value is not None]
    return sum(available) if available else None
