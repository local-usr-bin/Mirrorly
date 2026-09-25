"""Production Backup IPC over disposable repositories, never the GUI user's tasks."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from mirrorly.application.backup import BackupFacts, BackupRequest, BackupResult
from mirrorly.manifest import list_manifests, load_manifest
from mirrorly.repo import load_repo
from mirrorly.worker import backup as backup_wire
from mirrorly.worker import protocol
from test_worker import intent as intent
from test_worker_creation import create, worker


def backup_intent(intent, *, dry_run=False):
    return {
        "config_root": intent["config_root"],
        "task": intent["task_name"],
        "dry_run": dry_run,
        "full_hash": False,
        "exclude": [],
    }


@pytest.mark.parametrize("with_issue", [False, True])
def test_large_persisted_backup_report_has_bounded_factual_terminal(tmp_path, with_issue):
    """A realistic report may exceed protocol collection limits without losing outcome."""
    report_path = tmp_path / "full-report.json"
    report = {
        "status": "complete",
        "duration_seconds": 12.5,
        "changes": {"added": [f"file-{index}" for index in range(10000)]},
        "copied": [f"file-{index}" for index in range(10000)],
        "skipped": [{"path": "one", "reason": "test"}] if with_issue else [],
        "resume_untrusted": [],
        "resume_uncertified": [],
    }
    result = BackupResult(
        BackupFacts(commit_state="published", snapshot_id="snapshot", report_path=report_path),
        report,
        12.5,
    )
    intent = BackupRequest(tmp_path, "task")
    payload, error = backup_wire.project(intent, "succeeded", result)
    terminal = protocol.parse(
        protocol.encode(
            protocol.message(
                "response",
                "session",
                {"phase": "terminal", "result": payload, "error": error},
                "1",
                "operation",
            )
        )
    )["payload"]
    assert terminal["error"] is None
    assert terminal["result"]["outcome"] == ("completed_with_issues" if with_issue else "succeeded")
    assert terminal["result"]["facts"]["commit_state"] == "published"
    assert terminal["result"]["facts"]["report_path"] == str(report_path)
    assert terminal["result"]["report"]["skipped_count"] == int(with_issue)
    assert "changes" not in terminal["result"]["report"]


def execute(peer, params, *, answer=None):
    rid = peer.send("backup.run", params)
    first = peer.receive()
    if first["payload"]["phase"] == "rejected":
        return first, []
    assert first["payload"]["phase"] == "accepted"
    notices = []
    while True:
        message = peer.receive()
        assert message["request_id"] == rid
        assert message["operation_id"] == first["operation_id"]
        if message["message_type"] == "event":
            notices.append(message)
        elif message["message_type"] == "interaction_request":
            assert message["interaction_id"]
            assert message["payload"]["kind"] == "backup.resume"
            if answer is not None:
                response = {
                    **message,
                    "message_type": "interaction_response",
                    "payload": {"kind": "backup.resume", "answer": answer},
                }
                from mirrorly.worker import protocol

                peer.raw(protocol.encode(response))
        else:
            assert message["message_type"] == "response"
            assert message["payload"]["phase"] == "terminal"
            return message, notices


def prepare(peer, intent):
    create(peer, intent)
    source = Path(intent["source"])
    (source / "alpha.txt").write_bytes(b"alpha")
    (source / "unchanged.txt").write_bytes(b"unchanged")
    return load_repo(intent["target"])


@pytest.mark.parametrize("invalid", ["relative_root", "client_snapshot_id", "string_flag"])
def test_backup_intent_rejects_untrusted_or_malformed_fields_before_application(
    tmp_path, intent, invalid
):
    with worker(tmp_path) as peer:
        params = backup_intent(intent)
        if invalid == "relative_root":
            params["config_root"] = "relative/config"
        elif invalid == "client_snapshot_id":
            params["snapshot_id"] = "client-picked"
        else:
            params["dry_run"] = "false"
        reply, _ = execute(peer, params)
        assert reply["payload"]["phase"] == "rejected"
        assert reply["payload"]["error"]["code"] == "invalid_parameters"
        assert reply["payload"]["error"]["application_invoked"] is False
        assert not (tmp_path / "backup_calls").exists()


def test_real_first_and_incremental_backup_over_production_stdio(tmp_path, intent):
    with worker(tmp_path) as peer:
        repo = prepare(peer, intent)
        before = sorted(repo.path.rglob("*"))
        preview, _ = execute(peer, backup_intent(intent, dry_run=True))
        assert preview["payload"]["result"]["outcome"] == "dry_run"
        assert preview["payload"]["result"]["facts"]["commit_state"] == "not_published"
        assert sorted(repo.path.rglob("*")) == before
        first, _ = execute(peer, backup_intent(intent))
        facts = first["payload"]["result"]["facts"]
        assert first["payload"]["result"]["outcome"] == "succeeded"
        assert facts["commit_state"] == "published" and facts["lifecycle_seq"] == 0
        assert facts["materialization"]["copied"] == 2
        first_id = facts["snapshot_id"]
        peer.send(
            "backup.summary", {"config_root": intent["config_root"], "task": intent["task_name"]}
        )
        assert peer.receive()["payload"]["phase"] == "accepted"
        saved = peer.receive()["payload"]["result"]["summary"]["latest_complete"]
        assert saved["snapshot_id"] == first_id
        assert saved["snapshot_path"] == str(repo.path / "snapshots" / first_id)
        assert (repo.path / "snapshots" / first_id / "alpha.txt").read_bytes() == b"alpha"
        assert Path(facts["report_path"]).is_file()
        report = json.loads(Path(facts["report_path"]).read_text(encoding="utf-8"))
        assert report["snapshot_id"] == first_id
        first_manifest = (repo.path / "manifests" / f"{first_id}.json").read_bytes()
        source = Path(intent["source"])
        (source / "alpha.txt").write_bytes(b"changed")
        (source / "new.txt").write_bytes(b"new")
        second, _ = execute(peer, backup_intent(intent))
        current = second["payload"]["result"]["facts"]
        assert current["commit_state"] == "published" and current["lifecycle_seq"] == 1
        assert current["snapshot_id"] != first_id
        peer.send(
            "backup.summary", {"config_root": intent["config_root"], "task": intent["task_name"]}
        )
        assert peer.receive()["payload"]["phase"] == "accepted"
        latest = peer.receive()["payload"]["result"]["summary"]["latest_complete"]
        assert latest["snapshot_id"] == current["snapshot_id"]
        assert current["changes"]["added"] == 1 and current["changes"]["modified"] == 1
        assert current["materialization"]["linked"] == 1
        assert (repo.path / "snapshots" / current["snapshot_id"] / "new.txt").read_bytes() == b"new"
        assert (repo.path / "manifests" / f"{first_id}.json").read_bytes() == first_manifest
        assert [item.status for item in list_manifests(repo)] == ["complete", "complete"]
        assert load_manifest(repo, current["snapshot_id"], require_complete=True).lifecycle_seq == 1
        assert (source / "unchanged.txt").read_bytes() == b"unchanged"
        if repo.hardlinks:
            assert (
                os.stat(repo.path / "snapshots" / first_id / "unchanged.txt").st_ino
                == os.stat(
                    repo.path / "snapshots" / current["snapshot_id"] / "unchanged.txt"
                ).st_ino
            )


@pytest.mark.parametrize(
    "scenario,stage,state",
    [
        ("backup_publication_unknown", "complete_publication", "unknown"),
        ("backup_retention_failure", "retention_plan", "published"),
        ("backup_report_failure", "report", "published"),
        ("backup_materialization_failure", "materialization", "not_published"),
    ],
)
def test_commit_state_failure_boundaries(tmp_path, intent, scenario, stage, state):
    with worker(tmp_path, scenario) as peer:
        repo = prepare(peer, intent)
        reply, _ = execute(peer, backup_intent(intent))
        error, result = reply["payload"]["error"], reply["payload"]["result"]
        assert error["code"] == "backup_failure" and error["stage"] == stage
        assert result["outcome"] == "failed" and result["facts"]["commit_state"] == state
        assert result["facts"]["snapshot_id"]
        # Even if the injected publisher wrote to disk, the wire must preserve unknown.
        if state == "published":
            assert load_manifest(repo, result["facts"]["snapshot_id"], require_complete=True)
        assert not (repo.path / "locks" / f"{intent['task_name']}.lock").exists()
        assert not (repo.path / "locks" / "repo-writer" / "active.lock").exists()


def test_resume_and_decline_remain_explicit_inside_backup(tmp_path, intent):
    with worker(tmp_path, "backup_resume_roundtrip") as peer:
        repo = prepare(peer, intent)
        first, _ = execute(peer, backup_intent(intent))
        assert first["payload"]["result"]["facts"]["commit_state"] == "not_published"
        incomplete = first["payload"]["result"]["facts"]["snapshot_id"]
        second, notices = execute(peer, backup_intent(intent), answer="resume")
        facts = second["payload"]["result"]["facts"]
        assert facts["commit_state"] == "published" and facts["resumed_from"] == incomplete
        assert all(n["payload"]["kind"] == "backup.resume_notice" for n in notices)
        assert load_manifest(repo, facts["snapshot_id"], require_complete=True)
    with worker(tmp_path / "decline", "backup_resume_roundtrip") as peer:
        changed = dict(intent)
        decline = tmp_path / "decline"
        decline.mkdir(exist_ok=True)
        changed["source"] = str(decline / "source")
        changed["target"] = str(decline / "target")
        changed["config_root"] = str(decline / "config")
        Path(changed["source"]).mkdir()
        Path(changed["target"]).mkdir()
        prepare(peer, changed)
        execute(peer, backup_intent(changed))
        second, _ = execute(peer, backup_intent(changed), answer="decline_resume")
        facts = second["payload"]["result"]["facts"]
        assert facts["commit_state"] == "published" and facts["resumed_from"] is None


def test_resume_timeout_does_not_decline_or_publish(tmp_path, intent):
    with worker(tmp_path, "backup_resume_timeout") as peer:
        repo = prepare(peer, intent)
        execute(peer, backup_intent(intent))
        result, _ = execute(peer, backup_intent(intent))
        payload = result["payload"]
        assert payload["error"]["stage"] == "baseline"
        assert payload["result"]["facts"]["commit_state"] == "not_published"
        assert [item.status for item in list_manifests(repo)] == ["incomplete"]


@pytest.mark.parametrize("invalid", ["wrong_id", "bad_answer", "unavailable"])
def test_invalid_or_unavailable_resume_response_fails_closed(tmp_path, intent, invalid):
    with worker(tmp_path, "backup_resume_roundtrip") as peer:
        repo = prepare(peer, intent)
        execute(peer, backup_intent(intent))
        rid = peer.send("backup.run", backup_intent(intent))
        assert peer.receive()["payload"]["phase"] == "accepted"
        ask = peer.receive()
        assert ask["message_type"] == "interaction_request"
        response = dict(
            ask,
            message_type="interaction_response",
            payload={"kind": "backup.resume", "answer": "unavailable"},
        )
        if invalid == "wrong_id":
            response["interaction_id"] = "incorrect"
        elif invalid == "bad_answer":
            response["payload"] = {"kind": "backup.resume", "answer": "accept"}
        peer.raw(protocol.encode(response))
        terminal = peer.receive()
        assert terminal["request_id"] == rid
        assert terminal["payload"]["error"]["stage"] == "baseline"
        assert terminal["payload"]["result"]["facts"]["commit_state"] == "not_published"
        assert [item.status for item in list_manifests(repo)] == ["incomplete"]


def test_duplicate_valid_resume_answer_cannot_change_consumed_choice(tmp_path, intent):
    with worker(tmp_path, "backup_resume_roundtrip") as peer:
        prepare(peer, intent)
        execute(peer, backup_intent(intent))
        peer.send("backup.run", backup_intent(intent))
        assert peer.receive()["payload"]["phase"] == "accepted"
        ask = peer.receive()
        accepted = dict(
            ask,
            message_type="interaction_response",
            payload={"kind": "backup.resume", "answer": "decline_resume"},
        )
        peer.raw(protocol.encode(accepted))
        duplicate = dict(accepted, payload={"kind": "backup.resume", "answer": "resume"})
        peer.raw(protocol.encode(duplicate))
        terminal = peer.receive()
        assert terminal["payload"]["result"]["facts"]["commit_state"] == "published"
        assert terminal["payload"]["result"]["facts"]["resumed_from"] is None


def test_channel_eof_while_resume_pending_releases_existing_locks(tmp_path, intent):
    with worker(tmp_path, "backup_resume_roundtrip") as peer:
        repo = prepare(peer, intent)
        execute(peer, backup_intent(intent))
        peer.send("backup.run", backup_intent(intent))
        assert peer.receive()["payload"]["phase"] == "accepted"
        assert peer.receive()["message_type"] == "interaction_request"
        peer.process.stdin.close()
        peer.process.wait(timeout=5)
        assert not (repo.path / "locks" / f"{intent['task_name']}.lock").exists()
        assert not (repo.path / "locks" / "repo-writer" / "active.lock").exists()
        assert [item.status for item in list_manifests(repo)] == ["incomplete"]


def test_duplicate_backup_id_never_replays_even_after_terminal_cache_eviction(tmp_path, intent):
    with worker(tmp_path) as peer:
        repo = prepare(peer, intent)
        rid = peer.send("backup.run", backup_intent(intent))
        assert peer.receive()["payload"]["phase"] == "accepted"
        assert peer.receive()["payload"]["result"]["facts"]["commit_state"] == "published"
        for _ in range(34):
            peer.send("tasks.list", {"config_root": intent["config_root"]})
            assert peer.receive()["payload"]["phase"] == "accepted"
            assert peer.receive()["payload"]["phase"] == "terminal"
        peer.send("backup.run", backup_intent(intent), rid=rid)
        stale = peer.receive()["payload"]
        assert stale["phase"] == "rejected" and stale["error"]["application_invoked"] is False
        assert stale["error"]["code"] == "stale_request_id"
        assert len(list_manifests(repo)) == 1


@pytest.mark.parametrize("scenario", ["backup_notice_drop", "backup_notice_saturated"])
def test_noncritical_notice_delivery_failure_does_not_change_backup(tmp_path, intent, scenario):
    with worker(tmp_path, scenario) as peer:
        prepare(peer, intent)
        first, _ = execute(peer, backup_intent(intent))
        assert first["payload"]["result"]["facts"]["commit_state"] == "not_published"
        resumed, notices = execute(peer, backup_intent(intent), answer="resume")
        assert resumed["payload"]["result"]["facts"]["commit_state"] == "published"
        assert notices == []


def test_success_with_issues_is_not_ordinary_success(tmp_path, intent):
    with worker(tmp_path, "backup_with_issues") as peer:
        prepare(peer, intent)
        result, _ = execute(peer, backup_intent(intent))
        facts = result["payload"]["result"]["facts"]
        assert result["payload"]["result"]["outcome"] == "completed_with_issues"
        assert facts["commit_state"] == "published" and facts["scan_skipped"] == 1


def test_resumed_cleanup_failure_keeps_published_snapshot(tmp_path, intent):
    with worker(tmp_path, "backup_resume_cleanup_failure") as peer:
        repo = prepare(peer, intent)
        first, _ = execute(peer, backup_intent(intent))
        incomplete = first["payload"]["result"]["facts"]["snapshot_id"]
        result, _ = execute(peer, backup_intent(intent), answer="resume")
        facts = result["payload"]["result"]["facts"]
        assert result["payload"]["error"]["stage"] == "resume_cleanup"
        assert facts["commit_state"] == "published" and facts["resumed_from"] == incomplete
        assert facts["report_path"] is None and facts["retention_deleted"] is None
        assert load_manifest(repo, facts["snapshot_id"], require_complete=True)
        assert (repo.path / "snapshots" / incomplete).exists()


def test_busy_slot_and_lifecycle_gate_during_blocked_real_backup(tmp_path, intent):
    with worker(tmp_path, "backup_block_scan") as peer:
        prepare(peer, intent)
        rid = peer.send("backup.run", backup_intent(intent))
        assert peer.receive()["payload"]["phase"] == "accepted"
        deadline = threading.Event()
        for _ in range(1000):
            if (tmp_path / "entered").exists():
                break
            deadline.wait(0.01)
        assert (tmp_path / "entered").exists()
        attempts = (
            ("setup.preflight", intent),
            ("tasks.list", {"config_root": intent["config_root"]}),
            ("backup.run", backup_intent(intent)),
            ("worker.shutdown", {}),
        )
        for method, params in attempts:
            peer.send(method, params)
            assert peer.receive()["payload"]["error"]["code"] == "busy"
        peer.send("ping")
        assert peer.receive()["payload"]["result"]["reply"] == "pong"
        (tmp_path / "release").touch()
        result = peer.receive()
        assert result["request_id"] == rid
        assert result["payload"]["result"]["facts"]["commit_state"] == "published"
