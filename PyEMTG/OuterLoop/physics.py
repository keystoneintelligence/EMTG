"""Conservative two-body screens; heuristics remain auditable and optional."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol

from .model import JourneyPhenotype, MissionPhenotype
from .rules import UniverseCatalog


@dataclass(frozen=True)
class TwoBodyLegEstimate:
    departure: str
    arrival: str
    transfer_time_days: float
    departure_c3: float
    arrival_c3: float


def hohmann_leg_estimate(
    catalog: UniverseCatalog, departure: str, arrival: str
) -> TwoBodyLegEstimate:
    left = catalog.body(departure)
    right = catalog.body(arrival)
    radius_left, radius_right = left.semimajor_axis, right.semimajor_axis
    if radius_left <= 0 or radius_right <= 0 or catalog.central_mu <= 0:
        raise ValueError("two-body estimate requires positive semimajor axes and central mu")
    if departure == arrival:
        return TwoBodyLegEstimate(departure, arrival, 0.0, 0.0, 0.0)
    transfer_axis = 0.5 * (radius_left + radius_right)
    transfer_time = math.pi * math.sqrt(transfer_axis**3 / catalog.central_mu) / 86400.0
    circular_left = math.sqrt(catalog.central_mu / radius_left)
    circular_right = math.sqrt(catalog.central_mu / radius_right)
    transfer_left = math.sqrt(catalog.central_mu * (2.0 / radius_left - 1.0 / transfer_axis))
    transfer_right = math.sqrt(catalog.central_mu * (2.0 / radius_right - 1.0 / transfer_axis))
    return TwoBodyLegEstimate(
        departure,
        arrival,
        transfer_time,
        (transfer_left - circular_left) ** 2,
        (transfer_right - circular_right) ** 2,
    )


def journey_estimates(
    catalog: UniverseCatalog, journey: JourneyPhenotype
) -> tuple[TwoBodyLegEstimate, ...]:
    return tuple(
        hohmann_leg_estimate(catalog, left, right)
        for left, right in zip(journey.sequence, journey.sequence[1:])
    )


class PhysicsScreenProvider(Protocol):
    name: str

    def screen(
        self, phenotype: MissionPhenotype, catalog: UniverseCatalog
    ) -> tuple[bool, str | None, dict[str, float]]: ...


@dataclass(frozen=True)
class HohmannTimeScreen:
    minimum_factor: float = 0.25
    name: str = "hohmann_time"

    def screen(
        self, phenotype: MissionPhenotype, catalog: UniverseCatalog
    ) -> tuple[bool, str | None, dict[str, float]]:
        estimate = sum(
            leg.transfer_time_days
            for journey in phenotype.journeys
            for leg in journey_estimates(catalog, journey)
        )
        bounds = phenotype.mission.get("total_flight_time_bounds", phenotype.mission.get("flight_time_bounds"))
        duration = float(bounds[-1] if isinstance(bounds, (list, tuple)) else bounds) if bounds is not None else None
        threshold = estimate * self.minimum_factor
        accepted = duration is None or duration >= threshold
        return (
            accepted,
            None if accepted else f"mission upper flight time {duration} days is below {threshold:.6g} day heuristic",
            {"hohmann_transfer_time_days": estimate, "minimum_time_threshold_days": threshold},
        )


@dataclass(frozen=True)
class C3EnvelopeScreen:
    maximum_departure_c3: float | None = None
    maximum_arrival_c3: float | None = None
    name: str = "two_body_c3"

    def screen(
        self, phenotype: MissionPhenotype, catalog: UniverseCatalog
    ) -> tuple[bool, str | None, dict[str, float]]:
        estimates = [
            leg
            for journey in phenotype.journeys
            for leg in journey_estimates(catalog, journey)
        ]
        departure = estimates[0].departure_c3 if estimates else 0.0
        arrival = estimates[-1].arrival_c3 if estimates else 0.0
        reason = None
        if self.maximum_departure_c3 is not None and departure > self.maximum_departure_c3:
            reason = f"estimated departure C3 {departure:.6g} exceeds {self.maximum_departure_c3}"
        if self.maximum_arrival_c3 is not None and arrival > self.maximum_arrival_c3:
            reason = f"estimated arrival C3 {arrival:.6g} exceeds {self.maximum_arrival_c3}"
        return reason is None, reason, {"estimated_departure_c3": departure, "estimated_arrival_c3": arrival}
