"""Read-only repository/volume identity resolution; no frontend notifications."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .. import volume as _volume
from ..config import TaskConfig, validate_anchor
from ..repo import REPO_DIR_NAME, REPO_INFO_FILE, RepoError, RepoInfo, get_volume_info, load_repo


class IdentityMismatch(Exception):
    """卷标识不匹配（盘符漂移/插错盘，M10）→ 退出码 5。"""


@dataclass(frozen=True)
class RepositoryResolution:
    """Resolved repository and whether registered-volume search relocated it."""

    repo: RepoInfo
    relocated: bool = False


def _guid_matches(current: str | None, expected: str) -> bool:
    """Volume GUID path 比对（canonical 形式，大小写不敏感）。"""
    return current is not None and current.casefold() == expected.casefold()


def path_within(child: Path, parent: Path) -> bool:
    """child 是否与 parent 相同或位于其内部（realpath + normcase，防简单前缀误判）。

    用 os.path.commonpath 判断包含关系：字符串 startswith 在 Windows 卷根
    上会出错（realpath("C:\\") 以反斜杠结尾，再拼 os.sep 后前缀失配，
    卷根 source/target 的 containment 检查被绕过）。commonpath 在
    不同 drive（或混合类型）时抛 ValueError，按「不包含」处理。
    """
    c = os.path.normcase(os.path.realpath(child))
    p = os.path.normcase(os.path.realpath(parent))
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:
        return False


def _search_anchored(cfg: TaskConfig) -> RepoInfo:
    """M10 Phase 1：按已登记 Volume GUID 搜索当前挂载点并确认仓库。

    fail-closed 状态机（冻结裁定）：
    - GUID 无当前挂载点 → IdentityMismatch（exit 5，"目标备份卷未连接或卷锚已失效"）
    - GUID 定位成功但 repo_id 不匹配 → exit 5
    - GUID 定位成功但卷序列号不匹配 → exit 5
    - 卷在但预期仓库路径缺失 → RepoError（exit 1，不自动 init）
    - 多个不同仓库候选 → exit 5（同一卷的多个挂载点指向同一 repo，samefile 去重）

    绝不用 serial + repo_id 自动认领 GUID 解析不到的卷（无身份降级 fallback）。
    """
    assert cfg.volume_guid is not None and cfg.repo_id is not None and cfg.repo_dir is not None
    try:
        mount_roots = _volume.get_mount_roots(cfg.volume_guid)
    except _volume.VolumeError as e:
        raise IdentityMismatch(f"目标备份卷未连接或卷锚已失效（无法解析 volume GUID）: {e}") from e
    if not mount_roots:
        raise IdentityMismatch(
            f"目标备份卷未连接或卷锚已失效（volume GUID 无当前挂载点）: {cfg.volume_guid}"
        )
    candidates: list[tuple[Path, RepoInfo]] = []
    for m in mount_roots:
        root = Path(m)
        # join 后 containment 复验：不信任 config load 时的单次校验（防运行时逃逸）
        target_dir = root if cfg.repo_dir == "." else root / cfg.repo_dir
        if not path_within(target_dir, root):
            raise IdentityMismatch(f"repo_dir 逃逸出卷挂载根（拒绝）: {cfg.repo_dir!r} @ {m}")
        info_file = target_dir / REPO_DIR_NAME / REPO_INFO_FILE
        if not info_file.exists():
            continue
        repo = load_repo(target_dir)
        if repo.repo_id != cfg.repo_id:
            raise IdentityMismatch(
                f"卷定位成功但预期仓库 id 不匹配（配置 {cfg.repo_id}，"
                f"实际 {repo.repo_id}），拒绝继续"
            )
        current = get_volume_info(target_dir)
        if current.serial != repo.volume.serial:
            raise IdentityMismatch(
                f"目标卷标识不匹配：仓库记录序列号 {repo.volume.serial}，"
                f"当前卷序列号 {current.serial}，拒绝继续"
            )
        candidates.append((target_dir, repo))
    # 同一卷的多个挂载点指向同一物理仓库：按文件身份去重（samefile，非字符串比较）
    unique: list[tuple[Path, RepoInfo]] = []
    for td, repo in candidates:
        try:
            dup = any(os.path.samefile(td / REPO_DIR_NAME, u / REPO_DIR_NAME) for u, _ in unique)
        except OSError:
            dup = False
        if not dup:
            unique.append((td, repo))
    if len(unique) == 1:
        repo = unique[0][1]
        return repo
    if not unique:
        raise RepoError(
            "注册目标卷存在，但预期仓库路径缺失（"
            + "、".join(mount_roots)
            + f" 下未找到 {cfg.repo_dir}\\MirrorlyRepo；不会自动初始化新仓库）"
        )
    raise IdentityMismatch(
        "卷定位结果不唯一（多个不同仓库候选，拒绝猜测）: " + ", ".join(str(td) for td, _ in unique)
    )


def resolve_repo(cfg: TaskConfig) -> RepositoryResolution:
    """加载仓库并解析目标位置（M10：卷锚 + 盘符漂移自动重定位）。

    任何解析失败均发生在任务锁/源扫描/一切仓库写入之前（零写入）：
    - anchored 配置（volume_guid/repo_id/repo_dir 齐全）：
      路径有效且 guid+repo_id+serial 全匹配 → 正常使用；
      路径失联或被其他卷占用 → 按 GUID 搜索同一卷并重定位；
    - legacy 配置（无卷锚）：保持既有行为（path + serial 校验，不自动猜卷）；
    - 卷在但仓库缺失 → RepoError（exit 1）；身份域失败 → exit 5。
    """
    # 防直接构造 TaskConfig 绕过 load 边界（写入边界校验之外的第三重防线）
    validate_anchor(cfg.volume_guid, cfg.repo_id, cfg.repo_dir)
    anchored = cfg.volume_guid is not None
    path = Path(cfg.target_path)
    info_file = path / REPO_DIR_NAME / REPO_INFO_FILE

    if info_file.exists():
        repo = load_repo(path)
        if anchored:
            assert cfg.volume_guid is not None and cfg.repo_id is not None
            try:
                current_guid = _volume.get_volume_guid_for_path(path)
            except _volume.VolumeError:
                current_guid = None
            if not _guid_matches(current_guid, cfg.volume_guid):
                # 配置路径当前不在已登记卷上（盘符漂移/旧盘符被其他卷占用）→ 搜索
                return RepositoryResolution(_search_anchored(cfg), relocated=True)
            if repo.repo_id != cfg.repo_id:
                raise IdentityMismatch(
                    f"预期仓库 id 不匹配（配置记录 {cfg.repo_id}，实际 {repo.repo_id}），拒绝继续"
                )
            current = get_volume_info(path)
            if current.serial != repo.volume.serial:
                raise IdentityMismatch(
                    f"目标卷标识不匹配：仓库记录序列号 {repo.volume.serial}，"
                    f"当前卷序列号 {current.serial}，拒绝继续"
                )
            return RepositoryResolution(repo)
        # legacy：既有行为（MVP_TASKS 之前语义，不自动猜卷）
        current = get_volume_info(path)
        if current.serial != repo.volume.serial:
            raise IdentityMismatch(
                f"目标卷标识不匹配：仓库记录序列号 {repo.volume.serial}，"
                f"当前卷序列号 {current.serial}（盘符漂移或插错盘），拒绝继续"
            )
        return RepositoryResolution(repo)

    if anchored:
        return RepositoryResolution(_search_anchored(cfg), relocated=True)
    raise RepoError(
        f"未找到仓库（repo.json 不存在）: {info_file}\n"
        "该任务配置为 legacy 格式（无卷锚），无法自动重定位；"
        "请重新运行 mirrorly init 登记目标卷，或手工修正 target.path"
    )
