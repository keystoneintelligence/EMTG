import os


def _replace_tests_suffix(test_directory, replacement):
    return test_directory.replace('tests/', replacement).replace('tests\\', replacement)


def apply_test_options_overrides(
    test_options,
    test_directory,
    output_directory,
    test_path,
    update_truths=False,
    emtg_feasibility_tolerance=None,
    emtg_optimality_tolerance=None,
    emtg_major_iterations=None,
    emtg_max_run_time=None,
    emtg_quiet_nlp=None,
):
    test_options.override_working_directory = 1
    test_options.short_output_file_names = 1
    test_options.background_mode = 1
    test_options.override_mission_subfolder = 1
    test_options.forced_mission_subfolder = '.'
    test_options.universe_folder = _replace_tests_suffix(test_directory, 'universe/')
    test_options.HardwarePath = _replace_tests_suffix(test_directory, 'HardwareModels/')

    if update_truths:
        test_options.forced_working_directory = os.path.dirname(test_path)
    else:
        test_options.forced_working_directory = output_directory

    if emtg_feasibility_tolerance is not None:
        test_options.snopt_feasibility_tolerance = emtg_feasibility_tolerance
    if emtg_optimality_tolerance is not None:
        test_options.snopt_optimality_tolerance = emtg_optimality_tolerance
    if emtg_major_iterations is not None:
        test_options.snopt_major_iterations = emtg_major_iterations
    if emtg_max_run_time is not None:
        test_options.snopt_max_run_time = emtg_max_run_time
    if emtg_quiet_nlp is not None:
        test_options.quiet_NLP = emtg_quiet_nlp

    gravity_folder = _replace_tests_suffix(test_directory, 'universe/gravity_files/')
    for journey in test_options.Journeys:
        journey.universe_folder = test_options.universe_folder
        gravity_file_name = journey.central_body_gravity_file.replace('\\\\', '/').replace('\\', '/').split('/')[-1]
        journey.central_body_gravity_file = gravity_folder + gravity_file_name

    return test_options
