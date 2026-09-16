"""Auditable strict and heuristic prefilter pipeline."""

from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Any, Mapping, Protocol, Sequence

from .model import EvaluationStatus, MissionPhenotype
from .rules import MissionRules, UniverseCatalog, inclination_separation, validate_phenotype


@dataclass(frozen=True)
class FilterDecision:
    accepted: bool
    status: EvaluationStatus | None = None
    code: str | None = None
    reason: str | None = None
    heuristic: bool = False
    audited: bool = False
    metrics: Mapping[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.metrics is None:
            object.__setattr__(self, "metrics", {})


class Prefilter(Protocol):
    name: str
    heuristic: bool

    def evaluate(self, phenotype: MissionPhenotype) -> FilterDecision: ...


@dataclass(frozen=True)
class TopologyPrefilter:
    catalog: UniverseCatalog
    rules: MissionRules = MissionRules()
    name: str = "topology"
    heuristic: bool = False

    def evaluate(self, phenotype: MissionPhenotype) -> FilterDecision:
        report = validate_phenotype(phenotype, self.catalog, self.rules)
        if report.valid:
            return FilterDecision(True, metrics={"point_groups": report.group_results})
        reason = "; ".join(f"{issue.code}: {issue.message}" for issue in report.issues)
        return FilterDecision(False, EvaluationStatus.STRICT_FILTERED, "topology", reason)


@dataclass(frozen=True)
class SuccessiveDuplicatePrefilter:
    allow_same_body: bool = False
    name: str = "successive_duplicate"
    heuristic: bool = True

    def evaluate(self, phenotype: MissionPhenotype) -> FilterDecision:
        if self.allow_same_body:
            return FilterDecision(True)
        for journey in phenotype.journeys:
            for left, right in zip(journey.sequence, journey.sequence[1:]):
                if left == right:
                    return FilterDecision(False, EvaluationStatus.HEURISTIC_FILTERED, self.name, f"successive repeat of {left}", True)
        return FilterDecision(True)


@dataclass(frozen=True)
class InclinationBandpassPrefilter:
    catalog: UniverseCatalog
    maximum_degrees: float
    name: str = "inclination_bandpass"
    heuristic: bool = True

    def evaluate(self, phenotype: MissionPhenotype) -> FilterDecision:
        maximum_seen = 0.0
        for journey in phenotype.journeys:
            for left, right in zip(journey.sequence, journey.sequence[1:]):
                if left not in self.catalog.bodies or right not in self.catalog.bodies:
                    continue
                separation = inclination_separation(self.catalog.body(left), self.catalog.body(right))
                maximum_seen = max(maximum_seen, separation)
                if separation > self.maximum_degrees:
                    return FilterDecision(
                        False,
                        EvaluationStatus.HEURISTIC_FILTERED,
                        self.name,
                        f"{left}->{right} inclination separation {separation:.6g} exceeds {self.maximum_degrees}",
                        True,
                        metrics={"maximum_inclination_separation": maximum_seen},
                    )
        return FilterDecision(True, metrics={"maximum_inclination_separation": maximum_seen})


@dataclass(frozen=True)
class NumericEnvelopePrefilter:
    metric: str
    lower: float | None = None
    upper: float | None = None
    heuristic: bool = True

    @property
    def name(self) -> str:
        return f"envelope:{self.metric}"

    def evaluate(self, phenotype: MissionPhenotype) -> FilterDecision:
        raw = phenotype.mission.get(self.metric)
        if raw is None:
            return FilterDecision(True)
        value = float(raw)
        if self.lower is not None and value < self.lower:
            return FilterDecision(False, EvaluationStatus.HEURISTIC_FILTERED, self.name, f"{value} is below {self.lower}", True)
        if self.upper is not None and value > self.upper:
            return FilterDecision(False, EvaluationStatus.HEURISTIC_FILTERED, self.name, f"{value} exceeds {self.upper}", True)
        return FilterDecision(True, metrics={self.metric: value})


@dataclass(frozen=True)
class FilterPipelineResult:
    accepted: bool
    decisions: tuple[FilterDecision, ...]
    rejected_by: FilterDecision | None = None


class FilterPipeline:
    def __init__(self, filters: Sequence[Prefilter], audit_fraction: float = 0.05):
        if not 0.0 <= audit_fraction <= 1.0:
            raise ValueError("audit_fraction must be between zero and one")
        self.filters = tuple(filters)
        self.audit_fraction = audit_fraction

    def evaluate(self, phenotype: MissionPhenotype, audit_rng: random.Random) -> FilterPipelineResult:
        decisions: list[FilterDecision] = []
        for prefilter in self.filters:
            decision = prefilter.evaluate(phenotype)
            if decision.accepted:
                decisions.append(decision)
                continue
            if prefilter.heuristic and audit_rng.random() < self.audit_fraction:
                audited = FilterDecision(
                    True,
                    code=decision.code,
                    reason=decision.reason,
                    heuristic=True,
                    audited=True,
                    metrics=decision.metrics,
                )
                decisions.append(audited)
                continue
            decisions.append(decision)
            return FilterPipelineResult(False, tuple(decisions), decision)
        return FilterPipelineResult(True, tuple(decisions))
