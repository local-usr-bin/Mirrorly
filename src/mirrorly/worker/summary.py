"""Read-only, bounded wire projection of authoritative saved Backup facts."""

from pathlib import Path

from mirrorly.application.queries import saved_backup_summary


def request(params):
    if set(params) != {"config_root", "task"}:
        raise ValueError("Expected an explicit config root and task selector")
    root, task = params["config_root"], params["task"]
    if not isinstance(root, str) or not Path(root).is_absolute() or "\x00" in root:
        raise ValueError("Explicit absolute config root required")
    if not isinstance(task, str) or not task or "\x00" in task:
        raise ValueError("Task selector required")
    return root, task


def execute(intent):
    summary = saved_backup_summary(*intent)
    latest = summary.latest
    return {
        "selector": intent[1],
        "task_name": summary.context.task.name,
        "repository_path": str(summary.context.repo.path),
        "repository_id": summary.context.repo.repo_id,
        "relocated": summary.context.relocated,
        "latest_complete": None
        if latest is None
        else {
            "snapshot_id": latest.snapshot_id,
            "created_at": latest.created_at,
            "lifecycle_seq": latest.lifecycle_seq,
            "snapshot_path": str(summary.snapshot_path) if summary.snapshot_path else None,
        },
    }
