#requires -Version 5.1
# No Pester or application dependencies. Never opens tasks/repositories or launches Mirrorly.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
Add-Type -AssemblyName System.IO.Compression.FileSystem
$collector = Join-Path $PSScriptRoot 'Collect-P2-Diagnostic.ps1'
$hostExe = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('Mirrorly-DiagnosticKit-Tests-' + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($scratch) | Out-Null
$checks = 0
function Assert([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "FAIL: $Message" }
    $script:checks++
}
function Run-Case([string]$Case, [string]$TempDirectory, [string[]]$Arguments = @()) {
    $oldTemp = $env:TEMP; $oldTmp = $env:TMP
    $out = Join-Path $scratch $Case
    try {
        $env:TEMP = $TempDirectory; $env:TMP = $TempDirectory
        Push-Location $env:SystemRoot
        try {
            $result = & $hostExe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $collector -OutputDirectory $out @Arguments 2>&1
            if ($LASTEXITCODE -ne 0) { throw "Collector failed: $result" }
        } finally { Pop-Location }
    } finally { $env:TEMP = $oldTemp; $env:TMP = $oldTmp }
    $zip = @(Get-ChildItem -LiteralPath $out -Filter '*.zip')
    Assert ($zip.Count -eq 1) "$Case ZIP exists"
    $folder = @(Get-ChildItem -LiteralPath $out -Directory)[0].FullName
    $reader = [IO.Compression.ZipFile]::OpenRead($zip[0].FullName)
    try {
        $entries = @($reader.Entries | ForEach-Object { $_.FullName.Replace('\', '/') })
        foreach ($required in @('environment/windows.txt', 'environment/hardware.txt', 'environment/display.txt', 'environment/dpi.txt', 'process/mirrorly.txt', 'process/modules.txt', 'diagnostics/inventory.txt', 'metadata/timestamp.txt', 'metadata/collection-notes.txt')) {
            Assert ($entries -contains $required) "$Case $required"
        }
        Assert (@($entries | Where-Object { $_ -match '\.(exe|dll|ps1|cmd|toml)$' }).Count -eq 0) "$Case no program/config included"
    } finally { $reader.Dispose() }
    return $folder
}

# Use Unicode and spaces without relying on Windows PowerShell's BOM-less source decoding.
$fakeTemp = Join-Path $scratch (([char]0x4E2D).ToString() + ([char]0x6587).ToString() + ' space TEMP')
$logs = Join-Path $fakeTemp 'Mirrorly-SetupDiagnostics'
[IO.Directory]::CreateDirectory($logs) | Out-Null
$pidForFixture = 2147483646
$logName = "setup-$pidForFixture-" + ('a' * 32) + '.log'
$log = Join-Path $logs $logName
$bytes = [Text.Encoding]::UTF8.GetBytes("2026-09-27T00:00:00Z pid=$pidForFixture point=N0 pane=Source`npoint=F5 focus_result=True`n")
[IO.File]::WriteAllBytes($log, $bytes)
$stamp = [DateTime]::UtcNow.AddMinutes(-2)
[IO.File]::SetLastWriteTimeUtc($log, $stamp)
$originalHash = (Get-FileHash -LiteralPath $log -Algorithm SHA256).Hash
[IO.File]::WriteAllText((Join-Path $logs 'private.txt'), 'DO_NOT_COLLECT_PRIVATE_SENTINEL')
$nested = Join-Path $logs 'nested'
[IO.Directory]::CreateDirectory($nested) | Out-Null
[IO.File]::WriteAllText((Join-Path $nested $logName), 'DO_NOT_COLLECT_NESTED_SENTINEL')
$oldLog = Join-Path $logs ("setup-$pidForFixture-" + ('b' * 32) + '.log')
[IO.File]::WriteAllText($oldLog, 'DO_NOT_COLLECT_OLD_SENTINEL')
[IO.File]::SetLastWriteTimeUtc($oldLog, [DateTime]::UtcNow.AddDays(-8))
$largeLog = Join-Path $logs ("setup-$pidForFixture-" + ('c' * 32) + '.log')
[IO.File]::WriteAllText($largeLog, 'X' * 262145)
$otherLog = Join-Path $logs ('setup-2147483645-' + ('d' * 32) + '.log')
[IO.File]::WriteAllText($otherLog, 'DO_NOT_COLLECT_OTHER_PID_SENTINEL')
$lockedLog = Join-Path $logs ("setup-$pidForFixture-" + ('e' * 32) + '.log')
[IO.File]::WriteAllText($lockedLog, 'DO_NOT_COLLECT_LOCKED_SENTINEL')

$liveStream = [IO.File]::Open($log, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite)
$lockedStream = [IO.File]::Open($lockedLog, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None)
try {
    $folder = Run-Case 'filtered' $fakeTemp @('-MirrorlyProcessId', "$pidForFixture")
} finally { $liveStream.Dispose(); $lockedStream.Dispose() }
$copied = Join-Path (Join-Path $folder 'diagnostics') $logName
Assert ((Get-FileHash -LiteralPath $copied -Algorithm SHA256).Hash -eq $originalHash) 'log bytes preserved'
Assert ((Get-Item -LiteralPath $copied).LastWriteTimeUtc -eq $stamp) 'log timestamp preserved in evidence folder'
Assert ((Get-FileHash -LiteralPath $log -Algorithm SHA256).Hash -eq $originalHash) 'original log unchanged'
$text = (Get-ChildItem -LiteralPath $folder -Recurse -File | ForEach-Object { [IO.File]::ReadAllText($_.FullName) }) -join "`n"
Assert (-not $text.Contains('DO_NOT_COLLECT')) 'private/nested/old/other-PID files excluded'
Assert ($text.Contains('Skipped oversized log')) 'oversized log rejected'
Assert ((Get-Content -LiteralPath (Join-Path $folder 'diagnostics/inventory.txt') -Raw).Contains('unavailable: type=System.IO.IOException')) 'locked log failure is nonfatal'
Assert ((Get-Content -LiteralPath (Join-Path $folder 'process/mirrorly.txt') -Raw).Contains('unavailable')) 'absent process recorded'
Assert ((Get-Content -LiteralPath (Join-Path $folder 'environment/windows.txt') -Raw).Contains('BuildNumber=')) 'real OS collection'
Assert ((Get-Content -LiteralPath (Join-Path $folder 'environment/display.txt') -Raw).Contains('device=')) 'real native display query'

$empty = Join-Path $scratch 'empty-temp'
[IO.Directory]::CreateDirectory($empty) | Out-Null
$folder = Run-Case 'no-logs' $empty @('-MirrorlyProcessId', "$pidForFixture")
Assert ((Get-Content -LiteralPath (Join-Path $folder 'diagnostics/inventory.txt') -Raw).Contains('directory absent')) 'absent logs are nonfatal'

$manyTemp = Join-Path $scratch 'many-temp'
$manyLogs = Join-Path $manyTemp 'Mirrorly-SetupDiagnostics'
[IO.Directory]::CreateDirectory($manyLogs) | Out-Null
1..22 | ForEach-Object {
    [IO.File]::WriteAllText((Join-Path $manyLogs ('setup-2147483646-' + $_.ToString('x32') + '.log')), 'fixture')
}
$folder = Run-Case 'bounded' $manyTemp @('-MirrorlyProcessId', "$pidForFixture")
Assert (@(Get-ChildItem -LiteralPath (Join-Path $folder 'diagnostics') -Filter '*.log').Count -eq 20) '20-log cap'
Assert ((Get-Content -LiteralPath (Join-Path $folder 'diagnostics/inventory.txt') -Raw).Contains('OmittedOlderLogs=2')) 'omission recorded'

$linkTemp = Join-Path $scratch 'junction-temp'
[IO.Directory]::CreateDirectory($linkTemp) | Out-Null
New-Item -ItemType Junction -Path (Join-Path $linkTemp 'Mirrorly-SetupDiagnostics') -Target $logs | Out-Null
$folder = Run-Case 'reparse-root' $linkTemp @('-MirrorlyProcessId', "$pidForFixture")
Assert ((Get-Content -LiteralPath (Join-Path $folder 'diagnostics/inventory.txt') -Raw).Contains('refusing reparse-point')) 'reparse root rejected'
Assert (@(Get-ChildItem -LiteralPath (Join-Path $folder 'diagnostics') -Filter '*.log').Count -eq 0) 'reparse target not copied'

Write-Output "PASS: $checks checks; Windows PowerShell $($PSVersionTable.PSVersion) orchestrator, child collector uses Windows PowerShell 5.1."
Write-Output "Test artifacts retained: $scratch"
