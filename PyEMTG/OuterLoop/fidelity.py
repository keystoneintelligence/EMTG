"""Comparable-fidelity ladder and deterministic promotion policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .nsga2 import NSGA2Individual, assign_crowding_distance, fast_nondominated_sort


@dataclass(frozen=True)
class FidelityLevel:
    name: str
    rank: int
    budget: Mapping[str, Any] = field(default_factory=dict)
    transcription: str | None = None


class FidelityLadder:
    def __init__(self, levels: Sequence[FidelityLevel]):
        ordered = sorted(levels, key=lambda level: level.rank)
        if not ordered or len({level.name for level in ordered}) != len(ordered):
            raise ValueError("fidelity levels must be non-empty and uniquely named")
        if [level.rank for level in ordered] != list(range(len(ordered))):
            raise ValueError("fidelity ranks must be contiguous from zero")
        self.levels = tuple(ordered)

    def level(self, name: str) -> FidelityLevel:
        for level in self.levels:
            if level.name == name:
                return level
        raise KeyError(name)

    def next(self, name: str) -> FidelityLevel | None:
        level = self.level(name)
        return self.levels[level.rank + 1] if level.rank + 1 < len(self.levels) else None


def promote_diverse_nondominated(
    population: Sequence[NSGA2Individual], count: int
) -> tuple[NSGA2Individual, ...]:
    if count < 1:
        return ()
    selected: list[NSGA2Individual] = []
    for front in fast_nondominated_sort(population):
        crowded = assign_crowding_distance(front)
        crowded.sort(key=lambda item: (-item.crowding_distance, item.candidate_id))
        remaining = count - len(selected)
        selected.extend(crowded[:remaining])
        if len(selected) == count:
            break
    return tuple(selected)
