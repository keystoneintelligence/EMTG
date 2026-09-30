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
$StagedExecutable = Join-Path $EmtgRoot 'bin/EMTGv9.exe'
$HadExecutable = Test-Path $StagedExecutable
$ReceiptName = 'EMTG-windows-x64.provenance.json'
$Checks = [System.Collections.Generic.List[object]]::new()
for ($Index = 1; $Index -le 9999; $Index++) {
    $RunRoot = Join-Path $EmtgRoot ('_local\q{0:d4}' -f $Index)
    if (-not (Test-Path $RunRoot)) { break }
}
if ($Index -gt 9999) { throw 'No unused short qualification directory remains' }
New-Item -ItemType Directory $RunRoot | Out-Null
if ($HadExecutable) { Copy-Item $StagedExecutable (Join-Path $RunRoot 'previous-executable.bin') }

function Invoke-QualificationCheck {
    param([string]$Name, [scriptblock]$Command)
    Write-Host "Qualification: $Name"
    $Timer = [Diagnostics.Stopwatch]::StartNew()
    $Log = Join-Path $RunRoot "$Name.log"
    $Code = 0
    $PreviousErrorAction = $ErrorActionPreference
    try {
        $global:LASTEXITCODE = 0
        # Windows PowerShell wraps native stderr in ErrorRecord objects, even
        # for warnings with exit code zero. Keep the text and use the exit code.
        $ErrorActionPreference = 'Continue'
        $PSNativeCommandUseErrorActionPreference = $false
        & $Command 2>&1 | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) {
                if ($_.FullyQualifiedErrorId -like 'NativeCommandError*') {
                    $_.ToString()
                } else { throw $_ }
            } else { $_ }
        } -ErrorAction Stop | Tee-Object -FilePath $Log -ErrorAction Stop
        $Code = $LASTEXITCODE
    } catch {
        $_ | Out-String | Tee-Object -FilePath $Log -Append | Write-Host
        $Code = 1
    } finally {
        $ErrorActionPreference = $PreviousErrorAction
    }
    $Checks.Add([pscustomobject]@{name=$Name; exit_code=$Code; seconds=$Timer.Elapsed.TotalSeconds})
}

Push-Location $EmtgRoot
try {
    if (-not $Fast -and -not $SkipBuild) {
        Invoke-QualificationCheck 'build' { & (Join-Path $EmtgRoot 'build.ps1') }
        if ($Checks[-1].exit_code -ne 0) { throw 'Build failed; see the recorded build log' }
    }
    if (-not $Fast) {
        Invoke-QualificationCheck 'provenance-before' {
            python scripts/release_provenance.py verify --receipt "dist/$ReceiptName" --root $EmtgRoot --executable _local/builds/windows-release/bin/EMTGv9.exe --output "$RunRoot/provenance-before.json"
        }
        if ($Checks[-1].exit_code -ne 0) { throw 'Release provenance failed before scientific qualification' }
        # Retain the exact release that the subsequent checks qualify.
        Copy-Item dist "$RunRoot/qualified-release" -Recurse
        if ($env:GITHUB_ENV) { "EMTG_QUALIFIED_RELEASE=$RunRoot/qualified-release" | Add-Content $env:GITHUB_ENV -Encoding utf8 }
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
        Copy-Item "$RunRoot/qualified-release/EMTGv9-windows-x64.exe" $StagedExecutable -Force
        Invoke-QualificationCheck 'provenance-staged' {
            python scripts/release_provenance.py verify --receipt "$RunRoot/qualified-release/$ReceiptName" --root $EmtgRoot --executable $StagedExecutable --output "$RunRoot/provenance-staged.json"
        }
        if ($Checks[-1].exit_code -ne 0) { throw 'Staged native executable identity mismatch' }
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
        $Archives = @(Get-ChildItem "$RunRoot/qualified-release/EMTG-*.zip")
        if ($Archives.Count -ne 1) { throw 'Expected exactly one release ZIP in dist' }
        Expand-Archive -LiteralPath $Archives[0].FullName -DestinationPath "$RunRoot\relocated"
        $Executables = @(Get-ChildItem "$RunRoot\relocated" -Filter EMTGv9.exe -Recurse)
        if ($Executables.Count -ne 1) { throw 'Expected exactly one extracted EMTG executable' }
        Invoke-QualificationCheck 'provenance-package' {
            python scripts/release_provenance.py verify --receipt "$RunRoot/qualified-release/$ReceiptName" --root $EmtgRoot --executable $Executables[0].FullName --output "$RunRoot/provenance-package.json"
        }
        if ($Checks[-1].exit_code -ne 0) { throw 'Extracted package provenance failed' }
        $env:EMTG_RELEASE_ROOT = Split-Path (Split-Path $Executables[0].FullName)
        Invoke-QualificationCheck 'package' {
            python -m pytest tests/test_packaged_runtime.py --basetemp "$RunRoot\r" --junitxml="$RunRoot\package.xml" -ra
        }
        Invoke-QualificationCheck 'dll-audit' { & "$PSScriptRoot\audit-windows-dependencies.ps1" -Executable $Executables[0].FullName }
        Invoke-QualificationCheck 'path-audit' { python scripts/audit-release-paths.py "$RunRoot/qualified-release" --forbid-root $EmtgRoot }
        Invoke-QualificationCheck 'offline-build' { & (Join-Path $EmtgRoot 'build.ps1') -Offline -OutputDirectory "$RunRoot/offline-release" }
        if ($Checks[-1].exit_code -eq 0) {
            Invoke-QualificationCheck 'offline-identity' {
                python scripts/release_provenance.py verify --receipt "$RunRoot/qualified-release/$ReceiptName" --root $EmtgRoot --executable _local/builds/windows-release/bin/EMTGv9.exe --output "$RunRoot/offline-identity.json"
            }
        }
    }
} finally {
    $Checks | ConvertTo-Json -Depth 4 | Set-Content "$RunRoot\commands.json" -Encoding utf8
    & python "$PSScriptRoot\summarize-qualification.py" $RunRoot
    if ($LASTEXITCODE -ne 0) { $Checks.Add([pscustomobject]@{name='summary-validation'; exit_code=1; seconds=0}) }
    if ($HadExecutable) { Copy-Item (Join-Path $RunRoot 'previous-executable.bin') $StagedExecutable -Force }
    elseif (Test-Path $StagedExecutable) { Remove-Item -LiteralPath $StagedExecutable }
    & python -m pip freeze | Set-Content "$RunRoot\python-freeze.txt" -Encoding utf8
    & git rev-parse HEAD | Set-Content "$RunRoot\test-source-commit.txt" -Encoding ascii
    $env:EMTG_TEST_UNIVERSE = $SavedUniverse
    $env:EMTG_RUN_OUTERLOOP_INTEGRATION = $SavedIntegration
    $env:EMTG_ATLAS_AEPS_BUDGET_SECONDS = $SavedBudget
    $env:EMTG_RELEASE_ROOT = $SavedRelease
    Pop-Location
    Write-Host "Qualification evidence: $RunRoot"
}
if ($Checks | Where-Object { $_.exit_code -ne 0 }) { exit 1 }
