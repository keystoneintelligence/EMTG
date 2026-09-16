"""Optional control and observation interfaces for long-running campaigns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from .model import CandidateRecord, ScoredEvaluationResult


@dataclass(frozen=True)
class RunDirective:
    """A directive sampled at safe evaluation-batch boundaries."""

    core_limit: int | None = None
    pause: bool = False
    cancel: bool = False


class RunControl(Protocol):
    def snapshot(self) -> RunDirective: ...


class CampaignObserver(Protocol):
    def on_evaluation(
        self,
        candidate: CandidateRecord,
        result: ScoredEvaluationResult,
        *,
        trial: int,
        generation: int,
        role: str,
    ) -> None: ...

    def on_archive(
        self,
        entries: Sequence[ScoredEvaluationResult],
        *,
        trial: int,
        generation: int,
        fidelity: str,
    ) -> None: ...


class NullCampaignObserver:
    def on_evaluation(self, candidate, result, **context) -> None:
        return None

    def on_archive(self, entries, **context) -> None:
        return None
