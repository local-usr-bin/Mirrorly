# Phase 1B — Visual Shell & Home Prototype

> 2026-09-20 · **PASS — visual review approved; Phase 1B frozen.**
> Visual Baseline v1 and the current independent SVG assets are approved as the first implementation baseline.
> Baseline: `de1ed8626e0da563f212530327c762a87009b4c7`, `codex/mirrorly-gui`.
> CURRENT FACT below describes a fixture-driven visual prototype, not delivered backup functionality.

## Scope and boundaries

WinUI NavigationView provides Home, Backups, Restore and Activity, with its built-in
bottom Settings entry. Only Home has product-like content. Other pages, including
the first-backup setup destination, explicitly describe their placeholder status.
Phase 1A worker diagnostics have moved to a **Debug-only navigation entry**; no
protocol, ping, test crash or fixture selector appears on ordinary Home or in the
Release navigation. Close-to-tray and fake-worker lifecycle services are retained.

Home shows status → backup summaries → at most three recent activities. Healthy
copy says **Last backup completed successfully**, without claiming that the source
is currently up to date. Status, saved-version facts, last-backup time and recent
activity agree across the fixtures. Errors with unknown causes say so. All pages
identify the design preview. Backup/Explorer/retry/details actions show a prototype
notice; no displayed path is opened, read or modified. View all navigates to a
placeholder. Setup also only navigates to a placeholder.

The existing Python core, CLI, fake worker and Python environment are unchanged.
No real backup/restore/verify, application-service extraction, queue scheduler,
cancellation, Activity persistence, config import or production worker was added.
The explicit interpreter + checkout-local worker script arrangement is unchanged.
**O-09 remains OPEN**: packaged MSIX output from Phase 1A does not solve final
Python distribution. No multi-executable/unpackaged choice is made here.

## Presentation and resources

- `Presentation/HomePresentation.cs`: display-only records, fixture factory and
  small `HomePolicy`; no IPC or filesystem behavior. `HomeViewModel` projects the
  selected fixture, bounded preview and prototype notices. `ShellViewModel` tracks
  navigation. The previous technical ViewModel is now `WorkerDiagnosticsViewModel`.
- `Views/HomeView`: layout and action forwarding. `Components/BackupSummary` owns
  full and compact variants, so future Backups views can reuse it. Paths use
  ellipsis, full-text tooltips and accessible names. Fixture destinations show the
  final `MirrorlyRepo` leaf to match the current repository layout; they are fictitious.
- `Themes/MirrorlyResources.xaml`: semantic accent/surface/navigation/flower/leaf/
  success/warning/error/text/border brushes, typography, spacing, padding and radii.
  Native accent-button and NavigationView resources are mapped centrally.
  One dictionary replaces the Phase 1A `PrototypeResources.xaml`; no new framework.
- `Components/SpringSprig`: noninteractive presenter for two original SVG assets
  in `Assets/Decorations` (replacing the first pass's XAML shapes). Each has two
  attached pink flowers, varied translucent leaves, fine veins and gradient shading.
  Asset bounds do not determine layout measurement. It is non-hit-testable,
  non-tab-stop, and Raw automation view. Working/queued/problem states suppress
  it; narrow headers and collapsed navigation suppress their placements. A theme
  visibility resource hides it in High Contrast. Full screen-reader exclusion is
  not certified by the current inspection tool.

Light uses fresh green navigation, almost-white content and restrained green
actions. Success has its own semantic green and a small check + text. The reference
v0 mockup's large green status circle is absent; v1 uses a restrained 28-DIP check badge. High Contrast uses
system colors. Dark has a conservative fallback dictionary but is neither offered
in Settings nor claimed as visually accepted. No Mica is required for readability.

## Fixtures and responsive behavior

Twelve fixtures: Healthy, DestinationUnavailable, Failed, CompletedWithIssues,
FinalizationProblem, Running, Queued, TwoBackups, ThreeBackups, ManyBackups, Empty,
LongPath. Running shows only indeterminate sample progress; queued is only a
presentation state. Neither executes work or claims a real scheduler/ETA.

One backup uses a full card. Two/three use compact rows. The twenty-backup fixture
shows four rows, attention first then recent, and View all backups. Empty state
offers Set up backup and explains ordinary browsable files. Activity preview has
three entries by policy; narrow content replaces the list with an Activity link.

Navigation thresholds are 1000/640 effective pixels; the status action moves below
text below 720 content pixels, and activity/decorative detail reduces below 480.
These are reviewable layout policy, not fixed window dimensions. Main content
scrolls. Minimal NavigationView reserves space outside the scroller for its menu
button so scrolling never moves content underneath that button. Initial/debug
window sizing respects the display work area; it does not impose page dimensions.

## Original Phase 1B verification (before fidelity pass)

| Check | Result and evidence boundary |
| --- | --- |
| Locked restore | PASS; existing lock/dependencies unchanged |
| Debug / Release build | PASS; VS MSBuild, .NET 10.0.401, Windows App SDK 2.5.1; same Phase 1A targets |
| Packaged Debug launch | PASS; current-user loose package registration and AppsFolder activation; actual product Home observed |
| C# tests | **17 passed, 0 failed**; existing eight protocol/client/worker tests plus nine presentation tests; includes actual fake Python child |
| Presentation tests | Healthy/warnings/failure/running/queued, one/two/three/many, empty, long paths, navigation, responsive thresholds, bounded activity and harmless action notices |
| `ruff check .` / `ruff format --check .` | PASS; formatting check reports 55 files unchanged |
| 175% Light | PASS; ordinary Home, compact three-backup layout, many preview, empty/setup, long paths, running, finalization problem and failed states observed |
| 150% | PASS; actual XAML scale confirmed; normal and about 545×443 effective-pixel small content, navigation opens, primary action and scrolled summary actions reachable |
| 200% | PASS; actual XAML scale confirmed; about 547×444 effective-pixel small content, navigation opens, primary action and scroll accessible; no important unrecoverable clipping |
| Small / long paths | PASS for observed fixtures; action stacks below status, path ellipsis prevents overflow, lower controls reachable by scrolling |
| High Contrast | PASS, basic usability; actual Windows “夜空” theme at 175%, readable text/buttons/navigation, visible keyboard focus, decoration hidden |
| Keyboard | PASS, basics; Tab/Shift+Tab produce visible focus, Enter activates View details, Space activates Try again, explicit prototype notices observed |
| Screen reader / UI Automation | Standard controls and accessible path names supplied; computer-use returned no accessibility tree for this window. Narrator/full semantic reading order remains unverified |
| Python / CLI regression | No full CLI audit or separate Python fake-worker pytest rerun; those files are unchanged. Do not relabel Phase 1A's 11 Python tests as this phase's run |
| Package artifact / tray / visible notification | Phase 1A's frozen results remain historical evidence. Phase 1B launches with package identity but does not repeat MSIX creation, tray menu or visible notification acceptance |

System scale was restored to the user's **175%**, and contrast theme to **无**.
No Windows Text Size enlargement, 100/125%, cross-monitor movement, full keyboard
traversal, Narrator or final Dark visual certification was performed this phase.

Actual UI review found and fixed: an unnecessary HighContrastChanged subscription
that failed at startup in this environment (replaced by ThemeResource behavior),
initial window placement outside the usable work area, the minimal navigation
button overlapping scrolled content, overly tall multi-backup cards, and retained
scroll offset when returning to Home. These are presentation changes only.

## Screenshots and repeatable review

Local, unedited window captures are saved under
`desktop/artifacts/phase1b-review/` (ignored build/review output, not shipped assets):

| Capture | Purpose |
| --- | --- |
| `home-175.png` | Default healthy Home / reference-color review |
| `three-backups-175.png`, `many-backups-175.png` | Compact density and attention-first preview |
| `empty-175.png`, `setup-placeholder-175.png` | First-open experience and harmless setup action |
| `long-path-175.png` | Path clipping within layout |
| `running-175.png`, `finalization-175.png`, `failed-175.png` | Distinct operational presentations |
| `home-150.png`, `small-home-150.png`, `small-scroll-150.png` | Real 150% layout and reachable lower actions |
| `small-home-200.png`, `scale-200-evidence.png`, `scale-175-restored.png` | Real 200% and restoration evidence |
| `high-contrast-175.png`, `keyboard-focus-175.png` | Real contrast theme and visible focus |

Follow [desktop README](../../desktop/README.md) to build/launch. In Debug, use
Developer diagnostics → fixture selector / review window-size buttons; ordinary
navigation and View all links exercise placeholders. No fake task creation is
needed. Compare color freshness, restrained flowers, primary-action prominence
and density against the user-supplied Visual Baseline v1. Changing system scale
or contrast theme for further review should remain temporary and restore originals.

## Cost of common visual changes

Counts below cover implementation files; matching screenshots/tests should be
updated when behavior or acceptance changes, not by copying the design across pages.

| Requested change | Files / edit location |
| --- | --- |
| Make primary green lighter | 1: `Themes/MirrorlyResources.xaml`, accent/hover and associated state colors; recheck contrast |
| Halve the flowers | 2 SVG assets: remove one attached blossom group from each composition |
| Remove every flower/leaf decoration | 1: `Presentation/HomePresentation.cs`, `HomePolicy.DecorationsEnabled = false` |
| Card radius 16 → 12 | 1: resources, `MirrorlyCornerRadiusLarge`; compact cards already use Medium = 12 |
| Recent activity 3 → 2 | 1: presentation policy, `RecentActivityLimit`; tests check the policy rather than a copied count |
| Always place Home action below status | 1: presentation policy, `StackStatus`; responsive rule tests then need their expected behavior updated |
| Change backup-summary layout | 1 for XAML-only changes: `Components/BackupSummary.xaml`; 2 if its responsive behavior also changes in code-behind |

The standard window title bar/package icon remain technical placeholders. Native
hyperlink/progress accent can still follow the Windows accent rather than brand
green. These are known visual differences, not reasons to expand into production
features or a theme framework. User visual review has approved this implementation.

## Visual fidelity pass — Baseline v1

**2026-09-20 · PASS — user visual review approved.** User-supplied
v1 supersedes v0. Navigation, information order, fixtures, density policy, empty
state, long-path behavior and responsive breakpoints are unchanged. No core or
worker behavior changed. Only artwork/presentation resources and their integration
were adjusted; O-09 remains OPEN.

- Original sidebar/header SVGs replace the geometric XAML flowers. Pink petals
  have overlapping tonal layers and stamens; leaves vary in size, bend, angle and
  green gradient, with fine veins. Thin curved stems attach all leaves/blossoms.
  The sidebar tip-side flower sits above/left of the main branch; the other sits
  below/right. No detached petals exist. This is vector illustration, not a raster
  painting or a claim of pixel-level equivalence to the reference.
- Light accent becomes `#328351`, surfaces `#F8FCF9` / `#F0F8F1`, navigation
  `#EDF7ED`, border `#DEE9E1`; all functional colors stay in the central resource
  dictionary. Backup/Activity keep quiet surfaces and no new heavy shadow. The
  status mark becomes a small check badge; no large saturated status panel.
- SVG paint gradients live in each asset's `defs`; these artwork colors are
  intentionally separate from functional semantic brushes. `SpringSprig` uses
  standard `Image` / `SvgImageSource`, a fixed resource-owned slot, and Canvas
  drawing with size updates. This avoids a collapsed footer retaining zero image
  size. `CopyToOutputDirectory=PreserveNewest` ensures SVGs accompany normal builds.
  No renderer, package dependency or theme framework was added.
- [MOTION](MOTION.md) records only the future one-shot real backup-start flourish,
  approximately 250–500 ms, never ongoing progress; disabled/reduced motion and
  High Contrast can omit it. No animation is implemented or triggered by fixtures.

### Fidelity-pass verification (new runs)

| Check | Result |
| --- | --- |
| Debug / Release build | PASS; same .NET 10 / Windows App SDK 2.5.1; SVGs present in build output |
| C# harness | **17 passed, 0 failed** after visual code changes; includes unchanged actual fake-worker integration tests |
| 175% | PASS; actual Healthy/1 Backup, 3 Backups and Empty observed and captured |
| 200% small window | PASS; actual XAML scale confirmed, primary action stacked and reachable, navigation opens, summary actions reached by scrolling |
| High Contrast | PASS, basic usability; real Windows “夜空” at restored 175%, both SVGs hidden, text/actions/navigation readable, visible Tab focus; prototype action still harmless |
| Original settings restored | PASS; 175% and contrast theme “无” restored after checks |
| 150% / full accessibility | Not rerun in this visual pass; earlier results above remain historical. No new Narrator or full keyboard certification |
| Python core / fake worker | Unchanged; no full Python/core audit rerun |

Current, unedited WinUI screenshots are local ignored artifacts under
`desktop/artifacts/phase1b-fidelity-v1/`:
`healthy-175.png`, `three-backups-175.png`, `empty-175.png`,
`small-home-200.png`, `small-scroll-200.png`, `high-contrast-175.png`.
Scale/theme evidence: `scale-200-evidence.png`, `scale-175-restored.png`,
`theme-restored.png`. The original screenshot set documents the earlier artwork,
not the current fidelity result. The current fidelity result has passed user review.

### Further adjustment cost

| Change | Implementation scope |
| --- | --- |
| Replace all plants | 2 SVGs; same names/viewBox proportions need no code edits; at most 1 resource file for different placement proportions |
| Remove all plants | 1 presentation file: `HomePolicy.DecorationsEnabled = false` |
| Move lower-left plant 20 DIP | 1 resource file: `MirrorlySidebarDecorationOffsetX` / `OffsetY`; render transform does not reflow navigation |
| Shrink upper-right plant 20% | 1 resource file: `MirrorlyHeaderDecorationScale = 0.8`; reserved layout slot stays stable |
| Adjust main green | 1 resource file: accent/hover and corresponding native-control semantic colors |
| Adjust card radius | 1 resource file: `MirrorlyCornerRadiusLarge` / `Medium` |

At 175%/200%, placement values are Windows effective pixels (DIP), not raw panel
pixels. SVG coordinates are artwork-local. The illustration remains lighter and
simpler than v1's watercolor-like rendering; its actual appearance is shown in the
captures. The user approved these assets as the first implementation baseline;
future visual improvements should primarily replace the independent SVGs rather
than indefinitely extending Phase 1B to imitate watercolor detail.
