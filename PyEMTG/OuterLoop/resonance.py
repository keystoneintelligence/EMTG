"""Independent, public-record resonance-aware moon-tour extension."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import random
from typing import Iterable

from .genome import GenomeSchema, synchronize_topology
from .model import Genotype, HiddenGeneSlot, MissionPhenotype
from .rules import UniverseCatalog


@dataclass(frozen=True, order=True)
class ResonanceRatio:
    spacecraft_revolutions: int
    moon_revolutions: int

    def __post_init__(self) -> None:
        if self.spacecraft_revolutions < 1 or self.moon_revolutions < 1:
            raise ValueError("resonance integers must be positive")

    @property
    def label(self) -> str:
        return f"{self.spacecraft_revolutions}:{self.moon_revolutions}"


@dataclass(frozen=True)
class ResonanceOpportunity:
    moon: str
    ratio: ResonanceRatio
    moon_period_seconds: float
    spacecraft_period_seconds: float
    spacecraft_semimajor_axis_km: float
    encounter_speed_km_s: float
    moon_speed_km_s: float
    relative_speed_km_s: float
    maximum_turning_degrees: float
    required_turning_degrees: float
    orbit_crosses_moon_radius: bool
    periapsis_radius_km: float
    apoapsis_radius_km: float
    feasible: bool
    reason: str | None = None


def resonance_opportunity(
    catalog: UniverseCatalog,
    moon_name: str,
    ratio: ResonanceRatio,
    *,
    minimum_turning_degrees: float = 0.0,
    incoming_flight_path_angle_degrees: float = 0.0,
    outgoing_flight_path_angle_degrees: float = 0.0,
) -> ResonanceOpportunity:
    moon = catalog.body(moon_name)
    if moon.semimajor_axis <= 0 or catalog.central_mu <= 0:
        raise ValueError("resonance requires positive central mu and moon semimajor axis")
    moon_period = 2.0 * math.pi * math.sqrt(moon.semimajor_axis**3 / catalog.central_mu)
    spacecraft_period = moon_period * ratio.moon_revolutions / ratio.spacecraft_revolutions
    spacecraft_axis = (catalog.central_mu * (spacecraft_period / (2.0 * math.pi)) ** 2) ** (1.0 / 3.0)
    inverse_radius_term = 2.0 / moon.semimajor_axis - 1.0 / spacecraft_axis
    if inverse_radius_term <= 0:
        return ResonanceOpportunity(
            moon_name, ratio, moon_period, spacecraft_period, spacecraft_axis,
            0.0, 0.0, 0.0, 0.0,
            abs(outgoing_flight_path_angle_degrees - incoming_flight_path_angle_degrees),
            False, 0.0, 0.0, False, "resonant orbit does not reach the moon radius",
        )
    minimum_eccentricity = abs(1.0 - moon.semimajor_axis / spacecraft_axis)
    periapsis_radius = spacecraft_axis * (1.0 - minimum_eccentricity)
    apoapsis_radius = spacecraft_axis * (1.0 + minimum_eccentricity)
    crosses = periapsis_radius - 1.0e-9 <= moon.semimajor_axis <= apoapsis_radius + 1.0e-9
    spacecraft_speed = math.sqrt(catalog.central_mu * inverse_radius_term)
    moon_speed = math.sqrt(catalog.central_mu / moon.semimajor_axis)
    relative_speed = abs(spacecraft_speed - moon_speed)
    periapsis = moon.radius + max(0.0, moon.minimum_flyby_altitude)
    if relative_speed == 0.0:
        maximum_turning = 180.0
    else:
        eccentricity = 1.0 + periapsis * relative_speed**2 / moon.mu
        maximum_turning = math.degrees(2.0 * math.asin(min(1.0, 1.0 / eccentricity)))
    required_turning = max(
        minimum_turning_degrees,
        abs(outgoing_flight_path_angle_degrees - incoming_flight_path_angle_degrees),
    )
    feasible = crosses and maximum_turning + 1.0e-12 >= required_turning
    return ResonanceOpportunity(
        moon_name,
        ratio,
        moon_period,
        spacecraft_period,
        spacecraft_axis,
        spacecraft_speed,
        moon_speed,
        relative_speed,
        maximum_turning,
        required_turning,
        crosses,
        periapsis_radius,
        apoapsis_radius,
        feasible,
        None if feasible else (
            "resonant orbit does not cross the moon radius" if not crosses
            else "altitude-limited available turning is below the required incoming-to-outgoing turn"
        ),
    )


def resonance_mutation(
    schema: GenomeSchema,
    genotype: Genotype,
    rng: random.Random,
    catalog: UniverseCatalog,
    ratios: Iterable[ResonanceRatio],
    *,
    replace_existing: bool = False,
) -> tuple[Genotype, ResonanceOpportunity | None]:
    ratios = tuple(sorted(ratios))
    candidates: list[tuple[int, int, str]] = []
    for journey_index, journey in enumerate(genotype.journey_slots):
        if not journey.active:
            continue
        active = [(index, slot.values.get("flyby_body")) for index, slot in enumerate(journey.flyby_slots) if slot.active]
        inactive = [index for index, slot in enumerate(journey.flyby_slots) if not slot.active]
        if inactive:
            for _, body in active:
                if body in catalog.bodies:
                    candidates.append((journey_index, inactive[0], str(body)))
        if replace_existing:
            for left, right in zip(active, active[1:]):
                if left[1] == right[1] and left[1] in catalog.bodies:
                    candidates.append((journey_index, right[0], str(right[1])))
    if not candidates or not ratios:
        return genotype, None
    ordered_candidates = list(candidates)
    ordered_ratios = list(ratios)
    rng.shuffle(ordered_candidates)
    rng.shuffle(ordered_ratios)
    opportunity = None
    selected = None
    for candidate in ordered_candidates:
        for ratio in ordered_ratios:
            considered = resonance_opportunity(catalog, candidate[2], ratio)
            opportunity = considered
            if considered.feasible:
                selected = candidate
                break
        if selected is not None:
            break
    if selected is None or opportunity is None:
        return genotype, opportunity
    journey_index, slot_index, moon = selected
    journey = genotype.journey_slots[journey_index]
    flybys = list(journey.flyby_slots)
    flybys[slot_index] = HiddenGeneSlot(True, {
        **flybys[slot_index].values,
        "flyby_body": moon,
        "resonance_ratio": opportunity.ratio.label,
        "resonance_required_turning_degrees": opportunity.required_turning_degrees,
    })
    journeys = list(genotype.journey_slots)
    journeys[journey_index] = replace(journey, flyby_slots=tuple(flybys))
    return synchronize_topology(schema, replace(genotype, journey_slots=tuple(journeys))), opportunity


def resonance_metadata(
    phenotype: MissionPhenotype,
    catalog: UniverseCatalog,
    ratios: Iterable[ResonanceRatio],
    *,
    minimum_turning_degrees: float = 0.0,
) -> dict[str, object]:
    ratios = tuple(sorted(ratios))
    chains = []
    for journey_index, journey in enumerate(phenotype.journeys):
        for encounter_index, (left, right) in enumerate(zip(journey.sequence, journey.sequence[1:])):
            if left != right or left not in catalog.bodies:
                continue
            opportunities = [
                resonance_opportunity(
                    catalog,
                    left,
                    ratio,
                    minimum_turning_degrees=minimum_turning_degrees,
                )
                for ratio in ratios
            ]
            chains.append({
                "journey": journey_index,
                "encounter": encounter_index,
                "moon": left,
                "opportunities": [
                    {
                        "ratio": opportunity.ratio.label,
                        "feasible": opportunity.feasible,
                        "spacecraft_period_seconds": opportunity.spacecraft_period_seconds,
                        "spacecraft_semimajor_axis_km": opportunity.spacecraft_semimajor_axis_km,
                        "relative_speed_km_s": opportunity.relative_speed_km_s,
                        "maximum_turning_degrees": opportunity.maximum_turning_degrees,
                        "required_turning_degrees": opportunity.required_turning_degrees,
                        "orbit_crosses_moon_radius": opportunity.orbit_crosses_moon_radius,
                        "reason": opportunity.reason,
                    }
                    for opportunity in opportunities
                ],
            })
    return {"central_body": catalog.central_body, "chains": chains}
