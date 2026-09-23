"""Bounded projection of the Python-owned task catalog; never resolves a repository."""

import json
from pathlib import Path

from mirrorly.application.tasks import list_tasks
from mirrorly.repo import REPO_DIR_NAME

from . import preflight, protocol


def request(params):
    if set(params) not in ({"config_root"}, {"config_root", "after"}):
        raise ValueError("Only config_root and optional after are supported")
    root, after = params["config_root"], params.get("after")
    if not isinstance(root, str) or not Path(root).is_absolute() or "\x00" in root:
        raise ValueError("Absolute config root required")
    if after is not None and (not isinstance(after, str) or len(after) > 1024):
        raise ValueError("Invalid catalog cursor")
    return root, after


def execute(intent):
    page = list_tasks(intent[0], after=intent[1])
    entries = []
    for entry in page.entries:
        task = entry.task
        value = {
            "selector": entry.selector,
            "config_path": str(entry.config_path),
            "task": None
            if task is None
            else {
                "name": task.name,
                "source": task.source,
                "target": task.target_path,
                # Configured path only: no availability/relocation/latest claim.
                "configured_repository_path": str(Path(task.target_path) / REPO_DIR_NAME),
            },
            "problem": None if entry.problem is None else preflight.technical(entry.problem),
        }
        # A valid TOML can contain enormous strings. Never silently truncate task truth.
        # Limit each entry below 32 KiB so 16 entries fit the 1 MiB protocol frame.
        try:
            protocol.encode(protocol.message("event", "catalog", value))
            if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 32768:
                raise ValueError("Task entry exceeds catalog representation limit")
        except (ValueError, protocol.ProtocolFault) as exc:
            value["task"] = None
            value["problem"] = preflight.technical(exc)
        entries.append(value)
    return {"entries": entries, "next_after": page.next_after}
