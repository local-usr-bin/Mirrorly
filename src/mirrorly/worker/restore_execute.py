"""One-shot production Restore execution of a worker-owned prepared plan."""

from __future__ import annotations

import json
from dataclasses import dataclass

from mirrorly.application import restoration

from .restore_prepare import WorkerPreparedRestore

MAX_TERMINAL_BYTES = 65536


@dataclass(frozen=True)
class RestoreExecuteIntent:
    plan_id: str
    overwrite_approved: bool


def request(params) -> RestoreExecuteIntent:
    if set(params) != {"plan_id", "overwrite_approved"}:
        raise ValueError("Expected only the prepared plan ID and explicit overwrite approval")
    plan_id = params["plan_id"]
    approval = params["overwrite_approved"]
    if (
        not isinstance(plan_id, str)
        or len(plan_id) != 32
        or any(c not in "0123456789abcdef" for c in plan_id)
        or type(approval) is not bool
    ):
        raise ValueError("Invalid Restore execution intent")
    return RestoreExecuteIntent(plan_id, approval)


def admissible(plan: WorkerPreparedRestore | None, intent: RestoreExecuteIntent) -> bool:
    if plan is None or plan.plan_id != intent.plan_id:
        return False
    # A plan ID is never overwrite approval. Skip cannot manufacture approval.
    return (plan.intent.policy == "replace_existing" and intent.overwrite_approved) or (
        plan.intent.policy == "skip_existing" and not intent.overwrite_approved
    )


def invoke(
    plan: WorkerPreparedRestore, intent: RestoreExecuteIntent
) -> restoration.RestorationResult:
    return restoration.execute_restore(plan.prepared, overwrite_approved=intent.overwrite_approved)


def project(outcome: restoration.RestorationResult) -> dict:
    result = outcome.result
    # Summary counts only; no unbounded file/path list crosses the worker boundary.
    facts = {
        "snapshot_id": result.snapshot_id,
        "destination": str(result.destination),
        "files_restored": len(result.restored),
        "directories_created": len(result.dirs_created),
        "items_skipped": len(result.skipped),
        "conflicts": len(result.conflicts),
        "errors": len(result.errors),
        "leftover_temporary_files": len(result.leftovers),
        "bytes_written": result.bytes_written,
    }
    if (
        not isinstance(result.snapshot_id, str)
        or not result.snapshot_id
        or not isinstance(facts["destination"], str)
        or any(type(v) is not int or not 0 <= v <= 2**63 - 1 for v in list(facts.values())[2:])
        or len(json.dumps(facts, ensure_ascii=False).encode("utf-8")) > MAX_TERMINAL_BYTES
    ):
        raise ValueError("Restore result exceeds bounded wire representation")
    return {
        "outcome": "completed_with_issues"
        if (result.conflicts or result.errors or result.leftovers)
        else "completed",
        "facts": facts,
    }
