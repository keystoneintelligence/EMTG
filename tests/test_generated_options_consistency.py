import csv
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_ipopt_schema_matches_generated_cpp_and_python():
    with (REPO_ROOT / 'OptionsOverhaul' / 'list_of_missionoptions.csv').open(
        newline='', encoding='utf-8-sig'
    ) as schema_file:
        definition = next(
            row for row in csv.DictReader(schema_file) if row['name'] == 'NLP_solver_type'
        )

    assert definition['defaultValue'] == '2'
    assert definition['upperBound'] == '2'
    assert '#2: IPOPT' in definition['description']
    assert '#1: WORHP' not in definition['description']

    cpp = (REPO_ROOT / 'src' / 'Core' / 'missionoptions.cpp').read_text(encoding='utf-8')
    header = (REPO_ROOT / 'src' / 'Core' / 'missionoptions.h').read_text(encoding='utf-8')
    python = (REPO_ROOT / 'PyEMTG' / 'MissionOptions.py').read_text(encoding='utf-8')

    assert 'this->NLP_solver_type = 2;' in cpp
    assert 'this->NLP_solver_type_upperBound = 2;' in cpp
    assert '#2: IPOPT' in cpp
    assert '2 - IPOPT' in header
    assert 'self.NLP_solver_type = 2' in python
    assert '#2: IPOPT' in python


def test_option_generator_is_repository_relative():
    generator = (
        REPO_ROOT
        / 'PyEMTG'
        / 'OptionsOverhaul'
        / 'make_EMTG_missionoptions_journeyoptions.py'
    ).read_text(encoding='utf-8')

    assert 'DEFAULT_REPOSITORY_ROOT' in generator
    assert "EMTG_path = 'C:/emtg/'" not in generator
