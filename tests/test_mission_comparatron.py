from types import SimpleNamespace

import Mission as mission_module


MissionClass = mission_module.Mission


def make_mission(name='case', mission_value=10.0, journey_value=20.0, event_value=30.0, event_label='same'):
    mission = MissionClass.__new__(MissionClass)
    mission.mission_name = name
    mission.total_deterministic_deltav = mission_value
    mission.Journeys = [
        SimpleNamespace(
            journey_name='J1',
            journey_value=journey_value,
            missionevents=[
                SimpleNamespace(
                    ThrottleLevel='1',
                    event_value=event_value,
                    event_label=event_label,
                )
            ],
        )
    ]
    return mission


def compare(monkeypatch, tmp_path, baseline, candidate, **kwargs):
    monkeypatch.setattr(mission_module, 'Mission', lambda baseline_path: baseline)
    return candidate.Comparatron(
        baseline_path='baseline.emtg',
        csv_file_name=str(tmp_path / 'comparison.csv'),
        **kwargs
    )


def test_comparatron_accepts_numeric_differences_within_tolerance(monkeypatch, tmp_path):
    baseline = make_mission(mission_value=10.0)
    candidate = make_mission(mission_value=10.005)

    success, comparison = compare(monkeypatch, tmp_path, baseline, candidate, default_tolerance=0.01)

    assert success
    assert comparison.loc[comparison['Match'] == False].empty


def test_comparatron_reports_numeric_differences_outside_tolerance(monkeypatch, tmp_path):
    baseline = make_mission(mission_value=10.0)
    candidate = make_mission(mission_value=10.02)

    success, comparison = compare(monkeypatch, tmp_path, baseline, candidate, default_tolerance=0.01)

    assert not success
    failures = comparison.loc[comparison['Match'] == False]
    assert 'Mission.total_deterministic_deltav' in set(failures['Output Name'])
    assert (tmp_path / 'comparison.csv').is_file()


def test_comparatron_honors_ignored_mission_attributes(monkeypatch, tmp_path):
    baseline = make_mission(mission_value=10.0)
    candidate = make_mission(mission_value=50.0)

    success, comparison = compare(
        monkeypatch,
        tmp_path,
        baseline,
        candidate,
        default_tolerance=0.01,
        attributes_to_ignore=['M.total_deterministic_deltav'],
    )

    assert success
    assert comparison.loc[comparison['Match'] == False].empty


def test_comparatron_reports_string_differences(monkeypatch, tmp_path):
    baseline = make_mission(event_label='baseline')
    candidate = make_mission(event_label='candidate')

    success, comparison = compare(monkeypatch, tmp_path, baseline, candidate, default_tolerance=0.01)

    assert not success
    failures = comparison.loc[comparison['Match'] == False]
    assert 'Mission.Journey[0].MissionEvent[0].event_label' in set(failures['Output Name'])
