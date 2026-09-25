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

$rejectedRelativeRoot = $false
try {
    & $launcher -TestDataRoot 'relative-test-data' -ValidateOnly | Out-Null
} catch {
    $rejectedRelativeRoot = $_.Exception.Message -like '*absolute path*'
}
if (-not $rejectedRelativeRoot) {
    throw 'Debug GUI launcher accepted a relative test-data root.'
}

$after = @(Get-Process Mirrorly.Desktop -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
if (@(Compare-Object $before $after).Count -ne 0) {
    throw 'Validation-only launcher unexpectedly started or stopped Mirrorly.'
}

'Debug GUI package-context launch qualification: PASS'
