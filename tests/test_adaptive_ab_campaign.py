from pathlib import Path
from types import SimpleNamespace
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / 'testatron'))

from adaptive_ab_campaign import (  # noqa: E402
    RunResult,
    assert_pair_is_controlled,
    solution_family_signature,
    summarize,
)


def test_pair_guard_allows_only_integrator_and_output_directory_changes(tmp_path):
    fixed = tmp_path / 'fixed.emtgopt'
    adaptive = tmp_path / 'adaptive.emtgopt'
    fixed.write_text(
        'mission_name pair\nintegratorType 1\nforced_working_directory fixed\nMBH_RNG_seed 123\n',
        encoding='utf-8',
    )
    adaptive.write_text(
        'mission_name pair\nintegratorType 0\nforced_working_directory adaptive\nMBH_RNG_seed 123\n',
        encoding='utf-8',
    )
    assert_pair_is_controlled(fixed, adaptive)

    adaptive.write_text(adaptive.read_text(encoding='utf-8').replace('123', '124'), encoding='utf-8')
    try:
        assert_pair_is_controlled(fixed, adaptive)
    except AssertionError:
        pass
    else:
        raise AssertionError('pair guard accepted different stochastic seeds')


def fake_mission(epoch_offset=0.0):
    event = SimpleNamespace(
        EventType='pwr_flyby',
        Location='Earth',
        JulianDate=2459000.0 + epoch_offset,
        Altitude=500.0,
        Mass=900.0,
        SpacecraftState=[1.0e8, 2.0e8, 3.0e8, 10.0, 20.0, 30.0],
    )
    return SimpleNamespace(
        objective_value=-0.5,
        total_flight_time_years=2.0,
        final_mass_including_propellant_margin=800.0,
        Journeys=[SimpleNamespace(missionevents=[event])],
    )


def test_solution_family_signature_is_deterministic_and_thresholded():
    baseline = solution_family_signature(fake_mission())
    assert baseline == solution_family_signature(fake_mission(0.01))
    assert baseline != solution_family_signature(fake_mission(1.0))


def test_summary_reports_mode_specific_novelty():
    common = dict(
        seed=1,
        order=0,
        return_code=0,
        wall_seconds=1.0,
        output_directory='.',
        options_file='case.emtgopt',
        feasible=True,
        objective=1.0,
    )
    results = [
        RunResult(mode='fixed', solution_family='fixed-family', **common),
        RunResult(mode='adaptive', solution_family='adaptive-family', **common),
    ]
    summary = summarize(results)
    assert summary['fixed']['feasible_rate'] == 1.0
    assert summary['family_comparison']['fixed_only'] == ['fixed-family']
    assert summary['family_comparison']['adaptive_only'] == ['adaptive-family']
