# Mirrorly P2 Diagnostics Kit (developer/test tool)

This standalone kit is **not a Mirrorly product feature or release payload**.
Keep it separate from the portable ZIP; copy this tool directory separately to
a test machine when needed. Mirrorly.Desktop does not reference it. P2 visual
corruption remains **MONITORING / non-blocking for v1**. This kit does not fix it.

## Collect an existing incident

Keep the affected Mirrorly session open. Take a screenshot separately (avoid
personal information) and note the time and last operation. Do not restart just
to enable logging: that loses the current process evidence.

Double-click `Collect-P2-Diagnostic.cmd`, or use **64-bit Windows PowerShell 5.1**:

```powershell
& 'D:\Developer tools\Mirrorly.Diagnostics\Collect-P2-Diagnostic.ps1'

# Narrow to the affected GUI PID, including that PID's recent logs:
& 'D:\Developer tools\Mirrorly.Diagnostics\Collect-P2-Diagnostic.ps1' `
    -MirrorlyProcessId 1234 -OutputDirectory 'D:\Local diagnostic evidence'
```

Paths are examples. The scripts locate each other using their own location;
cwd may be the portable directory or anywhere else. There is no installation,
Python/.NET SDK requirement, registry modification, network request, auto-upload,
service or monitoring loop. The CMD wrapper selects x64 Windows PowerShell and
uses a **process-only** execution-policy bypass; no persisted policy is changed.
Managed policy or Constrained Language may still prevent execution/Add-Type.
Do not weaken machine policy; native-query failures are reported as unavailable.

Default output:

```text
%TEMP%\Mirrorly-DiagnosticCollections\
  Mirrorly-P2-Diagnostic-yyyyMMdd-HHmmss.zip
  Mirrorly-P2-Diagnostic-yyyyMMdd-HHmmss\
    environment\windows.txt, hardware.txt, display.txt, dpi.txt
    process\mirrorly.txt, modules.txt
    diagnostics\inventory.txt, setup-<PID>-<GUID>.log (when available)
    metadata\timestamp.txt, collection-notes.txt
```

Both the folder and ZIP remain local; nothing is automatically deleted. Existing
outputs are never overwritten. A same-second collision asks you to retry.
Section failures are recorded as `unavailable`; a non-writable output location or
ZIP failure still returns an error. If ZIP creation fails, keep the evidence folder.

## Existing application diagnostics

The portable Release already contains Setup T0-T8, selection/navigation S0-S2 /
N0-N3, focus F0-F5, FolderBrowser/container/ScrollViewer and shell snapshots.
The existing switch is **exactly** `MIRRORLY_SETUP_DIAGNOSTICS=1`, read at logger
initialization. There is no runtime toggle or retroactive logging. The collector
does not enable it, launch Mirrorly, or change the current session.

For a later, explicitly chosen diagnostic session, first exit Mirrorly normally.
In PowerShell, launch the existing portable entry with a process-local environment:

```powershell
$previous = $env:MIRRORLY_SETUP_DIAGNOSTICS
try {
    $env:MIRRORLY_SETUP_DIAGNOSTICS = '1'
    Start-Process -FilePath 'D:\Mirrorly\app\gui\Mirrorly.Desktop.exe'
} finally {
    $env:MIRRORLY_SETUP_DIAGNOSTICS = $previous
}
```

The app writes `%TEMP%\Mirrorly-SetupDiagnostics\setup-<PID>-<GUID>.log` only
after an instrumented event. Its unchanged limits are 512 records, 256 KiB per
process and 4096 characters per record. Long sessions can exhaust the logger;
no log does not prove no incident. No new runtime build is needed.

## Evidence and privacy boundaries

- Windows edition/build/architecture; CPU model/core counts, total RAM, GPU and
  driver information. No hostname, username field, serials, EDID or network inventory.
- Active monitor device names, primary flag, bounds/work areas and reported DPI.
  Monitor DPI/coordinates may depend on the collector's DPI-awareness context;
  they are labelled accordingly. GPU resolution fields are not authoritative
  multi-monitor topology. VM queries describe the guest, not the host GPU/display.
- Target window `GetDpiForWindow`, client/window rects when a main window exists.
  DPI/96 is the window effective scale, not Windows Text Size or XAML root width.
  Nothing calls SetWindowPos, Focus, UpdateLayout or changes DPI awareness.
- Every matching Mirrorly.Desktop PID (or `-MirrorlyProcessId` subset), start time,
  executable path/version/SHA-256; managed assembly SHA-256/ProductVersion if present.
  Version alone is not used as artifact identity.
- Only `hostfxr.dll`, `hostpolicy.dll`, `coreclr.dll`, `Microsoft.UI.Xaml.dll`,
  `Microsoft.WindowsAppRuntime.dll`: path, version and GUI-directory-local versus
  outside-GUI origin. This classification does not identify the cause of corruption.
- Default: newest 20 correctly named setup logs modified within 24 hours, <=256 KiB
  each (at most 5 MiB), from the current user's diagnostic TEMP directory only.
  `-LogAgeHours 1..168` changes the time window; optional PID filter also applies.
  No recursive search. Reparse-point log roots/files and oversized logs are skipped.
  Closed-session logs can be collected; PID reuse requires timestamp comparison.
- Log bytes are copied unchanged, with original creation/modification UTC times
  and copied-byte hashes in the inventory. A live file is a bounded prefix and can
  end mid-record. The ZIP's filesystem timestamp precision may differ; the inventory
  retains UTC metadata. No task configs, source trees, repositories or user files are read.

**Review before sharing.** Requested executable/module paths can expose a Windows
username or chosen install directory. Existing logs are copied verbatim; the kit
does not promise to sanitize arbitrary files placed in the diagnostic directory.
Raw collection exception messages, process command lines, window titles, complete
environment variables, memory dumps and UI Automation trees are not collected.

Run as the same user, without Administrator by default. Module enumeration may
fail for exited/inaccessible processes. The kit records unavailable and never
elevates itself; only consider an explicitly authorized elevated retry if necessary.
Missing data does not fail other sections. The snapshot is sequential, not atomic.

## Validation (no third-party test dependencies)

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Test-DiagnosticKit.ps1
```

The test uses isolated TEMP/TMP and synthetic logs, executes the real collector,
checks ZIP structure, byte/timestamp preservation, log bounds/privacy exclusions,
no-log/reparse-root cases and cwd/path independence. Its artifacts remain in a
unique temporary directory, printed at completion. It does not launch or alter
Mirrorly. Real live-GUI module/window capture and multi-monitor/VM behavior need
manual acceptance on those machines. No application suites/builds are required
for this external-only tool.
