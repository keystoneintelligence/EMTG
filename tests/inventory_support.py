import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TESTATRON_ROOT = REPO_ROOT / 'testatron'
TEST_ROOT = TESTATRON_ROOT / 'tests'
INVENTORY_PATH = TESTATRON_ROOT / 'test_inventory.json'


def load_inventory():
    return json.loads(INVENTORY_PATH.read_text(encoding='utf-8'))


def all_case_ids():
    return sorted(
        options_file.relative_to(TEST_ROOT).with_suffix('').as_posix()
        for options_file in TEST_ROOT.rglob('*.emtgopt')
    )


def case_options_path(case_id):
    return TEST_ROOT / (case_id + '.emtgopt')


def option_values(case_id, option_name):
    values = []
    with case_options_path(case_id).open(encoding='utf-8') as options_file:
        for line in options_file:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            parts = stripped.split()
            if parts and parts[0] == option_name:
                values.append(parts[1:])
    return values
