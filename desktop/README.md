# Mirrorly desktop — Phase 3E development implementation

Real Backup Setup now uses production preflight/create and Python-owned task catalog.
Home rereads durable GUI configuration, including after restart. Setup creates no
snapshot. Back up now is disabled. No backup.run, verify/restore, queue, progress,
cancellation or Activity persistence is exposed. See [PHASE3E](../docs/gui/PHASE3E.md)
for ownership, failure states, validation and the isolated GUI smoke launch command.

One app-owned DesktopSession starts the qualified mirrorly-gui-dev interpreter lazily.
FakeWorkerClient, phase1a_worker and fixtures remain tests/design infrastructure only.
Normal runtime never displays sample tasks/history or starts the fake worker.
Debug diagnostics expose production observation, window sizing and true Exit.

The existing Visual Studio 2026 x64 build and package identity remain development
infrastructure. Build with VS MSBuild /t:Build /p:Configuration=Debug /p:Platform=x64.
Run the registered package context; this machine's direct bare EXE launch still fails
Windows App SDK initialization. O-09 final Python packaging remains OPEN.

Tests use the dependency-free executable harness:

```powershell
dotnet run --project desktop/Mirrorly.Desktop.Tests -- 'C:\Users\sakur\anaconda3\envs\mirrorly-gui-dev\python.exe'
```

The historical fake-client tests retain their original interpreter intentionally.
All production worker tests and Python regression use mirrorly-gui-dev. No C# TOML
parser or second Backup implementation exists. Semantic resources, reusable cards
and independent floral SVG assets preserve the approved Visual Baseline v1.
