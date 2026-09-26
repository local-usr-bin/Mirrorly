# P1 — unpackaged GUI deployment PoC

Status: **PARTIAL**, 2026-09-26. Local GUI deployment works; clean-machine proof
and full tray menu acceptance are not established. This is not a production ZIP.
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
| Tray Open / Exit | Not verified; taskbar is not targetable through the current automation surface |
| Duplicate launch | Two live windows per probe path, no immediate crash; single-instance policy is not implemented |
| High Contrast / alternate DPI | Not verified; only current 175% DPI observed |
| Clean Windows VM with no shared runtimes | Not verified; no accessible VM established |

Three copies under the repository's ignored disposable root were each launched
with their own folder and unrelated `C:\Windows` as cwd:

- `.pytest_tmp/p1-ascii`
- `.pytest_tmp/p1 spaces`
- `.pytest_tmp/临时 软件/Mirrorly PoC`

All six had live windows, correct resources and payload-local runtime modules.
This supports local relocation/cwd independence of the **GUI layer only**. It
does not test post-use business state relocation, production Python, or prove
the absence of all machine prerequisites. No host runtime was uninstalled.

## Validation and remaining gate

- Full C# harness with explicit `mirrorly-gui-dev` Python: **118 passed / 0 failed**.
- Packaged Debug x64 build and Debug package-launch qualification: passed.
- PackagingPoC publish: **0 warnings / 0 errors**.
- Evaluated configuration / artifact tests: passed.
- No Python source change; no full Python suite rerun.

Before P1 can be PASS, repeat on a clean Windows VM without Visual Studio, .NET
SDK/runtime, Mirrorly development package, Conda or installed Windows App SDK
runtime, and finish tray Open/Exit acceptance. Inventory the VM first; do not
uninstall development-host runtimes to simulate this. Keep the PoC flag out of
the future production configuration. P2 must replace the disabled worker with
an explicit payload-local launch/qualification boundary, without changing the
worker protocol, operation semantics or development workflow.
