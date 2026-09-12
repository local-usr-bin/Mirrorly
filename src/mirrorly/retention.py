"""保留策略（T-07，对应 M3 / ADR-008）。

删除模型：snapshot 版本目录 + 对应 manifest 整体删除。不做文件内容级
删除逻辑、不扫描引用关系——数据生命周期由 NTFS 硬链接 link count 自然
管理（被其他快照链接的文件在目录删除后仍然存在）。

安全边界：
- 只删除 status=complete 的快照；incomplete / 孤儿目录 / 无 manifest
  的目录一律不触碰；
- 计划完全基于 list_manifests()（不扫描 snapshots/ 目录做决策）；
- 排序依据 manifest created_at 字段，不依赖目录 mtime；
- 删除失败显式抛 RetentionError，不静默跳过；
- 快照 id 做路径安全校验（防 ../ 与绝对路径）。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime

from .manifest import STATUS_COMPLETE, list_manifests
from .repo import RepoInfo
from .scan import to_long_path


class RetentionError(Exception):
    """保留策略相关错误（策略非法、删除失败、安全检查拒绝等）。"""


@dataclass(frozen=True)
class RetentionPlan:
    """保留计划：keep/delete 均为快照 id，按 created_at 升序。"""

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

    - keep_last：保留最近 N 个 complete 快照（按 manifest created_at 排序，
      N=0 表示该规则不保留任何快照）；
    - keep_monthly：从当前月份向过去回溯 N 个月，每月保留该月最新的
      complete 快照（无快照的月份跳过）；
    - 两者同时指定时保留集合为 union；都不指定则报错。
    """
    if keep_last is None and keep_monthly is None:
        raise RetentionError("至少指定 keep_last 或 keep_monthly 之一")
    if keep_last is not None and keep_last < 0:
        raise RetentionError(f"keep_last 不能为负: {keep_last}")
    if keep_monthly is not None and keep_monthly < 0:
        raise RetentionError(f"keep_monthly 不能为负: {keep_monthly}")

    complete = sorted(
        (s for s in list_manifests(repo) if s.status == STATUS_COMPLETE),
        key=lambda s: (s.created_at, s.snapshot_id),
    )

    keep: set[str] = set()
    if keep_last:
        keep.update(s.snapshot_id for s in complete[-keep_last:])
    if keep_monthly:
        # 每月代表：该月 created_at 最新的 complete（列表升序，后者覆盖前者）
        by_month: dict[tuple[int, int], str] = {}
        for s in complete:
            dt = datetime.fromisoformat(s.created_at)
            by_month[(dt.year, dt.month)] = s.snapshot_id
        now = datetime.now(UTC)
        for i in range(keep_monthly):
            rep = by_month.get(_shift_month(now.year, now.month, -i))
            if rep is not None:
                keep.add(rep)

    return RetentionPlan(
        keep=tuple(s.snapshot_id for s in complete if s.snapshot_id in keep),
        delete=tuple(s.snapshot_id for s in complete if s.snapshot_id not in keep),
    )


def apply_retention_plan(
    repo: RepoInfo,
    plan: RetentionPlan,
    *,
    dry_run: bool = False,
) -> tuple[str, ...]:
    """执行保留计划：删除 delete 集合的快照目录与对应 manifest。

    dry_run=True 时只返回空结果，不删除任何内容。
    删除前再次确认 manifest 存在（防计划与执行间隔内被外部改动）；
    任一失败显式抛 RetentionError（含已成功删除的列表），不静默跳过。
    """
    if dry_run:
        return ()

    deleted: list[str] = []
    errors: list[str] = []
    for snapshot_id in plan.delete:
        _validate_snapshot_id(snapshot_id)
        manifest_file = repo.path / "manifests" / f"{snapshot_id}.json"
        if not manifest_file.is_file():
            raise RetentionError(
                f"manifest 缺失，拒绝删除快照 {snapshot_id!r}（已删除: {deleted or '无'}）"
            )
        try:
            snap_dir = repo.path / "snapshots" / snapshot_id
            if snap_dir.exists():
                shutil.rmtree(to_long_path(snap_dir))
            manifest_file.unlink()
            deleted.append(snapshot_id)
        except OSError as e:
            errors.append(f"{snapshot_id}: {e}")

    if errors:
        raise RetentionError(
            f"部分快照删除失败: {'; '.join(errors)}（已成功删除: {deleted or '无'}）"
        )
    return tuple(deleted)
