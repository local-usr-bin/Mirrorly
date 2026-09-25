$ErrorActionPreference = 'Stop'

$launcher = Join-Path $PSScriptRoot 'Launch-DebugGui.ps1'
$testRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../.pytest_tmp/debug-gui-launch-check'))
$before = @(Get-Process Mirrorly.Desktop -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)

$qualification = & $launcher -TestDataRoot $testRoot -ValidateOnly
if ($qualification.LaunchMode -ne 'registered package context' -or
    $qualification.TestDataRoot -ne $testRoot -or
    $qualification.PackageFamilyName -ne 'Mirrorly.TechnicalPrototype_fhsq4wxgq3ejr') {
    throw 'Debug GUI launcher did not qualify the registered package and test root.'
}

$acceptedRoots = @(
    'C:\absolute\path',
    'C:\path with spaces\test',
    ('C:\' + [char]0x6D4B + [char]0x8BD5 + '\test'),
    '\\server\share\test'
)
foreach ($root in $acceptedRoots) {
    $result = & $launcher -TestDataRoot $root -ValidateOnly
    if ($result.TestDataRoot -ne [System.IO.Path]::GetFullPath($root)) {
        throw "Debug GUI launcher rejected or changed a qualified path: $root"
    }
}

$rejectedRoots = @('.\relative', 'relative\path', 'C:drive-relative', '\root-relative', '\\server')
foreach ($root in $rejectedRoots) {
    $rejected = $false
    try {
        & $launcher -TestDataRoot $root -ValidateOnly | Out-Null
    } catch {
        $rejected = $_.Exception.Message -like '*absolute path*'
    }
    if (-not $rejected) {
        throw "Debug GUI launcher accepted a non-qualified path: $root"
    }
}

$after = @(Get-Process Mirrorly.Desktop -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
if (@(Compare-Object $before $after).Count -ne 0) {
    throw 'Validation-only launcher unexpectedly started or stopped Mirrorly.'
}

'Debug GUI package-context launch qualification: PASS'
