"""First production mutation: real temp artifacts and conservative wire outcomes."""

import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from mirrorly.application import setup
from mirrorly.config import load_task_config
from mirrorly.repo import load_repo
from mirrorly.worker import lifecycle
from test_worker import HOST, ROOT, Peer
from test_worker import intent as intent
from test_worker_lifecycle import status


@contextmanager
def worker(tmp_path, scenario=None):
    script = ROOT / "tests/worker_fixture_host.py" if scenario else HOST
    child = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-u",
            str(script),
            "--expected-interpreter",
            sys.executable,
            "--expected-checkout",
            str(ROOT),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            **os.environ,
            "MIRRORLY_TEST_SCENARIO": scenario or "",
            "MIRRORLY_TEST_GATE": str(tmp_path),
        },
    )
    peer = Peer(child)
    try:
        peer.negotiated = peer.initialize()["payload"]["result"]
        yield peer
    finally:
        (tmp_path / "release").touch()
        peer.close()


def create(peer, intent, approved=False):
    rid = peer.send("setup.create", intent | {"copy_mode_approved": approved})
    accepted = peer.receive()
    assert accepted["payload"]["phase"] == "accepted"
    terminal = peer.receive()
    assert terminal["request_id"] == rid
    assert terminal["operation_id"] == accepted["operation_id"]
    return terminal["payload"]


def config_path(intent):
    return Path(intent["config_root"]) / "config.d" / f"{intent['task_name']}.toml"


def assert_success(intent, terminal):
    assert terminal["error"] is None and terminal["result"]["outcome"] == "succeeded"
    facts = terminal["result"]["setup"]
    task = load_task_config(config_path(intent))
    repo = load_repo(intent["target"])
    assert facts["repository_initialized"] is True and facts["config_written"] is True
    assert facts["repository_path"] == str(repo.path)
    assert facts["config_path"] == str(config_path(intent))
    assert facts["source"] == task.source == str(Path(intent["source"]).resolve())
    assert facts["task_name"] == task.name == intent["task_name"]
    assert facts["repo"]["repo_id"] == repo.repo_id == task.repo_id
    assert facts["repo"]["volume"]["guid"] == repo.volume.guid == task.volume_guid
    assert facts["repo"]["hardlinks"] == repo.hardlinks
    assert not list((repo.path / "snapshots").iterdir())


def test_real_create_without_preflight_and_exact_methods(tmp_path, intent):
    with worker(tmp_path) as peer:
        assert peer.negotiated["methods"] == [
            "ping",
            "status",
            "worker.shutdown",
            "setup.preflight",
            "setup.create",
        ]
        assert peer.hello["payload"]["qualification"]["cli_imported"] is False
        assert_success(intent, create(peer, intent))
        assert status(peer)["owned"]
        for method in ("backup.run", "verify", "restore.execute", "snapshot.list", "cancel"):
            peer.send(method)
            assert peer.receive()["payload"]["error"]["code"] == "unsupported_method"


@pytest.mark.parametrize("bad", [None, 0, 1, "true", "false", []])
def test_explicit_approval_requires_boolean_no_coercion(tmp_path, intent, bad):
    with worker(tmp_path) as peer:
        peer.send("setup.create", intent | {"copy_mode_approved": bad})
        assert peer.receive()["payload"]["error"]["application_invoked"] is False
        assert not config_path(intent).exists()
        assert not (Path(intent["target"]) / "MirrorlyRepo").exists()


def test_create_rejects_missing_approval_and_client_facts(tmp_path, intent):
    with worker(tmp_path) as peer:
        for extra in (
            None,
            {"preflight": {}},
            {"repository_path": "trusted"},
            {"TaskConfig": {}},
            {"RepoInfo": {}},
            {"config_contents": "x"},
        ):
            params = intent if extra is None else intent | {"copy_mode_approved": False} | extra
            peer.send("setup.create", params)
            error = peer.receive()["payload"]["error"]
            assert error["code"] == "invalid_parameters" and not error["application_invoked"]
        assert not config_path(intent).exists()
        assert not (Path(intent["target"]) / "MirrorlyRepo").exists()


@pytest.mark.parametrize(
    "scenario,stage,initialized,written",
    [
        ("create_config_failure", "config_write", True, None),
        ("create_config_partial", "config_write", True, None),
        ("create_config_complete", "config_write", True, None),
        ("create_repo_failure", "repository_init", None, False),
        ("create_construct_failure", "config_construct", True, False),
    ],
)
def test_partial_application_outcomes_over_real_ipc(
    tmp_path, intent, scenario, stage, initialized, written
):
    with worker(tmp_path, scenario) as peer:
        terminal = create(peer, intent)
        assert terminal["result"]["outcome"] == "failed"
        facts, error = terminal["result"]["setup"], terminal["error"]
        assert error["kind"] == "application" and error["code"] == "setup_failure"
        assert error["stage"] == stage
        assert "injected" in error["technical"]["message"]
        assert facts["repository_initialized"] is initialized
        assert facts["config_written"] is written
        assert facts["config_path"] == str(config_path(intent))
        repo_path = Path(intent["target"]) / "MirrorlyRepo"
        assert facts["repository_path"] == str(repo_path)
        assert (repo_path / "lifecycle.json").is_file()  # No rollback.
        if initialized:
            assert facts["repo"]["repo_id"] == load_repo(intent["target"]).repo_id
        else:
            assert facts["repo"] is None and not (repo_path / "repo.json").exists()
        if scenario == "create_config_partial":
            assert config_path(intent).read_bytes() == b"partial TOML"
        elif scenario == "create_config_complete":
            assert load_task_config(config_path(intent)).repo_id == facts["repo"]["repo_id"]
        else:
            assert not config_path(intent).exists()
        assert (tmp_path / "create_calls").read_text().splitlines() == ["create"]


@pytest.mark.parametrize("change", ["source", "config"])
def test_create_rechecks_stale_preflight(tmp_path, intent, change):
    with worker(tmp_path) as peer:
        peer.send("setup.preflight", intent)
        peer.receive()
        assert peer.receive()["payload"]["result"]["preflight"]["problem"] is None
        if change == "source":
            Path(intent["source"]).rmdir()
        else:
            config_path(intent).parent.mkdir(parents=True)
            config_path(intent).write_bytes(b"existing task, do not overwrite")
        terminal = create(peer, intent)
        assert terminal["error"]["stage"] == "inputs"
        facts = terminal["result"]["setup"]
        assert facts["repository_initialized"] is False and facts["config_written"] is False
        assert not (Path(intent["target"]) / "MirrorlyRepo").exists()
        if change == "config":
            assert config_path(intent).read_bytes() == b"existing task, do not overwrite"


def test_stale_filesystem_requires_new_explicit_approval_no_auto_retry(tmp_path, intent):
    intent = intent | {"filesystem_policy": "warn"}
    with worker(tmp_path, "create_approval") as peer:
        peer.send("setup.preflight", intent)
        peer.receive()
        assert (
            peer.receive()["payload"]["result"]["preflight"]["copy_mode_approval_required"] is False
        )
        terminal = create(peer, intent)
        assert terminal["result"]["outcome"] == "decision_required"
        assert terminal["error"]["code"] == "copy_mode_approval_required"
        assert terminal["error"]["volume"]["filesystem"] == "exFAT"
        assert terminal["result"]["setup"]["repository_initialized"] is False
        assert terminal["result"]["setup"]["config_written"] is False
        assert (
            not config_path(intent).exists()
            and not (Path(intent["target"]) / "MirrorlyRepo").exists()
        )
        assert (tmp_path / "create_calls").read_text().splitlines() == ["create"]
        terminal = create(peer, intent, approved=True)  # A new, explicitly approved request.
        assert_success(intent, terminal)
        assert terminal["result"]["setup"]["repo"]["hardlinks"] is False


def test_copy_approval_cannot_override_strict_policy(tmp_path, intent):
    with worker(tmp_path, "create_approval") as peer:
        terminal = create(peer, intent, approved=True)
        assert terminal["error"]["stage"] == "repository_init"
        assert terminal["result"]["setup"]["repository_initialized"] is None
        assert terminal["result"]["setup"]["config_written"] is False
        assert not config_path(intent).exists()
        assert not (Path(intent["target"]) / "MirrorlyRepo").exists()


@pytest.mark.parametrize("crash", [False, True])
def test_gate_rejects_second_worker_then_acquires_clean_or_abandoned(tmp_path, intent, crash):
    with worker(tmp_path) as first, worker(tmp_path, "create_count") as second:
        assert status(first)["owned"] and not status(second)["owned"]
        second.send("setup.create", intent | {"copy_mode_approved": False})
        rejected = second.receive()
        assert rejected["operation_id"] is None
        error = rejected["payload"]["error"]
        assert error["code"] == "mutation_gate_unavailable" and not error["application_invoked"]
        assert error["lifecycle_gate"]["state"] == "unavailable"
        assert not (tmp_path / "create_calls").exists()
        assert (
            not config_path(intent).exists()
            and not (Path(intent["target"]) / "MirrorlyRepo").exists()
        )
        if crash:
            first.process.kill()
        else:
            first.send("worker.shutdown")
            first.receive()
        first.process.wait(timeout=5)
        # No status probe steals/clears WAIT_ABANDONED before admission.
        assert_success(intent, create(second, intent))
        gate = status(second)
        assert gate["owned"] and gate["abandoned_observed"] is crash


def test_available_status_is_not_mutation_authorization(tmp_path, intent):
    with worker(tmp_path) as first, worker(tmp_path) as contender:
        first.send("worker.shutdown")
        first.receive()
        first.process.wait(timeout=5)
        assert status(contender)["state"] == "available"
        with worker(tmp_path) as intervening:
            assert status(intervening)["owned"]
            contender.send("setup.create", intent | {"copy_mode_approved": False})
            assert contender.receive()["payload"]["error"]["code"] == "mutation_gate_unavailable"
            assert not config_path(intent).exists()


def test_duplicate_mutation_after_terminal_eviction_never_calls_again(tmp_path, intent):
    with worker(tmp_path, "create_count") as peer:
        terminal = create(peer, intent)
        assert_success(intent, terminal)
        original_id = "2"  # initialize consumed ID 1.
        for _ in range(33):
            peer.send("setup.preflight", intent)
            peer.receive()
            peer.receive()
        peer.send("status", {"request_id": original_id})
        record = peer.receive()["payload"]["result"]["request"]
        assert record["state"] == "stale_result_not_cached" and not record["terminal_available"]
        peer.send("setup.create", intent | {"copy_mode_approved": False}, rid=original_id)
        assert peer.receive()["payload"]["error"]["code"] == "stale_request_id"
        assert (tmp_path / "create_calls").read_text().splitlines() == ["create"]
        assert load_repo(intent["target"]).repo_id == terminal["result"]["setup"]["repo"]["repo_id"]


def test_accepted_delivery_failure_does_not_prevent_admitted_create(intent):
    calls, finished = [], threading.Event()

    def service(request, **decision):
        calls.append(request)
        result = setup.create_backup(request, **decision)
        finished.set()
        return result

    peer = Peer(create_service=service)
    try:
        peer.initialize()
        original = peer.host._response

        def delivery_lost(rid, phase, **kwargs):
            if phase == "accepted":
                peer.host.lost.set()
                return threading.Event()
            return original(rid, phase, **kwargs)

        peer.host._response = delivery_lost
        peer.send("setup.create", intent | {"copy_mode_approved": False})
        assert finished.wait(5)
        peer.thread.join(timeout=5)
        assert not peer.thread.is_alive() and len(calls) == 1
        assert load_task_config(config_path(intent)).repo_id == load_repo(intent["target"]).repo_id
        assert peer.host.active is None and len(peer.host.terminals) == 1
    finally:
        peer.close()


def test_gate_os_error_rejects_mutation_before_application(monkeypatch, intent):
    def unavailable():
        raise OSError(5, "test namespace unavailable")

    monkeypatch.setattr(lifecycle, "_Windows", unavailable)
    calls = []
    peer = Peer(create_service=lambda *a, **kw: calls.append(a))
    try:
        peer.initialize()
        peer.send("setup.create", intent | {"copy_mode_approved": False})
        error = peer.receive()["payload"]["error"]
        assert error["code"] == "mutation_gate_unavailable"
        assert error["lifecycle_gate"]["state"] == "error" and not error["application_invoked"]
        assert not calls and not config_path(intent).exists()
    finally:
        peer.close()


@pytest.mark.parametrize("scenario", ["create_unstructured_failure", "create_projection_failure"])
def test_unstructured_or_projection_fault_never_claims_rollback(tmp_path, intent, scenario):
    with worker(tmp_path, scenario) as peer:
        terminal = create(peer, intent)
        assert load_task_config(config_path(intent)).repo_id == load_repo(intent["target"]).repo_id
        if scenario == "create_projection_failure":
            assert terminal["error"]["kind"] == "worker"
            assert terminal["result"] == {
                "application_outcome": "succeeded",
                "setup": {"repository_initialized": True, "config_written": True},
            }
        else:
            assert terminal["error"]["stage"] is None
            assert terminal["result"]["setup"]["repository_initialized"] is None
            assert terminal["result"]["setup"]["config_written"] is None
        assert (tmp_path / "create_calls").read_text().splitlines() == ["create"]
