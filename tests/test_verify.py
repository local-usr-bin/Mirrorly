"""T-06 完整性校验测试（对应 MVP_TASKS T-06 验收标准）。

覆盖：verify_snapshot 全量/quick 模式、写入即校验（verify_writes）、
以及 T-09 组合流程预演（linked 文件 sha 从旧 manifest 结转）。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from mirrorly import snapshot as snapshot_mod
from mirrorly import verify as verify_mod
from mirrorly.hashing import hash_file
from mirrorly.manifest import (
    LEGACY_FORMAT_VERSION,
    mark_complete,
)
from mirrorly.manifest import create_manifest as _create_manifest
from mirrorly.manifest import write_manifest as _write_manifest
from mirrorly.repo import VolumeInfo, init_repo
from mirrorly.scan import PreviousEntry, detect_changes, scan_source, to_long_path
from mirrorly.snapshot import SnapshotError, write_snapshot
from mirrorly.snapshot import _copy_file_atomic as real_copy
from mirrorly.verify import verify_snapshot

_NTFS = VolumeInfo(label="BackupDisk", serial="A1B2C3D4", filesystem="NTFS")


def create_manifest(*args, **kwargs):
    if "lifecycle_seq" in kwargs:
        return _create_manifest(*args, **kwargs)
    snapshot_id = args[0]
    placeholder = "2000-01-01_000000-s00000000000000000000-00000000000040008000000000000000"
    manifest = _create_manifest(placeholder, *args[1:], lifecycle_seq=0, **kwargs)
    return replace(
        manifest,
        snapshot_id=snapshot_id,
        lifecycle_seq=None,
        format_version=LEGACY_FORMAT_VERSION,
    )


def _write_legacy_manifest(repo, manifest):
    """Write raw v1 JSON for loader/verify compatibility tests only."""

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


def write_manifest(repo, manifest):
    if manifest.format_version == LEGACY_FORMAT_VERSION:
        return _write_legacy_manifest(repo, manifest)
    return _write_manifest(repo, manifest)


def _ntfs_provider(_path):
    return _NTFS


def _write(path: Path, data: bytes = b"x", mtime_ns: int | None = None) -> Path:
    lp = to_long_path(path)
    Path(lp).parent.mkdir(parents=True, exist_ok=True)
    Path(lp).write_bytes(data)
    if mtime_ns is not None:
        os.utime(lp, ns=(mtime_ns, mtime_ns))
    return path


def _init_repo(path: Path):
    return init_repo(path, volume_info_provider=_ntfs_provider)


def _make_backup(repo, src: Path, snap_id: str, *, with_hashes: bool = True):
    """做一次写入即校验的备份并提交 complete manifest，返回 SnapshotResult。"""
    current = scan_source(src, ()).entries
    changes = detect_changes(src, current, None)
    result = write_snapshot(
        src, repo, current, changes, snapshot_id=snap_id, verify_writes=with_hashes
    )
    manifest = create_manifest(
        snap_id,
        str(src),
        repo.hash_algorithm,
        current,
        hashes=result.hashes if with_hashes else None,
    )
    write_manifest(repo, mark_complete(manifest))
    return result


def _issue_kinds(report) -> dict[str, str]:
    return {i.path: i.kind for i in report.issues}


class TestVerifySnapshot:
    def test_clean_snapshot_passes(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "a.txt", b"aaa", mtime_ns=1000)
        _write(src / "sub" / "b.txt", b"bbbb", mtime_ns=2000)
        _make_backup(repo, src, "snap1")

        report = verify_snapshot(repo, "snap1")

        assert report.ok
        assert report.checked_files == 2 and report.checked_dirs == 1
        assert report.hashed_files == 2 and report.unhashed_entries == 0
        assert report.issues == () and report.extras == ()

    def test_byte_tampering_detected_as_corrupt(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "a.txt", b"aaaa", mtime_ns=1000)
        _make_backup(repo, src, "snap1")
        victim = repo.path / "snapshots" / "snap1" / "a.txt"
        data = bytearray(victim.read_bytes())
        data[0] ^= 0xFF  # 翻转一个字节，保持大小不变
        victim.write_bytes(bytes(data))

        report = verify_snapshot(repo, "snap1")

        assert not report.ok
        assert _issue_kinds(report) == {"a.txt": "corrupt"}

    def test_deleted_file_detected_as_missing(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "a.txt", b"aaa", mtime_ns=1000)
        _make_backup(repo, src, "snap1")
        (repo.path / "snapshots" / "snap1" / "a.txt").unlink()

        report = verify_snapshot(repo, "snap1")

        assert not report.ok
        assert _issue_kinds(report) == {"a.txt": "missing"}

    def test_deleted_dir_detected_as_missing(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "sub" / "b.txt", b"bbb", mtime_ns=1000)
        _make_backup(repo, src, "snap1")
        (repo.path / "snapshots" / "snap1" / "sub" / "b.txt").unlink()
        (repo.path / "snapshots" / "snap1" / "sub").rmdir()

        report = verify_snapshot(repo, "snap1")

        kinds = _issue_kinds(report)
        assert kinds["sub"] == "missing"
        assert kinds["sub/b.txt"] == "missing"

    def test_size_mismatch_detected(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "a.txt", b"aaaa", mtime_ns=1000)
        _make_backup(repo, src, "snap1")
        (repo.path / "snapshots" / "snap1" / "a.txt").write_bytes(b"aaaaaaaa")

        report = verify_snapshot(repo, "snap1")

        assert not report.ok
        assert _issue_kinds(report) == {"a.txt": "size_mismatch"}

    def test_quick_mode_never_hashes(self, tmp_path, monkeypatch) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "a.txt", b"aaaa", mtime_ns=1000)
        _make_backup(repo, src, "snap1")

        def forbidden(*args, **kwargs):
            raise AssertionError("quick 模式不得调用 hash_file")

        monkeypatch.setattr(verify_mod, "hash_file", forbidden)
        report = verify_snapshot(repo, "snap1", quick=True)
        assert report.ok and report.hashed_files == 0
        monkeypatch.undo()

        # quick 语义边界：同大小内容篡改不检出（只查存在性/类型/大小）
        (repo.path / "snapshots" / "snap1" / "a.txt").write_bytes(b"zzzz")
        monkeypatch.setattr(verify_mod, "hash_file", forbidden)
        assert verify_snapshot(repo, "snap1", quick=True).ok

    def test_unhashed_entries_skipped_and_counted(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "a.txt", b"aaa", mtime_ns=1000)
        _make_backup(repo, src, "snap1", with_hashes=False)  # 模拟 T-06 前的旧快照

        report = verify_snapshot(repo, "snap1")

        assert report.ok
        assert report.unhashed_entries == 1 and report.hashed_files == 0

    def test_extra_files_reported_but_not_failing(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "a.txt", b"aaa", mtime_ns=1000)
        _make_backup(repo, src, "snap1")
        (repo.path / "snapshots" / "snap1" / "stray.txt").write_bytes(b"stray")

        report = verify_snapshot(repo, "snap1")

        assert report.ok
        assert report.extras == ("stray.txt",)

    def test_unicode_and_long_path(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "报告 2026-09-13.txt", "中文内容".encode(), mtime_ns=1000)
        deep = src
        for i in range(12):
            deep = deep / f"深目录层级-{i:02d}-这是一个很长的目录名"
        _write(deep / "深层文件.txt", b"deep", mtime_ns=1001)
        assert len(str(deep / "深层文件.txt")) > 260
        _make_backup(repo, src, "snap1")

        assert verify_snapshot(repo, "snap1").ok


class TestWriteTimeVerification:
    def test_verify_writes_returns_correct_hashes(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "a.txt", b"aaa", mtime_ns=1000)
        _write(src / "b.txt", b"bbbb", mtime_ns=2000)

        result = _make_backup(repo, src, "snap1")

        assert sorted(result.hashes) == ["a.txt", "b.txt"]
        assert result.hashes["a.txt"] == hash_file(to_long_path(src / "a.txt"), repo.hash_algorithm)

    def test_retry_once_then_raise(self, tmp_path, monkeypatch) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "a.txt", b"aaa", mtime_ns=1000)
        current = scan_source(src, ()).entries
        changes = detect_changes(src, current, None)

        copies = {"n": 0}

        def counting_copy(src_file, dst, entry):
            copies["n"] += 1
            return real_copy(src_file, dst, entry)

        # 目标端 hash 恒坏 → 每次校验都不一致
        real_hash = snapshot_mod.hash_file

        def bad_dst_hash(path, algorithm=None, **kw):
            if "snapshots" in str(path):
                return "0" * 64
            return real_hash(path, algorithm, **kw)

        monkeypatch.setattr(snapshot_mod, "_copy_file_atomic", counting_copy)
        monkeypatch.setattr(snapshot_mod, "hash_file", bad_dst_hash)
        with pytest.raises(SnapshotError, match="校验"):
            write_snapshot(src, repo, current, changes, snapshot_id="snap1", verify_writes=True)
        assert copies["n"] == 2  # 首次 + 恰好重试一次

    def test_retry_succeeds_on_second_attempt(self, tmp_path, monkeypatch) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "a.txt", b"aaa", mtime_ns=1000)
        current = scan_source(src, ()).entries
        changes = detect_changes(src, current, None)

        real_hash = snapshot_mod.hash_file
        state = {"poisoned": True}

        def flaky_hash(path, algorithm=None, **kw):
            if state["poisoned"] and "snapshots" in str(path):
                state["poisoned"] = False  # 只毒化第一次目标端 hash
                return "0" * 64
            return real_hash(path, algorithm, **kw)

        monkeypatch.setattr(snapshot_mod, "hash_file", flaky_hash)
        result = write_snapshot(
            src, repo, current, changes, snapshot_id="snap1", verify_writes=True
        )
        assert result.hashes["a.txt"] == real_hash(to_long_path(src / "a.txt"))

    def test_hashes_feed_manifest_then_verify_passes(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "a.txt", b"aaa", mtime_ns=1000)
        result = _make_backup(repo, src, "snap1")

        assert result.hashes  # 写入即校验产出了哈希
        report = verify_snapshot(repo, "snap1")
        assert report.ok and report.hashed_files == 1

    def test_verify_writes_off_by_default(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "a.txt", b"aaa", mtime_ns=1000)
        current = scan_source(src, ()).entries
        changes = detect_changes(src, current, None)

        result = write_snapshot(src, repo, current, changes, snapshot_id="snap1")

        assert result.hashes == {}  # 默认关闭，行为与 T-03 一致


class TestLinkedShaCarryForward:
    def test_linked_file_sha_carried_from_previous_manifest(self, tmp_path) -> None:
        """T-09 组合流程预演：linked 文件 sha 从旧 manifest 结转，无需重算。"""
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "stable.txt", b"stable", mtime_ns=1000)
        _write(src / "changing.txt", b"v1", mtime_ns=2000)
        _make_backup(repo, src, "snap1")
        from mirrorly.manifest import load_manifest

        m1 = load_manifest(repo, "snap1", require_complete=True)
        sha_stable = next(e.sha for e in m1.entries if e.path == "stable.txt")
        assert sha_stable is not None

        # 第二次备份：只改 changing.txt，stable.txt 走硬链接
        _write(src / "changing.txt", b"v2-longer", mtime_ns=3000)
        current = scan_source(src, ()).entries
        prev = {
            e.path: PreviousEntry(size=e.size, mtime_ns=e.mtime_ns, sha=e.sha)
            for e in m1.entries
            if not e.is_dir
        }
        changes = detect_changes(src, current, prev)
        assert changes.modified == ["changing.txt"]
        result = write_snapshot(
            src,
            repo,
            current,
            changes,
            snapshot_id="snap2",
            previous_snapshot=repo.path / "snapshots" / "snap1",
            verify_writes=True,
        )
        assert result.linked == ("stable.txt",)
        assert sorted(result.hashes) == ["changing.txt"]  # linked 文件未重算

        # 组合流程：linked sha 从旧 manifest 结转 + copied hash 合并
        carried = {e.path: e.sha for e in m1.entries if not e.is_dir and e.sha}
        merged = {rel: carried[rel] for rel in result.linked} | result.hashes
        m2 = create_manifest("snap2", str(src), repo.hash_algorithm, current, hashes=merged)
        write_manifest(repo, mark_complete(m2))

        m2_stable = next(e for e in m2.entries if e.path == "stable.txt")
        assert m2_stable.sha == sha_stable  # 结转而非重算
        report = verify_snapshot(repo, "snap2")
        assert report.ok and report.hashed_files == 2 and report.unhashed_entries == 0
