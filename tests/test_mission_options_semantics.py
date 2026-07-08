from pathlib import Path

import MissionOptions
from options_preparation import apply_test_options_overrides


REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_DIRECTORY = (REPO_ROOT / 'testatron' / 'tests').as_posix() + '/'


def load_options():
    return MissionOptions.MissionOptions(
        str(REPO_ROOT / 'testatron' / 'tests' / 'output_options' / 'outputoptions_frameICRF.emtgopt')
    )


def test_testatron_option_overrides_rewrite_paths_and_solver_controls(tmp_path):
    options = load_options()
    original_gravity_name = Path(options.Journeys[0].central_body_gravity_file.replace('\\', '/')).name

    apply_test_options_overrides(
        options,
        TEST_DIRECTORY,
        str(tmp_path),
        'tests/output_options/outputoptions_frameICRF',
        emtg_feasibility_tolerance=1.0e-7,
        emtg_optimality_tolerance=2.0e-7,
        emtg_major_iterations=12,
        emtg_max_run_time=34,
        emtg_quiet_nlp=1,
    )

    assert options.override_working_directory == 1
    assert options.short_output_file_names == 1
    assert options.background_mode == 1
    assert options.override_mission_subfolder == 1
    assert options.forced_mission_subfolder == '.'
    assert options.forced_working_directory == str(tmp_path)
    assert options.universe_folder.endswith('/testatron/universe/')
    assert options.HardwarePath.endswith('/testatron/HardwareModels/')
    assert options.snopt_feasibility_tolerance == 1.0e-7
    assert options.snopt_optimality_tolerance == 2.0e-7
    assert options.snopt_major_iterations == 12
    assert options.snopt_max_run_time == 34
    assert options.quiet_NLP == 1
    assert options.Journeys[0].universe_folder == options.universe_folder
    assert options.Journeys[0].central_body_gravity_file.endswith('/testatron/universe/gravity_files/' + original_gravity_name)


def test_testatron_update_truths_writes_next_to_source_case():
    options = load_options()

    apply_test_options_overrides(
        options,
        TEST_DIRECTORY,
        None,
        'tests/output_options/outputoptions_frameICRF',
        update_truths=True,
    )

    assert options.forced_working_directory == 'tests/output_options'


def test_master_decision_vector_assembly_and_disassembly_are_consistent():
    options = load_options()
    options.Journeys[0].trialX = [['p0x', 1.25], ['p0y', -2.5]]

    options.AssembleMasterDecisionVector()

    assert options.trialX == [['j0p0x', 1.25], ['j0p0y', -2.5]]

    options.trialX.append(['j0p1z', 3.75])
    options.DisassembleMasterDecisionVector()

    assert options.Journeys[0].trialX == [['p0x', 1.25], ['p0y', -2.5], ['p1z', 3.75]]


def test_master_constraint_vectors_preserve_comment_state_and_journey_prefixes():
    options = load_options()
    options.Journeys[0].ManeuverConstraintDefinitions = ['p0 abs epoch constraint', '#p0 disabled maneuver']
    options.Journeys[0].BoundaryConstraintDefinitions = ['p0 boundary constraint', '#p0 disabled boundary']
    options.Journeys[0].PhaseDistanceConstraintDefinitions = ['p0 distance constraint', '#p0 disabled distance']

    options.AssembleMasterConstraintVectors()

    assert options.ManeuverConstraintDefinitions == ['j0p0 abs epoch constraint', '#j0p0 disabled maneuver']
    assert options.BoundaryConstraintDefinitions == ['j0p0 boundary constraint', '#j0p0 disabled boundary']
    assert options.PhaseDistanceConstraintDefinitions == ['j0p0 distance constraint', '#j0p0 disabled distance']

    options.DisassembleMasterConstraintVectors()

    assert options.Journeys[0].ManeuverConstraintDefinitions == ['p0 abs epoch constraint', '#p0 disabled maneuver']
    assert options.Journeys[0].BoundaryConstraintDefinitions == ['p0 boundary constraint', '#p0 disabled boundary']
    assert options.Journeys[0].PhaseDistanceConstraintDefinitions == ['p0 distance constraint', '#p0 disabled distance']
