# Windows setup and reproducibility

Use a short checkout such as `C:\projects\emtg`. Git for Windows and
PowerShell 5.1+ must already be available. Prebuilt ZIP users need neither
Python nor compilers: extract the ZIP, run `bin\EMTGv9.exe --doctor`, and
provide the mission's required BSP kernels. The bounded test kernel is not
a general-purpose mission ephemeris.

## Portable Python and tools

If Windows PowerShell blocks scripts, open a temporary session with
`powershell.exe -NoProfile -ExecutionPolicy Bypass` for the setup commands.
This changes no machine policy. Afterwards `build.cmd` and `qualify.cmd`
provide the same process-only behavior for builds and qualification.
The wrappers also isolate Windows PowerShell's module path from an enclosing
PowerShell 7 session; otherwise even standard commands such as `Get-FileHash`
can resolve against an incompatible module. The caller's environment is unchanged.

Run from the repository root; these commands change only session PATH.
Official NuGet CPython includes pip and avoids MSI registration, also
recovering the `core.msi`/`0x80070003` failure observed during evaluation.
This recipe qualifies CPython 3.12.10. CI separately checks Python 3.10 with
NumPy 1.26; untested Python versions are not release evidence.

```powershell
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
New-Item -ItemType Directory -Force _local/downloads,_local/tools,_local/setup | Out-Null
$Archive = '_local/downloads/python.3.12.10.zip'
if (-not (Test-Path $Archive)) {
    Invoke-WebRequest https://www.nuget.org/api/v2/package/python/3.12.10 -OutFile $Archive
}
if ((Get-FileHash $Archive -Algorithm SHA256).Hash -ne '0EB85C2DFCCCCF1B17352DE4C397F69194035B7D37149EACC16F1147D93DE3B8') {
    throw 'Python archive checksum mismatch'
}
if (-not (Test-Path '_local/tools/python-nuget')) {
    Expand-Archive $Archive _local/tools/python-nuget
}
. .\scripts\windows-environment.ps1
Initialize-EmtgLocalTools
python -m pip install -r requirements-qualification.txt 2>&1 | Tee-Object _local/setup/python-packages.log
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed' }
python -m pip freeze | Set-Content _local/setup/python-freeze.txt
```

The qualification constraints pin direct and transitive packages, including
CMake 3.31.6, Ninja 1.11.1.4 and Matplotlib. `requirements-dev.txt` remains
available for compatibility testing with newer dependencies.

To retain an explicit wheelhouse for this Windows/Python version, download it
while online and record hashes. A fresh portable runtime can then install the
same packages with `--no-index`; other Python versions/platforms need their
own wheels.

```powershell
python -m pip download --only-binary=:all: -r requirements-qualification.txt --dest _local/qualification-wheels
Get-ChildItem _local/qualification-wheels -File | Get-FileHash -Algorithm SHA256 |
    Select-Object Path,Hash | ConvertTo-Json | Set-Content _local/setup/wheel-hashes.json
python -m pip install --no-index --find-links _local/qualification-wheels -r requirements-qualification.txt
python -m pip check
```

## Visual Studio 2022

Skip installation when preflight finds VS 2022 C++ tools and the SDK.
Otherwise use this official installer; Windows may require elevation.
Microsoft's shared installer, SDK, registry entries and package cache remain
outside EMTG even with a local install path. Do not delete shared components
when cleaning this checkout.

```powershell
$Installer = '_local/downloads/vs_BuildTools.exe'
if (-not (Test-Path $Installer)) {
    Invoke-WebRequest https://aka.ms/vs/17/release/vs_BuildTools.exe -OutFile $Installer
}
$Signature = Get-AuthenticodeSignature $Installer
if ($Signature.Status -ne 'Valid' -or $Signature.SignerCertificate.Subject -notmatch 'Microsoft Corporation') {
    throw 'Expected a valid Microsoft installer signature'
}
Get-FileHash $Installer -Algorithm SHA256 | Format-List | Out-File _local/setup/vs-installer-hash.txt
$InstallPath = Join-Path (Get-Location) '_local/tools/vs2022'
$Install = Start-Process $Installer -WindowStyle Hidden -Wait -PassThru -ArgumentList @(
    '--quiet', '--wait', '--norestart', '--installPath', "`"$InstallPath`"",
    '--add', 'Microsoft.VisualStudio.Workload.VCTools', '--includeRecommended'
)
Copy-Item "$env:TEMP/dd_*" _local/setup -ErrorAction SilentlyContinue
if ($Install.ExitCode -notin @(0,3010)) { throw "VS installation failed: $($Install.ExitCode)" }
if ($Install.ExitCode -eq 3010) { throw 'Restart Windows, then resume setup' }
Get-EmtgVisualStudio | ConvertTo-Json -Depth 5 | Set-Content _local/setup/visual-studio.json
Assert-EmtgPrerequisites
.\scripts\qualify-windows.ps1
```

The VS 17 URL is a serviced channel, not an immutable version pin. The
evaluated installer produced VS 17.14.41 (installationVersion 17.14.37710.0).
Preserve its installer/hash and metadata; use a Microsoft offline layout or
VM snapshot for exact reinstallation. The managed compiler graph is pinned
independently by vcpkg.

## Time, disk, CPU and offline use

The portable dependency rebuild took 54 minutes 17 seconds on the evaluated
Ryzen 7 6800H, using retained downloads; OpenBLAS alone took about 32 minutes.
A clean `windows-2022` CI runner needed about 96 minutes for build, release
tests and packaging. Debug and Release dependency variants are intentional.
Four parallel AEPS cases add about 20 minutes on that eight-core machine.
Release CI uses `qualify.cmd -SkipBuild -Workers 2` on the evaluated two-core
runner, adding about 40 minutes. Four workers there exhausted the unchanged
wall-clock budget before the nearby cases became feasible; the same package
passed all four cases with two workers. Use `-Workers 2` on similarly small
machines; the per-case solver limits and scientific assertions stay unchanged.
Cached local builds take roughly
40–55 seconds, including tests, packaging and audits.
Another cold hosted build took 126 minutes 8 seconds. The Windows CI job allows four
hours overall to accommodate cold-build variation plus qualification; its
per-mission solver budgets remain unchanged.

Retaining the tools, both cache generations, extra Python environments and
qualification evidence used about 10.5 GiB under `_local`, plus 0.8 GiB for
the checkout/Git/distribution files. These are retained sizes, not measured
peak requirements. Allow 20 GiB in the checkout as a planning margin and
additional space for Microsoft's shared components and installer cache.
The portable EXE is about 45.5 MB and its ZIP about 15.4 MB.

Windows OpenBLAS uses runtime dispatch with CORE2 common code, which does
not require AVX. Host-only OpenBLAS build tools are not shipped. Record both
native CPU and non-AVX emulation evidence before claiming CPU portability;
forcing `OPENBLAS_CORETYPE` alone does not establish it. The LP64 interface
and threading policy are unchanged.

`build.ps1 -Offline` requires a complete matching online build and caches;
it rejects an older CPU-specific OpenBLAS cache and asks for an online rebuild.
It is not an air-gapped fresh installer. Versions and the dependency graph
are recorded in `dist/build-toolchain.txt` and SPDX. Qualification captures
stdout/stderr explicitly because transcripts alone can miss native output.
Tools, caches, logs and results stay under `_local`; copying it does not
reproduce shared Visual Studio/SDK state.

The MinGW cache tracks the selected compiler and build tools, but excludes
incidental session PATH changes from its ABI key. This avoids a complete
dependency rebuild when switching between PowerShell 5.1 and 7 or repeatedly
initializing the local environment. Use the managed compiler/tool recipe;
arbitrary tools substituted through PATH are not a qualified build setup.

The initial `vcpkg-gfortran` bootstrap bypasses binary caching: its binary
package contains runtime DLLs, while running its port acquires the compiler.
A fresh machine must execute that acquisition even when compiled library
packages are cached. The main dependency graph still uses the binary cache.

## Non-AVX execution check

Download the Windows Intel SDE kit from Intel's official download page into
`_local/tools`, preserving its license and published checksum. Version
10.13.1 (2026-07-28) was used for this evaluation. SDE is never part of the
distributed EMTG bundle. Select the materialized Earth-Mars `.emtgopt` from
the completed bounded qualification, whose hardware/kernel paths are local:

```powershell
python scripts/check-cpu-portability.py `
  --sde _local/tools/sde-external-10.13.1-2026-07-28-win/sde.exe `
  --executable _local/q0001/relocated/EMTG-9.2.0-Windows-AMD64/bin/EMTGv9.exe `
  --options <materialized-Earth-Mars.emtgopt> --output _local/cpu-check
```

Substitute the actual qualification directory and options path. The output
directory must be new. This sets Nehalem CPUID, fails on unsupported
instructions, preserves the mission's solver budget/tolerance, checks its
feasibility, and records the EXE hash and solver output. A successful `--version`
alone is insufficient; a solver workload must pass.
