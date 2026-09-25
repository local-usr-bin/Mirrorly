"""Read-only production Restore planning and worker-owned preview facts."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from mirrorly.repo import load_repo
from mirrorly.worker import protocol, restore_prepare
from test_restore import _make_snapshot
from test_worker import Peer
from test_worker import intent as intent
from test_worker_backup import backup_intent
from test_worker_backup import execute as run_backup
from test_worker_creation import create, worker


def params(intent, destination, *, snapshot_id=None, policy="skip_existing"):
    return {
        "config_root": intent["config_root"],
        "task": intent["task_name"],
        "snapshot_id": snapshot_id,
        "destination": str(destination),
        "policy": policy,
    }


def ask(peer, request):
    rid = peer.send("restore.prepare", request)
    first = peer.receive()
    assert first["request_id"] == rid
    if first["payload"]["phase"] == "rejected":
        return first["payload"]
    assert first["payload"]["phase"] == "accepted"
    terminal = peer.receive()
    assert terminal["request_id"] == rid
    assert terminal["operation_id"] == first["operation_id"]
    return terminal["payload"]


def test_real_prepare_skip_replace_and_worker_owned_plan(tmp_path, intent):
    peer = Peer()
    try:
        peer.initialize()
        assert create(peer, intent)["error"] is None
        source = Path(intent["source"])
        (source / "alpha.txt").write_bytes(b"source")
        (source / "beta.txt").write_bytes(b"other")
        backup, _ = run_backup(peer, backup_intent(intent))
        snapshot = backup["payload"]["result"]["facts"]["snapshot_id"]
        destination = tmp_path / "restore"
        destination.mkdir()
        (destination / "alpha.txt").write_bytes(b"existing")
        before = (destination / "alpha.txt").read_bytes()

        skipped = ask(peer, params(intent, destination))
        assert skipped["error"] is None
        first = skipped["result"]["preview"]
        assert first["selector"] == intent["task_name"] and first["snapshot_id"] == snapshot
        assert first["destination"] == str(destination)
        assert first["policy"] == "skip_existing"
        assert first["file_create_count"] == 1 and first["file_skip_count"] == 1
        assert first["file_overwrite_count"] == first["file_conflict_count"] == 0
        assert first["directory_entry_count"] == 0
        assert set(first) == {
            "plan_id",
            "selector",
            "snapshot_id",
            "destination",
            "policy",
            "file_create_count",
            "file_overwrite_count",
            "file_skip_count",
            "file_conflict_count",
            "directory_entry_count",
        }
        assert peer.host.restore_plan.plan_id == first["plan_id"]
        assert peer.host.restore_plan.prepared.plan.snapshot_id == snapshot
        assert peer.host.restore_plan.prepared.plan.entries
        assert len(protocol.encode(protocol.message("response", peer.session, skipped))) < 65536

        replaced = ask(
            peer, params(intent, destination, snapshot_id=snapshot, policy="replace_existing")
        )
        second = replaced["result"]["preview"]
        assert second["plan_id"] != first["plan_id"]
        assert peer.host.restore_plan.plan_id == second["plan_id"]
        assert second["file_create_count"] == 1 and second["file_overwrite_count"] == 1
        assert second["file_skip_count"] == 0 and second["policy"] == "replace_existing"
        assert (destination / "alpha.txt").read_bytes() == before
        assert not (destination / "beta.txt").exists()
        peer.send("restore.execute", {"plan_id": second["plan_id"]})
        assert peer.receive()["payload"]["error"]["code"] == "invalid_parameters"
        assert peer.host.restore_plan.plan_id == second["plan_id"]  # ID is not approval.
        unavailable = ask(peer, params(intent, destination) | {"task": "missing"})
        assert unavailable["result"]["preview"] is None
        assert unavailable["error"]["code"] == "unknown_task"
        assert peer.host.restore_plan is None  # Even a failed new intent retires the old plan.
    finally:
        peer.close()
    assert peer.host.restore_plan is None
    another = Peer()
    try:
        another.initialize()
        assert another.host.restore_plan is None  # No plan crosses worker sessions.
    finally:
        another.close()


def test_real_subprocess_prepare_and_failure_is_not_empty_success(tmp_path, intent):
    with worker(tmp_path) as peer:
        assert create(peer, intent)["error"] is None
        destination = tmp_path / "restore"
        destination.mkdir()
        no_complete = ask(peer, params(intent, destination))
        assert no_complete["result"]["preview"] is None
        assert no_complete["error"]["kind"] == "application"
        assert no_complete["error"]["code"] == "no_complete_snapshot"
        assert no_complete["error"]["technical"]["exception_type"] == "NoCompleteSnapshot"

        (Path(intent["source"]) / "a.txt").write_bytes(b"content")
        backup, _ = run_backup(peer, backup_intent(intent))
        snapshot = backup["payload"]["result"]["facts"]["snapshot_id"]
        success = ask(peer, params(intent, destination, snapshot_id=snapshot))
        assert success["error"] is None and success["result"]["preview"]["snapshot_id"] == snapshot
        for wrong, code in (
            (params(intent, destination, snapshot_id="missing"), "unknown_snapshot"),
            (params(intent, destination) | {"task": "unknown"}, "unknown_task"),
        ):
            failure = ask(peer, wrong)
            assert failure["result"]["preview"] is None
            assert failure["error"]["kind"] == "application"
            assert failure["error"]["code"] == code
        peer.send("restore.execute", {"plan_id": success["result"]["preview"]["plan_id"]})
        assert peer.receive()["payload"]["error"]["code"] == "invalid_parameters"


def test_incomplete_and_repository_overlapping_target_fail_closed(tmp_path, intent):
    with worker(tmp_path) as peer:
        assert create(peer, intent)["error"] is None
        repo = load_repo(intent["target"])
        _make_snapshot(repo, "unfinished", {"a.txt": b"a"}, complete=False)
        incomplete = ask(peer, params(intent, tmp_path / "restore", snapshot_id="unfinished"))
        assert incomplete["result"]["preview"] is None
        assert incomplete["error"]["code"] == "incomplete_snapshot"

        relative = f"target/{repo.path.name}/manifests/collision.json"
        _make_snapshot(repo, "collision", {relative: b"collision"})
        overlap = ask(
            peer, params(intent, tmp_path, snapshot_id="collision", policy="replace_existing")
        )
        assert overlap["result"]["preview"] is None
        assert overlap["error"]["code"] == "unsafe_destination"
        assert (repo.path / "manifests" / "collision.json").is_file()


def test_unreadable_config_invalid_repo_and_manifest_never_return_a_plan(tmp_path, intent):
    with worker(tmp_path) as peer:
        assert create(peer, intent)["error"] is None
        destination = tmp_path / "restore"
        config = Path(intent["config_root"]) / "config.d" / f"{intent['task_name']}.toml"
        original = config.read_bytes()
        config.write_text("invalid = [", encoding="utf-8")
        failure = ask(peer, params(intent, destination))
        assert (
            failure["error"]["code"] == "task_unreadable" and failure["result"]["preview"] is None
        )
        config.write_bytes(original)

        repo = load_repo(intent["target"])
        info = repo.path / "repo.json"
        original_info = info.read_bytes()
        info.write_text("{}", encoding="utf-8")
        failure = ask(peer, params(intent, destination))
        assert (
            failure["error"]["code"] == "repository_invalid"
            and failure["result"]["preview"] is None
        )
        info.write_bytes(original_info)

        _make_snapshot(repo, "broken", {"a.txt": b"a"})
        manifest = repo.path / "manifests" / "broken.json"
        contents = json.loads(manifest.read_text(encoding="utf-8"))
        contents["format_version"] = 999
        manifest.write_text(json.dumps(contents), encoding="utf-8")
        failure = ask(peer, params(intent, destination, snapshot_id="broken"))
        assert failure["error"]["code"] == "manifest_unavailable", failure
        assert failure["result"]["preview"] is None
        manifest.write_text("{not json", encoding="utf-8")
        failure = ask(peer, params(intent, destination, snapshot_id="broken"))
        assert (
            failure["error"]["code"] == "repository_invalid"
        )  # v2 lifecycle validation sees it first.
        manifest.unlink()
        repo.path.rename(Path(intent["target"]) / "moved-repo")
        failure = ask(peer, params(intent, destination))
        assert (
            failure["error"]["code"] == "repository_unavailable"
            and failure["result"]["preview"] is None
        )


@pytest.mark.parametrize(
    "change",
    [
        {"config_root": "relative"},
        {"task": "../escape"},
        {"snapshot_id": ""},
        {"destination": "relative"},
        {"policy": "older"},
        {"policy": []},
        {"in_place": True},
    ],
)
def test_invalid_intent_rejected_before_application(tmp_path, intent, change):
    peer = Peer()
    try:
        peer.initialize()
        rejected = ask(peer, params(intent, tmp_path / "restore") | change)
        assert rejected["error"] == {
            "kind": "admission",
            "code": "invalid_parameters",
            "application_invoked": False,
        }
        assert peer.host.restore_plan is None
    finally:
        peer.close()


def test_prepare_uses_one_slot_without_mutation_gate(tmp_path, intent, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow(_):
        entered.set()
        assert release.wait(5)
        raise OSError("deliberately unavailable")

    peer = Peer(restore_prepare_service=slow)
    try:
        peer.initialize()
        monkeypatch.setattr(
            peer.host.lifecycle_gate,
            "require_ownership",
            lambda: pytest.fail("read-only prepare used the mutation gate"),
        )
        rid = peer.send("restore.prepare", params(intent, tmp_path / "restore"))
        assert peer.receive()["payload"]["phase"] == "accepted"
        assert entered.wait(2)
        peer.send("restore.prepare", params(intent, tmp_path / "other"))
        assert peer.receive()["payload"]["error"]["code"] == "busy"
        peer.send("ping")
        assert peer.receive()["payload"]["result"]["reply"] == "pong"
        release.set()
        terminal = peer.receive()
        assert terminal["request_id"] == rid and terminal["payload"]["result"]["preview"] is None
    finally:
        release.set()
        peer.close()


def test_preview_is_bounded_and_plan_id_is_not_a_plan_schema(tmp_path):
    intent = restore_prepare.RestorePrepareIntent(
        str(tmp_path), "one", None, str(tmp_path), "skip_existing"
    )
    summary = SimpleNamespace(create=1, overwrite=0, skip=0, conflict=0, dirs=0)
    prepared = SimpleNamespace(
        plan=SimpleNamespace(snapshot_id="s1", destination=tmp_path), summary=summary
    )
    value = restore_prepare.WorkerPreparedRestore("a" * 32, intent, prepared)
    assert (
        len(
            protocol.encode(
                protocol.message("response", "session", {"preview": restore_prepare.project(value)})
            )
        )
        < 65536
    )
    prepared.plan.destination = tmp_path / ("x" * 10000)
    with pytest.raises(ValueError, match="bounded"):
        restore_prepare.project(value)


def test_unrepresentable_preview_never_becomes_worker_plan(tmp_path, intent):
    summary = SimpleNamespace(create=1, overwrite=0, skip=0, conflict=0, dirs=0)
    prepared = SimpleNamespace(
        plan=SimpleNamespace(snapshot_id="s1", destination=tmp_path / ("x" * 10000)),
        summary=summary,
    )
    peer = Peer(restore_prepare_service=lambda _: prepared)
    try:
        peer.initialize()
        failure = ask(peer, params(intent, tmp_path / "restore"))
        assert failure["result"]["outcome"] == "unreported"
        assert failure["error"]["code"] == "result_projection_failed"
        assert peer.host.restore_plan is None
    finally:
        peer.close()


def test_channel_loss_discards_ephemeral_plan_without_replay(tmp_path, intent):
    entered, release = threading.Event(), threading.Event()

    def slow(_):
        entered.set()
        assert release.wait(5)
        raise OSError("planning ended after channel loss")

    peer = Peer(restore_prepare_service=slow)
    peer.initialize()
    peer.send("restore.prepare", params(intent, tmp_path / "restore"))
    assert peer.receive()["payload"]["phase"] == "accepted"
    assert entered.wait(2)
    peer.input.put(b"")
    assert peer.host.lost.wait(2)
    release.set()
    peer.close()
    assert peer.host.restore_plan is None
