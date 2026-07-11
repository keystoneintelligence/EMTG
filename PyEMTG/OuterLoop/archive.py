"""Exact nondominated archive independent of persistence format."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .model import EvaluationResult
from .nsga2 import NSGA2Individual, dominates


@dataclass(frozen=True)
class ArchiveEntry:
    result: EvaluationResult
    objectives: tuple[float, ...]
    generation: int

    def individual(self) -> NSGA2Individual:
        return NSGA2Individual(
            self.result.evaluation_key,
            self.objectives,
            self.result.status,
            self.result.aggregate_violation,
        )

class ParetoArchive:
    def __init__(self, entries: Iterable[ArchiveEntry] = ()):
        self._entries: dict[str, ArchiveEntry] = {}
        for entry in entries:
            self.update(entry)

    def update(self, entry: ArchiveEntry) -> bool:
        candidate = entry.individual()
        if any(dominates(existing.individual(), candidate) for existing in self._entries.values()):
            return False
        remove = [
            key
            for key, existing in self._entries.items()
            if dominates(candidate, existing.individual())
        ]
        for key in remove:
            del self._entries[key]
        previous = self._entries.get(entry.result.evaluation_key)
        if previous is None or entry.generation < previous.generation:
            self._entries[entry.result.evaluation_key] = entry
        return True

    def entries(self) -> tuple[ArchiveEntry, ...]:
        return tuple(
            sorted(
                self._entries.values(),
                key=lambda entry: (entry.objectives, entry.result.candidate_id, entry.result.evaluation_key),
            )
        )
