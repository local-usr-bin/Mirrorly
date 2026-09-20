# Production Worker Contract v1 — Phase 3B

Current implementation / frozen contract, 2026-09-21. This is the current production
IPC source of truth. [Phase 0 IPC_CONTRACT](IPC_CONTRACT.md) remains a historical
proposal, not an implemented API. [Phase 2](PHASE2.md) completed shared application
orchestration; this slice exposes **only read-only setup preflight**. The real GUI
Home/Setup buttons remain prototypes. This is not production distribution readiness.

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
| Session request ledger | 4,096 distinct increasing request IDs |
| Terminal result cache | Last 32 application results |
| Protocol input queue | 32 frames |
| Protocol output queue | 32 normal positions + 1 reserved terminal position |
| Separate droppable notice queue | 16 frames |
| Diagnostics queue | 32 chunks, each at most 2,048 characters |
| C# pending requests | 64 |

Limits are centralized constants. The session ledger bound is an implementation
detail added to make admission memory strictly bounded, not a disk journal. At
exhaustion the session faults, admits no more work, lets an active call finish and
exits. A new session never replays requests. Before enabling long-running mutation,
account for this advertised budget in client control polling/session renewal;
renew only while idle with known outcomes, never by retrying an uncertain operation.

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

The ledger records whether each seen request invoked application, its operation ID
and lifecycle. Old/stale IDs never execute, even after terminal detail is evicted.
The `application_invoked:false` in a duplicate rejection describes that duplicate
attempt, not an assertion that its original request never ran.

C# returns a WorkerRequest handle. WaitAsync(timeout) times out only that wait;
the underlying Terminal task and correlation stay alive. No automatic retries.
Send failure can mean some/all bytes arrived and is transport uncertainty. Local
encoding rejection before any write is a local validation exception instead.

## Status is host observation

`status` takes `{}` or `{"request_id":"earlier-id"}`. It returns initialized,
channel_healthy, state (idle/busy/terminal_pending), active request/operation, and
optional request lookup. Lookup distinguishes never_seen, rejected/control,
accepted, terminal, and terminal with detail evicted (`terminal_available:false`).
Cached results do not survive process exit. A high-water mark is not exactly-once
across sessions. No status claims scanning/publishing, file or byte progress.
Host responsive does not mean filesystem IO is advancing.

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
- **Before any mutating method:** implement a worker lifecycle admission gate
  whose identity is stable for the relevant Mirrorly user/session scope, including
  orphan lifetime. A random per-GUI-process key is insufficient. No gate exists yet.
- O-07 config ownership blocks setup.create. C# must not parse/write task TOML.
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
