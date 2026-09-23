[CmdletBinding()]
param([switch]$Fast, [switch]$SkipBuild, [ValidateRange(1,4)][int]$Workers = 4)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'windows-environment.ps1')
Initialize-EmtgLocalTools
Assert-EmtgPrerequisites
$SavedUniverse = $env:EMTG_TEST_UNIVERSE
$SavedIntegration = $env:EMTG_RUN_OUTERLOOP_INTEGRATION
$SavedBudget = $env:EMTG_ATLAS_AEPS_BUDGET_SECONDS
$SavedRelease = $env:EMTG_RELEASE_ROOT
$Checks = [System.Collections.Generic.List[object]]::new()
for ($Index = 1; $Index -le 9999; $Index++) {
    $RunRoot = Join-Path $EmtgRoot ('_local\q{0:d4}' -f $Index)
    if (-not (Test-Path $RunRoot)) { break }
}
if ($Index -gt 9999) { throw 'No unused short qualification directory remains' }
New-Item -ItemType Directory $RunRoot | Out-Null

function Invoke-QualificationCheck {
    param([string]$Name, [scriptblock]$Command)
    Write-Host "Qualification: $Name"
    $Timer = [Diagnostics.Stopwatch]::StartNew()
    $Log = Join-Path $RunRoot "$Name.log"
    $Code = 0
    try {
        $global:LASTEXITCODE = 0
        & $Command 2>&1 | Tee-Object -FilePath $Log
        $Code = $LASTEXITCODE
    } catch {
        $_ | Out-String | Tee-Object -FilePath $Log -Append | Write-Host
        $Code = 1
    }
    $Checks.Add([pscustomobject]@{name=$Name; exit_code=$Code; seconds=$Timer.Elapsed.TotalSeconds})
}

Push-Location $EmtgRoot
try {
    if (-not $Fast -and -not $SkipBuild) {
        Invoke-QualificationCheck 'build' { & (Join-Path $EmtgRoot 'build.ps1') }
        if ($Checks[-1].exit_code -ne 0) { throw 'Build failed; see the recorded build log' }
    }
    Initialize-EmtgMingw
    $env:EMTG_RUN_OUTERLOOP_INTEGRATION = $null
    $env:EMTG_RELEASE_ROOT = $null
    Invoke-QualificationCheck 'python' {
        python -m pytest -m 'not emtg_integration' --basetemp "$RunRoot\p" --junitxml="$RunRoot\python.xml" -ra
    }
    Invoke-QualificationCheck 'fast-configure' { cmake --preset ci-fast --fresh }
    if ($Checks[-1].exit_code -eq 0) {
        Invoke-QualificationCheck 'fast-build' { cmake --build --preset ci-fast }
        if ($Checks[-1].exit_code -eq 0) {
            Invoke-QualificationCheck 'fast-ctest' { ctest --preset ci-fast --output-junit "$RunRoot\fast.xml" }
        }
    }
    if (-not $Fast) {
        Invoke-QualificationCheck 'release-ctest' { ctest --preset windows-release --output-junit "$RunRoot\release.xml" }
        New-Item -ItemType Directory -Force bin | Out-Null
        Copy-Item _local\builds\windows-release\bin\EMTGv9.exe bin\EMTGv9.exe -Force
        Invoke-QualificationCheck 'universe' { python scripts/prepare_test_ephemeris.py --output "$RunRoot\u" }
        if ($Checks[-1].exit_code -ne 0) { throw 'Test universe staging failed' }
        $env:EMTG_TEST_UNIVERSE = "$RunRoot\u"
        $env:EMTG_RUN_OUTERLOOP_INTEGRATION = '1'
        $env:EMTG_ATLAS_AEPS_BUDGET_SECONDS = '1200'
        Invoke-QualificationCheck 'bounded-native' {
            python -m pytest tests/test_outerloop_emtg_integration.py -k 'not aeps_real_atlas' --basetemp "$RunRoot\n" --junitxml="$RunRoot\bounded-native.xml" -ra
        }
        Invoke-QualificationCheck 'aeps' {
            python -m pytest tests/test_outerloop_emtg_integration.py::test_aeps_real_atlas_baseline_and_nearby_hardware_matrix -n $Workers --basetemp "$RunRoot\a" --junitxml="$RunRoot\aeps.xml" -ra
        }
        $Archives = @(Get-ChildItem dist\EMTG-*.zip)
        if ($Archives.Count -ne 1) { throw 'Expected exactly one release ZIP in dist' }
        Expand-Archive -LiteralPath $Archives[0].FullName -DestinationPath "$RunRoot\relocated"
        $Executables = @(Get-ChildItem "$RunRoot\relocated" -Filter EMTGv9.exe -Recurse)
        if ($Executables.Count -ne 1) { throw 'Expected exactly one extracted EMTG executable' }
        $env:EMTG_RELEASE_ROOT = Split-Path (Split-Path $Executables[0].FullName)
        Invoke-QualificationCheck 'package' {
            python -m pytest tests/test_packaged_runtime.py --basetemp "$RunRoot\r" --junitxml="$RunRoot\package.xml" -ra
        }
        Invoke-QualificationCheck 'dll-audit' { & "$PSScriptRoot\audit-windows-dependencies.ps1" -Executable $Executables[0].FullName }
        Invoke-QualificationCheck 'path-audit' { python scripts/audit-release-paths.py dist --forbid-root $EmtgRoot }
        Invoke-QualificationCheck 'offline-build' { & (Join-Path $EmtgRoot 'build.ps1') -Offline }
    }
} finally {
    $Checks | ConvertTo-Json -Depth 4 | Set-Content "$RunRoot\commands.json" -Encoding utf8
    & python "$PSScriptRoot\summarize-qualification.py" $RunRoot
    & python -m pip freeze | Set-Content "$RunRoot\python-freeze.txt" -Encoding utf8
    & git rev-parse HEAD | Set-Content "$RunRoot\source-commit.txt" -Encoding ascii
    $env:EMTG_TEST_UNIVERSE = $SavedUniverse
    $env:EMTG_RUN_OUTERLOOP_INTEGRATION = $SavedIntegration
    $env:EMTG_ATLAS_AEPS_BUDGET_SECONDS = $SavedBudget
    $env:EMTG_RELEASE_ROOT = $SavedRelease
    Pop-Location
    Write-Host "Qualification evidence: $RunRoot"
}
if ($Checks | Where-Object { $_.exit_code -ne 0 }) { exit 1 }
