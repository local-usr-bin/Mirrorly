# Production Worker Contract v1

Current implementation / frozen contract, 2026-09-24. This is the current production
IPC source of truth. [Phase 0 IPC_CONTRACT](IPC_CONTRACT.md) remains a historical
proposal, not an implemented API. [Phase 2](PHASE2.md) completed shared application
orchestration. Phase 3E connected real GUI Setup and a readonly durable task catalog.
Phase 4A added production `backup.run` and the required Resume interaction at the
worker/client boundary. Phase 4B connects one real GUI-started Backup at a time,
binds the Resume decision to the main window, and adds read-only `backup.summary`
for authoritative saved-version rediscovery. This is not production distribution readiness.
Phase 4C adds an app-scoped, memory-only GUI FIFO in the existing Backup coordinator;
the worker still has one execution slot and no queue.
The subsequent read-only data batch adds `snapshots.list`, now used by Backup
Detail's Snapshots section. Restore R1 adds read-only `restore.prepare`; R2
connects a real selection and Review GUI to it. Restore execution remains
unimplemented.
Phase 3B's readonly bridge is complete. Phase 3C removes the finite request budget,
adds worker-lifetime admission and freezes [GUI configuration ownership](CONFIGURATION.md).
Phase 3D enabled setup.create under that gate. [Phase 3E](PHASE3E.md) now binds Setup, owns the production session at app scope, and uses Python task data on Home.
Phase 4A reuses the unchanged shared Backup transaction and the same mutation gate.

## Implemented scope and ownership

- [worker/launch.py](../../src/mirrorly/worker/launch.py): explicit development
  entry point and checkout qualification, no PATH fallback or CLI import.
- [worker/host.py](../../src/mirrorly/worker/host.py): negotiation, admission,
  one synchronous application slot, bounded session records and channel lifecycle.
- [worker/protocol.py](../../src/mirrorly/worker/protocol.py): framing/validation.
- [worker/preflight.py](../../src/mirrorly/worker/preflight.py): intent validation
  and explicit projection of existing application facts; no setup policy copy.
- [worker/creation.py](../../src/mirrorly/worker/creation.py): explicit setup-create
  intent/approval and application success/decision/failure wire mapping.
- [worker/backup.py](../../src/mirrorly/worker/backup.py): strict Backup intent and
  projection of the shared application transaction's known facts.
- [worker/summary.py](../../src/mirrorly/worker/summary.py): bounded read-only
  projection of lifecycle-authoritative latest complete snapshot facts.
- [worker/transport.py](../../src/mirrorly/worker/transport.py): bounded output
  boundaries; only pumps perform protocol/diagnostic writes.
- [worker/lifecycle.py](../../src/mirrorly/worker/lifecycle.py): Windows user/logon
  session mutex, retained by the host thread through executor completion.
- [ProductionWorkerClient.cs](../../desktop/Mirrorly.Desktop/Services/ProductionWorkerClient.cs)
  and [ProductionProtocol.cs](../../desktop/Mirrorly.Desktop/Services/ProductionProtocol.cs):
  separate C# client behind DesktopSession; ViewModels do not own processes.

The Phase 1A worker, FakeWorkerClient and PrototypeConfiguration remain unchanged test infrastructure. Normal GUI runtime no longer starts them. The old fake interpreter still intentionally does not import core.
There is no worker Backup queue, verify/restore execution, progress, cancellation,
Activity persistence, completion notification UX or stdio reattach. No application/core/CLI Backup algorithm,
configuration/repository format, version or dependency changed.

## Development launch, not deployment design

PowerShell from this checkout (paths are explicit caller input, not committed
portable business configuration):

```powershell
$python = 'C:\Users\sakur\anaconda3\envs\mirrorly-gui-dev\python.exe'
$checkout = (Get-Location).Path
$hostScript = Join-Path $checkout 'src\mirrorly\worker\launch.py'
& $python -I -u $hostScript --expected-interpreter $python --expected-checkout $checkout
```

The client takes `WorkerDevelopmentLaunch(interpreter, checkout)`, derives the
absolute host script, uses `ArgumentList`, and launches without a shell or console.
Home lazily starts the app-owned production session after the shell appears. Each client owns one on-demand long-lived session;
no idle recycling, automatic restart or reattach is implemented.

Before hello, Python checks sys.executable, mirrorly.__file__, setup.__file__,
editable direct_url origin and absence of CLI import. C# checks the returned
qualification facts independently against its launch inputs. Mismatch fails;
cwd/PYTHONPATH is not a replacement for the correct editable installation.
O-09 packaging is OPEN. A packaged development WinUI build does not bundle Python.

## Wire identity and limits

`protocol = "mirrorly.worker"`; the only supported version is
`{"major":1,"minor":0}`. Fake-worker integer version 1 is rejected.

Every envelope has exactly these keys:

```json
{
  "protocol": "mirrorly.worker",
  "protocol_version": {"major": 1, "minor": 0},
  "message_type": "request",
  "session_id": "worker-generated-session",
  "request_id": "2",
  "operation_id": null,
  "interaction_id": null,
  "payload": {"method": "ping", "params": {}}
}
```

UTF-8, no BOM, one JSON object followed by LF. Limits include the LF:

| Limit | Value |
| --- | --- |
| Handshake frame | 65,536 bytes |
| Initialized frame | 1,048,576 bytes |
| JSON depth, root counted as 1 | 32 |
| Individual collection items | 4,096 |
| Total JSON nodes, including property names | 16,384 |
| String length, Unicode scalar values | 32,768 |
| IDs | 1–128 characters, or null where permitted |
| Request replay memory | One uint64 high-water mark + active operation |
| Terminal result cache | Last 32 application results |
| Protocol input queue | 32 frames |
| Protocol output queue | 32 normal positions + 1 reserved terminal position |
| Separate droppable notice queue | 16 frames |
| Diagnostics queue | 32 chunks, each at most 2,048 characters |
| C# pending requests | 64 |

Limits are centralized constants. There is no 4,096-request lifetime budget or
max_requests advertisement, and no automatic session renewal. Request IDs span
1 through 18446744073709551615. C# fails before incrementing uint64.MaxValue;
Python rejects values outside this canonical range without wrapping. Outcomes
and deduplication are session-local, not a disk journal or restart replay service.

Split/coalesced frames and multibyte boundaries are supported. Empty, truncated,
oversized, invalid UTF-8/JSON, duplicate-key, non-finite-number, excessive-depth or
collection frames are protocol faults. Unknown envelope fields are rejected in v1;
future extensions require an explicitly supported minor version. Parameters are
validated independently; invalid method input is a request rejection, not execution.

## Handshake and currently callable methods

1. Worker emits `hello`, version null, all correlation IDs null except session_id.
   Payload: supported_versions, worker=`production`, qualification, limits.
2. Client emits `initialize`, selected version, new request_id, payload
   `{"required_capabilities":[]}`.
3. Worker responds `response`, phase=`terminal`, result containing version,
   methods, capabilities and limits. Unsupported versions/capabilities are rejected.
4. Only then can `request` messages be admitted.

Implemented methods: `ping`, `status`, `worker.shutdown`, `setup.preflight`,
`setup.create`, `tasks.list`, `backup.run`, `backup.summary`, `snapshots.list`,
`restore.prepare`.
No repository.inspect was added. The bounded readonly tasks.list contract is specified in [PHASE3E](PHASE3E.md#durable-task-catalog). Unknown methods, including
verify, `restore.execute`, cancellation and test_crash, are rejected before application.

`resume_interaction=true` after the tested `backup.resume` round-trip. The other
capabilities remain false: phase_progress, item_progress, byte_progress,
current_item, cooperative_cancel. `event` carries only bounded, lossy Backup
relocation/resume notices. It is not progress. Interaction messages are required
business decisions, never droppable notices.

## Correlation, admission, late results

Client request_id is a canonical positive decimal uint64 string, strictly increasing
within a session (gaps allowed). Worker session/operation IDs are opaque generated
IDs. Control requests have no operation_id. The client never supplies one.

Application responses use these payloads:

```json
{"phase":"accepted","result":null,"error":null}
```

```json
{"phase":"rejected","result":null,"error":{"kind":"admission","code":"busy","application_invoked":false}}
```

```json
{"phase":"terminal","result":{"outcome":"succeeded","preflight":{}},"error":null}
```

Accepted and terminal share request_id and the worker-allocated operation_id.
Accepted is admission only, not validation success. The worker has one executor,
not an executor job queue. A second application call while that slot is occupied
gets busy. Control input continues. The slot becomes available after the application
returns and its terminal is retained/queued; it is not a filesystem mutation lock.

The high-water mark advances on each new request ID before admission checks,
including controls and rejected requests. IDs at/below it never execute, even after
terminal detail is evicted. Active request facts and the last 32 application terminal
results are retained; control requests do not evict application results. There is
no per-request history growing with session lifetime.
The `application_invoked:false` in a duplicate rejection describes that duplicate
attempt, not an assertion that its original request never ran.

C# returns a WorkerRequest handle. WaitAsync(timeout) times out only that wait;
the underlying Terminal task and correlation stay alive. No automatic retries.
Send failure can mean some/all bytes arrived and is transport uncertainty. Local
encoding rejection before any write is a local validation exception instead.

## Status is host observation

`status` takes `{}` or `{"request_id":"earlier-id"}`. It returns initialized,
channel_healthy, state (idle/busy/terminal_pending), active request/operation, and
optional request lookup. **Phase 3C lookup:** an active request is accepted; a cached application result is
terminal; an ID above highest_seen_request_id is never_seen/reusable. Any remaining
ID at/below that mark is stale_result_not_cached, reusable=false,
application_invoked=null, terminal_available=false. This may be an evicted result,
an old control/rejection or an unused gap; do not invent past execution facts.
The former unbounded rejected/control/evicted-terminal history is no longer available.
Cached results do not survive process exit. A high-water mark is not exactly-once
across sessions. No status claims scanning/publishing, file or byte progress.
Host responsive does not mean filesystem IO is advancing.

## Worker lifecycle admission (Phase 3C, CURRENT FACT)

The standalone worker host attempts a nonblocking acquisition at startup. Its
control thread owns a Windows named mutex via CreateMutexExW (no initial owner,
SYNCHRONIZE | MUTEX_MODIFY_STATE), WaitForSingleObject(handle, 0), ReleaseMutex and
CloseHandle. Only the Python worker owns this handle; it is not inheritable by a
GUI process or tied to a pipe/request/operation ID.

Stable name:

```text
Local\Mirrorly.ProductionWorker.v1.<TokenUser SID>.<AuthenticationId as 16 hex digits>
```

Local is the Windows session namespace. TokenStatistics.AuthenticationId identifies
the current logon session; TokenUser SID identifies the Windows user. Different
random GUI/stdio sessions in this scope share the gate. There is no PID in the name,
no guessed stale file deletion, and no third-party dependency. Default token DACL
applies; identity/open/wait errors fail closed for future mutation. This is lifecycle
coordination, not a security boundary against hostile processes in the same account.

Status includes lifecycle_gate: state (held/available/unavailable/error), identity,
scope=windows_user_logon_session, owned, abandoned_observed and error. A contender
can use readonly preflight but cannot create while another worker owns the gate;
it does not kill or reattach to the owner. Its status probes an
available mutex with acquire/release; that observation is immediately stale-able
and is **not** admission. It does not silently acquire retained ownership on status.

Create admission **calls require_ownership() on the host/control
thread before invoking the service**. Failed acquisition rejects with no
application invocation. Repeated checks by an owner do not recursively acquire.
The rejection code is mutation_gate_unavailable, application_invoked=false,
operation_id=null, with a lifecycle_gate observation. That observation is sampled
after the failed attempt and can already differ from the admission-time state.

The owning host thread remains alive and retains ownership through channel loss,
executor join, final result bookkeeping and transport cleanup. Only after no
application call can continue does it release/close. Normal idle shutdown releases;
process termination releases through Windows abandonment. WAIT_ABANDONED grants
ownership and is recorded, but proves nothing about a previous operation's side
effects or success. No rollback/replay follows abandonment.

Unexpected GUI/client process loss does not release a surviving worker's ownership.
Real tests terminate a disposable parent with a child blocked inside a test-only
service, observe contention from a second production host, and release the service
barrier before observing worker exit and availability.

This gate is independent from the worker operation slot and existing task/repository
locks. It neither excludes external CLI execution nor coordinates other user/logon
scopes. Ownership permits admission of setup.create and real backup.run; it does not
add progress or cancellation capability. WAIT_ABANDONED is successful ownership, not
a clean prior release, permanent refusal or evidence of repository corruption.
Normal application safety checks still run after abandoned acquisition.

Windows semantics: [CreateMutexExW](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexexw),
[TOKEN_STATISTICS](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-token_statistics),
[ReleaseMutex](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-releasemutex).

## setup.preflight mapping

Required params: task_name, source, target, config_root, filesystem_policy.
All are strings; paths must be explicit absolute paths, filesystem_policy strict
or warn. No client TaskConfig, RepoInfo, repository_path or preflight facts accepted.

The executor calls real `application.setup.preflight_setup(SetupRequest(...))`.
Success means the inspection returned, not that creation is guaranteed possible.
Projection contains repository_path, config_path, inputs_valid, volume, mount_root,
repository_volume, copy_mode_approval_required, problem. Missing facts remain null.
Volume projection is label/serial/filesystem/guid.

`problem`, if present, retains application stage, repository_initialized,
config_written and original technical cause. It is the first blocker in the
existing service order, not an invented comprehensive validation report. Prospective
repository path is supplied by application (`target / REPO_DIR_NAME`); no C# join rule.

An unexpected service exception produces terminal outcome=failed, error kind
application/code preflight_failure, original exception type/message/errno/winerror,
stage null if unknown. Projection failure *after* application returns instead has
kind worker/code result_projection_failed and `application_returned:true`; it does
not reinterpret the application outcome. Technical messages are capped at 4,096
characters with a truncation flag. Do not infer diagnosis from exception wording.

Preflight never authorizes a future write and creates no
repository/config/lock or test file. Create independently reruns application validation.

## setup.create — first mutation (Phase 3D, CURRENT FACT)

Request params contain exactly the five preflight intent strings plus an explicit
JSON boolean copy_mode_approved. Missing approval, numeric/string coercion, or
client TaskConfig/RepoInfo/repository_path/config contents/preflight tokens are
rejected before application. C# exposes CreateAsync(SetupCreateInput) and
CreateAsync(GuiDataPaths, SetupCreateIntent); approval has no implicit true/default.
No ViewModel or GUI business button calls these APIs.

Admission order: strict protocol/envelope/version/session/request-ID validation;
initialization and method validation; create intent/approval validation; available
application slot; nonblocking lifecycle require_ownership on the control thread;
operation-ID allocation and accepted response; dispatch to the synchronous executor.
Gate denial allocates no operation ID, does not call create_backup, does not write,
wait, kill, retry or enqueue. Status/previous preflight is never authorization.

Once admitted, even failure to deliver accepted must not suppress execution.
The executor constructs its work from the freshly validated SetupRequest and
calls the unchanged application.setup.create_backup with explicit approval.
No saved preflight checks are consumed. Source/config/containment/volume/repository
conditions are rechecked by the application in their existing order.

Success terminal payload:

```json
{
  "phase": "terminal",
  "result": {
    "outcome": "succeeded",
    "setup": {
      "task_name": "documents",
      "source": "C:\\Example\\Source",
      "repository_path": "E:\\Example\\MirrorlyRepo",
      "config_path": "C:\\Example\\GuiTasks\\config.d\\documents.toml",
      "repository_initialized": true,
      "config_written": true,
      "repo": {
        "repo_id": "application-returned-id",
        "format_version": 2,
        "hash_algorithm": "blake3",
        "filesystem_policy": "strict",
        "hardlinks": true,
        "volume": {"label": "Backup", "serial": "application-returned", "filesystem": "NTFS", "guid": "application-returned"}
      }
    }
  },
  "error": null
}
```

Values come from SetupResult, not this illustrative example or Python object/TOML
serialization. task_name is TaskConfig.name; config_path is the configuration
reference. Their existing identity semantics are not generalized or redesigned.

SetupFailure maps to outcome=failed, setup effects/paths/known repo, and error
kind=application, code=setup_failure, stage from the exception, technical from its
original cause. No exception-string diagnosis or post-failure disk probing occurs.

| Application fact | repository_initialized | config_written | repo |
| --- | --- | --- | --- |
| Failure before init | false | false | null |
| init_repo raised before successful return | null | false | null |
| Repo acknowledged, config construction failed | true | false | known RepoInfo facts |
| Config write raised, even if complete TOML exists | true | null | known RepoInfo facts |
| Success | true | true | known RepoInfo facts |

False means that step did not complete in this call, not that no prior artifact
exists. Null means unknown. Initialization can leave artifacts before raising;
config failure may leave partial or complete TOML. There is no rollback/deletion.
Unstructured service exceptions map stage/effects to unknown with the original
technical cause (setup_unexpected_failure); they are not synthetic SetupFailure.

CopyModeApprovalRequired maps outcome=decision_required, error code
copy_mode_approval_required, stage=volume and the queried volume. Both effect flags
are false because this existing application boundary precedes writes. No automatic
resubmission or silent approval follows. Even after an earlier NTFS preflight, a
new approval requirement needs a fresh explicit decision and a new request; execution
revalidates again. Approval cannot override strict filesystem policy.

Projection/encoding failure is a worker result_projection_failed, distinct from
application failure: preserve application_outcome and acknowledged boolean/null
effects even when detailed result projection is unavailable. Never rerun to obtain
details. Terminal delivery failure cannot alter application facts.

Both setup methods use one slot. During create, ping/status work; another application
call or shutdown gets busy. EOF/stdout loss stops new admission but lets create
return/fail normally. The owning thread joins the executor, records terminal facts,
finishes bounded transport cleanup, then releases the gate. No cancellation occurs.

Client timeout only times out the wait. Late terminal remains correlated. EOF/crash/
send uncertainty faults the pending request with WorkerTransportUncertainException;
WorkerRequest.ResponseAvailable remains false without a received response. This
never manufactures SetupFailure, no-write facts, or permission to retry. A received
admission rejection also sets ResponseAvailable=true, which means response availability,
not application success. There is no automatic replay or recovery workflow.

## backup.run — Phase 4A backend contract

Request params contain exactly `config_root` (explicit absolute path), `task`
(selector string or null), `dry_run` (boolean), `full_hash` (boolean), and `exclude`
(bounded array of strings). The worker builds `application.backup.BackupRequest`
and calls the existing synchronous `run_backup`. It never accepts a client-selected
baseline, snapshot/sequence ID, manifest, repository identity or change set.
The C# production client exposes `BackupAsync`; Phase 4B's app-scoped GUI
coordinator now calls it for one user-started Backup. Current GUI Setup and
catalog continue to use the same worker session.

Real Backup admission validates protocol/session/intent and slot, requires the
stable lifecycle gate on the control thread, then allocates operation_id, emits
`accepted` and dispatches the existing transaction. Gate denial is a rejection
with `application_invoked=false`; there is no wait or hidden queue. TaskLock then
RepoWriterLock inside the application remain separately authoritative. A dry-run
uses the one slot but requires no mutation-gate ownership; its existing zero-write
application path remains unchanged and never authorizes later real execution.

The only required interaction is `backup.resume`. The application callback still
occurs at its existing point **inside both locks**. Worker sends
`interaction_request` with the same request_id and operation_id, a new
interaction_id, and payload `kind=backup.resume`, snapshot_id, created_at and
deadline_seconds=600. C# dispatches its typed responder off the stdout reader and
sends `interaction_response` with `kind=backup.resume` and answer `resume`,
`decline_resume` or `unavailable`. Normal GUI has no responder/dialog yet. The
deadline starts when the worker creates the pending interaction, not when a dialog
renders. Invalid/mismatched response, explicit unavailable, timeout or channel
loss makes the callback fail as interaction unavailable; no answer is inferred.
Definitive channel loss wakes it immediately. Duplicate answer after consumption
cannot change the decision. The existing application failure path releases locks.
The approved 10-minute default is test-injected shorter only in test hosts.

Terminal result `outcome` is `dry_run`, `succeeded`, `completed_with_issues` or
`failed` where the application provides structured Backup facts. Facts include
the exact application `commit_state`, known task/repository identity, lifecycle
sequence, snapshot ID, resumed-from ID, change counts, skipped count,
materialization counts/bytes, retention deletion count and report path where
available. A success may still have issues. Failure includes application stage
and original technical cause; no exception-string diagnosis or invented partial
retention details. `report` is present only on successful real Backup, as a
bounded factual summary (status, duration and issue counts); the full persisted
report is available at `facts.report_path`. A real 10,000-file Backup showed why
the full report arrays must not be copied into a bounded terminal frame: they
exceeded the protocol's collection limit after the snapshot and report had
already been published. Projection failure has a separate worker error with
small acknowledged commit facts; it never triggers re-execution. The bounded
projection now lets that valid result reach the GUI without relaxing protocol
limits or changing the application Backup transaction.

`not_published` means the complete publisher was not entered, **not** zero side
effects. `unknown` means complete publication was attempted but did not return
successfully. `published` means complete publication returned successfully,
**not** finalization success. Resume cleanup, retention and required report
publication can fail afterward with `commit_state=published`; the snapshot is not
rolled back. An application terminal carrying `unknown` differs from client
transport uncertainty: when no terminal arrives, `WorkerRequest.ResponseAvailable`
is false and the client has **no application commit-state fact**. Timeout only
ends the caller's wait; late terminal remains correlated. Neither timeout nor
disconnect automatically replays Backup, including after restart/cache eviction.

Application relocation/resume notices enter the separate bounded lossy outbound
notice queue. Callback-side enqueue/encoding failure cannot throw into the Backup
transaction. A single writer owns stdout; a slow/failed consumer can mark the
channel lost without rewriting application facts. Active noninteractive Backup
continues under the lifecycle gate after channel loss, then the worker exits. If
Resume is required after channel loss, it becomes unavailable at that boundary.
Ping/status remain control-plane liveness only; other application calls and idle
shutdown are rejected busy while Backup owns the slot. There is no progress,
cooperative cancellation, process-kill control, GUI FIFO or true Exit-running
Backup UI in Phase 4A. O-02 and O-09 remain open.

## Threads, backpressure and channel loss

- One input reader parses bounded frames into a bounded control inbox.
- Host/control loop performs only protocol/admission/cache work.
- One application executor calls the unchanged synchronous service.
- Exactly one stdout writer drains bounded protocol output.
- A separate bounded lossy diagnostic pump owns stderr. Accidental Python
  print/warnings are routed there rather than corrupting protocol stdout.

Application code/callbacks never perform pipe writes or wait for notice delivery.
Backup notices use the separate try-enqueue boundary; failure is isolated.
Critical responses never enter that droppable queue. Queue overflow, write error,
or a protocol write stalled for 30 seconds marks the channel lost and stops admission.
No such fault is converted into application failure. An active application call returns
normally before the host exits. There is no cancellation or process kill fallback.

C# drains stdout and stderr concurrently for the process lifetime. After protocol
corruption, it closes input and drains remaining stdout as untrusted bytes. Diagnostic
loss is not business failure; bounded diagnostic data is obtained via DrainDiagnostics.

EOF while idle exits. EOF while active waits for that synchronous call to finish.
Busy worker.shutdown is rejected; idle shutdown clears session resources, attempts
delivery for at most one second and exits. Host shutdown joins application execution,
but never waits indefinitely for a blocked pipe pump. The standalone launcher then
uses os._exit after Host.run returns to avoid interpreter shutdown joining daemon IO
or flushing blocked stdio. It is **not** termination of an active application call.
OS/process exit code is not an application result.

Client disposal closes the channel, does not kill, and waits a bounded period; if
the service is still finishing, existing drain tasks continue. No supervisor restart.

## Approved lifecycle policies and remaining capabilities

- O-01: confirmed true Exit clears not-started GUI queue items, supervises the
  current operation to completion, then exits worker/GUI. No withdrawal of a
  confirmed Exit intent in v1. Close X/Minimize are not true Exit. Phase 3E implements this supervision for setup/catalog workflows; no Backup queue exists.
- O-03: unexpected parent loss exits idle worker; an active noninteractive call
  may finish before exit. Required interaction unavailable ends the operation at
  that existing decision boundary. Parent loss is not cancellation. No reattach.
- O-08 Resume: Phase 4A implements the ten-minute default deadline.
  Timeout/disconnect/unavailable cannot
  become decline_resume (False would continue Backup), nor silent acceptance.
  The decision stays inside existing locks, after migration and before scanning.
- O-04: all application operations share one execution slot. This is separate
  from the future GUI FIFO and does not solve external CLI concurrency.
- Phase 3D wires the lifecycle guard into setup.create admission before service
  invocation and retains ownership through all execution and terminal bookkeeping.
- O-07 ownership is APPROVED and centralized in Phase 3C; see
  [CONFIGURATION](CONFIGURATION.md). C# still must not parse/write task TOML.
  Setup.create is implemented and Phase 3E binds it to the reviewed Setup flow.
- O-02 cancellation and O-09 worker packaging remain open; no Job Object
  kill-on-close, mutation retry or production notification is added.

Implemented Backup result rules preserve not_published/unknown/published;
transport loss cannot manufacture a commit fact. Setup partial effects remain
three-valued. Future verify IPC must preserve mandatory report publication;
future restore IPC must use worker-owned plans and existing apply revalidation,
never a trusted plan reconstructed by C#.

## Validation and review scope

Python tests: [test_worker_protocol](../../tests/test_worker_protocol.py),
[test_worker](../../tests/test_worker.py). C# tests:
[ProductionWorkerTests](../../desktop/Mirrorly.Desktop.Tests/ProductionWorkerTests.cs).
[worker_fixture_host](../../tests/worker_fixture_host.py) is test-only, installs a
CLI-import blocker, and injects barriers/crashes/large stderr outside production
methods. Temp folders are test-owned, not user backup contents.

The executable desktop test harness now requires the explicit development interpreter:

```powershell
dotnet run --project desktop/Mirrorly.Desktop.Tests -- 'C:\Users\sakur\anaconda3\envs\mirrorly-gui-dev\python.exe'
```

Phase 3B validation: 42 new Python tests, 732 full Python regression (no skips),
33 desktop harness tests (26 existing + 7 production), WinUI Debug x64 build,
13 separately run Windows E2E and 11 unchanged fake-worker tests pass.
Ruff/check/format, pip check, diff check and updated-document links pass.
Tests cover real preflight/qualification/no CLI import, parser limits, duplicate
execution prevention, cache eviction, busy/no hidden queue, late response, large
stderr, stdout loss/backpressure, EOF finishing and bounded shutdown. No passing
test here demonstrates mutation, Resume, active Exit UX or final packaging.

Phase 3C validation: 55 focused Python worker/protocol/lifecycle tests, 745 full
Python regression (no skips), 36 C# harness tests, 13 Windows E2E, 11 unchanged
fake-worker tests and WinUI Debug x64 build pass. Ruff/check/format, pip check,
diff check and updated-document link checks pass. Python and C# real sessions
survive respectively 4,200 mixed control requests and 4,100 pings without restart;
cache eviction, stale gaps and uint64 boundaries remain protected.

[test_worker_lifecycle](../../tests/test_worker_lifecycle.py) covers normal exit,
forced termination, surviving child after actual client process termination,
thread affinity/nonrecursive checks and OS-error fail-closed behavior.
[worker_parent_fixture](../../tests/worker_parent_fixture.py) is a disposable
test-only parent; it does not add any production protocol method. Cross-process
gate tests use the real stable identity, so do not run competing worker-owning
test suites concurrently in the same Windows user/logon scope.

Phase 3D validation: 771 full Python regression (no skips), 111 focused worker/
protocol/lifecycle/Phase 2 setup tests (including 25 create cases and 6 lifecycle
cases), 43 C# harness tests (7 new create groups), 13 Windows E2E and 11 unchanged
fake-worker tests pass. WinUI Debug x64 build, Ruff/check/format, pip and diff checks
pass. [test_worker_creation](../../tests/test_worker_creation.py) covers real create,
stale preflight, approval, unknown partial effects, gate rejection/abandonment,
duplicate mutation after cache eviction and accepted-delivery failure.
[ProductionSetupTests](../../desktop/Mirrorly.Desktop.Tests/ProductionSetupTests.cs)
verify actual C# IPC, late results, process/channel loss and no replay. C# calls
[inspect_created_setup](../../tests/inspect_created_setup.py) to verify artifacts
using Python's existing config/repo readers rather than parsing TOML itself.

The lost-client tests now include a barrier after repo initialization and before
config persistence: another worker cannot create until the original service and
worker finish. All mutation artifacts are in test-owned temporary directories;
the actual user's GUI config root and personal folders are not used for creation.
Those historical Phase 3D results did not demonstrate GUI binding, Backup, Resume,
cancellation, true Exit-running UI, reconciliation or final distribution.

Phase 4A validation is in [test_worker_backup](../../tests/test_worker_backup.py),
[ProductionBackupTests](../../desktop/Mirrorly.Desktop.Tests/ProductionBackupTests.cs)
and the existing Phase 2 application tests. All production Backup integration
fixtures use disposable test roots and the qualified worktree interpreter. They
exercise first/incremental snapshots, Resume/Decline/unavailable, one-slot/gate,
notice isolation, three commit states, post-publication failures, timeout and
disconnect. At the Phase 4A validation point the Home button was disabled;
those historical results alone did not prove GUI Backup UX, progress/cancellation,
O-02 or O-09.

## Phase 4B GUI single-Backup binding

The app-scoped [BackupExecutionCoordinator](../../desktop/Mirrorly.Desktop/Services/BackupExecutionCoordinator.cs)
accepts one user-started task selector and owns the live GUI operation/result state.
It sends one `backup.run` through the existing production session and does not
queue or replay. `Running` and the indeterminate indicator mean only that the
operation has not returned; no phase, percentage, item, byte or current-file fact
is available. A second Backup action is unavailable until the current operation
finishes. The approved automatic FIFO, queued state and removal are Phase 4C.

The worker's `backup.resume` request is dispatched off the stdout reader to one
main-window ContentDialog. The app restores a tray-hidden/minimized window before
showing that required decision. `Resume`, `Don't resume` and unavailable map to
the existing interaction responses; dialog failure or window loss does not mean
decline. The worker's ten-minute deadline remains authoritative. Resume takes
priority over true-Exit confirmation; confirmed Exit remains pending until the
active operation and any required interaction finish, then supervises idle worker
shutdown. Close X and Minimize continue the operation.

Read-only `backup.summary` takes only the explicit GUI config root and task
selector. The Python application resolves the repository afresh and uses
`latest_complete()` rather than list display order. The result gives the actual
repository identity and, when present, saved snapshot ID, creation time,
lifecycle sequence and existing snapshot directory. C# does not reconstruct
snapshot paths or parse task TOML. On fresh launch Home can rediscover a saved
version without inventing Activity or claiming that the source is unchanged.
Home can open only the Python-provided existing snapshot path in File Explorer.

### Read-only snapshot collection

`snapshots.list` uses the existing application `list_snapshots()` and core
`select_default_complete()` facts. Its request contains absolute `config_root`,
durable task selector `task`, optional `after` snapshot ID (null for the first
page), and `limit` (default/max 16). It occupies the one application slot and
does not acquire mutation-gate ownership or publish a report. A successful
terminal result has `outcome=succeeded` and a `page` with `selector`, `items`,
`next_after` (null at the end), and `latest_complete_snapshot_id` (null when
there is no complete snapshot). Each item contains snapshot ID, literal
`complete`/`incomplete` status, creation timestamp, nullable uint64 lifecycle
sequence, file/directory counts, total logical bytes, nullable Resume origin,
and manifest format version. It does not expose entries, hashes, physical usage,
reports, or arbitrary snapshot directory paths.

Pages retain core manifest-filename order; their first row is **not** the
authoritative latest. The latest ID comes from the existing core selection over
the full observation, including its legacy ambiguity failure. `after` names the
last returned snapshot ID; it must still identify exactly one item on the next
fresh observation, or the query fails. Repository changes between page calls
can insert/skip items relative to that cursor; paging is not a repository
transaction or lock. Duplicate IDs also fail closed. Malformed manifests,
unreadable configuration, unavailable/invalid repository, and ambiguous latest
selection return application `snapshots_unavailable`, never successful empty
items. Only a real zero-manifest repository returns an empty successful page.

Each item is capped at 16 KiB of JSON and IDs/cursors at 1,024 characters;
the maximum 16-item page stays well below the normal 1 MiB frame. An
unrepresentable summary fails the query rather than truncating facts. The typed
C# `SnapshotCollectionPage` retains nullable fields and the separate latest ID.
Backup Detail now offers a lazy, read-only Snapshots section using this typed
query, with explicit `Load more` and Refresh. It marks only the loaded row whose
ID equals `latest_complete_snapshot_id`; it does not infer latest from page
position or timestamp. Incomplete rows remain visibly unfinished. Query errors
are unavailable states, not empty collections. Selected-snapshot Explorer,
Restore, Verify and Backup-specific Settings remain later work.

### Read-only Restore preparation (R1)

`restore.prepare` occupies the existing single application slot but requires no
mutation-gate ownership. It resolves the durable task selector and repository
afresh, then calls the existing Python `application.restoration.prepare_restore`.
It writes neither destination nor repository and publishes no report. Its exact
request fields are absolute `config_root`, durable `task`, nullable `snapshot_id`
(`null` selects the application's authoritative latest complete snapshot),
absolute `destination`, and `policy` = `skip_existing` or `replace_existing`.
These map only to core `never` and `always`; `older`, path subsets, `in_place`,
Keep both and exact-mirror Restore are not production GUI prepare options.
Only a complete snapshot can be prepared.

A successful terminal result has `outcome=succeeded` and bounded `preview`:
opaque 32-hex `plan_id`, selector, resolved snapshot ID, frozen absolute
destination, policy, file create/overwrite/skip/conflict counts, and
`directory_entry_count`. The latter counts planned directory entries, **not**
directories guaranteed to be created. No entry list, manifest digest, target
paths, duration, free-space promise, physical bytes or integrity conclusion is
sent. Preview JSON is capped at 64 KiB, with separately bounded identifiers and
paths, comfortably below the normal 1 MiB frame. Unrepresentable facts fail
projection; they do not become an approvable plan.

The worker retains at most **one** original Python prepared object. An admitted
new `restore.prepare` discards the prior plan even if the new application query
fails. Rejected invalid/busy requests do not replace it. The plan lives only in
the current worker session; shutdown, EOF and restart discard it. There is no
wall-clock TTL in R1. R3 `restore.execute` resolves this worker-owned
object by ID, consume it once and retain core's manifest/destination/no-upgrade
revalidation. **`plan_id` is a reference, not approval.** In particular,
`file_overwrite_count > 0` is a Review fact; execution still needs distinct,
explicit Replace approval. R1 exposed no execute method and performed
no Restore writes. A missing terminal is transport uncertainty; the client never
automatically replays prepare or assumes the old plan remains usable. A fresh
prepare produces a fresh approval candidate.

Request shape errors and busy remain admission rejections. Application failures
return no preview and a stable code such as `unknown_task`, `task_unreadable`,
`repository_unavailable`, `repository_invalid`, `unknown_snapshot`,
`incomplete_snapshot`, `no_complete_snapshot`, `unsafe_destination`,
`manifest_unavailable` or `restore_plan_failed`, plus bounded technical detail.
Malformed v2 manifest JSON can fail earlier repository lifecycle validation and
therefore report `repository_invalid`; the code names the actual failing
boundary, not a guessed diagnosis. A query failure is never a zero-count plan.
R3 adds destination mutation through the existing worker gate. Restore does
not enter the GUI Backup FIFO.

### Restore selection and Review (R2)

The top-level Restore page now reads the production task catalog and lets the
user choose a Backup by durable selector. Its default saved-version intent is
`snapshot_id=null`, resolved by Python's authoritative latest-complete rule at
prepare time. “Choose another saved version” reads `snapshots.list` in explicit
16-item pages; only complete items can be selected. Manifest-filename order is
not advertised as recency. The existing read-only folder browser selects an
accessible, existing absolute destination. Skip existing is the default;
Replace existing requires explicit selection. The GUI does not offer `in_place`,
Keep both, `older`, path subsets, or exact-mirror Restore.

“Review restore” calls **only** `restore.prepare`. The Review displays the
returned selector-bound snapshot ID, destination, policy, create/overwrite/skip/
conflict counts and planned directory-entry count. R1 does not expose the
prepared snapshot creation time, so Review does not substitute a cached summary
time for that authoritative fact. The opaque plan ID is current-session Review
identity, never approval. Editing choices or leaving Restore discards local
Review; a later Review calls prepare again. An in-flight stale response cannot
replace newer choices. No `restore.execute` method or final Restore action is
available in R2, and Review writes nothing to the destination.

The app-scoped Backup coordinator remains the sole Backup FIFO owner. Running
or queued Backup work prevents Review preparation, including queued work left
after queue attention stops advancement; Restore is never enqueued behind it.
The worker's independent busy admission still handles races. R3 adds the
reverse execution-time Backup admission and lifecycle policy described below.

Review explains safe merge: extra destination contents are not deleted, Skip
keeps existing files, Replace may overwrite matching ordinary files, and type
conflicts remain conflicts. It also states that future v1 execution cannot be
cancelled, devices should remain connected, shutdown/restart should be avoided,
and Close-to-tray will let a running Restore continue. Completed replacements
are not automatically rolled back after later failure or interruption. These
are future execution facts, not a claim that R2 can execute Restore.

### Production Restore execution and result (R3)

`restore.execute` accepts only the current worker-local 32-hex `plan_id` and
`overwrite_approved` boolean. It never accepts a C# plan, manifest digest,
file targets or altered preview. Replace requires `overwrite_approved=true`;
Skip requires false. The final WinUI confirmation, after Review, is the user
approval action; merely possessing a plan ID is not approval. Invalid IDs,
stale/replaced/consumed IDs and missing Replace approval are rejected before
mutation. A worker-busy or mutation-gate rejection is also pre-admission and
retains the current plan. The admitted execute consumes the plan immediately,
before accepted delivery or application apply; success, failure, client loss
and terminal uncertainty never make it reusable. There is no replay or Restore
queue. Another attempt requires a new prepare and confirmation.

The existing worker-wide lifecycle mutation gate is acquired on the worker
control thread and remains held until that worker exits; execute checks its
ownership before admission. The single application slot remains occupied until
the genuine terminal. A client/desktop disconnect does not cancel admitted
execution: the host joins the executor before exiting and retains gate
ownership during that work. The gate controls production worker admission,
not external CLI operations. `restore.prepare` remains read-only and does not
require gate ownership. Core `execute_restore` retains manifest, destination,
reparse and no-upgrade revalidation; Restore does not write the repository or
publish a report.

A normal terminal contains `outcome=completed` or `completed_with_issues` and
bounded summary facts: snapshot ID, destination, files actually restored,
directories actually created, skipped items, conflicts, per-file errors,
leftover temporary files and logical bytes written. Expected Skip-policy
skips alone do not turn completion into failure. Conflicts, errors or leftovers
do produce completed-with-issues. No unbounded per-file list crosses IPC.
An application execution exception has `outcome=failed`, no invented counts,
and `destination_may_have_changed=true`: partial destination writes may already
have happened. A terminal whose facts cannot be projected is unreported; loss
of a genuine terminal is transport uncertainty. Neither is silently retried.

The app-scoped Restore execution coordinator owns Starting, admitted Running,
terminal and transport-uncertain state across navigation and tray hiding.
During Starting/Running, and while the outcome remains uncertain, the sole
Backup coordinator refuses all Backup starts and enqueues. Running/queued
Backup work prevents Restore admission, including at final confirmation.
Restore never enters the Backup FIFO. Close hides to tray; minimize is normal.
True Exit offers Stay or supervised Exit after the current operation; it never
kills an admitted Restore. The UI has no Cancel, fabricated percentage or
Restore Resume. It restores a whole complete snapshot to a separate destination
(`in_place=false`); future per-file selection and cooperative cancellation are
not part of v1.

R3 validation used the registered Debug package and a disposable 20 MiB saved
version. Real GUI Skip preserved an existing file and destination-only extra
file while restoring two new files; Replace restored three files, including
the existing match, and retained the extra file. Repository file hashes stayed
unchanged across both runs. Both reached a factual Restore-complete result and
Mirrorly exited normally. These quick runs completed before a GUI capture of
the transient Running state; coordinator/lifecycle tests cover that state and
supervised Exit. The prepared preview does not yet project the snapshot's
creation time, so Review uses the authoritative snapshot ID.

Live terminal presentation distinguishes normal success, completed with issues,
`not_published`, application `unknown`, `published` with finalization failure,
and no-terminal transport uncertainty. A complete snapshot is a saved-version
fact, not proof that report/finalization succeeded or that the source is current.
An unknown or unreported outcome blocks further GUI-started Backup until a
separate future reconciliation flow; no retry is automatic. See
[Phase 4B acceptance](PHASE4B.md) for validation and remaining scope. Final
manual acceptance is PASS: the user verified the real single-Backup loop,
fresh-process saved-version rediscovery, and Windows Explorer opening the exact
Python-provided snapshot folder containing the expected saved files.

## Phase 4C GUI FIFO boundary

The existing [BackupExecutionCoordinator](../../desktop/Mirrorly.Desktop/Services/BackupExecutionCoordinator.cs)
is the only GUI scheduler. It keeps an ordered in-memory list of durable task
selectors and one Running selector. A second eligible task is enqueued, while a
Running or Queued selector cannot be added twice. Remove from queue only changes
that not-yet-started list; it never sends a worker cancellation request. At
dispatch, the coordinator sends a fresh `backup.run` with the selector, allowing
Python to resolve current task/repository truth again.

A genuine application terminal result advances FIFO even if it reports issues,
`not_published`, application `unknown`, or `published` with failed finalization.
Missing/unusable terminal evidence or worker busy/gate rejection pauses automatic
dispatch and keeps not-yet-started selectors in memory. The worker still rejects
concurrent application requests rather than silently queueing them. Resume holds
the one slot and retains the pending FIFO order. Close X and Minimize do not pause
dispatch. Confirmed true Exit clears pending selectors, supervises only the
Running operation (including a required Resume decision), then shuts down the
worker. Stay leaves the queue intact. A desktop restart does not restore it.
There is no queue persistence, progress, cooperative cancellation, priority,
reorder, pause, Back up all, or persistent Activity. The petal-start animation
remains deferred because it is not part of scheduling correctness. See
[Phase 4C acceptance](PHASE4C.md) for validation.
