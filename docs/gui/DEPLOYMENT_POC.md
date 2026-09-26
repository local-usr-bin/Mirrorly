# P1 — unpackaged GUI deployment PoC

Status: **CLOSED / PASS**, 2026-09-26. Local GUI deployment, tray Open/Exit and
clean-machine self-contained deployment acceptance passed. Clean-VM evidence
below was reported by the user after manual testing. This is not a production ZIP.
Python/worker deployment, launcher, single instance, notifications, signing and
installer remain P2/P3/later work. Backup/Restore semantics are unchanged.

## Separate development and PoC modes

- **Debug** remains packaged, with the registered development package,
  [Launch-DebugGui.ps1](../../desktop/Launch-DebugGui.ps1), explicit development
  Python and the existing harness. Never bare-launch this Debug executable.
- **PackagingPoC** is an explicit project configuration, not the normal Release
  path. `PACKAGING_POC` selects a session without a worker launch configuration.
  Every application request fails before process startup or IPC send. Home shows
  unavailable facts, not fixture tasks or an empty successful catalog. The title
  says `GUI deployment PoC (worker disabled)`. No task data is loaded or written.
- Normal **Release** does not opt into this isolation or unpackaged publishing;
  it is still development-bound and is not ready for public distribution.

The existing Home catalog-error projection had overwritten its technical details
and fallen through to `Backup set up`. P1 retains that error and presents
`Backups unavailable`, after existing operation/queue state precedence. This is a
truthfulness fix for unavailable workers, not a new Backup execution path.

## Build and inspect

Use Visual Studio MSBuild (the WinUI XAML compiler requires this toolchain here).
From the repository root, in PowerShell:

```powershell
$msbuild = 'C:\Program Files\Microsoft Visual Studio\18\Community\MSBuild\Current\Bin\MSBuild.exe'
$project = 'desktop\Mirrorly.Desktop\Mirrorly.Desktop.csproj'
& $msbuild $project /restore /t:Publish /p:Configuration=PackagingPoC /p:Platform=x64
& desktop/Test-PackagingPoC.ps1 -MSBuildPath $msbuild -PublishDirectory (Join-Path (Get-Location) 'desktop/artifacts/packaging-poc/publish')
```

Restore needs access to the user's NuGet configuration. On this sandbox, that
read was denied; the successful publish used the already restored, unchanged
dependency graph by omitting `/restore`. This is not a general instruction to
skip restoring a new checkout or changed dependencies.

The output is ignored by Git. Use a fresh output directory for qualification;
copy the **whole** output, including locale directories, PRI, XBF and assets.
Only this PoC EXE may be launched directly, with any working directory. Do not
use the Debug package launcher for it. Do not commit runtime binaries.

```powershell
$publish = Join-Path (Get-Location) 'desktop/artifacts/packaging-poc/publish'
Start-Process -FilePath (Join-Path $publish 'Mirrorly.Desktop.exe') -WorkingDirectory $env:SystemRoot
```

## Evaluated initialization and resource contract

Fixed package: `Microsoft.WindowsAppSDK 2.5.1`; RID `win-x64`.

| Evaluated property | PackagingPoC |
| --- | --- |
| WindowsPackageType | None |
| EnableMsixTooling | false |
| SelfContained | true (.NET) |
| WindowsAppSDKSelfContained | true (Windows App SDK) |
| WindowsAppSdkUndockedRegFreeWinRTInitialize | true |
| WindowsAppSdkBootstrapInitialize | false |
| WindowsAppSdkDeploymentManagerInitialize | false |
| PublishTrimmed / PublishReadyToRun / PublishSingleFile | false |
| DEBUG constant | absent |
| CheckoutRoot / Phase1AWorkerPath assembly metadata | absent |

The cached SDK targets enable registration-free WinRT for self-contained apps;
the shared-framework bootstrap and deployment-manager recipes are excluded.
P1 makes those choices explicit instead of adding a custom initializer.
See [Microsoft self-contained guidance](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/self-contained-deploy/deploy-self-contained-apps).

The first publish omitted generated application PRI/XBF although build produced
them. Runtime then failed with `0xc000027b` in the **payload-local** WinUI DLL.
`PublishPackagingPocXamlResources` now includes the generated app PRI and XBF
with their original relative layout. The fixed publish launches; the original
`ms-appx:///Assets/...` SVG paths work without changes to the asset system.
The artifact test checks the PRI/XBF and every decoration SVG, not only DLLs.

Representative published files: `coreclr.dll`, `hostfxr.dll`, `hostpolicy.dll`,
`Microsoft.UI.Xaml.dll`, `Microsoft.WindowsAppRuntime.dll`, `Microsoft.UI.pri`,
`Mirrorly.Desktop.pri`, `App.xbf`, `MainWindow.xbf`, and theme/component XBF.
The runtime config has `includedFrameworks` for `Microsoft.NETCore.App 10.0.12`,
and no shared `framework`/`frameworks` requirement. A bootstrap DLL can be present
as a dependency file without the bootstrap initializer being enabled.

## Measured acceptance

Host: Windows build 26200, x64, window DPI 168 (175%). The host has installed
Windows App Runtime packages, including 2.5.1; it is **not a clean machine**.

| Check | Result |
| --- | --- |
| Direct EXE startup, no package launcher | PASS |
| Process package identity | `GetPackageFullName` returned 15700 (no package) for all six path probes |
| Loaded .NET / WinUI / Windows App Runtime | All five representative modules loaded from each probe's own folder |
| Modules from WindowsApps or dotnet/shared | None observed in the six probes |
| Python/worker child process | None in the six probes |
| Home, NavigationView, light theme, XAML/PRI, two SVG decorations | Visually verified |
| Minimize and restore window | Observed on initial fixed publish |
| Close X hides window without exiting process | Observed on initial fixed publish |
| Tray Open / Exit | PASS: user manually confirmed Open and Exit; the original hidden process was subsequently confirmed exited |
| Duplicate launch | Two live windows per probe path, no immediate crash; single-instance policy is not implemented |
| High Contrast / alternate DPI | Not verified; only current 175% DPI observed |
| Clean Windows VM with no shared runtimes | PASS on the specific Windows 10 VM documented below; user-reported manual acceptance |

Three copies under the repository's ignored disposable root were each launched
with their own folder and unrelated `C:\Windows` as cwd:

- `.pytest_tmp/p1-ascii`
- `.pytest_tmp/p1 spaces`
- `.pytest_tmp/临时 软件/Mirrorly PoC`

All six had live windows, correct resources and payload-local runtime modules.
This supports local relocation/cwd independence of the **GUI layer only**. It
does not test post-use business state relocation, production Python, or prove
the absence of all machine prerequisites. No host runtime was uninstalled.

## Clean-VM closure acceptance

The user reported the following manual results on Windows 10 Pro, reported
version 2009, build 19045, 64-bit. This establishes deployment behavior on this
specific VM; it does **not** declare official Windows 10 support. O-05 and the
release acceptance matrix still determine the supported OS scope.

Before testing, `dotnet` was absent and neither `C:\Program Files\dotnet` nor
`C:\Program Files (x86)\dotnet` existed. `Get-AppxPackage *appruntime*` returned
no packages. There was no Mirrorly/TechnicalPrototype development package or
Visual Studio, and no actual Python/Conda runtime. The WindowsApps `python.exe`
entry was only a Windows App Execution Alias. No .NET, Windows App SDK, Python,
Visual Studio or development MSIX was installed during acceptance.

The closure publish was rebuilt from commit
`935a289e35ca5dc0a26d79b50b478c0ffe31323c` into the ignored
`desktop/artifacts/p1-closure/publish` directory using the same MSBuild publish
command above, without `/restore` and with an explicit `PublishDir`. Its 533
payload files totaled 239,839,149 bytes. The transfer ZIP added `SHA256SUMS.txt`
and totaled 95,642,642 bytes. Its SHA-256 matched on the VM:

```text
BC5162561B1144B32A08BA495962125C43937BB875E1327F384CF073E91B2DDA
```

| Clean-VM check | User-reported result |
| --- | --- |
| Extracted payload | `C:\临时 软件\Mirrorly PoC\`; `Integrity mismatches: 0` |
| Direct startup in Chinese + spaces path | PASS; no package-context launcher |
| Home / NavigationView / palette / XAML / PRI / both SVG plants | PASS |
| PoC worker boundary | Truthful worker-disabled state; no fake worker or development Python fallback |
| Minimize | PASS |
| Close X / tray Open / tray Exit | Window hid while process remained; Open restored the same session; Exit terminated normally |
| Unrelated cwd | After full Exit, direct launch from `C:\Windows` passed with correct UI/resources and executable path still under the copied payload |
| Package identity | `GetPackageFullName` returned 15700 (`APPMODEL_ERROR_NO_PACKAGE`) |

All five inspected runtime modules loaded from the copied payload:

```text
C:\临时 软件\Mirrorly PoC\hostfxr.dll
C:\临时 软件\Mirrorly PoC\hostpolicy.dll
C:\临时 软件\Mirrorly PoC\coreclr.dll
C:\临时 软件\Mirrorly PoC\Microsoft.WindowsAppRuntime.dll
C:\临时 软件\Mirrorly PoC\Microsoft.UI.Xaml.dll
```

Together with the pre-test inventory and successful direct startup, these paths
confirm that this VM run did not require shared .NET or Windows App Runtime
deployment. Windows system DLLs are expected to remain OS-provided.

## Validation and follow-on boundary

- Full C# harness with explicit `mirrorly-gui-dev` Python: **118 passed / 0 failed**.
- Packaged Debug x64 build and Debug package-launch qualification: passed.
- PackagingPoC publish: **0 warnings / 0 errors**.
- Evaluated configuration / artifact tests: passed.
- No Python source change; no full Python suite rerun.

The suite/build results above belong to P1 implementation. Closure acceptance
reran PackagingPoC publish and `Test-PackagingPoC.ps1`, both successfully; it did
not rerun the full suites or packaged Debug build. No source defect was found;
the closure change records acceptance only. Disposable runtime output is not
tracked in Git.

P1 is closed for the GUI deployment layer. Keep the PoC flag out of the future
production configuration. P2 remains separate and must replace the disabled
worker with an explicit payload-local launch/qualification boundary, without
changing the worker protocol, operation semantics or development workflow.
Production Python, the final ZIP/launcher, single instance, notifications,
signing, installer and full production-payload release acceptance remain outside
this P1 result.
