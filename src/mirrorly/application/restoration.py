"""Restore planning/approval boundary and execution through the existing core."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..restore import RestorePlan, RestoreResult, apply_restore, plan_restore
from . import queries


@dataclass(frozen=True)
class RestoreSummary:
    create: int
    overwrite: int
    skip: int
    conflict: int
    dirs: int


@dataclass(frozen=True)
class RestorePlanResult:
    context: queries.TaskRepository
    plan: RestorePlan

    @property
    def summary(self) -> RestoreSummary:
        counts = {"create": 0, "overwrite": 0, "skip": 0, "conflict": 0}
        dirs = 0
        for entry in self.plan.entries:
            if entry.is_dir:
                dirs += 1
            else:
                counts[entry.action] += 1
        return RestoreSummary(**counts, dirs=dirs)


class OverwriteApprovalRequired(Exception):
    """The existing planned-overwrite decision has not been explicitly approved."""


class RestoreExecutionFailure(Exception):
    """Apply raised without returning a result; partial effects are not known.

    The original cause and prepared intent are available, but no invented counts,
    rollback, or proof of zero writes. BaseException is not caught.
    """

    def __init__(self, cause: Exception, prepared: RestorePlanResult):
        super().__init__(str(cause))
        self.cause = cause
        self.prepared = prepared


@dataclass(frozen=True)
class RestorationResult:
    result: RestoreResult

    @property
    def has_issues(self) -> bool:
        result = self.result
        return bool(result.skipped or result.conflicts or result.errors or result.leftovers)


def prepare_restore(
    context: queries.TaskRepository,
    destination: str | Path,
    *,
    snapshot: str | None = None,
    paths: tuple[str, ...] = (),
    overwrite: str = "never",
    in_place: bool = False,
) -> RestorePlanResult:
    """Read-only plan after fresh task/repo resolution; no terminal interaction.

    in_place is the caller's explicit intent. CLI separately requires --yes in
    its original position, after resolution and before snapshot selection.
    """
    snapshot_id = snapshot
    if snapshot_id is None:
        latest = queries.latest_complete(context.repo)
        if latest is None:
            raise queries.NoCompleteSnapshot
        snapshot_id = latest.snapshot_id
    plan = plan_restore(
        context.repo, snapshot_id, destination, paths=paths, overwrite=overwrite, in_place=in_place
    )
    return RestorePlanResult(context, plan)


def execute_restore(
    prepared: RestorePlanResult, *, overwrite_approved: bool = False
) -> RestorationResult:
    """Apply the exact approved intent through core revalidation, never replan it here.

    Core reloads/validates the manifest and digest, validates plan parameters and
    destination/reparse boundaries, replans with no-upgrade reconciliation, and
    rechecks immediately before IO and file commit. No new locks or guarantees
    against the existing residual check-to-replace race are introduced.
    The Python object is not an IPC authorization token or an untrusted plan schema.
    """
    if not overwrite_approved and any(
        not entry.is_dir and entry.action == "overwrite" for entry in prepared.plan.entries
    ):
        raise OverwriteApprovalRequired("Planned file overwrites require explicit approval")
    try:
        return RestorationResult(apply_restore(prepared.context.repo, prepared.plan))
    except Exception as exc:
        raise RestoreExecutionFailure(exc, prepared) from exc
