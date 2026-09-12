"""T-05 中断恢复测试（对应 MVP_TASKS T-05 验收标准与中断场景矩阵）。

中断注入方式：monkeypatch mirrorly.snapshot._copy_file_atomic，
在第 k 个文件复制时抛出异常，模拟断电/杀进程留下的半成品状态。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mirrorly import snapshot as snapshot_mod
from mirrorly.manifest import (
    create_manifest,
    list_manifests,
    load_manifest,
    mark_complete,
    write_manifest,
)
from mirrorly.recovery import (
    RecoveryError,
    build_resume_baseline,
    clean_tmp_residue,
    discard_incomplete,
    scan_recovery,
)
from mirrorly.repo import VolumeInfo, init_repo
from mirrorly.scan import detect_changes, scan_source, to_long_path
from mirrorly.snapshot import write_snapshot

_NTFS = VolumeInfo(label="BackupDisk", serial="A1B2C3D4", filesystem="NTFS")


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


def _build_source(src: Path, n: int = 5) -> None:
    for i in range(n):
        _write(src / f"file{i}.txt", f"content-{i}".encode(), mtime_ns=1_000_000 + i)
    _write(src / "sub" / "nested.txt", b"nested", mtime_ns=2_000_000)


def _tree_bytes(root: Path) -> dict[str, bytes]:
    """目录树 -> {相对路径: 字节内容}（仅文件，长路径安全）。"""
    result: dict[str, bytes] = {}
    for dirpath, _dirnames, filenames in os.walk(to_long_path(root)):
        base = Path(str(dirpath).removeprefix("\\\\?\\"))
        for name in filenames:
            p = base / name
            rel = p.relative_to(root).as_posix()
            result[rel] = Path(to_long_path(p)).read_bytes()
    return result


def _start_backup_interrupted(repo, src: Path, snap_id: str, fail_at: int, monkeypatch):
    """模拟一次在第 fail_at 个文件复制时中断的备份。

    流程与正式 backup 一致：扫描 → 检测 → 写 incomplete manifest → 写快照（中断）。
    """
    current = scan_source(src, ()).entries
    changes = detect_changes(src, current, None)
    manifest = create_manifest(snap_id, str(src), repo.hash_algorithm, current)
    write_manifest(repo, manifest)

    real_copy = snapshot_mod._copy_file_atomic
    counter = {"n": 0}

    def flaky_copy(src_file, dst, entry):
        if counter["n"] == fail_at:
            raise RuntimeError("simulated power loss")
        counter["n"] += 1
        return real_copy(src_file, dst, entry)

    monkeypatch.setattr(snapshot_mod, "_copy_file_atomic", flaky_copy)
    with pytest.raises(RuntimeError, match="power loss"):
        write_snapshot(src, repo, current, changes, snapshot_id=snap_id)
    monkeypatch.undo()
    return current


def _resume(repo, src: Path, incomplete_id: str, new_id: str):
    """按 T-05 方案执行一次续传，返回 SnapshotResult。"""
    clean_tmp_residue(repo)
    baseline = build_resume_baseline(repo, incomplete_id)
    current = scan_source(src, ()).entries
    changes = detect_changes(src, current, baseline.previous, previous_dirs=baseline.previous_dirs)
    result = write_snapshot(
        src,
        repo,
        current,
        changes,
        snapshot_id=new_id,
        previous_snapshot=baseline.snapshot_path,
    )
    manifest = create_manifest(new_id, str(src), repo.hash_algorithm, current)
    write_manifest(repo, mark_complete(manifest))
    return result


class TestScanRecovery:
    def test_clean_repo_report_empty(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        report = scan_recovery(repo)
        assert report.incomplete == ()
        assert report.orphan_dirs == ()
        assert report.tmp_residue == ()
        assert report.manifest_tmp_residue == ()

    def test_incomplete_manifest_detected(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "a.txt")
        manifest = create_manifest(
            "snap1", str(src), repo.hash_algorithm, scan_source(src, ()).entries
        )
        write_manifest(repo, manifest)
        report = scan_recovery(repo)
        assert [s.snapshot_id for s in report.incomplete] == ["snap1"]
        assert report.orphan_dirs == ()

    def test_complete_manifest_not_reported(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "a.txt")
        manifest = create_manifest(
            "snap1", str(src), repo.hash_algorithm, scan_source(src, ()).entries
        )
        write_manifest(repo, mark_complete(manifest))
        assert scan_recovery(repo).incomplete == ()

    def test_orphan_dir_reported(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        (repo.path / "snapshots" / "ghost").mkdir(parents=True)
        report = scan_recovery(repo)
        assert report.orphan_dirs == ("ghost",)

    def test_tmp_residue_reported(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        snap = repo.path / "snapshots" / "snap1"
        (snap / "sub").mkdir(parents=True)
        (snap / "a.txt.mrtmp").write_bytes(b"half")
        (snap / "sub" / "b.txt.mrtmp").write_bytes(b"half")
        (snap / "ok.txt").write_bytes(b"fine")
        (repo.path / "manifests.tmp" / "snap1.json.tmp").write_text("{}")
        report = scan_recovery(repo)
        assert sorted(report.tmp_residue) == [
            "snapshots/snap1/a.txt.mrtmp",
            "snapshots/snap1/sub/b.txt.mrtmp",
        ]
        assert report.manifest_tmp_residue == ("snap1.json.tmp",)


class TestCleanTmpResidue:
    def test_clean_removes_only_temp_files(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        snap = repo.path / "snapshots" / "snap1"
        (snap / "sub").mkdir(parents=True)
        (snap / "a.txt.mrtmp").write_bytes(b"half")
        (snap / "ok.txt").write_bytes(b"fine")
        (repo.path / "manifests.tmp" / "leftover.tmp").write_text("{}")
        cleaned = clean_tmp_residue(repo)
        assert len(cleaned) == 2
        assert not (snap / "a.txt.mrtmp").exists()
        assert (snap / "ok.txt").read_bytes() == b"fine"
        assert list((repo.path / "manifests.tmp").iterdir()) == []

    def test_clean_scoped_to_single_snapshot(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        for sid in ("s1", "s2"):
            d = repo.path / "snapshots" / sid
            d.mkdir(parents=True)
            (d / "x.mrtmp").write_bytes(b"half")
        clean_tmp_residue(repo, snapshot_id="s1")
        assert not (repo.path / "snapshots" / "s1" / "x.mrtmp").exists()
        assert (repo.path / "snapshots" / "s2" / "x.mrtmp").exists()


class TestBuildResumeBaseline:
    def _make_incomplete(self, repo, src: Path, snap_id: str, copied_files: list[str]) -> None:
        """构造 incomplete 状态：完整计划 manifest + 部分已复制文件的快照目录。"""
        current = scan_source(src, ()).entries
        manifest = create_manifest(snap_id, str(src), repo.hash_algorithm, current)
        write_manifest(repo, manifest)
        snap_dir = repo.path / "snapshots" / snap_id
        for rel in sorted(current):
            e = current[rel]
            if e.is_dir:
                (snap_dir / Path(rel)).mkdir(parents=True, exist_ok=True)
            elif rel in copied_files:
                dst = snap_dir / Path(rel)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes((src / rel).read_bytes())

    def test_baseline_happy_path_and_missing_reported(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _build_source(src, n=3)
        # file0/file1 已复制，file2 与 sub/nested.txt 缺失（正常中断态）
        self._make_incomplete(repo, src, "snap1", ["file0.txt", "file1.txt"])
        baseline = build_resume_baseline(repo, "snap1")
        assert baseline.snapshot_id == "snap1"
        assert baseline.snapshot_path == repo.path / "snapshots" / "snap1"
        assert sorted(baseline.previous) == ["file0.txt", "file1.txt"]
        assert sorted(baseline.missing) == ["file2.txt", "sub/nested.txt"]
        assert "sub" in baseline.previous_dirs

    def test_rejects_complete_manifest(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "a.txt")
        manifest = create_manifest(
            "snap1", str(src), repo.hash_algorithm, scan_source(src, ()).entries
        )
        write_manifest(repo, mark_complete(manifest))
        (repo.path / "snapshots" / "snap1").mkdir(parents=True)
        with pytest.raises(RecoveryError, match="complete"):
            build_resume_baseline(repo, "snap1")

    def test_rejects_missing_manifest(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        with pytest.raises(RecoveryError):
            build_resume_baseline(repo, "nope")

    def test_rejects_missing_snapshot_dir(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "a.txt")
        manifest = create_manifest(
            "snap1", str(src), repo.hash_algorithm, scan_source(src, ()).entries
        )
        write_manifest(repo, manifest)
        with pytest.raises(RecoveryError, match="不存在"):
            build_resume_baseline(repo, "snap1")

    def test_rejects_size_mismatch(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "a.txt", b"aaaa")
        self._make_incomplete(repo, src, "snap1", ["a.txt"])
        # 篡改：目录中文件大小与清单记录不符
        (repo.path / "snapshots" / "snap1" / "a.txt").write_bytes(b"a")
        with pytest.raises(RecoveryError, match="不一致"):
            build_resume_baseline(repo, "snap1")

    def test_rejects_type_mismatch(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "a.txt", b"aaaa")
        self._make_incomplete(repo, src, "snap1", ["a.txt"])
        (repo.path / "snapshots" / "snap1" / "a.txt").unlink()
        (repo.path / "snapshots" / "snap1" / "a.txt").mkdir()
        with pytest.raises(RecoveryError, match="类型"):
            build_resume_baseline(repo, "snap1")

    def test_rejects_unsafe_snapshot_id(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        with pytest.raises(RecoveryError):
            build_resume_baseline(repo, "../evil")


class TestResumeIntegration:
    @pytest.mark.parametrize("fail_at", [0, 1, 3, 5])
    def test_resume_at_various_interruption_points(self, tmp_path, monkeypatch, fail_at) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _build_source(src, n=5)  # 6 个文件（含 sub/nested.txt）
        _start_backup_interrupted(repo, src, "snap-incomplete", fail_at, monkeypatch)

        result = _resume(repo, src, "snap-incomplete", "snap-final")

        # 验收 1/3：最终快照树与当前源逐字节一致，无 tmp 残留
        assert _tree_bytes(result.path) == _tree_bytes(src)
        assert scan_recovery(repo).tmp_residue == ()
        final = load_manifest(repo, "snap-final", require_complete=True)
        assert final.status == "complete"
        # 验收 1：已完整复制的 fail_at 个文件被硬链接复用，未从头复制
        assert len(result.linked) == fail_at
        assert len(result.copied) == 6 - fail_at

    def test_resume_reuses_inodes_not_recopy(self, tmp_path, monkeypatch) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _build_source(src, n=3)
        _start_backup_interrupted(repo, src, "snap-incomplete", 2, monkeypatch)
        inc_dir = repo.path / "snapshots" / "snap-incomplete"
        ino_before = os.stat(inc_dir / "file0.txt").st_ino

        result = _resume(repo, src, "snap-incomplete", "snap-final")

        assert os.stat(result.path / "file0.txt").st_ino == ino_before
        assert os.stat(result.path / "file0.txt").st_nlink > 1

    def test_double_interruption_converges(self, tmp_path, monkeypatch) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _build_source(src, n=5)
        _start_backup_interrupted(repo, src, "snap-i1", 2, monkeypatch)

        # 第一次续传也在中途被中断（写入第二个 incomplete）
        clean_tmp_residue(repo)
        baseline = build_resume_baseline(repo, "snap-i1")
        current = scan_source(src, ()).entries
        changes = detect_changes(
            src, current, baseline.previous, previous_dirs=baseline.previous_dirs
        )
        write_manifest(repo, create_manifest("snap-i2", str(src), repo.hash_algorithm, current))
        real_copy = snapshot_mod._copy_file_atomic
        counter = {"n": 0}

        def flaky_copy(src_file, dst, entry):
            if counter["n"] == 1:
                raise RuntimeError("simulated power loss")
            counter["n"] += 1
            return real_copy(src_file, dst, entry)

        monkeypatch.setattr(snapshot_mod, "_copy_file_atomic", flaky_copy)
        with pytest.raises(RuntimeError):
            write_snapshot(
                src,
                repo,
                current,
                changes,
                snapshot_id="snap-i2",
                previous_snapshot=baseline.snapshot_path,
            )
        monkeypatch.undo()

        # 第二次续传（以 snap-i2 为基线）必须收敛
        result = _resume(repo, src, "snap-i2", "snap-final")
        assert _tree_bytes(result.path) == _tree_bytes(src)
        assert scan_recovery(repo).tmp_residue == ()

    def test_source_changed_during_interruption(self, tmp_path, monkeypatch) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _build_source(src, n=3)
        _start_backup_interrupted(repo, src, "snap-incomplete", 2, monkeypatch)

        # 中断期间用户又修改了源文件（大小变化）
        _write(src / "file0.txt", b"brand-new-content", mtime_ns=9_000_000)
        result = _resume(repo, src, "snap-incomplete", "snap-final")

        assert (result.path / "file0.txt").read_bytes() == b"brand-new-content"
        assert "file0.txt" in result.copied
        assert _tree_bytes(result.path) == _tree_bytes(src)

    def test_resume_unicode_and_long_path(self, tmp_path, monkeypatch) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "报告 2026-09-13.txt", "中文内容".encode(), mtime_ns=1_000_000)
        deep = src
        for i in range(12):
            deep = deep / f"深目录层级-{i:02d}-这是一个很长的目录名"
        _write(deep / "深层文件.txt", b"deep", mtime_ns=1_000_001)
        assert len(str(deep / "深层文件.txt")) > 260

        _start_backup_interrupted(repo, src, "snap-incomplete", 0, monkeypatch)
        result = _resume(repo, src, "snap-incomplete", "snap-final")

        assert _tree_bytes(result.path) == _tree_bytes(src)


class TestDiscardIncomplete:
    def test_discard_removes_dir_and_manifest(self, tmp_path, monkeypatch) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _build_source(src, n=3)
        _start_backup_interrupted(repo, src, "snap-incomplete", 1, monkeypatch)
        _resume(repo, src, "snap-incomplete", "snap-final")

        discard_incomplete(repo, "snap-incomplete")

        assert not (repo.path / "snapshots" / "snap-incomplete").exists()
        assert not (repo.path / "manifests" / "snap-incomplete.json").exists()
        # 数据由新快照持有，不受 discard 影响
        assert _tree_bytes(repo.path / "snapshots" / "snap-final") == _tree_bytes(src)
        assert [s.snapshot_id for s in list_manifests(repo)] == ["snap-final"]

    def test_discard_refuses_complete_snapshot(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "a.txt")
        manifest = create_manifest(
            "snap1", str(src), repo.hash_algorithm, scan_source(src, ()).entries
        )
        write_manifest(repo, mark_complete(manifest))
        with pytest.raises(RecoveryError, match="complete"):
            discard_incomplete(repo, "snap1")
        assert (repo.path / "manifests" / "snap1.json").exists()

    def test_discard_rejects_unsafe_id(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        with pytest.raises(RecoveryError):
            discard_incomplete(repo, "../evil")
