# Production Worker Contract v1 — Phase 3E

Current implementation / frozen contract, 2026-09-24. This is the current production
IPC source of truth. [Phase 0 IPC_CONTRACT](IPC_CONTRACT.md) remains a historical
proposal, not an implemented API. [Phase 2](PHASE2.md) completed shared application
orchestration; the worker now exposes setup preflight and **setup.create, its first
and only production mutation**. Phase 3E connects real GUI Setup and a readonly durable task catalog; Back up now remains unavailable. This is not production distribution readiness.
Phase 3B's readonly bridge is complete. Phase 3C removes the finite request budget,
adds worker-lifetime admission and freezes [GUI configuration ownership](CONFIGURATION.md).
Phase 3D enabled setup.create under that gate. [Phase 3E](PHASE3E.md) now binds Setup, owns the production session at app scope, and uses Python task data on Home.

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
- [worker/transport.py](../../src/mirrorly/worker/transport.py): bounded output
  boundaries; only pumps perform protocol/diagnostic writes.
- [worker/lifecycle.py](../../src/mirrorly/worker/lifecycle.py): Windows user/logon
  session mutex, retained by the host thread through executor completion.
- [ProductionWorkerClient.cs](../../desktop/Mirrorly.Desktop/Services/ProductionWorkerClient.cs)
  and [ProductionProtocol.cs](../../desktop/Mirrorly.Desktop/Services/ProductionProtocol.cs):
  separate C# client behind DesktopSession; ViewModels do not own processes.

The Phase 1A worker, FakeWorkerClient and PrototypeConfiguration remain unchanged test infrastructure. Normal GUI runtime no longer starts them. The old fake interpreter still intentionally does not import core.
There is no Backup/verify/restore execution, Resume interaction, progress, cancellation, queue,
Activity, notification UX or stdio reattach. No application/core/CLI algorithm,
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

Implemented methods: `ping`, `status`, `worker.shutdown`, `setup.preflight`, `setup.create`, `tasks.list`.
No repository.inspect was added. The bounded readonly tasks.list contract is specified in [PHASE3E](PHASE3E.md#durable-task-catalog). Unknown methods, including
backup, verify, restore, cancellation and test_crash, are rejected before application.

All advertised capabilities are false: resume_interaction, phase_progress,
item_progress, byte_progress, current_item, cooperative_cancel.
`event` is an envelope kind and a reserved lossy output boundary; no business event
stream is emitted yet. Interaction message kinds are not accepted/advertised;
interaction_id must remain null. Adding a field is not adding a capability.

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
scopes. Ownership permits admission of setup.create only; it does not add Backup,
progress or cancellation capability. WAIT_ABANDONED is successful ownership, not
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

## Threads, backpressure and channel loss

- One input reader parses bounded frames into a bounded control inbox.
- Host/control loop performs only protocol/admission/cache work.
- One application executor calls the unchanged synchronous service.
- Exactly one stdout writer drains bounded protocol output.
- A separate bounded lossy diagnostic pump owns stderr. Accidental Python
  print/warnings are routed there rather than corrupting protocol stdout.

Application code/callbacks never perform pipe writes or wait for delivery. Future
noncritical notices use the separate try-enqueue boundary; failure is isolated.
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
- O-08 Resume: ten-minute default deadline. Timeout/disconnect/unavailable cannot
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

Future result rules remain frozen: Backup preserves not_published/unknown/published;
transport loss cannot manufacture a commit fact. Setup partial effects remain
three-valued. Verify writes mandatory reports. Restore uses session-owned plans
with existing apply revalidation, never a trusted plan reconstructed by C#.

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
No result here demonstrates GUI binding, Backup, Resume, cancellation, true
Exit-running UI, reconciliation after uncertainty or final distribution.
