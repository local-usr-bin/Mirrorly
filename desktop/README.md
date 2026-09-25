# Mirrorly desktop — Phase 3E development implementation

Real Backup Setup uses production preflight/create and a Python-owned task catalog.
Home rereads durable GUI configuration, including after restart. Setup creates no
snapshot. Real `Back up now` uses production `backup.run`; an app-scoped, memory-only
FIFO queues other configured Backups while one runs. The worker retains one
execution slot and no Backup queue. There is no verify/restore GUI, progress,
cancellation or persistent Activity. See [PHASE4C](../docs/gui/PHASE4C.md) for
the accepted FIFO and development smoke results.

One app-owned DesktopSession starts the qualified mirrorly-gui-dev interpreter lazily.
FakeWorkerClient, phase1a_worker and fixtures remain tests/design infrastructure only.
Normal runtime never displays sample tasks/history or starts the fake worker.
Debug diagnostics expose production observation, window sizing and true Exit.

The existing Visual Studio 2026 x64 build and package identity remain development
infrastructure. Build with VS MSBuild /t:Build /p:Configuration=Debug /p:Platform=x64.
Run the registered package context; this machine's direct bare EXE launch still fails
Windows App SDK initialization. O-09 final Python packaging remains OPEN.
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
