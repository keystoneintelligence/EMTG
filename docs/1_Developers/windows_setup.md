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
if ($Install.ExitCode -notin @(0,3010)) { throw "VS installation failed: $($Install.ExitCode)" }
Copy-Item "$env:TEMP/dd_*" _local/setup -ErrorAction SilentlyContinue
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

The original cold build took about 39 minutes and 7.2 GiB under `_local`,
excluding shared Microsoft components. Dynamic OpenBLAS kernels increase
build cost and size; allow additional disk/time. Debug and Release dependency
variants are intentional. Four parallel AEPS cases add about 20 minutes on
the evaluated 8-core host.

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
