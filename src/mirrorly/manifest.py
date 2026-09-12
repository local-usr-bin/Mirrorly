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
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from .repo import RepoInfo
from .scan import ScannedEntry

FORMAT_VERSION = 1
STATUS_INCOMPLETE = "incomplete"
STATUS_COMPLETE = "complete"


class ManifestError(Exception):
    """清单读写/校验相关错误。"""


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
    entries: tuple[ManifestEntry, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ManifestSummary:
    """list 用的轻量摘要（不加载全量条目）。"""

    snapshot_id: str
    status: str
    created_at: str
    stats: ManifestStats


def create_manifest(
    snapshot_id: str,
    source_root: str,
    hash_algorithm: str,
    current: Mapping[str, ScannedEntry],
    hashes: Mapping[str, str] | None = None,
) -> Manifest:
    """从扫描结果构建 incomplete 状态的清单。

    hashes：{相对路径: 摘要}，由调用方（T-02 复核 / T-06 写入校验）提供；
    未提供的文件 sha 记 None。
    """
    if not snapshot_id:
        raise ManifestError("snapshot_id 不能为空")
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
        entries=entries,
    )


def mark_complete(manifest: Manifest) -> Manifest:
    """状态流转 incomplete → complete（返回新实例）。"""
    return replace(manifest, status=STATUS_COMPLETE)


def write_manifest(repo: RepoInfo, manifest: Manifest) -> Path:
    """原子提交清单到 manifests/<id>.json；失败抛 ManifestError 且无残留。

    流程：写 manifests.tmp/<id>.json.tmp → flush + fsync → 关闭 → os.replace。
    """
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
    if data.get("format_version") != FORMAT_VERSION:
        raise ManifestError(
            f"清单格式版本不兼容: {data.get('format_version')}（支持 {FORMAT_VERSION}）"
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
        stats = data.get("stats", {})
        out.append(
            ManifestSummary(
                snapshot_id=data["snapshot_id"],
                status=data["status"],
                created_at=data["created_at"],
                stats=ManifestStats(
                    files=stats.get("files", 0),
                    dirs=stats.get("dirs", 0),
                    total_bytes=stats.get("total_bytes", 0),
                ),
            )
        )
    return out


def _to_json(manifest: Manifest) -> str:
    """稳定序列化：同一对象多次序列化逐字节一致（可 diff、可校验）。"""
    return json.dumps(_to_dict(manifest), ensure_ascii=False, indent=2) + "\n"


def _to_dict(manifest: Manifest) -> dict:
    return {
        "format_version": FORMAT_VERSION,
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


def _from_dict(data: dict) -> Manifest:
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
    return Manifest(
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
        entries=entries,
    )
