"""Run a materialized mission under Intel SDE's non-AVX Nehalem CPU model.

The separately downloaded SDE kit is a qualification tool, never bundled.
This checks the packaged executable with its statically linked dependencies.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--sde', type=Path, required=True)
parser.add_argument('--executable', type=Path, required=True)
parser.add_argument('--options', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
for name in ('sde', 'executable', 'options'):
    if not getattr(args, name).is_file():
        parser.error(f'--{name} must name an existing file: {getattr(args, name)}')
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'PyEMTG'))
from MissionOptions import MissionOptions
from Results import EMTGResultParser

output = args.output.resolve()
output.mkdir(parents=True, exist_ok=False)
options = MissionOptions(str(args.options.resolve()))
assert options.success
options.override_working_directory = 1
options.forced_working_directory = output.as_posix()
options.override_mission_subfolder = 1
options.forced_mission_subfolder = '.'
options.mission_name = 'non_avx'
options.short_output_file_names = 1
options.background_mode = 1
input_path = output / 'non_avx.emtgopt'
options.write_options_file(str(input_path), True)
executable = args.executable.resolve()
command = [str(args.sde.resolve()), '-nhm', '-chip_check_die', '1', '--', str(executable), str(input_path)]
with (output / 'solver.log').open('w') as log:
    run = subprocess.run(command, cwd=output, stdout=log, stderr=subprocess.STDOUT,
                         timeout=max(300, options.NLP_max_run_time + 120))
report = {'command': command, 'returncode': run.returncode,
          'executable_sha256': hashlib.sha256(executable.read_bytes()).hexdigest()}
if run.returncode == 0:
    result = EMTGResultParser().parse(output / 'non_avx.emtg')
    report.update(complete=result.complete, feasible=result.feasible,
                  violation=result.violation, delivered_mass=result.metrics.get('delivered_mass'))
(output / 'cpu-check.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
assert run.returncode == 0, (output / 'solver.log').read_text(errors='replace')[-3000:]
assert result.complete and result.feasible
assert result.violation is not None and result.violation <= options.NLP_feasibility_tolerance
assert result.metrics['delivered_mass'] > 0
