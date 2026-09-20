# Mirrorly desktop — Phase 1B visual prototype

This is a **local development prototype**, not the Mirrorly backup application.
It never imports or calls the existing Python core. No backup, restore, verify,
repository writes, real cancellation, queue, or persistent Activity exists here.

Open `Mirrorly.Desktop.slnx` in Visual Studio 2026 and select x64. The desktop
project uses the packaged launch profile. Home now uses sample presentation data;
Backups, Restore, Activity and Settings are placeholders. The current visual scope
and verification are recorded in [PHASE1B](../docs/gui/PHASE1B.md). The frozen
technical-slice acceptance remains in [PHASE1A](../docs/gui/PHASE1A.md).

## Visual review

Home opens with the healthy, one-backup fixture. Sample actions show a prototype
notice; they never access the displayed paths. In **Debug only**, open **Developer
diagnostics** at the bottom of the navigation pane. Its fixture selector covers
healthy, destination unavailable, failed, completed with issues, saved with a
finalization problem, running, queued, two/three/many backups, empty and long paths.
Selecting a fixture returns to Home. Resize controls provide a small review window
or restore the normal window. The page also retains the Phase 1A IPC/notification
test controls and reports the actual XAML DPI scale. Release has no diagnostics entry.

Use `Themes/MirrorlyResources.xaml` for semantic colors, spacing, typography and
radii; `Presentation/HomePresentation.cs` for fixture data, preview limits and
responsive policy; `Components/BackupSummary` for summary layouts; and
`Assets/Decorations/*.svg` for the two original botanical illustrations, with
`Components/SpringSprig` as their shared noninteractive SVG presenter. Placement,
scale and visibility stay in the central resources/presentation policy. Visual
Baseline v1 supersedes v0; static Home contains no detached petals. Future motion
is documented in [MOTION](../docs/gui/MOTION.md), not implemented.
Views express layout and harmless intentions, not backup orchestration. Light is
the current visual target; High Contrast uses system colors. Dark is only a
resource fallback, not an advertised theme option.

## Build and package

From this directory in PowerShell, using the installed VS MSBuild:

```powershell
$msbuild = 'C:\Program Files\Microsoft Visual Studio\18\Community\MSBuild\Current\Bin\MSBuild.exe'
& $msbuild Mirrorly.Desktop/Mirrorly.Desktop.csproj /t:Restore /p:Platform=x64 /p:RestoreLockedMode=true
& $msbuild Mirrorly.Desktop/Mirrorly.Desktop.csproj /t:Build /p:Configuration=Debug /p:Platform=x64
& $msbuild Mirrorly.Desktop/Mirrorly.Desktop.csproj /t:Build /p:Configuration=Debug /p:Platform=x64 /p:GenerateAppxPackageOnBuild=true /p:UapAppxPackageBuildMode=SideloadOnly
```

The MSIX is under `AppPackages/Mirrorly.Desktop_0.0.1.0_x64_Debug_Test/`.
It is **unsigned**. Do not run the generated install scripts expecting a signed
installer. This proves package production; clean-machine installation and signing
remain separate work. Symbols-package generation is disabled; a C++ symbols tool
is not needed for this C#/WinUI slice. The SDK still emits a missing `mspdbcmf.exe`
warning from its packaging prerequisite target. Normal managed PDBs remain in the build.

## Current-user debug deployment and launch

With Developer Mode already enabled, an authorized local developer can register
the loose debug package without generating or trusting a certificate:

```powershell
$manifest = Join-Path $PWD 'Mirrorly.Desktop/bin/x64/Debug/net10.0-windows10.0.19041.0/win-x64/AppxManifest.xml'
Add-AppxPackage -Register $manifest
$package = Get-AppxPackage -Name Mirrorly.TechnicalPrototype
Start-Process explorer.exe -ArgumentList "shell:AppsFolder\$($package.PackageFamilyName)!App"
```

Use the registered Start menu/package entry or VS packaged profile, **not** the
bare EXE. Direct EXE startup in this environment failed with `REGDB_E_CLASSNOTREG`
in App SDK deployment initialization; packaged activation succeeded.
Run unelevated for notifications. Exit through the tray before rebuilding files
used by the running app. Registration affects only this prototype identity.

## Worker configuration

`Services/PrototypeConfiguration.cs` deliberately names the approved interpreter:

`C:\Users\sakur\anaconda3\envs\mirrorly\python.exe`

MSBuild embeds the **absolute path to this checkout's** `phase1a_worker/worker.py`
in assembly metadata. The client launches it with `-I -u`, with redirected stdio
and no console window. It does not depend on `import mirrorly`, the working
directory's import lookup, or the existing editable install.

Consequently this test package is tied to this development machine/checkout.
Moving it to another PC does not yet distribute Python or the worker. That is a
known distribution gap, not a reason to change the Python core.
Single-project MSIX output does not settle final worker distribution: **O-09 stays
OPEN**, with multi-executable packaging and unpackaged/installer approaches still
undecided. Phase 1B does not change the package layout or dependencies.

## Tests

```powershell
dotnet run --project Mirrorly.Desktop.Tests/Mirrorly.Desktop.Tests.csproj
& 'C:\Users\sakur\anaconda3\envs\mirrorly\python.exe' -m pytest phase1a_worker -q
```

The C# test project is a small executable harness (exit 1 on failure), with no
third-party test framework. It links the actual non-UI services/ViewModel and
spawns the same explicit fake worker for integration tests. Phase 1B adds fixture,
navigation and responsive presentation coverage (17 tests total). Use `dotnet run`, not
`dotnet test`; this harness is not a Visual Studio Test Explorer adapter.

Package assets are unmodified placeholders from the installed Microsoft C# WinUI
packaged template. They are not Mirrorly branding. `bin`, `obj`, `AppPackages`,
certificates and local IDE state are excluded from Git.
Phase 1B local screenshots are in `artifacts/phase1b-review/` (also excluded); see
the verification record for which states and system display settings were observed.
The subsequent v1 fidelity-pass captures are in `artifacts/phase1b-fidelity-v1/`;
use that set for current visual review rather than the earlier provisional artwork.
