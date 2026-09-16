"""Typed objective and constraint registries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .config import ObjectiveConfig
from .model import EvaluationResult, EvaluationStatus


Extractor = Callable[[EvaluationResult], float | None]


@dataclass(frozen=True)
class ObjectiveDefinition:
    name: str
    direction: str
    units: str
    source: str
    extractor: Extractor
    valid_for_infeasible: bool = False
    missing_behavior: str = "reject"


@dataclass(frozen=True)
class ConstraintDefinition:
    name: str
    units: str
    extractor: Extractor
    scale: float = 1.0


def _metric(name: str) -> Extractor:
    def extract(result: EvaluationResult) -> float | None:
        value = result.metrics.get(name)
        if value is None and hasattr(result, "objectives"):
            value = getattr(result, "objectives").get(name)
        return None if value is None else float(value)
    return extract


class ObjectiveRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ObjectiveDefinition] = {}

    def register(self, definition: ObjectiveDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"objective already registered: {definition.name}")
        if definition.direction not in {"minimize", "maximize"}:
            raise ValueError("objective direction must be minimize or maximize")
        self._definitions[definition.name] = definition

    def definition(self, name: str) -> ObjectiveDefinition:
        try:
            return self._definitions[name]
        except KeyError as error:
            raise KeyError(f"unknown objective: {name}") from error

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def extract(
        self, result: EvaluationResult, selected: tuple[ObjectiveConfig, ...]
    ) -> tuple[tuple[float, ...], tuple[str, ...]]:
        values: list[float] = []
        missing: list[str] = []
        for selection in selected:
            definition = self.definition(selection.name)
            valid_for_infeasible = (
                definition.valid_for_infeasible
                if selection.valid_for_infeasible is None
                else selection.valid_for_infeasible
            )
            if result.status is not EvaluationStatus.FEASIBLE and not valid_for_infeasible:
                missing.append(selection.name)
                continue
            raw = definition.extractor(result)
            if raw is None:
                policy = selection.missing_policy or definition.missing_behavior
                if policy == "penalize" and selection.penalty is not None:
                    raw = selection.penalty
                else:
                    missing.append(selection.name)
                    continue
            direction = selection.direction or definition.direction
            value = float(raw) / selection.scale
            values.append(-value if direction == "maximize" else value)
        return tuple(values), tuple(missing)


class ConstraintRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ConstraintDefinition] = {}

    def register(self, definition: ConstraintDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"constraint already registered: {definition.name}")
        self._definitions[definition.name] = definition

    def definition(self, name: str) -> ConstraintDefinition:
        try:
            return self._definitions[name]
        except KeyError as error:
            raise KeyError(f"unknown constraint: {name}") from error

    def aggregate(self, result: EvaluationResult, names: tuple[str, ...]) -> float | None:
        values: list[float] = []
        for name in names:
            definition = self.definition(name)
            value = definition.extractor(result)
            if value is not None:
                values.append(max(0.0, value / definition.scale))
        return sum(values) if values else None


def default_objective_registry() -> ObjectiveRegistry:
    registry = ObjectiveRegistry()
    definitions = {
        "flight_time": ("minimize", "days"),
        "launch_epoch": ("minimize", "MJD"),
        "launch_window_open_date": ("minimize", "MJD"),
        "delivered_mass": ("maximize", "kg"),
        "final_journey_mass_increment": ("maximize", "kg"),
        "departure_c3": ("minimize", "km^2/s^2"),
        "arrival_c3": ("minimize", "km^2/s^2"),
        "arrival_declination": ("minimize", "degrees"),
        "entry_interface_velocity": ("minimize", "km/s"),
        "deterministic_delta_v": ("minimize", "km/s"),
        "emtg_objective": ("minimize", "native"),
        "total_propellant": ("minimize", "kg"),
        "number_of_journeys": ("minimize", "count"),
        "number_of_flybys": ("minimize", "count"),
        "dry_mass_margin": ("maximize", "kg"),
        "beginning_of_life_power": ("minimize", "kW"),
        "bus_power": ("minimize", "kW"),
        "thruster_duty_cycle": ("minimize", "fraction"),
        "number_of_thrusters": ("minimize", "count"),
        "launch_vehicle_preference": ("minimize", "rank"),
        "thruster_preference": ("minimize", "rank"),
        "normalized_aggregate_control": ("minimize", "dimensionless"),
        "point_group_value": ("maximize", "points"),
        "convergence_probability": ("maximize", "fraction"),
        "runtime": ("minimize", "seconds"),
    }
    for name, (direction, units) in definitions.items():
        registry.register(ObjectiveDefinition(name, direction, units, f"metric:{name}", _metric(name)))
    return registry
