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

This is a collection list, not the future per-Backup Overview, Snapshots or
Settings hierarchy. Restore, Activity and Settings remain separate later
work. Home shows the truthful empty Activity state without an active `View all`
link to the unfinished Activity page. Rebuilt Backup cards keep keyboard focus
with the same task across live state updates when that task remains visible.
No Python Backup transaction, worker protocol, task configuration, queue
semantics or persistence policy changed in this polish batch.
