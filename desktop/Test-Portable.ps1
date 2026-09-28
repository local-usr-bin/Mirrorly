# Validate an assembled Release folder without launching GUI or touching user tasks.
param(
    [Parameter(Mandatory)][string]$MSBuildPath,
    [Parameter(Mandatory)][string]$PayloadDirectory
)
$ErrorActionPreference = 'Stop'
if (-not [IO.Path]::IsPathFullyQualified($PayloadDirectory)) { throw 'Payload path must be absolute.' }
$payload = [IO.Path]::GetFullPath($PayloadDirectory)
$gui = Join-Path $payload 'app/gui'
& (Join-Path $PSScriptRoot 'Test-PackagingPoC.ps1') -MSBuildPath $MSBuildPath -PublishDirectory $gui
$launcher = Join-Path $payload 'Mirrorly.exe'
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) { throw 'Missing root launcher: Mirrorly.exe' }
# Compare with the actual native Release build, not a filename or a spoofable string.
$builtLauncher = Join-Path $PSScriptRoot 'Mirrorly.Launcher/bin/Release/Mirrorly.exe'
if (-not (Test-Path -LiteralPath $builtLauncher -PathType Leaf) -or
    (Get-FileHash -LiteralPath $launcher).Hash -ne (Get-FileHash -LiteralPath $builtLauncher).Hash) {
    throw 'Root launcher does not match the native Release build.'
}
$launcherStream = [IO.File]::OpenRead($launcher)
$launcherPe = [Reflection.PortableExecutable.PEReader]::new($launcherStream)
try {
    if ($launcherPe.PEHeaders.CoffHeader.Machine.ToString() -ne 'Amd64' -or
        $null -ne $launcherPe.PEHeaders.CorHeader -or
        $launcherPe.PEHeaders.PEHeader.Subsystem.ToString() -ne 'WindowsGui') {
        throw 'Root launcher must be native Windows x64.'
    }
} finally { $launcherPe.Dispose(); $launcherStream.Dispose() }
# Compare embedded resource bytes, not shell-rendered bitmaps (which may resample).
if (-not ('MirrorlyLauncherResources' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class MirrorlyLauncherResources {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] private static extern IntPtr LoadLibraryEx(string path, IntPtr file, uint flags);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] private static extern IntPtr FindResource(IntPtr module, IntPtr name, IntPtr type);
    [DllImport("kernel32.dll", SetLastError=true)] private static extern uint SizeofResource(IntPtr module, IntPtr resource);
    [DllImport("kernel32.dll", SetLastError=true)] private static extern IntPtr LoadResource(IntPtr module, IntPtr resource);
    [DllImport("kernel32.dll")] private static extern IntPtr LockResource(IntPtr resource);
    [DllImport("kernel32.dll")] private static extern bool FreeLibrary(IntPtr module);
    public static byte[] Read(string path, int type, int id) {
        var module = LoadLibraryEx(path, IntPtr.Zero, 0x22); // data/image resource only; no code execution
        if (module == IntPtr.Zero) throw new Win32Exception();
        try {
            var resource = FindResource(module, (IntPtr)id, (IntPtr)type);
            if (resource == IntPtr.Zero) throw new Win32Exception();
            var size = SizeofResource(module, resource);
            var pointer = LockResource(LoadResource(module, resource));
            if (size == 0 || pointer == IntPtr.Zero) throw new Win32Exception();
            var bytes = new byte[checked((int)size)];
            Marshal.Copy(pointer, bytes, 0, bytes.Length); return bytes;
        } finally { FreeLibrary(module); }
    }
}
'@
}
$approvedIcon = [IO.File]::ReadAllBytes((Join-Path $gui 'Assets/Mirrorly.ico'))
$group = [MirrorlyLauncherResources]::Read($launcher, 14, 1)
$count = [BitConverter]::ToUInt16($approvedIcon, 4)
if ([BitConverter]::ToUInt16($group, 4) -ne $count) { throw 'Root launcher icon image count changed.' }
for ($i = 0; $i -lt $count; $i++) {
    $entry = 6 + 16 * $i; $groupEntry = 6 + 14 * $i
    for ($j = 0; $j -lt 12; $j++) {
        if ($approvedIcon[$entry + $j] -ne $group[$groupEntry + $j]) { throw 'Root launcher icon directory differs from the approved icon.' }
    }
    $length = [BitConverter]::ToUInt32($approvedIcon, $entry + 8)
    $offset = [BitConverter]::ToUInt32($approvedIcon, $entry + 12)
    $image = [MirrorlyLauncherResources]::Read($launcher, 3, [BitConverter]::ToUInt16($group, $groupEntry + 12))
    $expected = [byte[]]$approvedIcon[$offset..($offset + $length - 1)]
    if ([Convert]::ToBase64String($image) -cne [Convert]::ToBase64String($expected)) { throw 'Root launcher icon differs from the approved icon.' }
}
$assembly = [IO.File]::ReadAllBytes((Join-Path $gui 'Mirrorly.Desktop.dll'))
if (-not [Text.Encoding]::UTF8.GetString($assembly).Contains('PortableRelease')) { throw 'Not a PortableRelease assembly.' }
foreach ($file in @('app/python/python.exe', 'app/python/python313.dll', 'app/python/python313.zip',
    'app/python/python313._pth', 'app/python/packages/blake3/blake3.cp313-win_amd64.pyd',
    'app/worker/mirrorly/worker/payload_launch.py', 'python-inventory.json', 'README.txt', 'LICENSE.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $payload $file) -PathType Leaf)) { throw "Missing runtime payload: $file" }
}
$pth = [IO.File]::ReadAllLines((Join-Path $payload 'app/python/python313._pth'))
if (($pth -join '|') -cne 'python313.zip|.|packages|../worker') { throw 'Python import isolation changed.' }
$forbiddenPaths = @('C:\Users\sakur\', '.codex\worktrees', 'P:\DevProjects\Mirrorly',
    [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent)))
foreach ($item in Get-ChildItem -LiteralPath $payload -Recurse -Force) {
    $relative = [IO.Path]::GetRelativePath($payload, $item.FullName).Replace('\', '/')
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Payload must not link outside staging: $relative" }
    if ($relative -match '(^|/)(\.git|\.pytest_cache|__pycache__|\.nuget|tests?|obj|bin|logs?)(/|$)') { throw "Development/state directory in payload: $relative" }
    if ($item.PSIsContainer) { continue }
    if ($item.Extension -in @('.cs', '.xaml', '.csproj', '.sln', '.pdb', '.ps1', '.log', '.tmp', '.pfx', '.pem', '.key', '.whl', '.pyc', '.toml')) { throw "Non-runtime file in payload: $relative" }
    if ($relative -notmatch '^app/(gui|python|worker)/' -and $relative -notin @('Mirrorly.exe', 'python-inventory.json', 'README.txt', 'LICENSE.txt', 'SHA256SUMS.txt')) { throw "Unexpected payload file: $relative" }
    # Installed worker/dependency .py modules are runtime files, not a source checkout.
    $bytes = [IO.File]::ReadAllBytes($item.FullName)
    foreach ($encoding in @([Text.Encoding]::UTF8, [Text.Encoding]::Unicode)) {
        $text = $encoding.GetString($bytes).Replace('/', '\')
        foreach ($path in $forbiddenPaths) {
            if ($text.Contains($path, [StringComparison]::OrdinalIgnoreCase)) { throw "Build-machine path found in payload: $relative" }
        }
    }
}
# Invoke the actual bundled interpreter with no PATH lookup and an unrelated cwd.
$python = Join-Path $payload 'app/python/python.exe'
$start = [Diagnostics.ProcessStartInfo]::new($python)
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.WorkingDirectory = $env:SystemRoot
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
foreach ($argument in @('-I', '-B', '-u', (Join-Path $payload 'app/worker/mirrorly/worker/payload_launch.py'),
    '--expected-interpreter', $python, '--payload-root', (Join-Path $payload 'app'), '--probe')) { $start.ArgumentList.Add($argument) }
$process = [Diagnostics.Process]::Start($start)
try {
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit(30000)) { throw 'Payload qualification timed out.' }
    $facts = $stdout.GetAwaiter().GetResult() | ConvertFrom-Json
    if ($process.ExitCode -ne 0 -or $stderr.GetAwaiter().GetResult().Length -ne 0 -or $facts.mode -ne 'payload' -or $facts.executable -ne $python) { throw 'Payload qualification failed.' }
    Write-Output "PASS: Release payload, isolated Python $($facts.python_version), BLAKE3 $($facts.blake3_version); no development path found."
} finally { $process.Dispose() }
