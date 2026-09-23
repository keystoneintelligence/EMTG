"""Batch entry points must not mix PowerShell 7 and Windows PowerShell modules."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != "nt", reason="Windows batch entry points")
@pytest.mark.parametrize("launcher,script", [
    ("build.cmd", "build.ps1"),
    ("qualify.cmd", "scripts/qualify-windows.ps1"),
])
def test_batch_launcher_uses_windows_powershell_modules(tmp_path, launcher, script):
    shutil.copy2(ROOT / launcher, tmp_path / launcher)
    script_path = tmp_path / script
    script_path.parent.mkdir(parents=True, exist_ok=True)
    payload = b"EMTG launcher module isolation\n"
    (script_path.parent / "probe.txt").write_bytes(payload)
    script_path.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "(Get-FileHash (Join-Path $PSScriptRoot 'probe.txt') -Algorithm SHA256).Hash\n"
    )
    # Model an inherited incompatible module without depending on a PS7 install.
    modules = tmp_path / "foreign-modules"
    utility = modules / "Microsoft.PowerShell.Utility"
    utility.mkdir(parents=True)
    (utility / "Microsoft.PowerShell.Utility.psd1").write_text(
        "@{ RootModule='foreign.psm1'; ModuleVersion='99.0'; "
        "FunctionsToExport=@('Get-FileHash') }\n"
    )
    (utility / "foreign.psm1").write_text(
        "function Get-FileHash { throw 'Inherited foreign module was loaded' }\n"
    )
    env = dict(os.environ, PSMODULEPATH=str(modules))
    run = subprocess.run(
        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", launcher],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip().lower() == hashlib.sha256(payload).hexdigest()
