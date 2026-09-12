"""源扫描与变更检测（T-02，ADR-006）。

策略：清单记录（路径、大小、mtime_ns、哈希）；大小或 mtime 变化的文件经
BLAKE3 流式哈希复核后判定变更；其余信任元数据。

- 长路径：Windows 下对文件系统调用统一加 ``\\\\?\\`` 前缀（TR-6）；
- Unicode：相对键一律用 POSIX 风格字符串（``/`` 分隔），不做编码转换；
- 排除规则（glob）：尾斜杠模式只匹配目录（整树剪枝），其余只匹配文件；
  含 ``/`` 的模式匹配相对路径，否则匹配任意层级的名称。

本模块只产出扫描结果与变更集，不做任何写入（快照写入属 T-03，
manifest 落盘格式属 T-04；PreviousEntry 是 T-02 消费上一清单的最小接口）。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from .hashing import hash_file

_DEFAULT_ALGORITHM = None  # 使用 hashing.default_algorithm()


@dataclass(frozen=True)
class ScannedEntry:
    """扫描到的单个条目（文件或目录）。"""

    path: str  # 相对源根的 POSIX 风格路径
    size: int
    mtime_ns: int
    is_dir: bool


@dataclass(frozen=True)
class ScanResult:
    """一次源扫描的结果。entries 键为相对路径（POSIX 风格）。"""

    entries: dict[str, ScannedEntry]
    skipped: tuple[tuple[str, str], ...] = ()  # (路径, 原因)：stat 失败等


@dataclass(frozen=True)
class PreviousEntry:
    """上一快照清单中单个文件的最小记录（T-04 的 manifest 将提供此数据）。"""

    size: int
    mtime_ns: int
    sha: str | None = None


@dataclass(frozen=True)
class ChangeSet:
    """变更检测结果（各类列表互斥，路径为相对 POSIX 风格字符串）。

    - added / modified / deleted：确认的文件变更；
    - suspected_modified：mtime 变化但经哈希复核确认内容未变的文件
      （touch 类操作，无需重传，仅供报告）；
    - added_dirs / deleted_dirs：目录增删（目录无内容概念，不比哈希）；
    - hashed_files：实际执行过哈希复核的文件（用于验证"仅疑似项触发复核"）。
    """

    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    suspected_modified: list[str] = field(default_factory=list)
    added_dirs: list[str] = field(default_factory=list)
    deleted_dirs: list[str] = field(default_factory=list)
    hashed_files: list[str] = field(default_factory=list)


def to_long_path(path: Path | str) -> str:
    """Windows 下返回 ``\\\\?\\`` 前缀路径以支持 >260 字符；其他平台原样返回。"""
    s = str(path)
    if sys.platform == "win32" and not s.startswith("\\\\?\\"):
        return "\\\\?\\" + s
    return s


class Excluder:
    """glob 排除规则（文件与目录模式区分）。

    - 尾斜杠模式（如 ``node_modules/``）：只匹配目录，命中即整树剪枝；
    - 其他模式（如 ``*.tmp``）：只匹配文件；
    - 含 ``/`` 的模式匹配相对路径，否则匹配任意层级的名称。
    """

    def __init__(self, patterns: Iterable[str]) -> None:
        self._dir_patterns = [p[:-1] for p in patterns if p.endswith("/")]
        self._file_patterns = [p for p in patterns if not p.endswith("/")]

    def _matches(self, rel: str, patterns: list[str]) -> bool:
        name = PurePosixPath(rel).name
        for pat in patterns:
            if "/" in pat:
                if fnmatch(rel, pat):
                    return True
            elif fnmatch(name, pat):
                return True
        return False

    def excludes_file(self, rel: str) -> bool:
        return self._matches(rel, self._file_patterns)

    def excludes_dir(self, rel: str) -> bool:
        return self._matches(rel, self._dir_patterns)


def scan_source(source: str | Path, exclude: Iterable[str]) -> ScanResult:
    """遍历源目录，返回全部文件与目录的元数据（不读取文件内容）。

    被排除的目录整树剪枝；stat 失败的条目记入 skipped 而不中断扫描（TR-4）。
    """
    source = Path(source)
    excluder = Excluder(exclude)
    entries: dict[str, ScannedEntry] = {}
    skipped: list[tuple[str, str]] = []

    # 栈元素：(绝对路径, 相对 POSIX 路径或 None)
    stack: list[tuple[Path, str | None]] = [(source, None)]
    while stack:
        dir_abs, dir_rel = stack.pop()
        try:
            it = os.scandir(to_long_path(dir_abs))
        except OSError as e:
            skipped.append((dir_rel or ".", f"无法读取目录: {e.strerror or e}"))
            continue
        with it:
            for dent in it:
                rel = dent.name if dir_rel is None else f"{dir_rel}/{dent.name}"
                try:
                    if dent.is_dir(follow_symlinks=False):
                        if excluder.excludes_dir(rel):
                            continue
                        st = dent.stat(follow_symlinks=False)
                        entries[rel] = ScannedEntry(rel, 0, st.st_mtime_ns, True)
                        stack.append((Path(dent.path), rel))
                    elif dent.is_file(follow_symlinks=False):
                        if excluder.excludes_file(rel):
                            continue
                        st = dent.stat(follow_symlinks=False)
                        entries[rel] = ScannedEntry(rel, st.st_size, st.st_mtime_ns, False)
                    # 符号链接等其他类型：v1 跳过并记录
                    else:
                        skipped.append((rel, "不支持的条目类型（链接/特殊文件），已跳过"))
                except OSError as e:
                    skipped.append((rel, f"无法读取元数据: {e.strerror or e}"))
    return ScanResult(entries=entries, skipped=tuple(skipped))


def detect_changes(
    source: str | Path,
    current: Mapping[str, ScannedEntry],
    previous: Mapping[str, PreviousEntry] | None,
    previous_dirs: Iterable[str] = (),
    algorithm: str | None = _DEFAULT_ALGORITHM,
) -> ChangeSet:
    """对比当前扫描与上一快照清单，产出变更集。

    判定规则（ADR-006）：
    - 不在上一清单 → added；
    - 大小不同 → modified（无需哈希）；
    - 大小相同且 mtime 相同 → 未变（信任元数据）；
    - 大小相同但 mtime 不同 → 哈希复核：不同 → modified；相同 → suspected_modified；
      上一清单无哈希记录时保守判 modified。
    - previous 为 None（首次扫描）→ 全部 added。
    """
    source = Path(source)
    previous = previous or {}
    prev_dirs = set(previous_dirs)
    added: list[str] = []
    modified: list[str] = []
    suspected: list[str] = []
    added_dirs: list[str] = []
    hashed: list[str] = []

    for rel in sorted(current):
        entry = current[rel]
        if entry.is_dir:
            if rel not in prev_dirs:
                added_dirs.append(rel)
            continue

        prev = previous.get(rel)
        if prev is None:
            added.append(rel)
        elif entry.size != prev.size:
            modified.append(rel)
        elif entry.mtime_ns == prev.mtime_ns:
            pass  # 元数据一致，信任未变
        else:
            # mtime 变化但大小不变：BLAKE3 流式哈希复核
            actual = hash_file(to_long_path(source / Path(rel)), algorithm)
            hashed.append(rel)
            if prev.sha is None or actual != prev.sha:
                modified.append(rel)
            else:
                suspected.append(rel)

    current_files = {rel for rel, e in current.items() if not e.is_dir}
    current_dirs = {rel for rel, e in current.items() if e.is_dir}
    return ChangeSet(
        added=added,
        modified=modified,
        deleted=sorted(rel for rel in previous if rel not in current_files),
        suspected_modified=suspected,
        added_dirs=added_dirs,
        deleted_dirs=sorted(rel for rel in prev_dirs if rel not in current_dirs),
        hashed_files=hashed,
    )
