"""Native EMTG result parsing, independent of search or publication services."""
from __future__ import annotations
from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

EXTRACTION_VERSION = "emtg-result-v3"

def _float_after_colon(text: str) -> float | None:
    try:
        return float(text.split(":", 1)[1].strip().split()[0])
    except (IndexError, ValueError):
        return None


def _float_csv(fields: Sequence[str], index: int) -> float | None:
    try:
        value = fields[index].strip()
        return None if value in {"", "-"} else float(value)
    except (IndexError, ValueError):
        return None


@dataclass(frozen=True)
class ParsedEMTGResult:
    complete: bool
    feasible: bool
    objective: float | None
    violation: float | None
    metrics: Mapping[str, Any]
    xdescriptions: tuple[str, ...]
    decision_vector: tuple[float, ...]
    constraint_descriptions: tuple[str, ...]
    constraint_vector: tuple[float, ...]
    failure_reason: str | None = None
    xlowerbounds: tuple[float, ...] = ()
    xupperbounds: tuple[float, ...] = ()


class EMTGResultParser:
    scalar_patterns = {
        "deterministic_delta_v": re.compile(r"^Total deterministic deltav \(km/s\):\s*(\S+)", re.I),
        "delivered_mass": re.compile(r"^Spacecraft: Final mass including propellant margin \(kg\):\s*(\S+)", re.I),
        "dry_mass": re.compile(r"^Spacecraft: Dry mass \(kg\):\s*(\S+)", re.I),
        "electric_propellant": re.compile(r"^Spacecraft: Total electric propellant \(kg\):\s*(\S+)", re.I),
        "chemical_fuel": re.compile(r"^Spacecraft: Total chemical fuel \(kg\):\s*(\S+)", re.I),
        "chemical_oxidizer": re.compile(r"^Spacecraft: Total chemical oxidizer \(kg\):\s*(\S+)", re.I),
        "beginning_of_life_power": re.compile(r"^Beginning of life power.*?:\s*(\S+)", re.I),
        "thruster_duty_cycle": re.compile(r"^Thruster duty cycle:\s*(\S+)", re.I),
        "bus_power": re.compile(r"^(?:Spacecraft:\s*)?Bus power.*?:\s*(\S+)", re.I),
        "electric_propellant_used": re.compile(r"^Spacecraft: Electric propellant used \(kg\):\s*(\S+)", re.I),
        "chemical_fuel_used": re.compile(r"^Spacecraft: Chemical fuel used \(kg\):\s*(\S+)", re.I),
        "chemical_oxidizer_used": re.compile(r"^Spacecraft: Chemical oxidizer used \(kg\):\s*(\S+)", re.I),
    }

    def parse(self, path: str | Path, *, failure_file: bool = False) -> ParsedEMTGResult:
        source = Path(path)
        try:
            lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as error:
            return ParsedEMTGResult(False, False, None, None, {}, (), (), (), (), str(error))
        objective = None
        violation = None
        best_feasible_attempt = 0
        first_feasible = 0
        solution_attempts = 0
        time_to_best_seconds: float | None = None
        first_nlp_objective: float | None = None
        metrics: dict[str, Any] = {}
        journey_times: list[float] = []
        journey_mass_increments: list[float] = []
        xdescriptions: tuple[str, ...] = ()
        decision_vector: tuple[float, ...] = ()
        xlowerbounds: tuple[float, ...] = ()
        xupperbounds: tuple[float, ...] = ()
        fdescriptions: tuple[str, ...] = ()
        constraint_vector: tuple[float, ...] = ()
        events: list[dict[str, Any]] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("J ="):
                try:
                    objective = float(stripped.split("=", 1)[1])
                except ValueError:
                    pass
            elif stripped.startswith("with violation"):
                try:
                    violation = abs(float(stripped.rsplit(" ", 1)[1]))
                except ValueError:
                    pass
            elif stripped.startswith("Solution attempt that produced a feasible solution"):
                value = _float_after_colon(stripped)
                best_feasible_attempt = int(value or 0)
            elif stripped.startswith("Number of solution attempts"):
                value = _float_after_colon(stripped)
                solution_attempts = int(value or 0)
            elif stripped.startswith(
                "Time to completion of solution attempt that produced a feasible solution"
            ):
                time_to_best_seconds = _float_after_colon(stripped)
            elif stripped.startswith("Was first NLP solve feasible"):
                value = _float_after_colon(stripped)
                first_feasible = int(value or 0)
            elif stripped.startswith("Objective function value from first NLP solve"):
                first_nlp_objective = _float_after_colon(stripped)
            elif stripped.startswith("Journey flight time (days)"):
                value = _float_after_colon(stripped)
                if value is not None:
                    journey_times.append(value)
            elif stripped.startswith("Journey final mass increment"):
                value = _float_after_colon(stripped)
                if value is not None:
                    journey_mass_increments.append(value)
            elif stripped.startswith("Xdescriptions,"):
                xdescriptions = tuple(stripped.split(",")[1:])
            elif stripped.startswith("Decision Vector:"):
                decision_vector = _parse_numeric_csv(stripped.split(",")[1:])
            elif stripped.startswith("Xlowerbounds,"):
                xlowerbounds = _parse_numeric_csv(stripped.split(",")[1:])
            elif stripped.startswith("Xupperbounds,"):
                xupperbounds = _parse_numeric_csv(stripped.split(",")[1:])
            elif stripped.startswith("Fdescriptions,"):
                fdescriptions = tuple(stripped.split(",")[1:])
            elif stripped.startswith("Constraint_Vector,"):
                constraint_vector = _parse_numeric_csv(stripped.split(",")[1:])
            elif re.match(r"^\d+\s*\|", stripped):
                event = self._parse_event(stripped)
                if event:
                    events.append(event)
            for name, pattern in self.scalar_patterns.items():
                match = pattern.match(stripped)
                if match:
                    try:
                        metrics[name] = float(match.group(1))
                    except ValueError:
                        pass
        if objective is not None:
            metrics["emtg_objective"] = objective
        metrics["optimizer_solution_attempts"] = solution_attempts
        metrics["optimizer_best_feasible_attempt"] = best_feasible_attempt
        metrics["optimizer_first_nlp_feasible"] = bool(first_feasible)
        if time_to_best_seconds is not None and time_to_best_seconds >= 0.0:
            metrics["optimizer_time_to_best_seconds"] = time_to_best_seconds
        if first_nlp_objective is not None:
            metrics["optimizer_first_nlp_objective"] = first_nlp_objective
        if journey_times:
            metrics["flight_time"] = sum(journey_times)
        if journey_mass_increments:
            metrics["final_journey_mass_increment"] = journey_mass_increments[-1]
        propellant_parts = [metrics.get(name) for name in ("electric_propellant", "chemical_fuel", "chemical_oxidizer")]
        available_propellant = [float(value) for value in propellant_parts if value is not None]
        if available_propellant:
            metrics["total_propellant"] = sum(available_propellant)
        consumed_parts = [
            metrics.get(name)
            for name in (
                "electric_propellant_used",
                "chemical_fuel_used",
                "chemical_oxidizer_used",
            )
        ]
        consumed = [float(value) for value in consumed_parts if value is not None]
        if consumed:
            metrics["total_propellant_used"] = sum(consumed)
        if metrics.get("delivered_mass") is not None and metrics.get("dry_mass") is not None:
            metrics["dry_mass_margin"] = float(metrics["delivered_mass"]) - float(metrics["dry_mass"])
        controls: dict[str, list[float]] = {}
        for description, value in zip(xdescriptions, decision_vector):
            match = re.search(r"step\s+(\d+)\s+u_([xyz])$", description, re.I)
            if match:
                controls.setdefault(match.group(1), []).append(float(value))
        complete_controls = [values for values in controls.values() if len(values) == 3]
        if complete_controls:
            metrics["normalized_aggregate_control"] = sum(
                math.sqrt(sum(component * component for component in values))
                for values in complete_controls
            ) / len(complete_controls)
        if events:
            first, last = events[0], events[-1]
            metrics.setdefault("launch_epoch", first.get("julian_date_mjd"))
            metrics.setdefault("arrival_epoch", last.get("julian_date_mjd"))
            metrics.setdefault("initial_mass", first.get("mass"))
            metrics.setdefault("departure_c3", first.get("c3"))
            metrics.setdefault("arrival_c3", last.get("c3"))
            metrics.setdefault("arrival_declination", last.get("declination"))
            metrics.setdefault("delivered_mass", last.get("mass"))
            if last.get("velocity_magnitude") is not None:
                metrics.setdefault("entry_interface_velocity", last["velocity_magnitude"])
            engines = [event.get("active_engines") for event in events if event.get("active_engines") is not None]
            if engines:
                metrics["number_of_thrusters"] = max(engines)
            power_margins = [
                float(event["available_power_kw"]) - float(event["active_power_kw"])
                for event in events
                if event.get("available_power_kw") is not None and event.get("active_power_kw") is not None
            ]
            if power_margins and "bus_power" not in metrics:
                metrics["bus_power"] = min(power_margins)
            actual_thrust = [
                float(event["thrust_magnitude_n"])
                for event in events
                if event.get("thrust_magnitude_n") is not None
            ]
            available_thrust = [
                float(event["available_thrust_n"])
                for event in events
                if event.get("available_thrust_n") is not None
            ]
            if actual_thrust:
                metrics.update({
                    "thrust_min": min(actual_thrust),
                    "thrust_max": max(actual_thrust),
                    "thrust_mean": sum(actual_thrust) / len(actual_thrust),
                })
            if available_thrust:
                metrics.update({
                    "available_thrust_min": min(available_thrust),
                    "available_thrust_max": max(available_thrust),
                })
            metrics["mission_events"] = events
            metrics["expended_delta_v"] = _expended_delta_v_km_s(metrics, events)
        feasible = not failure_file and (best_feasible_attempt > 0 or first_feasible > 0)
        decision_complete = xdescriptions == () or len(xdescriptions) == len(decision_vector)
        bounds_complete = (
            (not xlowerbounds or len(xlowerbounds) == len(xdescriptions))
            and (not xupperbounds or len(xupperbounds) == len(xdescriptions))
        )
        constraint_complete = fdescriptions == () or len(fdescriptions) == len(constraint_vector)
        complete = (
            objective is not None and bool(lines) and decision_complete
            and bounds_complete and constraint_complete
        )
        reason = None
        if not complete:
            reason = "missing objective or inconsistent decision/constraint-vector output"
        elif failure_file or not feasible:
            reason = "EMTG completed without a feasible trajectory"
        return ParsedEMTGResult(
            complete,
            feasible,
            objective,
            violation,
            {key: value for key, value in metrics.items() if value is not None},
            xdescriptions,
            decision_vector,
            fdescriptions,
            constraint_vector,
            reason,
            xlowerbounds,
            xupperbounds,
        )

    @staticmethod
    def _parse_event(line: str) -> dict[str, Any] | None:
        fields = [field.strip() for field in line.split("|")]
        # Leading/trailing separators produce empty fields.  The documented
        # event table indices below are stable in current EMTG output.
        if fields and fields[0] == "":
            fields = fields[1:]
        if fields and fields[-1] == "":
            fields = fields[:-1]
        if len(fields) < 32:
            return None
        julian_date = _float_csv(fields, 1)
        x, y, z = (_float_csv(fields, index) for index in (12, 13, 14))
        xdot, ydot, zdot = (_float_csv(fields, index) for index in (15, 16, 17))
        control_x, control_y, control_z = (_float_csv(fields, index) for index in (18, 19, 20))
        thrust_x, thrust_y, thrust_z = (_float_csv(fields, index) for index in (21, 22, 23))
        speed = math.sqrt(xdot**2 + ydot**2 + zdot**2) if None not in (xdot, ydot, zdot) else None
        thrust_magnitude = (
            math.sqrt(thrust_x**2 + thrust_y**2 + thrust_z**2)
            if None not in (thrust_x, thrust_y, thrust_z)
            else None
        )
        return {
            "index": int(float(fields[0])),
            "julian_date_mjd": julian_date - 2400000.5 if julian_date is not None else None,
            "event_type": fields[3],
            "location": fields[4],
            "timestep_days": _float_csv(fields, 5),
            "declination": _float_csv(fields, 10),
            "c3": _float_csv(fields, 11),
            "velocity_magnitude": speed,
            "position_km": [x, y, z] if None not in (x, y, z) else None,
            "velocity_km_s": [xdot, ydot, zdot] if None not in (xdot, ydot, zdot) else None,
            "control": (
                [control_x, control_y, control_z]
                if None not in (control_x, control_y, control_z)
                else None
            ),
            "thrust_n": (
                [thrust_x, thrust_y, thrust_z]
                if None not in (thrust_x, thrust_y, thrust_z)
                else None
            ),
            "thrust_magnitude_n": thrust_magnitude,
            "available_thrust_n": _float_csv(fields, 25),
            "isp_s": _float_csv(fields, 26),
            "throttle_fraction": _float_csv(fields, 24),
            "mass": _float_csv(fields, 29),
            "mass_flow_rate_kg_s": _float_csv(fields, 28),
            "active_engines": _float_csv(fields, 30),
            "active_power_kw": _float_csv(fields, 31),
            "available_power_kw": _float_csv(fields, 27),
            "raw_fields": fields,
        }


def _expended_delta_v_km_s(
    metrics: Mapping[str, Any], events: Sequence[Mapping[str, Any]]
) -> float:
    """Return deterministic plus integrated low-thrust delta-v in km/s.

    EMTG event values are printed at finite precision.  Each arc's estimated
    propellant is therefore normalized to EMTG's exact electric-propellant
    total while retaining its local Isp, mass, and throttle weighting.
    """
    duty_cycle = _finite_float(metrics.get("thruster_duty_cycle"))
    duty_cycle = duty_cycle if duty_cycle is not None and duty_cycle > 0.0 else 1.0
    arcs: list[tuple[float, float, float]] = []
    for event in events:
        duration_days = _finite_float(event.get("timestep_days"))
        throttle = _finite_float(event.get("throttle_fraction"))
        mass_flow = _finite_float(event.get("mass_flow_rate_kg_s"))
        isp = _finite_float(event.get("isp_s"))
        mass = _finite_float(event.get("mass"))
        if throttle is None:
            actual = _finite_float(event.get("thrust_magnitude_n"))
            available = _finite_float(event.get("available_thrust_n"))
            if actual is not None and available is not None and available > 0.0:
                throttle = actual / available
        values = (duration_days, throttle, mass_flow, isp, mass)
        if any(value is None or value <= 0.0 for value in values):
            continue
        propellant = mass_flow * duration_days * 86400.0 * throttle * duty_cycle
        if 0.0 < propellant < mass:
            arcs.append((propellant, isp, mass))

    estimated_propellant = sum(value[0] for value in arcs)
    exact_propellant = _finite_float(metrics.get("electric_propellant_used"))
    correction = (
        exact_propellant / estimated_propellant
        if exact_propellant is not None
        and exact_propellant >= 0.0
        and estimated_propellant > 0.0
        else 1.0
    )
    continuous = 0.0
    for estimated_used, isp, mass in arcs:
        used = estimated_used * correction
        if 0.0 < used < mass:
            continuous += isp * 9.80665 / 1000.0 * math.log(mass / (mass - used))
    deterministic = _finite_float(metrics.get("deterministic_delta_v")) or 0.0
    return max(0.0, deterministic) + continuous


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_numeric_csv(values: Sequence[str]) -> tuple[float, ...]:
    output: list[float] = []
    try:
        for value in values:
            output.append(float(value.strip()))
    except ValueError:
        return ()
    return tuple(output)



expended_delta_v_km_s = _expended_delta_v_km_s
