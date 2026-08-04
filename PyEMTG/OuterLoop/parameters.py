"""Model-aware parameter discovery and mutation planning for the Atlas.

This module inspects an anchor and describes mutations, but never applies them.
Case generation and immutable artifact writing are intentionally deferred to a
later layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from .atlas import (
    ATLAS_CONTRACT_SCHEMA,
    ArchitectureSignature,
    AtlasContractError,
    AxisDefinition,
    HardwareArtifactIdentity,
    HardwareBinding,
    HardwareTransformation,
    HardwareVariant,
    MutationStrategy,
    NumericBounds,
    NumericValue,
    ParameterAvailability,
    ParameterRole,
    ParameterScope,
    ParameterTransform,
    ParameterValue,
    ParameterValueType,
    ParameterVector,
    decimal_value,
    integer_value,
)
from .canonical import content_hash
from .hardware import (
    HardwareInspectionError,
    LaunchVehicleLibrary,
    LaunchVehicleRecord,
    PowerSystemLibrary,
    PowerSystemRecord,
    PropulsionSystemLibrary,
    PropulsionSystemRecord,
    SpacecraftInspection,
    ThrottleTableInspection,
    resolve_hardware_reference,
)
from .model import MissionPhenotype


LAUNCH_EPOCH = "launch.epoch_mjd"
LAUNCH_C3_INPUT = "launch.departure_c3_input_km2_s2"
LAUNCH_C3_REALIZED = "launch.departure_c3_realized_km2_s2"
MAXIMUM_MASS = "spacecraft.maximum_mass_kg"
ELECTRIC_PROPELLANT_CAPACITY = "propellant.electric_capacity_kg"
ENGINE_DUTY_CYCLE = "propulsion.engine_duty_cycle"
ENGINE_COUNT = "propulsion.engine_count"
BOL_POWER = "power.beginning_of_life_kw"
CONSTANT_THRUST = "propulsion.constant_thrust_n"
THRUST_SCALE = "propulsion.thrust_scale"


class ParameterRegistryError(ValueError):
    """Base class for parameter discovery and planning failures."""


class UnknownParameterError(ParameterRegistryError):
    pass


class ParameterApplicabilityError(ParameterRegistryError):
    pass


class ParameterValidationError(ParameterRegistryError):
    pass


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value.normalize()) if value != 0 else "0"
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True)
class ParameterDefinition:
    key: str
    label: str
    units: str
    value_type: ParameterValueType
    role: ParameterRole
    scope: ParameterScope
    transform: ParameterTransform
    hard_bounds: NumericBounds
    mutation_strategy: MutationStrategy
    architecture_affecting: bool = False
    family_axis_allowed: bool = True
    default_resolution: Decimal | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if not self.key or not self.label:
            raise AtlasContractError("parameter definition requires key and label")
        if self.default_resolution is not None:
            resolution = decimal_value(self.default_resolution, where=f"{self.key}.default_resolution")
            if resolution <= 0:
                raise AtlasContractError(f"{self.key} default resolution must be positive")
            object.__setattr__(self, "default_resolution", resolution)
        if self.role is ParameterRole.REALIZED and (
            self.family_axis_allowed or self.mutation_strategy is not MutationStrategy.RESULT_OBSERVATION
        ):
            raise AtlasContractError("realized parameters must be read-only and unavailable as axes")
        if self.architecture_affecting and self.family_axis_allowed:
            raise AtlasContractError("architecture-affecting parameters cannot be family axes")

    def normalize(self, value: Any) -> NumericValue:
        normalized: NumericValue
        try:
            if self.value_type is ParameterValueType.INTEGER:
                normalized = integer_value(value, where=self.key)
            else:
                normalized = decimal_value(value, where=self.key)
        except AtlasContractError as error:
            raise ParameterValidationError(str(error)) from error
        if not self.hard_bounds.contains(normalized):
            raise ParameterValidationError(
                f"{self.key}={normalized} is outside hard bounds {self.hard_bounds.to_dict()}"
            )
        return normalized

    def axis(self, lower: Any, upper: Any, resolution: Any) -> AxisDefinition:
        if not self.family_axis_allowed or self.architecture_affecting:
            raise ParameterValidationError(f"{self.key} cannot be a family axis")
        if self.role is not ParameterRole.INPUT or self.value_type is not ParameterValueType.CONTINUOUS:
            raise ParameterValidationError(f"{self.key} is not a continuous input")
        axis = AxisDefinition(self.key, lower, upper, resolution, self.transform)
        if not self.hard_bounds.contains(axis.lower) or not self.hard_bounds.contains(axis.upper):
            raise ParameterValidationError(f"axis for {self.key} exceeds hard bounds")
        return axis

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ATLAS_CONTRACT_SCHEMA,
            "key": self.key,
            "label": self.label,
            "units": self.units,
            "value_type": self.value_type.value,
            "role": self.role.value,
            "scope": self.scope.value,
            "transform": self.transform.value,
            "hard_bounds": self.hard_bounds.to_dict(),
            "mutation_strategy": self.mutation_strategy.value,
            "architecture_affecting": self.architecture_affecting,
            "family_axis_allowed": self.family_axis_allowed,
            "default_resolution": None if self.default_resolution is None else str(self.default_resolution),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ParameterDefinition":
        if data.get("schema_version") != ATLAS_CONTRACT_SCHEMA:
            raise AtlasContractError("parameter definition schema is incompatible")
        return cls(
            str(data["key"]), str(data["label"]), str(data.get("units", "")),
            ParameterValueType(data["value_type"]), ParameterRole(data["role"]),
            ParameterScope(data["scope"]), ParameterTransform(data["transform"]),
            NumericBounds.from_dict(data.get("hard_bounds", {})),
            MutationStrategy(data["mutation_strategy"]),
            bool(data.get("architecture_affecting", False)),
            bool(data.get("family_axis_allowed", True)),
            data.get("default_resolution"), str(data.get("description", "")),
        )


@dataclass(frozen=True)
class ResolvedParameter:
    definition: ParameterDefinition
    availability: ParameterAvailability
    reason_code: str
    reason: str
    current_value: NumericValue | None = None
    suggested_value: NumericValue | None = None
    effective_bounds: NumericBounds | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)
    mutation_strategy: MutationStrategy | None = None
    mutation_target: str | None = None
    hardware_baseline: HardwareArtifactIdentity | None = None
    hardware_transform_targets: tuple[str, ...] = ()
    activation_required: bool = False

    def __post_init__(self) -> None:
        if self.current_value is not None:
            object.__setattr__(self, "current_value", self.definition.normalize(self.current_value))
        if self.suggested_value is not None:
            object.__setattr__(self, "suggested_value", self.definition.normalize(self.suggested_value))
        object.__setattr__(self, "provenance", _freeze(self.provenance))
        object.__setattr__(self, "hardware_transform_targets", tuple(self.hardware_transform_targets))
        if self.availability is ParameterAvailability.AVAILABLE and self.mutation_strategy is None:
            object.__setattr__(self, "mutation_strategy", self.definition.mutation_strategy)

    @property
    def available(self) -> bool:
        return self.availability is ParameterAvailability.AVAILABLE

    def to_dict(self) -> dict[str, Any]:
        def value(item: NumericValue | None) -> Any:
            if item is None or isinstance(item, int):
                return item
            return str(item.normalize()) if item != 0 else "0"
        return {
            "schema_version": ATLAS_CONTRACT_SCHEMA,
            "definition": self.definition.to_dict(),
            "availability": self.availability.value,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "current_value": value(self.current_value),
            "suggested_value": value(self.suggested_value),
            "effective_bounds": None if self.effective_bounds is None else self.effective_bounds.to_dict(),
            "provenance": _plain(self.provenance),
            "mutation_strategy": None if self.mutation_strategy is None else self.mutation_strategy.value,
            "mutation_target": self.mutation_target,
            "hardware_baseline": None if self.hardware_baseline is None else self.hardware_baseline.to_dict(),
            "hardware_transform_targets": list(self.hardware_transform_targets),
            "activation_required": self.activation_required,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResolvedParameter":
        if data.get("schema_version") != ATLAS_CONTRACT_SCHEMA:
            raise AtlasContractError("resolved parameter schema is incompatible")
        definition = ParameterDefinition.from_dict(data["definition"])
        strategy = data.get("mutation_strategy")
        baseline = data.get("hardware_baseline")
        return cls(
            definition,
            ParameterAvailability(data["availability"]),
            str(data["reason_code"]),
            str(data.get("reason", "")),
            data.get("current_value"),
            data.get("suggested_value"),
            None if data.get("effective_bounds") is None
            else NumericBounds.from_dict(data["effective_bounds"]),
            dict(data.get("provenance", {})),
            None if strategy is None else MutationStrategy(strategy),
            data.get("mutation_target"),
            None if baseline is None else HardwareArtifactIdentity.from_dict(baseline),
            tuple(str(value) for value in data.get("hardware_transform_targets", ())),
            bool(data.get("activation_required", False)),
        )


@dataclass(frozen=True)
class ParameterMutation:
    parameter_key: str
    value: NumericValue
    strategy: MutationStrategy
    target: str
    hardware_baseline: HardwareArtifactIdentity | None = None
    hardware_transformations: tuple[HardwareTransformation, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "hardware_transformations",
                           tuple(sorted(self.hardware_transformations,
                                        key=lambda item: (item.target, item.parameter_key))))
        if self.strategy is MutationStrategy.HARDWARE_VARIANT:
            if self.hardware_baseline is None or not self.hardware_transformations:
                raise AtlasContractError("hardware mutation requires baseline and transformations")
        elif self.hardware_baseline is not None or self.hardware_transformations:
            raise AtlasContractError("non-hardware mutation cannot carry hardware transformations")

    def to_dict(self) -> dict[str, Any]:
        return {
            "parameter_key": self.parameter_key,
            "value": ParameterValue(self.parameter_key, self.value).to_dict(),
            "strategy": self.strategy.value,
            "target": self.target,
            "hardware_baseline": None if self.hardware_baseline is None else self.hardware_baseline.to_dict(),
            "hardware_transformations": [item.to_dict() for item in self.hardware_transformations],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ParameterMutation":
        value = ParameterValue.from_dict(data["value"])
        if value.key != str(data["parameter_key"]):
            raise AtlasContractError("mutation value key does not match parameter_key")
        baseline = data.get("hardware_baseline")
        return cls(
            str(data["parameter_key"]), value.value,
            MutationStrategy(data["strategy"]), str(data["target"]),
            None if baseline is None else HardwareArtifactIdentity.from_dict(baseline),
            tuple(
                HardwareTransformation.from_dict(item)
                for item in data.get("hardware_transformations", ())
            ),
        )


@dataclass(frozen=True)
class ParameterApplicationPlan:
    requested: ParameterVector
    mutations: tuple[ParameterMutation, ...]

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.mutations, key=lambda item: item.parameter_key))
        planned_keys = [item.parameter_key for item in ordered]
        requested_keys = [item.key for item in self.requested.values]
        if len(set(planned_keys)) != len(planned_keys):
            raise AtlasContractError("application plan contains duplicate mutations")
        if planned_keys != requested_keys:
            raise AtlasContractError(
                f"application plan is incomplete: requested={requested_keys}, planned={planned_keys}"
            )
        targets = [transformation.target for mutation in ordered
                   for transformation in mutation.hardware_transformations]
        if len(set(targets)) != len(targets):
            raise AtlasContractError("application plan contains conflicting hardware targets")
        object.__setattr__(self, "mutations", ordered)

    def hardware_variants(self) -> tuple[HardwareVariant, ...]:
        groups: dict[tuple[str, str], list[HardwareTransformation]] = {}
        baselines: dict[tuple[str, str], HardwareArtifactIdentity] = {}
        for mutation in self.mutations:
            if mutation.hardware_baseline is None:
                continue
            key = (mutation.hardware_baseline.kind, mutation.hardware_baseline.sha256)
            baselines[key] = mutation.hardware_baseline
            groups.setdefault(key, []).extend(mutation.hardware_transformations)
        return tuple(
            HardwareVariant(key[0], baselines[key], tuple(groups[key]))
            for key in sorted(groups)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ATLAS_CONTRACT_SCHEMA,
            "requested": self.requested.to_dict(),
            "mutations": [item.to_dict() for item in self.mutations],
            "hardware_variants": [item.to_dict() for item in self.hardware_variants()],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ParameterApplicationPlan":
        if data.get("schema_version") != ATLAS_CONTRACT_SCHEMA:
            raise AtlasContractError("parameter application plan schema is incompatible")
        value = cls(
            ParameterVector.from_dict(data["requested"]),
            tuple(ParameterMutation.from_dict(item) for item in data.get("mutations", ())),
        )
        serialized_variants = data.get("hardware_variants")
        if serialized_variants is not None:
            expected = [item.to_dict() for item in value.hardware_variants()]
            if list(serialized_variants) != expected:
                raise AtlasContractError("application plan hardware variants do not match mutations")
        return value


@dataclass(frozen=True)
class AtlasAnchor:
    options: object
    hardware_root: Path
    launch_library: LaunchVehicleLibrary
    launch_vehicle: LaunchVehicleRecord
    spacecraft_model_input: int
    power_library: PowerSystemLibrary | None
    propulsion_library: PropulsionSystemLibrary | None
    spacecraft: SpacecraftInspection | None
    effective_power_systems: tuple[PowerSystemRecord, ...]
    effective_propulsion_systems: tuple[PropulsionSystemRecord, ...]
    throttle_table_hashes: tuple[tuple[str, str | None], ...]
    phenotype: MissionPhenotype | None = None
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "hardware_root", self.hardware_root.resolve())
        object.__setattr__(self, "metrics", _freeze(self.metrics))

    @classmethod
    def from_options(
        cls,
        options: object,
        hardware_root: str | Path | None = None,
        *,
        phenotype: MissionPhenotype | None = None,
        metrics: Mapping[str, Any] | None = None,
    ) -> "AtlasAnchor":
        root = Path(hardware_root or getattr(options, "HardwarePath")).resolve()

        def required_artifact(reference: object, role: str) -> Path:
            resolved = resolve_hardware_reference(root, str(reference))
            if resolved is None:
                raise HardwareInspectionError(
                    f"cannot resolve {role} artifact {str(reference)!r} under {root}"
                )
            return resolved

        try:
            launch_library = LaunchVehicleLibrary.from_file(
                required_artifact(getattr(options, "LaunchVehicleLibraryFile"), "launch vehicle")
            )
            launch_vehicle = launch_library.get(str(getattr(options, "LaunchVehicleKey")))
            mode = integer_value(getattr(options, "SpacecraftModelInput"), where="SpacecraftModelInput")
            if mode not in {0, 1, 2}:
                raise ParameterRegistryError(f"unsupported SpacecraftModelInput {mode}")

            power_library: PowerSystemLibrary | None = None
            propulsion_library: PropulsionSystemLibrary | None = None
            spacecraft: SpacecraftInspection | None = None
            power_systems: tuple[PowerSystemRecord, ...]
            propulsion_systems: tuple[PropulsionSystemRecord, ...]
            if mode == 0:
                power_library = PowerSystemLibrary.from_file(
                    required_artifact(getattr(options, "PowerSystemsLibraryFile"), "power library")
                )
                propulsion_library = PropulsionSystemLibrary.from_file(
                    required_artifact(
                        getattr(options, "PropulsionSystemsLibraryFile"), "propulsion library"
                    )
                )
                power_systems = (power_library.get(str(getattr(options, "PowerSystemKey"))),)
                propulsion_systems = (
                    propulsion_library.get(str(getattr(options, "ElectricPropulsionSystemKey"))),
                )
                # The chemical binding is architecture-bearing even though no
                # Atlas parameter mutates it in this chunk.
                propulsion_library.get(str(getattr(options, "ChemicalPropulsionSystemKey")))
            elif mode == 1:
                spacecraft = SpacecraftInspection.from_file(
                    required_artifact(getattr(options, "SpacecraftOptionsFile"), "spacecraft")
                )
                power_systems = spacecraft.effective_power_systems()
                propulsion_systems = spacecraft.effective_electric_propulsion_systems()
            else:
                power_systems = (_mission_options_power_system(options),)
                propulsion_systems = (_mission_options_propulsion_system(options),)
        except HardwareInspectionError as error:
            raise ParameterRegistryError(str(error)) from error

        throttle_hashes: list[tuple[str, str | None]] = []
        for system in propulsion_systems:
            reference = system.throttle_table_file
            resolved = resolve_hardware_reference(root, reference)
            if system.thruster_mode >= 4 and resolved is None:
                raise ParameterRegistryError(
                    f"throttle-table mode {system.thruster_mode} cannot resolve {reference!r}"
                )
            if resolved is not None:
                try:
                    table = ThrottleTableInspection.from_file(resolved)
                    table.validate_for_mode(system.thruster_mode)
                except HardwareInspectionError as error:
                    raise ParameterRegistryError(str(error)) from error
                throttle_hashes.append((reference, table.sha256))
            else:
                throttle_hashes.append((reference, None))
        return cls(
            options, root, launch_library, launch_vehicle, mode,
            power_library, propulsion_library, spacecraft,
            power_systems, propulsion_systems, tuple(throttle_hashes),
            phenotype, dict(metrics or {}),
        )

    @property
    def architecture(self) -> ArchitectureSignature:
        bindings = [HardwareBinding(
            "launch_vehicle", self.launch_library.sha256,
            (self.launch_vehicle.name,),
            {"model_type": self.launch_vehicle.model_type},
        )]
        throttle_details = [
            {"reference": reference, "sha256": digest}
            for reference, digest in self.throttle_table_hashes
        ]
        if self.spacecraft_model_input == 0:
            assert self.power_library is not None and self.propulsion_library is not None
            bindings.extend((
                HardwareBinding(
                    "power_library", self.power_library.sha256,
                    (str(getattr(self.options, "PowerSystemKey")),),
                ),
                HardwareBinding(
                    "propulsion_library", self.propulsion_library.sha256,
                    (str(getattr(self.options, "ElectricPropulsionSystemKey")),
                     str(getattr(self.options, "ChemicalPropulsionSystemKey"))),
                    {"throttle_tables": throttle_details},
                ),
            ))
            engine_count = integer_value(
                getattr(self.options, "number_of_electric_propulsion_systems"),
                where="number_of_electric_propulsion_systems",
            )
        elif self.spacecraft_model_input == 1:
            assert self.spacecraft is not None
            stage_graph = [
                {
                    "name": stage.name,
                    "power_system": stage.power_system_key,
                    "electric_propulsion_system": stage.electric_propulsion_key,
                    "chemical_propulsion_system": stage.chemical_propulsion_key,
                    "engine_count": stage.effective_electric_propulsion.number_of_strings,
                    "thruster_mode": stage.effective_electric_propulsion.thruster_mode,
                }
                for stage in self.spacecraft.stages
            ]
            bindings.append(HardwareBinding(
                "spacecraft_file", self.spacecraft.sha256,
                tuple(stage.name for stage in self.spacecraft.stages),
                {"stage_graph": stage_graph, "throttle_tables": throttle_details},
            ))
            engine_count = sum(system.number_of_strings for system in self.effective_propulsion_systems)
        else:
            discrete = {
                "engine_type": int(getattr(self.options, "engine_type")),
                "power_source_type": int(getattr(self.options, "power_source_type")),
                "solar_power_model_type": int(getattr(self.options, "solar_power_model_type")),
                "spacecraft_power_model_type": int(getattr(self.options, "spacecraft_power_model_type")),
                "throttle_tables": throttle_details,
            }
            bindings.append(HardwareBinding(
                "mission_options_spacecraft",
                content_hash(discrete, prefix="emtg-mission-options-hardware-v1"),
                (), discrete,
            ))
            engine_count = integer_value(
                getattr(self.options, "number_of_electric_propulsion_systems"),
                where="number_of_electric_propulsion_systems",
            )
        return ArchitectureSignature(
            _architecture_topology(self), self.spacecraft_model_input, tuple(bindings),
            tuple(system.thruster_mode for system in self.effective_propulsion_systems),
            engine_count,
        )


def _mission_options_power_system(options: object) -> PowerSystemRecord:
    gamma = tuple(decimal_value(value, where="solar_power_gamma")
                  for value in getattr(options, "solar_power_gamma"))
    bus = tuple(decimal_value(value, where="spacecraft_power_coefficients")
                for value in getattr(options, "spacecraft_power_coefficients"))
    return PowerSystemRecord(
        "PowerFromMissionOptions", int(getattr(options, "power_source_type")),
        int(getattr(options, "solar_power_model_type")),
        int(getattr(options, "spacecraft_power_model_type")),
        decimal_value(getattr(options, "power_at_1_AU"), where="power_at_1_AU"),
        Decimal(0), decimal_value(getattr(options, "power_decay_rate"), where="power_decay_rate"),
        gamma, bus,
    )


def _effective_mode_for_engine_type(engine_type: int) -> int:
    mapping = {0: 0, 3: 1, 5: 3, 30: 4, 31: 5}
    unsupported = {1, 2, 4, 29, 32}
    if engine_type in unsupported:
        raise ParameterRegistryError(f"engine_type {engine_type} is not executable in current EMTG")
    # The current factory maps included legacy built-in engines to Poly1D.
    return mapping.get(engine_type, 3)


def _mission_options_propulsion_system(options: object) -> PropulsionSystemRecord:
    engine_type = int(getattr(options, "engine_type"))
    mode = _effective_mode_for_engine_type(engine_type)
    power_bounds = tuple(getattr(options, "engine_input_power_bounds"))
    if len(power_bounds) != 2:
        raise ParameterRegistryError("engine_input_power_bounds must have two values")
    return PropulsionSystemRecord(
        "ElectricPropulsionSystemFromMissionOptions", mode,
        str(getattr(options, "ThrottleTableFile")), Decimal(0),
        int(getattr(options, "number_of_electric_propulsion_systems")),
        decimal_value(power_bounds[0], where="engine power lower"),
        decimal_value(power_bounds[1], where="engine power upper"),
        decimal_value(getattr(options, "Thrust"), where="Thrust"),
        decimal_value(getattr(options, "IspLT"), where="IspLT"),
        decimal_value(getattr(options, "IspLT_minimum"), where="IspLT_minimum"),
        decimal_value(getattr(options, "user_defined_engine_efficiency"), where="engine efficiency"),
        Decimal(1),
        decimal_value(getattr(options, "thrust_scale_factor"), where="thrust_scale_factor"),
        tuple(decimal_value(value, where="engine thrust coefficient")
              for value in getattr(options, "engine_input_thrust_coefficients")),
        tuple(decimal_value(value, where="engine mass flow coefficient")
              for value in getattr(options, "engine_input_mass_flow_rate_coefficients")),
    )


def _architecture_topology(anchor: AtlasAnchor) -> Mapping[str, Any]:
    if anchor.phenotype is not None:
        journeys = []
        for journey in anchor.phenotype.journeys:
            phases = []
            for phase in journey.phases:
                phases.append({
                    "target": phase.target,
                    "phase_type": phase.values.get("phase_type", journey.values.get("phase_type")),
                    "dsm_count": phase.values.get(
                        "dsm_count", phase.values.get("impulses_per_phase",
                                                      journey.values.get("dsm_count"))
                    ),
                })
            journeys.append({
                "departure": journey.departure,
                "arrival": journey.arrival,
                "flybys": list(journey.flybys),
                "phases": phases,
                "boundary_types": [journey.values.get(name) for name in (
                    "departure_class", "departure_type", "arrival_class", "arrival_type"
                )],
                "constraint_structure": sorted(str(value) for value in
                                               journey.values.get("constraint_structure", ())),
            })
        resonance = {
            key: value for key, value in anchor.phenotype.resonance.items()
            if key in {"selected", "architecture"}
        }
        return {"journeys": journeys, "resonance": resonance}

    journeys = []
    for journey in getattr(anchor.options, "Journeys", ()):
        constraints = []
        for attribute in (
            "ManeuverConstraintDefinitions", "BoundaryConstraintDefinitions",
            "PhaseDistanceConstraintDefinitions",
        ):
            constraints.extend(str(value) for value in getattr(journey, attribute, ()))
        journeys.append({
            "destination_list": list(getattr(journey, "destination_list", ())),
            "sequence": list(getattr(journey, "sequence", ())),
            "phase_type": int(getattr(journey, "phase_type")),
            "dsm_count": int(getattr(journey, "impulses_per_phase", 0)),
            "boundary_types": [int(getattr(journey, name)) for name in (
                "departure_class", "departure_type", "arrival_class", "arrival_type"
            )],
            "constraint_structure": sorted(constraints),
        })
    return {"journeys": journeys, "resonance": {}}


def _definition(
    key: str, label: str, units: str, value_type: ParameterValueType,
    role: ParameterRole, scope: ParameterScope, bounds: NumericBounds,
    strategy: MutationStrategy, *, architecture_affecting: bool = False,
    family_axis_allowed: bool = True, description: str = "",
) -> ParameterDefinition:
    return ParameterDefinition(
        key, label, units, value_type, role, scope, ParameterTransform.LINEAR,
        bounds, strategy, architecture_affecting, family_axis_allowed, None, description,
    )


BUILTIN_PARAMETERS: tuple[ParameterDefinition, ...] = (
    _definition(LAUNCH_EPOCH, "Launch epoch", "MJD", ParameterValueType.CONTINUOUS,
                ParameterRole.INPUT, ParameterScope.FIRST_JOURNEY, NumericBounds(Decimal(0), None),
                MutationStrategy.FIRST_DEPARTURE_EPOCH),
    _definition(LAUNCH_C3_INPUT, "Requested departure C3", "km^2/s^2",
                ParameterValueType.CONTINUOUS, ParameterRole.INPUT, ParameterScope.LAUNCH_VEHICLE,
                NumericBounds(Decimal(0), None), MutationStrategy.FIRST_DEPARTURE_C3),
    _definition(LAUNCH_C3_REALIZED, "Realized departure C3", "km^2/s^2",
                ParameterValueType.CONTINUOUS, ParameterRole.REALIZED, ParameterScope.RESULT,
                NumericBounds(Decimal(0), None), MutationStrategy.RESULT_OBSERVATION,
                family_axis_allowed=False),
    _definition(MAXIMUM_MASS, "Maximum spacecraft mass", "kg", ParameterValueType.CONTINUOUS,
                ParameterRole.INPUT, ParameterScope.MISSION,
                NumericBounds(Decimal("1e-10"), None), MutationStrategy.MISSION_OPTION),
    _definition(ELECTRIC_PROPELLANT_CAPACITY, "Electric propellant capacity", "kg",
                ParameterValueType.CONTINUOUS, ParameterRole.INPUT, ParameterScope.SPACECRAFT,
                NumericBounds(Decimal(0), None), MutationStrategy.MISSION_OPTION),
    _definition(ENGINE_DUTY_CYCLE, "Engine duty cycle", "fraction",
                ParameterValueType.CONTINUOUS, ParameterRole.INPUT, ParameterScope.MISSION,
                NumericBounds(Decimal("1e-10"), Decimal(1)), MutationStrategy.MISSION_OPTION),
    _definition(ENGINE_COUNT, "Electric engine count", "count", ParameterValueType.INTEGER,
                ParameterRole.INPUT, ParameterScope.ELECTRIC_PROPULSION,
                NumericBounds(Decimal(1), None), MutationStrategy.MISSION_OPTION,
                architecture_affecting=True, family_axis_allowed=False),
    _definition(BOL_POWER, "Beginning-of-life power", "kW", ParameterValueType.CONTINUOUS,
                ParameterRole.INPUT, ParameterScope.POWER_SYSTEM,
                NumericBounds(Decimal("1e-10"), None), MutationStrategy.MISSION_OPTION),
    _definition(CONSTANT_THRUST, "Constant electric thrust", "N",
                ParameterValueType.CONTINUOUS, ParameterRole.INPUT,
                ParameterScope.ELECTRIC_PROPULSION, NumericBounds(Decimal("1e-10"), None),
                MutationStrategy.MISSION_OPTION,
                description="Baseline constant electric thrust before duty-cycle scaling."),
    _definition(THRUST_SCALE, "Electric thrust scale", "dimensionless",
                ParameterValueType.CONTINUOUS, ParameterRole.INPUT,
                ParameterScope.ELECTRIC_PROPULSION,
                NumericBounds(Decimal("1e-10"), Decimal(1)), MutationStrategy.MISSION_OPTION,
                description="Scales thrust and thrust derivatives only; mass flow is unchanged."),
)


class ParameterRegistry:
    def __init__(self, definitions: Iterable[ParameterDefinition] = BUILTIN_PARAMETERS):
        values = tuple(definitions)
        self._definitions = {item.key: item for item in values}
        if len(self._definitions) != len(values):
            raise ParameterRegistryError("parameter registry contains duplicate keys")

    def definitions(self) -> tuple[ParameterDefinition, ...]:
        return tuple(self._definitions[key] for key in sorted(self._definitions))

    def get(self, key: str) -> ParameterDefinition:
        try:
            return self._definitions[key]
        except KeyError as error:
            raise UnknownParameterError(f"unknown atlas parameter {key!r}") from error

    def discover(self, anchor: AtlasAnchor) -> tuple[ResolvedParameter, ...]:
        resolved = {
            LAUNCH_EPOCH: self._launch_epoch(anchor),
            LAUNCH_C3_INPUT: self._launch_c3_input(anchor),
            LAUNCH_C3_REALIZED: self._launch_c3_realized(anchor),
            MAXIMUM_MASS: self._maximum_mass(anchor),
        }
        electric = _uses_electric_propulsion(anchor)
        resolved[ELECTRIC_PROPELLANT_CAPACITY] = self._electric_capacity(anchor, electric)
        resolved[ENGINE_DUTY_CYCLE] = self._duty_cycle(anchor, electric)
        resolved[ENGINE_COUNT] = self._engine_count(anchor, electric)
        resolved[BOL_POWER] = self._bol_power(anchor)
        resolved.update(self._thrust_controls(anchor, electric))
        return tuple(resolved[key] for key in sorted(resolved))

    def discover_mapping(self, anchor: AtlasAnchor) -> Mapping[str, ResolvedParameter]:
        return MappingProxyType({item.definition.key: item for item in self.discover(anchor)})

    def validate_axis(self, resolved: ResolvedParameter, axis: AxisDefinition) -> None:
        definition = resolved.definition
        if axis.parameter_key != definition.key:
            raise ParameterValidationError("axis key does not match resolved parameter")
        if not resolved.available:
            raise ParameterApplicabilityError(f"{definition.key} is unavailable: {resolved.reason_code}")
        if not definition.family_axis_allowed or definition.architecture_affecting:
            raise ParameterValidationError(f"{definition.key} cannot be a family axis")
        if definition.role is not ParameterRole.INPUT or definition.value_type is not ParameterValueType.CONTINUOUS:
            raise ParameterValidationError(f"{definition.key} is not a continuous input")
        if axis.transform is not definition.transform:
            raise ParameterValidationError(f"{definition.key} does not support {axis.transform.value}")
        bounds = definition.hard_bounds.intersect(resolved.effective_bounds)
        if not bounds.contains(axis.lower) or not bounds.contains(axis.upper):
            raise ParameterValidationError(f"axis for {definition.key} exceeds effective bounds")

    def plan_mutations(
        self,
        anchor: AtlasAnchor,
        requested: ParameterVector | Mapping[str, Any],
        *,
        axes: Sequence[AxisDefinition] = (),
    ) -> ParameterApplicationPlan:
        discovered = self.discover_mapping(anchor)
        if isinstance(requested, Mapping):
            integer_keys = {
                key for key in requested
                if self.get(str(key)).value_type is ParameterValueType.INTEGER
            }
            try:
                vector = ParameterVector.from_mapping(requested, integer_keys)
            except AtlasContractError as error:
                raise ParameterValidationError(str(error)) from error
        else:
            vector = requested
        axis_map = {axis.parameter_key: axis for axis in axes}
        if len(axis_map) != len(axes):
            raise ParameterValidationError("mutation plan contains duplicate axes")
        extra_axes = set(axis_map) - {item.key for item in vector.values}
        if extra_axes:
            raise ParameterValidationError(
                f"mutation plan axes have no requested value: {sorted(extra_axes)}"
            )

        mutations: list[ParameterMutation] = []
        canonical_values: list[ParameterValue] = []
        for item in vector.values:
            definition = self.get(item.key)
            resolved = discovered[item.key]
            if definition.role is not ParameterRole.INPUT:
                raise ParameterApplicabilityError(f"{item.key} is read-only")
            if not resolved.available:
                raise ParameterApplicabilityError(
                    f"{item.key} is unavailable ({resolved.reason_code}): {resolved.reason}"
                )
            value = definition.normalize(item.value)
            bounds = definition.hard_bounds.intersect(resolved.effective_bounds)
            if not bounds.contains(value):
                raise ParameterValidationError(
                    f"{item.key}={value} is outside effective bounds {bounds.to_dict()}"
                )
            axis = axis_map.get(item.key)
            if axis is not None:
                self.validate_axis(resolved, axis)
                decimal = decimal_value(value)
                if decimal < axis.lower or decimal > axis.upper:
                    raise ParameterValidationError(f"{item.key} is outside its axis bounds")
                if (decimal - axis.lower) % axis.resolution != 0:
                    raise ParameterValidationError(f"{item.key} is off the configured axis grid")
            strategy = resolved.mutation_strategy or definition.mutation_strategy
            transformations: tuple[HardwareTransformation, ...] = ()
            if strategy is MutationStrategy.HARDWARE_VARIANT:
                values = []
                for target in resolved.hardware_transform_targets:
                    transformation_value: NumericValue = value
                    if target.endswith(":enable"):
                        transformation_value = 1
                    values.append(HardwareTransformation(item.key, target, transformation_value))
                transformations = tuple(values)
            mutations.append(ParameterMutation(
                item.key, value, strategy, resolved.mutation_target or item.key,
                resolved.hardware_baseline, transformations,
            ))
            canonical_values.append(ParameterValue(item.key, value))
        return ParameterApplicationPlan(ParameterVector(tuple(canonical_values)), tuple(mutations))

    def _unavailable(self, key: str, code: str, reason: str) -> ResolvedParameter:
        return ResolvedParameter(self.get(key), ParameterAvailability.UNAVAILABLE, code, reason)

    def _launch_epoch(self, anchor: AtlasAnchor) -> ResolvedParameter:
        journeys = getattr(anchor.options, "Journeys", ())
        if not journeys:
            return self._unavailable(LAUNCH_EPOCH, "missing_first_journey", "the anchor has no journeys")
        first = journeys[0]
        departure_class = int(getattr(first, "departure_class", -1))
        departure_type = int(getattr(first, "departure_type", -1))
        # Flyby-like departures cannot be the independently variable first
        # boundary event that this singular control promises to set.
        if departure_class not in {0, 1, 2, 3} or departure_type not in {0, 1, 2, 5}:
            return self._unavailable(
                LAUNCH_EPOCH, "unsupported_first_departure_epoch",
                "the first boundary event does not expose a supported independent epoch",
            )
        events = anchor.metrics.get("mission_events", ())
        epoch = events[0].get("julian_date_mjd") if events and isinstance(events[0], Mapping) else None
        if epoch is None:
            return ResolvedParameter(
                self.get(LAUNCH_EPOCH), ParameterAvailability.UNOBSERVED,
                "departure_epoch_unobserved", "the anchor has no parsed first-event epoch",
            )
        return ResolvedParameter(
            self.get(LAUNCH_EPOCH), ParameterAvailability.AVAILABLE, "available",
            "first departure epoch can be fixed exactly", decimal_value(epoch, where=LAUNCH_EPOCH),
            effective_bounds=NumericBounds(Decimal(0), None),
            provenance={
                "source": "result.mission_events[0].julian_date_mjd",
                "departure_class": departure_class,
                "departure_type": departure_type,
                "reconcile_departure_date_bounds": bool(
                    getattr(first, "bounded_departure_date", False)
                ),
            },
            mutation_target=(
                "MissionOptions.launch_window_open_date+"
                "MissionOptions.Journeys[0].wait_time_bounds=[0,0]+"
                "MissionOptions.Journeys[0].departure_date_bounds=reconcile_if_active"
            ),
        )

    def _launch_c3_input(self, anchor: AtlasAnchor) -> ResolvedParameter:
        journeys = getattr(anchor.options, "Journeys", ())
        if not journeys:
            return self._unavailable(LAUNCH_C3_INPUT, "missing_first_journey", "the anchor has no journeys")
        first = journeys[0]
        if int(getattr(first, "departure_type", -1)) != 0 or int(getattr(first, "departure_class", -1)) not in {0, 3}:
            return self._unavailable(
                LAUNCH_C3_INPUT, "departure_not_launch_vehicle_backed",
                "the first departure is not an ephemeris-pegged or periapse launch/direct insertion",
            )
        bounds = tuple(getattr(first, "initial_impulse_bounds", ()))
        if len(bounds) != 2:
            return self._unavailable(
                LAUNCH_C3_INPUT, "invalid_initial_impulse_bounds",
                "initial_impulse_bounds must contain exactly two velocities",
            )
        lower_speed = decimal_value(bounds[0], where="initial_impulse_bounds[0]")
        upper_speed = decimal_value(bounds[1], where="initial_impulse_bounds[1]")
        if lower_speed < 0 or lower_speed > upper_speed:
            return self._unavailable(
                LAUNCH_C3_INPUT, "invalid_initial_impulse_bounds",
                "initial_impulse_bounds must be nonnegative and ordered",
            )
        current: Decimal | None = None
        if lower_speed == upper_speed:
            current = lower_speed * lower_speed
        effective_bounds = NumericBounds(
            anchor.launch_vehicle.c3_lower_km2_s2,
            anchor.launch_vehicle.c3_upper_km2_s2,
        )
        if current is not None and not effective_bounds.contains(current):
            return self._unavailable(
                LAUNCH_C3_INPUT, "c3_input_outside_launch_vehicle_bounds",
                "the fixed initial impulse is outside the selected launch-vehicle C3 interval",
            )
        realized = _realized_c3(anchor)
        suggestion = (
            realized if current is None and realized is not None
            and effective_bounds.contains(realized) else None
        )
        return ResolvedParameter(
            self.get(LAUNCH_C3_INPUT), ParameterAvailability.AVAILABLE, "available",
            "departure C3 is constrained by the selected launch-vehicle interval",
            current, suggestion, effective_bounds,
            {
                "source": "journey[0].initial_impulse_bounds",
                "launch_vehicle": anchor.launch_vehicle.name,
                "suggestion_source": "realized_c3" if suggestion is not None else None,
            },
            mutation_target="MissionOptions.Journeys[0].initial_impulse_bounds=[sqrt(C3),sqrt(C3)]",
        )

    def _launch_c3_realized(self, anchor: AtlasAnchor) -> ResolvedParameter:
        value = _realized_c3(anchor)
        if value is None:
            return ResolvedParameter(
                self.get(LAUNCH_C3_REALIZED), ParameterAvailability.UNOBSERVED,
                "departure_c3_unobserved", "the first mission event has no parsed C3",
            )
        return ResolvedParameter(
            self.get(LAUNCH_C3_REALIZED), ParameterAvailability.AVAILABLE, "available",
            "realized C3 was parsed from the first mission event", value,
            effective_bounds=NumericBounds(Decimal(0), None),
            provenance={"source": "result.mission_events[0].c3"},
            mutation_strategy=MutationStrategy.RESULT_OBSERVATION,
        )

    def _maximum_mass(self, anchor: AtlasAnchor) -> ResolvedParameter:
        return ResolvedParameter(
            self.get(MAXIMUM_MASS), ParameterAvailability.AVAILABLE, "available",
            "mission maximum mass is propagated to every journey",
            decimal_value(getattr(anchor.options, "maximum_mass"), where=MAXIMUM_MASS),
            effective_bounds=self.get(MAXIMUM_MASS).hard_bounds,
            provenance={"source": "MissionOptions.maximum_mass"},
            mutation_target="MissionOptions.maximum_mass",
        )

    def _electric_capacity(self, anchor: AtlasAnchor, electric: bool) -> ResolvedParameter:
        if not electric:
            return self._unavailable(
                ELECTRIC_PROPELLANT_CAPACITY, "electric_propulsion_unused",
                "the anchor has no electric-propulsion phase or spiral",
            )
        if anchor.spacecraft_model_input == 1:
            assert anchor.spacecraft is not None
            baseline = HardwareArtifactIdentity("spacecraft_file", anchor.spacecraft.sha256)
            return ResolvedParameter(
                self.get(ELECTRIC_PROPELLANT_CAPACITY), ParameterAvailability.AVAILABLE, "available",
                "global electric capacity is represented by an immutable spacecraft variant",
                anchor.spacecraft.global_electric_capacity_kg,
                effective_bounds=self.get(ELECTRIC_PROPELLANT_CAPACITY).hard_bounds,
                provenance={"source": "SpacecraftOptions.GlobalElectricPropellantTankCapacity"},
                mutation_strategy=MutationStrategy.HARDWARE_VARIANT,
                mutation_target="spacecraft.global_electric_propellant_capacity_kg",
                hardware_baseline=baseline,
                hardware_transform_targets=(
                    "spacecraft:global_electric_propellant_capacity_kg",
                    "spacecraft:global_electric_propellant_constraint:enable",
                ),
                activation_required=not anchor.spacecraft.global_electric_constraint_enabled,
            )
        enabled = bool(getattr(anchor.options, "enable_electric_propellant_tank_constraint"))
        return ResolvedParameter(
            self.get(ELECTRIC_PROPELLANT_CAPACITY), ParameterAvailability.AVAILABLE, "available",
            "electric capacity is applied with the global tank constraint enabled",
            decimal_value(getattr(anchor.options, "maximum_electric_propellant"),
                          where=ELECTRIC_PROPELLANT_CAPACITY),
            effective_bounds=self.get(ELECTRIC_PROPELLANT_CAPACITY).hard_bounds,
            provenance={"source": "MissionOptions.maximum_electric_propellant"},
            mutation_target="MissionOptions.maximum_electric_propellant+enable_constraint",
            activation_required=not enabled,
        )

    def _duty_cycle(self, anchor: AtlasAnchor, electric: bool) -> ResolvedParameter:
        if not electric:
            return self._unavailable(ENGINE_DUTY_CYCLE, "electric_propulsion_unused",
                                     "the anchor does not use electric propulsion")
        overrides = [
            index for index, journey in enumerate(getattr(anchor.options, "Journeys", ()))
            if _journey_uses_electric_propulsion(journey)
            and bool(getattr(journey, "override_duty_cycle", False))
        ]
        if overrides:
            return self._unavailable(
                ENGINE_DUTY_CYCLE, "journey_duty_cycle_override",
                f"journeys {overrides} override the global duty cycle",
            )
        return ResolvedParameter(
            self.get(ENGINE_DUTY_CYCLE), ParameterAvailability.AVAILABLE, "available",
            "all relevant journeys inherit the global duty cycle",
            decimal_value(getattr(anchor.options, "engine_duty_cycle"), where=ENGINE_DUTY_CYCLE),
            effective_bounds=self.get(ENGINE_DUTY_CYCLE).hard_bounds,
            provenance={"source": "MissionOptions.engine_duty_cycle"},
            mutation_target="MissionOptions.engine_duty_cycle",
        )

    def _engine_count(self, anchor: AtlasAnchor, electric: bool) -> ResolvedParameter:
        if not electric:
            return self._unavailable(ENGINE_COUNT, "electric_propulsion_unused",
                                     "the anchor does not use electric propulsion")
        if anchor.spacecraft_model_input == 1:
            systems = _unique_propulsion_systems(anchor.effective_propulsion_systems)
            if len(systems) != 1:
                return self._unavailable(
                    ENGINE_COUNT, "ambiguous_multistage_electric_propulsion",
                    "multiple distinct electric-propulsion systems are active",
                )
            system = systems[0]
            assert anchor.spacecraft is not None
            key = system.name
            return ResolvedParameter(
                self.get(ENGINE_COUNT), ParameterAvailability.AVAILABLE, "available",
                "one unambiguous embedded electric-propulsion record is active",
                system.number_of_strings,
                effective_bounds=self.get(ENGINE_COUNT).hard_bounds,
                provenance={"source": f"SpacecraftOptions.propulsion[{key}].NumberOfStrings"},
                mutation_strategy=MutationStrategy.HARDWARE_VARIANT,
                mutation_target=f"spacecraft.propulsion_system[{key}].number_of_strings",
                hardware_baseline=HardwareArtifactIdentity("spacecraft_file", anchor.spacecraft.sha256),
                hardware_transform_targets=(f"spacecraft:propulsion_system:{key}:number_of_strings",),
            )
        return ResolvedParameter(
            self.get(ENGINE_COUNT), ParameterAvailability.AVAILABLE, "available",
            "engine count is fixed at the architecture-stratum level",
            integer_value(getattr(anchor.options, "number_of_electric_propulsion_systems"),
                          where=ENGINE_COUNT),
            effective_bounds=self.get(ENGINE_COUNT).hard_bounds,
            provenance={"source": "MissionOptions.number_of_electric_propulsion_systems"},
            mutation_target="MissionOptions.number_of_electric_propulsion_systems",
        )

    def _bol_power(self, anchor: AtlasAnchor) -> ResolvedParameter:
        systems = _unique_power_systems(anchor.effective_power_systems)
        if len(systems) != 1:
            return self._unavailable(
                BOL_POWER, "ambiguous_multistage_power_system",
                "multiple distinct power systems are active",
            )
        system = systems[0]
        if anchor.spacecraft_model_input == 2:
            return ResolvedParameter(
                self.get(BOL_POWER), ParameterAvailability.AVAILABLE, "available",
                "BOL power comes from MissionOptions",
                system.p0_kw, effective_bounds=self.get(BOL_POWER).hard_bounds,
                provenance={"source": "MissionOptions.power_at_1_AU"},
                mutation_target="MissionOptions.power_at_1_AU",
            )
        if anchor.spacecraft_model_input == 0:
            assert anchor.power_library is not None
            baseline = HardwareArtifactIdentity("power_library", anchor.power_library.sha256)
            target = f"power_library:power_system:{system.name}:p0_kw"
        else:
            assert anchor.spacecraft is not None
            baseline = HardwareArtifactIdentity("spacecraft_file", anchor.spacecraft.sha256)
            target = f"spacecraft:power_system:{system.name}:p0_kw"
        return ResolvedParameter(
            self.get(BOL_POWER), ParameterAvailability.AVAILABLE, "available",
            "BOL power requires an immutable hardware variant",
            system.p0_kw, effective_bounds=self.get(BOL_POWER).hard_bounds,
            provenance={"source": target}, mutation_strategy=MutationStrategy.HARDWARE_VARIANT,
            mutation_target=target, hardware_baseline=baseline,
            hardware_transform_targets=(target,),
        )

    def _thrust_controls(
        self, anchor: AtlasAnchor, electric: bool,
    ) -> dict[str, ResolvedParameter]:
        if not electric:
            return {
                CONSTANT_THRUST: self._unavailable(CONSTANT_THRUST, "electric_propulsion_unused",
                                                   "the anchor does not use electric propulsion"),
                THRUST_SCALE: self._unavailable(THRUST_SCALE, "electric_propulsion_unused",
                                               "the anchor does not use electric propulsion"),
            }
        systems = _unique_propulsion_systems(anchor.effective_propulsion_systems)
        if len(systems) != 1:
            reason = "multiple distinct electric-propulsion systems are active"
            return {
                CONSTANT_THRUST: self._unavailable(
                    CONSTANT_THRUST, "ambiguous_multistage_electric_propulsion", reason),
                THRUST_SCALE: self._unavailable(
                    THRUST_SCALE, "ambiguous_multistage_electric_propulsion", reason),
            }
        system = systems[0]
        if system.thruster_mode == 0:
            available_key, unavailable_key = CONSTANT_THRUST, THRUST_SCALE
            current = system.constant_thrust_n
            field_name = "constant_thrust_n"
            unavailable_reason = "constant-thrust models expose constant thrust instead of thrust scale"
        else:
            available_key, unavailable_key = THRUST_SCALE, CONSTANT_THRUST
            current = system.thrust_scale
            field_name = "thrust_scale"
            unavailable_reason = "power-dependent models expose thrust scale instead of constant thrust"
        output = {
            unavailable_key: self._unavailable(
                unavailable_key, "inapplicable_thruster_mode", unavailable_reason
            )
        }
        if anchor.spacecraft_model_input == 2:
            mission_field = "Thrust" if available_key == CONSTANT_THRUST else "thrust_scale_factor"
            output[available_key] = ResolvedParameter(
                self.get(available_key), ParameterAvailability.AVAILABLE, "available",
                "the active MissionOptions thruster mode supports this control",
                current, effective_bounds=self.get(available_key).hard_bounds,
                provenance={"source": f"MissionOptions.{mission_field}",
                            "thruster_mode": system.thruster_mode},
                mutation_target=f"MissionOptions.{mission_field}",
            )
            return output
        if anchor.spacecraft_model_input == 0:
            assert anchor.propulsion_library is not None
            baseline = HardwareArtifactIdentity("propulsion_library", anchor.propulsion_library.sha256)
            target = f"propulsion_library:propulsion_system:{system.name}:{field_name}"
        else:
            assert anchor.spacecraft is not None
            baseline = HardwareArtifactIdentity("spacecraft_file", anchor.spacecraft.sha256)
            target = f"spacecraft:propulsion_system:{system.name}:{field_name}"
        output[available_key] = ResolvedParameter(
            self.get(available_key), ParameterAvailability.AVAILABLE, "available",
            "the active hardware thruster mode supports this immutable variant control",
            current, effective_bounds=self.get(available_key).hard_bounds,
            provenance={"source": target, "thruster_mode": system.thruster_mode},
            mutation_strategy=MutationStrategy.HARDWARE_VARIANT,
            mutation_target=target, hardware_baseline=baseline,
            hardware_transform_targets=(target,),
        )
        return output


def _realized_c3(anchor: AtlasAnchor) -> Decimal | None:
    events = anchor.metrics.get("mission_events", ())
    value = events[0].get("c3") if events and isinstance(events[0], Mapping) else None
    return None if value is None else decimal_value(value, where=LAUNCH_C3_REALIZED)


def _unique_power_systems(values: Sequence[PowerSystemRecord]) -> tuple[PowerSystemRecord, ...]:
    output: dict[tuple[object, ...], PowerSystemRecord] = {}
    for value in values:
        output.setdefault(value.identity(), value)
    return tuple(output.values())


def _unique_propulsion_systems(
    values: Sequence[PropulsionSystemRecord],
) -> tuple[PropulsionSystemRecord, ...]:
    output: dict[tuple[object, ...], PropulsionSystemRecord] = {}
    for value in values:
        output.setdefault(value.identity(), value)
    return tuple(output.values())


def _uses_electric_propulsion(anchor: AtlasAnchor) -> bool:
    low_thrust_phase_types = {0, 1, 2, 3, 4, 5, 11}
    if anchor.phenotype is not None:
        for journey in anchor.phenotype.journeys:
            phase_types = [phase.values.get("phase_type", journey.values.get("phase_type"))
                           for phase in journey.phases]
            if not phase_types:
                phase_types = [journey.values.get("phase_type")]
            if any(value is not None and int(value) in low_thrust_phase_types for value in phase_types):
                return True
            if int(journey.values.get("departure_type", -1)) == 5 or int(
                journey.values.get("arrival_type", -1)
            ) == 6:
                return True
        return False
    return any(
        _journey_uses_electric_propulsion(journey)
        for journey in getattr(anchor.options, "Journeys", ())
    )


def _journey_uses_electric_propulsion(journey: object) -> bool:
    return (
        int(getattr(journey, "phase_type", -1)) in {0, 1, 2, 3, 4, 5, 11}
        or int(getattr(journey, "departure_type", -1)) == 5
        or int(getattr(journey, "arrival_type", -1)) == 6
    )


def default_parameter_registry() -> ParameterRegistry:
    return ParameterRegistry()
