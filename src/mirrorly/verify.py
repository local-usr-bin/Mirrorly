"""完整性校验（T-06，对应 M5 / TR-3 / ADR-009）。

职责边界：
- 本模块负责 manifest 读取、快照完整性检查、哈希比对与报告生成；
- 只检测和报告，不负责修复（修复属 restore/T-08）；
- 退出码 0/4 为 CLI 层语义（T-09），本层返回结构化 VerifyReport。

quick 模式只检查存在性/类型/大小，不调用 hash_file（TR-6：TB 级数据的
快速巡检）；全量模式逐文件重算哈希与 manifest sha 比对。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .hashing import hash_file
from .manifest import load_manifest
from .repo import RepoInfo
from .scan import to_long_path

#: VerifyIssue.kind 取值
KIND_MISSING = "missing"
KIND_TYPE_MISMATCH = "type_mismatch"
KIND_SIZE_MISMATCH = "size_mismatch"
KIND_CORRUPT = "corrupt"


@dataclass(frozen=True)
class VerifyIssue:
    """单条完整性问题（影响结论）。"""

    path: str  # 相对快照根的 POSIX 风格路径
    kind: str
    detail: str = ""


@dataclass(frozen=True)
class VerifyReport:
    """一次 verify 的结构化报告。ok 为 True 表示快照完整。"""

    snapshot_id: str
    quick: bool
    checked_files: int = 0
    checked_dirs: int = 0
    hashed_files: int = 0
    unhashed_entries: int = 0  # manifest 中 sha=None 的条目（T-06 前的旧快照）
    issues: tuple[VerifyIssue, ...] = ()
    extras: tuple[str, ...] = ()  # 快照中存在但清单未记录的文件（仅报告）

    @property
    def ok(self) -> bool:
        return not self.issues


def verify_snapshot(repo: RepoInfo, snapshot_id: str, *, quick: bool = False) -> VerifyReport:
    """校验一个 complete 快照与 manifest 的一致性。

    - manifest 必须 complete（incomplete 快照不可校验，ManifestError 透传）；
    - 全量模式：存在性/类型/大小检查 + 逐文件哈希比对（sha=None 的条目跳过
      哈希并计入 unhashed_entries）；
    - quick 模式：只做存在性/类型/大小检查，不调用 hash_file；
    - 快照目录中多出清单未记录的文件收入 extras，不影响 ok。
    """
    manifest = load_manifest(repo, snapshot_id, require_complete=True)
    snap_dir = repo.path / "snapshots" / snapshot_id
    algorithm = manifest.hash_algorithm or repo.hash_algorithm

    issues: list[VerifyIssue] = []
    checked_files = 0
    checked_dirs = 0
    hashed_files = 0
    unhashed_entries = 0
    manifest_paths: set[str] = set()

    for entry in manifest.entries:
        manifest_paths.add(entry.path)
        target_lp = to_long_path(snap_dir / Path(entry.path))
        if entry.is_dir:
            checked_dirs += 1
            if not os.path.exists(target_lp):
                issues.append(VerifyIssue(entry.path, KIND_MISSING, "目录不存在"))
            elif not os.path.isdir(target_lp):
                issues.append(VerifyIssue(entry.path, KIND_TYPE_MISMATCH, "清单记录为目录"))
            continue

        checked_files += 1
        if not os.path.exists(target_lp):
            issues.append(VerifyIssue(entry.path, KIND_MISSING, "文件不存在"))
            continue
        if not os.path.isfile(target_lp):
            issues.append(VerifyIssue(entry.path, KIND_TYPE_MISMATCH, "清单记录为文件"))
            continue
        actual_size = os.stat(target_lp).st_size
        if actual_size != entry.size:
            issues.append(
                VerifyIssue(
                    entry.path,
                    KIND_SIZE_MISMATCH,
                    f"清单记录 {entry.size}，实际 {actual_size}",
                )
            )
            continue
        if quick:
            continue
        if entry.sha is None:
            unhashed_entries += 1
            continue
        hashed_files += 1
        actual_sha = hash_file(target_lp, algorithm)
        if actual_sha != entry.sha:
            issues.append(VerifyIssue(entry.path, KIND_CORRUPT, "哈希与清单不一致"))

    extras: list[str] = []
    if snap_dir.is_dir():
        for dirpath, _dirnames, filenames in os.walk(to_long_path(snap_dir)):
            base = Path(str(dirpath).removeprefix("\\\\?\\"))
            for name in filenames:
                rel = (base / name).relative_to(snap_dir).as_posix()
                if rel not in manifest_paths:
                    extras.append(rel)

    return VerifyReport(
        snapshot_id=snapshot_id,
        quick=quick,
        checked_files=checked_files,
        checked_dirs=checked_dirs,
        hashed_files=hashed_files,
        unhashed_entries=unhashed_entries,
        issues=tuple(issues),
        extras=tuple(sorted(extras)),
    )
