"""Broken native version queries must not stop the prerequisite inventory."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell prerequisite checks")
def test_preflight_aggregates_failing_native_version_queries(tmp_path):
    def quote(path):
        return "'" + str(path).replace("'", "''") + "'"

    stub = tmp_path / "broken-tool.cmd"
    stub.write_text("@echo off\necho simulated unusable prerequisite 1>&2\nexit /b 42\n")
    script = tmp_path / "preflight.ps1"
    script.write_text(f"""
$ErrorActionPreference = 'Stop'
. {quote(ROOT / 'scripts/windows-environment.ps1')}
foreach ($Name in @('cmake', 'ninja', 'python')) {{ Set-Alias $Name {quote(stub)} }}
try {{
    Assert-EmtgPrerequisites
    throw 'Unexpected preflight success'
}} catch {{
    $Message = $_.Exception.Message
    if ($Message -notmatch 'Missing or incompatible prerequisites:' -or
        $Message -notmatch 'cmake 3.25' -or $Message -notmatch 'ninja 1.10' -or
        $Message -notmatch 'python 3.10') {{ throw $Message }}
}}
if ($ErrorActionPreference -ne 'Stop') {{ throw 'Caller error preference changed' }}
""")
    env = dict(os.environ)
    env.pop("PSMODULEPATH", None)
    run = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert run.returncode == 0, run.stdout + run.stderr
