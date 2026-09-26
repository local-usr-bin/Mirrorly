# Mirrorly desktop — Phase 3E development implementation

Real Backup Setup uses production preflight/create and a Python-owned task catalog.
Home rereads durable GUI configuration, including after restart. Setup creates no
snapshot. Real `Back up now` uses production `backup.run`; an app-scoped, memory-only
FIFO queues other configured Backups while one runs. The worker retains one
execution slot and no Backup queue. Restore selection uses production
`restore.prepare`; explicit final confirmation executes its worker-owned plan
through `restore.execute`. Restore and Backup GUI admission are mutually
exclusive. Verify, Restore cancellation/progress and persistent Activity remain
later work.
See [PHASE4C](../docs/gui/PHASE4C.md) for the accepted FIFO and development smoke results.
The complete Backups collection opens a selector-specific read-only Overview using
the existing task catalog and authoritative latest-saved summary. It shares the
same Backup coordinator; its Snapshots section reads production snapshot pages.

One app-owned DesktopSession starts its qualified worker lazily. Development uses
the explicit mirrorly-gui-dev interpreter; Release and PackagingWorkerPoC use payload-local Python.
FakeWorkerClient, phase1a_worker and fixtures remain tests/design infrastructure only.
Normal runtime never displays sample tasks/history or starts the fake worker.
Debug diagnostics expose production observation, window sizing and true Exit.

The existing Visual Studio 2026 x64 build and package identity remain development
infrastructure. Build with VS MSBuild /t:Build /p:Configuration=Debug /p:Platform=x64.
Run Debug in the registered package context; do not bare-launch the packaged Debug
EXE. The separate **PackagingPoC** configuration can publish an unpackaged,
dual-self-contained GUI with the worker intentionally disabled. It is not a
production Release or a usable portable backup application. See
[P1 deployment PoC](../docs/gui/DEPLOYMENT_POC.md) for commands and the measured
acceptance. The separate **PackagingWorkerPoC** configuration adds a pinned,
isolated Python runtime and real production worker under `app/python` and
`app/worker`; its GUI lives under `app/gui`. See
[P2 worker deployment PoC](../docs/gui/WORKER_DEPLOYMENT_POC.md) for assembly,
qualification and pending clean-VM acceptance. Neither PoC is final Release.
The **Release** configuration now uses the proven unpackaged runtime and
payload-local worker path. Build the complete portable folder with
[Build-Portable.ps1](Build-Portable.ps1); see [portable Release](../docs/gui/PORTABLE_RELEASE.md)
for its command, layout and remaining release acceptance. A GUI publish alone
does not assemble Python/worker and is not a complete portable artifact.
For a disposable Debug GUI smoke, use [Launch-DebugGui.ps1](Launch-DebugGui.ps1)
with an absolute `-TestDataRoot`; it verifies that the current user's registered
package points to this checkout before launching. Run
`desktop/Launch-DebugGui.Tests.ps1` to check that qualification without launching
the app. Do not start the Debug EXE with `Start-Process` or `&` directly.

Tests use the dependency-free executable harness:

```powershell
dotnet run --project desktop/Mirrorly.Desktop.Tests -- 'C:\Users\sakur\anaconda3\envs\mirrorly-gui-dev\python.exe'
```

The historical fake-client tests retain their original interpreter intentionally.
All production worker tests and Python regression use mirrorly-gui-dev. No C# TOML
parser or second Backup implementation exists. Semantic resources, reusable cards
and independent floral SVG assets preserve the approved Visual Baseline v1.
