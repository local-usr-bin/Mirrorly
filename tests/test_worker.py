"""Production host + real subprocess preflight, including bounded IO fault seams."""

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from mirrorly.application.setup import SetupPreflight
from mirrorly.worker import protocol as p
from mirrorly.worker.host import WorkerHost
from mirrorly.worker.transport import Diagnostics, Outbound

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "src/mirrorly/worker/launch.py"


class Peer:
    def __init__(self, process=None, service=None):
        self.process = process
        self.responses = queue.Queue()
        self.input = queue.Queue()
        self.counter = 0
        if process:
            self.thread = threading.Thread(target=self._drain, daemon=True)
            self.thread.start()
            self.errors = []
            self.error_thread = threading.Thread(target=self._errors, daemon=True)
            self.error_thread.start()
        else:
            self.host = WorkerHost(
                lambda _: self.input.get(),
                self.responses.put,
                {},
                service=service,
                shutdown_seconds=0.1,
            )
            self.thread = threading.Thread(target=self.host.run, daemon=True)
            self.thread.start()
        self.hello = self.receive()
        self.session = self.hello["session_id"]

    def _drain(self):
        while line := self.process.stdout.readline():
            self.responses.put(line)
        self.responses.put(None)

    def _errors(self):
        while data := self.process.stderr.read(2048):
            self.errors.append(data)

    def receive(self):
        raw = self.responses.get(timeout=10)
        assert raw is not None, "Worker closed stdout before response"
        return p.parse(raw)

    def raw(self, data):
        if self.process:
            self.process.stdin.write(data)
            self.process.stdin.flush()
        else:
            self.input.put(data)

    def send(self, method, params=None, *, rid=None, version=p.VERSION, kind="request"):
        self.counter += 1
        rid = rid or str(self.counter)
        payload = (
            {"required_capabilities": []}
            if kind == "initialize"
            else {"method": method, "params": params or {}}
        )
        self.raw(p.encode(p.message(kind, self.session, payload, rid, version=version)))
        return rid

    def initialize(self):
        self.send(None, kind="initialize")
        result = self.receive()
        assert result["payload"]["phase"] == "terminal"
        assert all(value is False for value in result["payload"]["result"]["capabilities"].values())

    def close(self):
        if self.process:
            if self.process.stdin and not self.process.stdin.closed:
                self.process.stdin.close()
            self.process.wait(timeout=10)
            self.thread.join(timeout=2)
            self.error_thread.join(timeout=2)
            self.process.stdout.close()
            self.process.stderr.close()
        else:
            self.input.put(b"")
            self.thread.join(timeout=5)
            assert not self.thread.is_alive()


@pytest.fixture
def peer():
    child = subprocess.Popen(
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
    peer = Peer(child)
    try:
        yield peer
    finally:
        peer.close()


@pytest.fixture
def intent(tmp_path):
    source = tmp_path / "源文件"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    return {
        "task_name": "documents",
        "source": str(source),
        "target": str(target),
        "config_root": str(tmp_path / "config"),
        "filesystem_policy": "strict",
    }


def test_real_preflight_and_qualification_no_cli(peer, intent):
    proof = peer.hello["payload"]["qualification"]
    assert Path(proof["executable"]) == Path(sys.executable)
    assert Path(proof["package_path"]).resolve() == ROOT / "src/mirrorly/__init__.py"
    assert Path(proof["setup_path"]).resolve() == ROOT / "src/mirrorly/application/setup.py"
    assert proof["editable_origin"]["url"].casefold() == ROOT.as_uri().casefold()
    assert proof["cli_imported"] is False
    peer.initialize()
    rid = peer.send("setup.preflight", intent)
    accepted, terminal = peer.receive(), peer.receive()
    assert accepted["payload"]["phase"] == "accepted"
    assert terminal["operation_id"] == accepted["operation_id"]
    assert terminal["request_id"] == rid
    result = terminal["payload"]["result"]
    assert result["outcome"] == "succeeded"
    facts = result["preflight"]
    assert facts["inputs_valid"] and facts["problem"] is None
    assert facts["repository_volume"]["guid"]
    assert Path(facts["repository_path"]) == Path(intent["target"]) / "MirrorlyRepo"
    assert not Path(facts["repository_path"]).exists()
    assert not Path(intent["config_root"]).exists()


def test_preflight_problem_and_reject_untrusted_fields(peer, intent):
    peer.initialize()
    peer.send("setup.preflight", intent | {"source": str(Path(intent["source"]) / "missing")})
    peer.receive()
    result = peer.receive()["payload"]["result"]["preflight"]
    assert result["problem"]["stage"] == "inputs"
    assert result["problem"]["repository_initialized"] is False
    for params in (
        intent | {"repo": {}},
        intent | {"config_root": "relative"},
        intent | {"filesystem_policy": "invented"},
    ):
        peer.send("setup.preflight", params)
        assert peer.receive()["payload"]["error"]["application_invoked"] is False
    for method in ("setup.create", "backup.run", "verify.run", "restore.execute", "test_crash"):
        peer.send(method)
        assert peer.receive()["payload"]["error"]["code"] == "unsupported_method"


def test_initialize_versions_before_init_and_duplicate(peer, intent):
    peer.send("setup.preflight", intent)
    assert peer.receive()["payload"]["error"]["code"] == "not_initialized"
    for version in ({"major": 2, "minor": 0}, {"major": 1, "minor": 1}):
        peer.send(None, kind="initialize", version=version)
        assert peer.receive()["payload"]["error"]["code"] == "unsupported_version"
    peer.initialize()
    rid = peer.send("ping")
    assert peer.receive()["payload"]["result"] == {"reply": "pong"}
    peer.send("setup.preflight", intent, rid=rid)
    assert peer.receive()["payload"]["error"]["code"] == "stale_request_id"


def test_busy_status_no_hidden_queue_and_shutdown(intent):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def service(request):
        calls.append(request)
        entered.set()
        assert release.wait(5)
        return SetupPreflight(request.repository_path, request.config_path)

    peer = Peer(service=service)
    try:
        peer.initialize()
        rid = peer.send("setup.preflight", intent)
        accepted = peer.receive()
        assert entered.wait(2)
        for method in ("setup.preflight", "worker.shutdown"):
            peer.send(method, intent if method == "setup.preflight" else None)
            error = peer.receive()["payload"]["error"]
            assert error == {"kind": "admission", "code": "busy", "application_invoked": False}
        peer.send("setup.preflight", intent, rid=rid)
        assert peer.receive()["payload"]["error"]["code"] == "stale_request_id"
        peer.send("status", {"request_id": rid})
        status = peer.receive()["payload"]["result"]
        assert status["state"] == "busy"
        assert status["active"]["operation_id"] == accepted["operation_id"]
        peer.send("ping")
        assert peer.receive()["payload"]["result"]["reply"] == "pong"
        release.set()
        assert peer.receive()["payload"]["phase"] == "terminal"
        peer.send("status", {"request_id": rid})
        assert peer.receive()["payload"]["result"]["request"]["terminal_available"]
        peer.send("worker.shutdown")
        assert peer.receive()["payload"]["result"]["shutdown"] == "idle"
        assert len(calls) == 1
    finally:
        release.set()
        peer.close()


def test_eof_waits_for_active_application(intent):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def service(request):
        entered.set()
        assert release.wait(5)
        finished.set()
        return SetupPreflight(request.repository_path, request.config_path)

    peer = Peer(service=service)
    peer.initialize()
    peer.send("setup.preflight", intent)
    peer.receive()
    assert entered.wait(2)
    peer.input.put(b"")
    assert peer.host.lost.wait(2)
    assert not finished.is_set()
    release.set()
    peer.close()
    assert finished.is_set()


def test_application_exception_roundtrip(intent):
    def service(_):
        raise OSError(5, "injected service failure")

    peer = Peer(service=service)
    try:
        peer.initialize()
        peer.send("setup.preflight", intent)
        peer.receive()
        terminal = peer.receive()["payload"]
        assert terminal["error"]["kind"] == "application"
        assert terminal["error"]["technical"]["errno"] == 5
        assert terminal["result"]["outcome"] == "failed"
    finally:
        peer.close()


def test_slow_writer_does_not_block_producer_and_is_bounded():
    blocked, release, lost = threading.Event(), threading.Event(), threading.Event()

    def write(_):
        blocked.set()
        assert release.wait(5)

    outbound = Outbound(write, lost, capacity=2)
    value = p.message("event", "s", {})
    try:
        outbound.send(value)
        assert blocked.wait(2)
        for _ in range(30):
            outbound.notice(value)
        assert outbound.notices.qsize() == 16
        outbound.send(value)
        outbound.send(value)
        terminal = outbound.send(value, terminal=True)
        assert not lost.is_set() and not terminal.is_set()
        outbound.send(value)
        assert lost.is_set()
        assert outbound.frames.qsize() == 3
    finally:
        release.set()
        outbound.close()


def test_stderr_failure_is_isolated():
    attempted = threading.Event()

    def write(_):
        attempted.set()
        raise BrokenPipeError

    diagnostics = Diagnostics(write)
    diagnostics.write("diagnostic")
    assert attempted.wait(2)
    for _ in range(100):
        diagnostics.write("x" * 10000)
    assert diagnostics.chunks.qsize() <= 32


@pytest.mark.parametrize(
    "data",
    [b"\xff\n", b"{\n", b"x" * p.HANDSHAKE_BYTES, b'{"a":1,"a":2}\n'],
    ids=["utf8", "json", "oversized", "duplicate-key"],
)
def test_real_protocol_fault_exits(peer, data):
    peer.raw(data)
    peer.process.wait(timeout=5)
    assert peer.process.returncode == 2


def test_bad_checkout_refuses_before_hello(tmp_path):
    child = subprocess.run(
        [
            sys.executable,
            "-I",
            "-u",
            str(HOST),
            "--expected-interpreter",
            sys.executable,
            "--expected-checkout",
            str(tmp_path),
        ],
        capture_output=True,
        timeout=10,
    )
    assert child.returncode == 3
    assert child.stdout == b""
    assert b"Wrong Mirrorly checkout" in child.stderr


def test_evicted_terminal_still_remembers_execution(intent):
    calls = []

    def service(request):
        calls.append(request)
        return SetupPreflight(request.repository_path, request.config_path)

    peer = Peer(service=service)
    try:
        peer.initialize()
        first = None
        for _ in range(33):
            rid = peer.send("setup.preflight", intent)
            first = first or rid
            assert peer.receive()["payload"]["phase"] == "accepted"
            assert peer.receive()["payload"]["phase"] == "terminal"
        peer.send("status", {"request_id": first})
        record = peer.receive()["payload"]["result"]["request"]
        assert record["state"] == "stale_result_not_cached"
        assert record["application_invoked"] is None and record["reusable"] is False
        assert not record["terminal_available"] and record["terminal"] is None
        peer.send("setup.preflight", intent, rid=first)
        assert peer.receive()["payload"]["error"]["code"] == "stale_request_id"
        assert len(calls) == 33
        assert len(peer.host.terminals) == 32
    finally:
        peer.close()


def test_blocked_stdout_does_not_block_application_or_eof_shutdown(intent):
    blocked, release, finished = threading.Event(), threading.Event(), threading.Event()

    def service(request):
        finished.set()
        return SetupPreflight(request.repository_path, request.config_path)

    peer = Peer(service=service)
    peer.initialize()

    def write(_):
        blocked.set()
        assert release.wait(5)

    peer.host.out.write = write
    try:
        peer.send("setup.preflight", intent)
        assert blocked.wait(2) and finished.wait(2)
        peer.close()  # Bounded transport teardown even though writer is still blocked.
        assert not release.is_set()
    finally:
        release.set()


def test_real_stdout_loss_finishes_readonly_call(intent, tmp_path):
    command = [
        sys.executable,
        "-I",
        "-u",
        str(ROOT / "tests/worker_fixture_host.py"),
        "--expected-interpreter",
        sys.executable,
        "--expected-checkout",
        str(ROOT),
    ]
    child = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            **os.environ,
            "MIRRORLY_TEST_SCENARIO": "blocked",
            "MIRRORLY_TEST_GATE": str(tmp_path),
        },
    )
    try:
        hello = p.parse(child.stdout.readline())
        session = hello["session_id"]
        for kind, rid, payload in (
            ("initialize", "1", {"required_capabilities": []}),
            ("request", "2", {"method": "setup.preflight", "params": intent}),
        ):
            child.stdin.write(p.encode(p.message(kind, session, payload, rid)))
            child.stdin.flush()
            reply = p.parse(child.stdout.readline())
        assert reply["payload"]["phase"] == "accepted"
        child.stdout.close()
        (tmp_path / "release").touch()
        child.wait(timeout=5)
        assert child.returncode == 0  # Channel failure did not terminate the service.
        assert not (Path(intent["target"]) / "MirrorlyRepo").exists()
    finally:
        (tmp_path / "release").touch()
        child.stdin.close()
        child.wait(timeout=5)
        child.stdout.close()
        child.stderr.close()


def test_normal_frame_limit_after_handshake(peer):
    peer.initialize()
    peer.raw(b"x" * p.FRAME_BYTES)
    peer.process.wait(timeout=5)
    assert peer.process.returncode == 2


def test_more_than_4096_requests_one_real_session(peer, intent):
    peer.initialize()
    session = peer.session
    first = peer.send("setup.preflight", intent)
    peer.receive()
    peer.receive()
    assert "max_requests" not in peer.hello["payload"]["limits"]
    for index in range(4200):
        rid = peer.send("status" if index % 2 else "ping")
        reply = peer.receive()
        assert reply["session_id"] == session and reply["request_id"] == rid
        assert reply["payload"]["phase"] == "terminal"
        if index % 2:
            assert reply["payload"]["result"]["highest_seen_request_id"] == rid
    peer.send("status", {"request_id": first})
    assert peer.receive()["payload"]["result"]["request"]["terminal_available"]
    peer.send("setup.preflight", intent, rid=first)
    assert peer.receive()["payload"]["error"] == {
        "kind": "admission",
        "code": "stale_request_id",
        "application_invoked": False,
    }
    peer.send("setup.preflight", intent)
    assert peer.receive()["payload"]["phase"] == "accepted"
    assert peer.receive()["payload"]["phase"] == "terminal"


def test_high_water_gaps_uint64_and_no_replay(intent):
    calls = []

    def service(request):
        calls.append(request)
        return SetupPreflight(request.repository_path, request.config_path)

    peer = Peer(service=service)
    try:
        peer.initialize()
        peer.send("ping", rid="100")
        peer.receive()
        peer.send("setup.preflight", intent, rid="50")  # Never used, but now stale.
        assert peer.receive()["payload"]["error"]["code"] == "stale_request_id"
        peer.send("status", {"request_id": "50"}, rid="101")
        record = peer.receive()["payload"]["result"]["request"]
        assert (
            record["state"] == "stale_result_not_cached" and record["application_invoked"] is None
        )
        maximum = str(2**64 - 1)
        peer.send("ping", rid=maximum)
        assert peer.receive()["payload"]["result"]["reply"] == "pong"
        for rid in ("100", maximum, "1"):
            peer.send("setup.preflight", intent, rid=rid)
            assert peer.receive()["payload"]["error"]["application_invoked"] is False
        assert peer.host.high_water == 2**64 - 1 and not calls
        peer.send("ping", rid=str(2**64))
        assert peer.receive()["message_type"] == "protocol_error"
        assert peer.host.high_water == 2**64 - 1
    finally:
        peer.close()
