"""The public solver fixture must stage offline and fail closed on corruption."""
import importlib.util
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stage_native_universe_without_production_kernels(tmp_path):
    stage = load_script('prepare_test_ephemeris')
    universe = tmp_path / 'input'
    (universe / 'ephemeris_files').mkdir(parents=True)
    (universe / 'Sun.emtg_universe').write_text('scientific universe input')
    (universe / 'ephemeris_files/naif0012.tls').write_text('leap seconds')
    (universe / 'ephemeris_files/private-production.bsp').write_bytes(b'never stage this')
    output = tmp_path / 'new'
    manifest = stage.prepare(output, universe=universe)
    kernels = list((output / 'ephemeris_files').glob('*.bsp'))
    assert [p.name for p in kernels] == [manifest['kernel']['name']]
    assert stage.sha256(kernels[0]) == manifest['kernel']['sha256']
    assert (output / 'Sun.emtg_universe').read_text() == 'scientific universe input'
    assert (output / 'ephemeris_files/naif0012.tls').is_file()
    with pytest.raises(ValueError, match='new output directory'):
        stage.prepare(output, universe=universe)


def test_corrupt_ephemeris_fails_before_creating_universe(tmp_path):
    stage = load_script('prepare_test_ephemeris')
    fixture = tmp_path / 'fixture'
    fixture.mkdir()
    shutil.copyfile(stage.FIXTURE / 'manifest.json', fixture / 'manifest.json')
    (fixture / 'emtg-tests-v1.bsp.gz').write_bytes(b'corrupt')
    output = tmp_path / 'output'
    with pytest.raises(ValueError, match='checksum mismatch'):
        stage.prepare(output, fixture=fixture)
    assert not output.exists()


@pytest.mark.parametrize('interval,covered,expected', [
    ((0, 10), [(3, 7)], [(0, 3), (7, 10)]),
    ((0, 10), [(-1, 11)], []),
    ((0, 10), [(0, 3), (7, 10)], [(3, 7)]),
    ((0, 10), [(10, 20)], [(0, 10)]),
])
def test_spk_precedence_preserves_uncovered_intervals(interval, covered, expected):
    builder = load_script('build_test_ephemeris')
    assert builder.subtract_intervals(interval, covered) == expected
