# GUI configuration ownership — O-07 v1

APPROVED / CURRENT FACT, Phase 3C, 2026-09-24. This living decision supersedes the
configuration-ownership proposals in the historical Phase 0 documents. It does not
enable real Create Backup or any production mutation.

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
LocalAppData/package paths. No ViewModel is bound to production preflight yet.

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

O-07 ownership is frozen. Before the first mutation, a separate task must connect
worker lifecycle require_ownership to admission, expose setup.create with the
existing execution revalidation/explicit decision/partial-side-effect contract,
and prove no replay under transport uncertainty. No creation method is present now.
