"""Production worker executes only one admitted, worker-owned Restore plan."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import pytest

from mirrorly.application import restoration
from mirrorly.repo import load_repo
from mirrorly.worker import protocol
from test_worker import Peer
from test_worker import intent as intent
from test_worker_backup import backup_intent
from test_worker_backup import execute as run_backup
from test_worker_creation import create, worker
from test_worker_restore_prepare import ask as prepare
from test_worker_restore_prepare import params as prepare_params


def execute(peer, plan_id, approval=False):
    rid = peer.send("restore.execute", {"plan_id": plan_id, "overwrite_approved": approval})
    first = peer.receive()
    assert first["request_id"] == rid
    if first["payload"]["phase"] == "rejected":
        return first["payload"]
    assert first["payload"]["phase"] == "accepted"
    terminal = peer.receive()
    assert terminal["request_id"] == rid
    assert terminal["operation_id"] == first["operation_id"]
    return terminal["payload"]


def repository_digest(path):
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        if item.is_file():
            digest.update(str(item.relative_to(path)).encode())
            digest.update(item.read_bytes())
    return digest.hexdigest()


@pytest.mark.parametrize(
    "policy,approval,expected",
    [
        ("skip_existing", False, b"existing"),
        ("replace_existing", True, b"source"),
    ],
)
def test_real_subprocess_execute_safe_merge_and_one_shot(
    tmp_path, intent, policy, approval, expected
):
    with worker(tmp_path) as peer:
        assert create(peer, intent)["error"] is None
        source = Path(intent["source"])
        (source / "alpha.txt").write_bytes(b"source")
        (source / "new.txt").write_bytes(b"new")
        backup, _ = run_backup(peer, backup_intent(intent))
        assert backup["payload"]["error"] is None
        repo = load_repo(intent["target"])
        before = repository_digest(repo.path)
        destination = tmp_path / "restore"
        destination.mkdir()
        (destination / "alpha.txt").write_bytes(b"existing")
        (destination / "extra.txt").write_bytes(b"extra")
        preview = prepare(peer, prepare_params(intent, destination, policy=policy))["result"][
            "preview"
        ]
        terminal = execute(peer, preview["plan_id"], approval)
        assert terminal["error"] is None
        facts = terminal["result"]["facts"]
        assert terminal["result"]["outcome"] == "completed"
        assert facts["files_restored"] == (1 if not approval else 2)
        assert facts["items_skipped"] == (1 if not approval else 0)
        assert facts["bytes_written"] == (3 if not approval else 9)
        assert (destination / "alpha.txt").read_bytes() == expected
        assert (destination / "new.txt").read_bytes() == b"new"
        assert (destination / "extra.txt").read_bytes() == b"extra"
        assert repository_digest(repo.path) == before
        again = execute(peer, preview["plan_id"], approval)
        assert again["error"]["code"] == "restore_plan_unavailable"
        assert len(protocol.encode(protocol.message("response", peer.session, terminal))) < 65536


def test_approval_stale_plan_and_gate_rejection_preserve_unadmitted_plan(
    tmp_path, intent, monkeypatch
):
    peer = Peer()
    try:
        peer.initialize()
        assert create(peer, intent)["error"] is None
        (Path(intent["source"]) / "a.txt").write_bytes(b"source")
        run_backup(peer, backup_intent(intent))
        destination = tmp_path / "restore"
        destination.mkdir()
        (destination / "a.txt").write_bytes(b"existing")
        first = prepare(peer, prepare_params(intent, destination))["result"]["preview"]
        second = prepare(peer, prepare_params(intent, destination, policy="replace_existing"))[
            "result"
        ]["preview"]
        assert execute(peer, first["plan_id"])["error"]["code"] == "restore_plan_unavailable"
        assert execute(peer, second["plan_id"])["error"]["code"] == "overwrite_approval_required"
        assert peer.host.restore_plan.plan_id == second["plan_id"]
        original = peer.host.lifecycle_gate.require_ownership

        def unavailable():
            raise RuntimeError("gate unavailable")

        monkeypatch.setattr(peer.host.lifecycle_gate, "require_ownership", unavailable)
        assert (
            execute(peer, second["plan_id"], True)["error"]["code"] == "mutation_gate_unavailable"
        )
        assert peer.host.restore_plan.plan_id == second["plan_id"]
        monkeypatch.setattr(peer.host.lifecycle_gate, "require_ownership", original)
        assert execute(peer, second["plan_id"], True)["error"] is None
    finally:
        peer.close()


def test_admitted_failure_consumes_plan_and_preserves_partial_mutation_warning(tmp_path, intent):
    destination = tmp_path / "restore"
    destination.mkdir()

    def failing(plan, request):
        (destination / "partial.txt").write_bytes(b"already changed")
        raise OSError("failure after a destination write")

    peer = Peer(restore_execute_service=failing)
    try:
        peer.initialize()
        assert create(peer, intent)["error"] is None
        (Path(intent["source"]) / "a.txt").write_bytes(b"source")
        run_backup(peer, backup_intent(intent))
        preview = prepare(peer, prepare_params(intent, destination))["result"]["preview"]
        failed = execute(peer, preview["plan_id"])
        assert failed["result"]["outcome"] == "failed"
        assert failed["result"]["destination_may_have_changed"] is True
        assert failed["result"]["facts"] is None
        assert failed["error"]["code"] == "restore_execution_failed"
        assert (destination / "partial.txt").read_bytes() == b"already changed"
        assert execute(peer, preview["plan_id"])["error"]["code"] == "restore_plan_unavailable"
    finally:
        peer.close()


def test_admitted_restore_survives_client_loss_and_holds_gate(tmp_path, intent):
    entered, release = threading.Event(), threading.Event()

    def slow(plan, request):
        entered.set()
        assert release.wait(5)
        return restoration.execute_restore(plan.prepared)

    peer = Peer(restore_execute_service=slow)
    peer.initialize()
    assert create(peer, intent)["error"] is None
    (Path(intent["source"]) / "a.txt").write_bytes(b"source")
    run_backup(peer, backup_intent(intent))
    destination = tmp_path / "restore"
    destination.mkdir()
    preview = prepare(peer, prepare_params(intent, destination))["result"]["preview"]
    peer.send("restore.execute", {"plan_id": preview["plan_id"], "overwrite_approved": False})
    assert peer.receive()["payload"]["phase"] == "accepted"
    assert entered.wait(2)
    assert peer.host.restore_plan is None
    peer.send("status")
    assert peer.receive()["payload"]["result"]["lifecycle_gate"]["state"] == "held"
    peer.input.put(b"")
    assert peer.host.lost.wait(2)
    release.set()
    peer.close()
    assert (destination / "a.txt").read_bytes() == b"source"


def test_invalid_execute_intent_and_unknown_plan(tmp_path):
    peer = Peer()
    try:
        peer.initialize()
        for invalid in (
            {},
            {"plan_id": "x", "overwrite_approved": False},
            {"plan_id": "a" * 32, "overwrite_approved": "yes"},
        ):
            peer.send("restore.execute", invalid)
            assert peer.receive()["payload"]["error"]["code"] == "invalid_parameters"
        peer.send("restore.execute", {"plan_id": "a" * 32, "overwrite_approved": False})
        assert peer.receive()["payload"]["error"]["code"] == "restore_plan_unavailable"
    finally:
        peer.close()


def test_real_second_worker_cannot_mutate_while_first_owns_gate(tmp_path, intent):
    with worker(tmp_path) as first:
        assert create(first, intent)["error"] is None
        (Path(intent["source"]) / "a.txt").write_bytes(b"source")
        run_backup(first, backup_intent(intent))
        destination = tmp_path / "restore"
        destination.mkdir()
        with worker(tmp_path) as contender:
            preview = prepare(contender, prepare_params(intent, destination))["result"]["preview"]
            refused = execute(contender, preview["plan_id"])
            assert refused["error"]["code"] == "mutation_gate_unavailable"
            assert not (destination / "a.txt").exists()


def test_normal_result_with_type_conflict_is_not_clean_success(tmp_path, intent):
    with worker(tmp_path) as peer:
        assert create(peer, intent)["error"] is None
        (Path(intent["source"]) / "a.txt").write_bytes(b"source")
        run_backup(peer, backup_intent(intent))
        destination = tmp_path / "restore"
        (destination / "a.txt").mkdir(parents=True)
        preview = prepare(peer, prepare_params(intent, destination, policy="replace_existing"))[
            "result"
        ]["preview"]
        assert preview["file_conflict_count"] == 1
        terminal = execute(peer, preview["plan_id"], True)
        assert terminal["result"]["outcome"] == "completed_with_issues"
        assert terminal["result"]["facts"]["conflicts"] == 1
        assert (destination / "a.txt").is_dir()
