from inventory_support import TEST_ROOT, all_case_ids, load_inventory, option_values
from test_selection import UNIT_TEST_FOLDERS


CASE_LIST_KEYS = [
    'expected_failure_cases',
    'expected_no_truth_cases',
    'requires_spice_cases',
    'requires_ipopt_cases',
    'requires_snopt_cases',
    'fixture_reference_cases',
]


def cases_with_option_value(option_name, expected_value):
    return sorted(
        case_id
        for case_id in all_case_ids()
        if any(values and values[0] == expected_value for values in option_values(case_id, option_name))
    )


def test_inventory_manifest_schema_and_paths_are_valid():
    inventory = load_inventory()
    all_cases = set(all_case_ids())

    assert inventory['schema_version'] == 1
    assert inventory['fast_unit_folders'] == UNIT_TEST_FOLDERS
    assert inventory['slow_integration_folders'] == ['integration_asteroid_missions']

    for folder in inventory['fast_unit_folders'] + inventory['slow_integration_folders']:
        assert (TEST_ROOT / folder).is_dir()

    for key in CASE_LIST_KEYS:
        assert sorted(inventory[key]) == inventory[key]
        unknown_cases = sorted(set(inventory[key]) - all_cases)
        assert unknown_cases == []


def test_every_case_has_exactly_one_primary_inventory_category():
    inventory = load_inventory()
    primary_folders = inventory['fast_unit_folders'] + inventory['slow_integration_folders']
    uncategorized = []
    multi_categorized = []

    for case_id in all_case_ids():
        matches = [folder for folder in primary_folders if case_id.startswith(folder + '/')]
        if not matches:
            uncategorized.append(case_id)
        elif len(matches) > 1:
            multi_categorized.append(case_id)

    assert uncategorized == []
    assert multi_categorized == []


def test_expected_no_truth_inventory_matches_missing_truth_files_and_markers():
    inventory = load_inventory()
    expected_no_truth = sorted(inventory['expected_no_truth_cases'])
    missing_truths = sorted(
        case_id
        for case_id in all_case_ids()
        if not (TEST_ROOT / (case_id + '.emtg')).is_file()
    )

    assert expected_no_truth == missing_truths
    for case_id in expected_no_truth:
        assert (TEST_ROOT / case_id).parent.joinpath('EXPECTED_NO_TRUTH.md').is_file()


def test_requires_spice_inventory_matches_spice_ephemeris_cases():
    inventory = load_inventory()
    assert sorted(inventory['requires_spice_cases']) == cases_with_option_value('ephemeris_source', '1')


def test_requires_ipopt_inventory_matches_ipopt_cases():
    inventory = load_inventory()
    assert sorted(inventory['requires_ipopt_cases']) == cases_with_option_value('NLP_solver_type', '2')


def test_expected_failure_cases_are_explicit_and_disjoint_from_no_truth_cases():
    inventory = load_inventory()
    assert set(inventory['expected_failure_cases']).isdisjoint(inventory['expected_no_truth_cases'])
