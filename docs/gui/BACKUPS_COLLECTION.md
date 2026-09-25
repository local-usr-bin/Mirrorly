# Configured Backups collection — first GUI list

Home remains a small preview: it prioritizes Backups needing attention, then
uses the Python-provided latest saved-version creation time for presentation
recency. Missing or malformed times do not become invented dates; those tasks
retain a stable catalog-order fallback. This ordering does not select a Backup
baseline or change Python catalog discovery.

The top-level Backups destination now shows every configured GUI Backup from
the same Python-backed catalog as Home. It reuses the app-scoped GUI Backup
execution coordinator and existing summary controls. An omitted Home-preview
task can start a real Backup, join the in-memory FIFO, show Running/Queued,
and be removed while Queued. The shell owns vertical scrolling. Catalog
refresh and navigation do not own or reorder the queue.

This is a collection list, not the future per-Backup Overview, Snapshots or
Settings hierarchy. Restore, Activity and Settings remain separate later
work. No Python Backup transaction, worker protocol, task configuration,
queue semantics or persistence policy changed in this batch.
