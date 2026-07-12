[CmdletBinding()]
param([switch]$SkipInstall, [switch]$ReleaseArchive)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Frontend = Join-Path $Root 'PyEMTG\Studio\frontend'
$BundleState = Join-Path $Root 'dist\EMTGStudio\_local\studio'
$PreservedState = Join-Path $Root '_local\studio-package-state-preserve'
$ReleaseArchivePath = Join-Path $Root 'dist\EMTGStudio-windows-x64.zip'

if (Test-Path $BundleState) {
    if (Test-Path $PreservedState) {
        throw "A preserved Studio state already exists at $PreservedState; restore or remove it before packaging."
    }
    Move-Item -LiteralPath $BundleState -Destination $PreservedState
}

Push-Location $Frontend
try {
    if (-not $SkipInstall) { npm.cmd ci }
    npm.cmd run build
} finally {
    Pop-Location
}

Push-Location $Root
try {
    python -m PyInstaller --noconfirm --clean `
        --distpath (Join-Path $Root 'dist') `
        --workpath (Join-Path $Root '_local\pyinstaller-studio') `
        (Join-Path $PSScriptRoot 'EMTGStudio.spec')
    if ($LASTEXITCODE -ne 0) { throw 'EMTG Studio packaging failed' }
    $Bundle = Join-Path $Root 'dist\EMTGStudio'
    New-Item -ItemType Directory -Force -Path `
        (Join-Path $Bundle 'PyEMTG'), (Join-Path $Bundle 'OptionsOverhaul') | Out-Null
    Copy-Item (Join-Path $Root 'PyEMTG\default.emtgopt') (Join-Path $Bundle 'PyEMTG\default.emtgopt') -Force
    Copy-Item (Join-Path $Root 'OptionsOverhaul\list_of_missionoptions.csv') (Join-Path $Bundle 'OptionsOverhaul\list_of_missionoptions.csv') -Force
    Copy-Item (Join-Path $Root 'OptionsOverhaul\list_of_journeyoptions.csv') (Join-Path $Bundle 'OptionsOverhaul\list_of_journeyoptions.csv') -Force

    # Ship a solver-ready baseline. Large optional planetary kernels are not
    # copied wholesale; the bounded asteroid template only needs the files
    # below and Studio can also discover a neighboring full EMTG checkout.
    Copy-Item (Join-Path $Root 'bin') (Join-Path $Bundle 'bin') -Recurse -Force
    Copy-Item (Join-Path $Root 'testatron\HardwareModels') (Join-Path $Bundle 'testatron\HardwareModels') -Recurse -Force
    New-Item -ItemType Directory -Force -Path `
        (Join-Path $Bundle 'testatron\tests\integration_asteroid_missions'), `
        (Join-Path $Bundle 'testatron\tests\transcription_tests'), `
        (Join-Path $Bundle 'testatron\universe\ephemeris_files') | Out-Null
    Copy-Item (Join-Path $Root 'testatron\tests\integration_asteroid_missions\A20136163_AEPS_IPOPT_FBLT.emtgopt') `
        (Join-Path $Bundle 'testatron\tests\integration_asteroid_missions\A20136163_AEPS_IPOPT_FBLT.emtgopt') -Force
    Copy-Item (Join-Path $Root 'testatron\tests\transcription_tests\MGAnDSMs_EMintercept.emtgopt') `
        (Join-Path $Bundle 'testatron\tests\transcription_tests\MGAnDSMs_EMintercept.emtgopt') -Force
    Get-ChildItem (Join-Path $Root 'testatron\universe') -File | Copy-Item -Destination (Join-Path $Bundle 'testatron\universe') -Force
    foreach ($Directory in @('atmosphere_files', 'gravity_files')) {
        $Source = Join-Path $Root "testatron\universe\$Directory"
        if (Test-Path $Source) { Copy-Item $Source (Join-Path $Bundle "testatron\universe\$Directory") -Recurse -Force }
    }
    foreach ($Kernel in @('asteroids_100_2026_04_07.bsp', 'de430.bsp', 'naif0012.tls', 'pck00010.tpc')) {
        $Source = Join-Path $Root "testatron\universe\ephemeris_files\$Kernel"
        if (Test-Path $Source) { Copy-Item $Source (Join-Path $Bundle "testatron\universe\ephemeris_files\$Kernel") -Force }
    }
    if ($ReleaseArchive) {
        # BundleState was moved out before PyInstaller ran. Archive the clean
        # suite now, before the developer's local state is restored below.
        # Each extracted copy creates its own _local/studio beside the EXE.
        if (Test-Path $ReleaseArchivePath) { Remove-Item -LiteralPath $ReleaseArchivePath -Force }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::CreateFromDirectory(
            $Bundle, $ReleaseArchivePath,
            [System.IO.Compression.CompressionLevel]::Optimal, $false
        )
        $Archive = [System.IO.Compression.ZipFile]::OpenRead($ReleaseArchivePath)
        try {
            $StateEntries = @($Archive.Entries | Where-Object {
                $_.FullName -eq '_local' -or $_.FullName.StartsWith('_local/') -or $_.FullName.StartsWith('_local\')
            })
            if ($StateEntries.Count -ne 0) {
                throw "Release archive contains local Studio state: $($StateEntries[0].FullName)"
            }
        } finally {
            $Archive.Dispose()
        }
    }
} finally {
    Pop-Location
    if (Test-Path $PreservedState) {
        $StateParent = Split-Path $BundleState -Parent
        New-Item -ItemType Directory -Force -Path $StateParent | Out-Null
        if (Test-Path $BundleState) {
            throw "The rebuilt bundle unexpectedly created Studio state at $BundleState; preserved state remains at $PreservedState."
        }
        Move-Item -LiteralPath $PreservedState -Destination $BundleState
    }
}

Write-Host "EMTG Studio bundle: $(Join-Path $Root 'dist\EMTGStudio\EMTGStudio.exe')"
if ($ReleaseArchive) { Write-Host "Clean release archive: $ReleaseArchivePath" }
