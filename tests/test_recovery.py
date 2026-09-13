"""T-05 中断恢复测试（对应 MVP_TASKS T-05 验收标准与中断场景矩阵）。

中断注入方式：monkeypatch mirrorly.snapshot._copy_file_atomic，
在第 k 个文件复制时抛出异常，模拟断电/杀进程留下的半成品状态。
"""

from __future__ import annotations

import os
from dataclasses import replace
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
        src = tmp_path / "src"
        _write(src / "a.txt")
        _write(src / "sub" / "b.txt")
        _write(src / "keep.mrtmp")  # 用户文件名恰以 .mrtmp 结尾（合法）
        current = scan_source(src, ()).entries
        write_manifest(repo, create_manifest("snap1", str(src), repo.hash_algorithm, current))
        snap = repo.path / "snapshots" / "snap1"
        (snap / "sub").mkdir(parents=True)
        (snap / "a.txt.mrtmp").write_bytes(b"half")
        (snap / "sub" / "b.txt.mrtmp").write_bytes(b"half")
        (snap / "keep.mrtmp").write_bytes(b"user data")
        (snap / "ok.txt").write_bytes(b"fine")
        (repo.path / "manifests.tmp" / "snap1.json.tmp").write_text("{}")
        report = scan_recovery(repo)
        # 只报告可证明为 staging residue 的文件；manifested 用户文件不误报
        assert sorted(report.tmp_residue) == [
            "snapshots/snap1/a.txt.mrtmp",
            "snapshots/snap1/sub/b.txt.mrtmp",
        ]
        assert report.manifest_tmp_residue == ("snap1.json.tmp",)


class TestTmpResidueOwnership:
    """B1-1 回归：.mrtmp 后缀不足以证明 temp ownership。

    Mirrorly 只为 manifest 规划的最终文件创建 ``<目标名>.mrtmp`` staging temp，
    因此 residue 判定必须是「stem 为该快照 manifest 文件条目、且自身不在清单中」；
    manifested 用户文件（含合法以 .mrtmp 结尾的）绝不被清理触碰。
    """

    def _incomplete(self, repo, src: Path, snap_id: str, materialized=()) -> Path:
        """构造 incomplete 中断态：完整计划 manifest + 部分已物化的快照目录。"""
        current = scan_source(src, ()).entries
        write_manifest(repo, create_manifest(snap_id, str(src), repo.hash_algorithm, current))
        snap = repo.path / "snapshots" / snap_id
        snap.mkdir(parents=True)
        for rel in materialized:
            (snap / Path(rel)).write_bytes((src / rel).read_bytes())
        return snap

    def test_manifested_mrtmp_user_files_survive_complete_snapshot(self, tmp_path) -> None:
        """E（含 B1-1 核心）：complete 快照中 manifested 用户文件零修改。"""
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        for rel, data in (
            ("keep.mrtmp", b"keep"),
            ("foo.mrtmp", b"foo"),
            ("foo.mrtmp.mrtmp", b"double"),
            ("nested/bar.mrtmp", b"bar"),
        ):
            _write(src / Path(rel), data)
        current = scan_source(src, ()).entries
        write_manifest(
            repo, mark_complete(create_manifest("snap1", str(src), repo.hash_algorithm, current))
        )
        snap = repo.path / "snapshots" / "snap1"
        snap.mkdir(parents=True)
        for rel, data in (
            ("keep.mrtmp", b"keep"),
            ("foo.mrtmp", b"foo"),
            ("foo.mrtmp.mrtmp", b"double"),
            ("nested/bar.mrtmp", b"bar"),
        ):
            p = snap / Path(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        before = _tree_bytes(snap)
        assert clean_tmp_residue(repo) == []
        assert _tree_bytes(snap) == before
        assert scan_recovery(repo).tmp_residue == ()

    def test_incomplete_user_mrtmp_survives_alongside_real_residue(self, tmp_path) -> None:
        """C/D：incomplete 快照中合法 foo.mrtmp 保留，真实 residue 被清理。"""
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "foo.mrtmp", b"final-user-file")
        _write(src / "data.txt", b"data")
        snap = self._incomplete(repo, src, "snap1", materialized=["foo.mrtmp"])
        # 真实 staging residue：分别为 data.txt 与用户文件 foo.mrtmp 生成
        (snap / "data.txt.mrtmp").write_bytes(b"staging")
        (snap / "foo.mrtmp.mrtmp").write_bytes(b"staging")
        # stem 不在 manifest 中：无法证明 ownership，fail safe 保留
        (snap / "unprovable.mrtmp").write_bytes(b"unknown-owner")
        cleaned = clean_tmp_residue(repo)
        assert sorted(cleaned) == [
            "snapshots/snap1/data.txt.mrtmp",
            "snapshots/snap1/foo.mrtmp.mrtmp",
        ]
        assert (snap / "foo.mrtmp").read_bytes() == b"final-user-file"
        assert (snap / "unprovable.mrtmp").exists()
        assert scan_recovery(repo).tmp_residue == ()

    def test_real_write_path_residue_cleaned(self, tmp_path, monkeypatch) -> None:
        """B：真实写入路径（os.replace 被打断）留下的 temp residue 仍可清理。"""
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _build_source(src, n=2)
        current = scan_source(src, ()).entries
        changes = detect_changes(src, current, None)
        write_manifest(repo, create_manifest("snap1", str(src), repo.hash_algorithm, current))

        def boom(*a, **k):
            raise RuntimeError("simulated power loss during replace")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(RuntimeError):
            write_snapshot(src, repo, current, changes, snapshot_id="snap1")
        monkeypatch.undo()

        snap = repo.path / "snapshots" / "snap1"
        residue = sorted(
            p.name for p in snap.rglob("*.mrtmp") if p.is_file()
        )
        assert residue, "真实写入路径应留下 staging temp"

        cleaned = clean_tmp_residue(repo)
        assert cleaned == [f"snapshots/snap1/{name}" for name in residue]
        assert not [p for p in snap.rglob("*.mrtmp") if p.is_file()]
        assert scan_recovery(repo).tmp_residue == ()

    def test_orphan_dir_residue_kept_fail_safe(self, tmp_path) -> None:
        """孤儿目录（无 manifest）无法证明 ownership，fail safe 保留。"""
        repo = _init_repo(tmp_path / "target")
        snap = repo.path / "snapshots" / "ghost"
        snap.mkdir(parents=True)
        (snap / "a.txt.mrtmp").write_bytes(b"half")
        assert clean_tmp_residue(repo) == []
        assert (snap / "a.txt.mrtmp").exists()

    def test_complete_snapshot_absolutely_readonly_for_cleanup(self, tmp_path) -> None:
        """Architecture review 边界冻结：complete 快照发布后，cleanup 对其是零操作。

        即使快照树中存在能通过 ownership 判定的 staging-like 文件
        （<manifested_target>.mrtmp：stem 是 complete manifest 的文件条目、
        自身不在清单中），也不得删除——complete 即只读；
        scan_recovery 可以报告可疑 residue，但报告不构成删除权限。
        """
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "normal.txt", b"normal")
        current = scan_source(src, ()).entries
        write_manifest(
            repo, mark_complete(create_manifest("snap1", str(src), repo.hash_algorithm, current))
        )
        snap = repo.path / "snapshots" / "snap1"
        snap.mkdir(parents=True)
        (snap / "normal.txt").write_bytes(b"normal")
        (snap / "normal.txt.mrtmp").write_bytes(b"stale-staging")
        before = _tree_bytes(snap)

        # 全量清理（snapshot_id=None）与作用域清理（snapshot_id 指定）都是零操作
        assert clean_tmp_residue(repo) == []
        assert clean_tmp_residue(repo, snapshot_id="snap1") == []
        assert _tree_bytes(snap) == before

        # 报告侧：可疑 residue 可被 scan_recovery 发现（仅报告，不删除）
        report = scan_recovery(repo)
        assert report.tmp_residue == ("snapshots/snap1/normal.txt.mrtmp",)
        assert _tree_bytes(snap) == before

    def test_unknown_status_manifest_snapshot_zero_op(self, tmp_path) -> None:
        """正向删除授权收口：manifest status 未知时 cleanup 零操作。

        manifest parser 对 status 无白名单校验——未知 status 可成功加载，
        因此删除授权必须是正向条件（仅 STATUS_INCOMPLETE 放行），
        未知/损坏状态与 complete 一样零操作。
        """
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "normal.txt", b"normal")
        current = scan_source(src, ()).entries
        manifest = create_manifest("snap1", str(src), repo.hash_algorithm, current)
        # 手工构造未知 status（模拟磁盘损坏/外部篡改）；load_manifest 可成功加载
        write_manifest(repo, replace(manifest, status="corrupted-unknown"))
        assert load_manifest(repo, "snap1").status == "corrupted-unknown"
        snap = repo.path / "snapshots" / "snap1"
        snap.mkdir(parents=True)
        (snap / "normal.txt").write_bytes(b"normal")
        (snap / "normal.txt.mrtmp").write_bytes(b"stale-staging")
        before = _tree_bytes(snap)

        assert clean_tmp_residue(repo) == []
        assert clean_tmp_residue(repo, snapshot_id="snap1") == []
        assert _tree_bytes(snap) == before


class TestCleanTmpResidue:
    def test_clean_removes_only_temp_files(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        src = tmp_path / "src"
        _write(src / "a.txt")
        _write(src / "sub" / "b.txt")
        current = scan_source(src, ()).entries
        write_manifest(repo, create_manifest("snap1", str(src), repo.hash_algorithm, current))
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
        src = tmp_path / "src"
        _write(src / "x.txt")
        current = scan_source(src, ()).entries
        for sid in ("s1", "s2"):
            write_manifest(repo, create_manifest(sid, str(src), repo.hash_algorithm, current))
            d = repo.path / "snapshots" / sid
            d.mkdir(parents=True)
            (d / "x.txt.mrtmp").write_bytes(b"half")
        clean_tmp_residue(repo, snapshot_id="s1")
        assert not (repo.path / "snapshots" / "s1" / "x.txt.mrtmp").exists()
        assert (repo.path / "snapshots" / "s2" / "x.txt.mrtmp").exists()


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
