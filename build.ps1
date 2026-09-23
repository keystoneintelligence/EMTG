[CmdletBinding()]
param(
    [switch]$Offline,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Local = Join-Path $Root '_local'
$Dist = Join-Path $Root 'dist'
$Vcpkg = if ($env:VCPKG_ROOT) { $env:VCPKG_ROOT } else { Join-Path $Local 'tools\vcpkg' }
$Version = (Get-Content (Join-Path $Root 'VERSION') -Raw).Trim()
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Invalid EMTG version in VERSION: '$Version'" }

. (Join-Path $Root 'scripts\windows-environment.ps1')
Initialize-EmtgLocalTools
Assert-EmtgPrerequisites
Initialize-EmtgVisualStudio
$CmakeVersionText = (& cmake --version | Select-Object -First 1)
$NinjaVersion = (& ninja --version | Out-String).Trim()
New-Item -ItemType Directory -Force -Path $Local, $Dist | Out-Null

$Preset = 'windows-release'
if (-not (Test-Path (Join-Path $Vcpkg '.git'))) {
    if ($Offline) { throw "Offline build requested but vcpkg is missing at $Vcpkg" }
    if (Test-Path $Vcpkg) { throw "Incomplete vcpkg checkout at $Vcpkg; remove it and retry" }
    & git clone --branch 2025.06.13 --depth 1 https://github.com/microsoft/vcpkg.git $Vcpkg
    if ($LASTEXITCODE -ne 0) { throw 'Failed to download the pinned vcpkg checkout' }
}
$ExpectedVcpkg = (Get-Content (Join-Path $Root 'cmake/vcpkg-revision.txt') -Raw).Trim()
$ActualVcpkg = (& git -C $Vcpkg rev-parse HEAD | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $ActualVcpkg -ne $ExpectedVcpkg) { throw "vcpkg revision mismatch: expected $ExpectedVcpkg, found $ActualVcpkg" }
& git -C $Vcpkg diff --quiet HEAD --
if ($LASTEXITCODE -ne 0) { throw 'vcpkg tracked sources are modified' }
if (-not (Test-Path (Join-Path $Vcpkg 'vcpkg.exe'))) {
    if ($Offline) { throw "Offline build requested but vcpkg has not been bootstrapped" }
    & (Join-Path $Vcpkg 'bootstrap-vcpkg.bat') -disableMetrics
    if ($LASTEXITCODE -ne 0) { throw 'Failed to bootstrap vcpkg' }
}
$env:VCPKG_ROOT = $Vcpkg
$Cache = Join-Path $Local 'vcpkg-cache'
New-Item -ItemType Directory -Force -Path $Cache | Out-Null
$env:VCPKG_BINARY_SOURCES = "clear;files,$Cache,readwrite"

# Install the manifest first. This also bootstraps vcpkg's pinned MinGW
# toolchain, which must exist before CMake performs compiler detection.
$VcpkgInstall = Join-Path $Local 'builds\windows-release\vcpkg_installed'
$MingwCompiler = Get-EmtgMingwCompiler $Vcpkg
if (-not $MingwCompiler) {
    if ($Offline) { throw 'Offline build requires the cached MinGW compiler; run an online build first' }
    & (Join-Path $Vcpkg 'vcpkg.exe') install vcpkg-gfortran:x64-windows `
        --x-install-root=$VcpkgInstall `
        --classic
    if ($LASTEXITCODE -ne 0) { throw 'Failed to provision the pinned MinGW-w64 compiler' }
    $MingwCompiler = Get-EmtgMingwCompiler $Vcpkg
}
if (-not $MingwCompiler) { throw 'vcpkg did not provision the pinned MinGW-w64 compiler' }
Initialize-EmtgMingw $Vcpkg
$CompilerVersion = (& $MingwCompiler -dumpfullversion | Out-String).Trim()
if (-not $CompilerVersion) { throw 'Unable to determine the pinned MinGW-w64 compiler version' }
$OverlayPorts = Join-Path $Root 'cmake\vcpkg-overlays'

if ($Offline) {
    $TripletRoot = Join-Path $VcpkgInstall 'x64-mingw-static'
    $RequiredCachedAssets = @(
        (Join-Path $TripletRoot 'include\boost\version.hpp'),
        (Join-Path $TripletRoot 'include\coin-or\IpStdCInterface.h'),
        (Join-Path $TripletRoot 'include\cspice\SpiceUsr.h'),
        (Join-Path $TripletRoot 'lib\libipopt.a'),
        (Join-Path $TripletRoot 'lib\libcoinmumps.a'),
        (Join-Path $TripletRoot 'lib\libcspice.a'),
        (Join-Path $TripletRoot 'lib\liblapack.a'),
        (Join-Path $TripletRoot 'lib\libopenblas.a')
    )
    $MissingCachedAssets = $RequiredCachedAssets | Where-Object { -not (Test-Path $_) }
    if ($MissingCachedAssets) {
        throw "Offline build cache is incomplete. Missing:`n$($MissingCachedAssets -join "`n")"
    }
} else {
    & (Join-Path $Vcpkg 'vcpkg.exe') install `
        --triplet x64-mingw-static `
        "--x-manifest-root=$Root" `
        "--x-install-root=$VcpkgInstall" `
        "--overlay-ports=$OverlayPorts"
    if ($LASTEXITCODE -ne 0) { throw 'Failed to install the pinned managed dependency graph' }
}

# In particular, -Offline must not silently reuse a pre-portability BLAS cache.
$InstalledStatus = Get-Content (Join-Path $VcpkgInstall 'vcpkg/status') -Raw
$DynamicBlas = $InstalledStatus -split '\r?\n\r?\n' | Where-Object {
    $_ -match '(?m)^Package: openblas\r?$' -and $_ -match '(?m)^Feature: dynamic-arch\r?$' -and
    $_ -match '(?m)^Architecture: x64-mingw-static\r?$' -and $_ -match '(?m)^Status: install ok installed\r?$'
}
$BlasConfig = Get-Content (Join-Path $VcpkgInstall 'x64-mingw-static/include/openblas/openblas_config.h') -Raw
if (-not $DynamicBlas -or $BlasConfig -notmatch '#define OPENBLAS_CORE_CORE2\b') {
    throw 'Portable OpenBLAS cache is missing or stale. Run an online build before using -Offline.'
}

@(
    "vcpkg_commit=$ActualVcpkg"
    "cmake=$CmakeVersionText"
    "ninja=$NinjaVersion"
    "python=$((& python --version | Out-String).Trim())"
    "visual_studio=$((Get-EmtgVisualStudio).installationVersion)"
    "compiler=$($MingwCompiler.Name)"
    "compiler_version=$CompilerVersion"
    (Get-Content (Join-Path $VcpkgInstall 'vcpkg/status') -Raw)
) | Set-Content -Encoding utf8 (Join-Path $Dist 'build-toolchain.txt')

if ($SkipTests) {
    & cmake --preset $Preset --fresh
    if ($LASTEXITCODE -ne 0) { throw 'CMake configure failed' }
    & cmake --build --preset $Preset --target EMTGv9
    if ($LASTEXITCODE -ne 0) { throw 'CMake build failed' }
    & cpack --preset $Preset
    if ($LASTEXITCODE -ne 0) { throw 'CPack failed' }
} else {
    & cmake --preset $Preset --fresh
    if ($LASTEXITCODE -ne 0) { throw 'CMake configure failed' }
    & cmake --build --preset $Preset
    if ($LASTEXITCODE -ne 0) { throw 'CMake build failed' }
    & ctest --preset $Preset
    if ($LASTEXITCODE -ne 0) { throw 'CTest failed' }
    & cpack --preset $Preset
    if ($LASTEXITCODE -ne 0) { throw 'CPack failed' }
}

$Build = Join-Path $Local "builds\$Preset"
$Executable = Join-Path $Build 'bin\EMTGv9.exe'
if (-not (Test-Path $Executable)) { throw "Expected executable was not produced: $Executable" }
$ReportedVersion = (& $Executable --version | Out-String).Trim()
if ($ReportedVersion -ne "EMTG $Version") {
    throw "Built executable reports '$ReportedVersion', expected 'EMTG $Version'"
}
& (Join-Path $Root 'scripts\audit-windows-dependencies.ps1') -Executable $Executable
$Standalone = Join-Path $Dist 'EMTGv9-windows-x64.exe'
Copy-Item $Executable $Standalone -Force
"{0}  {1}" -f (Get-FileHash $Standalone -Algorithm SHA256).Hash.ToLowerInvariant(), (Split-Path $Standalone -Leaf) |
    Set-Content -Encoding ascii "$Standalone.sha256"
Copy-Item (Join-Path $Root 'THIRD_PARTY_NOTICES.md') $Dist -Force
$RuntimeLicenseRoot = Join-Path (Split-Path $MingwCompiler.DirectoryName -Parent) 'share\licenses'
$ReleaseRuntimeNotices = @{
    'gcc-libs\COPYING3' = 'GCC-GPL-3.0.txt'
    'gcc-libs\COPYING.RUNTIME' = 'GCC-Runtime-Library-Exception-3.1.txt'
    'gcc-libs\COPYING.LIB' = 'GCC-LGPL.txt'
    'gcc-libs\README' = 'GCC-runtime-license-summary.txt'
    'crt\COPYING.MinGW-w64-runtime.txt' = 'MinGW-w64-runtime-license.txt'
    'crt\COPYING.MinGW-w64.txt' = 'MinGW-w64-license.txt'
    'libwinpthread\COPYING' = 'MinGW-w64-libwinpthread-license.txt'
}
foreach ($Notice in $ReleaseRuntimeNotices.GetEnumerator()) {
    $Source = Join-Path $RuntimeLicenseRoot $Notice.Key
    if (-not (Test-Path $Source)) { throw "Managed compiler runtime notice is missing: $Source" }
    Copy-Item $Source (Join-Path $Dist $Notice.Value) -Force
}
Get-ChildItem $Build -File | Where-Object { $_.Name -match '\.(zip|sha256)$' } | Copy-Item -Destination $Dist -Force
& cmake `
    "-DSTATUS_FILE=$VcpkgInstall\vcpkg\status" `
    "-DOUTPUT=$Dist\EMTG-windows-x64.spdx" `
    "-DEMTG_VERSION=$Version" `
    '-DCOMPILER_RUNTIME_NAME=GNU-MinGW-w64-runtime' `
    "-DCOMPILER_RUNTIME_VERSION=$CompilerVersion" `
    -DPLATFORM=windows-x64 `
    -P (Join-Path $Root 'cmake\GenerateVcpkgSbom.cmake')
if ($LASTEXITCODE -ne 0) { throw 'Failed to generate the dependency SBOM' }

& python (Join-Path $Root 'scripts/audit-release-paths.py') $Dist --forbid-root $Root --forbid-root $Vcpkg
if ($LASTEXITCODE -ne 0) { throw 'Release contains local build paths' }

Write-Host "EMTG artifacts: $Dist"
