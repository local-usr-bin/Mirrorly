"""快照写入引擎（T-03，ADR-005 / TR-2 不变量）。

安全纪律（不可违反）：
1. **旧快照永不修改**：所有写入只发生在新快照目录内；已完成的快照目录
   不被任何写操作触碰。
2. **绝不原地写已链接文件**：变更文件写入 ``<name>.mrtmp`` 临时文件，
   flush + fsync + 关闭后经复测并在 staging 上设置 mtime，再由
   ``os.replace`` 原子改名；os.replace 的目标是新快照中的新路径，绝不
   覆盖旧快照中通过硬链接共享的文件。
3. **os.link 失败显式报错**（SnapshotError），不静默降级（TR-2）。

变动中文件（TR-4）：复制完成后复测源文件 size/mtime_ns，与扫描时不一致
则删除临时文件、记入 skipped，下次备份自然收敛。

边界：manifest 落盘（T-04）、中断续传（T-05）、写入即哈希校验（T-06）
均不在本模块职责内。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .hashing import hash_file
from .repo import RepoInfo
from .scan import ChangeSet, ScannedEntry, to_long_path

#: 复制缓冲区大小（TR-6：大文件流式复制）
_COPY_BUFFER_SIZE = 1024 * 1024

#: 临时文件后缀（写入中标识，崩溃残留可被安全识别/清理）
TMP_SUFFIX = ".mrtmp"


class SnapshotError(Exception):
    """快照写入相关错误（硬链接失败、快照已存在等）。"""


@dataclass(frozen=True)
class SnapshotResult:
    """一次快照写入的结果报告。

    hashes：verify_writes=True 时每个 copied 文件的写入校验哈希（供
    create_manifest 持久化）；linked 文件不在其中——其 sha 由调用方从
    上一 manifest 结转（保持本层不感知 previous manifest）。
    """

    snapshot_id: str
    path: Path
    linked: tuple[str, ...] = ()
    copied: tuple[str, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()  # (相对路径, 原因)
    bytes_written: int = 0
    dirs_created: int = 0
    hashes: dict[str, str] = field(default_factory=dict)


def generate_snapshot_id(now: datetime | None = None) -> str:
    """生成快照 id（本地时间，格式 YYYY-MM-DD_HHMMSS）。"""
    return (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")


def write_snapshot(
    source: str | Path,
    repo: RepoInfo,
    current: Mapping[str, ScannedEntry],
    changes: ChangeSet,
    *,
    snapshot_id: str | None = None,
    previous_snapshot: str | Path | None = None,
    verify_writes: bool = False,
) -> SnapshotResult:
    """按变更集把当前源状态物化为一个新快照目录树。

    - current 中不在 added/modified 的文件（含 suspected_modified）视为未变：
      有上一快照且仓库启用硬链接时 os.link 复用，否则复制；
    - added/modified 文件走"临时文件 + fsync + 复测 + staging mtime + 原子改名"；
    - deleted 的文件/目录自然缺席新快照，旧快照不受影响；
    - verify_writes=True 时（T-06 写入即校验）：每个 copied 文件在原子改名后
      重算目标端哈希与源端比对，不一致仅重试该文件一次，仍失败抛
      SnapshotError；默认 False 保持 T-03 行为不变。
    """
    source = Path(source)
    snapshot_id = snapshot_id or generate_snapshot_id()
    snap_dir = repo.path / "snapshots" / snapshot_id
    if snap_dir.exists():
        raise SnapshotError(f"快照 id 已存在: {snapshot_id}（拒绝覆盖半成品或历史快照）")
    Path(to_long_path(snap_dir)).mkdir(parents=True)

    prev_dir = Path(previous_snapshot) if previous_snapshot else None
    changed = set(changes.added) | set(changes.modified)

    linked: list[str] = []
    copied: list[str] = []
    skipped: list[tuple[str, str]] = []
    hashes: dict[str, str] = {}
    bytes_written = 0
    dirs_created = 0

    # 1) 目录重建（空目录保留）
    for rel in sorted(current):
        if current[rel].is_dir:
            Path(to_long_path(snap_dir / Path(rel))).mkdir(parents=True, exist_ok=True)
            dirs_created += 1

    # 2) 文件物化
    for rel in sorted(current):
        entry = current[rel]
        if entry.is_dir:
            continue
        dst = snap_dir / Path(rel)
        Path(to_long_path(dst.parent)).mkdir(parents=True, exist_ok=True)
        src_file = source / Path(rel)

        prev_file = prev_dir / Path(rel) if prev_dir else None
        if rel not in changed and prev_file is not None and prev_file.exists():
            if repo.hardlinks:
                _link_file(prev_file, dst, rel)
                linked.append(rel)
                continue
            # hardlinks=False（exFAT warn 模式）：显式降级为整文件复制
        # 变更文件 / 无上一快照 / 不可链接：复制
        ok = _copy_file_atomic(src_file, dst, entry)
        if ok:
            if verify_writes:
                hashes[rel] = _verify_write(src_file, dst, entry, repo.hash_algorithm)
            copied.append(rel)
            bytes_written += entry.size
        else:
            skipped.append((rel, "复制期间源文件发生变动，已跳过（下次备份自动收敛）"))

    return SnapshotResult(
        snapshot_id=snapshot_id,
        path=snap_dir,
        linked=tuple(linked),
        copied=tuple(copied),
        skipped=tuple(skipped),
        bytes_written=bytes_written,
        dirs_created=dirs_created,
        hashes=hashes,
    )


def _link_file(prev_file: Path, dst: Path, rel: str) -> None:
    """建立硬链接复用上一快照内容；失败显式报错（TR-2：不静默降级）。"""
    try:
        os.link(to_long_path(prev_file), to_long_path(dst))
    except OSError as e:
        raise SnapshotError(f"硬链接失败: {rel}（{e}）") from e


def _verify_write(src_file: Path, dst: Path, entry: ScannedEntry, algorithm: str) -> str:
    """写入即校验（T-06 / ADR-009）：原子改名后重算目标端哈希与源端比对。

    不一致时仅重试该文件一次（重新复制再校验）；仍失败抛 SnapshotError。
    返回目标端哈希（供 manifest 持久化）。
    """
    src_lp = to_long_path(src_file)
    dst_lp = to_long_path(dst)
    for attempt in (1, 2):
        src_hash = hash_file(src_lp, algorithm)
        dst_hash = hash_file(dst_lp, algorithm)
        if src_hash == dst_hash:
            return dst_hash
        if attempt == 1:
            # 仅重试该文件一次：重新复制（完整走临时文件+原子改名流程）
            if not _copy_file_atomic(src_file, dst, entry):
                raise SnapshotError(f"写入校验重试时源文件发生变动: {src_file}")
    raise SnapshotError(f"写入校验失败（重试一次后哈希仍不一致）: {dst}")


def _copy_file_atomic(src_file: Path, dst: Path, entry: ScannedEntry) -> bool:
    """临时文件 + fsync + 复测 + staging mtime + 原子改名复制单个文件。

    返回 True 表示写入完成；False 表示复制期间源文件发生变动（TR-4，
    临时文件已清理，目标未产生任何内容）。
    """
    src_lp = to_long_path(src_file)
    tmp = dst.with_name(dst.name + TMP_SUFFIX)
    tmp_lp = to_long_path(tmp)
    try:
        with open(src_lp, "rb") as fin, open(tmp_lp, "wb") as fout:
            while chunk := fin.read(_COPY_BUFFER_SIZE):
                fout.write(chunk)
            fout.flush()
            os.fsync(fout.fileno())
        # 复测：复制期间源文件是否变动（大小或 mtime 与扫描时不一致）
        st_after = os.stat(src_lp)
        if st_after.st_size != entry.size or st_after.st_mtime_ns != entry.mtime_ns:
            os.remove(tmp_lp)
            return False
        # mtime 是 snapshot entry 正确性的一部分，必须在 staging phase 完成；
        # 失败时清理 temp，绝不能先发布 dst 再留下 metadata half-published entry。
        # 注意：Windows FILETIME 粒度 100ns，os.utime 会截断，属平台限制。
        os.utime(tmp_lp, ns=(st_after.st_atime_ns, entry.mtime_ns))
        # 单文件 publication point：content 与所需 mtime 均准备好后才公开。
        os.replace(tmp_lp, to_long_path(dst))
        return True
    except OSError:
        # 清理临时文件，错误向上抛（IO 错误不应留下半成品临时文件）
        try:
            if os.path.exists(tmp_lp):
                os.remove(tmp_lp)
        finally:
            raise
