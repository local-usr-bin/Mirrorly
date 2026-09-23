"""Bounded production v1 framing, independent from the Phase 1A protocol."""

from __future__ import annotations

import json
import math
import re

IDENTITY = "mirrorly.worker"
VERSION = {"major": 1, "minor": 0}
HANDSHAKE_BYTES = 65536
FRAME_BYTES = 1048576  # Includes LF.
MAX_DEPTH = 32
MAX_COLLECTION = 4096
MAX_NODES = 16384
MAX_STRING = 32768
KINDS = {"hello", "initialize", "request", "response", "event", "protocol_error"}
FIELDS = {
    "protocol",
    "protocol_version",
    "message_type",
    "session_id",
    "request_id",
    "operation_id",
    "interaction_id",
    "payload",
}


class ProtocolFault(ValueError):
    pass


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ProtocolFault("Duplicate JSON key")
        result[key] = value
    return result


def _constant(value):
    raise ProtocolFault(f"Non-finite JSON number: {value}")


def validate_tree(value):
    stack = [(value, 1)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if depth > MAX_DEPTH or nodes > MAX_NODES:
            raise ProtocolFault("JSON depth/node limit exceeded")
        if isinstance(item, (dict, list)):
            if len(item) > MAX_COLLECTION:
                raise ProtocolFault("JSON collection limit exceeded")
            if isinstance(item, dict):
                stack.extend((key, depth + 1) for key in item)
                item = item.values()
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            if len(item) > MAX_STRING:
                raise ProtocolFault("JSON string limit exceeded")
            # Reject escaped lone surrogates as well as invalid raw UTF-8.
            item.encode("utf-8", errors="strict")
        elif isinstance(item, float) and not math.isfinite(item):
            raise ProtocolFault("Non-finite JSON number")


def parse(raw: bytes) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        validate_tree(value)
        if not isinstance(value, dict) or set(value) != FIELDS:
            raise ProtocolFault("Invalid envelope fields")
        if value["protocol"] != IDENTITY or value["message_type"] not in KINDS:
            raise ProtocolFault("Unsupported protocol identity/message type")
        version = value["protocol_version"]
        if version is not None and (
            not isinstance(version, dict)
            or set(version) != {"major", "minor"}
            or any(type(version[k]) is not int or version[k] < 0 for k in version)
        ):
            raise ProtocolFault("Invalid version shape")
        for key in ("session_id", "request_id", "operation_id", "interaction_id"):
            field = value[key]
            if field is not None and (not isinstance(field, str) or not 0 < len(field) <= 128):
                raise ProtocolFault(f"Invalid {key}")
        if not isinstance(value["payload"], dict):
            raise ProtocolFault("payload must be an object")
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ProtocolFault(str(exc)) from exc


def request_number(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value):
        raise ProtocolFault("request_id must be a canonical positive decimal uint64")
    number = int(value)
    if number > 2**64 - 1:
        raise ProtocolFault("request_id exceeds uint64")
    return number


def message(kind, session, payload, request=None, operation=None, *, version=VERSION):
    return {
        "protocol": IDENTITY,
        "protocol_version": version,
        "message_type": kind,
        "session_id": session,
        "request_id": request,
        "operation_id": operation,
        "interaction_id": None,
        "payload": payload,
    }


def encode(value, limit=FRAME_BYTES):
    validate_tree(value)
    data = (
        json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode(
            "utf-8"
        )
        + b"\n"
    )
    if len(data) > limit:
        raise ProtocolFault("Outbound frame exceeds limit")
    return data


class Framer:
    def __init__(self):
        self.pending = bytearray()

    def feed(self, data: bytes, limit: int):
        for byte in data:
            if len(self.pending) >= limit - 1 and byte != 10:
                raise ProtocolFault("Frame exceeds byte limit")
            if byte == 10:
                if not self.pending:
                    raise ProtocolFault("Empty frame")
                yield bytes(self.pending)
                self.pending.clear()
            else:
                self.pending.append(byte)

    def finish(self):
        if self.pending:
            raise ProtocolFault("Truncated frame")
