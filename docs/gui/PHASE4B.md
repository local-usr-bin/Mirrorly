# Phase 4B — real GUI single-Backup loop

Phase 4B connects the reviewed Home action to the existing shared Python Backup
transaction through production IPC. It executes **one GUI-started Backup at a
time**. The worker still owns mutation admission, lifecycle gate, task/repository
locks, Resume decision position, snapshot publication, finalization and reports.
There is no C# Backup algorithm, automatic replay or GUI FIFO queue.

## Runtime behavior

- The app-scoped `BackupExecutionCoordinator` owns the selected task selector,
  running/awaiting-decision/terminal/transport-uncertain state and request/operation
  correlation. Home sends only the durable Python task selector via `backup.run`.
  Double clicks and another task's action are not admitted while one is running.
- Running shows an indeterminate indicator without percent, current file, item
  count, phase or ETA. `backup.resume` shows one main-window ContentDialog with
  `Resume` and `Don't resume`. The window is reopened from the tray/minimized
  state when a required decision arrives. Dialog failure responds unavailable,
  never decline. The worker's ten-minute deadline remains authoritative.
- A normal terminal distinguishes success, completed with issues, no new
  complete publication, uncertain publication, and known publication with
  later failure. No terminal response is separately transport-uncertain. The
  GUI does not infer rollback or source freshness. Technical details retain
  application stage, wire facts and error data. Unknown/unreported results
  block another GUI-started Backup pending a future reconciliation design.
- `backup.summary` is a read-only one-slot query. Python resolves the task and
  repository afresh and selects `latest_complete()` by lifecycle semantics,
  independently of display-list order. Home shows `Latest saved backup` when
  an authoritative complete snapshot is found after execution or a fresh
  application start. File Explorer receives the Python-provided existing
  snapshot path; C# neither reconstructs it nor parses task TOML.
- Close X hides to the notification area and Minimize keeps the operation
  running. True Exit asks whether to stay or exit after the operation finishes;
  confirmed Exit keeps supervision and allows any required Resume decision,
  then requests idle worker shutdown. There is no process-kill cancellation.

## Boundaries

The Home catalog is still the real Python-owned GUI config root. Setup alone
creates zero snapshots. Fixtures are confined to tests/design preview. No fake
Activity is shown in normal runtime. Phase 4C will add the approved automatic
GUI FIFO, queued state, removal and no duplicate Running/Queued task. Phase 4B
does not add persistent Activity, notifications, progress or cancellation.
O-02 and O-09 remain open. The optional one-shot petal-start animation is
deferred; it is presentation only and never part of Backup correctness.

## Validation

The Phase 4B tests are in [Python query tests](../../tests/test_query_operations.py),
[worker IPC tests](../../tests/test_worker_backup.py), and the
[desktop GUI flow harness](../../desktop/Mirrorly.Desktop.Tests/BackupGuiFlowTests.cs).
They cover lifecycle-authoritative summary, one GUI operation, no double dispatch,
Resume answer mapping, late terminal, all application commit states, transport
uncertainty, real disposable repository/snapshot/report, fresh-session rediscovery
and supervised true Exit with a blocked real Backup. No valuable user data is used.

Validation on the disposable development root `.pytest_tmp/s`:

| Check | Result |
| --- | --- |
| Qualified Python regression | 807 passed; no skips |
| Windows E2E / historical fake worker | 13 / 11 passed |
| Complete C# harness after GUI fixes | 71 passed; 0 failed |
| WinUI Debug x64 / Ruff / format / pip / diff / GUI links | PASS |
| Real GUI Setup → Home → Back up now | PASS on disposable source/target |
| Visible Running UI | PASS; indeterminate only, no fake percentage |
| Close X while running | PASS; desktop/worker remained alive |
| Tray Open and factual completion | PASS; same session, real saved version |
| Python/core inspection | PASS; one complete snapshot, 1600 ordinary files, required report, unchanged source |
| Fresh desktop/worker process rediscovery | PASS; saved version shown from Python, no fake Activity |
| Open in File Explorer | **PASS**; the user manually opened the exact Python-provided saved snapshot directory in Windows Explorer and saw `bulk` and `sample.txt` |

The first GUI smoke found a real action-state defect: after Setup returned Home,
the button remained disabled when the supervised catalog refresh became idle.
The coordinator now listens to session-state changes; a regression test and a
fresh real session confirmed the enabled action. The ordinary File Explorer
failure notice now keeps the technical cause behind View technical details.
The final user-led Explorer check opened the authoritative saved snapshot path:

`C:\Users\sakur\.codex\worktrees\1047\Mirrorly\.pytest_tmp\s\target\MirrorlyRepo\snapshots\2026-09-24_210715-s00000000000000000000-e424840912994aa68902acd6bd0f4d3d`

Explorer visibly contained `bulk` and `sample.txt`. No security-software bypass
or automation was used for that successful check. The earlier intercepted launch
was an environmental obstacle, now cleared by this manual verification.

**Phase 4B acceptance: PASS.** The real GUI smoke verified `Back up now`, visible
`Backing up…`, Close-to-tray while Backup continued, tray reopen, `Backup
completed`, exactly one real complete snapshot, the required report, unchanged
source, fresh-process rediscovery of the saved Backup, no fabricated Activity,
and File Explorer opening the exact authoritative snapshot folder. This is the
single-Backup GUI loop; automatic FIFO remains Phase 4C scope.
