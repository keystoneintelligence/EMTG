from pathlib import Path

from test_selection import SMOKE_TEST_CASES


REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = REPO_ROOT / 'testatron' / 'tests'
EXPECTED_NO_TRUTH_MARKER = 'EXPECTED_NO_TRUTH.md'


def test_every_emtgopt_has_truth_or_expected_no_truth_marker():
    missing_truths = []
    for options_file in TEST_ROOT.rglob('*.emtgopt'):
        truth_file = options_file.with_suffix('.emtg')
        marker = options_file.parent / EXPECTED_NO_TRUTH_MARKER
        if not truth_file.is_file() and not marker.is_file():
            missing_truths.append(options_file.relative_to(TEST_ROOT).as_posix())

    assert missing_truths == []


def test_smoke_cases_have_options_and_truth_files():
    missing_files = []
    for smoke_case in SMOKE_TEST_CASES:
        case_path = REPO_ROOT / 'testatron' / smoke_case
        if not case_path.with_suffix('.emtgopt').is_file():
            missing_files.append(case_path.with_suffix('.emtgopt').relative_to(REPO_ROOT).as_posix())
        if not case_path.with_suffix('.emtg').is_file():
            missing_files.append(case_path.with_suffix('.emtg').relative_to(REPO_ROOT).as_posix())

    assert missing_files == []
