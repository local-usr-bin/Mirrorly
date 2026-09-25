"""Read-only task/repository resolution and existing snapshot query semantics."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import TaskConfig
from ..manifest import ManifestSummary, list_manifests, select_default_complete
from ..repo import RepoInfo
from . import repositories, tasks


@dataclass(frozen=True)
class TaskRepository:
    """Resolved facts for one operation, not a persistent cache or authorization token."""

    task: TaskConfig
    repo: RepoInfo
    relocated: bool


class NoCompleteSnapshot(Exception):
    """No default/all complete target; the frontend supplies operation-specific wording."""


@dataclass(frozen=True)
class SavedBackupSummary:
    """Current repository observation, not source freshness or prior finalization."""

    context: TaskRepository
    latest: ManifestSummary | None
    snapshot_path: Path | None


@dataclass(frozen=True)
class SnapshotPage:
    """One filename-ordered observation; pages are not a repository transaction."""

    entries: tuple[ManifestSummary, ...]
    next_after: str | None
    latest_complete_snapshot_id: str | None


class SnapshotCursorUnavailable(ValueError):
    """The cursor no longer identifies exactly one snapshot in this observation."""


def resolve_task_repository(config_root: str | Path, task: str | None = None) -> TaskRepository:
    """Keep the original task -> repository order and caller-relative paths.

    A separate resolution step lets CLI present relocation before later errors,
    including its --in-place/--yes usage check. No frontend callback is needed.
    Resolve afresh for each operation; this object does not pin a volume.
    """
    cfg = tasks.resolve_task_config(config_root, task)
    resolution = repositories.resolve_repo(cfg)
    return TaskRepository(cfg, resolution.repo, resolution.relocated)


def list_snapshots(context: TaskRepository) -> tuple[ManifestSummary, ...]:
    """All summaries in core's manifest-filename order, not lifecycle-latest order."""
    return tuple(list_manifests(context.repo))


def latest_complete(repo: RepoInfo) -> ManifestSummary | None:
    """Existing default selection; multiple legacy completes remain ambiguous."""
    return select_default_complete(list_manifests(repo))


def list_snapshot_page(
    config_root: str | Path, task: str, *, after: str | None = None, limit: int = 16
) -> SnapshotPage:
    """Page existing summaries without redefining core ordering or latest selection.

    Each call resolves and reads afresh. If the repository changes between calls,
    the cursor must still exist; no cross-page snapshot consistency is promised.
    """
    if type(limit) is not int or not 1 <= limit <= 16:
        raise ValueError("Snapshot page limit must be 1..16")
    if after is not None and (not isinstance(after, str) or not after):
        raise ValueError("Snapshot cursor must be a nonempty snapshot ID")
    context = resolve_task_repository(config_root, task)
    if not (context.repo.path / "manifests").is_dir():
        raise OSError("Repository manifest directory is unavailable")
    summaries = list_snapshots(context)
    identifiers = [item.snapshot_id for item in summaries]
    if len(set(identifiers)) != len(identifiers):
        raise SnapshotCursorUnavailable("Duplicate snapshot IDs make paging ambiguous")
    latest = select_default_complete(summaries)
    if after is not None:
        try:
            start = identifiers.index(after) + 1
        except ValueError as exc:
            raise SnapshotCursorUnavailable("Snapshot cursor is no longer available") from exc
    else:
        start = 0
    entries = summaries[start : start + limit]
    next_after = entries[-1].snapshot_id if start + limit < len(summaries) else None
    return SnapshotPage(entries, next_after, latest.snapshot_id if latest else None)


def saved_backup_summary(config_root: str | Path, task: str) -> SavedBackupSummary:
    """Resolve identity and select the authoritative complete snapshot afresh."""

    context = resolve_task_repository(config_root, task)
    latest = latest_complete(context.repo)
    candidate = context.repo.path / "snapshots" / latest.snapshot_id if latest else None
    return SavedBackupSummary(
        context,
        latest,
        candidate if candidate is not None and candidate.is_dir() else None,
    )
