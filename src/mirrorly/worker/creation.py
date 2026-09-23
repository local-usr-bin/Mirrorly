"""Setup execution wire mapping; application.setup owns every business decision."""

from mirrorly.application import setup

from . import preflight


def request(params):
    if "copy_mode_approved" not in params or type(params["copy_mode_approved"]) is not bool:
        raise ValueError("copy_mode_approved must be an explicit boolean")
    intent = {key: value for key, value in params.items() if key != "copy_mode_approved"}
    return preflight.request(intent), params["copy_mode_approved"]


def repo_facts(repo):
    if repo is None:
        return None
    return {
        "repo_id": repo.repo_id,
        "format_version": repo.format_version,
        "hash_algorithm": repo.hash_algorithm,
        "filesystem_policy": repo.filesystem_policy,
        "hardlinks": repo.hardlinks,
        "volume": preflight._volume(repo.volume),
    }


def effects(repository_path, config_path, initialized, written, repo=None):
    return {
        "repository_path": str(repository_path),
        "config_path": str(config_path),
        "repository_initialized": initialized,
        "config_written": written,
        "repo": repo_facts(repo),
    }


def invoke(service, intent, approved):
    # Capture the application's outcome separately from projection. A projection
    # exception must never be mistaken for application failure or rolled-back IO.
    try:
        result = service(intent, copy_mode_approved=approved)
    except setup.CopyModeApprovalRequired as exc:
        return "decision_required", exc
    except setup.SetupFailure as exc:
        return "failed", exc
    except Exception as exc:
        return "unexpected_failure", exc
    return "succeeded", result


def acknowledged_effects(outcome, value):
    """Small fallback when details cannot be projected; preserve acknowledged IO."""
    if outcome == "succeeded":
        initialized, written = True, True
    elif outcome == "failed":
        initialized, written = value.repository_initialized, value.config_written
    elif outcome == "decision_required":
        initialized, written = False, False
    else:
        initialized, written = None, None
    return {"repository_initialized": initialized, "config_written": written}


def project(intent, outcome, value):
    if outcome == "succeeded":
        facts = effects(value.repo.path, value.config_path, True, True, value.repo)
        facts.update(task_name=value.task.name, source=value.task.source)
        return {"outcome": outcome, "setup": facts}, None
    if outcome == "failed":
        facts = effects(
            value.repository_path,
            value.config_path,
            value.repository_initialized,
            value.config_written,
            value.repo,
        )
        code, stage, cause = "setup_failure", value.stage, value.cause
    else:
        # ApprovalRequired is explicitly guaranteed pre-write by the application.
        # An unstructured exception has no such guarantee, even if artifacts exist.
        known = False if outcome == "decision_required" else None
        facts = effects(intent.repository_path, intent.config_path, known, known)
        code = "copy_mode_approval_required" if known is False else "setup_unexpected_failure"
        stage, cause = ("volume" if known is False else None), value
    error = {
        "kind": "application",
        "code": code,
        "stage": stage,
        "technical": preflight.technical(cause),
    }
    if outcome == "decision_required":
        error["volume"] = preflight._volume(value.volume)
    return {
        "outcome": "decision_required" if outcome == "decision_required" else "failed",
        "setup": facts,
    }, error
