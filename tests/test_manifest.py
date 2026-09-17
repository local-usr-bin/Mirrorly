"""T-04 测试：Manifest 管理（ADR-010：每快照独立 JSON、原子提交、状态流转）。"""

import json
import os
from dataclasses import asdict, replace

import pytest

from mirrorly.lifecycle import parse_snapshot_sequence
from mirrorly.manifest import (
    FORMAT_VERSION,
    LEGACY_FORMAT_VERSION,
    ManifestError,
    list_manifests,
    load_manifest,
    mark_complete,
    write_manifest,
)
from mirrorly.manifest import create_manifest as _create_manifest
from mirrorly.repo import VolumeInfo, init_repo
from mirrorly.scan import scan_source

_NTFS = VolumeInfo(label="BackupDisk", serial="A1B2C3D4", filesystem="NTFS")
_V2_ID_0 = "2026-09-17_120000-s00000000000000000000-11111111111141118111111111111111"
_V2_ID_1 = "2026-09-17_120001-s00000000000000000001-22222222222242228222222222222222"


def create_manifest(*args, **kwargs):
    if "lifecycle_seq" not in kwargs:
        kwargs["lifecycle_seq"] = parse_snapshot_sequence(args[0]) or 0
    return _create_manifest(*args, **kwargs)


def _legacy_manifest(snapshot_id, *args, **kwargs):
    manifest = _create_manifest(_V2_ID_0, *args, lifecycle_seq=0, **kwargs)
    return replace(
        manifest,
        snapshot_id=snapshot_id,
        lifecycle_seq=None,
        format_version=LEGACY_FORMAT_VERSION,
    )


def _write_legacy_manifest(repo, manifest):
    """Write raw v1 JSON for loader compatibility tests only."""

    assert manifest.format_version == LEGACY_FORMAT_VERSION
    data = {
        "format_version": LEGACY_FORMAT_VERSION,
        "snapshot_id": manifest.snapshot_id,
        "created_at": manifest.created_at,
        "source_root": manifest.source_root,
        "hash_algorithm": manifest.hash_algorithm,
        "status": manifest.status,
        "stats": asdict(manifest.stats),
        "entries": [
            {
                "path": entry.path,
                "type": "dir" if entry.is_dir else "file",
                "size": entry.size,
                "mtime_ns": entry.mtime_ns,
                "sha": entry.sha,
            }
            for entry in manifest.entries
        ],
    }
    path = repo.path / "manifests" / f"{manifest.snapshot_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


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
            _V2_ID_0,
            str(src),
            "blake3",
            current,
            hashes={"a.txt": "sha-a", "sub/b.txt": "sha-b"},
        )
        assert m.snapshot_id == _V2_ID_0
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
        m = create_manifest(_V2_ID_0, str(src), "blake3", current)
        files = {e.path: e for e in m.entries if not e.is_dir}
        assert files["a.txt"].sha is None  # 哈希由调用方提供（T-06），缺失记 None


class TestAtomicWrite:
    def test_write_then_no_tmp_left(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        src, current = _scan(tmp_path)
        m = create_manifest(_V2_ID_0, str(src), "blake3", current)
        path = write_manifest(repo, m)
        assert path == repo.path / "manifests" / f"{_V2_ID_0}.json"
        assert path.exists()
        assert list((repo.path / "manifests.tmp").iterdir()) == []  # 临时区无残留

    def test_write_failure_leaves_no_complete_manifest(self, tmp_path, monkeypatch) -> None:
        repo = _repo(tmp_path)
        src, current = _scan(tmp_path)
        m = mark_complete(create_manifest(_V2_ID_0, str(src), "blake3", current))

        def _boom(*_a, **_k):
            raise OSError("simulated replace failure")

        monkeypatch.setattr(os, "replace", _boom)
        with pytest.raises(ManifestError, match="写入失败"):
            write_manifest(repo, m)
        # 原子性：失败时不存在"半个 complete manifest"
        assert not (repo.path / "manifests" / f"{_V2_ID_0}.json").exists()


class TestStatusFlow:
    def test_incomplete_not_loadable_as_complete(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        src, current = _scan(tmp_path)
        m = create_manifest(_V2_ID_0, str(src), "blake3", current)
        write_manifest(repo, m)
        with pytest.raises(ManifestError, match="incomplete"):
            load_manifest(repo, _V2_ID_0, require_complete=True)

    def test_mark_complete_transition(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        src, current = _scan(tmp_path)
        m = create_manifest(_V2_ID_0, str(src), "blake3", current)
        assert m.status == "incomplete"
        write_manifest(repo, mark_complete(m))
        loaded = load_manifest(repo, _V2_ID_0, require_complete=True)
        assert loaded.status == "complete"
        assert loaded.snapshot_id == _V2_ID_0

    def test_list_manifests_shows_status(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        src, current = _scan(tmp_path)
        write_manifest(
            repo,
            mark_complete(create_manifest(_V2_ID_0, str(src), "blake3", current)),
        )
        write_manifest(repo, create_manifest(_V2_ID_1, str(src), "blake3", current))
        listing = list_manifests(repo)
        assert [(s.snapshot_id, s.status) for s in listing] == [
            (_V2_ID_0, "complete"),
            (_V2_ID_1, "incomplete"),
        ]


class TestSerialization:
    def test_unicode_roundtrip(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        src = tmp_path / "src"
        _write(src / "你好_数据_ñ🙂.txt", "中文".encode())
        current = scan_source(src, ()).entries
        write_manifest(repo, mark_complete(create_manifest(_V2_ID_0, str(src), "blake3", current)))
        loaded = load_manifest(repo, _V2_ID_0)
        paths = {e.path for e in loaded.entries}
        assert "你好_数据_ñ🙂.txt" in paths

    def test_json_content_stable(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        src, current = _scan(tmp_path)
        snapshot_id = _V2_ID_0
        m = create_manifest(snapshot_id, str(src), "blake3", current, lifecycle_seq=0)
        write_manifest(repo, m)
        raw1 = (repo.path / "manifests" / f"{snapshot_id}.json").read_text("utf-8")
        # 同一对象再次序列化，内容逐字节一致（可 diff、可校验）
        write_manifest(repo, load_manifest(repo, snapshot_id))
        raw2 = (repo.path / "manifests" / f"{snapshot_id}.json").read_text("utf-8")
        assert raw1 == raw2
        data = json.loads(raw1)
        assert data["format_version"] == FORMAT_VERSION
        assert data["lifecycle_seq"] == 0
        assert data["status"] == "incomplete"

    def test_v1_loader_keeps_sequence_absent(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        src, current = _scan(tmp_path)
        legacy = _legacy_manifest("2026-09-16_120000-01", str(src), "blake3", current)
        _write_legacy_manifest(repo, legacy)
        loaded = load_manifest(repo, "2026-09-16_120000-01")
        assert loaded.format_version == LEGACY_FORMAT_VERSION
        assert loaded.lifecycle_seq is None
        assert loaded.resumed_from_snapshot_id is None

    def test_production_writer_rejects_v1_without_publishing(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        src, current = _scan(tmp_path)
        snapshot_id = "2026-09-16_120000-01"
        legacy = _legacy_manifest(snapshot_id, str(src), "blake3", current)

        with pytest.raises(ManifestError, match="只允许写入 manifest v2"):
            write_manifest(repo, legacy)

        assert not (repo.path / "manifests" / f"{snapshot_id}.json").exists()
        assert not (repo.path / "manifests.tmp" / f"{snapshot_id}.json.tmp").exists()

    @pytest.mark.parametrize("lifecycle_seq", [None, -1, 2**64, True, "1"])
    def test_v2_invalid_sequence_rejected(self, tmp_path, lifecycle_seq) -> None:
        _repo(tmp_path)
        src, current = _scan(tmp_path)
        snapshot_id = _V2_ID_0
        with pytest.raises(ManifestError, match="lifecycle_seq"):
            create_manifest(
                snapshot_id,
                str(src),
                "blake3",
                current,
                lifecycle_seq=lifecycle_seq,
            )

    def test_v2_id_sequence_mismatch_rejected(self, tmp_path) -> None:
        _repo(tmp_path)
        src, current = _scan(tmp_path)
        snapshot_id = "2026-09-17_120000-s00000000000000000001-11111111111141118111111111111111"
        with pytest.raises(ManifestError, match="不一致"):
            create_manifest(snapshot_id, str(src), "blake3", current, lifecycle_seq=0)

    def test_v2_requires_sequence_bearing_uuid4_id(self, tmp_path) -> None:
        _repo(tmp_path)
        src, current = _scan(tmp_path)
        with pytest.raises(ManifestError, match="未携带 lifecycle sequence"):
            create_manifest("snap1", str(src), "blake3", current, lifecycle_seq=0)

    def test_load_missing_raises(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        with pytest.raises(ManifestError, match="不存在"):
            load_manifest(repo, "nope")

    def test_incompatible_format_version_raises(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        src, current = _scan(tmp_path)
        write_manifest(repo, create_manifest(_V2_ID_0, str(src), "blake3", current))
        f = repo.path / "manifests" / f"{_V2_ID_0}.json"
        data = json.loads(f.read_text("utf-8"))
        data["format_version"] = 999
        f.write_text(json.dumps(data), "utf-8")
        with pytest.raises(ManifestError, match="格式版本"):
            load_manifest(repo, _V2_ID_0)

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
        m = create_manifest(_V2_ID_0, "src", "blake3", current)
        t0 = time.perf_counter()
        write_manifest(repo, m)
        loaded = load_manifest(repo, _V2_ID_0)
        elapsed = time.perf_counter() - t0
        assert len(loaded.entries) == 20000
        print(f"\n20k entries write+load: {elapsed:.2f}s")
