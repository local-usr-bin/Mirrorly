# Phase 1C — Backup Setup UX Prototype

> 2026-09-20 · **PASS — Backup Setup UX Prototype validated; product review approved.**
> Phase 1B frozen at `00d07da0bd8fe2e3350523c57eb6c2d2089e8683` on `codex/mirrorly-gui`.
> CURRENT FACT: interactive UI + read-only filesystem browsing + prototype confirmation.

## Scope and boundaries

Home's Empty fixture → **Set up backup** opens a product Setup page under Backups.
Step 1 chooses one source folder and one backup location. Step 2 reviews the display
name, source, chosen location and final repository path preview. **Create backup**
only opens an InfoBar: “Prototype only” / “Backup setup is not connected to the
Mirrorly backup engine yet. No folders or backup data were changed.” Return Home
does not insert a fictitious Backup or snapshot. Form state is kept only in memory.

No core/CLI call, `init`, backup, restore, verify, repository creation, file operation,
config persistence or production worker was added. Phase 1A fake worker/tray services
remain unchanged. No dependency, package version or packaging decision changed.
**O-09 remains OPEN**; Phase 1A MSIX output is not a final Python distribution solution.

## Component and service boundary

- `Components/FolderBrowserPane.xaml(.cs)` is shared by Source and Backup location.
  It owns standard WinUI controls and input/focus handling, not filesystem policy.
- `FolderBrowserViewModel` owns each pane's history, current address, single selected
  folder, edit/busy/error state. A late validation result cannot override a newer
  selection or navigation. Failed navigation does not enter Back history.
- `IFolderBrowserService` exposes only `BrowseAsync` and `ValidateAsync`.
  `FolderBrowserService` uses background work for drive names, immediate child-folder
  enumeration, full-path normalization and directory/read-access checks. It has no
  create, rename, delete, attribute write or file-content API. It does not recurse.
  Expected missing/access-denied/IO errors become ordinary inline messages.
- `BackupSetupViewModel` owns Continue/review/name/prototype notice. Continue rechecks
  both paths. Editing a path, pending IO or an invalid selection disables Continue.
  An empty display name disables Create. A custom display name survives source changes.
- `Views/BackupSetupView` composes the two panes and review. `MainWindow` only connects
  the existing navigation and outer scroll fallback.

## Interaction contract

Each pane starts at **This PC**, with drive roots and no selection. Single-click
selects one folder; double-click or Enter on a selected row opens it. Opening a
directory selects that current directory; **Use this folder** reselects it after a
child row was selected. Back follows successful browsing history; Up reaches the
parent, and a drive/share root goes Up to This PC.

Click the address or press **Ctrl+L** within that pane to edit an absolute path.
Enter navigates; Escape leaves edit mode. Quoted paths copied from Explorer are
accepted. Relative paths are rejected with a friendly message. The standard Windows
path APIs permit UNC syntax; no drive-letter-only filter was introduced. Full path
text remains available through tooltips/accessibility data; the address and selection
summary use ellipsis, and review text wraps. Back/Up also support Alt+Left / Alt+Up.
No multi-select or file-manager operations are exposed.

The visible address is a current-path button, not a full clickable breadcrumb tree.
This is intentionally a folder selector rather than a replacement for Explorer.

## Repository preview and future real validation

`SetupPreview.RepositoryPath` displays `<chosen location>\MirrorlyRepo`. This is an
explicit **prototype preview helper**, not a second implementation of core policy.
Before real setup is connected, the **shared application/core service must return
the authoritative repository path and setup preflight result**.

Only an obvious same-path warning is shown. The prototype does not validate nested
paths, NTFS capability, reparse-point aliases, existing repository identity, locks,
source/repository relationships or write permission. A folder passing read-only
browsing checks is not declared safe or writable for backup. No test file is written.
Even invalid real-backup combinations cannot write anything through this prototype.

## Resources, responsive layout and accessibility

Visual Baseline v1 remains the reference. Setup uses the existing centralized
surface, brush, spacing, typography, radius and button resources; no floral decoration
is placed on the task page. The only new resource is `MirrorlyFolderListHeight`, a
208-DIP scroll viewport. Labels, errors and action regions retain automatic height.

`SetupPreview.SideBySideAt` (960 effective pixels of available page width) controls
side-by-side versus stacked panes. The shell keeps navigation compact until 1280 epx
of NavigationView width; below 640 epx it uses Minimal mode. There is no page-wide
horizontal scrolling. Each standard
ListView scrolls its folders and the shell scrolls the entire page, including actions.
Back/Up and lists have pane-specific accessible names; path inputs have visible
headers. Errors combine icon, text and native InfoBar styling, with polite live-region
metadata. Standard selection, focus visuals and High Contrast resources are retained.
Refresh restores keyboard focus to the initiating pane. No custom accessibility or
filesystem framework was added.

The approved thresholds are centralized presentation/layout policy, not permanent
product or repository format. Future versions may adjust them from user feedback:
Setup uses `SetupPreview.SideBySideAt`; shell navigation uses
`HomePolicy.ExpandedNavigationAt` / `CompactNavigationAt`. Views consume these policies
instead of repeating the thresholds. Mainstream windows prioritize comfortable dual
panes; high DPI and narrow windows reflow without squeezing FolderBrowserPane.

## Phase 1C closure

Product review approved the Setup UX and final responsive calibration. PASS applies
to the interactive read-only prototype only; real setup/core integration and all
O-01–O-09 production decisions remain outside this phase. The freeze task reran the
C# desktop harness (**26 passed, 0 failed**), Debug x64 build (**PASS**), whitespace
and GUI document-link checks (**PASS**), and Ruff check/format (**PASS**). The
recorded UI/DPI/High Contrast observations below are prior implementation evidence,
not new manual tests performed during the freeze. No full CLI audit is reopened.

## Final responsive calibration — 2026-09-20

This follow-up changes only two responsive constants, their boundary assertions and
this document. The original Setup breakpoint was **720 epx**. Original NavigationView
thresholds were Expanded at **1000**, Compact at **640**, Minimal below **640**.
The approved objective is comfortable dual panes for mainstream desktop/laptop
windows, with complete, scrollable stacked interaction when space is insufficient.
It does not require dual panes at every resolution/scaling combination.

### Actual WinUI width comparison

Measurements used the running packaged Debug app at the unchanged **175% Windows
scale**. Temporary title-bar measurements and resize controls were removed after
review. The 1008/960/920/880 nominal content-width presets produced these actual
`BackupSetupView.ActualWidth` values (native borders/layout rounding account for the
small difference). Normal typography, padding, folder rows and buttons were retained.
Both panes browsed real worktree folders, including long absolute paths.

| Nominal page width | Actual page / individual pane width, epx | Visual assessment |
| --- | --- | --- |
| 1008 | 1006.3 / 494.9 | Spacious; clear folder rows and address area |
| 960 | 958.3 / 470.9 | Lowest sampled range judged comfortably dual-pane |
| 920 | 918.3 / 450.9 | Usable, but less address context and less room for longer labels |
| 880 | 878.3 / 430.9 | Still functional; not the preferred dual-pane experience, more path truncation |

**Chosen breakpoint: 960 epx of available page width**, not monitor pixels or total
window width. At the boundary each pane is about 472 epx wide before padding/borders.
This is a visual judgment for the current normal-text layout, not a claim that every
path fits without ellipsis. Tooltips/editing still expose full paths. We did not lower
font size, padding, row height or control size to preserve two columns.

**Navigation Expanded threshold: 1280 epx; Compact threshold remains 640 epx.**
The 244-epx expanded navigation and 64-epx page margins leave about 971–972 epx at
the expansion boundary, so expanding navigation does not immediately force Setup
into stacked layout. Compact navigation releases about 196 epx in medium windows.
This is a shared shell policy, not Setup-specific navigation machinery. Home was
visually checked in Expanded and Compact modes; the Restore placeholder was checked
in Compact mode. Selection, content and primary actions remained clear. The existing
Minimal behavior, Home content breakpoints and all theme resources are unchanged.

### Expected resolution/scaling outcomes

These are estimates for a maximized or nearly full-width window with normal Windows
text size, using available effective width. Taskbar placement, window borders and a
manually reduced window can alter the result; a physical monitor mode is not a layout
input. Page width is capped at 1120 epx by the existing content resource.

| Display scenario | Approximate available page width, epx | Expected navigation / Setup |
| --- | --- | --- |
| 1366×768 @100% | 1043–1057 | Expanded / comfortable dual panes |
| 1366×768 @125% | 966–980 | Compact / comfortable dual panes near the lower end of the range |
| 1366×768 @150% | 784–798 | Compact / stacked |
| 1920×1080 @100% | 1120 content cap | Expanded / spacious dual panes |
| 1920×1080 @125% | 1120 content cap | Expanded / spacious dual panes |
| 1920×1080 @150% | ~971 Expanded, or 1120 cap Compact | Dual panes; navigation depends on actual client width at its boundary |
| 1920×1080 @200% | 833–847 | Compact / stacked |

The four mainstream targets retain comfortable dual panes without squeezing controls.
At 1366@125%, reducing the window width further may naturally stack the panes. At
1920@175%, Compact navigation is also expected to leave roughly 970–984 epx for dual
panes in a full-width window, but dual panes are not a high-scaling requirement.

Real-window checks at this session's 175% scale:

- 1366×768-epx outer window: **1042.9 epx page / 513.7 pane**, Expanded, dual panes.
- 1092.8×614.4-epx outer window (1366@125% equivalent): **965.7 / 474.9**, Compact,
  dual panes; scrolling fully exposes Continue. Long-path entry/list browsing worked.
- 960×540-epx outer window (1920@200% equivalent): **833.1 epx**, Compact, stacked;
  Source, Destination, Continue, Review, Back and Create were reachable by scrolling.

These are real WinUI effective-size checks, **not new physical-display-mode/DPI runs**.
The earlier actual 150/175/200% and High Contrast evidence below remains separate.
No display scale or contrast setting changed in this calibration. Increased Windows
Text Size and mixed-monitor behavior remain unverified.

Calibration checks: C# desktop harness **26 passed, 0 failed**, final Debug x64 build
**PASS**, `git diff --check` **PASS**. No CLI audit was rerun. No existing Python core,
worker, filesystem service, dependency, package metadata or new feature changed.
Temporary measurement controls/title text are absent from the final source/build.

Actual screenshots are ignored local artifacts in `desktop/artifacts/phase1c-breakpoints/`:
`01-page-1006.png`, `02-page-960.png`, `03-page-920.png`, `04-page-880.png`,
`05-home-compact.png`, `06-1366-at125-epx.png`, `06b-1366-at125-actions.png`,
`07-placeholder-compact.png`, `08-1366-at100-epx.png`, `09-home-expanded.png`,
`10-1920-at200-epx.png`, `10b-stacked-destination.png`, `10c-stacked-continue.png`,
`10d-stacked-review-actions.png`. The title-bar values in the four comparison captures
are authoritative; filenames reflect nominal presets.

## Verification performed in this implementation task

| Check | Result and limits |
| --- | --- |
| C# desktop harness | **26 passed, 0 failed** (17 existing + 9 Setup groups); includes real Phase 1A fake-worker IPC regression |
| Debug x64 build | **PASS**, .NET 10 / Windows App SDK 2.5.1; no new dependencies; final build emitted no warnings |
| Lightweight checks | `git diff --check` including separate checks of new files, GUI relative-file links, `ruff check .`, `ruff format --check .`: **PASS** (59 Python files already formatted) |
| Setup model/service tests | Drives, Back/Up, valid/invalid/inaccessible paths, both selections, Continue, disappearing destination, name, repository preview, late async result, long/UNC path data, responsive boundary |
| Real service test | Uses a unique temporary test-owned directory; enumerates/validates only; verifies fixture file unchanged and no repository created; fixture creation/cleanup belongs solely to the test harness |
| Actual Windows UI | Empty Home entry, real worktree folder browsing, row selection, double-click/Enter, Up/Alt+Left, Ctrl+L, path error and recovery, Review, prototype-only Create observed |
| Keyboard | Tab, Shift+Tab, Enter, Space, visible focus observed; fixed ListView Enter routing and focus leaving the initiating pane during async refresh |
| DPI 175% | Wide dual panes, manual input, inline error, Review and confirmation observed |
| DPI 200% | Actual Windows scale + app diagnostics confirmed; small 560×480-DIP window stacks panes; navigation, destination, Continue and Review actions reachable by scrolling |
| DPI 150% | Additional actual-scale small-window check; stacked source and Review/actions usable |
| High Contrast | Actual Windows Night sky theme at 175%; text, selection, focus, navigation and primary action usable; no task-page decoration |
| Settings restored | Original **175%** and contrast theme **None** restored |
| Core boundary | Existing `src/mirrorly`, CLI, Python tests and fake-worker files unchanged; selected destination's `MirrorlyRepo` remained absent |

No full CLI audit or standalone Python test suite was rerun. These are this task's
results, not substituted Phase 1A/1B results. Narrator/screen-reader certification,
increased Windows Text Size and mixed-monitor behavior were not independently tested
in Phase 1C. Access denial and disappearance are covered by fake-service tests;
actual disk unplugging/permission changes were not performed. Actual UNC/NAS browsing
is **UNVERIFIED**. The OS can take time to return from a network enumeration; background
work keeps the UI responsive, but this prototype has no IO timeout or cancellation
contract. No real backup safety guarantee is implied.

## Review screenshots and reproduction

Actual WinUI captures are local ignored artifacts in `desktop/artifacts/phase1c-review/`:

- `01-setup-wide-175.png`, `01b-setup-maximized-175.png`
- `02-manual-path-175.png`
- `03-invalid-path-175.png`
- `04-review-backup-175.png`, `04b-prototype-only-175.png`
- `05-setup-narrow-200.png`, `05b-narrow-destination-200.png`,
  `05c-narrow-continue-200.png`, `05d-narrow-review-200.png`
- `06-high-contrast-175.png`, `06b-high-contrast-focus-175.png`,
  `06c-high-contrast-continue-175.png`, `06d-high-contrast-navigation-175.png`
- `07-review-small-150.png`, `07b-setup-small-150.png`

Run the registered Debug package, select **Developer diagnostics → Empty**, then
use the ordinary Home **Set up backup** entry. Choose existing directories; none
will be changed. Check the two-step flow and use Create to see the explicit prototype
notice. The default Home still uses Phase 1B fixtures; no real backup registry exists.

Next work requires review. Real setup must be a separately scoped application-service
integration with authoritative preflight; it is not authorized by this prototype.
