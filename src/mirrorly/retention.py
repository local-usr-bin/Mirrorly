"""保留策略（T-07，对应 M3 / ADR-008）。

删除模型：snapshot 版本目录 + 对应 manifest 整体删除。不做文件内容级
删除逻辑、不扫描引用关系——数据生命周期由 NTFS 硬链接 link count 自然
管理（被其他快照链接的文件在目录删除后仍然存在）。

安全边界：
- 只删除 status=complete 的快照；incomplete / 孤儿目录 / 无 manifest
  的目录一律不触碰；
- 计划完全基于 list_manifests()（不扫描 snapshots/ 目录做决策）；
- v2 生命周期排序只依据 manifest lifecycle_seq，不依赖 wall clock 或目录 mtime；
- legacy v1 complete 的真实顺序无法可靠重建，自动保留策略一律保护；
- 执行阶段不假设 RetentionPlan 来自 build_retention_plan：先统一对每个
  待删快照重新加载 manifest 并校验 status=complete（复用
  load_manifest(require_complete=True)），任一非法即整体拒绝，零删除；
- 删除顺序为「先移除 manifest，再删除快照目录」：
  - manifest 删除失败 → 数据与 manifest 均未动（完全一致状态）；
  - 快照目录删除失败 → manifest 已移除，残留（可能残缺的）目录成为
    孤儿目录，可被 recovery.scan_recovery 发现——宁可残留孤儿数据，
    也不留下「complete manifest 指向已删/残缺数据」的虚假可信记录；
- 删除失败显式抛 RetentionError，不静默跳过；
- 快照 id 做路径安全校验（防 ../ 与绝对路径）。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime

from .manifest import STATUS_COMPLETE, ManifestError, list_manifests, load_manifest
from .repo import RepoInfo
from .scan import to_long_path


class RetentionError(Exception):
    """保留策略相关错误（策略非法、删除失败、安全检查拒绝等）。"""


@dataclass(frozen=True)
class RetentionPlan:
    """保留计划：legacy 先列出，v2 keep/delete 按 lifecycle_seq 升序。"""

    keep: tuple[str, ...] = ()
    delete: tuple[str, ...] = ()


def _validate_snapshot_id(snapshot_id: str) -> None:
    """路径安全：快照 id 必须是单段相对名称。"""
    if (
        not snapshot_id
        or "/" in snapshot_id
        or "\\" in snapshot_id
        or snapshot_id in (".", "..")
        or (len(snapshot_id) > 1 and snapshot_id[1] == ":")  # Windows 盘符绝对路径
    ):
        raise RetentionError(f"非法快照 id: {snapshot_id!r}")


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """月份偏移（delta 为负向过去回溯）。"""
    total = (year * 12 + (month - 1)) + delta
    return total // 12, total % 12 + 1


def build_retention_plan(
    repo: RepoInfo,
    *,
    keep_last: int | None = None,
    keep_monthly: int | None = None,
) -> RetentionPlan:
    """基于 complete manifest 计算保留计划（只读，不删除任何内容）。

    - keep_last：保留 lifecycle_seq 最大的 N 个 v2 complete 快照
      （N=0 表示该规则不保留任何 v2 快照）；
    - keep_monthly：从当前月份向过去回溯 N 个月，每月保留该月最新的
      v2 complete（月份由 created_at 决定，同月 newest 由 lifecycle_seq
      决定；无快照的月份跳过）；
    - legacy v1 complete 的真实生命周期顺序不可靠，始终自动保护；
    - 两者同时指定时保留集合为 union；都不指定则报错。
    """
    if keep_last is None and keep_monthly is None:
        raise RetentionError("至少指定 keep_last 或 keep_monthly 之一")
    if keep_last is not None and keep_last < 0:
        raise RetentionError(f"keep_last 不能为负: {keep_last}")
    if keep_monthly is not None and keep_monthly < 0:
        raise RetentionError(f"keep_monthly 不能为负: {keep_monthly}")

    summaries = list_manifests(repo)
    complete = [s for s in summaries if s.status == STATUS_COMPLETE]
    legacy = sorted((s for s in complete if s.lifecycle_seq is None), key=lambda s: s.snapshot_id)
    sequenced = sorted(
        (s for s in complete if s.lifecycle_seq is not None),
        key=lambda s: s.lifecycle_seq,
    )

    keep: set[str] = {s.snapshot_id for s in legacy}
    if keep_last:
        keep.update(s.snapshot_id for s in sequenced[-keep_last:])
    if keep_monthly:
        # calendar month 仍是 wall-clock 语义；同月 lifecycle newest 则由
        # sequence 升序遍历、后者覆盖前者确定。
        by_month: dict[tuple[int, int], str] = {}
        for s in sequenced:
            dt = datetime.fromisoformat(s.created_at)
            by_month[(dt.year, dt.month)] = s.snapshot_id
        now = datetime.now(UTC)
        for i in range(keep_monthly):
            rep = by_month.get(_shift_month(now.year, now.month, -i))
            if rep is not None:
                keep.add(rep)

    return RetentionPlan(
        keep=tuple(s.snapshot_id for s in (*legacy, *sequenced) if s.snapshot_id in keep),
        delete=tuple(s.snapshot_id for s in sequenced if s.snapshot_id not in keep),
    )


def apply_retention_plan(
    repo: RepoInfo,
    plan: RetentionPlan,
    *,
    dry_run: bool = False,
) -> tuple[str, ...]:
    """执行保留计划：删除 delete 集合的快照目录与对应 manifest。

    dry_run=True 时只返回空结果，不删除任何内容。

    两阶段执行：
    1. 校验阶段（零删除）：不假设 plan 来自 build_retention_plan——对每个
       待删快照重新 load_manifest(require_complete=True)，incomplete /
       非法状态 / manifest 缺失或损坏均整体拒绝，任何删除都不会发生；
    2. 执行阶段：对每个快照先移除 manifest 再删除目录（失败安全顺序，
       见模块文档），任一失败显式抛 RetentionError（含已成功删除的列表），
       不静默跳过。
    """
    if dry_run:
        return ()

    # 阶段 1：执行前状态复核（零删除）
    for snapshot_id in plan.delete:
        _validate_snapshot_id(snapshot_id)
        try:
            load_manifest(repo, snapshot_id, require_complete=True)
        except ManifestError as e:
            raise RetentionError(
                f"拒绝删除快照 {snapshot_id!r}：manifest 校验失败（{e}）。"
                "仅允许删除 status=complete 的快照。"
            ) from e

    # 阶段 2：先 manifest 后目录的失败安全删除
    deleted: list[str] = []
    errors: list[str] = []
    for snapshot_id in plan.delete:
        manifest_file = repo.path / "manifests" / f"{snapshot_id}.json"
        snap_dir = repo.path / "snapshots" / snapshot_id
        try:
            manifest_file.unlink()
        except OSError as e:
            errors.append(f"{snapshot_id}: manifest 删除失败（数据未触碰）: {e}")
            continue
        try:
            if snap_dir.exists():
                shutil.rmtree(to_long_path(snap_dir))
        except OSError as e:
            errors.append(
                f"{snapshot_id}: 快照目录删除失败（manifest 已移除，"
                f"残留目录为孤儿，可被 scan_recovery 发现）: {e}"
            )
            continue
        deleted.append(snapshot_id)

    if errors:
        raise RetentionError(
            f"部分快照删除失败: {'; '.join(errors)}（已成功删除: {deleted or '无'}）"
        )
    return tuple(deleted)
