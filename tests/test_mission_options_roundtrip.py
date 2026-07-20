from pathlib import Path

import MissionOptions


REPO_ROOT = Path(__file__).resolve().parents[1]


def assert_options_match(left, right):
    for attr in [
        'mission_name',
        'objective_type',
        'mission_type',
        'NLP_solver_type',
        'number_of_journeys',
        'universe_folder',
        'launch_window_open_date',
        'total_flight_time_bounds',
    ]:
        assert getattr(right, attr) == getattr(left, attr)

    assert len(right.Journeys) == len(left.Journeys)
    for left_journey, right_journey in zip(left.Journeys, right.Journeys):
        for attr in [
            'journey_name',
            'phase_type',
            'departure_class',
            'arrival_class',
            'destination_list',
        ]:
            assert getattr(right_journey, attr) == getattr(left_journey, attr)


def roundtrip_options(source, tmp_path):
    options = MissionOptions.MissionOptions(str(source))
    output = tmp_path / source.name
    options.write_options_file(str(output), writeAll=True)
    return options, MissionOptions.MissionOptions(str(output))


def test_default_options_roundtrip_preserves_core_schema(tmp_path):
    source = REPO_ROOT / 'PyEMTG' / 'default.emtgopt'

    original, reparsed = roundtrip_options(source, tmp_path)

    assert_options_match(original, reparsed)


def test_regression_options_roundtrip_preserves_core_schema(tmp_path):
    source = (
        REPO_ROOT
        / 'testatron'
        / 'tests'
        / 'global_mission_options'
        / 'globalmissionoptions_MGALT_obj0.emtgopt'
    )

    original, reparsed = roundtrip_options(source, tmp_path)

    assert_options_match(original, reparsed)


def test_ipopt_solver_selection_roundtrips_with_stable_numeric_id(tmp_path):
    options = MissionOptions.MissionOptions()
    options.NLP_solver_type = 2
    output = tmp_path / 'ipopt.emtgopt'

    options.write_options_file(str(output), writeAll=True)
    reparsed = MissionOptions.MissionOptions(str(output))

    assert reparsed.NLP_solver_type == 2
    text = output.read_text(encoding='utf-8')
    assert '#2: IPOPT' in text
    assert '#1: WORHP' not in text


def test_open_source_default_solver_is_ipopt():
    options = MissionOptions.MissionOptions()
    assert options.NLP_solver_type == 2
    assert options.NLP_feasibility_tolerance == 1.0e-8


def test_legacy_solver_option_attributes_normalize_and_write_canonical_names(tmp_path):
    options = MissionOptions.MissionOptions()
    legacy_values = {
        'snopt_feasibility_tolerance': 1.0e-7,
        'snopt_optimality_tolerance': 2.0e-7,
        'NLP_max_step': 0.25,
        'snopt_major_iterations': 321,
        'snopt_max_run_time': 45,
    }
    canonical_values = {
        'NLP_feasibility_tolerance': 1.0e-7,
        'NLP_optimality_tolerance': 2.0e-7,
        'snopt_major_step_limit': 0.25,
        'NLP_iteration_limit': 321,
        'NLP_max_run_time': 45,
    }

    for legacy_name, value in legacy_values.items():
        setattr(options, legacy_name, value)

    for canonical_name, value in canonical_values.items():
        assert getattr(options, canonical_name) == value
    assert not set(legacy_values).intersection(vars(options))

    output = tmp_path / 'canonical_solver_names.emtgopt'
    options.write_options_file(str(output), writeAll=True)
    option_names = {
        line.split(' ', 1)[0]
        for line in output.read_text(encoding='utf-8').splitlines()
        if line and not line.startswith('#')
    }

    assert set(canonical_values).issubset(option_names)
    assert not set(legacy_values).intersection(option_names)


def test_legacy_solver_option_file_names_load_into_canonical_attributes():
    options = MissionOptions.MissionOptions(
        str(REPO_ROOT / 'testatron' / 'tests' / 'output_options' / 'outputoptions_frameICRF.emtgopt')
    )

    assert options.NLP_feasibility_tolerance == 1.0e-5
    assert options.NLP_optimality_tolerance == 0.002
    assert options.snopt_major_step_limit == 1
    assert options.NLP_iteration_limit == 500
    assert options.NLP_max_run_time == 59
    assert not {
        'snopt_feasibility_tolerance', 'snopt_optimality_tolerance',
        'NLP_max_step', 'snopt_major_iterations', 'snopt_max_run_time',
    }.intersection(vars(options))


def test_fixed_and_adaptive_integration_options_roundtrip(tmp_path):
    for integrator_type in (0, 1):
        options = MissionOptions.MissionOptions()
        options.integratorType = integrator_type
        options.integrator_error_control_mode = 1
        options.integrator_relative_tolerance = 2.0e-9
        options.integrator_absolute_tolerance_position = 3.0e-6
        options.integrator_absolute_tolerance_velocity = 4.0e-9
        options.integrator_absolute_tolerance_mass = 5.0e-9
        options.integrator_absolute_tolerance_time = 6.0e-6
        options.integrator_stm_error_control = 1
        options.integrator_stm_relative_tolerance = 7.0e-9
        options.integrator_stm_absolute_tolerance = 8.0e-10
        options.integration_time_step_size = 900.0
        options.integrator_initial_step_size = 30.0
        options.integrator_minimum_step_size = 1.0e-8
        options.integrator_rejection_limit = 17
        output = tmp_path / f'integrator_{integrator_type}.emtgopt'

        options.write_options_file(str(output), writeAll=True)
        reparsed = MissionOptions.MissionOptions(str(output))

        for attribute in (
            'integratorType',
            'integrator_error_control_mode',
            'integrator_relative_tolerance',
            'integrator_absolute_tolerance_position',
            'integrator_absolute_tolerance_velocity',
            'integrator_absolute_tolerance_mass',
            'integrator_absolute_tolerance_time',
            'integrator_stm_error_control',
            'integrator_stm_relative_tolerance',
            'integrator_stm_absolute_tolerance',
            'integration_time_step_size',
            'integrator_initial_step_size',
            'integrator_minimum_step_size',
            'integrator_rejection_limit',
        ):
            assert getattr(reparsed, attribute) == getattr(options, attribute)
