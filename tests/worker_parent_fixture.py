"""Disposable client process for proving orphan lifetime; never a worker method."""

import json
import os
import subprocess
import sys
from pathlib import Path

from mirrorly.worker import protocol as p

root = Path(__file__).resolve().parents[1]
intent = json.loads(sys.stdin.readline())
method = os.environ.get("MIRRORLY_TEST_METHOD", "setup.preflight")
child = subprocess.Popen(
    [
        sys.executable,
        "-I",
        "-u",
        str(root / "tests/worker_fixture_host.py"),
        "--expected-interpreter",
        sys.executable,
        "--expected-checkout",
        str(root),
    ],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    env={
        **os.environ,
        "MIRRORLY_TEST_SCENARIO": "create_blocked_config"
        if method == "setup.create"
        else "blocked",
    },
)
session = p.parse(child.stdout.readline())["session_id"]
for kind, rid, payload in (
    ("initialize", "1", {"required_capabilities": []}),
    ("request", "2", {"method": "status", "params": {}}),
    ("request", "3", {"method": method, "params": intent}),
):
    child.stdin.write(p.encode(p.message(kind, session, payload, rid)))
    child.stdin.flush()
    reply = p.parse(child.stdout.readline())
    if rid == "2":
        gate = reply["payload"]["result"]["lifecycle_gate"]
assert reply["payload"]["phase"] == "accepted"
print(json.dumps({"pid": child.pid, "gate": gate, "session": session}), flush=True)
# Parent owns all three pipe endpoints. Test terminates this process; the child
# must keep its mutex while finishing the already-entered test service.
sys.stdin.readline()
