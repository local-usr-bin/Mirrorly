"""Subprocess tests of the prototype protocol; deliberately independent of core."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

WORKER = Path(__file__).with_name("worker.py")


def request(command, request_id="test"):
    return json.dumps(
        {
            "protocol_version": 1,
            "message_type": "request",
            "request_id": request_id,
            "payload": {"command": command},
        }
    )


def run_frames(*frames):
    result = subprocess.run(
        [sys.executable, "-I", "-u", str(WORKER)],
        input="\n".join(frames) + "\n",
        capture_output=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    return result, [json.loads(line) for line in result.stdout.splitlines()]


def test_ping_version_shutdown():
    result, frames = run_frames(
        request("ping", "p"),
        request("version", "v"),
        request("shutdown", "s"),
        request("ping", "ignored"),
    )
    assert result.returncode == 0
    assert [frame["request_id"] for frame in frames] == ["p", "v", "s"]
    assert frames[0]["payload"] == {"reply": "pong"}
    assert frames[1]["payload"]["worker"] == "phase1a-fake"
    assert frames[1]["payload"]["python"] == sys.version.split()[0]
    assert frames[2]["payload"] == {"reply": "goodbye"}
    assert "fake worker started" in result.stderr


@pytest.mark.parametrize(
    "bad",
    [
        "{",
        "[]",
        "{}",
        "null",
        request("ping").replace('"protocol_version": 1', '"protocol_version": 2'),
        request("ping").replace('"protocol_version": 1', '"protocol_version": true'),
    ],
)
def test_malformed_request_recovers(bad):
    result, frames = run_frames(bad, request("ping"))
    assert result.returncode == 0
    assert frames[0]["message_type"] == "error"
    assert frames[0]["payload"]["code"] == "invalid_request"
    assert frames[1]["payload"] == {"reply": "pong"}


def test_event_before_response_and_unicode():
    result, frames = run_frames(request("test_event", "中文"))
    assert result.returncode == 0
    assert [frame["message_type"] for frame in frames] == ["event", "response"]
    assert all(frame["request_id"] == "中文" for frame in frames)
    assert "测试" in frames[0]["payload"]["text"]


def test_unknown_command_is_structured_error():
    result, frames = run_frames(request("backup"))
    assert result.returncode == 0
    assert frames[0]["payload"]["code"] == "unknown_command"


def test_controlled_crash():
    result, frames = run_frames(request("test_crash"))
    assert result.returncode == 23
    assert frames == []
    assert "Intentional Phase 1A crash" in result.stderr


def test_oversized_frame_exits():
    result, frames = run_frames("x" * 65536)
    assert result.returncode == 2
    assert frames[0]["payload"]["code"] == "invalid_frame"
