# P2 — payload-local Python and production worker

Status: **PARTIAL / BLOCKED — intermittent clean-VM Setup-to-Review visual corruption;
business and post-use relocation acceptance remain incomplete**.
The development-machine qualification, application integration and regression
results below are measured. They do not establish a complete portable release.
[P1](DEPLOYMENT_POC.md) remains CLOSED for the GUI deployment layer.

## Separate launch modes

| Configuration | Worker | Deployment |
| --- | --- | --- |
| Debug | Explicit development Python, checkout and editable-install qualification | Existing registered packaged development workflow |
| PackagingPoC | Disabled; truthful unavailable state | Reproducible P1 GUI-only experiment |
| PackagingWorkerPoC | Payload-local qualified Python and the real WorkerHost | Unpackaged dual-self-contained P2 experiment |
| Release | Existing development-bound configuration | Not the final production configuration |

P2 has no root launcher. Start `app/gui/Mirrorly.Desktop.exe` directly. It uses
`GuiDataPaths.ForCurrentUser()` and the same application/session/coordinators as
development. User task configuration stays in `%LOCALAPPDATA%/Mirrorly/Gui/Tasks`.
Relocating an exited payload changes internal executable/module paths, never
Source, Backup location, repository or task selector. There is no second worker
engine, IPC system, automatic replay or changed Backup/Restore semantics.

`ProductionPayloadPaths` derives `app` from `AppContext.BaseDirectory` ending in
`app/gui`. `WorkerPayloadLaunch` derives absolute Python/bootstrap paths from it;
it does not search PATH, cwd, Conda, registry or the checkout. Missing or invalid
payload fails closed through the existing worker-unavailable presentation.
No internal program path is persisted as launch configuration.

## Pinned runtime and assembly

The development environment remains **CPython 3.12.14 x64**. With explicit user
approval, P2 uses official **CPython 3.13.15 x64 embeddable**, after its full
compatibility suite passed. No language requirement change was made.

[python-runtime.json](../../desktop/packaging/python-runtime.json) pins the URLs,
filenames, versions, ABI/platform tags and SHA-256 values:

| Artifact | SHA-256 |
| --- | --- |
| python-3.13.15-embed-amd64.zip | `d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf` |
| blake3-1.0.9-cp313-cp313-win_amd64.whl | `caded2806d2cbeed638c5e2517ed8b2a94165b3452fda35e72896142d22070e0` |

Sources: [official Python release](https://www.python.org/downloads/release/python-31315/)
and [BLAKE3 distribution](https://pypi.org/project/blake3/1.0.9/).
The build downloads only missing cache entries and verifies every cached or new
artifact. Hash mismatch stops assembly; a matching filename is insufficient.
No runtime, wheel, generated bundle or VM artifact belongs in Git.

From the repository root, use an explicit build-time Python with pip/setuptools
and Visual Studio MSBuild. This Python is a build tool, not a runtime dependency:

```powershell
& desktop/Build-WorkerPoC.ps1 `
  -Python 'C:\Users\sakur\anaconda3\envs\mirrorly-gui-dev\python.exe' `
  -MSBuildPath 'C:\Program Files\Microsoft Visual Studio\18\Community\MSBuild\Current\Bin\MSBuild.exe' `
  -OutputDirectory (Join-Path (Get-Location) 'desktop/artifacts/p2-new-output')
```

Use a fresh output directory. The script assembles pinned Python/dependencies,
builds a normal Mirrorly wheel from a fresh source staging tree without fetching
build dependencies, qualifies it, then publishes the P2 GUI. It writes a
per-file `SHA256SUMS.txt`. `python-inventory.json` records pins, actual staged
distribution metadata/license files, source HEAD, dirty status and worker-file
hashes. A pre-commit candidate honestly records a dirty source tree; its file
hashes identify the tested worker. This locks dependency versions, not ZIP bytes
or all developer toolchain versions.

```text
P2 candidate/
  SHA256SUMS.txt
  python-inventory.json
  app/
    gui/       GUI + self-contained .NET/Windows App SDK + PRI/XBF/assets
    python/    official embedded executable/DLLs/stdlib ZIP/LICENSE.txt
      python313._pth
      packages/  blake3 Python package, native extension and dist-info/licenses
    worker/    mirrorly package and normal wheel dist-info/license
```

## Isolation and qualification

Python starts with `-I -B -u`, absolute bootstrap, expected interpreter and app
root. The only `_pth` entries are `python313.zip`, `.`, `packages`, `../worker`.
There is **no `import site`**, no editable installation, no pip or startup install.
The GUI removes inherited `PYTHON*`, `CONDA*`, `VIRTUAL_ENV` and
`__PYVENV_LAUNCHER__` variables, preserving normal Windows environment variables.
Explicit executable selection and `_pth` isolation also withstand hostile PATH.
`-B` prevents worker bytecode writes inside the payload.

Before advertising hello, `payload_launch.qualify` checks:

- Exact interpreter/bootstrap roots, Python 3.13.15, Windows x64 and isolation flags.
- Exact allowed import search roots and absence of initialized `site`.
- Actual Mirrorly application/core/WorkerHost module origins within the worker tree.
- Protocol 1.0; BLAKE3 package, native extension and distribution version/origin.
- Known BLAKE3 `abc` digest and actual core BLAKE3 hasher availability.
- Normal payload-local Mirrorly distribution metadata; no `direct_url.json`.
- Every loaded file-backed module stays under the Python or worker subtree;
  CLI is not imported.

The C# client independently validates hello facts, including the actual child PID,
interpreter, paths, versions, module origins and isolation facts before initialize.
Failures cannot fall back to another Python or checkout. The development
qualification remains separate and retains its original strict checks.
Both bootstraps call the same `launch.serve` and `WorkerHost`.

`--probe` executes the same qualification and emits JSON without starting a host
or application operation. Its origin facts are diagnostic output, not persisted
launch configuration or business input. Qualification checks layout/import
provenance; it is not a signature or a defense against an attacker replacing the
entire trusted payload. Signing and release integrity remain later work.

## Native dependencies and licenses

Actual staged Python distributions are Mirrorly 0.1.0 and BLAKE3 1.0.9.
BLAKE3's conditional typing-extensions dependency does not apply to Python 3.13.
There is no separate installed Python dependency needed at runtime.

`dumpbin /DEPENDENTS` on the staged BLAKE3 extension lists `python313.dll`,
`VCRUNTIME140.dll` and Windows kernel/CRT API dependencies. The official embedded
runtime carries its VC runtime DLLs. The real extension loads and computes the
fixed digest without PATH modification or extra DLL search registration. Clean
VM native-dependency confirmation remains pending.

The assembly checks actual dist-info metadata and license-file presence. CPython's
LICENSE.txt and BLAKE3/Mirrorly wheel licenses are retained. Its inventory preserves
license declarations and requires-dist markers from the actual wheels. A final
combined third-party notice for the entire GUI/runtime payload remains release
assembly work; P2 does not claim that inventory alone completes legal review.

## Measured development validation

- Candidate 3.13.15 compatibility before implementation: **848 passed**.
- New payload qualification/isolation tests: **17 passed**.
- Full Python suite with the payload integration environment set: **865 passed**
  on development 3.12.14 and **865 passed** on candidate 3.13.15.
- Full C# harness with explicit development Python and real P2 payload:
  **123 passed / 0 failed**.
- Packaged Debug x64 build, P1 publish/evaluated artifact checks and P2 assembly:
  passed. P1 GUI was launched again and retained its worker-disabled title,
  unavailable Home and both SVG decorations.

The test-only candidate interpreter has pytest/setuptools and a checkout editable
install for the repository's full-suite guard. That test environment is **not**
the production payload. Production subprocess tests use the isolated staged
interpreter with no site/editable install.

Python tests exercise real qualification under hostile environment/cwd, read-only
file attributes, outside module origins, missing/unloadable BLAKE3, wrong
interpreter/root/version/protocol, site contamination and cache hash mismatch.
C# tests cover paths, environment, client facts and the same real DesktopSession
Setup/catalog/Backup/summary/snapshots/Restore APIs. The application smoke checks
source preservation, real complete snapshots, destination extras and repository
SHA-256 stability through Restore. After moving the payload, the same durable
task/business paths survive and another Backup produces the second snapshot.
No fake WorkerHost or replacement Restore engine is used.

Set `MIRRORLY_TEST_PAYLOAD` to an absolute assembled candidate root when running
these integration tests. Artifact-dependent Python cases explicitly skip without
it; the C# harness prints NOT RUN for those cases. Such runs do not establish P2
acceptance. File read-only attributes were tested; a directory ACL denying all
payload writes still requires production release acceptance.

## Clean-VM acceptance pending

The user confirmed restoring the P1 Windows 10 VM's clean snapshot. Delivery:
625 files including the integrity manifest, 262,134,564 bytes; transfer ZIP
104,297,537 bytes, SHA-256:

```text
DC5F147771FAD2F346F0A35DC9EF0870BE09A25171CCC439334CDFD6A5A731D5
```

Await pre-test inventory, checksum, real GUI Setup/Backup/Restore, actual worker
process/import origins, Chinese+spaces/unrelated cwd, tray/normal Exit and
post-use relocation with unchanged tasks/business paths and a second Backup.
Do not install runtimes on the VM or recreate missing tasks to bypass a failure.
Passing on that specific Windows 10 VM will not declare official Windows 10
support. P2 remains PARTIAL until all core acceptance evidence is established.

P3/later work remains: single-instance and notification portability, root thin
launcher, final production configuration/layout, signing, installer, updates,
final metadata and production-payload release acceptance. None is implemented here.

## Opt-in Setup diagnostic experiment (not a fix)

The user reported severe visual corruption after Continue on the original P2
artifact. A subsequent controlled A/B observation on that same artifact did not
reproduce it: Review reached Ready, without Create. Both captures (PID 10724)
loaded hostfxr, hostpolicy, coreclr, Microsoft.UI.Xaml and
Microsoft.WindowsAppRuntime from `app/gui`. This weakens the runtime-origin
mismatch hypothesis; it does not exclude WinUI/platform issues or close P2.
P1 remains CLOSED/PASS independently.

The diagnostic candidate uses the unchanged PackagingWorkerPoC configuration,
SDK/runtime versions and original candidate Python/worker payload. It adds only
the internal `SetupDiagnostics` helper and read-only sampling calls. Enable for
one launched process with `MIRRORLY_SETUP_DIAGNOSTICS=1`; absent, `0`, or any other
value leaves it off in Debug, P1, P2 and Release. No persistent setting is written.

Logs are UTF-8 text under `%TEMP%/Mirrorly-SetupDiagnostics`, named
`setup-<pid>-<random>.log`. This needs no administrator access or payload writes.
One process writes its own file under a lock. Limits: 512 records, 256 KiB total,
4096 characters per record (plus newline). No rotation, background writer,
timer, retry, or third-party logger. A sink failure disables further writes and
does not escape into application code. Stop and collect the log if it hits a
limit; absence of a record is not proof a code path did not run.

Each record carries UTC timestamp, elapsed milliseconds, PID, managed thread ID
and SynchronizationContext type/null. T0/T6/T7/exit/T8 are in Continue_Click;
T1 follows revalidation; T2 follows Checking; T3/T4 record preflight send/terminal
and request/operation correlation; T5 follows final state application. Existing
catch paths record exception type, HResult and message length. Raw exception
messages are deliberately replaced by `[redacted]` because they may contain
paths, names or other user data. No DTO, business path, file name/content,
textbox text or technical-details text is logged.

When the UI dispatcher grants thread access, sampling includes IsReview/State/
working; both step Visibility/Opacity/ActualWidth/ActualHeight; InfoBar open/
severity and empty/present title/message classification; focused control type
and XAML name; PageHost content type; navigation selection type/known tag, pane,
enabled/opacity/size/visibility/display mode. Off-thread snapshots explicitly
skip UI properties. Snapshot exceptions are recorded by type/HResult only.
One additional dispatcher callback takes T8 without changing UI state. Its
enqueue result is logged; it is not a render-completion guarantee.

Instrumentation keeps the existing revalidation, preflight, visibility, scroll
and Focus order. It does not sleep, repaint, mutate resources or suppress/retry
application exceptions. Synchronous diagnostic reads/file appends and the extra
callback necessarily add timing overhead; non-reproduction is not a fix claim.

For VM evidence, extract the diagnostic ZIP into a separate folder, preserving
the original P2 artifact. In Windows PowerShell, with Mirrorly fully exited:

```powershell
$diagRoot = 'C:\临时 软件\Mirrorly P2 Diagnostic'
$previousDiagnosticValue = $env:MIRRORLY_SETUP_DIAGNOSTICS
try {
    $env:MIRRORLY_SETUP_DIAGNOSTICS = '1'
    Start-Process -FilePath (Join-Path $diagRoot 'app\gui\Mirrorly.Desktop.exe') -WorkingDirectory 'C:\Windows'
} finally {
    if ($null -eq $previousDiagnosticValue) {
        Remove-Item Env:MIRRORLY_SETUP_DIAGNOSTICS -ErrorAction SilentlyContinue
    } else { $env:MIRRORLY_SETUP_DIAGNOSTICS = $previousDiagnosticValue }
}
```

Choose the existing disposable Source and Backup location; click Continue once.
Record whether Review is stable or corrupt, do not click Create, and exit normally
through the tray. Collect that PID's log from the directory above. Do not proceed
to Backup/Restore/relocation until this diagnostic evidence has been reviewed.

Development-host validation of this diagnostic candidate (2026-09-26):

- Full C# harness: 126 passed / 0 failed with diagnostics off, and separately
  126 passed / 0 failed with diagnostics enabled, using the explicit development
  Python and the candidate payload. The tests cover opt-in gating, bounded output,
  redaction, sink failures and identical Setup Ready results with logging off/on.
- Two earlier enabled runs with a custom, deep workspace TEMP/TMP location had
  117 passed / 9 failed. The first observed failure was a real snapshot-query
  Backup outcome assertion; subsequent gate failures cascaded. The cause of the
  custom-TEMP failure is not established. Returning to the baseline TEMP/TMP,
  without source changes, produced the enabled 126/0 result above.
- Packaged Debug build, PackagingWorkerPoC publish, deployment-property/resource
  checks, XAML parsing, local documentation links and diff checks passed.
  This instrumentation does not change Python source; no Python suite was rerun.
- A visible diagnostic GUI launched from an unrelated cwd reached Review Ready
  through the real worker without Create. PID 29800 captured T0 through T8:
  request 3 received a terminal, all samples used managed thread 2 with
  DispatcherQueueSynchronizationContext and HasThreadAccess=true; Review was
  visible with nonzero dimensions by T4, and focus reached NameInput at T7/T8.
  No corruption or exception record was observed in this run. The disposable
  Backup location remained empty. This is a successful-path observation, not a
  diagnosis or proof that the intermittent VM blocker has disappeared.
- Diagnostic ZIP integrity and unchanged Python/worker files were checked;
  the five runtime DLLs used in the earlier A/B capture match the original P2
  artifact byte-for-byte. Clean-VM execution of this diagnostic candidate remains
  not verified. P2 remains PARTIAL/BLOCKED.

### Navigation-specific diagnostic extension (not a fix)

The separate `Mirrorly-P2-NavigationDiagnostic.zip` extends the same opt-in
`MIRRORLY_SETUP_DIAGNOSTICS=1` logger. Original P2 and SetupDiagnostic ZIPs remain
unchanged. Default-off behavior and the shared 512-record / 256-KiB / 4096-character
limits remain unchanged. There is no second log framework or background sampler.
The VM navigation-corruption case has **not** been run against this artifact.

Finite sampling points:

- S0 SelectionChanged, SelectAsync entry and validation pending; S1 validation
  applied; S2 the existing revision guard discarded a stale result.
- N0 row double-tap, keyboard, address, Back or Up request; N1 admitted navigation;
  N2 Browse returned before publishing; N3 normal assignments and Changed finished.
- F0 the result of the existing TryEnqueue; F1 callback entry, including unloaded
  panes; F2/F3 around the existing UpdateLayout; F4 after target resolution, before
  the existing Focus; F5 immediately after it, including its actual bool result.

Pane labels are allowlisted Source/Backup/Other, independent of business paths.
Each pane has separate selection, navigation and focus counters. An AsyncLocal
diagnostic request scope carries the originating navigation ID across existing
awaits; the callback captures that ID and the queued list identity. The latest
**admitted** navigation ID is logged alongside it, but is never consulted by a
business guard. Requests rejected by existing CanBack/CanUp/IsBrowsing conditions
may have N0 without N1. This is evidence, not cancellation or suppression of focus.
Initialization/direct model navigation also gets an ID even without a UI request.

All records include thread/context, revision, browsing/validating/editing, list
identity/count and path *classification/presence only*. UI records add thread
access, loaded/enabled state, selection index/presence and focus type/allowlisted
XAML name. S0 entry reports the revision before increment; validation-pending and
S1/S2 report the revision actually captured by the existing guard.

Focus snapshots include ListView size/opacity/visibility, container-zero
existence/type/identity/load state/size, bounds relative to the list, and explicit
Clip type/rectangle. F2/F3/F5 additionally read the internal and nearest outer
ScrollViewer: offsets, viewport, extent, scrollable size, zoom, effective
BringIntoViewOnFocusChange and element state. Template lookup is capped at 32
nodes and depth 8; ancestor lookup at 32 levels. Missing references/read failures
are explicit unavailable evidence. No template application, scroll, layout,
focus, timer, LayoutUpdated or SizeChanged subscription is added.

RuntimeHelpers.GetHashCode supplies process-local object identifiers. It retains
no list/container reference, is not a content hash, and can collide; never compare
it across processes or treat it alone as proof of object identity. Focus names
are allowlisted; Automation.Name, item content, paths, folder names and input text
are never sampled. Snapshot errors log only type/HResult. Synchronous property
reads, bounded tree lookup and file writes can perturb timing. F5 is immediate,
not a rendered-frame or completion-of-scrolling guarantee; no extra enqueue was
added to wait for either. Neither a healthy trace nor matching IDs proves a fix.

VM procedure after review: verify the ZIP SHA-256 supplied with the artifact,
extract to a new Chinese/space folder, then verify its SHA256SUMS.txt. With all
Mirrorly sessions exited, set the opt-in only in the launching PowerShell process
and directly launch `app/gui/Mirrorly.Desktop.exe` with unrelated cwd. Enter Setup
Step 1 and perform one Source drive navigation. Do not deliberately hunt an exact
window geometry. On corruption stop interacting and retain that PID's log; on a
healthy attempt retain it as a control. Do not Continue/Create/Backup/Restore.
Exit through the tray. Collect `%TEMP%/Mirrorly-SetupDiagnostics/setup-<pid>-*.log`.
Further attempts require review; this batch does not run VM reproduction.

Navigation extension validation (2026-09-26): full C# harness **129 passed / 0
failed**, separately with diagnostics off and on, against the new diagnostic
payload. This includes real payload-worker Setup/Backup/Restore/relocation tests,
existing FolderBrowser/Setup tests, stale-selection evidence, privacy/bounds,
opt-in/no-log behavior, request identity across awaits, and logging failures.
Packaged Debug build, PackagingWorkerPoC publish, evaluated deployment/resource
checks, XAML parsing, this document's local links and `git diff --check` passed.
No Python source was changed in this extension and no full Python suite was rerun.
No interactive GUI or VM run was performed for the new navigation artifact;
ScrollViewer/container snapshots still require runtime evidence in that run.

Artifact: `desktop/artifacts/Mirrorly-P2-NavigationDiagnostic.zip` (ignored),
107265458 bytes. SHA-256:
`1FAC40A1D8BEAF691F0A7F5B527F5D18B394C16D9105A178D5767B411990FCFA`.
ZIP-entry verification against SHA256SUMS: **625 files, 0 mismatches**, plus the
manifest itself (626 entries). The 90 Python/worker files and five relevant GUI
runtime DLLs match original P2 byte-for-byte; both older ZIP hashes are unchanged.
P1 remains CLOSED/PASS; P2 remains PARTIAL/BLOCKED. No root-cause fix is included.
