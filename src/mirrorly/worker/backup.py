"""Wire boundary for the existing synchronous application Backup transaction."""

from __future__ import annotations

from pathlib import Path

from mirrorly.application import backup

from . import preflight


def request(params):
    if set(params) != {"config_root", "task", "dry_run", "full_hash", "exclude"}:
        raise ValueError("Expected only BackupRequest business intent")
    root, task, dry_run, full_hash, exclude = (
        params[key] for key in ("config_root", "task", "dry_run", "full_hash", "exclude")
    )
    if not isinstance(root, str) or not Path(root).is_absolute() or "\x00" in root:
        raise ValueError("Explicit absolute config root required")
    if task is not None and (not isinstance(task, str) or not task or "\x00" in task):
        raise ValueError("Invalid task selector")
    if type(dry_run) is not bool or type(full_hash) is not bool:
        raise ValueError("Backup flags must be explicit booleans")
    if (
        not isinstance(exclude, list)
        or len(exclude) > 256
        or any(not isinstance(item, str) or "\x00" in item for item in exclude)
    ):
        raise ValueError("Invalid exclusions")
    return backup.BackupRequest(root, task, dry_run, full_hash, tuple(exclude))


def facts(value):
    changes = value.changes
    materialization = value.materialization
    return {
        "commit_state": value.commit_state,
        "task_name": value.task.name if value.task else None,
        "repository_path": str(value.repo.path) if value.repo else None,
        "repository_id": value.repo.repo_id if value.repo else None,
        "relocated": value.relocated,
        "lifecycle_seq": value.lifecycle_seq,
        "snapshot_id": value.snapshot_id,
        "resumed_from": value.resumed_from,
        "changes": None
        if changes is None
        else {
            "added": len(changes.added),
            "modified": len(changes.modified),
            "deleted": len(changes.deleted),
            "suspected_modified": len(changes.suspected_modified),
        },
        "scan_skipped": len(value.scan_skipped),
        "materialization": None
        if materialization is None
        else {
            "linked": len(materialization.linked),
            "copied": len(materialization.copied),
            "skipped": len(materialization.skipped),
            "bytes_written": materialization.bytes_written,
        },
        "manifest_status": value.manifest.status if value.manifest else None,
        "retention_deleted": None
        if value.retention_deleted is None
        else len(value.retention_deleted),
        "report_path": str(value.report_path) if value.report_path else None,
    }


def invoke(service, intent, decide_resume, on_relocation, on_resume):
    try:
        return "succeeded", service(
            intent,
            decide_resume=decide_resume,
            on_relocation=on_relocation,
            on_resume=on_resume,
        )
    except backup.BackupFailure as exc:
        return "failed", exc
    except Exception as exc:
        # A wrapper failure has no application commit evidence.
        return "unexpected_failure", exc


def project(intent, outcome, value):
    if outcome == "succeeded":
        if isinstance(value, backup.DryRunResult):
            return {
                "outcome": "dry_run",
                "dry_run": True,
                "facts": facts(value.facts),
                "report": None,
            }, None
        return {
            "outcome": "completed_with_issues" if value.has_issues else "succeeded",
            "dry_run": False,
            "facts": facts(value.facts),
            "report": value.report,
        }, None
    if outcome == "failed":
        return {
            "outcome": "failed",
            "dry_run": intent.dry_run,
            "facts": facts(value.facts),
            "report": None,
        }, {
            "kind": "application",
            "code": "backup_failure",
            "stage": value.stage,
            "technical": preflight.technical(value.cause),
        }
    return {"outcome": "unreported", "dry_run": intent.dry_run, "facts": None}, {
        "kind": "worker",
        "code": "backup_unstructured_failure",
        "technical": preflight.technical(value),
    }


def acknowledged(outcome, value):
    """Keep the proven commit boundary even if detailed JSON cannot be encoded."""
    known = value.facts if outcome in ("failed", "succeeded") else None
    if known is None:
        return None
    return {
        "commit_state": known.commit_state,
        "snapshot_id": known.snapshot_id,
        "lifecycle_seq": known.lifecycle_seq,
        "resumed_from": known.resumed_from,
    }
