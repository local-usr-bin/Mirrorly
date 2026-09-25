# Configured Backups collection — first GUI list

Home remains a small preview: it prioritizes Backups needing attention, then
uses the Python-provided latest saved-version creation time for presentation
recency. Missing or malformed times do not become invented dates; those tasks
retain a stable catalog-order fallback. This ordering does not select a Backup
baseline or change Python catalog discovery.
Saved-version times shown on Home and Backups are converted from Python's
offset-bearing timestamp to the user's local time and normal Windows date/time
format. Missing or malformed values show `Time unavailable`; the stored fact
and ordering rules remain unchanged. Cards label the Python-derived path as
the Mirrorly repository, distinct from the Backup location selected in Setup.

The top-level Backups destination now shows every configured GUI Backup from
the same Python-backed catalog as Home. It reuses the app-scoped GUI Backup
execution coordinator and existing summary controls. An omitted Home-preview
task can start a real Backup, join the in-memory FIFO, show Running/Queued,
and be removed while Queued. The shell owns vertical scrolling. Catalog
refresh and navigation do not own or reorder the queue.
When automatic FIFO advancement is paused, both pages show the shared queue
attention and affected-operation details. A queued task remains marked Queued
and removable, without a promise that it will start automatically.

The collection offers `View backup` for selector-based, production read-only
Overview and Snapshots sections inside the existing shell. The Backups navigation
item remains selected; the Detail Back action returns to the complete collection
and restores focus
to that task's `View backup` control when it still exists, or the collection's
Set up backup action if it does not. The Overview reacquires task facts from the
shared Python catalog and latest-complete summary, and uses the same app-scoped
Backup coordinator for Running, Queued, removal, results and attention. It keeps
Source, the selected Backup location and the freshly resolved Mirrorly repository
distinct. A failed summary remains unavailable, not zero saved versions.

The Snapshots section lazily reads the first `snapshots.list` page on entry.
Rows retain repository manifest-filename order; the UI makes no newest-first
claim. Only a loaded complete row whose ID matches the production
`latest_complete_snapshot_id` is marked “Latest saved backup.” Incomplete rows
say “Incomplete · unfinished work” and are never labeled saved. Times use the
same local/locale formatter as Overview, with “Time unavailable” for missing or
malformed values. Counts and bytes describe logical content, not disk usage.
There are no row actions yet.

Each page contains at most 16 items. `Load more` explicitly requests the
returned cursor and appends in order; it does not infer a total count. A
continuation failure keeps already loaded rows and offers retry or Refresh.
Refresh discards the current pages/cursor and starts a new observation at the
first page. Page calls are fresh observations, not a repository transaction;
a stale cursor can fail. Genuine zero snapshots shows “No saved versions yet”;
unfinished-only data shows “No complete saved versions yet”; a first-page query
failure shows “Snapshots unavailable,” never a fabricated empty collection.
Technical errors remain behind “View technical details.” Backup-specific
Settings, Restore, Verify, selected-snapshot Explorer paths and reports remain
deferred, without placeholder destinations.

Top-level Restore now has its own selection and read-only Review flow; no
Snapshots-row Restore action or Restore execution exists yet. Activity and
global Settings remain later work. Home shows the truthful empty Activity state without an active `View all`
link to the unfinished Activity page. Rebuilt Backup cards keep keyboard focus
with the same task across live state updates when that task remains visible.
No Python Backup transaction, worker protocol, task configuration, queue
semantics or persistence policy changed in this polish batch.
