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
