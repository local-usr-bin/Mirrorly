"""Bounded read-only projection of application-owned snapshot summaries."""

from __future__ import annotations

import json
from pathlib import Path

from mirrorly.application.queries import list_snapshot_page

DEFAULT_LIMIT = 16
MAX_LIMIT = 16
MAX_CURSOR_CHARS = 1024
MAX_ITEM_BYTES = 16384


def request(params):
    if set(params) not in (
        {"config_root", "task"},
        {"config_root", "task", "after"},
        {"config_root", "task", "limit"},
        {"config_root", "task", "after", "limit"},
    ):
        raise ValueError("Only config_root, task, after and limit are supported")
    root, task = params["config_root"], params["task"]
    after, limit = params.get("after"), params.get("limit", DEFAULT_LIMIT)
    if not isinstance(root, str) or not Path(root).is_absolute() or "\x00" in root:
        raise ValueError("Explicit absolute config root required")
    if not isinstance(task, str) or not task or "\x00" in task:
        raise ValueError("Task selector required")
    if after is not None and (
        not isinstance(after, str) or not after or len(after) > MAX_CURSOR_CHARS or "\x00" in after
    ):
        raise ValueError("Invalid snapshot cursor")
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError("Snapshot page limit must be 1..16")
    return root, task, after, limit


def execute(intent):
    root, task, after, limit = intent
    page = list_snapshot_page(root, task, after=after, limit=limit)
    if len(page.entries) > limit:
        raise ValueError("Snapshot page exceeded requested limit")
    if (
        page.latest_complete_snapshot_id is not None
        and len(page.latest_complete_snapshot_id) > MAX_CURSOR_CHARS
    ):
        raise ValueError("Latest snapshot ID exceeds bounded wire representation")
    entries = []
    for summary in page.entries:
        value = {
            "snapshot_id": summary.snapshot_id,
            "status": summary.status,
            "created_at": summary.created_at,
            "lifecycle_seq": summary.lifecycle_seq,
            "file_count": summary.stats.files,
            "directory_count": summary.stats.dirs,
            "logical_bytes": summary.stats.total_bytes,
            "resumed_from_snapshot_id": summary.resumed_from_snapshot_id,
            "format_version": summary.format_version,
        }
        # Per-item representability is independent of the overall 1 MiB frame.
        if (
            not isinstance(summary.snapshot_id, str)
            or not 0 < len(summary.snapshot_id) <= MAX_CURSOR_CHARS
            or summary.status not in ("complete", "incomplete")
            or any(
                type(number) is not int or not 0 <= number <= 2**63 - 1
                for number in (summary.stats.files, summary.stats.dirs, summary.stats.total_bytes)
            )
            or len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_ITEM_BYTES
        ):
            raise ValueError("Snapshot summary exceeds bounded wire representation")
        entries.append(value)
    return {
        "selector": task,
        "items": entries,
        "next_after": page.next_after,
        "latest_complete_snapshot_id": page.latest_complete_snapshot_id,
    }
