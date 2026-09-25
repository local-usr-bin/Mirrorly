"""Read-only Restore planning and a bounded Review projection.

The Python plan stays in the worker. The opaque ID identifies it but is not
approval to execute it; this module has no Restore execution path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from mirrorly.application import queries, repositories, restoration
from mirrorly.config import ConfigError, ConfigNotFoundError, validate_task_name
from mirrorly.manifest import ManifestError, ManifestIncompleteError, ManifestNotFoundError
from mirrorly.repo import RepoError, RepoFormatError
from mirrorly.restore import RestoreError, RestoreUnsafeDestinationError

MAX_PATH_BYTES = 8192
MAX_SELECTOR_BYTES = 1024
MAX_PREVIEW_BYTES = 65536
POLICIES = {"skip_existing": "never", "replace_existing": "always"}


@dataclass(frozen=True)
class RestorePrepareIntent:
    config_root: str
    task: str
    snapshot_id: str | None
    destination: str
    policy: str


@dataclass(frozen=True)
class WorkerPreparedRestore:
    plan_id: str
    intent: RestorePrepareIntent
    prepared: restoration.RestorePlanResult


def _bounded_string(value, maximum: int) -> bool:
    return isinstance(value, str) and "\x00" not in value and len(value.encode("utf-8")) <= maximum


def request(params) -> RestorePrepareIntent:
    if set(params) != {"config_root", "task", "snapshot_id", "destination", "policy"}:
        raise ValueError("Expected only Restore prepare intent")
    root, task = params["config_root"], params["task"]
    snapshot_id, destination, policy = (
        params["snapshot_id"],
        params["destination"],
        params["policy"],
    )
    if not _bounded_string(root, MAX_PATH_BYTES) or not Path(root).is_absolute():
        raise ValueError("Explicit absolute config root required")
    if not _bounded_string(task, MAX_SELECTOR_BYTES) or not task:
        raise ValueError("Durable task selector required")
    try:
        validate_task_name(task)
    except ConfigError as exc:
        raise ValueError("Invalid task selector") from exc
    if snapshot_id is not None and (
        not _bounded_string(snapshot_id, MAX_SELECTOR_BYTES) or not snapshot_id
    ):
        raise ValueError("Invalid snapshot ID")
    if not _bounded_string(destination, MAX_PATH_BYTES) or not Path(destination).is_absolute():
        raise ValueError("Explicit absolute Restore destination required")
    if not isinstance(policy, str) or policy not in POLICIES:
        raise ValueError("Only Skip existing and Replace existing are supported")
    return RestorePrepareIntent(root, task, snapshot_id, destination, policy)


def prepare(intent: RestorePrepareIntent) -> restoration.RestorePlanResult:
    context = queries.resolve_task_repository(intent.config_root, intent.task)
    return restoration.prepare_restore(
        context,
        intent.destination,
        snapshot=intent.snapshot_id,
        overwrite=POLICIES[intent.policy],
        in_place=False,
    )


def failure_code(error: Exception) -> str:
    """Stable error category; consumers never parse localized Python exception text."""
    if isinstance(error, ConfigNotFoundError):
        return "unknown_task"
    if isinstance(error, ConfigError):
        return "task_unreadable"
    if isinstance(error, repositories.IdentityMismatch):
        return "repository_unavailable"
    if isinstance(error, RepoFormatError):
        return "repository_invalid"
    if isinstance(error, RepoError):
        return "repository_unavailable"
    if isinstance(error, queries.NoCompleteSnapshot):
        return "no_complete_snapshot"
    if isinstance(error, ManifestNotFoundError):
        return "unknown_snapshot"
    if isinstance(error, ManifestIncompleteError):
        return "incomplete_snapshot"
    if isinstance(error, ManifestError):
        return "manifest_unavailable"
    if isinstance(error, (json.JSONDecodeError, UnicodeError)):
        return "manifest_unavailable"
    if isinstance(error, RestoreUnsafeDestinationError):
        return "unsafe_destination"
    if isinstance(error, RestoreError):
        return "restore_plan_failed"
    return "restore_prepare_unavailable"


def project(value: WorkerPreparedRestore) -> dict:
    plan, counts = value.prepared.plan, value.prepared.summary
    preview = {
        "plan_id": value.plan_id,
        "selector": value.intent.task,
        "snapshot_id": plan.snapshot_id,
        "destination": str(plan.destination),
        "policy": value.intent.policy,
        "file_create_count": counts.create,
        "file_overwrite_count": counts.overwrite,
        "file_skip_count": counts.skip,
        "file_conflict_count": counts.conflict,
        "directory_entry_count": counts.dirs,
    }
    if (
        len(value.plan_id) != 32
        or any(
            not _bounded_string(preview[key], limit) or not preview[key]
            for key, limit in (
                ("selector", MAX_SELECTOR_BYTES),
                ("snapshot_id", MAX_SELECTOR_BYTES),
                ("destination", MAX_PATH_BYTES),
            )
        )
        or any(
            type(preview[key]) is not int or not 0 <= preview[key] <= 2**63 - 1
            for key in (
                "file_create_count",
                "file_overwrite_count",
                "file_skip_count",
                "file_conflict_count",
                "directory_entry_count",
            )
        )
        or len(json.dumps(preview, ensure_ascii=False).encode("utf-8")) > MAX_PREVIEW_BYTES
    ):
        raise ValueError("Restore preview exceeds bounded wire representation")
    return preview
