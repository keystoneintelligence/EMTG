import hashlib
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def test_results_import_needs_only_standard_library(tmp_path):
    code = "import sys; sys.path.insert(0, %r); from PyEMTG.Results import EMTGResultParser; r=EMTGResultParser().parse('absent.emtg'); assert not r.complete; assert not any('OuterLoop' in n for n in sys.modules)" % str(ROOT)
    subprocess.run([sys.executable, '-I', '-S', '-c', code], cwd=tmp_path, check=True)

def test_legacy_parser_is_a_reexport():
    from PyEMTG.Results import EMTGResultParser, ParsedEMTGResult
    from PyEMTG.OuterLoop.evaluator import EMTGResultParser as old_parser, ParsedEMTGResult as old_result
    assert EMTGResultParser is old_parser
    assert ParsedEMTGResult is old_result

def test_inventory_has_explicit_missing_context_and_per_journey_frames(tmp_path):
    from PyEMTG.Results import artifact_inventory
    native = tmp_path / 'mission.emtg'
    native.write_text('Journey: 0\nCentral Body: Sun\nFrame: ICRF\nJourney: 1\nCentral Body: Mars\nFrame: J2000_BCI\n')
    inventory = artifact_inventory({'emtg-output': native, 'options': tmp_path/'absent.emtgopt'})
    assert inventory['schema_version'] == 'emtg-artifacts/v1'
    output, missing = inventory['artifacts']
    assert output['sha256'] == hashlib.sha256(native.read_bytes()).hexdigest()
    assert output['journeys'] == [{'journey': '0', 'central_body': 'Sun', 'frame': 'ICRF'}, {'journey': '1', 'central_body': 'Mars', 'frame': 'J2000_BCI'}]
    assert output['time_system'] is None
    assert output['units']['position'] == 'km'
    assert missing['status'] == 'missing'
    assert missing['sha256'] is None
    assert missing['journeys'] == []
    assert missing['units'] == {}
