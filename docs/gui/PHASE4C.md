# Phase 4C — automatic GUI FIFO Backup queue

Phase 4C extends the existing Phase 4B app-scoped Backup coordinator. One real
Backup runs at a time; eligible requests for different configured Backups enter
an ordered, memory-only GUI FIFO. The production worker remains a single-slot
executor with no hidden queue. The Python Backup transaction and production
protocol limits are unchanged. During acceptance, the worker terminal projection
was bounded so a large persisted report cannot exceed the protocol collection
limit; the complete report remains available at `report_path`.

## Scheduling contract

- A task selector starts immediately when idle, or joins the tail when another
  Backup runs. Running/Queued selectors cannot be added twice. A queued selector
  can be removed before dispatch without sending a worker request.
- Each dispatch sends a new `backup.run` with only the durable selector. Python
  resolves current configuration, repository identity and Backup policy at that
  time. Catalog refresh, navigation and card reconstruction do not own/reorder
  the queue.
- Genuine application terminal results advance to the next queued task even
  after an application failure, including `commit_state=unknown`. The finished
  task keeps its factual result; scheduler state is separate from Backup health.
- No terminal result/transport uncertainty, unusable terminal detail, worker
  busy or mutation-gate rejection pauses automatic dispatch and preserves
  not-yet-started queue entries. No automatic retry or replacement worker is
  launched. A lost transport is never classified as an application failure.
- Queued remains a factual task state while automatic dispatch is paused. Home
  and Backups show queue attention ahead of ordinary "will start next" wording,
  with technical details from the operation that caused the pause even when a
  different queued task is selected. A genuine terminal application
  `commit_state=unknown` still permits FIFO advancement; it is not transport
  uncertainty.
- A required Resume interaction holds the slot and does not reorder the queue.
  The existing ten-minute deadline, hidden-window reopening and explicit
  Resume/Don't resume/unavailable behavior remain unchanged.
- Close X hides to tray and Minimize keeps FIFO moving. Confirmed true Exit
  clears queued items, disallows new work and supervises only Running until its
  terminal and idle worker shutdown. Stay leaves Running and queue intact.
  Desktop restart restores durable tasks/saved snapshots, not queued requests.

Home cards show Running as `Backing up…` without fake progress and Queued with
`Remove from queue`. No position/ETA is promised. There is no cancellation,
pause, reorder, priority, Back up all, persistent Activity or completion
notification. The one-shot petal-start animation remains deferred to visual
polish; it is not operation progress and must never affect scheduling.

## Acceptance: PASS

[BackupQueueTests](../../desktop/Mirrorly.Desktop.Tests/BackupQueueTests.cs)
exercise controlled in-memory scheduling and real cross-process temporary
repositories through the production worker. [BackupGuiFlowTests](../../desktop/Mirrorly.Desktop.Tests/BackupGuiFlowTests.cs)
retains the Phase 4B result/Resume/Exit regression.

The final manual smoke used a fresh registered-package Debug GUI session and
an isolated disposable GUI task root. A showed `Backing up…` while B and C
showed `Queued` with `Remove from queue`; neither had started. Removing B left
A running and C queued. Re-adding B produced the FIFO order A → C → B.
Close X hid the same desktop/worker session to the notification area, where
the queue continued. A separate tray round-trip reopened that same session while
A still showed `Backing up…` and B/C still showed `Queued`, each with its Remove
action; the order survived. Python application
and core readers confirmed one complete snapshot and one required report per
task, in A → C → B creation order, with no duplicate execution. Home showed
factual terminal states, without percentages, current-file progress or invented
Activity. The operations used only disposable source trees.

The separate active-Exit smoke selected `Stay in Mirrorly` while A was running
and B queued; both states remained. A second real tray Exit and `Exit after it
finishes` cleared B, disabled new Backup starts and let A continue. Once A
finished, the desktop and production worker exited normally. Python readers
confirmed A's new complete snapshot and report while B's snapshot count did
not increase. No worker kill or cancellation was used. Queue state is not
restored after a new desktop session. The deterministic tests cover terminal
application failure advancing FIFO and transport uncertainty pausing it.

The first manual attempt exposed a separate worker-result projection defect:
a real 10,000-file Backup published its complete snapshot and required report,
but projecting the entire report into one terminal frame exceeded the frozen
4,096-element JSON collection limit. The worker reported an `unreported`
terminal and the GUI correctly paused FIFO rather than assuming failure. The
worker now returns bounded report summary counts while retaining `report_path`
for the complete persisted report. A regression test projects 10,000 entries
through the real protocol encoder and verifies the published fact and both
normal/with-issues outcomes. The full FIFO smoke was restarted with fresh
disposable tasks after this fix.

Final validation: 809 full Python tests, 13 separately run Windows E2E tests,
11 historical fake-worker tests, 23 focused Backup-worker tests, and 80 desktop
C# harness tests passed. The WinUI Debug x64 build, Ruff check/format, pip
check, package-context launch qualification, documentation-link check and
`git diff --check` passed. The additional same-session tray round-trip ran a
second disposable A → B → C queue and verified Running/Queued state immediately
after reopening. Python readers again found exactly one complete snapshot and
required report per dispatched task. No new Mirrorly Event 1026/1000 was
observed during the package-context sessions.

## Debug startup incident and development launcher

The initial Debug smoke command launched the EXE directly, outside its registered
package context. On this machine that launch mode fails before Mirrorly's `App`
or any Home/coordinator/queue code executes. Windows Application logs for the
earlier 2026-09-19 and 2026-09-24 failures have the same Event 1026 managed
exception and stack: `COMException 0x80040154 (REGDB_E_CLASSNOTREG)` while
`WindowsAppRuntime.DeploymentManagerCS.AutoInitialize` constructs
`DeploymentInitializeOptions`. Both Event 1000 records identify
`KERNELBASE.dll`, `0xe0434352`, offset `0xc41ca`, with no package identity.
The user's interrupted Phase 4C process PID 34624 had no distinct Event 1026
Application log record; it is not forensic proof of that PID's managed stack.
The earlier matching events and controlled reproduction establish the launch-path
failure, not an individual stack for PID 34624.

A controlled 2026-09-25 direct-EXE reproduction generated the identical Event
1026/1000 stack/signature before managed app startup. The Debug smoke now uses
the guarded [package-context launcher](../../desktop/Launch-DebugGui.ps1), and
its [qualification check](../../desktop/Launch-DebugGui.Tests.ps1) rejects a
relative test root and confirms package registration without starting the app.
This corrects the development smoke launch path; it does not alter Mirrorly
runtime logic or choose final packaging O-09. The packaged-context fresh launch
started a new desktop PID 2096 and production Python worker PID 7696 from the
qualified worktree interpreter. The final FIFO smoke used fresh package-context
desktop sessions and the isolated GUI data root, not the bare EXE. No new
Mirrorly Event 1026/1000 appeared during those package-context sessions.
`Invoke-CommandInDesktopPackage` is development/debug infrastructure only and
does not resolve final deployment decision O-09.
