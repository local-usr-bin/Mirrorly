#requires -Version 5.1
<# Standalone developer tool. No application reference, activation, or configuration changes. #>
[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path ([IO.Path]::GetTempPath()) 'Mirrorly-DiagnosticCollections'),
    [ValidateRange(1, 2147483647)][int[]]$MirrorlyProcessId,
    [ValidateRange(1, 168)][int]$LogAgeHours = 24
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2

function Error-Class($Record) {
    # Never include raw exception text: it may contain unrelated paths or other data.
    $exception = $Record.Exception.GetBaseException()
    'unavailable: type={0} hresult=0x{1:X8}' -f $exception.GetType().FullName, $exception.HResult
}
function Save-Text([string]$Relative, [string]$Text) {
    [IO.File]::WriteAllText((Join-Path $script:collection $Relative), $Text, [Text.UTF8Encoding]::new($false))
}
function Capture([string]$Relative, [scriptblock]$Read) {
    try {
        $text = (& $Read | Out-String -Width 4096).TrimEnd()
        if ([string]::IsNullOrWhiteSpace($text)) { $text = 'unavailable: no matching information' }
    } catch { $text = Error-Class $_ }
    Save-Text $Relative ($text + [Environment]::NewLine)
}
function Fields($Object, [string[]]$Names) {
    foreach ($name in $Names) {
        $value = $Object.$name
        if ($null -eq $value -or "$value" -eq '') { $value = 'unavailable' }
        '{0}={1}' -f $name, $value
    }
}
function Origin([string]$ModulePath, [string]$ExecutablePath) {
    if (-not $ExecutablePath) { return 'unavailable: executable path' }
    $base = [IO.Path]::GetDirectoryName($ExecutablePath).TrimEnd('\') + '\'
    if ($ModulePath.StartsWith($base, [StringComparison]::OrdinalIgnoreCase)) { return 'gui-directory-local' }
    return 'outside-gui-directory (inspect path; not a causal finding)'
}

if ($env:OS -ne 'Windows_NT') { throw 'This developer collector requires Windows.' }
if (-not [Environment]::Is64BitProcess) { throw 'Use 64-bit Windows PowerShell (the CMD wrapper selects it on x64 Windows).' }
$started = [DateTimeOffset]::Now
$name = 'Mirrorly-P2-Diagnostic-' + $started.ToString('yyyyMMdd-HHmmss')
$output = [IO.Path]::GetFullPath($OutputDirectory)
[IO.Directory]::CreateDirectory($output) | Out-Null
$script:collection = Join-Path $output $name
$archive = $script:collection + '.zip'
if ((Test-Path -LiteralPath $script:collection) -or (Test-Path -LiteralPath $archive)) {
    throw 'Collection with this timestamp already exists; retry in a new second or choose another output directory.'
}
foreach ($part in @('environment', 'process', 'diagnostics', 'metadata')) {
    [IO.Directory]::CreateDirectory((Join-Path $script:collection $part)) | Out-Null
}

Capture 'environment/windows.txt' {
    $os = Get-CimInstance Win32_OperatingSystem -OperationTimeoutSec 15
    Fields $os @('Caption', 'Version', 'BuildNumber', 'OSArchitecture')
    'CollectorPowerShell=' + $PSVersionTable.PSVersion
    'Collector64Bit=' + [Environment]::Is64BitProcess
}
Capture 'environment/hardware.txt' {
    'CPU:'
    try {
        foreach ($cpu in @(Get-CimInstance Win32_Processor -OperationTimeoutSec 15)) {
            Fields $cpu @('Name', 'Manufacturer', 'NumberOfCores', 'NumberOfLogicalProcessors')
        }
    } catch { Error-Class $_ }
    'RAM:'
    try {
        $machine = Get-CimInstance Win32_ComputerSystem -OperationTimeoutSec 15
        Fields $machine @('TotalPhysicalMemory')
    } catch { Error-Class $_ }
    'GPU (reported by Windows; guest adapter in a VM):'
    try {
        foreach ($gpu in @(Get-CimInstance Win32_VideoController -OperationTimeoutSec 15)) {
            Fields $gpu @('Name', 'DriverVersion', 'DriverDate', 'VideoProcessor', 'CurrentHorizontalResolution', 'CurrentVerticalResolution')
        }
    } catch { Error-Class $_ }
}

# Read-only Win32 queries. No focus, layout, window movement or DPI-awareness changes.
$nativeAvailable = $false
$nativeError = 'unavailable'
try {
    if (-not ('MirrorlyDiagnosticKit.Native' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
namespace MirrorlyDiagnosticKit {
    public static class Native {
        [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left, Top, Right, Bottom;
            public override string ToString() { return string.Format("{0},{1},{2},{3} width={4} height={5}", Left, Top, Right, Bottom, Right-Left, Bottom-Top); } }
        [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] public struct MonitorInfo {
            public int Size; public Rect Monitor, Work; public uint Flags;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string Device;
        }
        private delegate bool MonitorCallback(IntPtr monitor, IntPtr dc, ref Rect bounds, IntPtr data);
        [DllImport("user32.dll")] private static extern bool EnumDisplayMonitors(IntPtr dc, IntPtr clip, MonitorCallback callback, IntPtr data);
        [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern bool GetMonitorInfo(IntPtr monitor, ref MonitorInfo info);
        [DllImport("shcore.dll")] private static extern int GetDpiForMonitor(IntPtr monitor, int type, out uint x, out uint y);
        [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr window);
        [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr window, out Rect rect);
        [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr window, out Rect rect);
        public static string[] Monitors() {
            var lines = new List<string>();
            MonitorCallback callback = delegate(IntPtr handle, IntPtr dc, ref Rect rect, IntPtr data) {
                var info = new MonitorInfo(); info.Size = Marshal.SizeOf(typeof(MonitorInfo));
                if (!GetMonitorInfo(handle, ref info)) { lines.Add("monitor=unavailable"); return true; }
                lines.Add(string.Format("device={0} primary={1} bounds={2} work={3}", info.Device, (info.Flags & 1)!=0, info.Monitor, info.Work));
                try {
                    uint x,y; int result=GetDpiForMonitor(handle,0,out x,out y);
                    lines.Add(result==0 ? string.Format("monitor_dpi_collector_context={0},{1}",x,y) : "monitor_dpi=unavailable");
                } catch { lines.Add("monitor_dpi=unavailable"); }
                return true;
            };
            if (!EnumDisplayMonitors(IntPtr.Zero,IntPtr.Zero,callback,IntPtr.Zero)) lines.Add("monitor_enumeration=unavailable");
            return lines.ToArray();
        }
    }
}
'@ | Out-Null
    }
    $nativeAvailable = $true
} catch { $nativeError = Error-Class $_ }
Capture 'environment/display.txt' {
    'Coordinates and monitor DPI are reported in collector DPI-awareness context; may be virtualized.'
    'Not proof of XAML effective size or every monitor setting. No EDID/serial numbers collected.'
    if ($nativeAvailable) { [MirrorlyDiagnosticKit.Native]::Monitors() } else { $nativeError }
}

$processes = @()
$processError = $null
try {
    $processes = @(Get-Process -Name 'Mirrorly.Desktop' -ErrorAction SilentlyContinue | Where-Object {
        (-not $MirrorlyProcessId) -or ($MirrorlyProcessId -contains $_.Id)
    })
} catch { $processError = Error-Class $_ }
Capture 'process/mirrorly.txt' {
    if ($processError) { $processError }
    if ($processes.Count -eq 0) { 'unavailable: no matching running Mirrorly.Desktop process; existing logs can still be collected' }
    foreach ($process in $processes) {
        'PID=' + $process.Id
        try { 'StartTimeUtc=' + $process.StartTime.ToUniversalTime().ToString('O') } catch { Error-Class $_ }
        try {
            $path = $process.Path
            if (-not $path) { throw 'Process path unavailable' }
            'ExecutablePath=' + $path
            $version = [Diagnostics.FileVersionInfo]::GetVersionInfo($path)
            Fields $version @('FileVersion', 'ProductVersion')
            'ExecutableSHA256=' + (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
            # The apphost EXE alone does not identify the managed application build.
            $dll = Join-Path ([IO.Path]::GetDirectoryName($path)) 'Mirrorly.Desktop.dll'
            if (Test-Path -LiteralPath $dll -PathType Leaf) {
                'ManagedAssemblySHA256=' + (Get-FileHash -LiteralPath $dll -Algorithm SHA256).Hash
                'ManagedAssemblyProductVersion=' + [Diagnostics.FileVersionInfo]::GetVersionInfo($dll).ProductVersion
            }
        } catch { Error-Class $_ }
    }
}
Capture 'environment/dpi.txt' {
    'WindowDPI/96 is the target window effective scale, not every monitor or Windows Text Size setting.'
    'Window/client rects queried by collector can be DPI-virtualized; do not infer XAML breakpoints.'
    if ($processes.Count -eq 0) { 'unavailable: no matching running Mirrorly.Desktop process' }
    foreach ($process in $processes) {
        'PID=' + $process.Id
        if (-not $nativeAvailable) { $nativeError; continue }
        try {
            $window = $process.MainWindowHandle
            if ($window -eq [IntPtr]::Zero) { 'unavailable: no visible main window (possibly hidden to tray)'; continue }
            $dpi = [MirrorlyDiagnosticKit.Native]::GetDpiForWindow($window)
            if ($dpi -eq 0) { 'WindowDPI=unavailable' } else { 'WindowDPI={0} ScalePercent={1}' -f $dpi, ($dpi * 100.0 / 96) }
            $rect = New-Object MirrorlyDiagnosticKit.Native+Rect
            if ([MirrorlyDiagnosticKit.Native]::GetWindowRect($window, [ref]$rect)) { 'WindowRect=' + $rect } else { 'WindowRect=unavailable' }
            if ([MirrorlyDiagnosticKit.Native]::GetClientRect($window, [ref]$rect)) { 'ClientRect=' + $rect } else { 'ClientRect=unavailable' }
        } catch { Error-Class $_ }
    }
}
Capture 'process/modules.txt' {
    $names = @('hostfxr.dll', 'hostpolicy.dll', 'coreclr.dll', 'Microsoft.UI.Xaml.dll', 'Microsoft.WindowsAppRuntime.dll')
    'Only the five P2 runtime modules are queried. Origin classification is relative to GUI directory.'
    if ($processes.Count -eq 0) { 'unavailable: no matching running Mirrorly.Desktop process' }
    foreach ($process in $processes) {
        'PID=' + $process.Id
        try {
            $path = $process.Path
            $modules = @($process.Modules)
            foreach ($name in $names) {
                $matchesForName = @($modules | Where-Object { $_.ModuleName -ieq $name })
                if ($matchesForName.Count -eq 0) { "$name=unavailable: not observed" }
                foreach ($module in $matchesForName) {
                    Fields $module @('ModuleName', 'FileName')
                    'FileVersion=' + $module.FileVersionInfo.FileVersion
                    'Origin=' + (Origin $module.FileName $path)
                }
            }
        } catch { Error-Class $_ }
    }
}

Capture 'diagnostics/inventory.txt' {
    "Scope: setup-<PID>-<32 hex>.log in this user's TEMP/Mirrorly-SetupDiagnostics; last $LogAgeHours hours; newest 20; <=256 KiB each."
    'Live files are point-in-time prefixes, may end mid-record. Original files are not changed.'
    'Selection by PID is advisory: PID reuse is possible; compare timestamps and process start time.'
    $logRoot = Join-Path ([IO.Path]::GetTempPath()) 'Mirrorly-SetupDiagnostics'
    if (-not (Test-Path -LiteralPath $logRoot -PathType Container)) { 'unavailable: diagnostic log directory absent'; return }
    if ((Get-Item -LiteralPath $logRoot).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        'unavailable: refusing reparse-point log directory'; return
    }
    $cutoff = [DateTime]::UtcNow.AddHours(-$LogAgeHours)
    $logs = @(Get-ChildItem -LiteralPath $logRoot -File | Where-Object {
        $_.Name -match '^setup-([0-9]+)-[0-9a-f]{32}\.log$' -and
        $_.LastWriteTimeUtc -ge $cutoff -and
        ((-not $MirrorlyProcessId) -or ($MirrorlyProcessId -contains [int]$Matches[1]))
    } | Sort-Object LastWriteTimeUtc -Descending)
    'EligibleLogCount=' + $logs.Count
    if ($logs.Count -eq 0) { 'unavailable: no matching recent logs (diagnostics may have been off)' }
    if ($logs.Count -gt 20) { 'OmittedOlderLogs=' + ($logs.Count - 20) }
    foreach ($file in @($logs | Select-Object -First 20)) {
        if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) { 'Skipped reparse log: ' + $file.Name; continue }
        if ($file.Length -gt 262144) { 'Skipped oversized log: ' + $file.Name; continue }
        $stream = $null
        try {
            $stream = [IO.File]::Open($file.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
            $length = $stream.Length
            if ($length -gt 262144) { 'Skipped growing oversized log: ' + $file.Name; continue }
            $buffer = New-Object byte[] ([int]$length)
            $read = 0
            while ($read -lt $length) {
                $count = $stream.Read($buffer, $read, [int]$length - $read)
                if ($count -eq 0) { throw 'Log changed during read' }
                $read += $count
            }
            $target = Join-Path (Join-Path $script:collection 'diagnostics') $file.Name
            [IO.File]::WriteAllBytes($target, $buffer)
            [IO.File]::SetCreationTimeUtc($target, $file.CreationTimeUtc)
            [IO.File]::SetLastWriteTimeUtc($target, $file.LastWriteTimeUtc)
            '{0} bytes={1} created_utc={2:O} modified_utc={3:O} sha256={4}' -f $file.Name, $length, $file.CreationTimeUtc, $file.LastWriteTimeUtc, (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
        } catch { $file.Name + ' ' + (Error-Class $_) }
        finally { if ($null -ne $stream) { $stream.Dispose() } }
    }
}
Save-Text 'metadata/timestamp.txt' ("Started=$($started.ToString('O'))`r`nCompleted=$([DateTimeOffset]::Now.ToString('O'))`r`nCollectorSHA256=$((Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash)`r`n")
Save-Text 'metadata/collection-notes.txt' @'
Developer/test collection only. P2 remains MONITORING / non-blocking for v1.
No application settings, processes, registry, focus, layout or rendering were changed.
No task/config/repository contents, directory trees, command lines, window titles,
environment dump, credentials, hardware serials or screenshots were collected.
Executable/module paths may include a Windows username or chosen installation directory.
Existing diagnostic logs are copied verbatim, not reformatted or scrubbed. Review before sharing.
No upload occurs. This folder and its ZIP remain local; remove them manually when no longer needed.
Unavailable sections do not imply an application defect. No runtime-origin cause is inferred.
Use the same user and 64-bit PowerShell; access denial is recorded, not automatically elevated.
An existing session with diagnostics off cannot be backfilled. Logger caps can stop later records.
Capture a screenshot separately, avoiding personal data; note occurrence time and operation.
Manually note host/guest display mode, Windows Text Size and scaling settings when relevant.
This is a sequential snapshot, not an atomic capture of all machine and application state.
'@
try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory($script:collection, $archive)
} catch {
    throw "ZIP creation failed; collected evidence remains at: $script:collection"
}
Write-Output "Collection directory: $script:collection"
Write-Output "ZIP: $archive"
Write-Output "SHA256: $((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash)"
