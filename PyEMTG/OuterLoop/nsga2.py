"""Deterministic feasibility-first NSGA-II implementation."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import random
from typing import Any, Callable, Iterable, Sequence

from .config import NSGA2Config
from .model import EvaluationStatus, status_severity


@dataclass(frozen=True)
class NSGA2Individual:
    candidate_id: str
    objectives: tuple[float, ...]
    status: EvaluationStatus = EvaluationStatus.FEASIBLE
    aggregate_violation: float | None = 0.0
    payload: Any = None
    rank: int = 2**31 - 1
    crowding_distance: float = 0.0

    @property
    def feasible(self) -> bool:
        return self.status is EvaluationStatus.FEASIBLE


def _constraint_order(individual: NSGA2Individual) -> tuple[int, float]:
    if individual.feasible:
        return (0, 0.0)
    violation = individual.aggregate_violation
    if individual.status in {
        EvaluationStatus.EMTG_INFEASIBLE,
        EvaluationStatus.OUTER_CONSTRAINT_INFEASIBLE,
    } and violation is not None and math.isfinite(violation):
        return (1, max(0.0, violation))
    return (1 + status_severity(individual.status), math.inf if violation is None else max(0.0, violation))


def dominates(left: NSGA2Individual, right: NSGA2Individual, tolerance: float = 0.0) -> bool:
    left_constraint = _constraint_order(left)
    right_constraint = _constraint_order(right)
    if left_constraint < right_constraint:
        return True
    if left_constraint > right_constraint:
        return False
    if not left.feasible:
        return False
    if len(left.objectives) != len(right.objectives):
        raise ValueError("objective dimensions differ")
    if not left.objectives:
        return False
    no_worse = all(a <= b + tolerance for a, b in zip(left.objectives, right.objectives))
    strictly_better = any(a < b - tolerance for a, b in zip(left.objectives, right.objectives))
    return no_worse and strictly_better


def fast_nondominated_sort(
    population: Sequence[NSGA2Individual], tolerance: float = 0.0
) -> list[list[NSGA2Individual]]:
    ordered = sorted(population, key=lambda item: item.candidate_id)
    dominates_indices: list[list[int]] = [[] for _ in ordered]
    domination_count = [0 for _ in ordered]
    fronts: list[list[int]] = [[]]
    for p, left in enumerate(ordered):
        for q, right in enumerate(ordered):
            if p == q:
                continue
            if dominates(left, right, tolerance):
                dominates_indices[p].append(q)
            elif dominates(right, left, tolerance):
                domination_count[p] += 1
        if domination_count[p] == 0:
            fronts[0].append(p)
    index = 0
    while index < len(fronts) and fronts[index]:
        following: list[int] = []
        for p in fronts[index]:
            for q in dominates_indices[p]:
                domination_count[q] -= 1
                if domination_count[q] == 0:
                    following.append(q)
        if following:
            fronts.append(sorted(set(following), key=lambda i: ordered[i].candidate_id))
        index += 1
    return [[ordered[i] for i in front] for front in fronts if front]


def assign_crowding_distance(front: Sequence[NSGA2Individual]) -> list[NSGA2Individual]:
    if not front:
        return []
    distances = {item.candidate_id: 0.0 for item in front}
    objective_count = len(front[0].objectives)
    if len(front) <= 2:
        return [replace(item, crowding_distance=math.inf) for item in front]
    for objective in range(objective_count):
        ordered = sorted(front, key=lambda item: (item.objectives[objective], item.candidate_id))
        minimum = ordered[0].objectives[objective]
        maximum = ordered[-1].objectives[objective]
        distances[ordered[0].candidate_id] = math.inf
        distances[ordered[-1].candidate_id] = math.inf
        span = maximum - minimum
        if span <= 0.0:
            continue
        for index in range(1, len(ordered) - 1):
            candidate_id = ordered[index].candidate_id
            if math.isinf(distances[candidate_id]):
                continue
            distances[candidate_id] += (
                ordered[index + 1].objectives[objective]
                - ordered[index - 1].objectives[objective]
            ) / span
    return [replace(item, crowding_distance=distances[item.candidate_id]) for item in front]


def rank_population(population: Sequence[NSGA2Individual]) -> list[NSGA2Individual]:
    ranked: list[NSGA2Individual] = []
    for rank, front in enumerate(fast_nondominated_sort(population)):
        ranked.extend(replace(item, rank=rank) for item in assign_crowding_distance(front))
    return sorted(ranked, key=lambda item: item.candidate_id)


def tournament_key(item: NSGA2Individual) -> tuple[int, float, tuple[int, float], str]:
    crowding = item.crowding_distance
    return (item.rank, -crowding, _constraint_order(item), item.candidate_id)


def tournament_select(
    population: Sequence[NSGA2Individual], rng: random.Random, tournament_size: int = 2
) -> NSGA2Individual:
    if tournament_size < 2 or tournament_size > len(population):
        raise ValueError("invalid tournament size")
    contestants = rng.sample(list(population), tournament_size)
    return min(contestants, key=tournament_key)


def environmental_selection(
    combined: Sequence[NSGA2Individual], population_size: int
) -> list[NSGA2Individual]:
    if population_size < 1 or len(combined) < population_size:
        raise ValueError("combined population is too small")
    survivors: list[NSGA2Individual] = []
    for rank, raw_front in enumerate(fast_nondominated_sort(combined)):
        front = [replace(item, rank=rank) for item in assign_crowding_distance(raw_front)]
        remaining = population_size - len(survivors)
        if len(front) <= remaining:
            survivors.extend(sorted(front, key=lambda item: item.candidate_id))
            continue
        front.sort(key=lambda item: (-item.crowding_distance, item.candidate_id))
        survivors.extend(front[:remaining])
        break
    return rank_population(survivors)


class NSGA2Engine:
    def __init__(self, config: NSGA2Config):
        self.config = config

    def rank(self, population: Sequence[NSGA2Individual]) -> list[NSGA2Individual]:
        return rank_population(population)

    def select_parent(self, population: Sequence[NSGA2Individual], rng: random.Random) -> NSGA2Individual:
        return tournament_select(population, rng, self.config.tournament_size)

    def survive(
        self,
        parents: Sequence[NSGA2Individual],
        offspring: Sequence[NSGA2Individual],
    ) -> list[NSGA2Individual]:
        return environmental_selection([*parents, *offspring], self.config.population_size)

    def make_offspring_payloads(
        self,
        ranked_population: Sequence[NSGA2Individual],
        rng_for_slot: Callable[[int, str], random.Random],
        breed: Callable[[Any, Any, random.Random, random.Random, bool, bool], Any],
    ) -> list[Any]:
        """Select and breed in stable slots, independent of evaluation ordering."""
        output: list[Any] = []
        ranked = rank_population(ranked_population)
        for slot in range(self.config.population_size):
            selection_rng = rng_for_slot(slot, "selection")
            left = self.select_parent(ranked, selection_rng)
            right = self.select_parent(ranked, selection_rng)
            crossover_rng = rng_for_slot(slot, "crossover")
            mutation_rng = rng_for_slot(slot, "mutation")
            do_crossover = crossover_rng.random() < self.config.crossover_probability
            do_mutation = mutation_rng.random() < self.config.mutation_probability
            output.append(
                breed(left.payload, right.payload, crossover_rng, mutation_rng, do_crossover, do_mutation)
            )
        return output


def exact_hypervolume_2d(
    population: Iterable[NSGA2Individual], reference: tuple[float, float]
) -> float:
    feasible = [item for item in population if item.feasible and len(item.objectives) == 2]
    front = fast_nondominated_sort(feasible)[0] if feasible else []
    points = sorted(
        (item.objectives for item in front if item.objectives[0] <= reference[0] and item.objectives[1] <= reference[1]),
        key=lambda value: (value[0], value[1]),
    )
    area = 0.0
    previous_y = reference[1]
    for x_value, y_value in points:
        if y_value < previous_y:
            area += max(0.0, reference[0] - x_value) * (previous_y - y_value)
            previous_y = y_value
    return area
