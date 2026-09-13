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
    ManifestError,
    create_manifest,
    list_manifests,
    load_manifest,
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


class TestDeletionSafetyHardening:
    """T-07 safety hardening：执行阶段状态复核 + 删除失败安全方向。"""

    def test_apply_rejects_incomplete_in_handmade_plan(self, tmp_path) -> None:
        """手工构造指向 incomplete 的计划必须被拒绝，目录与 manifest 均不变。"""
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "wip", _iso("2026-09-01"), status=STATUS_INCOMPLETE)
        manifest_file = repo.path / "manifests" / "wip.json"
        before = manifest_file.read_bytes()

        plan = RetentionPlan(keep=(), delete=("wip",))
        with pytest.raises(RetentionError, match="wip"):
            apply_retention_plan(repo, plan)

        assert (repo.path / "snapshots" / "wip").is_dir()  # 数据未动
        assert manifest_file.read_bytes() == before  # manifest 未动

    def test_apply_rejects_unknown_status_manifest(self, tmp_path) -> None:
        """非法 status（非 complete）同样拒绝，不静默继续。"""
        import json

        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "snap1", _iso("2026-09-01"))
        manifest_file = repo.path / "manifests" / "snap1.json"
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
        data["status"] = "corrupted-state"
        manifest_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        before = manifest_file.read_bytes()

        plan = RetentionPlan(keep=(), delete=("snap1",))
        with pytest.raises(RetentionError):
            apply_retention_plan(repo, plan)

        assert (repo.path / "snapshots" / "snap1").is_dir()
        assert manifest_file.read_bytes() == before

    def test_validation_failure_aborts_before_any_deletion(self, tmp_path) -> None:
        """计划中混有非法项时：整体拒绝，合法项也不被删除（先验证后执行）。"""
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "good", _iso("2026-09-01"))
        _make_snapshot(repo, "wip", _iso("2026-09-02"), status=STATUS_INCOMPLETE)

        plan = RetentionPlan(keep=(), delete=("good", "wip"))
        with pytest.raises(RetentionError):
            apply_retention_plan(repo, plan)

        assert (repo.path / "snapshots" / "good").is_dir()  # 合法项也未被删
        assert (repo.path / "manifests" / "good.json").is_file()

    def test_manifest_unlink_failure_preserves_snapshot_data(self, tmp_path, monkeypatch) -> None:
        """manifest 删除失败：快照数据必须完好，manifest 仍可被视为 complete（一致状态）。"""
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "snap1", _iso("2026-09-01"))
        manifest_file = repo.path / "manifests" / "snap1.json"
        snap_file = repo.path / "snapshots" / "snap1" / "a.txt"
        snap_file.write_bytes(b"important")

        real_unlink = Path.unlink

        def boom(self, *args, **kwargs):
            if self == manifest_file:
                raise OSError("manifest locked")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", boom)
        plan = RetentionPlan(keep=(), delete=("snap1",))
        with pytest.raises(RetentionError, match="snap1"):
            apply_retention_plan(repo, plan)
        monkeypatch.undo()

        # 数据未被破坏，manifest 仍可正常按 complete 加载（无虚假/无残缺）
        assert snap_file.read_bytes() == b"important"
        m = load_manifest(repo, "snap1", require_complete=True)
        assert m.snapshot_id == "snap1"

    def test_rmtree_failure_leaves_no_fake_complete_manifest(self, tmp_path, monkeypatch) -> None:
        """snapshot 删除失败：不得留下"complete manifest + 已损数据"的可信假象。

        安全方向：manifest 先移除，残留目录成为孤儿（可被 scan_recovery 发现），
        而不是一条指向缺失数据的 complete 记录。
        """
        import shutil as shutil_mod

        from mirrorly.recovery import scan_recovery

        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "snap1", _iso("2026-09-01"))
        snap_dir = repo.path / "snapshots" / "snap1"

        def boom(_path):
            raise OSError("disk error")

        monkeypatch.setattr(shutil_mod, "rmtree", boom)
        plan = RetentionPlan(keep=(), delete=("snap1",))
        with pytest.raises(RetentionError, match="snap1"):
            apply_retention_plan(repo, plan)
        monkeypatch.undo()

        # 不存在可被当作 complete 的 manifest
        assert not (repo.path / "manifests" / "snap1.json").exists()
        with pytest.raises(ManifestError):
            load_manifest(repo, "snap1", require_complete=True)
        # 数据残留为孤儿目录（安全方向：保留数据而非虚假记录）
        assert snap_dir.is_dir()
        report = scan_recovery(repo)
        assert "snap1" in report.orphan_dirs

    def test_normal_deletion_still_in_sync_after_hardening(self, tmp_path) -> None:
        """正常路径：snapshot + manifest 同步消失（加固不改变成功语义）。"""
        repo = _init_repo(tmp_path / "target")
        for i in range(1, 4):
            _make_snapshot(repo, f"snap{i}", _iso(f"2026-09-0{i}"))
        plan = build_retention_plan(repo, keep_last=1)

        deleted = apply_retention_plan(repo, plan)

        assert set(deleted) == {"snap1", "snap2"}
        assert not (repo.path / "snapshots" / "snap1").exists()
        assert not (repo.path / "manifests" / "snap1.json").exists()
        assert (repo.path / "snapshots" / "snap3").is_dir()
        assert (repo.path / "manifests" / "snap3.json").is_file()
