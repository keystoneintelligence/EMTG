"""Native stderr must be logged without changing a successful exit status."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell native streams")
def test_qualification_preserves_exit_codes_and_powershell_errors(tmp_path):
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"

    emitter = tmp_path / "emit.py"
    emitter.write_text(
        "import sys\nprint('native-warning', file=sys.stderr)\n"
        "print('native-output')\nsys.exit(int(sys.argv[1]))\n"
    )
    script = tmp_path / "check.ps1"
    script.write_text(f"""
$ErrorActionPreference = 'Stop'
$RunRoot = {quote(tmp_path)}
$Python = {quote(sys.executable)}
$Emitter = {quote(emitter)}
$Checks = [System.Collections.Generic.List[object]]::new()
$Ast = [System.Management.Automation.Language.Parser]::ParseFile(
    {quote(ROOT / 'scripts/qualify-windows.ps1')}, [ref]$null, [ref]$null)
$Function = $Ast.Find({{ param($Node)
    $Node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $Node.Name -eq 'Invoke-QualificationCheck'
}}, $true)
Invoke-Expression $Function.Extent.Text
Invoke-QualificationCheck 'warning' {{ & $Python $Emitter 0 }}
Invoke-QualificationCheck 'failure' {{ & $Python $Emitter 7 }}
Invoke-QualificationCheck 'script-error' {{ Get-Item -LiteralPath (Join-Path $RunRoot 'missing-file') }}
if ($Checks[0].exit_code -ne 0 -or $Checks[1].exit_code -ne 7 -or $Checks[2].exit_code -ne 1) {{
    throw ($Checks | ConvertTo-Json)
}}
$Log = Get-Content (Join-Path $RunRoot 'warning.log') -Raw
if ($Log -notmatch 'native-warning' -or $Log -notmatch 'native-output') {{ throw 'Missing native output' }}
if ($ErrorActionPreference -ne 'Stop') {{ throw 'Caller error preference was changed' }}
""")
    env = dict(os.environ)
    env.pop("PSMODULEPATH", None)
    run = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert run.returncode == 0, run.stdout + run.stderr
