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

The collection now offers `View backup` for a selector-based, production read-only
Overview inside the existing shell. The Backups navigation item remains selected;
the Overview's Back action returns to the complete collection and restores focus
to that task's `View backup` control when it still exists, or the collection's
Set up backup action if it does not. The Overview reacquires task facts from the
shared Python catalog and latest-complete summary, and uses the same app-scoped
Backup coordinator for Running, Queued, removal, results and attention. It keeps
Source, the selected Backup location and the freshly resolved Mirrorly repository
distinct. A failed summary remains unavailable, not zero saved versions. A
bounded production `snapshots.list` data query now exists, but the Snapshots GUI
and Backup-specific Settings remain deferred; neither has a placeholder
destination in Overview.

Restore, Activity and global Settings remain separate later work. Home shows the truthful empty Activity state without an active `View all`
link to the unfinished Activity page. Rebuilt Backup cards keep keyboard focus
with the same task across live state updates when that task remains visible.
No Python Backup transaction, worker protocol, task configuration, queue
semantics or persistence policy changed in this polish batch.
