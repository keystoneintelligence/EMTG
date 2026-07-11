"""Deterministic generic and mission-aware genetic operators."""

from __future__ import annotations

from dataclasses import dataclass, replace
import random
from typing import Callable, Iterable

from .genome import GenomeSchema, decode_genotype, mutate_gene, sample_gene, synchronize_topology
from .model import Genotype, HiddenGeneSlot, JourneyGenome
from .rules import PointGroup


Mutation = Callable[[GenomeSchema, Genotype, random.Random], Genotype]
Crossover = Callable[[GenomeSchema, Genotype, Genotype, random.Random], Genotype]


@dataclass(frozen=True)
class OperatorDefinition:
    name: str
    scopes: tuple[str, ...]
    mutation: Mutation | None = None
    crossover: Crossover | None = None


def _replace_journey(genotype: Genotype, index: int, journey: JourneyGenome) -> Genotype:
    journeys = list(genotype.journey_slots)
    journeys[index] = journey
    return replace(genotype, journey_slots=tuple(journeys))


def activation_mutation(schema: GenomeSchema, genotype: Genotype, rng: random.Random) -> Genotype:
    if schema.search.activation_mode == "count":
        current = int(genotype.mission.get("number_of_journeys", schema.search.min_journeys))
        alternatives = []
        if current > schema.search.min_journeys:
            alternatives.append(current - 1)
        if current < schema.search.max_journeys:
            alternatives.append(current + 1)
        if not alternatives:
            return genotype
        mission = dict(genotype.mission)
        mission["number_of_journeys"] = alternatives[rng.randrange(len(alternatives))]
        return synchronize_topology(schema, replace(genotype, mission=mission))
    choices: list[tuple[str, int, int | None]] = []
    active_journeys = sum(journey.active for journey in genotype.journey_slots)
    for journey_index, journey in enumerate(genotype.journey_slots):
        if not journey.active or active_journeys > schema.search.min_journeys:
            choices.append(("journey", journey_index, None))
        if journey.active:
            active_flybys = sum(slot.active for slot in journey.flyby_slots)
            choices.extend(
                ("flyby", journey_index, index)
                for index, slot in enumerate(journey.flyby_slots)
                if not slot.active or active_flybys > schema.search.min_flybys
            )
    if not choices:
        return genotype
    kind, journey_index, flyby_index = choices[rng.randrange(len(choices))]
    journey = genotype.journey_slots[journey_index]
    if kind == "journey":
        return synchronize_topology(schema, _replace_journey(genotype, journey_index, replace(journey, active=not journey.active)))
    flybys = list(journey.flyby_slots)
    slot = flybys[flyby_index]  # type: ignore[index]
    flybys[flyby_index] = replace(slot, active=not slot.active)  # type: ignore[index]
    return synchronize_topology(schema, _replace_journey(genotype, journey_index, replace(journey, flyby_slots=tuple(flybys))))


def insertion_mutation(schema: GenomeSchema, genotype: Genotype, rng: random.Random) -> Genotype:
    inactive_flybys = [
        (j, f)
        for j, journey in enumerate(genotype.journey_slots)
        if journey.active
        for f, flyby in enumerate(journey.flyby_slots)
        if not flyby.active
    ]
    inactive_journeys = [index for index, journey in enumerate(genotype.journey_slots) if not journey.active]
    if inactive_flybys and (not inactive_journeys or rng.random() < 0.75):
        journey_index, flyby_index = inactive_flybys[rng.randrange(len(inactive_flybys))]
        journey = genotype.journey_slots[journey_index]
        flybys = list(journey.flyby_slots)
        slot = flybys[flyby_index]
        values = dict(slot.values)
        if schema.search.flyby_bodies and not values.get("flyby_body"):
            values["flyby_body"] = schema.search.flyby_bodies[rng.randrange(len(schema.search.flyby_bodies))]
        flybys[flyby_index] = HiddenGeneSlot(True, values)
        return synchronize_topology(schema, _replace_journey(genotype, journey_index, replace(journey, flyby_slots=tuple(flybys))))
    if inactive_journeys:
        if schema.search.activation_mode == "count":
            mission = dict(genotype.mission)
            mission["number_of_journeys"] = min(
                schema.search.max_journeys,
                int(mission.get("number_of_journeys", schema.search.min_journeys)) + 1,
            )
            return synchronize_topology(schema, replace(genotype, mission=mission))
        index = inactive_journeys[rng.randrange(len(inactive_journeys))]
        return synchronize_topology(schema, _replace_journey(genotype, index, replace(genotype.journey_slots[index], active=True)))
    return genotype


def deletion_mutation(schema: GenomeSchema, genotype: Genotype, rng: random.Random) -> Genotype:
    active_flybys = [
        (j, f)
        for j, journey in enumerate(genotype.journey_slots)
        if journey.active
        for f, flyby in enumerate(journey.flyby_slots)
        if flyby.active and sum(slot.active for slot in journey.flyby_slots) > schema.search.min_flybys
    ]
    active_journeys = [index for index, journey in enumerate(genotype.journey_slots) if journey.active]
    deletable_journeys = active_journeys if len(active_journeys) > schema.search.min_journeys else []
    if active_flybys and (not deletable_journeys or rng.random() < 0.75):
        journey_index, flyby_index = active_flybys[rng.randrange(len(active_flybys))]
        journey = genotype.journey_slots[journey_index]
        flybys = list(journey.flyby_slots)
        flybys[flyby_index] = replace(flybys[flyby_index], active=False)
        return synchronize_topology(schema, _replace_journey(genotype, journey_index, replace(journey, flyby_slots=tuple(flybys))))
    if deletable_journeys:
        if schema.search.activation_mode == "count":
            mission = dict(genotype.mission)
            mission["number_of_journeys"] = max(
                schema.search.min_journeys,
                int(mission.get("number_of_journeys", schema.search.min_journeys)) - 1,
            )
            return synchronize_topology(schema, replace(genotype, mission=mission))
        index = deletable_journeys[rng.randrange(len(deletable_journeys))]
        return synchronize_topology(schema, _replace_journey(genotype, index, replace(genotype.journey_slots[index], active=False)))
    return genotype


def replacement_mutation(schema: GenomeSchema, genotype: Genotype, rng: random.Random) -> Genotype:
    active = [
        (j, f)
        for j, journey in enumerate(genotype.journey_slots)
        if journey.active
        for f, flyby in enumerate(journey.flyby_slots)
        if flyby.active
    ]
    if not active or len(schema.search.flyby_bodies) < 2:
        return genotype
    journey_index, flyby_index = active[rng.randrange(len(active))]
    journey = genotype.journey_slots[journey_index]
    flybys = list(journey.flyby_slots)
    slot = flybys[flyby_index]
    current = slot.values.get("flyby_body")
    alternatives = [body for body in schema.search.flyby_bodies if body != current]
    values = dict(slot.values)
    values["flyby_body"] = alternatives[rng.randrange(len(alternatives))]
    flybys[flyby_index] = replace(slot, values=values)
    return synchronize_topology(
        schema,
        _replace_journey(genotype, journey_index, replace(journey, flyby_slots=tuple(flybys))),
    )


def swap_mutation(schema: GenomeSchema, genotype: Genotype, rng: random.Random) -> Genotype:
    eligible = [
        index
        for index, journey in enumerate(genotype.journey_slots)
        if sum(slot.active for slot in journey.flyby_slots) >= 2
    ]
    if not eligible:
        return genotype
    journey_index = eligible[rng.randrange(len(eligible))]
    journey = genotype.journey_slots[journey_index]
    indices = [index for index, slot in enumerate(journey.flyby_slots) if slot.active]
    left, right = rng.sample(indices, 2)
    flybys = list(journey.flyby_slots)
    flybys[left], flybys[right] = flybys[right], flybys[left]
    phases = list(journey.phase_slots)
    phases[left], phases[right] = phases[right], phases[left]
    return _replace_journey(
        genotype,
        journey_index,
        replace(journey, flyby_slots=tuple(flybys), phase_slots=tuple(phases)),
    )


def _scoped_gene_mutation(
    schema: GenomeSchema,
    genotype: Genotype,
    rng: random.Random,
    scope: str,
    allowed_names: Iterable[str] | None = None,
) -> Genotype:
    allowed = set(allowed_names) if allowed_names is not None else None
    if scope == "mission":
        specs = {name: spec for name, spec in schema.search.mission_genes.items() if spec.variable and (allowed is None or name in allowed)}
        if not specs:
            return genotype
        name = sorted(specs)[rng.randrange(len(specs))]
        values = dict(genotype.mission)
        values[name] = mutate_gene(values[name], specs[name], rng)
        return replace(genotype, mission=values)
    active = [index for index, journey in enumerate(genotype.journey_slots) if journey.active]
    if not active:
        return genotype
    journey_index = active[rng.randrange(len(active))]
    journey = genotype.journey_slots[journey_index]
    if scope == "journey":
        specs = {name: spec for name, spec in schema.search.journey_genes.items() if spec.variable and (allowed is None or name in allowed)}
        if not specs:
            return genotype
        name = sorted(specs)[rng.randrange(len(specs))]
        values = dict(journey.values)
        values[name] = mutate_gene(values[name], specs[name], rng)
        return _replace_journey(genotype, journey_index, replace(journey, values=values))
    specs = {name: spec for name, spec in schema.search.phase_genes.items() if spec.variable and (allowed is None or name in allowed)}
    if not specs:
        return genotype
    active_phases = [index for index, phase in enumerate(journey.phase_slots) if phase.active]
    if not active_phases:
        return genotype
    phase_index = active_phases[rng.randrange(len(active_phases))]
    phases = list(journey.phase_slots)
    phase = phases[phase_index]
    name = sorted(specs)[rng.randrange(len(specs))]
    values = dict(phase.values)
    values[name] = mutate_gene(values[name], specs[name], rng)
    phases[phase_index] = replace(phase, values=values)
    return _replace_journey(genotype, journey_index, replace(journey, phase_slots=tuple(phases)))


def timing_mutation(schema: GenomeSchema, genotype: Genotype, rng: random.Random) -> Genotype:
    names = {
        "launch_epoch", "launch_window", "flight_time", "flight_time_bounds",
        "wait_time_bounds", "journey_time_bounds",
    }
    result = _scoped_gene_mutation(schema, genotype, rng, "mission", names)
    return _scoped_gene_mutation(schema, genotype, rng, "journey", names) if result == genotype else result


def hardware_mutation(schema: GenomeSchema, genotype: Genotype, rng: random.Random) -> Genotype:
    return _scoped_gene_mutation(
        schema,
        genotype,
        rng,
        "mission",
        {
            "launch_vehicle", "spacecraft_configuration", "power_system",
            "electric_propulsion_system", "number_of_electric_propulsion_systems",
            "duty_cycle", "chemical_fuel_capacity", "chemical_oxidizer_capacity",
            "electric_propellant_capacity",
        },
    )


def phase_type_mutation(schema: GenomeSchema, genotype: Genotype, rng: random.Random) -> Genotype:
    result = _scoped_gene_mutation(schema, genotype, rng, "phase", {"phase_type"})
    return _scoped_gene_mutation(schema, result, rng, "journey", {"phase_type"}) if result == genotype else result


def dsm_count_mutation(schema: GenomeSchema, genotype: Genotype, rng: random.Random) -> Genotype:
    result = _scoped_gene_mutation(schema, genotype, rng, "phase", {"dsm_count", "impulses_per_phase"})
    return _scoped_gene_mutation(schema, result, rng, "journey", {"dsm_count", "impulses_per_phase"}) if result == genotype else result


def generic_gene_mutation(schema: GenomeSchema, genotype: Genotype, rng: random.Random) -> Genotype:
    scopes = []
    if any(spec.variable for spec in schema.search.mission_genes.values()):
        scopes.append("mission")
    if any(spec.variable for spec in schema.search.journey_genes.values()):
        scopes.append("journey")
    if any(spec.variable for spec in schema.search.phase_genes.values()):
        scopes.append("phase")
    if not scopes:
        return genotype
    return _scoped_gene_mutation(schema, genotype, rng, scopes[rng.randrange(len(scopes))])


def point_group_mutation(
    schema: GenomeSchema,
    genotype: Genotype,
    rng: random.Random,
    groups: Iterable[PointGroup],
) -> Genotype:
    groups = tuple(group for group in groups if group.members.intersection(schema.search.flyby_bodies))
    slots = [
        (journey_index, flyby_index)
        for journey_index, journey in enumerate(genotype.journey_slots)
        if journey.active
        for flyby_index, _ in enumerate(journey.flyby_slots)
    ]
    if not groups or not slots:
        return genotype
    group = groups[rng.randrange(len(groups))]
    bodies = sorted(group.members.intersection(schema.search.flyby_bodies))
    journey_index, flyby_index = slots[rng.randrange(len(slots))]
    journey = genotype.journey_slots[journey_index]
    flybys = list(journey.flyby_slots)
    values = dict(flybys[flyby_index].values)
    values["flyby_body"] = bodies[rng.randrange(len(bodies))]
    flybys[flyby_index] = HiddenGeneSlot(True, values)
    return synchronize_topology(
        schema,
        _replace_journey(genotype, journey_index, replace(journey, flyby_slots=tuple(flybys))),
    )


def journey_crossover(
    schema: GenomeSchema, left: Genotype, right: Genotype, rng: random.Random
) -> Genotype:
    cut = rng.randrange(schema.search.max_journeys + 1)
    mission = dict(left.mission)
    for name in sorted(schema.search.mission_genes):
        if rng.random() < 0.5:
            mission[name] = right.mission.get(name, mission.get(name))
    return synchronize_topology(schema, Genotype(mission, left.journey_slots[:cut] + right.journey_slots[cut:]))


def subsequence_crossover(
    schema: GenomeSchema, left: Genotype, right: Genotype, rng: random.Random
) -> Genotype:
    if not left.journey_slots:
        return left
    journey_index = rng.randrange(len(left.journey_slots))
    left_journey = left.journey_slots[journey_index]
    right_journey = right.journey_slots[journey_index]
    if not left_journey.flyby_slots:
        return journey_crossover(schema, left, right, rng)
    start = rng.randrange(len(left_journey.flyby_slots))
    stop = rng.randrange(start + 1, len(left_journey.flyby_slots) + 1)
    flybys = list(left_journey.flyby_slots)
    phases = list(left_journey.phase_slots)
    flybys[start:stop] = right_journey.flyby_slots[start:stop]
    phases[start:stop] = right_journey.phase_slots[start:stop]
    child_journey = replace(left_journey, flyby_slots=tuple(flybys), phase_slots=tuple(phases))
    return synchronize_topology(schema, _replace_journey(left, journey_index, child_journey))


def phase_crossover(
    schema: GenomeSchema, left: Genotype, right: Genotype, rng: random.Random
) -> Genotype:
    journey_index = rng.randrange(len(left.journey_slots))
    left_journey = left.journey_slots[journey_index]
    right_journey = right.journey_slots[journey_index]
    phases = tuple(
        right_phase if rng.random() < 0.5 else left_phase
        for left_phase, right_phase in zip(left_journey.phase_slots, right_journey.phase_slots)
    )
    return synchronize_topology(schema, _replace_journey(left, journey_index, replace(left_journey, phase_slots=phases)))


class OperatorRegistry:
    def __init__(self) -> None:
        self._operators: dict[str, OperatorDefinition] = {}

    def register(self, operator: OperatorDefinition) -> None:
        if operator.name in self._operators:
            raise ValueError(f"operator already registered: {operator.name}")
        self._operators[operator.name] = operator

    def get(self, name: str) -> OperatorDefinition:
        try:
            return self._operators[name]
        except KeyError as error:
            raise KeyError(f"unknown operator: {name}") from error

    def choose(self, weights: dict[str, float], rng: random.Random, *, crossover: bool = False) -> OperatorDefinition:
        candidates = []
        for name, weight in sorted(weights.items()):
            operator = self.get(name)
            if weight > 0 and ((operator.crossover is not None) if crossover else (operator.mutation is not None)):
                candidates.append((operator, weight))
        if not candidates:
            raise ValueError("no eligible weighted operators")
        threshold = rng.random() * sum(weight for _, weight in candidates)
        running = 0.0
        for operator, weight in candidates:
            running += weight
            if threshold <= running:
                return operator
        return candidates[-1][0]

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._operators))


def default_operator_registry() -> OperatorRegistry:
    registry = OperatorRegistry()
    for name, scopes, function in (
        ("activation", ("journey", "flyby"), activation_mutation),
        ("insertion", ("journey", "flyby"), insertion_mutation),
        ("deletion", ("journey", "flyby"), deletion_mutation),
        ("flyby_replacement", ("flyby",), replacement_mutation),
        ("swap", ("flyby",), swap_mutation),
        ("timing", ("mission", "journey"), timing_mutation),
        ("hardware", ("mission",), hardware_mutation),
        ("phase_type", ("journey", "phase"), phase_type_mutation),
        ("dsm_count", ("journey", "phase"), dsm_count_mutation),
        ("generic_gene", ("mission", "journey", "phase"), generic_gene_mutation),
    ):
        def safe_mutation(schema, genotype, rng, implementation=function):
            proposed = synchronize_topology(schema, implementation(schema, genotype, rng))
            try:
                decode_genotype(schema, proposed, repair_policy="reject")
            except Exception:
                return genotype
            return proposed
        registry.register(OperatorDefinition(name, scopes, mutation=safe_mutation))
    for name, scopes, implementation in (
        ("journey_crossover", ("journey",), journey_crossover),
        ("subsequence_crossover", ("flyby", "phase"), subsequence_crossover),
        ("phase_crossover", ("phase",), phase_crossover),
    ):
        def safe_crossover(schema, left, right, rng, function=implementation):
            proposed = synchronize_topology(schema, function(schema, left, right, rng))
            try:
                decode_genotype(schema, proposed, repair_policy="reject")
            except Exception:
                return left
            return proposed
        registry.register(OperatorDefinition(name, scopes, crossover=safe_crossover))
    return registry
