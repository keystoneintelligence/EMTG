import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PyEMTG.MissionOptions import MissionOptions
from PyEMTG.nasa_compatibility import export_nasa_options, NASACompatibilityError


def test_nasa_export_preserves_options_constraints_and_trial_vector(tmp_path):
    options = MissionOptions()
    options.NLP_feasibility_tolerance = 1e-5
    options.Journeys[0].trialX = [['p0flight time', 42.0]]
    options.Journeys[0].BoundaryConstraintDefinitions = ['p0 arrival_declination -10 10']
    source, destination = tmp_path/'source.emtgopt', tmp_path/'nasa.emtgopt'
    options.write_options_file(str(source), writeAll=True)
    report = export_nasa_options(source, destination, solver='SNOPT')
    text = destination.read_text()
    assert 'NLP_solver_type 0\n' in text
    assert 'snopt_feasibility_tolerance 1e-05' in text
    assert 'NLP_feasibility_tolerance' not in text
    assert 'integrator_error_control_mode' not in text
    assert 'integrator_error_control_mode' in report['omitted_inactive_fork_defaults']
    reparsed = MissionOptions(str(destination))
    assert reparsed.NLP_solver_type == 0
    assert reparsed.NLP_feasibility_tolerance == 1e-5
    assert reparsed.Journeys[0].trialX == options.Journeys[0].trialX
    assert reparsed.Journeys[0].BoundaryConstraintDefinitions == options.Journeys[0].BoundaryConstraintDefinitions


@pytest.mark.parametrize('line', ['integrator_error_control_mode 1', 'SPICE_high_fidelity_derivatives 1', 'unrecognized_option 7'])
def test_unsupported_settings_fail_without_writing(tmp_path, line):
    source, destination = tmp_path/'source.emtgopt', tmp_path/'nasa.emtgopt'
    source.write_text(line+'\n')
    with pytest.raises(NASACompatibilityError, match='unsupported'):
        export_nasa_options(source, destination, solver='SNOPT')
    assert not destination.exists()


def test_compatible_solver_must_be_explicit(tmp_path):
    with pytest.raises(TypeError):
        export_nasa_options(tmp_path/'absent', tmp_path/'out')
    with pytest.raises(NASACompatibilityError, match='SNOPT'):
        export_nasa_options(tmp_path/'absent', tmp_path/'out', solver='IPOPT')


def test_export_with_nasa_parser_when_provided(tmp_path):
    nasa = os.getenv('EMTG_NASA_PYEMTG')
    if not nasa:
        pytest.skip('set EMTG_NASA_PYEMTG to the pinned NASA PyEMTG checkout')
    assert (Path(nasa)/'MissionOptions.py').is_file(), 'requested NASA parser qualification assets are missing'
    options = MissionOptions()
    source, destination = tmp_path/'source.emtgopt', tmp_path/'nasa.emtgopt'
    options.write_options_file(str(source), writeAll=True)
    export_nasa_options(source, destination, solver='SNOPT')
    code = "import sys; sys.path.insert(0,sys.argv[1]); import MissionOptions; o=MissionOptions.MissionOptions(sys.argv[2]); assert o.success; assert o.NLP_solver_type==0; assert o.snopt_feasibility_tolerance==1e-5; assert len(o.Journeys)==1"
    subprocess.run([sys.executable, '-I', '-c', code, nasa, str(destination)], check=True, cwd=tmp_path)
