"""Phase 1A stdio test peer. No Mirrorly imports or filesystem operations."""

import json
import os
import sys

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 65536


def emit(message_type, request_id, payload):
    frame = {
        "protocol_version": PROTOCOL_VERSION,
        "message_type": message_type,
        "request_id": request_id,
        "payload": payload,
    }
    sys.stdout.buffer.write(
        json.dumps(frame, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    )
    sys.stdout.buffer.flush()


def main():
    print("Phase 1A fake worker started; no backup capabilities.", file=sys.stderr)
    while True:
        raw = sys.stdin.buffer.readline(MAX_FRAME_BYTES + 1)
        if not raw:
            return 0
        if len(raw) > MAX_FRAME_BYTES or not raw.endswith(b"\n"):
            emit(
                "error", None, {"code": "invalid_frame", "message": "Frame too long or incomplete."}
            )
            return 2  # Cannot safely resynchronize an oversized frame.
        request_id = None
        try:
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("Request must be an object.")
            candidate = request.get("request_id")
            if isinstance(candidate, str) and 0 < len(candidate) <= 128:
                request_id = candidate
            if (
                type(request.get("protocol_version")) is not int
                or request["protocol_version"] != PROTOCOL_VERSION
            ):
                raise ValueError("Unsupported protocol_version.")
            if request.get("message_type") != "request" or request_id is None:
                raise ValueError("Expected request with a nonempty request_id.")
            payload = request.get("payload")
            if not isinstance(payload, dict) or not isinstance(payload.get("command"), str):
                raise ValueError("Expected payload.command.")
            command = payload["command"]
        except (ValueError, UnicodeDecodeError) as error:
            emit("error", request_id, {"code": "invalid_request", "message": str(error)})
            continue

        if command == "ping":
            emit("response", request_id, {"reply": "pong"})
        elif command == "version":
            emit(
                "response", request_id, {"worker": "phase1a-fake", "python": sys.version.split()[0]}
            )
        elif command == "test_event":
            emit("event", request_id, {"name": "test_event", "text": "Test event received · 测试"})
            emit("response", request_id, {"reply": "event_sent"})
        elif command == "shutdown":
            emit("response", request_id, {"reply": "goodbye"})
            return 0
        elif command == "test_crash":
            print("Intentional Phase 1A crash (exit 23).", file=sys.stderr, flush=True)
            os._exit(23)
        else:
            emit(
                "error",
                request_id,
                {
                    "code": "unknown_command",
                    "message": "Command is not supported by the fake worker.",
                },
            )


if __name__ == "__main__":
    raise SystemExit(main())
