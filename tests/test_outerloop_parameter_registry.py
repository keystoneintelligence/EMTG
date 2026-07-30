from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import ast
from pathlib import Path
import shutil

import pytest

from MissionOptions import MissionOptions
from OuterLoop.atlas import (
    AxisDefinition,
    MutationStrategy,
    ParameterAvailability,
    ParameterRole,
    ParameterValueType,
)
from OuterLoop.canonical import file_sha256
from OuterLoop.hardware import (
    HardwareInspectionError,
    LaunchVehicleLibrary,
    PowerSystemLibrary,
    PropulsionSystemLibrary,
    SpacecraftInspection,
    ThrottleTableInspection,
)
from OuterLoop.parameters import (
    BOL_POWER,
    BUILTIN_PARAMETERS,
    CONSTANT_THRUST,
    ELECTRIC_PROPELLANT_CAPACITY,
    ENGINE_COUNT,
    ENGINE_DUTY_CYCLE,
    LAUNCH_C3_INPUT,
    LAUNCH_C3_REALIZED,
    LAUNCH_EPOCH,
    MAXIMUM_MASS,
    THRUST_SCALE,
    AtlasAnchor,
    ParameterApplicabilityError,
    ParameterRegistry,
    ParameterRegistryError,
    ParameterValidationError,
    UnknownParameterError,
)


ROOT = Path(__file__).resolve().parents[1]
HARDWARE = ROOT / "testatron" / "HardwareModels"
OPTIONS_FILE = ROOT / "testatron" / "tests" / "transcription_tests" / "FBLT_EMintercept.emtgopt"
METRICS = {
    "mission_events": (
        {"julian_date_mjd": 60000.25, "c3": 5.0},
    ),
}


def options() -> MissionOptions:
    value = MissionOptions(str(OPTIONS_FILE))
    value.HardwarePath = str(HARDWARE)
    return value


def anchor(value: MissionOptions | None = None, **changes: object) -> AtlasAnchor:
    mission = value or options()
    for key, item in changes.items():
        setattr(mission, key, item)
    return AtlasAnchor.from_options(mission, HARDWARE, metrics=METRICS)


def resolved(value: AtlasAnchor) -> dict[str, object]:
    return dict(ParameterRegistry().discover_mapping(value))


def test_registry_definition_contract_and_stable_order():
    registry = ParameterRegistry()
    definitions = registry.definitions()
    expected = {
        LAUNCH_EPOCH,
        LAUNCH_C3_INPUT,
        LAUNCH_C3_REALIZED,
        MAXIMUM_MASS,
        ELECTRIC_PROPELLANT_CAPACITY,
        ENGINE_DUTY_CYCLE,
        ENGINE_COUNT,
        BOL_POWER,
        CONSTANT_THRUST,
        THRUST_SCALE,
    }
    assert {item.key for item in definitions} == expected
    assert [item.key for item in definitions] == sorted(expected)
    assert all(item.default_resolution is None for item in definitions)
    assert all(item.transform.value == "linear" for item in definitions)
    assert all(type(item).from_dict(item.to_dict()) == item for item in definitions)
    assert registry.get(LAUNCH_C3_REALIZED).role is ParameterRole.REALIZED
    assert registry.get(ENGINE_COUNT).value_type is ParameterValueType.INTEGER
    assert registry.get(ENGINE_COUNT).architecture_affecting
    assert not registry.get(ENGINE_COUNT).family_axis_allowed
    assert all(item in BUILTIN_PARAMETERS for item in definitions)


@pytest.mark.parametrize(
    ("engine_type", "available", "unavailable"),
    (
        (0, CONSTANT_THRUST, THRUST_SCALE),
        (3, THRUST_SCALE, CONSTANT_THRUST),
        (5, THRUST_SCALE, CONSTANT_THRUST),
        (30, THRUST_SCALE, CONSTANT_THRUST),
    ),
)
def test_mode2_discovers_effective_constant_fixed_polynomial_and_throttle_models(
    engine_type: int, available: str, unavailable: str,
):
    mission = options()
    mission.SpacecraftModelInput = 2
    mission.engine_type = engine_type
    mission.ThrottleTableFile = "empty.ThrottleTable"
    found = resolved(anchor(mission))
    assert found[available].availability is ParameterAvailability.AVAILABLE
    assert found[unavailable].availability is ParameterAvailability.UNAVAILABLE
    assert found[available].provenance["thruster_mode"] == {0: 0, 3: 1, 5: 3, 30: 4}[engine_type]
    assert found[BOL_POWER].mutation_target == "MissionOptions.power_at_1_AU"


def test_mode0_selected_rows_use_hardware_variants_and_preserve_sources():
    mission = options()
    mission.SpacecraftModelInput = 0
    before = {
        name: file_sha256(HARDWARE / name)
        for name in (
            mission.PowerSystemsLibraryFile,
            mission.PropulsionSystemsLibraryFile,
        )
    }
    model = anchor(mission)
    found = resolved(model)
    assert found[BOL_POWER].mutation_strategy is MutationStrategy.HARDWARE_VARIANT
    assert found[THRUST_SCALE].mutation_strategy is MutationStrategy.HARDWARE_VARIANT
    assert found[CONSTANT_THRUST].availability is ParameterAvailability.UNAVAILABLE

    plan = ParameterRegistry().plan_mutations(model, {
        BOL_POWER: "6.0",
        THRUST_SCALE: "0.75",
    })
    assert [item.parameter_key for item in plan.mutations] == [BOL_POWER, THRUST_SCALE]
    assert len(plan.hardware_variants()) == 2
    assert type(plan).from_dict(plan.to_dict()) == plan
    assert all(
        type(item).from_dict(item.to_dict()) == item
        for item in found.values()
    )
    assert before == {
        name: file_sha256(HARDWARE / name)
        for name in before
    }

    mission.ElectricPropulsionSystemKey = "WarpDrive"
    constant = resolved(anchor(mission))
    assert constant[CONSTANT_THRUST].availability is ParameterAvailability.AVAILABLE
    assert constant[THRUST_SCALE].availability is ParameterAvailability.UNAVAILABLE


@pytest.mark.parametrize("thruster_mode", range(11))
def test_hardware_registry_exposes_model_aware_thrust_control_for_every_mode(
    tmp_path: Path, thruster_mode: int,
):
    mission = options()
    mission.SpacecraftModelInput = 0
    for name in (mission.LaunchVehicleLibraryFile, mission.PowerSystemsLibraryFile):
        shutil.copy2(HARDWARE / name, tmp_path / name)
    table_name = "qualification.ThrottleTable"
    (tmp_path / table_name).write_text(
        "PPU efficiency,1\nPPU min power (kW),0\nPPU max power (kW),10\n"
        "TL1,1,1,1,1,1000,0.5,1\n"
        + "".join(f"2Dpolyrow{index},0,0,0,0,0\n" for index in range(1, 6)),
        encoding="utf-8",
    )
    reference = table_name if thruster_mode >= 4 else "none"
    coefficients = " ".join("0" for _ in range(14))
    library_name = "qualification.emtg_propulsionsystemopt"
    (tmp_path / library_name).write_text(
        "# qualification library\n"
        f"QualificationElectric {thruster_mode} {reference} 0 1 0 10 0.25 2000 1000 0.7 1 1 {coefficients}\n"
        f"QualificationChemical 0 none 0 1 0 1 22 320 220 0.5 0.925 1 {coefficients}\n",
        encoding="utf-8",
    )
    mission.HardwarePath = str(tmp_path)
    mission.PropulsionSystemsLibraryFile = library_name
    mission.ElectricPropulsionSystemKey = "QualificationElectric"
    mission.ChemicalPropulsionSystemKey = "QualificationChemical"
    model = AtlasAnchor.from_options(mission, tmp_path, metrics=METRICS)
    found = resolved(model)
    available = CONSTANT_THRUST if thruster_mode == 0 else THRUST_SCALE
    unavailable = THRUST_SCALE if thruster_mode == 0 else CONSTANT_THRUST
    assert found[available].availability is ParameterAvailability.AVAILABLE
    assert found[unavailable].availability is ParameterAvailability.UNAVAILABLE
    assert found[available].provenance["thruster_mode"] == thruster_mode
    target = Decimal("0.251") if thruster_mode == 0 else Decimal("0.999")
    plan = ParameterRegistry().plan_mutations(model, {available: target})
    assert len(plan.hardware_variants()) == 1
    assert plan.hardware_variants()[0].transformations[0].parameter_key == available


def test_mode1_single_stage_and_heterogeneous_multistage_applicability():
    mission = options()
    mission.SpacecraftModelInput = 1
    mission.SpacecraftOptionsFile = "default.emtg_spacecraftopt"
    single_anchor = anchor(mission)
    single = resolved(single_anchor)
    assert single[BOL_POWER].availability is ParameterAvailability.AVAILABLE
    assert single[ENGINE_COUNT].availability is ParameterAvailability.AVAILABLE
    assert single[THRUST_SCALE].availability is ParameterAvailability.AVAILABLE
    assert all(
        single[key].mutation_strategy is MutationStrategy.HARDWARE_VARIANT
        for key in (BOL_POWER, ENGINE_COUNT, THRUST_SCALE)
    )

    before = file_sha256(HARDWARE / mission.SpacecraftOptionsFile)
    plan = ParameterRegistry().plan_mutations(single_anchor, {
        ELECTRIC_PROPELLANT_CAPACITY: 800,
        BOL_POWER: 6,
        THRUST_SCALE: "0.8",
    })
    assert len(plan.hardware_variants()) == 1
    assert len(plan.hardware_variants()[0].transformations) == 4
    assert file_sha256(HARDWARE / mission.SpacecraftOptionsFile) == before

    mission.SpacecraftOptionsFile = "default_2stage.emtg_spacecraftopt"
    multi = resolved(anchor(mission))
    for key in (BOL_POWER, ENGINE_COUNT, CONSTANT_THRUST, THRUST_SCALE):
        assert multi[key].availability is ParameterAvailability.UNAVAILABLE
        assert multi[key].reason_code.startswith("ambiguous_multistage")
    assert multi[ELECTRIC_PROPELLANT_CAPACITY].availability is ParameterAvailability.AVAILABLE


def test_electric_applicability_overrides_and_activation_are_never_silent():
    mission = options()
    mission.SpacecraftModelInput = 2
    mission.enable_electric_propellant_tank_constraint = 0
    found = resolved(anchor(mission))
    assert found[ELECTRIC_PROPELLANT_CAPACITY].availability is ParameterAvailability.AVAILABLE
    assert found[ELECTRIC_PROPELLANT_CAPACITY].activation_required
    capacity_plan = ParameterRegistry().plan_mutations(
        anchor(mission), {ELECTRIC_PROPELLANT_CAPACITY: 900}
    )
    assert "enable_constraint" in capacity_plan.mutations[0].target

    mission.Journeys[0].override_duty_cycle = 1
    found = resolved(anchor(mission))
    assert found[ENGINE_DUTY_CYCLE].reason_code == "journey_duty_cycle_override"
    with pytest.raises(ParameterApplicabilityError, match="journey_duty_cycle_override"):
        ParameterRegistry().plan_mutations(anchor(mission), {ENGINE_DUTY_CYCLE: "0.8"})

    mission.Journeys[0].override_duty_cycle = 0
    chemical = deepcopy(mission.Journeys[0])
    chemical.phase_type = 6
    chemical.override_duty_cycle = 1
    mission.Journeys.append(chemical)
    assert resolved(anchor(mission))[ENGINE_DUTY_CYCLE].availability is ParameterAvailability.AVAILABLE
    mission.Journeys.pop()

    mission.Journeys[0].phase_type = 6
    mission.Journeys[0].departure_type = 0
    mission.Journeys[0].arrival_type = 1
    ballistic = resolved(anchor(mission))
    for key in (
        ELECTRIC_PROPELLANT_CAPACITY, ENGINE_DUTY_CYCLE, ENGINE_COUNT,
        CONSTANT_THRUST, THRUST_SCALE,
    ):
        assert ballistic[key].reason_code == "electric_propulsion_unused"


def test_launch_epoch_is_exact_and_unsupported_or_unobserved_forms_are_not_inputs():
    mission = options()
    mission.Journeys[0].bounded_departure_date = 1
    found = resolved(anchor(mission))
    epoch = found[LAUNCH_EPOCH]
    assert epoch.current_value == Decimal("60000.25")
    assert epoch.provenance["reconcile_departure_date_bounds"] is True
    epoch_plan = ParameterRegistry().plan_mutations(anchor(mission), {LAUNCH_EPOCH: "60001.5"})
    assert "launch_window_open_date" in epoch_plan.mutations[0].target
    assert "wait_time_bounds=[0,0]" in epoch_plan.mutations[0].target
    assert "reconcile_if_active" in epoch_plan.mutations[0].target

    no_metrics = AtlasAnchor.from_options(mission, HARDWARE, metrics={})
    assert resolved(no_metrics)[LAUNCH_EPOCH].availability is ParameterAvailability.UNOBSERVED
    with pytest.raises(ParameterApplicabilityError, match="departure_epoch_unobserved"):
        ParameterRegistry().plan_mutations(no_metrics, {LAUNCH_EPOCH: 60001})

    aggregate_only = AtlasAnchor.from_options(
        mission, HARDWARE, metrics={"launch_epoch": 60001, "departure_c3": 4}
    )
    aggregate = resolved(aggregate_only)
    assert aggregate[LAUNCH_EPOCH].availability is ParameterAvailability.UNOBSERVED
    assert aggregate[LAUNCH_C3_REALIZED].availability is ParameterAvailability.UNOBSERVED

    mission.Journeys[0].departure_type = 3
    unsupported = resolved(anchor(mission))[LAUNCH_EPOCH]
    assert unsupported.availability is ParameterAvailability.UNAVAILABLE
    assert unsupported.reason_code == "unsupported_first_departure_epoch"


def test_launch_c3_input_bounds_polynomial_and_realized_observation_are_distinct():
    mission = options()
    mission.Journeys[0].initial_impulse_bounds = [2, 2]
    model = anchor(mission)
    found = resolved(model)
    requested = found[LAUNCH_C3_INPUT]
    realized = found[LAUNCH_C3_REALIZED]
    assert requested.current_value == Decimal(4)
    assert requested.effective_bounds.lower == Decimal(0)
    assert requested.effective_bounds.upper == Decimal(10)
    assert realized.current_value == Decimal(5)
    assert realized.definition.role is ParameterRole.REALIZED

    plan = ParameterRegistry().plan_mutations(model, {LAUNCH_C3_INPUT: 10})
    assert "sqrt(C3)" in plan.mutations[0].target
    with pytest.raises(ParameterValidationError, match="effective bounds"):
        ParameterRegistry().plan_mutations(model, {LAUNCH_C3_INPUT: 11})
    with pytest.raises(ParameterApplicabilityError, match="read-only"):
        ParameterRegistry().plan_mutations(model, {LAUNCH_C3_REALIZED: 5})

    mission.Journeys[0].initial_impulse_bounds = [0, 5]
    ranged = resolved(anchor(mission))[LAUNCH_C3_INPUT]
    assert ranged.current_value is None
    assert ranged.suggested_value == Decimal(5)

    mission.Journeys[0].initial_impulse_bounds = [-1, 5]
    assert resolved(anchor(mission))[LAUNCH_C3_INPUT].reason_code == "invalid_initial_impulse_bounds"

    mission.Journeys[0].initial_impulse_bounds = [4, 4]
    assert (
        resolved(anchor(mission))[LAUNCH_C3_INPUT].reason_code
        == "c3_input_outside_launch_vehicle_bounds"
    )

    library = LaunchVehicleLibrary.from_file(HARDWARE / "NLSII_April2017.emtg_launchvehicleopt")
    rocket = library.get("ExampleRocket")
    assert rocket.delivered_mass_kg(10) == Decimal(2385)
    with pytest.raises(HardwareInspectionError, match="outside"):
        rocket.delivered_mass_kg(Decimal("50.0001"))


def test_atomic_planning_rejects_unknown_realized_unavailable_type_bound_and_grid_errors():
    model = anchor()
    registry = ParameterRegistry()
    with pytest.raises(UnknownParameterError):
        registry.plan_mutations(model, {"unknown.parameter": 1})
    with pytest.raises(ParameterValidationError):
        registry.plan_mutations(model, {ENGINE_COUNT: True})
    with pytest.raises(ParameterValidationError):
        registry.plan_mutations(model, {ENGINE_COUNT: "1.5"})
    with pytest.raises(ParameterValidationError, match="hard bounds"):
        registry.plan_mutations(model, {ENGINE_DUTY_CYCLE: 0})
    with pytest.raises(ParameterValidationError, match="off the configured axis grid"):
        registry.plan_mutations(
            model, {MAXIMUM_MASS: 2050},
            axes=(AxisDefinition(MAXIMUM_MASS, 1000, 3000, 100),),
        )
    with pytest.raises(ParameterValidationError, match="no requested value"):
        registry.plan_mutations(
            model, {MAXIMUM_MASS: 2000},
            axes=(AxisDefinition(LAUNCH_EPOCH, 59000, 61000, 10),),
        )
    with pytest.raises(ParameterValidationError, match="cannot be a family axis"):
        registry.validate_axis(
            resolved(model)[ENGINE_COUNT], AxisDefinition(ENGINE_COUNT, 1, 4, 1)
        )

    plan = registry.plan_mutations(model, {
        MAXIMUM_MASS: "5000.0",
        ENGINE_DUTY_CYCLE: "0.90",
    })
    assert tuple(item.parameter_key for item in plan.mutations) == (
        ENGINE_DUTY_CYCLE, MAXIMUM_MASS,
    )
    assert set(plan.requested.as_mapping()) == {
        MAXIMUM_MASS, ENGINE_DUTY_CYCLE,
    }


@pytest.mark.parametrize(
    ("factory", "suffix", "valid_row", "duplicate_row", "bad_enum_row"),
    (
        (
            LaunchVehicleLibrary.from_file, ".emtg_launchvehicleopt",
            "rocket 0 -10 10 0 20 0 1000 -10",
            "rocket 0 -10 10 0 20 0 1000 -10",
            "rocket 9 -10 10 0 20 0 1000 -10",
        ),
        (
            PowerSystemLibrary.from_file, ".emtg_powersystemsopt",
            "power 1 0 0 5 10 0 1 0 0 0 0 0 0 0 0 0",
            "power 1 0 0 5 10 0 1 0 0 0 0 0 0 0 0 0",
            "power 7 0 0 5 10 0 1 0 0 0 0 0 0 0 0 0",
        ),
        (
            PropulsionSystemLibrary.from_file, ".emtg_propulsionsystemopt",
            "prop 3 none 1 1 0 10 0.1 2000 1000 0.7 1 1 0 0 0 0 0 0 0 0 0 0 0 0 0 0",
            "prop 3 none 1 1 0 10 0.1 2000 1000 0.7 1 1 0 0 0 0 0 0 0 0 0 0 0 0 0 0",
            "prop 99 none 1 1 0 10 0.1 2000 1000 0.7 1 1 0 0 0 0 0 0 0 0 0 0 0 0 0 0",
        ),
    ),
)
def test_hardware_readers_reject_duplicate_malformed_and_unknown_models(
    tmp_path: Path, factory: object, suffix: str, valid_row: str,
    duplicate_row: str, bad_enum_row: str,
):
    source = tmp_path / f"valid{suffix}"
    source.write_text(valid_row + "\n", encoding="utf-8")
    parsed = factory(source)  # type: ignore[operator]
    assert len(parsed.records) == 1

    duplicate = tmp_path / f"duplicate{suffix}"
    duplicate.write_text(valid_row + "\n" + duplicate_row + "\n", encoding="utf-8")
    with pytest.raises(HardwareInspectionError, match="duplicate"):
        factory(duplicate)  # type: ignore[operator]

    malformed = tmp_path / f"malformed{suffix}"
    malformed.write_text("too few tokens\n", encoding="utf-8")
    with pytest.raises(HardwareInspectionError, match="requires at least"):
        factory(malformed)  # type: ignore[operator]

    enum = tmp_path / f"enum{suffix}"
    enum.write_text(bad_enum_row + "\n", encoding="utf-8")
    with pytest.raises(HardwareInspectionError, match="unsupported|unknown"):
        factory(enum)  # type: ignore[operator]


def test_missing_selected_records_and_throttle_tables_fail_discovery():
    mission = options()
    mission.SpacecraftModelInput = 0
    mission.PowerSystemKey = "missing"
    with pytest.raises(ParameterRegistryError, match="does not exist"):
        anchor(mission)

    mission = options()
    mission.SpacecraftModelInput = 2
    mission.engine_type = 30
    mission.ThrottleTableFile = "missing.ThrottleTable"
    with pytest.raises(ParameterRegistryError, match="cannot resolve"):
        anchor(mission)


def test_throttle_table_reader_validates_keys_columns_and_numbers(tmp_path: Path):
    table = ThrottleTableInspection.from_file(
        HARDWARE / "NEXT_TT11_NewFrontiers_EOL_1_3_2017.ThrottleTable"
    )
    assert len(table.settings) == 15
    table.validate_for_mode(4)

    duplicate = tmp_path / "duplicate.ThrottleTable"
    duplicate.write_text(
        "PPU efficiency,1\nPPU min power (kW),0\nPPU max power (kW),2\n"
        "TL1,1,1,1,1,1,1,1\nTL1,1,1,1,1,1,1,1\n",
        encoding="utf-8",
    )
    with pytest.raises(HardwareInspectionError, match="duplicate throttle setting"):
        ThrottleTableInspection.from_file(duplicate)

    malformed = tmp_path / "malformed.ThrottleTable"
    malformed.write_text("TL1,1,1\n", encoding="utf-8")
    with pytest.raises(HardwareInspectionError, match="8 columns"):
        ThrottleTableInspection.from_file(malformed)

    nonnumeric = tmp_path / "nonnumeric.ThrottleTable"
    nonnumeric.write_text("TL1,not-a-number,1,1,1,1,1,1\n", encoding="utf-8")
    with pytest.raises(HardwareInspectionError, match="not numeric"):
        ThrottleTableInspection.from_file(nonnumeric)


def test_spacecraft_inspection_validates_selected_embedded_records(tmp_path: Path):
    source = HARDWARE / "default.emtg_spacecraftopt"
    malformed = tmp_path / source.name
    malformed.write_text(
        source.read_text(encoding="utf-8").replace(
            "PowerSystem PowerFromMissionOptions", "PowerSystem MissingPower", 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(HardwareInspectionError, match="missing power system"):
        SpacecraftInspection.from_file(malformed)


def test_architecture_id_changes_for_engine_or_discrete_hardware_not_continuous_inputs():
    mission = options()
    mission.SpacecraftModelInput = 2
    baseline = anchor(mission).architecture

    continuous = deepcopy(mission)
    continuous.maximum_mass += 1
    continuous.power_at_1_AU += 1
    continuous.thrust_scale_factor = 0.5
    assert anchor(continuous).architecture.architecture_id == baseline.architecture_id

    engine_count = deepcopy(mission)
    engine_count.number_of_electric_propulsion_systems += 1
    assert anchor(engine_count).architecture.architecture_id != baseline.architecture_id

    thruster = deepcopy(mission)
    thruster.engine_type = 0
    assert anchor(thruster).architecture.architecture_id != baseline.architecture_id

    launch_vehicle = deepcopy(mission)
    launch_vehicle.LaunchVehicleKey = "Atlas_V_401"
    assert anchor(launch_vehicle).architecture.architecture_id != baseline.architecture_id


def test_atlas_import_boundary_has_no_application_or_persistence_dependencies():
    for name in ("atlas.py", "parameters.py"):
        source = (ROOT / "PyEMTG" / "OuterLoop" / name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            (node.module or "").lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        assert not any(
            any(term in imported for term in ("studio", "fastapi", "sqlite", "scheduler"))
            for imported in imports
        )
