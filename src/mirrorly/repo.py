"""备份仓库初始化与加载（T-01，对应 M1 基础与 M10 卷标识）。

仓库布局（ADR-010 / DESIGN_DECISIONS 第 1 节）::

    <target>/MirrorlyRepo/
    ├── repo.json        # 仓库信息：格式版本、卷标识、哈希算法
    ├── snapshots/       # 快照目录树（纯数据）
    ├── manifests/       # 每快照独立 JSON 清单
    ├── manifests.tmp/   # 清单写入临时区（原子改名来源）
    ├── locks/           # 任务锁
    └── logs/            # 备份/校验报告

文件系统策略（PRD v0.2 D2）：NTFS 为 v1 正式支持；exFAT/FAT32 不静默降级——
strict 直接拒绝，warn 明确提示能力限制后由用户决定继续或取消。
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .hashing import default_algorithm

REPO_DIR_NAME = "MirrorlyRepo"
REPO_INFO_FILE = "repo.json"
FORMAT_VERSION = 1
SUBDIRS = ("snapshots", "manifests", "manifests.tmp", "locks", "logs")

#: v1 正式支持硬链接的文件系统（ADR-005）
HARDLINK_FILESYSTEMS = {"NTFS"}
FILESYSTEM_POLICIES = ("strict", "warn")


class RepoError(Exception):
    """仓库初始化/加载相关错误。"""


class RepoFormatError(RepoError):
    """仓库格式版本不兼容（CLI 退出码 5：目标身份不符）。"""


@dataclass(frozen=True)
class VolumeInfo:
    """目标卷标识信息（M10：防盘符漂移写错盘）。"""

    label: str
    serial: str
    filesystem: str


@dataclass(frozen=True)
class RepoInfo:
    """repo.json 的内存表示。"""

    path: Path
    format_version: int
    repo_id: str
    created_at: str
    tool_version: str
    hash_algorithm: str
    volume: VolumeInfo
    filesystem_policy: str
    hardlinks: bool


def get_volume_info(path: Path) -> VolumeInfo:
    """读取路径所在卷的标识信息。Windows 使用 GetVolumeInformationW。"""
    if sys.platform.startswith("win"):
        return _get_volume_info_windows(path)
    # 非 Windows 兜底：v1 仅支持 Windows（ADR-005），此处保证可测试性
    st = os.stat(path)
    return VolumeInfo(label="", serial=f"{st.st_dev:X}", filesystem="unknown")


def _get_volume_info_windows(path: Path) -> VolumeInfo:
    import ctypes
    from ctypes import wintypes

    root = Path(path.resolve().anchor)  # 如 "E:\\"
    vol_name = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
    fs_name = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
    serial = wintypes.DWORD(0)
    max_component = wintypes.DWORD(0)
    flags = wintypes.DWORD(0)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        str(root),
        vol_name,
        wintypes.MAX_PATH,
        ctypes.byref(serial),
        ctypes.byref(max_component),
        ctypes.byref(flags),
        fs_name,
        wintypes.MAX_PATH,
    )
    if not ok:
        raise RepoError(f"无法读取卷信息: {root}")
    return VolumeInfo(label=vol_name.value, serial=f"{serial.value:08X}", filesystem=fs_name.value)


def init_repo(
    target_root: str | Path,
    *,
    filesystem_policy: str = "strict",
    assume_yes: bool = False,
    confirm: Callable[[str], bool] | None = None,
    volume_info_provider: Callable[[Path], VolumeInfo] | None = None,
) -> RepoInfo:
    """在 target_root 下初始化 MirrorlyRepo 仓库。

    - filesystem_policy="strict"：目标非 NTFS 时拒绝并提示转换 NTFS；
    - filesystem_policy="warn"：明确提示能力限制，经用户确认（或 assume_yes）
      后以整文件复制模式继续（hardlinks=False），绝不静默降级。
    """
    if filesystem_policy not in FILESYSTEM_POLICIES:
        allowed = " / ".join(FILESYSTEM_POLICIES)
        raise RepoError(f"非法 filesystem_policy: {filesystem_policy!r}（允许: {allowed}）")

    target_root = Path(target_root)
    repo_dir = target_root / REPO_DIR_NAME
    if (repo_dir / REPO_INFO_FILE).exists():
        raise RepoError(f"目标已存在已初始化的仓库: {repo_dir}（重复 init 被拒绝）")

    get_info = volume_info_provider or get_volume_info
    volume = get_info(target_root)

    hardlinks = volume.filesystem in HARDLINK_FILESYSTEMS
    if not hardlinks:
        limitation = (
            f"目标文件系统为 {volume.filesystem}，不支持硬链接：\n"
            "  - 将无法跨快照共享未变更文件的存储（空间占用显著增加）；\n"
            "  - 备份将以整文件复制模式运行。\n"
            "建议将目标盘转换为 NTFS 后重新 init。"
        )
        if filesystem_policy == "strict":
            raise RepoError(f"strict 策略拒绝非 NTFS 目标。{limitation}")
        # warn 策略：明示能力限制，由用户决定继续或取消
        proceed = assume_yes or (confirm or _default_confirm)(
            limitation + "\n是否仍以整文件复制模式继续？"
        )
        if not proceed:
            raise RepoError("用户取消：非 NTFS 目标未初始化")

    for sub in SUBDIRS:
        (repo_dir / sub).mkdir(parents=True, exist_ok=True)

    info = RepoInfo(
        path=repo_dir,
        format_version=FORMAT_VERSION,
        repo_id=uuid.uuid4().hex,
        created_at=datetime.now(UTC).isoformat(),
        tool_version=__version__,
        hash_algorithm=default_algorithm(),
        volume=volume,
        filesystem_policy=filesystem_policy,
        hardlinks=hardlinks,
    )
    _write_json_atomic(repo_dir / REPO_INFO_FILE, _repo_to_dict(info))
    return info


def load_repo(target_root: str | Path) -> RepoInfo:
    """加载已初始化的仓库信息。"""
    info_file = Path(target_root) / REPO_DIR_NAME / REPO_INFO_FILE
    if not info_file.exists():
        raise RepoError(f"未找到仓库（repo.json 不存在）: {info_file}")
    data = json.loads(info_file.read_text(encoding="utf-8"))
    if data["format_version"] != FORMAT_VERSION:
        raise RepoFormatError(
            f"仓库格式版本不兼容: {data['format_version']}（本工具支持 {FORMAT_VERSION}）"
        )
    vol = data["volume"]
    return RepoInfo(
        path=info_file.parent,
        format_version=data["format_version"],
        repo_id=data["repo_id"],
        created_at=data["created_at"],
        tool_version=data["tool_version"],
        hash_algorithm=data["hash_algorithm"],
        volume=VolumeInfo(label=vol["label"], serial=vol["serial"], filesystem=vol["filesystem"]),
        filesystem_policy=data["filesystem_policy"],
        hardlinks=data["hardlinks"],
    )


def _repo_to_dict(info: RepoInfo) -> dict:
    return {
        "format_version": info.format_version,
        "repo_id": info.repo_id,
        "created_at": info.created_at,
        "tool_version": info.tool_version,
        "hash_algorithm": info.hash_algorithm,
        "volume": {
            "label": info.volume.label,
            "serial": info.volume.serial,
            "filesystem": info.volume.filesystem,
        },
        "filesystem_policy": info.filesystem_policy,
        "hardlinks": info.hardlinks,
    }


def _write_json_atomic(path: Path, data: dict) -> None:
    """先写临时文件再原子改名（TR-5 设计纪律：不产生半个 JSON）。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _default_confirm(message: str) -> bool:
    """默认交互确认（CLI 场景）。"""
    print(message)
    return input("[y/N] ").strip().lower() in ("y", "yes")
