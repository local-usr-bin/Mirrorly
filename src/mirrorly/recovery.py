"""中断恢复（T-05，对应 M7 / TR-5）。

设计要点（ADR-005 / ADR-010 不变量）：

- **续传不复用原目录**：incomplete 快照目录只作为只读硬链接基线，
  续传产出全新 snapshot id，不引入第二套写入逻辑（复用 T-03 write_snapshot）；
- **基线一致性校验**：build_resume_baseline 校验快照目录存在、
  目录中已存在文件与清单记录一致（大小、类型）——矛盾即 RecoveryError，
  不静默继续；清单中尚未复制的条目（正常中断态）显式收入 missing 报告；
- **tmp 残留清理**：只删除可证明为 Mirrorly staging residue 的文件
  （判定见 :func:`_provable_tmp_residues`，文件名后缀不足以证明 ownership），
  且仅限 incomplete 快照——**complete 快照对清理绝对只读**（发布后零操作）；
  manifests.tmp/ 为自有 metadata staging 目录，清理语义不变；不触碰正式数据。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .manifest import (
    STATUS_COMPLETE,
    STATUS_INCOMPLETE,
    ManifestError,
    ManifestSummary,
    list_manifests,
    load_manifest,
)
from .repo import RepoInfo
from .scan import PreviousEntry, to_long_path
from .snapshot import TMP_SUFFIX


class RecoveryError(Exception):
    """中断恢复相关错误（基线不一致、状态非法、目录缺失等）。"""


@dataclass(frozen=True)
class RecoveryReport:
    """仓库恢复状态扫描结果（只报告，不做任何修改）。"""

    incomplete: tuple[ManifestSummary, ...] = ()
    orphan_dirs: tuple[str, ...] = ()  # snapshots/ 下无对应 manifest 的目录名
    tmp_residue: tuple[str, ...] = ()  # 可证明为 Mirrorly staging residue 的仓库相对路径
    manifest_tmp_residue: tuple[str, ...] = ()  # manifests.tmp/ 内残留文件名


@dataclass(frozen=True)
class ResumeBaseline:
    """续传基线：供 detect_changes + write_snapshot 组合使用。

    - previous/previous_dirs：incomplete 清单中已确认存在于快照目录的条目；
    - missing：清单中记录但尚未复制的文件（正常中断态，调用方应重新复制，
      detect_changes 会因基线中无记录而将其判为 added）。
    """

    snapshot_id: str
    snapshot_path: Path
    previous: dict[str, PreviousEntry]
    previous_dirs: frozenset[str]
    missing: tuple[str, ...] = ()


def _validate_snapshot_id(snapshot_id: str) -> None:
    """防路径穿越：快照 id 必须是单段安全名称。"""
    if not snapshot_id or "/" in snapshot_id or "\\" in snapshot_id or snapshot_id in (".", ".."):
        raise RecoveryError(f"非法快照 id: {snapshot_id!r}")


def _unprefix(path_str: str) -> str:
    """去掉 os.walk(os 长路径) 返回的 ``\\\\?\\`` 前缀，便于相对路径计算。"""
    return path_str.removeprefix("\\\\?\\")


def _try_load_snap_manifest(repo: RepoInfo, snap_dir: Path):
    """加载快照目录对应的 manifest；失败（孤儿/损坏清单）返回 None。"""
    try:
        return load_manifest(repo, snap_dir.name)
    except (ManifestError, OSError, ValueError, KeyError):
        return None


def _provable_tmp_residues(repo: RepoInfo, snap_dir: Path, manifest=None) -> list[tuple[str, Path]]:
    """枚举快照目录中可证明为 Mirrorly staging residue 的文件。

    返回 ``(仓库相对 POSIX 路径, 绝对路径)`` 列表。ownership 证明必须同时满足：

    1. 文件名以 TMP_SUFFIX 结尾——Mirrorly temp 命名规则是
       ``<目标名>.mrtmp``（见 snapshot._copy_file_atomic），这只是候选信号；
    2. 去掉后缀的目标路径（stem）是该快照 manifest 中的**文件**条目——
       write_snapshot 只为 manifest 规划的最终文件创建 staging temp，
       且 manifest 先于物化落盘，因此真实残留的 stem 必然可追溯到清单；
    3. 文件自身路径**不是** manifest 条目——用户文件可以合法地以
       .mrtmp 结尾（并非 Windows 保留扩展名），清单记录的路径永远是用户数据。

    manifest 缺失或无法加载（孤儿目录、清单损坏）时无法证明 ownership，
    返回空列表——调用方必须保留文件不删（fail safe）。

    ``manifest`` 可传入已加载的实例以避免重复 IO；None 时自行加载。
    本函数只做 ownership 判定，**不区分 complete/incomplete**——
    complete 快照的只读保护由 :func:`clean_tmp_residue` 显式执行，
    scan_recovery 仅报告，报告不构成删除权限。
    """
    if manifest is None:
        manifest = _try_load_snap_manifest(repo, snap_dir)
        if manifest is None:
            return []
    manifested = {e.path for e in manifest.entries}
    manifested_files = {e.path for e in manifest.entries if not e.is_dir}
    residues: list[tuple[str, Path]] = []
    for dirpath, _dirnames, filenames in os.walk(to_long_path(snap_dir)):
        for name in filenames:
            if not name.endswith(TMP_SUFFIX):
                continue
            rel = (Path(_unprefix(dirpath)) / name).relative_to(snap_dir).as_posix()
            if rel in manifested:
                continue  # 清单记录的用户文件，即使以 .mrtmp 结尾也绝不动
            if rel[: -len(TMP_SUFFIX)] in manifested_files:
                residues.append((f"snapshots/{snap_dir.name}/{rel}", Path(dirpath) / name))
    return residues


def scan_recovery(repo: RepoInfo) -> RecoveryReport:
    """扫描仓库中的可恢复状态：incomplete 清单、孤儿目录、tmp 残留。"""
    summaries = list_manifests(repo)
    incomplete = tuple(s for s in summaries if s.status != STATUS_COMPLETE)
    manifested_ids = {s.snapshot_id for s in summaries}

    snapshots_root = repo.path / "snapshots"
    orphan_dirs: list[str] = []
    tmp_residue: list[str] = []
    if snapshots_root.is_dir():
        for child in sorted(os.listdir(to_long_path(snapshots_root))):
            child_path = snapshots_root / child
            if not child_path.is_dir():
                continue
            if child not in manifested_ids:
                orphan_dirs.append(child)
            # 只报告可证明为 Mirrorly staging residue 的文件（与清理同一判定）；
            # 无法证明 ownership 的（如用户文件名恰以 .mrtmp 结尾）不误报
            tmp_residue.extend(rel for rel, _p in _provable_tmp_residues(repo, child_path))

    manifest_tmp_dir = repo.path / "manifests.tmp"
    manifest_tmp_residue: list[str] = []
    if manifest_tmp_dir.is_dir():
        manifest_tmp_residue = sorted(
            name
            for name in os.listdir(to_long_path(manifest_tmp_dir))
            if (manifest_tmp_dir / name).is_file()
        )

    return RecoveryReport(
        incomplete=incomplete,
        orphan_dirs=tuple(orphan_dirs),
        tmp_residue=tuple(sorted(tmp_residue)),
        manifest_tmp_residue=tuple(manifest_tmp_residue),
    )


def clean_tmp_residue(repo: RepoInfo, snapshot_id: str | None = None) -> list[str]:
    """删除可证明为 Mirrorly staging residue 的临时文件，返回已清理的仓库相对路径。

    ownership 判定见 :func:`_provable_tmp_residues`：文件名以 .mrtmp 结尾
    **不足以**证明是 Mirrorly 临时文件——用户文件可以合法使用该后缀
    （B1-1 回归）。只有「stem 是该快照 manifest 文件条目、且自身不在清单中」
    的路径才是写入路径留下的 staging residue；无法证明 ownership 的一律保留。

    manifests.tmp/ 为 Mirrorly 自有 metadata staging 目录（写入路径固定、
    目录名即保留命名空间），清理语义不变。snapshot_id 为 None 时清理全部
    快照目录；指定时仅清理该快照目录。

    **complete 快照绝对只读**（ADR-010 / B1-1 architecture review invariant）：
    删除授权是**正向条件**——只有 manifest.status == STATUS_INCOMPLETE 的
    快照目录才允许进入 ownership 清理；complete、未知 status（manifest
    parser 对 status 无白名单校验，可成功加载）、manifest 加载失败
    （孤儿/损坏）一律零操作。fail safe：宁可残留不误删。
    """
    _validate_snapshot_id(snapshot_id) if snapshot_id else None
    cleaned: list[str] = []

    snapshots_root = repo.path / "snapshots"
    if snapshot_id is not None:
        targets = [snapshots_root / snapshot_id]
    elif snapshots_root.is_dir():
        targets = [
            snapshots_root / name
            for name in sorted(os.listdir(to_long_path(snapshots_root)))
            if (snapshots_root / name).is_dir()
        ]
    else:
        targets = []
    for snap_dir in targets:
        if not snap_dir.is_dir():
            continue
        manifest = _try_load_snap_manifest(repo, snap_dir)
        if manifest is None or manifest.status != STATUS_INCOMPLETE:
            # 正向删除授权：仅 incomplete 快照允许清理；
            # complete / 未知 status / 无法加载 manifest 均零操作
            continue
        for rel, path in _provable_tmp_residues(repo, snap_dir, manifest=manifest):
            path.unlink()
            cleaned.append(rel)

    manifest_tmp_dir = repo.path / "manifests.tmp"
    if manifest_tmp_dir.is_dir():
        for name in sorted(os.listdir(to_long_path(manifest_tmp_dir))):
            p = manifest_tmp_dir / name
            if p.is_file():
                p.unlink()
                cleaned.append(p.relative_to(repo.path).as_posix())

    return sorted(cleaned)


def build_resume_baseline(repo: RepoInfo, snapshot_id: str) -> ResumeBaseline:
    """以 incomplete 快照构建续传基线（只读校验，不修改任何数据）。

    校验（任一不满足即 RecoveryError，不静默继续）：
    - manifest 存在且 status=incomplete（complete 快照无需续传）；
    - snapshots/<id> 目录存在；
    - 清单中已存在于目录的条目与记录一致（文件大小、文件/目录类型）。

    清单中记录但目录中缺失的文件属于正常中断态，收入 missing 显式报告，
    不纳入 previous（detect_changes 会将其判为 added 重新复制）。
    """
    _validate_snapshot_id(snapshot_id)
    try:
        manifest = load_manifest(repo, snapshot_id, require_complete=False)
    except ManifestError as exc:
        raise RecoveryError(f"无法加载清单 {snapshot_id!r}: {exc}") from exc
    if manifest.status == STATUS_COMPLETE:
        raise RecoveryError(f"快照 {snapshot_id!r} 状态为 complete，无需续传")

    snap_dir = repo.path / "snapshots" / snapshot_id
    if not snap_dir.is_dir():
        raise RecoveryError(f"incomplete 快照目录不存在: {snap_dir}（清单与数据不一致）")

    previous: dict[str, PreviousEntry] = {}
    previous_dirs: set[str] = set()
    missing: list[str] = []
    for entry in manifest.entries:
        target = snap_dir / Path(entry.path)
        if entry.is_dir:
            previous_dirs.add(entry.path)
            if target.exists() and not target.is_dir():
                raise RecoveryError(
                    f"清单与快照内容类型不一致: {entry.path!r} 清单记录为目录，实际不是目录"
                )
            continue
        if not target.exists():
            missing.append(entry.path)
            continue
        if not target.is_file():
            raise RecoveryError(
                f"清单与快照内容类型不一致: {entry.path!r} 清单记录为文件，实际不是文件"
            )
        actual_size = target.stat().st_size
        if actual_size != entry.size:
            raise RecoveryError(
                f"清单与快照内容不一致: {entry.path!r} "
                f"清单记录大小 {entry.size}，实际 {actual_size}"
            )
        previous[entry.path] = PreviousEntry(
            size=entry.size, mtime_ns=entry.mtime_ns, sha=entry.sha
        )

    return ResumeBaseline(
        snapshot_id=snapshot_id,
        snapshot_path=snap_dir,
        previous=previous,
        previous_dirs=frozenset(previous_dirs),
        missing=tuple(sorted(missing)),
    )


def discard_incomplete(repo: RepoInfo, snapshot_id: str) -> None:
    """显式删除 incomplete 快照目录与对应 manifest（续传成功后善后）。

    硬链接语义下数据已由新快照持有，删除安全。complete 快照显式拒绝，
    防止误删正式备份。
    """
    _validate_snapshot_id(snapshot_id)
    try:
        manifest = load_manifest(repo, snapshot_id, require_complete=False)
    except ManifestError as exc:
        raise RecoveryError(f"无法加载清单 {snapshot_id!r}: {exc}") from exc
    if manifest.status == STATUS_COMPLETE:
        raise RecoveryError(f"拒绝删除 complete 快照: {snapshot_id!r}")

    snap_dir = repo.path / "snapshots" / snapshot_id
    if snap_dir.exists():
        shutil.rmtree(to_long_path(snap_dir))
    manifest_file = repo.path / "manifests" / f"{snapshot_id}.json"
    if manifest_file.exists():
        manifest_file.unlink()
