from os import getcwd, path, walk


UNIT_TEST_FOLDERS = [
    'global_mission_options',
    'journey_options',
    'mission_tests',
    'output_options',
    'physics_options',
    'script_constraint_tests',
    'solver_options',
    'spacecraft_options',
    'state_representation_tests',
    'transcription_tests',
]

SMOKE_TEST_CASES = [
    'tests/output_options/outputoptions_frameICRF',
    'tests/output_options/outputoptions_frameJ2000BCF',
    'tests/output_options/outputoptions_frameTODBCF',
    'tests/output_options/outputoptions_frameTODBCI',
    'tests/output_options/outputoptions_outputjourneywaittimes',
    'tests/physics_options/physicsoptions_add3rdbody',
    'tests/physics_options/physicsoptions_addJ2',
    'tests/physics_options/physicsoptions_addSRP',
    'tests/physics_options/physicsoptions_periapseCartesian',
    'tests/physics_options/physicsoptions_periapseIncomingBplane',
    'tests/physics_options/physicsoptions_periapseSphericalAZFPA',
    'tests/physics_options/physicsoptions_periapseSphericalRADEC',
    'tests/spacecraft_options/spacecraftoptions_LT_Emargin',
    'tests/spacecraft_options/spacecraftoptions_LT_eng0',
    'tests/spacecraft_options/spacecraftoptions_LT_eng3',
    'tests/spacecraft_options/spacecraftoptions_LT_powsrc0',
]

SMOKE_DEFAULT_TOLERANCE = 10.0
SMOKE_ATTRIBUTES_TO_IGNORE = [
    'E.GregorianDate',
]


def _normalize_directory(directory):
    normalized = directory.replace('\\', '/')
    if not normalized.endswith('/'):
        normalized += '/'
    return normalized


def _append_unique(items, item):
    if item not in items:
        items.append(item)


def make_tests_list(test_cases, run_type, test_directory, cwd=None):
    tests_list = []
    test_directory = _normalize_directory(test_directory)
    cwd = (cwd or getcwd()).replace('\\', '/')

    def append_tests_from_path(test):
        normalized_test = test.replace('\\', '/')
        candidate_path = normalized_test
        if not path.isdir(candidate_path) and not path.isfile(candidate_path + '.emtgopt'):
            if normalized_test.startswith('tests/'):
                candidate_path = cwd + '/' + normalized_test
            else:
                candidate_path = test_directory + normalized_test

        if path.isdir(candidate_path):
            for root, dirs, filenames in walk(candidate_path):
                dirs.sort()
                for filename in sorted(filenames):
                    if filename.endswith('.emtgopt'):
                        _append_unique(
                            tests_list,
                            (root + '/' + filename.replace('.emtgopt', '')).replace('\\', '/'),
                        )
        elif path.isfile(candidate_path + '.emtgopt'):
            _append_unique(tests_list, candidate_path.replace('\\', '/'))
        else:
            _append_unique(tests_list, normalized_test)

    if run_type == 'all':
        test_folders = sorted(next(walk(test_directory))[1])
        test_folders.append('')
    elif run_type == 'folders':
        test_folders = test_cases
    elif run_type == 'cases':
        for test_case in test_cases:
            append_tests_from_path(test_case)
        return tests_list
    elif run_type == 'failed':
        for failpath in test_cases:
            with open(failpath + 'failed_tests.csv', 'r') as fail_file:
                list_of_failed_flag = False
                for line in fail_file.readlines():
                    if 'List of failed runs:' in line:
                        list_of_failed_flag = True
                    elif list_of_failed_flag:
                        _append_unique(tests_list, line.strip('\n'))
        return tests_list
    else:
        raise ValueError('Unknown testatron run type: ' + str(run_type))

    for test in test_folders:
        append_tests_from_path(test)

    return tests_list
