from pathlib import Path

import Mission as mission_module


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / 'testatron' / 'tests' / 'output_options'


def test_comparatron_passes_for_identical_real_mission_files(tmp_path):
    fixture = FIXTURE_ROOT / 'outputoptions_frameICRF.emtg'
    mission = mission_module.Mission(str(fixture))

    success, comparison = mission.Comparatron(
        baseline_path=str(fixture),
        csv_file_name=str(tmp_path / 'comparison.csv'),
    )

    assert success
    assert comparison.loc[comparison['Match'] == False].empty
    assert not (tmp_path / 'comparison.csv').exists()


def test_comparatron_reports_real_mission_file_differences_and_writes_csv(tmp_path):
    baseline = FIXTURE_ROOT / 'outputoptions_frameICRF.emtg'
    candidate = FIXTURE_ROOT / 'outputoptions_frameJ2000BCF.emtg'
    mission = mission_module.Mission(str(candidate))
    csv_path = tmp_path / 'comparison.csv'

    success, comparison = mission.Comparatron(
        baseline_path=str(baseline),
        csv_file_name=str(csv_path),
        default_tolerance=1.0e-10,
    )

    failures = comparison.loc[comparison['Match'] == False]
    assert not success
    assert not failures.empty
    assert csv_path.is_file()
    assert 'Mission.Journey[0].boundary_states[0][0]' in set(failures['Output Name'])
