# Focused artifact negatives. Only a new disposable copy is changed.
param([Parameter(Mandatory)][string]$PayloadDirectory)
$ErrorActionPreference = 'Stop'
$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$testRoot = [IO.Path]::GetFullPath((Join-Path $tempBase ('Mirrorly license tests ' + [Guid]::NewGuid().ToString('N'))))
if (-not $testRoot.StartsWith([IO.Path]::TrimEndingDirectorySeparator($tempBase) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test directory.' }
$validator = Join-Path $PSScriptRoot 'Test-Redistribution.ps1'
$script:cases = 0
function Validate { & $validator -PayloadDirectory $testRoot }
function Reject-Change([string]$relative, [scriptblock]$change, [string]$expected) {
    $path = Join-Path $testRoot $relative
    $bytes = [IO.File]::ReadAllBytes($path)
    try {
        & $change $path
        $rejected = $false
        try { Validate } catch {
            if ($_.Exception.Message -notlike $expected) { throw }
            $rejected = $true
        }
        if (-not $rejected) { throw "License mutation accepted: $relative" }
        $script:cases++
    } finally { [IO.File]::WriteAllBytes($path, $bytes) }
}
New-Item -ItemType Directory -Path $testRoot | Out-Null
try {
    Get-ChildItem -LiteralPath $PayloadDirectory -Force | Copy-Item -Destination $testRoot -Recurse
    Validate
    $script:cases++
    Reject-Change 'licenses/python/EXPAT-COPYING.txt' { param($p) Remove-Item -LiteralPath $p } 'Redistribution: missing*'
    Reject-Change 'licenses/python/HACL-NOTICES.txt' { param($p) [IO.File]::WriteAllText($p, '') } 'Redistribution: empty*'
    # A source MIT license must never substitute for the Windows App SDK binary terms.
    Reject-Change 'licenses/windowsappsdk/LICENSE.txt' { param($p) [IO.File]::WriteAllBytes($p, [IO.File]::ReadAllBytes((Join-Path $testRoot 'licenses/microsoft/MIT.txt'))) } 'Redistribution: hash mismatch:*'
    Reject-Change 'licenses/windowsml/SOURCE-AVAILABILITY.txt' { param($p) [IO.File]::WriteAllText($p, [IO.File]::ReadAllText($p).Replace('800ac32bc82d562c611d641b1112a8aa9f90c4f9', 'unknown')) } 'Redistribution: hash mismatch:*'
    Reject-Change 'licenses/rust/COPYRIGHT-library.html' { param($p) [IO.File]::AppendAllText($p, 'wrong release') } 'Redistribution: hash mismatch:*'
    Reject-Change 'app/python/LICENSE.txt' { param($p) [IO.File]::AppendAllText($p, 'changed') } 'Redistribution: hash mismatch:*'
    Reject-Change 'app/python/packages/blake3/blake3.cp313-win_amd64.pyd' { param($p) [IO.File]::AppendAllText($p, 'changed') } 'Redistribution: hash mismatch:*'
    Reject-Change 'app/gui/Mirrorly.Desktop.deps.json' { param($p) [IO.File]::WriteAllText($p, [IO.File]::ReadAllText($p).Replace('System.Numerics.Tensors/9.0.0', 'System.Numerics.Tensors/10.0.0')) } 'Redistribution: published dependency version/set changed'
    Reject-Change 'README.txt' { param($p) [IO.File]::WriteAllText($p, [IO.File]::ReadAllText($p).Replace('THIRD-PARTY-SOFTWARE-TERMS.txt', '')) } 'Redistribution: README lacks*'
    Reject-Change 'SHA256SUMS.txt' { param($p) [IO.File]::WriteAllLines($p, [string[]]@([IO.File]::ReadAllLines($p) | Where-Object { $_ -notlike '*licenses/python/EXPAT-COPYING.txt' })) } 'Redistribution: checksum coverage differs*'
    $extra = Join-Path $testRoot 'licenses/unapproved.txt'
    try {
        [IO.File]::WriteAllText($extra, 'unapproved')
        $rejected = $false
        try { Validate } catch {
            if ($_.Exception.Message -ne 'Redistribution: unexpected license file') { throw }
            $rejected = $true
        }
        if (-not $rejected) { throw 'Unlisted license material accepted.' }
        $script:cases++
    } finally { Remove-Item -LiteralPath $extra }
    Validate
    Write-Output "PASS: $script:cases redistribution artifact cases."
} finally {
    # Absolute testRoot was checked to remain inside the chosen temp root.
    Remove-Item -LiteralPath $testRoot -Recurse -Force
}
