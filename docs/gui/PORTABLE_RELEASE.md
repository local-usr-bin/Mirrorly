# Portable Release build

`Release` is the Windows x64 portable configuration. It promotes the existing P2
payload layout, worker qualification and P1 runtime initialization. It is
unpackaged, .NET self-contained and Windows App SDK self-contained; no shared
runtime, registered development package, PATH Python or checkout is required at
runtime. Debug remains packaged; PackagingPoC and PackagingWorkerPoC remain
independent regression configurations.

## Build

From the repository root in PowerShell 7, supply absolute build-tool paths and a
new absolute output directory:

```powershell
& ./desktop/Build-Portable.ps1 -Python $buildPython -MSBuildPath $msbuild `
  -OutputDirectory (Join-Path (Get-Location) 'desktop/artifacts/Mirrorly-portable') -Zip
```

The build Python needs the existing pip/setuptools tooling. Visual Studio MSBuild
provides the WinUI compiler and the MSVC v145 x64 C++ tools/Windows SDK for the
native launcher. These are build dependencies only. NuGet uses the
committed lock file. Python 3.13.15 x64 and BLAKE3 1.0.9 use the same pinned URLs,
hashes and cache as [P2](WORKER_DEPLOYMENT_POC.md); no new runtime or dependency
resolution path is introduced. Existing output is never overwritten.

The script assembles the normal Mirrorly wheel with
`scripts/build_worker_payload.py`, publishes Release, builds/stages the native
root launcher, checks the complete payload,
and writes SHA256SUMS.txt. Optional ZIP creation is a mechanical compression of
that directory to its sibling `.zip`. Runtime files and artifacts stay ignored.
`python-inventory.json` preserves dependency licenses, source HEAD/dirty status
and worker hashes. A candidate built before commit records that fact honestly.

## Run and move

```text
Mirrorly-portable/
  Mirrorly.exe
  README.txt, LICENSE.txt, SHA256SUMS.txt, python-inventory.json
  app/
    gui/       Mirrorly.Desktop.exe, .NET/Windows App SDK, PRI/XBF, assets
    python/    pinned embedded Python, isolated _pth, BLAKE3, licenses
    worker/    installed Mirrorly runtime package and distribution metadata
```

Extract the entire directory and open **`Mirrorly.exe`** in its root. `app/` is
internal payload; users do not need to launch `app/gui/Mirrorly.Desktop.exe`
directly. No installer or registration step is required. Internal GUI paths
derive from AppContext.BaseDirectory through
the unchanged ProductionPayloadPaths. Worker launch uses the unchanged isolated
`-I -B -u` bootstrap and fail-closed production qualification, with no fallback.

The native x64 launcher uses its own executable directory, an explicit child EXE
path and `app/gui` as child cwd. It forwards the original Unicode argument tail
without reparsing it. It displays a Windows error dialog and returns nonzero on
launch failure; after successful process creation it closes its handles and exits
zero without supervising or waiting for the GUI. The CRT is statically linked;
the launcher needs no installed .NET or Visual C++ runtime. It reuses the approved
Mirrorly icon and does not read or change task/user state.

The installed Python `.py` modules are executable runtime contents. They are
deliberately retained, together with distribution metadata, rather than replaced
with a new bytecode-only distribution. The source checkout, C#/XAML source,
projects/tests, caches, PDBs, build logs, credentials and user state are excluded.
SDK-provided runtime components are retained without speculative trimming.

Exit before moving/replacing the folder. Local task configuration remains at
`%LOCALAPPDATA%/Mirrorly/Gui/Tasks`; Source and repository paths are not rewritten.
Copying the program to another computer does not migrate local user state.

## Validation and acceptance boundary

`desktop/Test-Portable.ps1` reuses the evaluated Debug/P1/P2/Release deployment
checks, verifies runtime/resources and the Release marker, scans staged files for
development paths and non-runtime artifacts, checks `_pth`, then runs the actual
bundled interpreter's qualification from an unrelated cwd. It does not launch
the GUI, create tasks or perform Backup/Restore. It is a deployment regression
guard, not a signature or a general malware/secret scanner.

It also requires a native x64 root launcher matching the local
`Mirrorly.Launcher/bin/Release/Mirrorly.exe` build and the approved icon. Build that
launcher before validating a supplied staging folder; unexpected root EXEs remain
rejected. Checksums/ZIP include the launcher automatically.

`desktop/Test-Portable.Tests.ps1` copies the complete output to a disposable
Chinese/space path and verifies qualification there, plus rejection of changed
import isolation, stray source and embedded developer paths. Run both with
`-MSBuildPath $msbuild -PayloadDirectory $absolutePayload`.

`desktop/Test-Launcher.ps1 -MSBuildPath $msbuild -LauncherPath $absoluteLauncher`
builds a test-only native child and uses disposable copies to check real argument
forwarding, child cwd, Unicode/space paths, relocation, immediate launcher exit,
and visible missing/invalid-payload errors. It dismisses only error dialogs owned
by its own test processes. The probe is never staged. Real GUI startup from a
disposable complete portable copy is a separate smoke: test normal/unrelated cwd
and Chinese/space relocation, then use tray Exit without creating tasks.

Existing .NET payload integration and Python payload/worker tests also apply;
set `MIRRORLY_TEST_PAYLOAD` to the assembled directory. They exercise the real
worker, hostile environment, fail-closed qualification and disposable application
operations without depending on system Python for the worker.

The development manifest identity/version stays unchanged because Release uses
unpackaged execution. Diagnostic capability is preserved and remains off unless
`MIRRORLY_SETUP_DIAGNOSTICS=1` is explicitly set. No diagnostic logs are staged.

This build produces a release candidate, not a public-release acceptance claim.
Final icon, clean-VM GUI acceptance, multiple machines, manual post-use move/rename
with existing tasks, and continued P2 visual-corruption monitoring remain separate.
