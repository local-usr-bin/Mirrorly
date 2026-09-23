# Phase 3E — real GUI Setup closed loop

**PASS — real GUI Setup closed loop, including fresh-process rediscovery.**
Current implementation, 2026-09-24. Phase 3D mutation semantics remain unchanged.
Setup now calls production Python; it creates a repository and task configuration,
**not a snapshot**. Back up now is unavailable. No backup.run, verify/restore,
Resume, progress, cancellation, Activity persistence, queue or reconciliation is added.

## Ownership and lifecycle

[App](../../desktop/Mirrorly.Desktop/App.xaml.cs) owns one
[DesktopSession](../../desktop/Mirrorly.Desktop/Services/DesktopSession.cs).
The shell appears before Home lazily starts the qualified production worker.
Navigation, Minimize and Close-to-tray do not dispose the session. No page owns a
worker. Phase 1A FakeWorkerClient/worker and their tests remain intact, but normal
runtime and developer diagnostics no longer start or present them as the app worker.
Fixtures remain test/design data; runtime Home never selects them.

DesktopSession supervises a whole GUI workflow, including interpretation of the
terminal result and success catalog refresh. Busy workflows are rejected, not queued.
Process/session health, request/operation correlation, waiting for terminal, workflow
busy and Exit pending are distinct facts. A 30-second caller wait only changes the
waiting state; it keeps the original request, operation supervision and late result.
There is no automatic restart, resend or stdio reattach.

True Exit while busy asks **Exit after it finishes / Stay in Mirrorly**. Stay changes
nothing. Confirmation stops new user workflows, waits for the current workflow to
process its facts, then requests idle shutdown and waits for worker process completion.
Exit intent cannot be withdrawn. Transport loss is still uncertainty; the GUI waits
for process completion rather than force-killing a surviving operation. A failed
shutdown acknowledgement does not pretend the worker exited. Close X only hides;
Minimize retains normal taskbar behavior. No Backup FIFO exists to clear.

## Setup presentation

[BackupSetupViewModel](../../desktop/Mirrorly.Desktop/ViewModels/BackupSetupViewModel.cs)
uses the app session; filesystem browsing stays the Phase 1C readonly selector.
Entering Review sends the current name/source/target, provider root and existing
`warn` filesystem policy to setup.preflight. This GUI policy makes existing full-copy
approval available, not automatic; CLI defaults are unchanged. Python's prospective
repository path replaces the prototype C# path join. Editing the name invalidates
readiness and requires Check again. Checking/blocked/unavailable disables Create.

Create takes current intent plus an explicit boolean decision, never preflight data.
Copy approval uses a focused dialog explaining lack of NTFS hardlink reuse and the
space consequence. Declining stays on Review. If create discovers a new requirement,
only its explicit decision-required/no-write result permits a new request after
fresh approval. Timeout/disconnect never permits that retry.

The UI separately presents admission busy, lifecycle gate unavailable, failure before
acknowledged initialization, repository created/config incomplete, and unknown effects.
Repository-created/config-write-unknown retains both facts. Unknown or potentially
partial writes disable another Create/Check/Back in the retained workflow; navigating
away does not reset this protection. Technical payload/cause is behind an expander,
not normal user-facing exception text. No rollback or automatic reconciliation exists.

Confirmed success refreshes catalog through Python, returns Home, and clears transient
setup state. Refresh failure does not turn confirmed creation into application failure.
A valid task config must be read before a Home item appears. Back up now, Explorer and
version browsing remain disabled; no repository availability or history is inferred.

## Durable task catalog

[application.tasks.list_tasks](../../src/mirrorly/application/tasks.py) reads only the
explicit absolute config root's config.d. It calls the existing config reader and
returns CatalogEntry(selector, config_path, task or problem), plus a next_after cursor.
Selector (filename stem), TaskConfig.name and lock identity remain distinct. No UUID,
repair, CLI task discovery/import or second GUI business-data registry is introduced.
Missing config.d is an empty catalog; root access errors fail the query; individual
malformed/unreadable entries are problems alongside valid entries.

`tasks.list` is a readonly application method sharing the existing worker slot.
Params: `{config_root: absolute string, after: filename or null}`; after is optional.
Result: `{outcome: succeeded, catalog: {entries: [...], next_after: string or null}}`.
Each entry carries selector, config_path, task `{name, source, target,
configured_repository_path}` or null, and problem or null. The configured repository
path is a Python-derived config fact, not identity/relocation/online verification.

A real test with a valid 40,000-character config field showed an unlimited catalog
cannot safely fit v1 limits. Pages therefore contain at most 16 entries, each under
32 KiB projected JSON and the existing protocol string/node limits. Oversized task
representation becomes an explicit per-entry problem, never truncated valid truth.
Filename ordering/cursors use Python ordinal order, including mixed-case filenames;
C# checks cursor advancement using the same scalar ordering. Pages are observations,
not a transactional snapshot against concurrent external config changes. User refresh
restarts enumeration. No snapshot listing or caching of authoritative task data.

Home uses the durable catalog on startup/refresh and after confirmed Create. It says
**Backup set up**, **Last backup: Not checked**, and explicitly states that setup
creates no snapshot and availability/history were not checked. Activity has no fake
records. Damaged entries and query failure have visible explanations with technical
details. C# never parses or writes TOML.

## Development smoke isolation and validation

Normal root remains `%LOCALAPPDATA%\Mirrorly\Gui\Tasks` behind GuiDataPaths (O-07).
Debug only accepts `--test-data-root <absolute local-data-base>`; this selects a
separate provider base for GUI smoke tests, not portable task configuration.
A process-only MIRRORLY_GUI_TEST_DATA override also exists, but packaged activation
on this machine did not inherit it. Always verify the returned preflight config_path
before creating test artifacts. No normal user root was written during smoke testing.

Use the existing registered package context, not the bare executable (the latter
still fails Windows App SDK auto-initialization on this machine). Example after build:

```powershell
$exe = Join-Path $PWD 'desktop/Mirrorly.Desktop/bin/x64/Debug/net10.0-windows10.0.19041.0/win-x64/Mirrorly.Desktop.exe'
$testBase = Join-Path $PWD '.pytest_tmp/phase3e-gui-smoke/LocalData'
Invoke-CommandInDesktopPackage -PackageFamilyName Mirrorly.TechnicalPrototype_fhsq4wxgq3ejr -AppId App -Command $exe -Args ('--test-data-root "' + $testBase + '"')
```

New coverage: [test_task_catalog](../../tests/test_task_catalog.py),
[DesktopFlowTests](../../desktop/Mirrorly.Desktop.Tests/DesktopFlowTests.cs).
Existing Phase 2 setup, production mutation/gate, protocol and fake-worker regressions
remain required. Production test methods were not added. O-09 packaging is still OPEN;
this is a qualified development launch, not distributable Python packaging.

At 175% the GUI selected isolated Source/Destination, displayed real preflight/config
path, created repository/config, and returned Home with the real task. Minimize and
Close retained desktop PID 40440 and worker PID 8312. The user then manually used
the tray **Open Mirrorly** and **Exit Mirrorly** commands; Home was restored and
the application exited normally. Both old PIDs were gone before a new launch.

The final Debug x64 build launched desktop PID 31760 and a new production worker
PID 11424. On its first Home load, the Python-backed catalog rediscovered `Source`
without another create. Home showed **Backup set up**, **Last backup: Not checked**,
disabled **Back up now**, no fixture task and no fabricated Activity. It explicitly
said setup creates no snapshot and Backup execution is unavailable. Python's
`load_task_config`, `load_repo` and `list_tasks` confirmed the same source, configured
target, matching repository ID and volume GUID, unchanged source sample and exactly
zero snapshots. No C# TOML parsing or writing was involved.

Local review screenshots (ignored test artifacts):
`.pytest_tmp/phase3e-review/home-created.png` and
`.pytest_tmp/phase3e-review/home-after-restart.png`.

| Validation in this phase | Result |
| --- | --- |
| Qualified Python checkout/import and editable origin | PASS, current GUI worktree |
| Focused catalog/create/lifecycle/setup Python tests | 75 passed |
| Final catalog tests after formatting/import cleanup | 14 passed |
| Full Python regression | 785 passed |
| Windows E2E / historical fake worker | 13 / 11 passed |
| Complete C# harness, including real cross-process session/create/catalog/restart and supervised active Exit | 52 passed |
| WinUI Debug x64 build | PASS; final build launched from the standard Debug x64 output |
| Ruff / format / pip check / diff check / GUI document file links | PASS |
| Real UI Create, Home, Minimize, Close-to-tray, tray Open and tray Exit | PASS; tray actions manually observed by the user |
| Real UI full Exit/relaunch/rediscovery | PASS; new desktop/worker PIDs and durable task facts verified |

The final build additionally clears folder selections after confirmed creation;
the C# harness covers that state reset. The GUI restart and durable rediscovery
were observed on the final build. This acceptance does not claim a Backup snapshot,
Backup execution, final worker packaging or Activity persistence.
