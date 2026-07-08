from pathlib import Path

from test_selection import (
    SMOKE_ATTRIBUTES_TO_IGNORE,
    SMOKE_DEFAULT_TOLERANCE,
    SMOKE_TEST_CASES,
    UNIT_TEST_FOLDERS,
    make_tests_list,
)


def as_posix(path):
    return str(path).replace('\\', '/')


def write_case(root, relative_name):
    case_path = root / (relative_name + '.emtgopt')
    case_path.parent.mkdir(parents=True, exist_ok=True)
    case_path.write_text('# test case\n', encoding='utf-8')
    return case_path.with_suffix('')


def test_folder_selection_recurses_in_stable_order(tmp_path):
    tests_root = tmp_path / 'tests'
    beta = write_case(tests_root, 'feature/beta')
    alpha = write_case(tests_root, 'feature/nested/alpha')

    tests = make_tests_list(['feature'], 'folders', as_posix(tests_root), cwd=as_posix(tmp_path))

    assert tests == [as_posix(beta), as_posix(alpha)]


def test_case_selection_accepts_absolute_files_without_extension(tmp_path):
    tests_root = tmp_path / 'tests'
    case = write_case(tests_root, 'feature/case')

    tests = make_tests_list([as_posix(case)], 'cases', as_posix(tests_root), cwd=as_posix(tmp_path))

    assert tests == [as_posix(case)]


def test_all_selection_deduplicates_root_recursive_discovery(tmp_path):
    tests_root = tmp_path / 'tests'
    case = write_case(tests_root, 'feature/case')

    tests = make_tests_list([], 'all', as_posix(tests_root), cwd=as_posix(tmp_path))

    assert tests == [as_posix(case)]


def test_failed_selection_preserves_first_seen_order_and_deduplicates(tmp_path):
    output = tmp_path / 'output'
    output.mkdir()
    (output / 'failed_tests.csv').write_text(
        'header\nList of failed runs:\ncase/a\ncase/b\ncase/a\n',
        encoding='utf-8',
    )

    tests = make_tests_list([as_posix(output) + '/'], 'failed', as_posix(tmp_path / 'tests'))

    assert tests == ['case/a', 'case/b']


def test_unit_folder_list_excludes_integration_regressions():
    assert 'integration_asteroid_missions' not in UNIT_TEST_FOLDERS
    assert 'mission_tests' in UNIT_TEST_FOLDERS


def test_smoke_case_list_is_explicit_and_has_comparison_defaults():
    assert SMOKE_TEST_CASES == [
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
    assert SMOKE_DEFAULT_TOLERANCE == 10.0
    assert SMOKE_ATTRIBUTES_TO_IGNORE == ['E.GregorianDate']
