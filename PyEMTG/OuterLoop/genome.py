"""Hierarchical null/hidden-gene mission genome codec."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from decimal import Decimal, ROUND_FLOOR
import random
from typing import Any, Iterable, Mapping

from .config import GeneSpec, SearchConfig
from .model import (
    Genotype,
    HiddenGeneSlot,
    JourneyGenome,
    JourneyPhenotype,
    MissionPhenotype,
    PhasePhenotype,
    RepairRecord,
    RepairStatus,
)


class GenomeError(ValueError):
    pass


class TopologyError(GenomeError):
    pass


def _quantize(value: Decimal, spec: GeneSpec) -> Decimal:
    if spec.resolution is None or spec.lower is None:
        return value
    steps = ((value - spec.lower) / spec.resolution).to_integral_value(rounding=ROUND_FLOOR)
    result = spec.lower + steps * spec.resolution
    if spec.upper is not None:
        result = min(result, spec.upper)
    return result


def normalize_gene(value: Any, spec: GeneSpec) -> Any:
    if spec.kind == "fixed":
        return spec.fixed
    if spec.kind == "choice":
        if value not in spec.choices:
            raise GenomeError(f"{value!r} is outside the configured choices")
        return value
    if spec.kind == "integer":
        result = int(value)
        if Decimal(result) < spec.lower or Decimal(result) > spec.upper:  # type: ignore[operator]
            raise GenomeError(f"{result} is outside the configured integer range")
        return result
    result = _quantize(Decimal(str(value)), spec)
    if result < spec.lower or result > spec.upper:  # type: ignore[operator]
        raise GenomeError(f"{result} is outside the configured decimal range")
    return str(result.normalize())


def sample_gene(spec: GeneSpec, rng: random.Random) -> Any:
    if spec.kind == "fixed":
        return spec.fixed
    if spec.kind == "choice":
        return spec.choices[rng.randrange(len(spec.choices))]
    if spec.kind == "integer":
        return rng.randint(int(spec.lower), int(spec.upper))  # type: ignore[arg-type]
    steps = int(((spec.upper - spec.lower) / spec.resolution).to_integral_value(rounding=ROUND_FLOOR))  # type: ignore[operator]
    return str((spec.lower + spec.resolution * rng.randint(0, steps)).normalize())  # type: ignore[operator]


def mutate_gene(value: Any, spec: GeneSpec, rng: random.Random) -> Any:
    if spec.kind == "fixed":
        return spec.fixed
    if spec.kind == "choice":
        alternatives = [choice for choice in spec.choices if choice != value]
        return alternatives[rng.randrange(len(alternatives))] if alternatives else value
    if spec.kind == "integer":
        lower, upper = int(spec.lower), int(spec.upper)  # type: ignore[arg-type]
        if lower == upper:
            return lower
        step = -1 if rng.random() < 0.5 else 1
        result = int(value) + step
        return max(lower, min(upper, result))
    current = Decimal(str(value))
    step = -spec.resolution if rng.random() < 0.5 else spec.resolution  # type: ignore[operator]
    return str(max(spec.lower, min(spec.upper, current + step)).normalize())  # type: ignore[arg-type]


def _sample_values(specs: Mapping[str, GeneSpec], rng: random.Random) -> dict[str, Any]:
    return {name: sample_gene(spec, rng) for name, spec in sorted(specs.items())}


def _resolve_values(values: Mapping[str, Any], specs: Mapping[str, GeneSpec]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for name, spec in sorted(specs.items()):
        if spec.kind == "fixed":
            output[name] = spec.fixed
        elif name in values:
            output[name] = normalize_gene(values[name], spec)
        else:
            raise GenomeError(f"genotype is missing variable gene {name}")
    return output


def _activation_tags(size: int, minimum: int, rng: random.Random) -> list[bool]:
    tags = [rng.random() >= 0.5 for _ in range(size)]
    active = sum(tags)
    if active < minimum:
        inactive = [index for index, value in enumerate(tags) if not value]
        rng.shuffle(inactive)
        for index in inactive[: minimum - active]:
            tags[index] = True
    return tags


@dataclass(frozen=True)
class GenomeSchema:
    search: SearchConfig

    @property
    def max_journeys(self) -> int:
        return self.search.max_journeys

    @property
    def max_flybys(self) -> int:
        return self.search.max_flybys


def synchronize_topology(schema: GenomeSchema, genotype: Genotype) -> Genotype:
    """Atomically synchronize activity tags/counts and phase-slot invariants."""
    search = schema.search
    if len(genotype.journey_slots) != search.max_journeys:
        return genotype
    mission = dict(genotype.mission)
    count_mode_count = None
    if search.activation_mode == "count":
        count_mode_count = max(
            search.min_journeys,
            min(search.max_journeys, int(mission.get("number_of_journeys", search.min_journeys))),
        )
    journeys = []
    for journey_index, journey in enumerate(genotype.journey_slots):
        if count_mode_count is not None:
            journey = replace(journey, active=journey_index < count_mode_count)
        if (len(journey.flyby_slots) != search.max_flybys
                or len(journey.phase_slots) != search.max_flybys + 1):
            return genotype
        phases = [
            replace(journey.phase_slots[index], active=flyby.active)
            for index, flyby in enumerate(journey.flyby_slots)
        ]
        phases.append(replace(journey.phase_slots[-1], active=True))
        journeys.append(replace(journey, phase_slots=tuple(phases)))
    active = sum(journey.active for journey in journeys)
    if search.activation_mode in {"count", "tags_and_count"}:
        mission["number_of_journeys"] = active
    return Genotype(mission, tuple(journeys))


def validate_genotype_structure(schema: GenomeSchema, genotype: Genotype) -> None:
    """Strict operator/output invariant check (separate from legacy decoding)."""
    synchronized = synchronize_topology(schema, genotype)
    if synchronized != genotype:
        raise TopologyError("flyby/phase tags, terminal phase, or activation count are not synchronized")
    decode_genotype(schema, genotype, repair_policy="reject")


def random_genotype(schema: GenomeSchema, rng: random.Random) -> Genotype:
    search = schema.search
    mission = _sample_values(search.mission_genes, rng)
    journey_tags = _activation_tags(search.max_journeys, search.min_journeys, rng)
    active_count = sum(journey_tags)
    if search.activation_mode in {"count", "tags_and_count"}:
        active_count = rng.randint(search.min_journeys, search.max_journeys)
        mission["number_of_journeys"] = active_count
        if search.activation_mode == "count":
            journey_tags = [index < active_count for index in range(search.max_journeys)]

    journeys: list[JourneyGenome] = []
    for active in journey_tags:
        flyby_tags = _activation_tags(search.max_flybys, search.min_flybys, rng)
        flybys = tuple(
            HiddenGeneSlot(
                tag,
                {
                    "flyby_body": search.flyby_bodies[rng.randrange(len(search.flyby_bodies))]
                    if search.flyby_bodies else None,
                },
            )
            for tag in flyby_tags
        )
        # One phase payload is retained for every possible flyby leg plus the
        # terminal leg.  Flyby activity controls which payloads are decoded.
        phases = tuple(
            HiddenGeneSlot(flyby_tags[index] if index < search.max_flybys else True,
                           _sample_values(search.phase_genes, rng))
            for index in range(search.max_flybys + 1)
        )
        journeys.append(
            JourneyGenome(
                active=active,
                values=_sample_values(search.journey_genes, rng),
                flyby_slots=flybys,
                phase_slots=phases,
            )
        )
    return synchronize_topology(schema, Genotype(mission=mission, journey_slots=tuple(journeys)))


def stratify_genotype(schema: GenomeSchema, genotype: Genotype, slot: int) -> Genotype:
    """Deterministically cover major discrete domains in an initial population."""
    search = schema.search
    mission = dict(genotype.mission)
    journey_levels = search.max_journeys - search.min_journeys + 1
    journey_count = search.min_journeys + slot % journey_levels
    if search.activation_mode in {"count", "tags_and_count"}:
        mission["number_of_journeys"] = journey_count
    for offset, (name, spec) in enumerate(sorted(search.mission_genes.items())):
        if spec.variable and spec.kind == "choice":
            mission[name] = spec.choices[(slot // max(1, journey_levels + offset)) % len(spec.choices)]
    flyby_levels = search.max_flybys - search.min_flybys + 1
    flyby_count = search.min_flybys + (slot // journey_levels) % flyby_levels
    journeys = []
    for journey_index, journey in enumerate(genotype.journey_slots):
        active = journey_index < journey_count
        values = dict(journey.values)
        for offset, (name, spec) in enumerate(sorted(search.journey_genes.items())):
            if spec.variable and spec.kind == "choice":
                values[name] = spec.choices[
                    (slot // max(1, journey_levels * flyby_levels + offset)) % len(spec.choices)
                ]
        flybys = tuple(
            replace(flyby, active=(active and index < flyby_count))
            for index, flyby in enumerate(journey.flyby_slots)
        )
        phases = []
        for phase_index, phase in enumerate(journey.phase_slots):
            phase_values = dict(phase.values)
            for offset, (name, spec) in enumerate(sorted(search.phase_genes.items())):
                if spec.variable and spec.kind == "choice":
                    phase_values[name] = spec.choices[
                        (slot // max(1, journey_levels * flyby_levels + offset)) % len(spec.choices)
                    ]
            phases.append(replace(phase, values=phase_values))
        journeys.append(
            replace(
                journey,
                active=active,
                values=values,
                flyby_slots=flybys,
                phase_slots=tuple(phases),
            )
        )
    return synchronize_topology(schema, Genotype(mission, tuple(journeys)))


def _active_journey_indices(genotype: Genotype, search: SearchConfig) -> list[int]:
    if len(genotype.journey_slots) != search.max_journeys:
        raise TopologyError(
            f"expected {search.max_journeys} journey slots, got {len(genotype.journey_slots)}"
        )
    tagged = [index for index, slot in enumerate(genotype.journey_slots) if slot.active]
    if search.activation_mode == "count":
        count = int(genotype.mission.get("number_of_journeys", search.min_journeys))
        return list(range(max(0, min(count, search.max_journeys))))
    if search.activation_mode == "tags_and_count":
        count = int(genotype.mission.get("number_of_journeys", len(tagged)))
        return tagged[: max(0, count)]
    return tagged


def decode_genotype(
    schema: GenomeSchema,
    genotype: Genotype,
    *,
    repair_policy: str = "reject",
    repairs: Iterable[str] = (),
) -> MissionPhenotype:
    if repair_policy not in {"reject", "compact"}:
        raise ValueError("repair_policy must be reject or compact")
    search = schema.search
    enabled_repairs = frozenset(repairs) | frozenset(search.repairs)
    mission = _resolve_values(genotype.mission, search.mission_genes)
    active_indices = _active_journey_indices(genotype, search)
    repairs: list[RepairRecord] = []

    stop_after = mission.get("stop_after_journey")
    if stop_after is not None:
        count = int(stop_after)
        if count < 1:
            raise TopologyError("stop_after_journey must be at least one")
        active_indices = active_indices[:count]

    if len(active_indices) < search.min_journeys:
        if repair_policy == "reject":
            raise TopologyError("too few active journeys")
        before = tuple(active_indices)
        for index in range(search.max_journeys):
            if index not in active_indices:
                active_indices.append(index)
            if len(active_indices) == search.min_journeys:
                break
        active_indices.sort()
        repairs.append(RepairRecord("journeys", before, tuple(active_indices), "activate minimum journeys"))
    if len(active_indices) > search.max_journeys:
        raise TopologyError("too many active journeys")

    journeys: list[JourneyPhenotype] = []
    previous_arrival: str | None = None
    for decoded_index, slot_index in enumerate(active_indices):
        slot = genotype.journey_slots[slot_index]
        values = _resolve_values(slot.values, search.journey_genes)
        is_last = decoded_index == len(active_indices) - 1

        departure_value = values.get("departure_destination")
        arrival_value = values.get("arrival_destination")
        if decoded_index == 0 and search.fixed_start:
            departure = search.fixed_start
        elif search.chain_journeys and previous_arrival:
            if departure_value is not None and str(departure_value) != previous_arrival:
                if "reconnect_endpoints" not in enabled_repairs:
                    raise TopologyError(
                        f"journey {decoded_index} departure does not continue prior arrival"
                    )
                repairs.append(
                    RepairRecord(
                        f"journeys[{decoded_index}].departure",
                        str(departure_value),
                        previous_arrival,
                        "reconnect inherited journey endpoint",
                    )
                )
            departure = previous_arrival
        elif departure_value:
            departure = str(departure_value)
        else:
            raise TopologyError(f"journey {decoded_index} has no departure destination")

        if is_last and search.fixed_final:
            arrival = search.fixed_final
        elif arrival_value:
            arrival = str(arrival_value)
        elif search.fixed_final:
            arrival = search.fixed_final
        else:
            raise TopologyError(f"journey {decoded_index} has no arrival destination")

        if len(slot.flyby_slots) != search.max_flybys:
            raise TopologyError(f"journey {decoded_index} has the wrong flyby slot count")
        active_flyby_indices = [index for index, flyby in enumerate(slot.flyby_slots) if flyby.active]
        if len(active_flyby_indices) < search.min_flybys:
            if repair_policy == "reject":
                raise TopologyError(f"journey {decoded_index} has too few active flybys")
            before = tuple(active_flyby_indices)
            for index in range(search.max_flybys):
                if index not in active_flyby_indices:
                    active_flyby_indices.append(index)
                if len(active_flyby_indices) == search.min_flybys:
                    break
            active_flyby_indices.sort()
            repairs.append(
                RepairRecord(
                    f"journeys[{decoded_index}].flybys",
                    before,
                    tuple(active_flyby_indices),
                    "activate minimum flybys",
                )
            )

        flyby_bodies: list[str] = []
        phases: list[PhasePhenotype] = []
        if len(slot.phase_slots) != search.max_flybys + 1:
            raise TopologyError(f"journey {decoded_index} has the wrong phase slot count")
        # Decoding remains tolerant of legacy in-memory genotypes so inactive
        # payload is neutral. New generators/operators are checked by
        # ``validate_genotype_structure`` and always synchronize these tags.
        for flyby_index in active_flyby_indices:
            body = slot.flyby_slots[flyby_index].values.get("flyby_body")
            if not body or str(body) not in search.flyby_bodies:
                raise TopologyError(f"journey {decoded_index} has an invalid flyby body")
            flyby_bodies.append(str(body))
            phase_values = _resolve_values(slot.phase_slots[flyby_index].values, search.phase_genes)
            phases.append(PhasePhenotype(str(body), phase_values))
        terminal_values = _resolve_values(slot.phase_slots[-1].values, search.phase_genes)
        phases.append(PhasePhenotype(arrival, terminal_values))

        public_values = {
            key: value
            for key, value in values.items()
            if key not in {"departure_destination", "arrival_destination"}
        }
        for name, value in list(public_values.items()):
            if (
                name.endswith("bounds")
                and isinstance(value, (list, tuple))
                and len(value) == 2
                and float(value[0]) > float(value[1])
            ):
                if "clamp_bounds" not in enabled_repairs:
                    raise TopologyError(f"journey {decoded_index} {name} is reversed")
                corrected = (value[1], value[0])
                repairs.append(
                    RepairRecord(
                        f"journeys[{decoded_index}].{name}", value, corrected,
                        "restore ordered bounds",
                    )
                )
                public_values[name] = corrected
        resonance_choices = [
            slot.flyby_slots[index].values.get("resonance_ratio")
            for index in active_flyby_indices
            if slot.flyby_slots[index].values.get("resonance_ratio") is not None
        ]
        if resonance_choices:
            public_values["resonance_choices"] = tuple(resonance_choices)
        journeys.append(
            JourneyPhenotype(
                departure=departure,
                arrival=arrival,
                flybys=tuple(flyby_bodies),
                values=public_values,
                phases=tuple(phases),
            )
        )
        previous_arrival = arrival

    mission["number_of_journeys"] = len(journeys)
    mission["number_of_flybys"] = sum(len(journey.flybys) for journey in journeys)
    return MissionPhenotype(
        mission=mission,
        journeys=tuple(journeys),
        repair_status=RepairStatus.REPAIRED if repairs else RepairStatus.UNCHANGED,
        repairs=tuple(repairs),
    )


def encode_phenotype(schema: GenomeSchema, phenotype: MissionPhenotype) -> Genotype:
    """Create a deterministic active-prefix genotype for warm population import."""
    search = schema.search
    if not search.min_journeys <= len(phenotype.journeys) <= search.max_journeys:
        raise GenomeError("phenotype journey count is outside the schema")
    mission = {
        name: normalize_gene(phenotype.mission.get(name, spec.fixed), spec)
        for name, spec in search.mission_genes.items()
    }
    if search.activation_mode in {"count", "tags_and_count"}:
        mission["number_of_journeys"] = len(phenotype.journeys)
    journeys: list[JourneyGenome] = []
    for index in range(search.max_journeys):
        if index >= len(phenotype.journeys):
            journeys.append(
                JourneyGenome(
                    False,
                    _sample_values(search.journey_genes, random.Random(index)),
                    tuple(HiddenGeneSlot(False, {"flyby_body": search.flyby_bodies[0] if search.flyby_bodies else None}) for _ in range(search.max_flybys)),
                    tuple(HiddenGeneSlot(False if phase < search.max_flybys else True, _sample_values(search.phase_genes, random.Random(index))) for phase in range(search.max_flybys + 1)),
                )
            )
            continue
        journey = phenotype.journeys[index]
        values = dict(journey.values)
        values["departure_destination"] = journey.departure
        values["arrival_destination"] = journey.arrival
        resolved = _resolve_values(values, search.journey_genes)
        flybys = []
        phases = []
        for flyby_index in range(search.max_flybys):
            active = flyby_index < len(journey.flybys)
            body = journey.flybys[flyby_index] if active else (search.flyby_bodies[0] if search.flyby_bodies else None)
            flybys.append(HiddenGeneSlot(active, {"flyby_body": body}))
            phase_values = (
                journey.phases[flyby_index].values
                if active else _sample_values(search.phase_genes, random.Random(index * 1000 + flyby_index))
            )
            phases.append(HiddenGeneSlot(active, _resolve_values(phase_values, search.phase_genes)))
        terminal = journey.phases[-1].values if journey.phases else {}
        phases.append(HiddenGeneSlot(True, _resolve_values(terminal, search.phase_genes)))
        journeys.append(JourneyGenome(True, resolved, tuple(flybys), tuple(phases)))
    return synchronize_topology(schema, Genotype(mission, tuple(journeys)))
