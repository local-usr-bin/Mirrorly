"""Manifest 管理（T-04，ADR-010）。

每个快照对应 ``manifests/<snapshot_id>.json`` 一个独立清单：
- 与快照数据目录分离（快照目录保持纯数据）；
- 原子提交：先写 ``manifests.tmp/`` 临时文件 → flush + fsync → 关闭 →
  ``os.replace`` 原子改名；写入失败不产生"半个 complete manifest"；
- 状态流转：新建为 ``incomplete``，快照物化完成后由调用方显式
  ``mark_complete`` 并重新原子提交；``load_manifest(require_complete=True)``
  拒绝把 incomplete 误读为完整备份。

哈希值由调用方提供（写入即校验属 T-06；变更检测复核属 T-02），
本模块只负责结构、序列化与落盘，不计算哈希。
中断续传（T-05）将消费 incomplete manifest，不在本任务范围。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from .lifecycle import MAX_LIFECYCLE_SEQUENCE, LifecycleStateError, parse_snapshot_sequence
from .repo import RepoInfo
from .scan import ScannedEntry

LEGACY_FORMAT_VERSION = 1
FORMAT_VERSION = 2
SUPPORTED_FORMAT_VERSIONS = (LEGACY_FORMAT_VERSION, FORMAT_VERSION)
STATUS_INCOMPLETE = "incomplete"
STATUS_COMPLETE = "complete"

# Windows 单 path component 上限（NTFS/exFAT 均为 255 个 UTF-16 code unit）
MAX_COMPONENT_UTF16 = 255

# Windows 保留设备名（大小写不敏感；含上位数字形式，含带扩展名形式）
_RESERVED_DEVICE_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
        "COM¹",
        "COM²",
        "COM³",
        "LPT¹",
        "LPT²",
        "LPT³",
    }
)

# Windows 文件名禁止字符（另加所有 < 0x20 的控制字符）
_FORBIDDEN_CHARS = frozenset('<>:"|?*')


class ManifestError(Exception):
    """清单读写/校验相关错误。"""


class ManifestPathError(ManifestError):
    """Manifest 条目路径不符合 canonical snapshot-relative 规则。"""


class ManifestOrderingError(ManifestError):
    """清单生命周期顺序不明确或违反唯一性约束。"""


@dataclass(frozen=True)
class ManifestEntry:
    """单个条目：文件含 size/mtime_ns/sha；目录仅路径（is_dir=True）。"""

    path: str  # 相对源根的 POSIX 风格路径
    size: int
    mtime_ns: int
    sha: str | None
    is_dir: bool


@dataclass(frozen=True)
class ManifestStats:
    files: int = 0
    dirs: int = 0
    total_bytes: int = 0


@dataclass(frozen=True)
class Manifest:
    """一个快照的完整清单（不可变对象；状态流转用 mark_complete 产生新实例）。"""

    snapshot_id: str
    created_at: str
    source_root: str
    hash_algorithm: str
    status: str
    stats: ManifestStats
    lifecycle_seq: int | None
    resumed_from_snapshot_id: str | None = None
    entries: tuple[ManifestEntry, ...] = field(default_factory=tuple)
    format_version: int = FORMAT_VERSION


@dataclass(frozen=True)
class ManifestSummary:
    """list 用的轻量摘要（从完整解析并校验过条目路径的 manifest 提取）。"""

    snapshot_id: str
    status: str
    created_at: str
    stats: ManifestStats
    lifecycle_seq: int | None
    resumed_from_snapshot_id: str | None = None
    format_version: int = FORMAT_VERSION


def _utf16_units(value: str) -> int:
    """按 Windows 底层长度语义计 UTF-16 code unit 数（非 BMP 字符算 2）。"""
    return len(value.encode("utf-16-le")) // 2


def validate_canonical_entry_path(path: str, *, what: str = "manifest 条目路径") -> PurePosixPath:
    """校验 canonical snapshot-relative POSIX 路径，不访问或改写文件系统。

    规则源自 Restore 已冻结的 manifest-entry lexical contract。输入不会被
    normalize：反斜杠、空段、``.``/``..``、绝对/盘符路径、Windows 特殊
    名称或不可 canonical 表示的 component 均直接 fail closed。
    """
    if not isinstance(path, str) or not path:
        raise ManifestPathError(f"{what}不能为空")
    if "\\" in path:
        raise ManifestPathError(f"{what}不允许反斜杠（须为 POSIX 风格相对路径）: {path!r}")
    if path.startswith("/"):
        raise ManifestPathError(f"{what}必须是相对路径: {path!r}")
    if len(path) > 1 and path[1] == ":":
        raise ManifestPathError(f"{what}不允许盘符绝对路径: {path!r}")
    parts = path.split("/")
    for part in parts:
        if not part:
            raise ManifestPathError(f"{what}含空路径段: {path!r}")
        if part in (".", ".."):
            raise ManifestPathError(f"{what}不允许 . / .. 路径段: {path!r}")
        if part != part.rstrip(" ."):
            raise ManifestPathError(f"{what}的路径段不允许尾随点或空格: {path!r}")
        if any(ord(char) < 0x20 or char in _FORBIDDEN_CHARS for char in part):
            raise ManifestPathError(f"{what}含 Windows 禁止字符: {path!r}")
        stem = part.split(".", 1)[0].upper()
        if stem in _RESERVED_DEVICE_NAMES:
            raise ManifestPathError(f"{what}含保留设备名: {path!r}")
        try:
            units = _utf16_units(part)
        except UnicodeEncodeError as exc:
            raise ManifestPathError(f"{what}含无法编码为 UTF-16 的字符: {path!r}") from exc
        if units > MAX_COMPONENT_UTF16:
            raise ManifestPathError(
                f"{what}的路径段超过 {MAX_COMPONENT_UTF16} 个 UTF-16 code unit: {path!r}"
            )
    return PurePosixPath(path)


def validate_manifest_entry_paths(entries: Sequence[ManifestEntry]) -> None:
    """全量验证 manifest 路径，并拒绝精确重复和 Windows 大小写冲突。"""
    seen_exact: set[str] = set()
    seen_folded: set[str] = set()
    for entry in entries:
        validate_canonical_entry_path(entry.path)
        if entry.path in seen_exact:
            raise ManifestPathError(
                f"manifest 含重复条目路径（拒绝重复执行/字典折叠）: {entry.path!r}"
            )
        folded = entry.path.casefold()
        if folded in seen_folded:
            raise ManifestPathError(
                f"manifest 含 Windows 大小写冲突条目路径（fail closed，拒绝执行）: {entry.path!r}"
            )
        seen_exact.add(entry.path)
        seen_folded.add(folded)


def create_manifest(
    snapshot_id: str,
    source_root: str,
    hash_algorithm: str,
    current: Mapping[str, ScannedEntry],
    hashes: Mapping[str, str] | None = None,
    *,
    lifecycle_seq: int,
    resumed_from_snapshot_id: str | None = None,
) -> Manifest:
    """从扫描结果构建 incomplete 状态的清单。

    hashes：{相对路径: 摘要}，由调用方（T-02 复核 / T-06 写入校验）提供；
    未提供的文件 sha 记 None。
    """
    if not snapshot_id:
        raise ManifestError("snapshot_id 不能为空")
    value = _validate_lifecycle_seq(lifecycle_seq)
    _validate_snapshot_sequence_match(snapshot_id, value)
    hashes = hashes or {}
    entries = tuple(
        ManifestEntry(
            path=e.path,
            size=e.size,
            mtime_ns=e.mtime_ns,
            sha=None if e.is_dir else hashes.get(e.path),
            is_dir=e.is_dir,
        )
        for e in (current[k] for k in sorted(current))
    )
    files = [e for e in entries if not e.is_dir]
    return Manifest(
        snapshot_id=snapshot_id,
        created_at=datetime.now(UTC).isoformat(),
        source_root=source_root,
        hash_algorithm=hash_algorithm,
        status=STATUS_INCOMPLETE,
        stats=ManifestStats(
            files=len(files),
            dirs=len(entries) - len(files),
            total_bytes=sum(e.size for e in files),
        ),
        lifecycle_seq=lifecycle_seq,
        resumed_from_snapshot_id=resumed_from_snapshot_id,
        entries=entries,
    )


def mark_complete(manifest: Manifest) -> Manifest:
    """状态流转 incomplete → complete（返回新实例）。"""
    return replace(manifest, status=STATUS_COMPLETE)


def write_manifest(repo: RepoInfo, manifest: Manifest) -> Path:
    """原子提交清单到 manifests/<id>.json；失败抛 ManifestError 且无残留。

    流程：写 manifests.tmp/<id>.json.tmp → flush + fsync → 关闭 → os.replace。
    """
    _validate_manifest(manifest)
    final = repo.path / "manifests" / f"{manifest.snapshot_id}.json"
    tmp = repo.path / "manifests.tmp" / f"{manifest.snapshot_id}.json.tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(_to_json(manifest))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, final)
    except OSError as e:
        try:
            if tmp.exists():
                tmp.unlink()
        finally:
            raise ManifestError(f"清单写入失败: {manifest.snapshot_id}（{e}）") from e
    return final


def load_manifest(repo: RepoInfo, snapshot_id: str, *, require_complete: bool = False) -> Manifest:
    """加载清单。require_complete=True 时拒绝 incomplete（防误当完整备份）。"""
    path = repo.path / "manifests" / f"{snapshot_id}.json"
    if not path.exists():
        raise ManifestError(f"清单不存在: {snapshot_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format_version") not in SUPPORTED_FORMAT_VERSIONS:
        raise ManifestError(
            f"清单格式版本不兼容: {data.get('format_version')}（支持 {SUPPORTED_FORMAT_VERSIONS}）"
        )
    if require_complete and data["status"] != STATUS_COMPLETE:
        raise ManifestError(f"清单状态为 {data['status']}，不是完整备份: {snapshot_id}")
    return _from_dict(data)


def list_manifests(repo: RepoInfo) -> list[ManifestSummary]:
    """列出全部清单摘要（按 snapshot_id 排序；incomplete 如实标注）。"""
    out = []
    manifests_dir = repo.path / "manifests"
    for f in sorted(manifests_dir.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        manifest = _from_dict(data)
        out.append(
            ManifestSummary(
                snapshot_id=manifest.snapshot_id,
                status=manifest.status,
                created_at=manifest.created_at,
                stats=manifest.stats,
                lifecycle_seq=manifest.lifecycle_seq,
                resumed_from_snapshot_id=manifest.resumed_from_snapshot_id,
                format_version=manifest.format_version,
            )
        )
    _validate_unique_lifecycle_sequences(out)
    return out


def latest_sequenced_complete(
    summaries: Sequence[ManifestSummary],
) -> ManifestSummary | None:
    """返回 sequence 最大的 v2 complete；legacy 不参与生命周期竞争。"""
    _validate_unique_lifecycle_sequences(summaries)
    complete = [
        summary
        for summary in summaries
        if summary.status == STATUS_COMPLETE and summary.lifecycle_seq is not None
    ]
    if not complete:
        return None
    return max(complete, key=lambda summary: summary.lifecycle_seq)


def select_default_complete(
    summaries: Sequence[ManifestSummary],
) -> ManifestSummary | None:
    """选择默认 complete，无法确定 legacy latest 时 fail closed。

    一旦存在 sequenced complete，所有 legacy complete 都视为更早的历史；
    legacy-only 仓库仅在恰有一个 complete 时可无歧义地默认选择。
    """
    latest = latest_sequenced_complete(summaries)
    if latest is not None:
        return latest
    legacy = [
        summary
        for summary in summaries
        if summary.status == STATUS_COMPLETE and summary.lifecycle_seq is None
    ]
    if len(legacy) <= 1:
        return legacy[0] if legacy else None
    raise ManifestOrderingError(
        "legacy 仓库包含多个 complete 快照，无法可靠判断 latest；"
        "请显式指定 --snapshot，或使用 verify --all"
    )


def newest_eligible_incomplete(
    summaries: Sequence[ManifestSummary],
) -> ManifestSummary | None:
    """返回 latest complete 之后 sequence 最大的 v2 incomplete。

    legacy incomplete 不自动选择；sequence 不大于 latest complete 的 v2
    incomplete 是 stale/superseded residue，也不参与自动续传。
    """
    latest_complete = latest_sequenced_complete(summaries)
    complete_seq = -1 if latest_complete is None else latest_complete.lifecycle_seq
    eligible = [
        summary
        for summary in summaries
        if summary.status == STATUS_INCOMPLETE
        and summary.lifecycle_seq is not None
        and summary.lifecycle_seq > complete_seq
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda summary: summary.lifecycle_seq)


def _validate_unique_lifecycle_sequences(summaries: Sequence[ManifestSummary]) -> None:
    owners: dict[int, str] = {}
    for summary in summaries:
        sequence = summary.lifecycle_seq
        if sequence is None:
            continue
        previous = owners.get(sequence)
        if previous is not None:
            raise ManifestOrderingError(
                f"manifest lifecycle_seq 重复: {sequence} "
                f"同时属于 {previous!r} 与 {summary.snapshot_id!r}"
            )
        owners[sequence] = summary.snapshot_id


def _to_json(manifest: Manifest) -> str:
    """稳定序列化：同一对象多次序列化逐字节一致（可 diff、可校验）。"""
    return json.dumps(_to_dict(manifest), ensure_ascii=False, indent=2) + "\n"


def _to_dict(manifest: Manifest) -> dict:
    data = {
        "format_version": manifest.format_version,
        "snapshot_id": manifest.snapshot_id,
        "created_at": manifest.created_at,
        "source_root": manifest.source_root,
        "hash_algorithm": manifest.hash_algorithm,
        "status": manifest.status,
        "stats": {
            "files": manifest.stats.files,
            "dirs": manifest.stats.dirs,
            "total_bytes": manifest.stats.total_bytes,
        },
        "entries": [
            {
                "path": e.path,
                **({} if e.is_dir else {"size": e.size, "mtime_ns": e.mtime_ns, "sha": e.sha}),
                "type": "dir" if e.is_dir else "file",
            }
            for e in manifest.entries
        ],
    }
    if manifest.format_version == FORMAT_VERSION:
        data["lifecycle_seq"] = manifest.lifecycle_seq
        if manifest.resumed_from_snapshot_id is not None:
            data["resumed_from_snapshot_id"] = manifest.resumed_from_snapshot_id
    return data


def _from_dict(data: dict) -> Manifest:
    version = data.get("format_version")
    if version not in SUPPORTED_FORMAT_VERSIONS:
        raise ManifestError(f"清单格式版本不兼容: {version!r}（支持 {SUPPORTED_FORMAT_VERSIONS}）")
    lifecycle_seq = _lifecycle_seq_from_data(data)
    stats = data.get("stats", {})
    entries = tuple(
        ManifestEntry(
            path=e["path"],
            size=e.get("size", 0),
            mtime_ns=e.get("mtime_ns", 0),
            sha=e.get("sha"),
            is_dir=e.get("type") == "dir",
        )
        for e in data["entries"]
    )
    manifest = Manifest(
        snapshot_id=data["snapshot_id"],
        created_at=data["created_at"],
        source_root=data["source_root"],
        hash_algorithm=data["hash_algorithm"],
        status=data["status"],
        stats=ManifestStats(
            files=stats.get("files", 0),
            dirs=stats.get("dirs", 0),
            total_bytes=stats.get("total_bytes", 0),
        ),
        lifecycle_seq=lifecycle_seq,
        resumed_from_snapshot_id=data.get("resumed_from_snapshot_id"),
        entries=entries,
        format_version=version,
    )
    validate_manifest_entry_paths(manifest.entries)
    return manifest


def _validate_lifecycle_seq(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestError("manifest lifecycle_seq 必须是整数")
    if not 0 <= value <= MAX_LIFECYCLE_SEQUENCE:
        raise ManifestError(f"manifest lifecycle_seq 超出 uint64: {value!r}")
    return value


def _validate_snapshot_sequence_match(snapshot_id: str, lifecycle_seq: int) -> None:
    try:
        encoded = parse_snapshot_sequence(snapshot_id)
    except LifecycleStateError as exc:
        raise ManifestError(str(exc)) from exc
    if encoded is None:
        raise ManifestError(f"manifest v2 snapshot id 未携带 lifecycle sequence: {snapshot_id!r}")
    if encoded != lifecycle_seq:
        raise ManifestError(
            f"snapshot id sequence {encoded} 与 manifest lifecycle_seq {lifecycle_seq} 不一致"
        )


def _lifecycle_seq_from_data(data: dict) -> int | None:
    if data.get("format_version") == LEGACY_FORMAT_VERSION:
        return None
    value = _validate_lifecycle_seq(data.get("lifecycle_seq"))
    _validate_snapshot_sequence_match(data.get("snapshot_id", ""), value)
    return value


def _validate_manifest(manifest: Manifest) -> None:
    if manifest.format_version != FORMAT_VERSION:
        raise ManifestError(
            f"production writer 只允许写入 manifest v2，拒绝 format: {manifest.format_version}"
        )
    value = _validate_lifecycle_seq(manifest.lifecycle_seq)
    _validate_snapshot_sequence_match(manifest.snapshot_id, value)
    validate_manifest_entry_paths(manifest.entries)
