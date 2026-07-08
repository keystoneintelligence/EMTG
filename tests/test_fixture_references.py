from pathlib import Path

import MissionOptions
from inventory_support import REPO_ROOT, TEST_ROOT, load_inventory
from options_preparation import apply_test_options_overrides


TEST_DIRECTORY = TEST_ROOT.as_posix() + '/'
OPTIONAL_PLACEHOLDER_FILENAMES = {
    'DoesNotExist.grv',
    'DoesNotExist.emtg_densityopt',
}


def resolve_emtg_fixture_path(raw_path, case_path):
    normalized = raw_path.replace('\\', '/')
    if normalized.startswith('C:/emtg/testatron/'):
        return REPO_ROOT / 'testatron' / normalized[len('C:/emtg/testatron/'):]
    candidate = Path(normalized)
    if candidate.is_absolute():
        return candidate
    return (case_path.parent / candidate).resolve()


def hardware_file(options, filename):
    return resolve_emtg_fixture_path(options.HardwarePath, Path.cwd()) / filename


def universe_file(options, journey):
    return resolve_emtg_fixture_path(options.universe_folder, Path.cwd()) / (journey.journey_central_body + '.emtg_universe')


def ephemeris_file(options, filename):
    return resolve_emtg_fixture_path(options.universe_folder, Path.cwd()) / 'ephemeris_files' / filename


def atmosphere_file(options, filename):
    return resolve_emtg_fixture_path(options.universe_folder, Path.cwd()) / 'atmosphere_files' / filename


def gravity_file(options, filename):
    return resolve_emtg_fixture_path(options.universe_folder, Path.cwd()) / 'gravity_files' / filename


def assert_file_exists(missing, label, path):
    if not path.is_file():
        missing.append(label + ': ' + path.as_posix())


def test_selected_fixture_references_exist_after_testatron_rewrites(tmp_path):
    inventory = load_inventory()
    missing = []

    for case_id in inventory['fixture_reference_cases']:
        case_path = TEST_ROOT / (case_id + '.emtgopt')
        options = MissionOptions.MissionOptions(str(case_path))
        apply_test_options_overrides(
            options,
            TEST_DIRECTORY,
            str(tmp_path),
            'tests/' + case_id,
            update_truths=False,
        )

        for journey in options.Journeys:
            assert_file_exists(missing, case_id + ' universe', universe_file(options, journey))

            gravity_filename = Path(journey.central_body_gravity_file.replace('\\', '/')).name
            if gravity_filename not in OPTIONAL_PLACEHOLDER_FILENAMES:
                assert_file_exists(missing, case_id + ' gravity', gravity_file(options, gravity_filename))

            density_filename = Path(journey.AtmosphericDensityModelDataFile.replace('\\', '/')).name
            if getattr(journey, 'perturb_drag', 0) and density_filename not in OPTIONAL_PLACEHOLDER_FILENAMES:
                assert_file_exists(missing, case_id + ' atmosphere', atmosphere_file(options, density_filename))

        for attr in [
            'ThrottleTableFile',
            'LaunchVehicleLibraryFile',
            'PowerSystemsLibraryFile',
            'PropulsionSystemsLibraryFile',
            'SpacecraftOptionsFile',
        ]:
            filename = getattr(options, attr)
            if filename and Path(filename).name not in OPTIONAL_PLACEHOLDER_FILENAMES:
                assert_file_exists(missing, case_id + ' ' + attr, hardware_file(options, filename))

        if int(options.ephemeris_source) == 1:
            assert_file_exists(missing, case_id + ' SPICE leap seconds', ephemeris_file(options, options.SPICE_leap_seconds_kernel))
            assert_file_exists(missing, case_id + ' SPICE frame kernel', ephemeris_file(options, options.SPICE_reference_frame_kernel))
            ephemeris_dir = resolve_emtg_fixture_path(options.universe_folder, Path.cwd()) / 'ephemeris_files'
            if not list(ephemeris_dir.glob('*.bsp')) and not (ephemeris_dir / 'go_get_these_files.txt').is_file():
                missing.append(case_id + ' SPICE BSP kernels or retrieval manifest: ' + ephemeris_dir.as_posix())

    assert missing == []
