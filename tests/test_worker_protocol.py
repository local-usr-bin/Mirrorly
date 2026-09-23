"""Production framing tests; no dependency on the historical fake protocol."""

import json

import pytest

from mirrorly.worker import protocol as p


def envelope(**changes):
    return p.message("request", "session", {"method": "ping", "params": {}}, "1") | changes


def test_split_coalesced_unicode_and_limits():
    value = envelope(payload={"text": "中文🌸"})
    raw = p.encode(value)
    parser = p.Framer()
    frames = []
    for byte in raw + raw:
        frames.extend(parser.feed(bytes([byte]), p.FRAME_BYTES))
    parser.finish()
    assert [p.parse(f) for f in frames] == [value, value]
    for limit in (p.HANDSHAKE_BYTES, p.FRAME_BYTES):
        parser = p.Framer()
        assert list(parser.feed(b"x" * (limit - 1) + b"\n", limit)) == [b"x" * (limit - 1)]
        with pytest.raises(p.ProtocolFault):
            list(p.Framer().feed(b"x" * limit, limit))


@pytest.mark.parametrize(
    "raw",
    [b"\xff", b"{", b"[]", b"\xef\xbb\xbf{}", b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":"\\ud800"}'],
)
def test_malformed_json_and_utf8(raw):
    with pytest.raises(p.ProtocolFault):
        p.parse(raw)


@pytest.mark.parametrize(
    "change",
    [
        {"protocol": "fake"},
        {"protocol_version": 1},
        {"protocol_version": {"major": True, "minor": 0}},
        {"protocol_version": {"major": 1, "minor": 0.0}},
        {"payload": []},
        {"message_type": "interaction_request"},
    ],
)
def test_strict_envelope(change):
    with pytest.raises(p.ProtocolFault):
        p.parse(json.dumps(envelope(**change)).encode())


def test_collection_depth_string_and_node_limits():
    deep = {}
    for _ in range(33):
        deep = {"x": deep}
    for payload in (
        deep,
        {"x": [0] * 4097},
        {"x": "x" * 32769},
        {"x": [[0] * 4096 for _ in range(5)]},
    ):
        with pytest.raises(p.ProtocolFault):
            p.parse(json.dumps(envelope(payload=payload)).encode())


@pytest.mark.parametrize(
    "value", [None, True, 1, "0", "01", "-1", "+1", "1.0", "1e2", "１", " 1", "1\n", str(2**64)]
)
def test_request_id(value):
    with pytest.raises(p.ProtocolFault):
        p.request_number(value)


def test_request_id_valid_boundaries():
    for value in (1, 2**63, 2**64 - 1):
        assert p.request_number(str(value)) == value


def test_empty_and_truncated_frames():
    with pytest.raises(p.ProtocolFault):
        list(p.Framer().feed(b"\n", 10))
    parser = p.Framer()
    list(parser.feed(b"{", 10))
    with pytest.raises(p.ProtocolFault):
        parser.finish()


def test_normal_frame_larger_than_handshake_and_numeric_overflow():
    value = envelope(payload={"strings": ["x" * 32768] * 3})
    raw = p.encode(value)
    assert p.HANDSHAKE_BYTES < len(raw) < p.FRAME_BYTES
    assert p.parse(next(p.Framer().feed(raw, p.FRAME_BYTES))) == value
    with pytest.raises(p.ProtocolFault):
        list(p.Framer().feed(raw, p.HANDSHAKE_BYTES))
    with pytest.raises(p.ProtocolFault):
        p.parse(b'{"value":1e9999}')
