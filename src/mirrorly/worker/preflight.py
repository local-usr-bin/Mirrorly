"""Explicit wire projection of setup facts, not a second setup implementation."""

from pathlib import Path

from mirrorly.application import setup


def technical(error):
    text = str(error)
    return {
        "exception_type": type(error).__name__,
        "message": text[:4096],
        "message_truncated": len(text) > 4096,
        "errno": getattr(error, "errno", None),
        "winerror": getattr(error, "winerror", None),
    }


def request(params):
    fields = {"task_name", "source", "target", "config_root", "filesystem_policy"}
    if set(params) != fields or any(not isinstance(params[k], str) for k in fields):
        raise ValueError("Expected only setup business intent string fields")
    for key in ("source", "target", "config_root"):
        if not Path(params[key]).is_absolute() or "\x00" in params[key]:
            raise ValueError(f"{key} must be an explicit absolute path")
    if params["filesystem_policy"] not in ("strict", "warn"):
        raise ValueError("Unsupported filesystem_policy")
    return setup.SetupRequest(**params)


def _volume(value):
    return (
        None
        if value is None
        else {
            "label": value.label,
            "serial": value.serial,
            "filesystem": value.filesystem,
            "guid": value.guid,
        }
    )


def project(value):
    problem = value.problem
    return {
        "repository_path": str(value.repository_path),
        "config_path": str(value.config_path),
        "inputs_valid": value.inputs_valid,
        "volume": _volume(value.volume),
        "mount_root": value.mount_root,
        "repository_volume": _volume(value.repository_volume),
        "copy_mode_approval_required": value.copy_mode_approval_required,
        "problem": None
        if problem is None
        else {
            "kind": "application",
            "code": "setup_preflight_problem",
            "stage": problem.stage,
            "repository_initialized": problem.repository_initialized,
            "config_written": problem.config_written,
            "technical": technical(problem.cause),
        },
    }
