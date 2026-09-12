"""T-07 保留策略测试（对应 MVP_TASKS T-07 验收标准）。

删除模型：snapshot 版本目录 + 对应 manifest 整体删除，依赖 NTFS 硬链接
link count 自然管理数据生命周期——不做文件级引用分析。
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from mirrorly.manifest import (
    STATUS_COMPLETE,
    STATUS_INCOMPLETE,
    create_manifest,
    list_manifests,
    mark_complete,
    write_manifest,
)
from mirrorly.repo import VolumeInfo, init_repo
from mirrorly.retention import (
    RetentionError,
    RetentionPlan,
    apply_retention_plan,
    build_retention_plan,
)
from mirrorly.scan import to_long_path

_NTFS = VolumeInfo(label="BackupDisk", serial="A1B2C3D4", filesystem="NTFS")


def _ntfs_provider(_path):
    return _NTFS


def _init_repo(path: Path):
    return init_repo(path, volume_info_provider=_ntfs_provider)


def _make_snapshot(
    repo,
    snap_id: str,
    created_at: str,
    *,
    status: str = STATUS_COMPLETE,
    with_dir: bool = True,
    with_manifest: bool = True,
) -> None:
    """按指定创建时间登记一个快照（目录 + manifest）。"""
    if with_manifest:
        m = create_manifest(snap_id, "C:/source", repo.hash_algorithm, {})
        m = replace(m, created_at=created_at)
        if status == STATUS_COMPLETE:
            m = mark_complete(m)
        write_manifest(repo, m)
    if with_dir:
        d = repo.path / "snapshots" / snap_id
        Path(to_long_path(d)).mkdir(parents=True, exist_ok=True)
        (d / "marker.txt").write_bytes(b"x")


def _iso(day: str) -> str:
    return f"{day}T12:00:00+00:00"


class TestKeepLast:
    def test_keep_last_deletes_oldest(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        for i in range(1, 6):
            _make_snapshot(repo, f"snap{i}", _iso(f"2026-09-0{i}"))

        plan = build_retention_plan(repo, keep_last=3)

        assert plan.keep == ("snap3", "snap4", "snap5")
        assert plan.delete == ("snap1", "snap2")
        apply_retention_plan(repo, plan)
        remaining = sorted(p.name for p in (repo.path / "snapshots").iterdir())
        assert remaining == ["snap3", "snap4", "snap5"]

    def test_keep_last_zero_keeps_nothing_by_itself(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        for i in range(1, 4):
            _make_snapshot(repo, f"snap{i}", _iso(f"2026-09-0{i}"))

        plan = build_retention_plan(repo, keep_last=0)

        assert plan.keep == ()
        assert plan.delete == ("snap1", "snap2", "snap3")

    def test_no_policy_raises(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        with pytest.raises(RetentionError, match="至少"):
            build_retention_plan(repo)

    def test_sorting_uses_created_at_not_dir_mtime(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "old", _iso("2026-09-01"))
        _make_snapshot(repo, "new", _iso("2026-09-02"))
        # 人为触碰 old 的目录 mtime 使其"看起来更新"
        os.utime(repo.path / "snapshots" / "old", None)

        plan = build_retention_plan(repo, keep_last=1)

        assert plan.keep == ("new",)
        assert plan.delete == ("old",)


class TestKeepMonthly:
    def test_monthly_picks_latest_of_each_month(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "jul-early", _iso("2026-07-05"))
        _make_snapshot(repo, "jul-late", _iso("2026-07-25"))
        _make_snapshot(repo, "aug-mid", _iso("2026-08-15"))
        _make_snapshot(repo, "sep-early", _iso("2026-09-02"))
        _make_snapshot(repo, "sep-late", _iso("2026-09-10"))

        plan = build_retention_plan(repo, keep_monthly=3)

        # 当前为 2026-09：回溯 9/8/7 三个月，每月取该月最新 complete
        assert plan.keep == ("jul-late", "aug-mid", "sep-late")
        assert plan.delete == ("jul-early", "sep-early")

    def test_monthly_skips_months_without_snapshots(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "may", _iso("2026-05-10"))
        _make_snapshot(repo, "sep", _iso("2026-09-10"))

        plan = build_retention_plan(repo, keep_monthly=12)

        assert plan.keep == ("may", "sep")
        assert plan.delete == ()


class TestCombinedPolicy:
    def test_union_of_keep_last_and_monthly(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "jun", _iso("2026-06-15"))
        _make_snapshot(repo, "aug", _iso("2026-08-15"))
        _make_snapshot(repo, "sep-a", _iso("2026-09-01"))
        _make_snapshot(repo, "sep-b", _iso("2026-09-05"))
        _make_snapshot(repo, "sep-c", _iso("2026-09-09"))

        # keep_last=2 → sep-b, sep-c；keep_monthly=4 → 9/8/7/6 月代表
        plan = build_retention_plan(repo, keep_last=2, keep_monthly=4)

        assert plan.keep == ("jun", "aug", "sep-b", "sep-c")
        assert plan.delete == ("sep-a",)


class TestSafetyBoundaries:
    def test_dry_run_deletes_nothing(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        for i in range(1, 4):
            _make_snapshot(repo, f"snap{i}", _iso(f"2026-09-0{i}"))
        plan = build_retention_plan(repo, keep_last=1)
        assert plan.delete == ("snap1", "snap2")

        result = apply_retention_plan(repo, plan, dry_run=True)

        assert result == ()
        for i in range(1, 4):
            assert (repo.path / "snapshots" / f"snap{i}").is_dir()
            assert (repo.path / "manifests" / f"snap{i}.json").is_file()

    def test_incomplete_never_deleted(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        for i in range(1, 4):
            _make_snapshot(repo, f"snap{i}", _iso(f"2026-09-0{i}"))
        _make_snapshot(repo, "incomplete-one", _iso("2026-08-01"), status=STATUS_INCOMPLETE)

        plan = build_retention_plan(repo, keep_last=1)
        apply_retention_plan(repo, plan)

        assert "incomplete-one" not in plan.delete
        assert (repo.path / "snapshots" / "incomplete-one").is_dir()
        assert (repo.path / "manifests" / "incomplete-one.json").is_file()

    def test_orphan_dir_never_deleted(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "snap1", _iso("2026-09-01"))
        _make_snapshot(repo, "snap2", _iso("2026-09-02"))
        _make_snapshot(repo, "ghost", _iso("2026-09-03"), with_manifest=False)

        plan = build_retention_plan(repo, keep_last=1)
        apply_retention_plan(repo, plan)

        assert "ghost" not in plan.delete
        assert (repo.path / "snapshots" / "ghost").is_dir()

    def test_missing_manifest_at_apply_raises(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "ghost", _iso("2026-09-01"), with_manifest=False)
        plan = RetentionPlan(keep=(), delete=("ghost",))

        with pytest.raises(RetentionError, match="manifest"):
            apply_retention_plan(repo, plan)
        assert (repo.path / "snapshots" / "ghost").is_dir()  # 未被误删

    def test_unsafe_snapshot_id_rejected(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        for bad in ("../evil", "a/b", "C:/abs", ""):
            with pytest.raises(RetentionError):
                apply_retention_plan(repo, RetentionPlan(keep=(), delete=(bad,)))

    def test_delete_failure_reported_explicitly(self, tmp_path, monkeypatch) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "snap1", _iso("2026-09-01"))
        _make_snapshot(repo, "snap2", _iso("2026-09-02"))
        plan = build_retention_plan(repo, keep_last=1)

        import shutil

        def boom(_path):
            raise OSError("disk error")

        monkeypatch.setattr(shutil, "rmtree", boom)
        with pytest.raises(RetentionError, match="snap1"):
            apply_retention_plan(repo, plan)


class TestApplyAndSync:
    def test_manifest_and_dir_deleted_in_sync(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        for i in range(1, 5):
            _make_snapshot(repo, f"snap{i}", _iso(f"2026-09-0{i}"))
        plan = build_retention_plan(repo, keep_last=2)
        deleted = apply_retention_plan(repo, plan)

        assert deleted == ("snap1", "snap2")
        manifest_ids = sorted(s.snapshot_id for s in list_manifests(repo))
        dir_ids = sorted(p.name for p in (repo.path / "snapshots").iterdir())
        assert manifest_ids == dir_ids == ["snap3", "snap4"]

    def test_empty_repo_noop(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        plan = build_retention_plan(repo, keep_last=3, keep_monthly=12)
        assert plan.keep == () and plan.delete == ()
        assert apply_retention_plan(repo, plan) == ()

    def test_unicode_snapshot_id(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "快照-八月", _iso("2026-08-15"))
        _make_snapshot(repo, "快照-九月", _iso("2026-09-10"))

        plan = build_retention_plan(repo, keep_last=1)
        assert plan.delete == ("快照-八月",)
        apply_retention_plan(repo, plan)

        assert not (repo.path / "snapshots" / "快照-八月").exists()
        assert (repo.path / "snapshots" / "快照-九月").is_dir()

    def test_long_path_content_deleted(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "snap1", _iso("2026-09-01"))
        _make_snapshot(repo, "snap2", _iso("2026-09-02"))
        # 在 snap1 内构造 >260 字符的深层文件
        deep = repo.path / "snapshots" / "snap1"
        for i in range(12):
            deep = deep / f"深层目录-{i:02d}-这是一个很长的目录名"
        lp = to_long_path(deep / "deep.txt")
        Path(lp).parent.mkdir(parents=True, exist_ok=True)
        Path(lp).write_bytes(b"deep")
        assert len(str(deep / "deep.txt")) > 260

        plan = build_retention_plan(repo, keep_last=1)
        apply_retention_plan(repo, plan)

        assert not (repo.path / "snapshots" / "snap1").exists()

    def test_hardlinked_data_survives_snapshot_deletion(self, tmp_path) -> None:
        """硬链接语义：删除旧快照目录后，被新快照链接的文件数据仍在。"""
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "snap1", _iso("2026-09-01"))
        _make_snapshot(repo, "snap2", _iso("2026-09-02"))
        shared = repo.path / "snapshots" / "snap1" / "marker.txt"
        link_in_snap2 = repo.path / "snapshots" / "snap2" / "marker.txt"
        link_in_snap2.unlink()  # 用指向 snap1 同一 inode 的硬链接替换独立副本
        os.link(to_long_path(shared), to_long_path(link_in_snap2))
        assert os.stat(to_long_path(shared)).st_nlink == 2

        plan = build_retention_plan(repo, keep_last=1)
        apply_retention_plan(repo, plan)

        assert link_in_snap2.read_bytes() == b"x"  # link count 自然管理
