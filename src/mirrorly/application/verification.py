"""Synchronous verification plus mandatory report publication; not a read-only query."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from ..manifest import STATUS_COMPLETE
from ..verify import VerifyReport, verify_snapshot
from . import queries, reports


@dataclass(frozen=True)
class VerificationFacts:
    context: queries.TaskRepository
    targets: tuple[str, ...] = ()
    reports: tuple[VerifyReport, ...] = ()
    verification_completed: bool = False
    report_path: Path | None = None

    @property
    def ok(self) -> bool | None:
        # No aggregate integrity conclusion when target verification is unfinished.
        return all(report.ok for report in self.reports) if self.verification_completed else None


VerificationStage = Literal["selection", "verification", "report_build", "presentation", "report"]


class VerificationFailure(Exception):
    """Original cause and returned verification facts; never a rollback claim.

    report_path=None means publication did not return successfully. It does not
    prove that no report file was written before an exception.
    """

    def __init__(self, stage: VerificationStage, cause: Exception, facts: VerificationFacts):
        super().__init__(str(cause))
        self.stage = stage
        self.cause = cause
        self.facts = facts


@dataclass(frozen=True)
class VerificationResult:
    facts: VerificationFacts
    report: dict  # Existing persisted payload, not a new frontend schema.


def verify_snapshots(
    context: queries.TaskRepository,
    *,
    snapshot: str | None = None,
    all_snapshots: bool = False,
    quick: bool = False,
    on_verified: Callable[[VerifyReport], None] | None = None,
) -> VerificationResult:
    """Verify selected targets, present existing completed results, then publish.

    Resolve context first with queries.resolve_task_repository. All target calls
    finish before any on_verified notice, preserving CLI's previous list-comprehension
    boundary. This optional presentation hook is not progress or an IPC stream.
    No locks/migration/repair/history are added. Interrupts propagate unchanged.
    """
    facts = VerificationFacts(context)
    stage: VerificationStage = "selection"
    try:
        if all_snapshots:
            targets = tuple(
                s.snapshot_id
                for s in queries.list_snapshots(context)
                if s.status == STATUS_COMPLETE
            )
            if not targets:
                raise queries.NoCompleteSnapshot
        elif snapshot:
            targets = (snapshot,)
        else:
            latest = queries.latest_complete(context.repo)
            if latest is None:
                raise queries.NoCompleteSnapshot
            targets = (latest.snapshot_id,)
        facts = replace(facts, targets=targets)
        stage = "verification"
        for sid in targets:
            rep = verify_snapshot(context.repo, sid, quick=quick)
            facts = replace(facts, reports=(*facts.reports, rep))
        facts = replace(facts, verification_completed=True)

        failed = False
        report_entries = []
        for rep in facts.reports:
            stage = "report_build"
            failed = failed or not rep.ok
            report_entries.append(
                {
                    "snapshot_id": rep.snapshot_id,
                    "quick": rep.quick,
                    "ok": rep.ok,
                    "checked_files": rep.checked_files,
                    "checked_dirs": rep.checked_dirs,
                    "hashed_files": rep.hashed_files,
                    "unhashed_entries": rep.unhashed_entries,
                    "issues": [
                        {"path": i.path, "kind": i.kind, "detail": i.detail} for i in rep.issues
                    ],
                    "extras": list(rep.extras),
                }
            )
            if on_verified is not None:
                stage = "presentation"
                on_verified(rep)
        stage = "report_build"
        report = {
            "command": "verify",
            "quick": bool(quick),
            "snapshots": report_entries,
            "ok": not failed,
        }
        name = f"verify-{targets[0]}" if len(targets) == 1 else "verify-all"
        stage = "report"
        report_path = reports.write_report(context.repo, name, report)
        facts = replace(facts, report_path=report_path)
        return VerificationResult(facts, report)
    except Exception as exc:
        raise VerificationFailure(stage, exc, facts) from exc
