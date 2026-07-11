"""Synthetic evaluator and safe EMTG case/evaluation boundary."""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
import importlib
import json
import math
import os
import platform
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Any, Mapping, Protocol, Sequence

from .canonical import content_hash, file_sha256
from .ephemeris import EphemerisCoverage
from .hardware import HardwareCatalog
from .model import ArtifactRef, EvaluationRequest, EvaluationResult, EvaluationStatus, MissionPhenotype
from .process import ProcessOutcome, run_process
from .rules import UniverseCatalog
from .storage import ArtifactStore


EXTRACTION_VERSION = "emtg-result-v3"


class Evaluator(Protocol):
    def context_identity(self) -> Mapping[str, Any]: ...

    def evaluate(
        self, request: EvaluationRequest, cancel_event: threading.Event | None = None
    ) -> EvaluationResult: ...


class CaseGenerationError(ValueError):
    pass


class SyntheticEvaluator:
    """Deterministic finite-architecture and ZDT evaluator for qualification."""

    def __init__(self, settings: Mapping[str, Any] | None = None):
        self.settings = dict(settings or {})
        self.problem = str(self.settings.get("problem", "architecture"))

    def context_identity(self) -> Mapping[str, Any]:
        return {"type": "synthetic", "problem": self.problem, "settings": self.settings, "version": 3}

    def evaluate(
        self, request: EvaluationRequest, cancel_event: threading.Event | None = None
    ) -> EvaluationResult:
        start = time.monotonic()
        phenotype = request.candidate.phenotype
        if cancel_event is not None and cancel_event.is_set():
            return _result(request, EvaluationStatus.CANCELLED, runtime=time.monotonic() - start)
        if self.problem in {"zdt1", "zdt2", "zdt3"}:
            values = [float(value) for key, value in sorted(phenotype.mission.items()) if key.startswith("x")]
            if not values:
                return _result(request, EvaluationStatus.OUTPUT_INCOMPLETE, reason="ZDT phenotype has no x genes")
            first = values[0]
            g_value = 1.0 + 9.0 * sum(values[1:]) / max(1, len(values) - 1)
            ratio = first / g_value
            if self.problem == "zdt1":
                second = g_value * (1.0 - math.sqrt(ratio))
            elif self.problem == "zdt2":
                second = g_value * (1.0 - ratio**2)
            else:
                second = g_value * (1.0 - math.sqrt(ratio) - ratio * math.sin(10.0 * math.pi * first))
            metrics = {"f1": first, "f2": second, "emtg_objective": first}
        else:
            body_scores = {str(key): float(value) for key, value in self.settings.get("body_scores", {}).items()}
            hardware_scores = {str(key): float(value) for key, value in self.settings.get("hardware_scores", {}).items()}
            visits = [body for journey in phenotype.journeys for body in journey.sequence[1:]]
            sequence_cost = sum(body_scores.get(body, 1.0) for body in visits)
            sequence_cost += float(self.settings.get("flyby_penalty", 0.25)) * sum(len(j.flybys) for j in phenotype.journeys)
            hardware = phenotype.mission.get("spacecraft_configuration")
            sequence_cost += hardware_scores.get(str(hardware), 0.0)
            base_mass = float(self.settings.get("base_mass", 1000.0))
            metrics = {
                "emtg_objective": sequence_cost,
                "flight_time": float(phenotype.mission.get("flight_time", sequence_cost * 100.0)),
                "delivered_mass": base_mass - sequence_cost * float(self.settings.get("mass_cost_scale", 10.0)),
                "number_of_journeys": len(phenotype.journeys),
                "number_of_flybys": sum(len(journey.flybys) for journey in phenotype.journeys),
                "point_group_value": sum(
                    float(value.get("score", 0.0)) for value in phenotype.point_group.values()
                ),
            }
        failure_threshold = self.settings.get("infeasible_above_cost")
        if failure_threshold is not None and float(metrics["emtg_objective"]) > float(failure_threshold):
            violation = float(metrics["emtg_objective"]) - float(failure_threshold)
            return _result(
                request,
                EvaluationStatus.EMTG_INFEASIBLE,
                metrics=metrics,
                violation=violation,
                runtime=time.monotonic() - start,
            )
        return _result(
            request,
            EvaluationStatus.FEASIBLE,
            metrics=metrics,
            runtime=time.monotonic() - start,
        )


class RepeatedEvaluator:
    """Aggregate deterministic repeated inner-loop trials for one phenotype."""

    def __init__(self, base: Evaluator, trials: int):
        if trials < 1:
            raise ValueError("repeated evaluator requires at least one trial")
        self.base = base
        self.trials = trials

    def context_identity(self) -> Mapping[str, Any]:
        return {
            "type": "repeated",
            "trials": self.trials,
            "base": self.base.context_identity(),
            "aggregation_version": 3,
        }

    def evaluate(
        self, request: EvaluationRequest, cancel_event: threading.Event | None = None
    ) -> EvaluationResult:
        from .randomness import derive_seed

        results: list[EvaluationResult] = []
        configured_seeds = tuple(request.context.get("inner_seed_set", ()))
        if configured_seeds and len(configured_seeds) not in {1, self.trials}:
            raise ValueError("fixed inner_seed_set length must equal evaluator.inner_trials")
        for trial in range(self.trials):
            trial_seed = (
                int(configured_seeds[trial])
                if len(configured_seeds) == self.trials
                else derive_seed(request.evaluation_seed, "repeat", trial, bits=31)
            )
            trial_request = replace(
                request,
                evaluation_seed=trial_seed,
                context={**request.context, "repeat_index": trial, "repeat_count": self.trials},
            )
            results.append(self.base.evaluate(trial_request, cancel_event))
            if cancel_event is not None and cancel_event.is_set():
                break
        feasible = [result for result in results if result.feasible]
        if feasible:
            best = min(
                feasible,
                key=lambda result: (
                    float(result.metrics.get("emtg_objective", math.inf)),
                    result.evaluation_key,
                ),
            )
        else:
            best = min(
                results,
                key=lambda result: (
                    math.inf if result.solver_violation is None else result.solver_violation,
                    result.status.value,
                    result.evaluation_key,
                ),
            )
        metrics = dict(best.metrics)
        metrics["convergence_probability"] = len(feasible) / self.trials
        provenance = dict(best.provenance)
        provenance["repeat_trials"] = [
            {
                "evaluation_key": result.evaluation_key,
                "status": result.status.value,
                "violation": result.solver_violation,
                "runtime_seconds": result.runtime_seconds,
                "artifacts": dict(result.artifacts),
            }
            for result in results
        ]
        return replace(
            best,
            evaluation_key=request.evaluation_key,
            candidate_id=request.candidate.candidate_id,
            fidelity=request.fidelity,
            metrics=metrics,
            runtime_seconds=sum(result.runtime_seconds for result in results),
            provenance=provenance,
        )


class AlternativeSeedEvaluator:
    """Evaluate registered compatible seeds and retain the best attempt."""

    def __init__(self, base: Evaluator):
        self.base = base

    def context_identity(self) -> Mapping[str, Any]:
        return {"type": "alternative_seeds", "selection_version": 3, "base": self.base.context_identity()}

    def evaluate(
        self, request: EvaluationRequest, cancel_event: threading.Event | None = None
    ) -> EvaluationResult:
        initial = request.initial_guess or {}
        alternatives = tuple(initial.get("alternatives", ())) if isinstance(initial, Mapping) else ()
        if not alternatives:
            return self.base.evaluate(request, cancel_event)
        results = []
        for index, alternative in enumerate(alternatives):
            attempt = replace(
                request,
                initial_guess=dict(alternative),
                context={**request.context, "seed_attempt": index},
            )
            results.append(self.base.evaluate(attempt, cancel_event))
            if cancel_event is not None and cancel_event.is_set():
                break
        feasible = [result for result in results if result.feasible]
        if feasible:
            best = min(feasible, key=lambda result: (float(result.metrics.get("emtg_objective", math.inf)), result.evaluation_key))
        else:
            best = min(results, key=lambda result: (math.inf if result.solver_violation is None else result.solver_violation, result.status.value, result.evaluation_key))
        provenance = dict(best.provenance)
        provenance["seed_attempts"] = [
            {
                "seed_id": alternatives[index].get("seed_id"),
                "status": result.status.value,
                "violation": result.solver_violation,
                "runtime_seconds": result.runtime_seconds,
                "artifacts": dict(result.artifacts),
            }
            for index, result in enumerate(results)
        ]
        return replace(
            best,
            evaluation_key=request.evaluation_key,
            candidate_id=request.candidate.candidate_id,
            fidelity=request.fidelity,
            runtime_seconds=sum(result.runtime_seconds for result in results),
            provenance=provenance,
        )


def _result(
    request: EvaluationRequest,
    status: EvaluationStatus,
    *,
    metrics: Mapping[str, Any] | None = None,
    violation: float | None = None,
    reason: str | None = None,
    runtime: float = 0.0,
    artifacts: Mapping[str, str] | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> EvaluationResult:
    return EvaluationResult(
        evaluation_key=request.evaluation_key,
        candidate_id=request.candidate.candidate_id,
        status=status,
        fidelity=request.fidelity,
        solver_violation=violation,
        metrics=dict(metrics or {}),
        failure_reason=reason,
        runtime_seconds=runtime,
        artifacts=dict(artifacts or {}),
        provenance=dict(provenance or {}),
    )


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
        metrics: dict[str, Any] = {}
        journey_times: list[float] = []
        journey_mass_increments: list[float] = []
        xdescriptions: tuple[str, ...] = ()
        decision_vector: tuple[float, ...] = ()
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
            elif stripped.startswith("Was first NLP solve feasible"):
                value = _float_after_colon(stripped)
                first_feasible = int(value or 0)
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
        if journey_times:
            metrics["flight_time"] = sum(journey_times)
        if journey_mass_increments:
            metrics["final_journey_mass_increment"] = journey_mass_increments[-1]
        propellant_parts = [metrics.get(name) for name in ("electric_propellant", "chemical_fuel", "chemical_oxidizer")]
        available_propellant = [float(value) for value in propellant_parts if value is not None]
        if available_propellant:
            metrics["total_propellant"] = sum(available_propellant)
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
            metrics["mission_events"] = events
        feasible = not failure_file and (best_feasible_attempt > 0 or first_feasible > 0)
        decision_complete = xdescriptions == () or len(xdescriptions) == len(decision_vector)
        constraint_complete = fdescriptions == () or len(fdescriptions) == len(constraint_vector)
        complete = objective is not None and bool(lines) and decision_complete and constraint_complete
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
        xdot, ydot, zdot = (_float_csv(fields, index) for index in (15, 16, 17))
        speed = math.sqrt(xdot**2 + ydot**2 + zdot**2) if None not in (xdot, ydot, zdot) else None
        return {
            "index": int(float(fields[0])),
            "julian_date_mjd": julian_date - 2400000.5 if julian_date is not None else None,
            "event_type": fields[3],
            "location": fields[4],
            "declination": _float_csv(fields, 10),
            "c3": _float_csv(fields, 11),
            "velocity_magnitude": speed,
            "mass": _float_csv(fields, 29),
            "active_engines": _float_csv(fields, 30),
            "active_power_kw": _float_csv(fields, 31),
            "available_power_kw": _float_csv(fields, 27),
            "raw_fields": fields,
        }


def _parse_numeric_csv(values: Sequence[str]) -> tuple[float, ...]:
    output: list[float] = []
    try:
        for value in values:
            output.append(float(value.strip()))
    except ValueError:
        return ()
    return tuple(output)


MISSION_GENE_ADAPTERS = {
    "launch_epoch": "launch_window_open_date",
    "total_flight_time_bounds": "total_flight_time_bounds",
    "flight_time_bounds": "total_flight_time_bounds",
    "launch_vehicle": "LaunchVehicleKey",
    "spacecraft_configuration": "SpacecraftOptionsFile",
    "power_system": "PowerSystemKey",
    "electric_propulsion_system": "ElectricPropulsionSystemKey",
    "chemical_propulsion_system": "ChemicalPropulsionSystemKey",
    "number_of_electric_propulsion_systems": "number_of_electric_propulsion_systems",
    "chemical_fuel_capacity": "maximum_chemical_fuel",
    "chemical_oxidizer_capacity": "maximum_chemical_oxidizer",
    "electric_propellant_capacity": "maximum_electric_propellant",
    "beginning_of_life_power": "power_at_1_AU",
    "stop_after_journey": "stop_after_journey",
    "power_at_1_AU": "power_at_1_AU",
    "engine_duty_cycle": "engine_duty_cycle",
    "maximum_chemical_fuel": "maximum_chemical_fuel",
    "maximum_chemical_oxidizer": "maximum_chemical_oxidizer",
    "maximum_electric_propellant": "maximum_electric_propellant",
}

JOURNEY_GENE_ADAPTERS = {
    "phase_type": "phase_type",
    "dsm_count": "impulses_per_phase",
    "impulses_per_phase": "impulses_per_phase",
    "departure_type": "departure_type",
    "arrival_type": "arrival_type",
    "duty_cycle": "duty_cycle",
    "journey_time_bounds": "flight_time_bounds",
    "flight_time_bounds": "flight_time_bounds",
    "wait_time_bounds": "wait_time_bounds",
    "central_body": "journey_central_body",
    "periapse_burn_enabled": "enable_periapse_burns",
    "departure_class": "departure_class",
    "arrival_class": "arrival_class",
    "timebounded": "timebounded",
    "departure_date_bounds": "departure_date_bounds",
    "initial_impulse_bounds": "initial_impulse_bounds",
    "final_velocity": "final_velocity",
}


def _mission_options_module():
    try:
        return importlib.import_module("MissionOptions")
    except ImportError:
        return importlib.import_module("PyEMTG.MissionOptions")


class EMTGCaseBuilder:
    def __init__(
        self,
        base_case: str | Path,
        *,
        universe_folder: str | Path | None = None,
        hardware_path: str | Path | None = None,
        journey_template_index: int = 0,
        expand_phase_genes: bool = True,
        ephemeris_source_override: int | None = None,
        journey_templates: Mapping[str, int] | None = None,
        constraint_migration_allowlist: Sequence[str] = (),
    ):
        self.base_case = Path(base_case).resolve()
        module = _mission_options_module()
        self._base = module.MissionOptions(str(self.base_case))
        if not getattr(self._base, "success", 1):
            raise CaseGenerationError(f"could not parse base case {self.base_case}")
        self.universe_folder = Path(universe_folder or self._base.universe_folder).resolve()
        self.hardware_path = Path(hardware_path or self._base.HardwarePath).resolve()
        self.journey_template_index = journey_template_index
        self.expand_phase_genes = expand_phase_genes
        self.ephemeris_source_override = ephemeris_source_override
        self.journey_templates = {str(name): int(index) for name, index in (journey_templates or {}).items()}
        self.constraint_migration_allowlist = tuple(str(value) for value in constraint_migration_allowlist)
        if not 0 <= journey_template_index < len(self._base.Journeys):
            raise CaseGenerationError("journey_template_index is outside the base case")
        for name, index in self.journey_templates.items():
            if not name or not 0 <= index < len(self._base.Journeys):
                raise CaseGenerationError(f"named journey template {name!r} has invalid base index")
        self._catalogs: dict[str, UniverseCatalog] = {}
        self.hardware_catalog = HardwareCatalog.from_options(self.hardware_path, self._base)

    def catalog(self, central_body: str) -> UniverseCatalog:
        if central_body not in self._catalogs:
            self._catalogs[central_body] = UniverseCatalog.from_file(
                self.universe_folder / f"{central_body}.emtg_universe"
            )
        return self._catalogs[central_body]

    def build(
        self,
        phenotype: MissionPhenotype,
        case_directory: str | Path,
        case_name: str,
        *,
        evaluation_seed: int,
        budget: Mapping[str, Any],
        initial_guess: Mapping[str, Any] | None,
    ) -> Path:
        case_directory = Path(case_directory).resolve()
        case_directory.mkdir(parents=True, exist_ok=True)
        options = copy.deepcopy(self._base)
        if self.ephemeris_source_override is not None:
            if self.ephemeris_source_override not in {0, 1, 2}:
                raise CaseGenerationError("ephemeris_source_override must be 0, 1, or 2")
            options.ephemeris_source = self.ephemeris_source_override
        options.mission_name = case_name
        options.override_working_directory = 1
        options.forced_working_directory = str(case_directory).replace("\\", "/")
        options.override_mission_subfolder = 1
        options.forced_mission_subfolder = "."
        options.short_output_file_names = 1
        options.background_mode = 1
        options.call_system_to_generate_bsp = 0
        options.universe_folder = str(self.universe_folder).replace("\\", "/") + "/"
        options.HardwarePath = str(self.hardware_path).replace("\\", "/") + "/"
        options.seed_MBH = int(evaluation_seed % (2**31 - 1))
        self._apply_budget(options, budget)
        self._apply_mission_genes(options, phenotype.mission)
        options.Journeys = self._build_journeys(options, phenotype)
        options.number_of_journeys = len(options.Journeys)
        if not options.Journeys:
            raise CaseGenerationError("decoded mission has no journeys")
        self._apply_boundary_genes(options, phenotype)
        self._apply_initial_guess(options, initial_guess)
        options.AssembleMasterConstraintVectors()
        output = case_directory / f"{case_name}.emtgopt"
        options.write_options_file(str(output), True)
        return output

    @staticmethod
    def _apply_budget(options: Any, budget: Mapping[str, Any]) -> None:
        mode = str(budget.get("inner_loop", "mbh")).lower()
        if mode not in {"mbh", "nlp", "trialx"}:
            raise CaseGenerationError(f"unsupported inner_loop mode {mode}")
        options.run_inner_loop = {"trialx": 0, "mbh": 1, "nlp": 3}[mode]
        for key, attribute in {
            "mbh_max_run_time": "MBH_max_run_time",
            "mbh_max_trials": "MBH_max_trials",
            "mbh_max_not_improve": "MBH_max_not_improve",
            "nlp_max_run_time": "snopt_max_run_time",
            "nlp_major_iterations": "snopt_major_iterations",
            "feasibility_tolerance": "snopt_feasibility_tolerance",
            "optimality_tolerance": "snopt_optimality_tolerance",
            "nlp_solver_type": "NLP_solver_type",
        }.items():
            if key in budget:
                setattr(options, attribute, budget[key])
        options.quiet_NLP = int(budget.get("quiet_nlp", 1))

    @staticmethod
    def _apply_mission_genes(options: Any, genes: Mapping[str, Any]) -> None:
        ignored = {"number_of_journeys", "number_of_flybys", "launch_window"}
        for name, value in genes.items():
            if name in ignored:
                continue
            if name == "flight_time":
                duration = float(value)
                if duration < 0:
                    raise CaseGenerationError("flight_time must be nonnegative")
                options.global_timebounded = 1
                options.total_flight_time_bounds = [duration, duration]
            elif name == "duty_cycle":
                options.engine_duty_cycle = float(value)
            elif name == "spacecraft_configuration":
                if isinstance(value, int):
                    options.SpacecraftModelInput = value
                else:
                    options.SpacecraftModelInput = 1
                    options.SpacecraftOptionsFile = str(value)
            elif name in MISSION_GENE_ADAPTERS:
                setattr(options, MISSION_GENE_ADAPTERS[name], _list_if_tuple(value))
            elif name in {"departure_c3", "final_arrival_c3", "arrival_declination_bounds", "entry_interface_velocity"}:
                continue
            else:
                raise CaseGenerationError(f"no EMTG mission-gene adapter is registered for {name}")
        if "electric_propellant_capacity" in genes:
            options.enable_electric_propellant_tank_constraint = 1
        if "chemical_fuel_capacity" in genes or "chemical_oxidizer_capacity" in genes:
            options.enable_chemical_propellant_tank_constraint = 1

    def _build_journeys(self, options: Any, phenotype: MissionPhenotype) -> list[Any]:
        output: list[Any] = []
        default_template = self._base.Journeys[self.journey_template_index]
        for journey_index, phenotype_journey in enumerate(phenotype.journeys):
            template_value = phenotype_journey.values.get(
                "journey_template", phenotype_journey.values.get("template_index", self.journey_template_index)
            )
            if isinstance(template_value, str) and not template_value.lstrip("-+").isdigit():
                if template_value not in self.journey_templates:
                    raise CaseGenerationError(f"unknown named journey template {template_value!r}")
                template_index = self.journey_templates[template_value]
            else:
                template_index = int(template_value)
            if not 0 <= template_index < len(self._base.Journeys):
                raise CaseGenerationError(f"journey {journey_index} template index is invalid")
            template = self._base.Journeys[template_index] if self._base.Journeys else default_template
            central_body = str(phenotype_journey.values.get("central_body", template.journey_central_body))
            catalog = self.catalog(central_body)
            phase_types = [phase.values.get("phase_type", phenotype_journey.values.get("phase_type", template.phase_type)) for phase in phenotype_journey.phases]
            phase_dsms = [phase.values.get("dsm_count", phase.values.get("impulses_per_phase", phenotype_journey.values.get("dsm_count", template.impulses_per_phase))) for phase in phenotype_journey.phases]
            requires_expansion = len(set(phase_types)) > 1 or len(set(phase_dsms)) > 1
            if requires_expansion and not self.expand_phase_genes:
                raise CaseGenerationError("per-phase genes require single-phase-journey expansion")
            if requires_expansion:
                sequence = phenotype_journey.sequence
                for phase_index, target in enumerate(sequence[1:]):
                    journey = copy.deepcopy(template)
                    self._apply_common_journey(journey, phenotype_journey.values)
                    journey.journey_name = f"outer_j{journey_index}_phase{phase_index}"
                    journey.journey_central_body = central_body
                    journey.universe_folder = options.universe_folder
                    journey.destination_list = [catalog.body_index(sequence[phase_index]), catalog.body_index(target)]
                    journey.sequence = []
                    journey.phase_type = int(phase_types[phase_index])
                    journey.impulses_per_phase = int(phase_dsms[phase_index])
                    if phase_index > 0:
                        journey.departure_class = 0
                        journey.departure_type = 3
                    if phase_index < len(sequence) - 2:
                        journey.arrival_class = 0
                        journey.arrival_type = 2
                        journey.timebounded = 0
                        journey.journey_end_TCM = 0.0
                        journey.journey_end_deltav = 0.0
                    journey.trialX = []
                    self._migrate_constraints(journey)
                    output.append(journey)
            else:
                journey = copy.deepcopy(template)
                self._apply_common_journey(journey, phenotype_journey.values)
                journey.journey_name = f"outer_j{journey_index}"
                journey.journey_central_body = central_body
                journey.universe_folder = options.universe_folder
                journey.destination_list = [catalog.body_index(phenotype_journey.departure), catalog.body_index(phenotype_journey.arrival)]
                journey.sequence = [catalog.flyby_index(body) for body in phenotype_journey.flybys]
                journey.phase_type = int(phase_types[0])
                journey.impulses_per_phase = int(phase_dsms[0])
                journey.trialX = []
                self._migrate_constraints(journey)
                output.append(journey)
        return output

    def _migrate_constraints(self, journey: Any) -> None:
        allow = self.constraint_migration_allowlist
        for attribute in (
            "ManeuverConstraintDefinitions", "BoundaryConstraintDefinitions",
            "PhaseDistanceConstraintDefinitions",
        ):
            current = list(getattr(journey, attribute, ()))
            setattr(journey, attribute, [value for value in current if any(token in value for token in allow)])

    @staticmethod
    def _apply_common_journey(journey: Any, genes: Mapping[str, Any]) -> None:
        ignored = {"departure_destination", "arrival_destination", "template_index", "journey_template", "resonance_choices"}
        for name, value in genes.items():
            if name in ignored:
                continue
            if name in JOURNEY_GENE_ADAPTERS:
                setattr(journey, JOURNEY_GENE_ADAPTERS[name], _list_if_tuple(value))
            elif name in {"arrival_c3", "arrival_declination_bounds", "entry_interface_velocity"}:
                continue
            else:
                raise CaseGenerationError(f"no EMTG journey-gene adapter is registered for {name}")

    @staticmethod
    def _apply_boundary_genes(options: Any, phenotype: MissionPhenotype) -> None:
        first = options.Journeys[0]
        last = options.Journeys[-1]
        launch_window = phenotype.mission.get("launch_window")
        if launch_window is not None:
            first.departure_date_bounds = list(_bounds(launch_window))
            first.timebounded = 2 if int(first.timebounded) == 0 else 3
        departure_c3 = phenotype.mission.get("departure_c3")
        if departure_c3 is not None:
            speed = math.sqrt(max(0.0, float(departure_c3)))
            first.initial_impulse_bounds = [speed, speed]
        arrival_c3 = phenotype.mission.get("final_arrival_c3")
        if arrival_c3 is not None:
            speed = math.sqrt(max(0.0, float(arrival_c3)))
            last.final_velocity = [speed, speed, 0.0]
        entry_velocity = phenotype.mission.get("entry_interface_velocity")
        if entry_velocity is not None:
            bounds = _bounds(entry_velocity)
            if int(last.phase_type) == 10:
                last.probe_AEI_velocity = [bounds[0], bounds[1], 0.0]
            else:
                last.final_velocity = [bounds[0], bounds[1], 0.0]
        declination = phenotype.mission.get("arrival_declination_bounds")
        if declination is not None:
            lower, upper = _bounds(declination)
            phase_index = len(last.sequence)
            constraint = f"p{phase_index}_arrival_VelocityDeclination_{lower}deg_{upper}deg_ICRF"
            last.BoundaryConstraintDefinitions = [
                value for value in last.BoundaryConstraintDefinitions if "VelocityDeclination" not in value
            ] + [constraint]

    @staticmethod
    def _apply_initial_guess(options: Any, initial_guess: Mapping[str, Any] | None) -> None:
        for journey in options.Journeys:
            journey.trialX = []
        options.trialX = []
        if not initial_guess:
            return
        descriptions = tuple(initial_guess.get("xdescriptions", ()))
        vector = tuple(initial_guess.get("decision_vector", ()))
        if not descriptions or len(descriptions) != len(vector):
            raise CaseGenerationError("initial guess descriptions/vector are missing or inconsistent")
        if len(set(map(str, descriptions))) != len(descriptions):
            raise CaseGenerationError("initial guess contains duplicate Xdescriptions")
        if any(not math.isfinite(float(value)) for value in vector):
            raise CaseGenerationError("initial guess contains non-finite values")
        for description in map(str, descriptions):
            match = re.match(r"^j(\d+)", description)
            if match and int(match.group(1)) >= len(options.Journeys):
                raise CaseGenerationError(
                    f"initial guess references missing journey j{match.group(1)}"
                )
        options.trialX = [[str(description), float(value)] for description, value in zip(descriptions, vector)]
        options.DisassembleMasterDecisionVector()
        options.AssembleMasterDecisionVector()
        round_trip = tuple(str(entry[0]) for entry in options.trialX)
        if round_trip != tuple(map(str, descriptions)):
            raise CaseGenerationError("initial guess Xdescriptions do not match generated journey structure")


def _bounds(value: Any) -> tuple[float, float]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        lower, upper = map(float, value)
    else:
        lower = upper = float(value)
    if lower > upper:
        raise CaseGenerationError("bounds are reversed")
    return lower, upper


def _list_if_tuple(value: Any) -> Any:
    return list(value) if isinstance(value, tuple) else value


def _directory_manifest(root: Path, extensions: set[str]) -> Mapping[str, str]:
    if not root.is_dir():
        return {"$missing": str(root)}
    return {
        path.relative_to(root).as_posix(): file_sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix.lower() in extensions
    }


class EMTGEvaluator:
    def __init__(
        self,
        *,
        base_case: str | Path,
        executable: str | Path,
        run_directory: str | Path,
        timeout_seconds: float,
        universe_folder: str | Path | None = None,
        hardware_path: str | Path | None = None,
        environment: Mapping[str, str] | None = None,
        journey_template_index: int = 0,
        expand_phase_genes: bool = True,
        brief_executable: str | Path | None = None,
        ephemeris_source_override: int | None = None,
        resource_limits: Mapping[str, int] | None = None,
        journey_templates: Mapping[str, int] | None = None,
        constraint_migration_allowlist: Sequence[str] = (),
    ):
        self.executable = Path(executable).resolve()
        self.run_directory = Path(run_directory).resolve()
        self.timeout_seconds = float(timeout_seconds)
        if not self.executable.is_file():
            raise FileNotFoundError(self.executable)
        self.builder = EMTGCaseBuilder(
            base_case,
            universe_folder=universe_folder,
            hardware_path=hardware_path,
            journey_template_index=journey_template_index,
            expand_phase_genes=expand_phase_genes,
            ephemeris_source_override=ephemeris_source_override,
            journey_templates=journey_templates,
            constraint_migration_allowlist=constraint_migration_allowlist,
        )
        self.environment = dict(environment or {})
        self.resource_limits = {str(key): int(value) for key, value in (resource_limits or {}).items()}
        self.parser = EMTGResultParser()
        self.artifact_store = ArtifactStore(self.run_directory / "artifacts")
        self._identity: Mapping[str, Any] | None = None
        self.brief_executable = Path(brief_executable).resolve() if brief_executable else None
        self._coverage: EphemerisCoverage | None = None

    def _freeze_artifacts(
        self, artifacts: Mapping[str, str], provenance: Mapping[str, Any]
    ) -> tuple[dict[str, str], dict[str, Any]]:
        frozen = dict(artifacts)
        hashes: dict[str, str] = {}
        references: dict[str, Mapping[str, Any]] = {}
        for role, value in sorted(artifacts.items()):
            path = Path(value)
            if not path.is_file():
                continue
            stored, digest = self.artifact_store.put(path)
            frozen[role] = str(stored)
            hashes[role] = digest
            references[role] = ArtifactRef(
                role, digest, str(stored), stored.stat().st_size
            ).__dict__
        updated = dict(provenance)
        updated["artifact_hashes"] = hashes
        updated["artifact_refs"] = references
        return frozen, updated

    def ephemeris_coverage(self) -> EphemerisCoverage:
        if self._coverage is None:
            self._coverage = EphemerisCoverage.from_directory(
                self.builder.universe_folder / "ephemeris_files",
                brief_executable=self.brief_executable,
            )
        return self._coverage

    def context_identity(self) -> Mapping[str, Any]:
        if self._identity is None:
            executable_directory = self.executable.parent
            runtime_files = {
                path.name: file_sha256(path)
                for path in sorted(executable_directory.iterdir())
                if path.is_file() and (path.suffix.lower() == ".dll" or path.name == "solver_capabilities.json")
            }
            git_head = None
            try:
                completed = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=self.builder.base_case.parents[1] if len(self.builder.base_case.parents) > 1 else self.builder.base_case.parent,
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    check=False,
                    shell=False,
                )
                if completed.returncode == 0:
                    git_head = completed.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                pass
            self._identity = {
                "type": "emtg",
                "executable": str(self.executable),
                "executable_sha256": file_sha256(self.executable),
                "runtime_files": runtime_files,
                "base_case": str(self.builder.base_case),
                "base_case_sha256": file_sha256(self.builder.base_case),
                "universe_manifest": _directory_manifest(self.builder.universe_folder, {".emtg_universe", ".bsp", ".tls", ".tpc", ".tf"}),
                "hardware_manifest": _directory_manifest(self.builder.hardware_path, {".emtg_spacecraftopt", ".emtg_launchvehicleopt", ".emtg_powersystemsopt", ".emtg_propulsionsystemopt", ".throttletable"}),
                "source_commit": git_head,
                "extraction_version": EXTRACTION_VERSION,
                "timeout_seconds": self.timeout_seconds,
                "merged_solver_environment": {
                    **{name: value for name, value in os.environ.items() if name.startswith(("EMTG_", "SNOPT", "IPOPT"))},
                    **self.environment,
                },
                "merged_environment_sha256": content_hash(
                    {
                        **dict(os.environ),
                        "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                        "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1",
                        **self.environment,
                    },
                    prefix="outerloop-process-environment-v3",
                ),
                "template_policy": {
                    "journey_template_index": self.builder.journey_template_index,
                    "journey_templates": self.builder.journey_templates,
                    "constraint_migration_allowlist": self.builder.constraint_migration_allowlist,
                },
                "phase_expansion_policy": self.builder.expand_phase_genes,
                "ephemeris_override": self.builder.ephemeris_source_override,
                "resource_limits": self.resource_limits,
                "platform": {
                    "system": platform.system(),
                    "release": platform.release(),
                    "machine": platform.machine(),
                    "python": platform.python_version(),
                },
            }
        return self._identity

    def evaluate(
        self, request: EvaluationRequest, cancel_event: threading.Event | None = None
    ) -> EvaluationResult:
        case_name = f"ol_{request.candidate.candidate_id[:12]}_{request.evaluation_key[:8]}"
        case_directory = self.run_directory / "cases" / request.fidelity / request.evaluation_key[:2] / request.evaluation_key
        try:
            options_path = self.builder.build(
                request.candidate.phenotype,
                case_directory,
                case_name,
                evaluation_seed=request.evaluation_seed,
                budget=request.budget,
                initial_guess=request.initial_guess,
            )
        except (OSError, PermissionError) as error:
            return _result(
                request,
                EvaluationStatus.INFRASTRUCTURE_FAILED,
                reason=f"case filesystem/infrastructure failure: {error}",
                provenance={"context": self.context_identity()},
            )
        except Exception as error:
            return _result(
                request,
                EvaluationStatus.CONFIGURATION_FAILED,
                reason=f"case generation/configuration failed: {error}",
                provenance={"context": self.context_identity()},
            )
        try:
            outcome = run_process(
                [self.executable, options_path],
                cwd=case_directory,
                timeout_seconds=self.timeout_seconds,
                stdout_path=case_directory / "stdout.log",
                stderr_path=case_directory / "stderr.log",
                environment=self.environment,
                cancel_event=cancel_event,
                cpu_seconds=self.resource_limits.get("cpu_seconds"),
                memory_bytes=self.resource_limits.get(
                    "worker_memory_bytes", self.resource_limits.get("memory_bytes")
                ),
                max_processes=self.resource_limits.get("processes_per_worker"),
            )
        except OSError as error:
            return _result(
                request, EvaluationStatus.INFRASTRUCTURE_FAILED,
                reason=f"process infrastructure failure: {error}",
                provenance={"context": self.context_identity(), "transient": True},
            )
        artifacts = {
            "case_directory": str(case_directory),
            "options": str(options_path),
            "stdout": outcome.stdout_path,
            "stderr": outcome.stderr_path,
        }
        provenance = {
            "process_arguments": list(outcome.arguments),
            "returncode": outcome.returncode,
            "resource_statistics": dict(outcome.resource_statistics),
            "evaluation_seed": request.evaluation_seed,
            "budget": dict(request.budget),
            "context": self.context_identity(),
        }
        if outcome.cancelled:
            artifacts, provenance = self._freeze_artifacts(artifacts, provenance)
            return _result(request, EvaluationStatus.CANCELLED, reason="evaluation cancelled", runtime=outcome.runtime_seconds, artifacts=artifacts, provenance=provenance)
        if outcome.timed_out:
            artifacts, provenance = self._freeze_artifacts(artifacts, provenance)
            return _result(request, EvaluationStatus.TIMED_OUT, reason="EMTG timeout", runtime=outcome.runtime_seconds, artifacts=artifacts, provenance=provenance)
        nominal = case_directory / f"{case_name}.emtg"
        failure = case_directory / f"FAILURE_{case_name}.emtg"
        if nominal.is_file():
            output_path, failure_file = nominal, False
        elif failure.is_file():
            output_path, failure_file = failure, True
        else:
            reason = f"EMTG wrote no expected output; returncode={outcome.returncode}; stderr={outcome.stderr_tail[-1000:]}"
            artifacts, provenance = self._freeze_artifacts(artifacts, provenance)
            return _result(request, EvaluationStatus.EXECUTION_FAILED, reason=reason, runtime=outcome.runtime_seconds, artifacts=artifacts, provenance=provenance)
        artifacts["emtg"] = str(output_path)
        for artifact in sorted(case_directory.iterdir()):
            lower = artifact.name.lower()
            if artifact.is_file() and any(token in lower for token in ("archive", "summary", "missionevents")):
                artifacts[f"supplemental:{artifact.name}"] = str(artifact)
        parsed = self.parser.parse(output_path, failure_file=failure_file)
        metrics = dict(parsed.metrics)
        metrics.update({
            "xdescriptions": parsed.xdescriptions,
            "decision_vector": parsed.decision_vector,
            "constraint_descriptions": parsed.constraint_descriptions,
            "constraint_vector": parsed.constraint_vector,
            "number_of_journeys": len(request.candidate.phenotype.journeys),
            "number_of_flybys": sum(len(journey.flybys) for journey in request.candidate.phenotype.journeys),
        })
        if not parsed.complete:
            status = EvaluationStatus.OUTPUT_INCOMPLETE
        elif parsed.feasible:
            status = EvaluationStatus.FEASIBLE
        else:
            status = EvaluationStatus.EMTG_INFEASIBLE
        artifacts, provenance = self._freeze_artifacts(artifacts, provenance)
        return _result(
            request,
            status,
            metrics=metrics,
            violation=parsed.violation,
            reason=parsed.failure_reason,
            runtime=outcome.runtime_seconds,
            artifacts=artifacts,
            provenance=provenance,
        )
