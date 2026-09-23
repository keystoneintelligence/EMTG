# Shared session-only environment setup. No machine PATH or registry changes.
# Windows PowerShell 5.1 supplies Get-FileHash as a module function. Make it
# available before entering nested build/logging scopes, not after a long build.
Import-Module Microsoft.PowerShell.Utility -Global -ErrorAction Stop
$EmtgRoot = Split-Path $PSScriptRoot -Parent

function Initialize-EmtgLocalTools {
    $PythonRoot = Join-Path $EmtgRoot '_local\tools\python-nuget\tools'
    if (Test-Path (Join-Path $PythonRoot 'python.exe')) {
        $env:PATH = "$PythonRoot;$PythonRoot\Scripts;$env:PATH"
    }
    $env:PIP_CACHE_DIR = Join-Path $EmtgRoot '_local\pip-cache'
}

function Get-EmtgVisualStudio {
    $VsWhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path $VsWhere) {
        $Json = & $VsWhere -latest -products * -version '[17.0,18.0)' `
            -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -format json
        if ($LASTEXITCODE -eq 0 -and $Json) {
            @($Json | ConvertFrom-Json) | Select-Object -First 1
        }
    }
}

function Assert-EmtgPrerequisites {
    $Problems = @()
    if ($PSVersionTable.PSVersion -lt [version]'5.1') { $Problems += 'PowerShell 5.1+' }
    foreach ($Tool in @('git', 'cmake', 'ctest', 'cpack', 'ninja', 'python')) {
        if (-not (Get-Command $Tool -ErrorAction SilentlyContinue)) { $Problems += "$Tool on PATH" }
    }
    foreach ($Check in @(@('cmake', '3.25'), @('ninja', '1.10'), @('python', '3.10'))) {
        if (Get-Command $Check[0] -ErrorAction SilentlyContinue) {
            $VersionText = (& $Check[0] --version 2>&1 | Out-String)
            if ($LASTEXITCODE -ne 0 -or $VersionText -notmatch '(\d+\.\d+(?:\.\d+)?)' -or
                [version]$Matches[1] -lt [version]$Check[1]) {
                $Problems += "$($Check[0]) $($Check[1])+ (found: $($VersionText.Trim()))"
            }
        }
    }
    if (-not (Get-EmtgVisualStudio)) { $Problems += 'Visual Studio 2022 C++ Build Tools' }
    $Kits = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\Lib'
    if (-not (Get-ChildItem "$Kits\*\um\x64\kernel32.lib" -ErrorAction SilentlyContinue)) {
        $Problems += 'Windows SDK x64 libraries'
    }
    if ($Problems) {
        throw "Missing or incompatible prerequisites:`n - $($Problems -join "`n - ")`nSee BUILDING.md (Windows setup)."
    }
}

function Initialize-EmtgVisualStudio {
    $Vs = Get-EmtgVisualStudio
    if (-not $Vs) { throw 'Visual Studio 2022 C++ Build Tools were not found. See BUILDING.md.' }
    $OriginalPath = $env:PATH
    $VsDevCmd = Join-Path $Vs.installationPath 'Common7\Tools\VsDevCmd.bat'
    $Environment = cmd.exe /d /s /c "`"$VsDevCmd`" -arch=x64 -host_arch=x64 >nul && set"
    if ($LASTEXITCODE -ne 0) { throw 'Visual Studio environment initialization failed' }
    $Environment | ForEach-Object {
        if ($_ -match '^([^=]+)=(.*)$') { Set-Item -Path "Env:$($Matches[1])" -Value $Matches[2] }
    }
    $env:PATH = "$env:PATH;$OriginalPath"
}

function Get-EmtgMingwCompiler {
    param([string]$VcpkgRoot = $(if ($env:VCPKG_ROOT) { $env:VCPKG_ROOT } else { Join-Path $EmtgRoot '_local\tools\vcpkg' }))
    Get-ChildItem (Join-Path $VcpkgRoot 'downloads\tools\msys2') -Filter g++.exe -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '[\\/]mingw64[\\/]bin[\\/]g\+\+\.exe$' } |
        Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
}

function Initialize-EmtgMingw {
    param([string]$VcpkgRoot = $(if ($env:VCPKG_ROOT) { $env:VCPKG_ROOT } else { Join-Path $EmtgRoot '_local\tools\vcpkg' }))
    $Compiler = Get-EmtgMingwCompiler $VcpkgRoot
    if (-not $Compiler) { throw 'Managed MinGW is missing. Run build.ps1 once before fast tests.' }
    $env:EMTG_MINGW_ROOT = (Split-Path $Compiler.DirectoryName -Parent) -replace '\\', '/'
    $env:PATH = "$($Compiler.DirectoryName);$env:PATH"
    $env:CC = Join-Path $Compiler.DirectoryName 'gcc.exe'
    $env:CXX = $Compiler.FullName
}
