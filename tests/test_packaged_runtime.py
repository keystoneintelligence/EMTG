"""Run extracted release artifacts from an unrelated working directory."""
import json
import os
from pathlib import Path
import subprocess
import pytest

def test_extracted_bundle_has_no_local_build_paths():
    configured = os.getenv('EMTG_RELEASE_ROOT')
    if not configured:
        pytest.skip('set EMTG_RELEASE_ROOT to qualify an extracted release')
    repository = Path(__file__).resolve().parents[1]
    import sys
    subprocess.run([
        sys.executable, str(repository/'scripts/audit-release-paths.py'),
        str(Path(configured).resolve()), '--forbid-root', str(repository),
    ], check=True)

def test_extracted_bundle_discovers_its_own_data(tmp_path):
    configured = os.getenv('EMTG_RELEASE_ROOT')
    if not configured:
        pytest.skip('set EMTG_RELEASE_ROOT to qualify an extracted release')
    root = Path(configured).resolve()
    executable = root/'bin'/('EMTGv9.exe' if os.name == 'nt' else 'EMTGv9')
    data = root/'share/emtg'
    assert executable.is_file(), 'requested release executable is missing'
    assert (data/'Universe/Sun.emtg_universe').is_file()
    assert (data/'HardwareModels/default.emtg_spacecraftopt').is_file()
    assert (data/'licenses/EMTG_NOSA_License.pdf').is_file()
    env = {key: value for key, value in os.environ.items() if key != 'EMTG_DATA_DIR'}
    version = subprocess.run([str(executable), '--version'], cwd=tmp_path, env=env, text=True, capture_output=True, check=True)
    assert version.stdout.strip() == 'EMTG '+(data/'VERSION').read_text().strip()
    capabilities = subprocess.run([str(executable), '--capabilities'], cwd=tmp_path, env=env, text=True, capture_output=True, check=True)
    assert json.loads(capabilities.stdout) == {'ipopt': True, 'snopt': False}
    doctor = subprocess.run([str(executable), '--doctor'], cwd=tmp_path, env=env, text=True, capture_output=True)
    assert str(data).replace('\\','/').lower() in doctor.stdout.replace('\\','/').lower()
    # BSPs are deliberately external. Discovery must report that state honestly.
    assert 'Leap-seconds kernel (.tls): found' in doctor.stdout
    assert 'Planetary constants kernel (.tpc): found' in doctor.stdout

    assert doctor.returncode == 3
    assert 'BSP kernels: not found' in doctor.stdout
    assert 'Status: action required' in doctor.stdout

    runtime = data/'licenses/compiler-runtime'
    if os.name == 'nt':
        assert (runtime/'gcc-runtime-library-exception-3.1.txt').is_file()
    else:
        notice = (runtime/'gcc-runtime-copyright.txt').read_text()
        assert 'GCC RUNTIME LIBRARY EXCEPTION' in notice
        assert all((runtime/name).is_file() for name in ('GPL-3','LGPL-2.1','LGPL-3'))
