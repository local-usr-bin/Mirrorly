"""T-03 测试：快照写入引擎（ADR-005/TR-2 不变量：旧快照永不修改、绝不原地写已链接文件）。"""

import os

import pytest

from mirrorly.repo import VolumeInfo, init_repo
from mirrorly.scan import detect_changes, scan_source
from mirrorly.snapshot import (
    SnapshotError,
    generate_snapshot_id,
    write_snapshot,
)

_NTFS = VolumeInfo(label="BackupDisk", serial="A1B2C3D4", filesystem="NTFS")
_EXFAT = VolumeInfo(label="UsbStick", serial="E5F60708", filesystem="exFAT")


def _write(path, data: bytes = b"x", mtime_ns: int | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


def _scan_and_detect(source, previous=None):
    """T-03 测试辅助：扫描当前状态并对空基线/给定基线做变更检测。"""
    result = scan_source(source, ())
    if previous is None:
        return result.entries, detect_changes(source, result.entries, None)
    return result.entries, detect_changes(source, result.entries, previous)


def _tree_bytes(root):
    """目录树 → {相对路径: 字节}，用于整树比对（不含空目录）。"""
    out = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, root)
            with open(p, "rb") as f:
                out[rel] = f.read()
    return out


class TestFirstSnapshot:
    def test_copies_everything_and_preserves_dirs(self, tmp_path) -> None:
        src = tmp_path / "src"
        _write(src / "a.txt", b"aaa", mtime_ns=1000)
        _write(src / "sub" / "b.txt", b"bb", mtime_ns=2000)
        (src / "empty_dir").mkdir()
        repo = init_repo(tmp_path / "target", volume_info_provider=lambda p: _NTFS)

        current, changes = _scan_and_detect(src)
        result = write_snapshot(src, repo, current, changes, snapshot_id="snap1")

        snap = result.path
        assert snap == repo.path / "snapshots" / "snap1"
        assert (snap / "a.txt").read_bytes() == b"aaa"
        assert (snap / "sub" / "b.txt").read_bytes() == b"bb"
        assert (snap / "empty_dir").is_dir()  # 空目录保留（验收 4）
        assert sorted(result.copied) == ["a.txt", "sub/b.txt"]
        assert result.linked == ()
        assert result.bytes_written == 5

    def test_mtime_preserved_on_copies(self, tmp_path) -> None:
        src = tmp_path / "src"
        _write(src / "a.txt", b"aaa", mtime_ns=123456789)
        repo = init_repo(tmp_path / "target", volume_info_provider=lambda p: _NTFS)
        current, changes = _scan_and_detect(src)
        result = write_snapshot(src, repo, current, changes, snapshot_id="snap1")
        st = os.stat(result.path / "a.txt")
        # Windows FILETIME 粒度为 100ns，os.utime 会截断；容差 100ns
        assert abs(st.st_mtime_ns - 123456789) <= 100

    def test_unicode_filename(self, tmp_path) -> None:
        src = tmp_path / "src"
        _write(src / "你好_数据.txt", "中文内容".encode())
        repo = init_repo(tmp_path / "target", volume_info_provider=lambda p: _NTFS)
        current, changes = _scan_and_detect(src)
        result = write_snapshot(src, repo, current, changes, snapshot_id="snap1")
        assert (result.path / "你好_数据.txt").read_bytes() == "中文内容".encode()


class TestHardlinkReuse:
    def _two_snapshots(self, tmp_path):
        """构造：snap1 后修改 b.txt、删除 gone.txt，生成 snap2，返回上下文。"""
        src = tmp_path / "src"
        _write(src / "keep.txt", b"keep", mtime_ns=1000)
        _write(src / "b.txt", b"old", mtime_ns=1000)
        _write(src / "gone.txt", b"gone", mtime_ns=1000)
        repo = init_repo(tmp_path / "target", volume_info_provider=lambda p: _NTFS)

        cur1, ch1 = _scan_and_detect(src)
        r1 = write_snapshot(src, repo, cur1, ch1, snapshot_id="snap1")

        # 构造第二版：keep.txt 不动；b.txt 改内容；gone.txt 删除
        prev = {rel: _prev_entry(src / rel, e) for rel, e in cur1.items() if not e.is_dir}
        _write(src / "b.txt", b"new-content", mtime_ns=5000)
        os.remove(src / "gone.txt")
        cur2, ch2 = _scan_and_detect(src, prev)
        r2 = write_snapshot(src, repo, cur2, ch2, snapshot_id="snap2", previous_snapshot=r1.path)
        return src, repo, r1, r2

    def test_unchanged_file_shares_inode(self, tmp_path) -> None:
        _src, _repo, r1, r2 = self._two_snapshots(tmp_path)
        st1 = os.stat(r1.path / "keep.txt")
        st2 = os.stat(r2.path / "keep.txt")
        assert st1.st_ino == st2.st_ino  # 同一 inode
        assert st2.st_nlink > 1  # 硬链接数 >1（验收 1）
        assert "keep.txt" in r2.linked

    def test_modified_file_is_independent_new_file(self, tmp_path) -> None:
        _src, _repo, r1, r2 = self._two_snapshots(tmp_path)
        st_old = os.stat(r1.path / "b.txt")
        st_new = os.stat(r2.path / "b.txt")
        assert st_old.st_ino != st_new.st_ino  # 独立新文件
        assert "b.txt" in r2.copied
        assert (r2.path / "b.txt").read_bytes() == b"new-content"

    def test_history_not_polluted_by_source_rewrite(self, tmp_path) -> None:
        """核心回归：改写源文件后，旧快照字节不变（验收 2）。"""
        _src, _repo, r1, _r2 = self._two_snapshots(tmp_path)
        assert (r1.path / "b.txt").read_bytes() == b"old"
        assert (r1.path / "keep.txt").read_bytes() == b"keep"

    def test_deleted_file_absent_in_new_present_in_old(self, tmp_path) -> None:
        _src, _repo, r1, r2 = self._two_snapshots(tmp_path)
        assert not (r2.path / "gone.txt").exists()  # 新快照缺席（验收 3）
        assert (r1.path / "gone.txt").read_bytes() == b"gone"  # 旧快照保留


class TestSafetyInvariants:
    def test_link_failure_raises_explicitly(self, tmp_path, monkeypatch) -> None:
        src = tmp_path / "src"
        _write(src / "a.txt", b"aaa", mtime_ns=1000)
        repo = init_repo(tmp_path / "target", volume_info_provider=lambda p: _NTFS)
        cur1, ch1 = _scan_and_detect(src)
        r1 = write_snapshot(src, repo, cur1, ch1, snapshot_id="snap1")

        cur2, ch2 = _scan_and_detect(
            src, {rel: _prev_entry(src / rel, e) for rel, e in cur1.items() if not e.is_dir}
        )

        def _boom(*_args, **_kwargs):
            raise OSError("simulated link failure")

        monkeypatch.setattr(os, "link", _boom)
        with pytest.raises(SnapshotError, match="硬链接失败"):
            write_snapshot(src, repo, cur2, ch2, snapshot_id="snap2", previous_snapshot=r1.path)

    def test_mutation_during_copy_is_skipped(self, tmp_path) -> None:
        """复制中源文件变动 → 跳过并记录（TR-4），无临时文件残留。"""
        src = tmp_path / "src"
        f = _write(src / "a.txt", b"aaa", mtime_ns=1000)
        repo = init_repo(tmp_path / "target", volume_info_provider=lambda p: _NTFS)
        current, changes = _scan_and_detect(src)  # 扫描后
        _write(f, b"changed-during-copy", mtime_ns=9999)  # 源被修改（过期 current）
        result = write_snapshot(src, repo, current, changes, snapshot_id="snap1")
        assert result.skipped and result.skipped[0][0] == "a.txt"
        assert not (result.path / "a.txt").exists()  # 未写入陈旧内容
        assert list(result.path.rglob("*.mrtmp")) == []  # 临时文件已清理

    def test_existing_snapshot_id_rejected(self, tmp_path) -> None:
        src = tmp_path / "src"
        _write(src / "a.txt")
        repo = init_repo(tmp_path / "target", volume_info_provider=lambda p: _NTFS)
        current, changes = _scan_and_detect(src)
        write_snapshot(src, repo, current, changes, snapshot_id="snap1")
        with pytest.raises(SnapshotError, match="已存在"):
            write_snapshot(src, repo, current, changes, snapshot_id="snap1")

    def test_crash_does_not_touch_old_snapshot(self, tmp_path) -> None:
        """崩溃安全性：新快照写入中断，旧快照目录哈希不变。"""
        src = tmp_path / "src"
        _write(src / "a.txt", b"aaa", mtime_ns=1000)
        _write(src / "b.txt", b"bbb", mtime_ns=1000)
        repo = init_repo(tmp_path / "target", volume_info_provider=lambda p: _NTFS)
        cur1, ch1 = _scan_and_detect(src)
        r1 = write_snapshot(src, repo, cur1, ch1, snapshot_id="snap1")
        before = _tree_bytes(r1.path)

        # 新快照写入"中断"：手动制造半成品 snap2
        partial = repo.path / "snapshots" / "snap2"
        partial.mkdir()
        (partial / "partial.txt.mrtmp").write_bytes(b"half")

        assert _tree_bytes(r1.path) == before  # 旧快照完全未变


class TestDegradedRepo:
    def test_hardlinks_false_copies_instead(self, tmp_path) -> None:
        """exFAT warn 模式（hardlinks=False）：复制而非链接（显式能力降级，不静默）。"""
        src = tmp_path / "src"
        _write(src / "a.txt", b"aaa", mtime_ns=1000)
        repo = init_repo(
            tmp_path / "target",
            filesystem_policy="warn",
            assume_yes=True,
            volume_info_provider=lambda p: _EXFAT,
        )
        assert repo.hardlinks is False
        cur1, ch1 = _scan_and_detect(src)
        r1 = write_snapshot(src, repo, cur1, ch1, snapshot_id="snap1")
        cur2, ch2 = _scan_and_detect(
            src, {rel: _prev_entry(src / rel, e) for rel, e in cur1.items() if not e.is_dir}
        )
        r2 = write_snapshot(src, repo, cur2, ch2, snapshot_id="snap2", previous_snapshot=r1.path)
        assert "a.txt" in r2.copied  # 未变文件也复制
        st = os.stat(r2.path / "a.txt")
        assert st.st_nlink == 1


class TestHelpers:
    def test_generate_snapshot_id_format(self) -> None:
        from datetime import datetime

        sid = generate_snapshot_id(datetime(2026, 9, 13, 1, 2, 3))
        assert sid == "2026-09-13_010203"


def _prev_entry(path, scanned):
    from mirrorly.hashing import hash_file
    from mirrorly.scan import PreviousEntry, to_long_path

    return PreviousEntry(
        size=scanned.size,
        mtime_ns=scanned.mtime_ns,
        sha=hash_file(to_long_path(path)),
    )
