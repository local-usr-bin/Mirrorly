# Production Worker Contract v1 — Phase 3C

Current implementation / frozen contract, 2026-09-24. This is the current production
IPC source of truth. [Phase 0 IPC_CONTRACT](IPC_CONTRACT.md) remains a historical
proposal, not an implemented API. [Phase 2](PHASE2.md) completed shared application
orchestration; this slice exposes **only read-only setup preflight**. The real GUI
Home/Setup buttons remain prototypes. This is not production distribution readiness.
Phase 3B's readonly bridge is complete. Phase 3C removes the finite request budget,
adds worker-lifetime admission and freezes [GUI configuration ownership](CONFIGURATION.md).
It does **not** enable any mutation method, including setup.create.

## Implemented scope and ownership

- [worker/launch.py](../../src/mirrorly/worker/launch.py): explicit development
  entry point and checkout qualification, no PATH fallback or CLI import.
- [worker/host.py](../../src/mirrorly/worker/host.py): negotiation, admission,
  one synchronous application slot, bounded session records and channel lifecycle.
- [worker/protocol.py](../../src/mirrorly/worker/protocol.py): framing/validation.
- [worker/preflight.py](../../src/mirrorly/worker/preflight.py): intent validation
  and explicit projection of existing application facts; no setup policy copy.
- [worker/transport.py](../../src/mirrorly/worker/transport.py): bounded output
  boundaries; only pumps perform protocol/diagnostic writes.
- [worker/lifecycle.py](../../src/mirrorly/worker/lifecycle.py): Windows user/logon
  session mutex, retained by the host thread through executor completion.
- [ProductionWorkerClient.cs](../../desktop/Mirrorly.Desktop/Services/ProductionWorkerClient.cs)
  and [ProductionProtocol.cs](../../desktop/Mirrorly.Desktop/Services/ProductionProtocol.cs):
  separate C# client, not bound to any View/ViewModel.

The Phase 1A worker, FakeWorkerClient, PrototypeConfiguration and GUI lifecycle
are unchanged. The old fake interpreter still intentionally does not import core.
There is no mutation method, Resume interaction, progress, cancellation, queue,
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
Nothing auto-starts from Home. Each client owns one on-demand long-lived session;
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
   Payload: supported_versions, worker=`production-readonly`, qualification, limits.
2. Client emits `initialize`, selected version, new request_id, payload
   `{"required_capabilities":[]}`.
3. Worker responds `response`, phase=`terminal`, result containing version,
   methods, capabilities and limits. Unsupported versions/capabilities are rejected.
4. Only then can `request` messages be admitted.

Implemented methods: `ping`, `status`, `worker.shutdown`, `setup.preflight`.
No repository.inspect or tasks.list was added. Unknown methods, including create,
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
stays readonly and does not kill or reattach to the owner. Its status probes an
available mutex with acquire/release; that observation is immediately stale-able
and is **not** admission. It does not silently acquire retained ownership on status.

Future mutation admission **must call require_ownership() on the host/control
thread before invoking the service**. Failed acquisition must reject with no
application invocation. Repeated checks by an owner do not recursively acquire.
There is no mutation dispatcher to connect this guard to yet.

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
scopes. All four production methods remain readonly; a gate owner gains no extra
method or advertised progress/cancellation capability.

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

There is no setup.create. Preflight never authorizes a future write and creates no
repository/config/lock or test file. Future create must rerun application validation.

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
No such fault is converted into application failure. An active read-only call returns
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

## Approved policies for later mutation phases — NOT IMPLEMENTED

- O-01: confirmed true Exit clears not-started GUI queue items, supervises the
  current operation to completion, then exits worker/GUI. No withdrawal of a
  confirmed Exit intent in v1. Close X/Minimize are not true Exit.
- O-03: unexpected parent loss exits idle worker; an active noninteractive call
  may finish before exit. Required interaction unavailable ends the operation at
  that existing decision boundary. Parent loss is not cancellation. No reattach.
- O-08 Resume: ten-minute default deadline. Timeout/disconnect/unavailable cannot
  become decline_resume (False would continue Backup), nor silent acceptance.
  The decision stays inside existing locks, after migration and before scanning.
- O-04: all application operations share one execution slot. This is separate
  from the future GUI FIFO and does not solve external CLI concurrency.
- **Before any mutating method:** wire the Phase 3C lifecycle guard into mutation
  admission before service invocation; retain ownership through all execution.
- O-07 ownership is APPROVED and centralized in Phase 3C; see
  [CONFIGURATION](CONFIGURATION.md). C# still must not parse/write task TOML.
  Setup.create itself remains unimplemented and requires a separate approved slice.
- O-02 cancellation and O-09 worker packaging remain open; no Job Object
  kill-on-close, mutation retry, GUI binding or production notification is added.

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
