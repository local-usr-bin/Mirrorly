"""Real Windows process-lifetime admission; no simulated ownership assertions."""

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes as w

import pytest

from mirrorly.worker.lifecycle import LifecycleGate
from test_worker import HOST, ROOT, Peer
from test_worker import intent as intent


def start():
    peer = Peer(
        subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-u",
                str(HOST),
                "--expected-interpreter",
                sys.executable,
                "--expected-checkout",
                str(ROOT),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    )
    peer.initialize()
    return peer


def status(peer):
    peer.send("status")
    return peer.receive()["payload"]["result"]["lifecycle_gate"]


@pytest.mark.parametrize("crash", [False, True], ids=["normal-exit", "forced-termination"])
def test_stable_gate_cross_process_release_and_readonly_contender(intent, crash):
    first = start()
    second = None
    try:
        owned = status(first)
        assert owned["state"] == "held" and owned["owned"]
        assert owned["identity"].startswith("Local\\Mirrorly.ProductionWorker.v1.S-1-")
        second = start()
        blocked = status(second)
        assert first.session != second.session
        assert blocked["identity"] == owned["identity"]
        assert blocked["state"] == "unavailable" and not blocked["owned"]
        guard = LifecycleGate()
        try:
            assert guard.name == owned["identity"]
            with pytest.raises(RuntimeError, match="not owned"):
                guard.require_ownership()
            assert not guard.held
        finally:
            guard.close()
        second.send("setup.preflight", intent)
        assert second.receive()["payload"]["phase"] == "accepted"
        result = second.receive()["payload"]["result"]["preflight"]
        assert result["problem"] is None
        second.send(
            "backup.run",
            {
                "config_root": intent["config_root"],
                "task": None,
                "dry_run": True,
                "full_hash": False,
                "exclude": [],
            },
        )
        assert second.receive()["payload"]["phase"] == "accepted"
        dry_run = second.receive()["payload"]
        assert dry_run["phase"] == "terminal"
        assert dry_run["error"]["code"] == "backup_failure"
        second.send(
            "backup.run",
            {
                "config_root": intent["config_root"],
                "task": None,
                "dry_run": False,
                "full_hash": False,
                "exclude": [],
            },
        )
        blocked_backup = second.receive()["payload"]
        assert blocked_backup["error"]["code"] == "mutation_gate_unavailable"
        assert blocked_backup["error"]["application_invoked"] is False
        if crash:
            first.process.kill()  # Only this test-owned worker; never a production action.
            first.process.wait(timeout=5)
        else:
            first.send("worker.shutdown")
            assert first.receive()["payload"]["result"]["shutdown"] == "idle"
            first.process.wait(timeout=5)
        available = status(second)
        assert available["state"] == "available" and not available["owned"]
        assert available["abandoned_observed"] is crash
        third = start()
        try:
            assert status(third)["state"] == "held"
            assert status(second)["state"] == "unavailable"
        finally:
            third.close()
    finally:
        first.close()
        if second:
            second.close()


@pytest.mark.parametrize("method", ["setup.preflight", "setup.create"])
def test_parent_process_loss_does_not_release_surviving_worker_gate(intent, tmp_path, method):
    params = intent | {"copy_mode_approved": False} if method == "setup.create" else intent
    parent = subprocess.Popen(
        [sys.executable, "-I", "-u", str(ROOT / "tests/worker_parent_fixture.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "MIRRORLY_TEST_GATE": str(tmp_path), "MIRRORLY_TEST_METHOD": method},
    )
    contender = None
    handle = None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.WaitForSingleObject.restype = w.DWORD
    kernel.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    try:
        parent.stdin.write(json.dumps(params).encode() + b"\n")
        parent.stdin.flush()
        # Bounded reading, so startup failures cannot hang the regression suite.
        import queue

        replies = queue.Queue()
        threading.Thread(target=lambda: replies.put(parent.stdout.readline()), daemon=True).start()
        facts = json.loads(replies.get(timeout=10))
        assert facts["gate"]["owned"]
        handle = kernel.OpenProcess(
            0x00100001, False, facts["pid"]
        )  # synchronize/terminate test child
        assert handle
        deadline = time.monotonic() + 5
        while not (tmp_path / "entered").exists():
            assert time.monotonic() < deadline
            threading.Event().wait(0.01)
        parent.kill()
        parent.wait(timeout=5)
        if method == "setup.create":
            assert (tmp_path / "target/MirrorlyRepo/repo.json").is_file()
            assert not (tmp_path / "config/config.d/documents.toml").exists()
        assert (
            kernel.WaitForSingleObject(handle, 0) == 0x102
        )  # Still alive after parent termination.
        contender = start()
        gate = status(contender)
        assert gate["identity"] == facts["gate"]["identity"]
        assert gate["state"] == "unavailable" and not gate["owned"]
        if method == "setup.create":
            contender.send("setup.create", params)
            error = contender.receive()["payload"]["error"]
            assert error["code"] == "mutation_gate_unavailable" and not error["application_invoked"]
            assert not (tmp_path / "create_finished").exists()
        (tmp_path / "release").touch()
        assert kernel.WaitForSingleObject(handle, 5000) == 0
        assert status(contender)["state"] == "available"
        if method == "setup.create":
            from mirrorly.config import load_task_config
            from mirrorly.repo import load_repo

            cfg = load_task_config(tmp_path / "config/config.d/documents.toml")
            assert cfg.repo_id == load_repo(intent["target"]).repo_id
            assert (tmp_path / "create_calls").read_text().splitlines() == ["create"]
            assert (tmp_path / "create_finished").exists()
    finally:
        (tmp_path / "release").touch()
        if handle:
            if kernel.WaitForSingleObject(handle, 5000) == 0x102:
                kernel.TerminateProcess(
                    handle, 99
                )  # Test cleanup only, by retained process handle.
            kernel.CloseHandle(handle)
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
        for stream in (parent.stdin, parent.stdout, parent.stderr):
            stream.close()
        if contender:
            contender.close()


def test_gate_guard_has_thread_affinity_and_does_not_recursively_acquire():
    gate = LifecycleGate()
    try:
        gate.require_ownership()
        gate.require_ownership()
        errors = []

        def wrong_thread():
            try:
                gate.require_ownership()
            except RuntimeError as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=wrong_thread)
        thread.start()
        thread.join(timeout=2)
        assert errors and "host/control thread" in errors[0]
    finally:
        gate.close()
    peer = start()
    try:
        assert status(peer)["owned"]  # One close released both guard checks, not two acquisitions.
    finally:
        peer.close()


def test_gate_os_error_fails_closed_without_disabling_readonly_host(monkeypatch, intent):
    from mirrorly.worker import lifecycle

    def inaccessible():
        raise OSError(5, "injected inaccessible synchronization namespace")

    monkeypatch.setattr(lifecycle, "_Windows", inaccessible)
    gate = LifecycleGate()
    with pytest.raises(RuntimeError, match="not owned"):
        gate.require_ownership()
    assert gate.observe()["state"] == "error" and not gate.held
    gate.close()
    peer = Peer()
    try:
        peer.initialize()
        observed = status(peer)
        assert observed["state"] == "error" and not observed["owned"]
        peer.send("setup.preflight", intent)
        assert peer.receive()["payload"]["phase"] == "accepted"
        assert peer.receive()["payload"]["result"]["preflight"]["problem"] is None
    finally:
        peer.close()
