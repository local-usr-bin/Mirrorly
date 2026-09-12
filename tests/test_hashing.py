"""T-01 测试：哈希算法抽象（ADR-013：BLAKE3 主用，SHA-256 兜底）。"""

import hashlib

import pytest

from mirrorly import hashing


def test_default_algorithm_in_dev_env() -> None:
    """开发环境已安装 blake3，默认算法应为 blake3。"""
    assert hashing.default_algorithm() == "blake3"


def test_new_hasher_matches_blake3_reference() -> None:
    """BLAKE3 哈希值应与官方参考向量一致。"""
    h = hashing.new_hasher("blake3")
    h.update(b"abc")
    assert h.hexdigest() == ("6437b3ac38465133ffb63b75273a8db548c558465d79db03fd359c6cd5bd9d85")


def test_sha256_hasher_matches_hashlib() -> None:
    """SHA-256 兜底路径应与标准库一致。"""
    h = hashing.new_hasher("sha256")
    h.update(b"abc")
    assert h.hexdigest() == hashlib.sha256(b"abc").hexdigest()


def test_fallback_to_sha256_when_blake3_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """blake3 不可用时默认算法降级为 sha256。"""
    monkeypatch.setattr(hashing, "_blake3", None)
    assert hashing.default_algorithm() == "sha256"
    assert hashing.blake3_available() is False


def test_blake3_unavailable_but_requested_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式要求 blake3 但不可用时应抛出明确错误。"""
    monkeypatch.setattr(hashing, "_blake3", None)
    with pytest.raises(hashing.HashError):
        hashing.new_hasher("blake3")


def test_unknown_algorithm_raises() -> None:
    with pytest.raises(hashing.HashError):
        hashing.new_hasher("md5")


def test_hash_file_streaming(tmp_path) -> None:
    """流式文件哈希与一次性哈希结果一致（TR-6：大文件分块读取）。"""
    data = bytes(range(256)) * 5000  # ~1.28 MB，跨多个分块
    f = tmp_path / "data.bin"
    f.write_bytes(data)
    h = hashing.new_hasher("blake3")
    h.update(data)
    assert hashing.hash_file(f, "blake3", chunk_size=4096) == h.hexdigest()
