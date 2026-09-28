# Native launcher regression only. Never starts the real GUI or reads user tasks.
param(
    [Parameter(Mandatory)][string]$MSBuildPath,
    [Parameter(Mandatory)][string]$LauncherPath
)
$ErrorActionPreference = 'Stop'
if (-not [IO.Path]::IsPathFullyQualified($LauncherPath)) { throw 'Launcher path must be absolute.' }
$project = Join-Path $PSScriptRoot 'Mirrorly.Launcher/Mirrorly.Launcher.vcxproj'
& $MSBuildPath $project /t:Build /p:Configuration=Probe /p:Platform=x64 /verbosity:minimal /nologo
if ($LASTEXITCODE -ne 0) { throw 'Launcher test probe build failed.' }
$tools = (& $MSBuildPath $project /p:Configuration=Release /p:Platform=x64 -getProperty:VCToolsInstallDir /nologo).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Native toolchain evaluation failed.' }
$dumpbin = Join-Path $tools 'bin/Hostx64/x64/dumpbin.exe'
$dependencies = & $dumpbin /dependents $LauncherPath
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect launcher imports.' }
$imports = @($dependencies | ForEach-Object { if ($_ -match '^\s+([\w.-]+\.dll)\s*$') { $Matches[1] } })
if ($imports.Count -eq 0 -or @($imports | Where-Object { $_ -notin @('KERNEL32.dll', 'USER32.dll') }).Count -ne 0) {
    throw "Unexpected launcher runtime dependency: $($imports -join ', ')"
}
Write-Output "PASS: native launcher imports only $($imports -join ', ')."

# Observe/dismiss only a standard error dialog owned by the launched test PID.
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class LauncherDialogProbe {
    private delegate bool EnumProc(IntPtr window, IntPtr unused);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumProc callback, IntPtr data);
    [DllImport("user32.dll")] private static extern bool EnumChildWindows(IntPtr window, EnumProc callback, IntPtr data);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetClassName(IntPtr window, StringBuilder text, int length);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetWindowText(IntPtr window, StringBuilder text, int length);
    [DllImport("user32.dll")] private static extern bool PostMessage(IntPtr window, uint message, IntPtr w, IntPtr l);
    public static string CaptureAndDismiss(uint pid) {
        string result = null;
        EnumWindows((window, unused) => {
            GetWindowThreadProcessId(window, out uint owner);
            if (owner != pid || !IsWindowVisible(window)) return true;
            var type = new StringBuilder(256); GetClassName(window, type, type.Capacity);
            if (type.ToString() != "#32770") return true;
            var title = new StringBuilder(256); GetWindowText(window, title, title.Capacity);
            if (title.ToString() != "Mirrorly") return true;
            var message = new StringBuilder();
            EnumChildWindows(window, (child, ignored) => {
                var text = new StringBuilder(2048); GetWindowText(child, text, text.Capacity);
                message.Append(text).Append(' '); return true;
            }, IntPtr.Zero);
            result = message.ToString();
            PostMessage(window, 0x10, IntPtr.Zero, IntPtr.Zero); // Close this owned MessageBox.
            return false;
        }, IntPtr.Zero);
        return result;
    }
}
'@
$base = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'artifacts'))
$testRoot = [IO.Path]::GetFullPath((Join-Path $base ('launcher-tests-' + [Guid]::NewGuid().ToString('N'))))
if (-not $testRoot.StartsWith($base + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test directory.' }
New-Item -ItemType Directory -Path $testRoot | Out-Null
$probe = Join-Path $PSScriptRoot 'Mirrorly.Launcher/bin/Probe/Mirrorly.LauncherProbe.exe'
function Assert($condition, [string]$message) { if (-not $condition) { throw $message } }
function Make-Copy([string]$name) {
    $root = Join-Path $testRoot $name
    New-Item -ItemType Directory -Path (Join-Path $root 'app/gui') -Force | Out-Null
    Copy-Item -LiteralPath $LauncherPath -Destination (Join-Path $root 'Mirrorly.exe')
    Copy-Item -LiteralPath $probe -Destination (Join-Path $root 'app/gui/Mirrorly.Desktop.exe')
    return $root
}
function Check-Forwarding([string]$root, [string]$cwd, [string[]]$arguments) {
    $eventName = 'Local\MirrorlyLauncherTest-' + [Guid]::NewGuid().ToString('N')
    $released = [Threading.EventWaitHandle]::new($false, [Threading.EventResetMode]::ManualReset, $eventName)
    $report = Join-Path $testRoot ([Guid]::NewGuid().ToString('N') + '.report')
    $start = [Diagnostics.ProcessStartInfo]::new((Join-Path $root 'Mirrorly.exe'))
    $start.UseShellExecute = $false; $start.WorkingDirectory = $cwd
    $start.Environment['PATH'] = ''
    $start.Environment['MIRRORLY_LAUNCHER_TEST_REPORT'] = $report
    $start.Environment['MIRRORLY_LAUNCHER_TEST_EVENT'] = $eventName
    foreach ($argument in $arguments) { $start.ArgumentList.Add($argument) }
    $launcher = $null; $child = $null
    try {
        $launcher = [Diagnostics.Process]::Start($start)
        Assert ($launcher.WaitForExit(10000)) 'Launcher waited for its child instead of exiting.'
        Assert ($launcher.ExitCode -eq 0) 'Launcher did not report successful process creation.'
        $watch = [Diagnostics.Stopwatch]::StartNew()
        $values = $null
        while ($watch.Elapsed.TotalSeconds -lt 10 -and $null -eq $values) {
            if (Test-Path -LiteralPath $report) {
                $reader = $null
                try {
                    $reader = [IO.BinaryReader]::new([IO.File]::OpenRead($report))
                    $childId = $reader.ReadUInt32()
                    $collected = [Collections.Generic.List[string]]::new()
                    for ($i = 0; $i -lt ($arguments.Count + 2); $i++) {
                        $length = $reader.ReadUInt32()
                        Assert ($length -le 32768) 'Invalid probe record length.'
                        $bytes = $reader.ReadBytes([int]($length * 2))
                        if ($bytes.Length -ne $length * 2) { throw 'Probe record not complete yet.' }
                        $collected.Add([Text.Encoding]::Unicode.GetString($bytes))
                    }
                    $values = $collected.ToArray()
                } catch { $values = $null }
                finally { if ($reader) { $reader.Dispose() } }
            }
            if ($null -eq $values) { Start-Sleep -Milliseconds 20 }
        }
        Assert ($null -ne $values) 'Child argument report missing/incomplete.'
        $child = [Diagnostics.Process]::GetProcessById($childId)
        # Retain the handle before releasing a process not started by this .NET
        # Process instance; otherwise ExitCode may be unavailable after exit.
        $null = $child.Handle
        Assert (-not $child.HasExited) 'Child must still be waiting after launcher exit.'
        Assert ($values[0] -ceq (Join-Path $root 'app/gui').Replace('/', '\')) 'Child cwd was inherited or incorrect.'
        Assert ($values[1] -ceq (Join-Path $root 'app/gui/Mirrorly.Desktop.exe').Replace('/', '\')) 'Child argv[0] was not the exact payload path.'
        for ($i = 0; $i -lt $arguments.Count; $i++) { Assert ($values[$i + 2] -ceq $arguments[$i]) "Argument $i changed." }
        $released.Set() | Out-Null
        $childExited = $child.WaitForExit(10000)
        Assert ($childExited -and $child.ExitCode -eq 0) "Test child did not exit normally (exited=$childExited, code=$($child.ExitCode))."
    } finally {
        $released.Set() | Out-Null
        if ($child) { $child.WaitForExit(10000) | Out-Null; $child.Dispose() }
        if ($launcher) { $launcher.Dispose() }
        $released.Dispose()
    }
}
function Check-Failure([string]$root, [string]$expected) {
    $start = [Diagnostics.ProcessStartInfo]::new((Join-Path $root 'Mirrorly.exe'))
    $start.UseShellExecute = $false; $start.WorkingDirectory = $env:SystemRoot
    $process = [Diagnostics.Process]::Start($start)
    try {
        $message = $null; $watch = [Diagnostics.Stopwatch]::StartNew()
        while ($watch.Elapsed.TotalSeconds -lt 10 -and $null -eq $message -and -not $process.HasExited) {
            $message = [LauncherDialogProbe]::CaptureAndDismiss([uint32]$process.Id)
            if ($null -eq $message) { Start-Sleep -Milliseconds 20 }
        }
        Assert ($message -like "*$expected*") 'Expected visible launcher error dialog was not observed.'
        Assert ($process.WaitForExit(10000) -and $process.ExitCode -ne 0) 'Launcher failure must return nonzero.'
    } finally { $process.Dispose() }
}
try {
    $normal = Make-Copy 'ordinary'
    Check-Forwarding $normal $normal @()
    $arguments = @('normal', 'two words', '', 'embedded"quote', 'trailing\', 'space and trailing\', '\\"', '中文 参数', '& | < > ^ %PATH%')
    Check-Forwarding $normal $env:SystemRoot $arguments
    # Move the same executable tree: no rebuild, no old-location fallback.
    $moved = [IO.Path]::GetFullPath((Join-Path $testRoot '新的 位置 Mirrorly'))
    if (-not $moved.StartsWith($testRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe relocation.' }
    Move-Item -LiteralPath $normal -Destination $moved
    Check-Forwarding $moved $env:SystemRoot $arguments
    $missing = Make-Copy 'missing'
    Remove-Item -LiteralPath (Join-Path $missing 'app/gui/Mirrorly.Desktop.exe')
    Check-Failure $missing 'could not find app\gui\Mirrorly.Desktop.exe'
    [IO.File]::WriteAllText((Join-Path $missing 'app/gui/Mirrorly.Desktop.exe'), 'not a Windows executable')
    Check-Failure $missing 'could not start its GUI'
    Write-Output 'PASS: 5 native launcher cases; exact arguments/cwd, no PATH, moved Chinese/space path, immediate exit, missing/invalid payload dialogs.'
} finally {
    # Absolute root and relocation targets were checked against ignored artifacts.
    Remove-Item -LiteralPath $testRoot -Recurse -Force
}
