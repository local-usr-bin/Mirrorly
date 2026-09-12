"""T-04 测试：Manifest 管理（ADR-010：每快照独立 JSON、原子提交、状态流转）。"""

import json
import os

import pytest

from mirrorly.manifest import (
    FORMAT_VERSION,
    ManifestError,
    create_manifest,
    list_manifests,
    load_manifest,
    mark_complete,
    write_manifest,
)
from mirrorly.repo import VolumeInfo, init_repo
from mirrorly.scan import scan_source

_NTFS = VolumeInfo(label="BackupDisk", serial="A1B2C3D4", filesystem="NTFS")


def _repo(tmp_path):
    return init_repo(tmp_path / "target", volume_info_provider=lambda p: _NTFS)


def _write(path, data: bytes = b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _scan(tmp_path):
    src = tmp_path / "src"
    _write(src / "a.txt", b"aaa")
    _write(src / "sub" / "b.txt", b"bb")
    (src / "empty_dir").mkdir()
    return src, scan_source(src, ()).entries


class TestCreate:
    def test_create_manifest_fields_and_entries(self, tmp_path) -> None:
        src, current = _scan(tmp_path)
        m = create_manifest(
            "snap1", str(src), "blake3", current, hashes={"a.txt": "sha-a", "sub/b.txt": "sha-b"}
        )
        assert m.snapshot_id == "snap1"
        assert m.status == "incomplete"  # 新建为 incomplete（完成后显式流转）
        assert m.hash_algorithm == "blake3"
        assert m.source_root == str(src)
        files = {e.path: e for e in m.entries if not e.is_dir}
        dirs = {e.path for e in m.entries if e.is_dir}
        assert set(files) == {"a.txt", "sub/b.txt"}
        assert files["a.txt"].size == 3 and files["a.txt"].sha == "sha-a"
        assert files["sub/b.txt"].sha == "sha-b"
        assert {"sub", "empty_dir"} <= dirs
        assert m.stats.files == 2 and m.stats.dirs == 2
        assert m.stats.total_bytes == 5

    def test_missing_hash_recorded_as_none(self, tmp_path) -> None:
        src, current = _scan(tmp_path)
        m = create_manifest("snap1", str(src), "blake3", current)
        files = {e.path: e for e in m.entries if not e.is_dir}
        assert files["a.txt"].sha is None  # 哈希由调用方提供（T-06），缺失记 None


class TestAtomicWrite:
    def test_write_then_no_tmp_left(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        src, current = _scan(tmp_path)
        m = create_manifest("snap1", str(src), "blake3", current)
        path = write_manifest(repo, m)
        assert path == repo.path / "manifests" / "snap1.json"
        assert path.exists()
        assert list((repo.path / "manifests.tmp").iterdir()) == []  # 临时区无残留

    def test_write_failure_leaves_no_complete_manifest(self, tmp_path, monkeypatch) -> None:
        repo = _repo(tmp_path)
        src, current = _scan(tmp_path)
        m = mark_complete(create_manifest("snap1", str(src), "blake3", current))

        def _boom(*_a, **_k):
            raise OSError("simulated replace failure")

        monkeypatch.setattr(os, "replace", _boom)
        with pytest.raises(ManifestError, match="写入失败"):
            write_manifest(repo, m)
        # 原子性：失败时不存在"半个 complete manifest"
        assert not (repo.path / "manifests" / "snap1.json").exists()


class TestStatusFlow:
    def test_incomplete_not_loadable_as_complete(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        src, current = _scan(tmp_path)
        m = create_manifest("snap1", str(src), "blake3", current)
        write_manifest(repo, m)
        with pytest.raises(ManifestError, match="incomplete"):
            load_manifest(repo, "snap1", require_complete=True)

    def test_mark_complete_transition(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        src, current = _scan(tmp_path)
        m = create_manifest("snap1", str(src), "blake3", current)
        assert m.status == "incomplete"
        write_manifest(repo, mark_complete(m))
        loaded = load_manifest(repo, "snap1", require_complete=True)
        assert loaded.status == "complete"
        assert loaded.snapshot_id == "snap1"

    def test_list_manifests_shows_status(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        src, current = _scan(tmp_path)
        write_manifest(repo, mark_complete(create_manifest("snap1", str(src), "blake3", current)))
        write_manifest(repo, create_manifest("snap2", str(src), "blake3", current))
        listing = list_manifests(repo)
        assert [(s.snapshot_id, s.status) for s in listing] == [
            ("snap1", "complete"),
            ("snap2", "incomplete"),
        ]


class TestSerialization:
    def test_unicode_roundtrip(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        src = tmp_path / "src"
        _write(src / "你好_数据_ñ🙂.txt", "中文".encode())
        current = scan_source(src, ()).entries
        write_manifest(repo, mark_complete(create_manifest("s1", str(src), "blake3", current)))
        loaded = load_manifest(repo, "s1")
        paths = {e.path for e in loaded.entries}
        assert "你好_数据_ñ🙂.txt" in paths

    def test_json_content_stable(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        src, current = _scan(tmp_path)
        m = create_manifest("snap1", str(src), "blake3", current)
        write_manifest(repo, m)
        raw1 = (repo.path / "manifests" / "snap1.json").read_text("utf-8")
        # 同一对象再次序列化，内容逐字节一致（可 diff、可校验）
        write_manifest(repo, load_manifest(repo, "snap1"))
        raw2 = (repo.path / "manifests" / "snap1.json").read_text("utf-8")
        assert raw1 == raw2
        data = json.loads(raw1)
        assert data["format_version"] == FORMAT_VERSION
        assert data["status"] == "incomplete"

    def test_load_missing_raises(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        with pytest.raises(ManifestError, match="不存在"):
            load_manifest(repo, "nope")

    def test_incompatible_format_version_raises(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        src, current = _scan(tmp_path)
        write_manifest(repo, create_manifest("snap1", str(src), "blake3", current))
        f = repo.path / "manifests" / "snap1.json"
        data = json.loads(f.read_text("utf-8"))
        data["format_version"] = 999
        f.write_text(json.dumps(data), "utf-8")
        with pytest.raises(ManifestError, match="格式版本"):
            load_manifest(repo, "snap1")

    def test_snapshot_id_mismatch_rejected_on_write(self, tmp_path) -> None:
        """manifest 与快照 id 对应关系：id 只能匹配自身文件名，不允许空 id。"""
        src, current = _scan(tmp_path)
        with pytest.raises(ManifestError):
            create_manifest("", str(src), "blake3", current)


class TestScale:
    def test_large_manifest_roundtrip(self, tmp_path) -> None:
        """2 万条目读写冒烟（记录耗时，不设硬阈值）。"""
        import time

        repo = _repo(tmp_path)
        from mirrorly.scan import ScannedEntry

        current = {
            f"dir{i % 100}/file{i}.txt": ScannedEntry(f"dir{i % 100}/file{i}.txt", i, 1000, False)
            for i in range(20000)
        }
        m = create_manifest("big", "src", "blake3", current)
        t0 = time.perf_counter()
        write_manifest(repo, m)
        loaded = load_manifest(repo, "big")
        elapsed = time.perf_counter() - t0
        assert len(loaded.entries) == 20000
        print(f"\n20k entries write+load: {elapsed:.2f}s")
