# GUI configuration ownership — O-07 v1

APPROVED / CURRENT FACT, Phase 3C, 2026-09-24. This living decision supersedes the
configuration-ownership proposals in the historical Phase 0 documents. Phase 3D now
enables setup.create through worker/client. [Phase 3E](PHASE3E.md) connects the actual GUI Setup flow and Python-owned task catalog.

## Physical root and replaceable boundary

GUI-created Mirrorly tasks belong to a dedicated, per-Windows-user, machine-local,
stable root independent of cwd and the CLI default `./.mirrorly`.

Current development selection:

```text
%LOCALAPPDATA%\Mirrorly\Gui\Tasks
```

[GuiDataPaths](../../desktop/Mirrorly.Desktop/Services/GuiDataPaths.cs) alone selects
this path using Environment.SpecialFolder.LocalApplicationData with DoNotVerify.
It returns absolute DataRoot and TaskConfigRoot. It does not create directories,
enumerate tasks, persist data or access TOML. It fails for a missing/relative local
data base rather than falling back to cwd. Tests inject an absolute, isolated base.

[ProductionWorkerClient](../../desktop/Mirrorly.Desktop/Services/ProductionWorkerClient.cs)
accepts this provider plus SetupPreflightIntent and projects an explicit absolute
config_root into the existing readonly wire request. Its lower-level input also
accepts explicit roots for tests/tools. Neither client nor ViewModels construct
LocalAppData/package paths. Phase 3D adds a CreateAsync provider overload with
explicit boolean approval. Phase 3E ViewModels use these through one app-owned DesktopSession.

The existing Python application alone computes config.d/task filename and the
authoritative prospective repository path. Calling the provider/preflight does not
write either root. No Codex checkout or interpreter path goes into user configuration.
O-09 remains OPEN: packaged path virtualization/runtime behavior and final physical
deployment location must be qualified before distribution. The provider is the
replacement point; this development choice does not decide packaging or migration.

## Business truth vs GUI presentation

Python owns task TOML parsing/serialization/validation, source and target, filters,
retention, verify policy, volume/repository anchors and identity semantics. C# passes
intent and a root; it never parses, generates or edits task TOML. A GUI-owned root
does not authorize C# to maintain a second configuration implementation.

Future window state/theme/order/pinning/last-page preferences stay separate from
TaskConfig. If future GUI associations reference a task, that reference is not a
second source/target/retention/repository truth. No GUI registry, preference store,
Activity persistence or association database is added in Phase 3C.

Configuration-file selection identity, TaskConfig.name and lock identity retain
their existing Python meanings. They are not interchangeable. No new task UUID,
display-name model or repository format is introduced. First GUI task creation must
use the existing application semantics and independently rerun setup validation.

## Coexistence with CLI

CLI defaults and existing tasks are unchanged. GUI does not discover, scan for,
copy, move or merge arbitrary CLI `./.mirrorly` roots. Existing CLI tasks remain
independent and untouched. Future **Import existing Backup/task** or association
must be explicit and separately designed; none is implemented here.

The dedicated root is a GUI ownership convention, not a new application/CLI
restriction on shared repositories or task naming.

## Validation and next boundary

C# tests check absolute per-user LocalApplicationData selection, cwd independence,
absence of persistence, rejection of a relative base, and real IPC preflight using
the provider's explicit root. No repository/task config is created by these tests.
Source review confirms the sole LocalApplicationData construction is this provider;
the C# production code contains no task TOML handling or CLI-root discovery.

O-07 ownership is frozen. Phase 3D connects worker lifecycle require_ownership to
setup.create admission, preserving application revalidation, explicit approval and
known/unknown partial effects. Mutation tests use isolated temporary roots through
the provider and verify artifacts with existing Python config/repo readers; C# does
not parse TOML. Tests never use the actual user's GUI config root for creation.

Full registry, CLI import and uncertainty reconciliation remain later scope. Phase 3E rereads Python task configurations after restart; it persists no second registry. Its Debug-only explicit smoke-test data-base argument is handled at the centralized development/provider boundary. An unreported create result is unknown, not permission for an automatic retry.
