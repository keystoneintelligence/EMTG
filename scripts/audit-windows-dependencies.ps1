[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Executable)

$ErrorActionPreference = 'Stop'
$Dumpbin = Get-Command dumpbin.exe -ErrorAction SilentlyContinue
if (-not $Dumpbin) {
    $VsWhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path $VsWhere) {
        $VsRoot = & $VsWhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        $DumpbinPath = Get-ChildItem (Join-Path $VsRoot 'VC\Tools\MSVC\*\bin\Hostx64\x64\dumpbin.exe') -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1
        if ($DumpbinPath) { $Dumpbin = Get-Command $DumpbinPath.FullName }
    }
    if (-not $Dumpbin) { throw 'dumpbin.exe is required to audit a Windows release' }
}

$Allowed = @(
    '^api-ms-win-', '^ext-ms-win-', '^KERNEL32\.dll$', '^USER32\.dll$',
    '^ADVAPI32\.dll$', '^SHELL32\.dll$', '^OLE32\.dll$', '^OLEAUT32\.dll$',
    '^WS2_32\.dll$', '^bcrypt\.dll$', '^ntdll\.dll$', '^ucrtbase\.dll$',
    '^msvcrt\.dll$'
)
$Dependencies = & $Dumpbin.Source /nologo /dependents $Executable |
    Select-String -Pattern '^\s+[^\s]+\.dll\s*$' |
    ForEach-Object { $_.Matches.Value.Trim() } |
    Sort-Object -Unique

$Unexpected = foreach ($Dependency in $Dependencies) {
    if (-not ($Allowed | Where-Object { $Dependency -match $_ })) { $Dependency }
}
if ($Unexpected) {
    throw "Standalone EXE imports non-system DLLs: $($Unexpected -join ', ')"
}

Write-Host "Dependency audit passed: $($Dependencies -join ', ')"
