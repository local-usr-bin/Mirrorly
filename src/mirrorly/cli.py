"""Mirrorly 命令行接口（T-09，CLI_SPEC v1.0 / MVP_TASKS T-09）。

职责边界：本模块只做参数解析、确认流程、退出码映射与报告落盘，
把 T-01~T-08 的 library API 编排成五个命令；不重新实现任何业务逻辑，
不绕过底层模块的安全边界（Restore plan/apply、Retention 执行前复核、
incomplete 不当 complete 等语义全部在底层模块内强制执行）。

退出码（CLI_SPEC 第 6 节，唯一事实源）：

- 0   成功，无跳过项
- 1   一般错误（未捕获异常、IO 错误、配置/仓库/manifest 错误等）
- 2   用法错误（argparse 约定；含 --in-place 未配 --yes 的非法组合）
- 3   部分完成（备份/恢复有文件被跳过、冲突或单文件错误——报告列明细）
- 4   校验失败（verify 发现哈希不匹配/文件缺失）
- 5   目标身份不符（卷标识不匹配 / 仓库格式版本不兼容）
- 6   用户中止（交互确认拒绝、非交互环境未给 --yes、任务锁被占用）
- 130 Ctrl+C（Unix 约定）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import __version__
from . import volume as _volume
from .config import (
    ConfigError,
    TaskConfig,
    load_task_config,
    validate_anchor,
    validate_task_name,
    write_task_config,
)
from .manifest import (
    STATUS_COMPLETE,
    ManifestError,
    ManifestSummary,
    create_manifest,
    list_manifests,
    load_manifest,
    mark_complete,
    write_manifest,
)
from .recovery import RecoveryError, build_resume_baseline, clean_tmp_residue, scan_recovery
from .repo import (
    HARDLINK_FILESYSTEMS,
    REPO_DIR_NAME,
    REPO_INFO_FILE,
    RepoError,
    RepoFormatError,
    RepoInfo,
    get_volume_info,
    init_repo,
    load_repo,
)
from .restore import RestoreError, apply_restore, plan_restore
from .retention import RetentionError, apply_retention_plan, build_retention_plan
from .scan import PreviousEntry, detect_changes, scan_source
from .snapshot import SnapshotError, generate_snapshot_id, write_snapshot
from .verify import verify_snapshot

# ---------------------------------------------------------------------------
# 退出码（CLI_SPEC 第 6 节）
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_PARTIAL = 3
EXIT_VERIFY_FAILED = 4
EXIT_IDENTITY = 5
EXIT_ABORTED = 6
EXIT_INTERRUPTED = 130

DEFAULT_CONFIG_ROOT = "./.mirrorly"
DEFAULT_TASK_NAME = "default"


class _UserAbort(Exception):
    """用户中止（交互确认拒绝 / 非交互环境缺少 --yes）→ 退出码 6。"""


class _LockBusy(Exception):
    """任务锁被占用（另一实例运行中）→ 退出码 6。"""


class _IdentityMismatch(Exception):
    """卷标识不匹配（盘符漂移/插错盘，M10）→ 退出码 5。"""


class _UsageError(Exception):
    """语义级用法错误（argparse 覆盖不到的参数组合/缺失）→ 退出码 2。"""


# ---------------------------------------------------------------------------
# 输出与交互辅助
# ---------------------------------------------------------------------------


def _flag(args: argparse.Namespace, name: str) -> bool:
    """读取全局布尔旗标（parents 解析器用 SUPPRESS 默认值，缺省为 False）。"""
    return bool(getattr(args, name, False))


def _info(args: argparse.Namespace, message: str) -> None:
    """常规信息。--json 模式下转到 stderr（stdout 只保留单一 JSON 文档）。"""
    if _flag(args, "quiet"):
        return
    if _flag(args, "json"):
        print(message, file=sys.stderr)
    else:
        print(message)


def _detail(args: argparse.Namespace, message: str) -> None:
    """--verbose 细节信息（--json 模式下同样只走 stderr）。"""
    if _flag(args, "verbose") and not _flag(args, "quiet"):
        if _flag(args, "json"):
            print(message, file=sys.stderr)
        else:
            print(message)


def _err(message: str) -> None:
    print(f"错误：{message}", file=sys.stderr)


def _confirm(args: argparse.Namespace, prompt: str) -> None:
    """破坏性操作确认：--yes 跳过；拒绝/非交互 → _UserAbort（退出码 6）。

    --json 模式下绝不向 stdout 写交互提示：未显式 --yes 直接视为未授权。
    """
    if _flag(args, "yes"):
        return
    if _flag(args, "json"):
        raise _UserAbort(f"{prompt}——--json 模式不进行交互，请显式提供 --yes")
    try:
        answer = input(f"{prompt} [y/N] ")
    except EOFError as e:
        raise _UserAbort("非交互环境无法确认，请显式提供 --yes") from e
    if answer.strip().lower() not in ("y", "yes"):
        raise _UserAbort("用户在确认提示中拒绝")


def _ask(args: argparse.Namespace, prompt: str) -> bool:
    """是非提问（--yes 视为肯定；非交互/--json 无 --yes 中止——不做静默假设）。"""
    if _flag(args, "yes"):
        return True
    if _flag(args, "json"):
        raise _UserAbort(f"{prompt}——--json 模式不进行交互，请显式提供 --yes")
    try:
        answer = input(f"{prompt} [Y/n] ")
    except EOFError as e:
        raise _UserAbort("非交互环境无法提问，请显式提供 --yes") from e
    return answer.strip().lower() not in ("n", "no")


def _human_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{n} B"
        value /= 1024
    return f"{n} B"


# ---------------------------------------------------------------------------
# 仓库 / 配置解析
# ---------------------------------------------------------------------------


def _resolve_task_config(args: argparse.Namespace) -> TaskConfig:
    """解析任务配置：--task 指定；省略时要求 config.d/ 下恰有一个任务。"""
    config_root = Path(getattr(args, "config", None) or DEFAULT_CONFIG_ROOT)
    config_d = config_root / "config.d"
    task = getattr(args, "task", None)
    if task:
        # --task 直接拼进文件路径，非法值属用法错误（防路径逃逸）
        try:
            validate_task_name(task)
        except ConfigError as e:
            raise _UsageError(f"--task 非法：{e}") from e
        return load_task_config(config_d / f"{task}.toml")
    if not config_d.is_dir():
        raise ConfigError(f"未找到任务配置目录: {config_d}（请先运行 mirrorly init）")
    candidates = sorted(config_d.glob("*.toml"))
    if not candidates:
        raise ConfigError(f"未找到任何任务配置: {config_d}（请先运行 mirrorly init）")
    if len(candidates) > 1:
        names = ", ".join(p.stem for p in candidates)
        raise _UsageError(f"存在多个任务（{names}），必须用 --task 指定")
    return load_task_config(candidates[0])


def _guid_matches(current: str | None, expected: str) -> bool:
    """Volume GUID path 比对（canonical 形式，大小写不敏感）。"""
    return current is not None and current.casefold() == expected.casefold()


def _notify_relocation(cfg: TaskConfig, repo: RepoInfo) -> None:
    """重定位提示：始终走 stderr（--json 下 stdout 仍保持单一 JSON 文档）。"""
    print(
        f"目标卷已重定位: {cfg.target_path} → {repo.path.parent}（卷标识匹配；配置未自动修改）",
        file=sys.stderr,
    )


def _search_anchored(cfg: TaskConfig) -> RepoInfo:
    """M10 Phase 1：按已登记 Volume GUID 搜索当前挂载点并确认仓库。

    fail-closed 状态机（冻结裁定）：
    - GUID 无当前挂载点 → _IdentityMismatch（exit 5，"目标备份卷未连接或卷锚已失效"）
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
        raise _IdentityMismatch(f"目标备份卷未连接或卷锚已失效（无法解析 volume GUID）: {e}") from e
    if not mount_roots:
        raise _IdentityMismatch(
            f"目标备份卷未连接或卷锚已失效（volume GUID 无当前挂载点）: {cfg.volume_guid}"
        )
    candidates: list[tuple[Path, RepoInfo]] = []
    for m in mount_roots:
        root = Path(m)
        # join 后 containment 复验：不信任 config load 时的单次校验（防运行时逃逸）
        target_dir = root if cfg.repo_dir == "." else root / cfg.repo_dir
        if not _path_within(target_dir, root):
            raise _IdentityMismatch(f"repo_dir 逃逸出卷挂载根（拒绝）: {cfg.repo_dir!r} @ {m}")
        info_file = target_dir / REPO_DIR_NAME / REPO_INFO_FILE
        if not info_file.exists():
            continue
        repo = load_repo(target_dir)
        if repo.repo_id != cfg.repo_id:
            raise _IdentityMismatch(
                f"卷定位成功但预期仓库 id 不匹配（配置 {cfg.repo_id}，"
                f"实际 {repo.repo_id}），拒绝继续"
            )
        current = get_volume_info(target_dir)
        if current.serial != repo.volume.serial:
            raise _IdentityMismatch(
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
        _notify_relocation(cfg, repo)
        return repo
    if not unique:
        raise RepoError(
            "注册目标卷存在，但预期仓库路径缺失（"
            + "、".join(mount_roots)
            + f" 下未找到 {cfg.repo_dir}\\MirrorlyRepo；不会自动初始化新仓库）"
        )
    raise _IdentityMismatch(
        "卷定位结果不唯一（多个不同仓库候选，拒绝猜测）: " + ", ".join(str(td) for td, _ in unique)
    )


def _resolve_repo(cfg: TaskConfig) -> RepoInfo:
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
                return _search_anchored(cfg)
            if repo.repo_id != cfg.repo_id:
                raise _IdentityMismatch(
                    f"预期仓库 id 不匹配（配置记录 {cfg.repo_id}，实际 {repo.repo_id}），拒绝继续"
                )
            current = get_volume_info(path)
            if current.serial != repo.volume.serial:
                raise _IdentityMismatch(
                    f"目标卷标识不匹配：仓库记录序列号 {repo.volume.serial}，"
                    f"当前卷序列号 {current.serial}，拒绝继续"
                )
            return repo
        # legacy：既有行为（MVP_TASKS 之前语义，不自动猜卷）
        current = get_volume_info(path)
        if current.serial != repo.volume.serial:
            raise _IdentityMismatch(
                f"目标卷标识不匹配：仓库记录序列号 {repo.volume.serial}，"
                f"当前卷序列号 {current.serial}（盘符漂移或插错盘），拒绝继续"
            )
        return repo

    if anchored:
        return _search_anchored(cfg)
    raise RepoError(
        f"未找到仓库（repo.json 不存在）: {info_file}\n"
        "该任务配置为 legacy 格式（无卷锚），无法自动重定位；"
        "请重新运行 mirrorly init 登记目标卷，或手工修正 target.path"
    )


def _latest_complete(repo: RepoInfo) -> ManifestSummary | None:
    complete = [s for s in list_manifests(repo) if s.status == STATUS_COMPLETE]
    if not complete:
        return None
    return max(complete, key=lambda s: (s.created_at, s.snapshot_id))


class _TaskLock:
    """任务锁（locks/<task>.lock，O_EXCL 独占创建）；占用即 _LockBusy。

    MVP 语义：锁文件存在即视为另一实例运行中，不做 stale 自动清理
    （崩溃残留由用户确认后手工删除，避免误判活人锁）。
    """

    def __init__(self, repo: RepoInfo, task_name: str) -> None:
        self._path = repo.path / "locks" / f"{task_name}.lock"

    def __enter__(self) -> _TaskLock:
        try:
            fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as e:
            raise _LockBusy(
                f"任务锁被占用: {self._path}（另一实例可能正在运行；"
                "确认无实例运行后可手工删除该锁文件）"
            ) from e
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"pid={os.getpid()} acquired_at={datetime.now().isoformat()}\n")
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            self._path.unlink()
        except OSError:
            pass


def _snapshot_id_free(repo: RepoInfo, snapshot_id: str) -> bool:
    """快照 id 未被占用（数据目录与 manifest 均不存在）。"""
    return (
        not (repo.path / "snapshots" / snapshot_id).exists()
        and not (repo.path / "manifests" / f"{snapshot_id}.json").exists()
    )


def _new_snapshot_id(repo: RepoInfo) -> str:
    """分配唯一快照 id：秒级时间型 id 冲突时追加 ``-01``/``-02`` canonical 后缀。

    保证任何 id collision 下既有快照目录/manifest 字节/complete 状态不被
    触碰——先选定空闲 id，再落盘 incomplete manifest；后缀形式
    ``2026-09-13_133000-01`` 满足 Restore 的单组件 canonical 校验。
    在任务锁内调用，同任务并发已由锁排除。
    """
    base = generate_snapshot_id()
    for n in range(100):
        candidate = base if n == 0 else f"{base}-{n:02d}"
        if _snapshot_id_free(repo, candidate):
            return candidate
    raise SnapshotError(f"无法分配唯一快照 id：基准 {base} 的 100 个候选均已被占用")


def _write_report(repo: RepoInfo, name: str, data: dict) -> Path:
    """报告落盘到仓库 logs/（M9）：临时文件 + 原子改名（不产生半个 JSON）。

    文件名带微秒时间戳；仍撞名（同微秒）时追加 ``-01`` 等后缀，
    绝不静默覆盖已有报告。
    """
    logs_dir = repo.path / "logs"
    logs_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
    final = logs_dir / f"{name}-{ts}.json"
    for n in range(1, 100):
        if not final.exists():
            break
        final = logs_dir / f"{name}-{ts}-{n:02d}.json"
    else:
        raise RepoError(f"无法分配唯一报告文件名: {name}-{ts}")
    tmp = final.with_suffix(final.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, final)
    return final


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def _path_within(child: Path, parent: Path) -> bool:
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


def cmd_init(args: argparse.Namespace) -> int:
    # ---- 预检（全部在 init_repo 之前，任何失败保证零仓库写入）----
    task_name = getattr(args, "task", None) or DEFAULT_TASK_NAME
    try:
        validate_task_name(task_name)
    except ConfigError as e:
        _err(f"--task 非法：{e}")
        return EXIT_USAGE

    source = Path(args.source)
    if not source.is_dir():
        _err(f"--source 不是已存在的目录: {source}")
        return EXIT_USAGE
    target = Path(args.target)
    policy = args.filesystem_policy

    config_root = Path(getattr(args, "config", None) or DEFAULT_CONFIG_ROOT)
    config_file = config_root / "config.d" / f"{task_name}.toml"
    if config_file.exists():
        _err(f"任务配置已存在: {config_file}（如需重建请先手工删除）")
        return EXIT_ERROR

    # source 与 prospective repo 不得互相包含（备份源含仓库会自我吞没/递归）
    prospective_repo = target / REPO_DIR_NAME
    if _path_within(prospective_repo, source):
        _err(f"备份目标仓库 {prospective_repo} 位于源目录 {source} 内，拒绝初始化")
        return EXIT_ERROR
    if _path_within(source, prospective_repo):
        _err(f"源目录 {source} 位于备份目标仓库 {prospective_repo} 内，拒绝初始化")
        return EXIT_ERROR

    # ---- 预检全部通过后才允许产生写入 ----
    # warn 策略的确认由 CLI 层完成（区分「用户取消 → 6」与一般错误 → 1）
    volume = get_volume_info(target)
    if volume.filesystem not in HARDLINK_FILESYSTEMS and policy == "warn":
        _info(
            args,
            f"目标文件系统为 {volume.filesystem}，不支持硬链接：\n"
            "  - 将无法跨快照共享未变更文件的存储（空间占用显著增加）；\n"
            "  - 备份将以整文件复制模式运行。\n"
            "建议将目标盘转换为 NTFS 后重新 init。",
        )
        _confirm(args, "是否仍以整文件复制模式继续？")

    # M10 卷锚前置解析（失败零仓库写入；init_repo 内部会再取 Volume GUID）
    try:
        mount_root = _volume.get_volume_mount_root(str(target.resolve()))
    except _volume.VolumeError as e:
        _err(f"无法解析目标卷挂载点（M10 卷锚所需）: {e}")
        return EXIT_ERROR

    # 确认已完成（或 strict 由底层直接拒绝），assume_yes 防止底层二次提问
    info = init_repo(target, filesystem_policy=policy, assume_yes=True)

    # M10 卷锚三字段：Volume GUID + repo_id + 卷内相对路径（重定位依据）
    assert info.volume.guid is not None
    rel = os.path.relpath(str(target.resolve()), mount_root)
    repo_dir = "." if rel == "." else rel.replace("/", "\\")
    cfg = TaskConfig(
        name=task_name,
        source=str(source.resolve()),
        target_path=str(target.resolve()),
        filesystem_policy=policy,
        volume_guid=info.volume.guid,
        repo_id=info.repo_id,
        repo_dir=repo_dir,
    )
    written = write_task_config(cfg, config_root)

    summary = {
        "repo": str(info.path),
        "repo_id": info.repo_id,
        "filesystem": info.volume.filesystem,
        "volume_serial": info.volume.serial,
        "hardlinks": info.hardlinks,
        "hash_algorithm": info.hash_algorithm,
        "filesystem_policy": policy,
        "task_config": str(written),
    }
    if _flag(args, "json"):
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _info(args, f"仓库已初始化: {info.path}")
        _info(
            args,
            f"  文件系统: {info.volume.filesystem}"
            f"（{'硬链接模式' if info.hardlinks else '整文件复制模式（降级，已明示）'}）"
            f"，哈希算法: {info.hash_algorithm}",
        )
        _info(args, f"  任务配置: {written}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# backup
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Baseline:
    """备份基线：上一 complete 快照或 incomplete 续传基线的统一视图。"""

    previous: dict[str, PreviousEntry]
    previous_dirs: tuple[str, ...]
    previous_snapshot_dir: Path | None
    carried_shas: dict[str, str]
    resumed_from: str | None
    untrusted: tuple[str, ...] = ()
    uncertified: tuple[str, ...] = ()


def _select_baseline(args: argparse.Namespace, cfg: TaskConfig, repo: RepoInfo) -> _Baseline:
    """选择变更检测基线：发现 incomplete 时提示续传（TR-5），否则用最近 complete。"""
    recovery = scan_recovery(repo)
    if recovery.incomplete:
        latest_inc = max(recovery.incomplete, key=lambda s: (s.created_at, s.snapshot_id))
        if args.dry_run:
            _info(args, f"检测到中断的备份 {latest_inc.snapshot_id}（正式执行时将提示续传）")
        elif _ask(
            args,
            f"检测到中断的备份 {latest_inc.snapshot_id}"
            f"（{latest_inc.created_at}），是否以其为基线续传？"
            "（选 n 将从头开始新备份，incomplete 保留不动）",
        ):
            # B1-2：verify_on_write=True 时对已物化条目做内容等价认证，
            # 获得可信哈希覆盖或拒绝信任旧副本；False 时保持既有语义
            baseline = build_resume_baseline(
                repo,
                latest_inc.snapshot_id,
                source=cfg.source,
                verify_content=cfg.verify_on_write,
            )
            _info(
                args,
                f"将以 {latest_inc.snapshot_id} 为基线续传"
                f"（已物化 {len(baseline.previous)} 个文件，待补 {len(baseline.missing)} 个）",
            )
            if baseline.untrusted:
                _info(
                    args,
                    f"  {len(baseline.untrusted)} 个已物化文件内容与当前源不一致"
                    "（中断后副本可能被损坏），不信任旧副本，将重新复制",
                )
            if baseline.uncertified:
                _info(
                    args,
                    f"  {len(baseline.uncertified)} 个已物化文件未能完成内容认证"
                    "（源暂不可读或元数据与清单不一致），将重新复制",
                )
            return _Baseline(
                previous=dict(baseline.previous),
                previous_dirs=tuple(sorted(baseline.previous_dirs)),
                previous_snapshot_dir=baseline.snapshot_path,
                carried_shas={p: e.sha for p, e in baseline.previous.items() if e.sha},
                resumed_from=latest_inc.snapshot_id,
                untrusted=baseline.untrusted,
                uncertified=baseline.uncertified,
            )
        else:
            _info(args, "不续传，从头开始新备份（incomplete 快照保留不动）")

    latest = _latest_complete(repo)
    if latest is None:
        return _Baseline({}, (), None, {}, None)
    manifest = load_manifest(repo, latest.snapshot_id, require_complete=True)
    previous = {
        e.path: PreviousEntry(size=e.size, mtime_ns=e.mtime_ns, sha=e.sha)
        for e in manifest.entries
        if not e.is_dir
    }
    return _Baseline(
        previous=previous,
        previous_dirs=tuple(e.path for e in manifest.entries if e.is_dir),
        previous_snapshot_dir=repo.path / "snapshots" / latest.snapshot_id,
        carried_shas={e.path: e.sha for e in manifest.entries if not e.is_dir and e.sha},
        resumed_from=None,
    )


def _scan_and_detect(args: argparse.Namespace, cfg: TaskConfig, baseline: _Baseline):
    """扫描源并做变更检测（dry-run 与真实执行共用同一逻辑）。"""
    excludes = tuple(cfg.exclude) + tuple(args.exclude or ())
    scan = scan_source(cfg.source, excludes)
    current = scan.entries

    # --full-hash：跳过元数据初筛——把基线 mtime 置为不可能匹配的值，
    # 强制 detect_changes 对所有共存文件做哈希复核（复用 ADR-006 冻结逻辑）
    prev_for_detect = baseline.previous
    force_recopy = set(baseline.uncertified)
    if cfg.verify_on_write:
        # 当前已启用写入校验时，任何缺少可信哈希的基线文件都不得走
        # unchanged 硬链接复用。尤其是 False→True：旧 complete snapshot
        # 的 sha=None 副本可能已损坏，不能只重算旧副本哈希后为其背书。
        # 将其送入既有 copy/write-verify 路径，以当前源重新物化并获得哈希。
        force_recopy.update(p for p, e in baseline.previous.items() if e.sha is None)
    if force_recopy:
        # mtime 置为不可能值（与 --full-hash 同一冻结机制）强制哈希复核：
        # prev.sha=None → 保守判 modified → 正常 write-verify 重拷获得可信
        # 哈希；源已删除的条目则维持 deleted 分类（keys 不变）。
        prev_for_detect = {
            k: PreviousEntry(size=v.size, mtime_ns=-1, sha=v.sha) if k in force_recopy else v
            for k, v in prev_for_detect.items()
        }
    if args.full_hash:
        prev_for_detect = {
            k: PreviousEntry(size=v.size, mtime_ns=-1, sha=v.sha)
            for k, v in prev_for_detect.items()
        }
    changes = detect_changes(cfg.source, current, prev_for_detect, baseline.previous_dirs)
    return scan, current, changes


def cmd_backup(args: argparse.Namespace) -> int:
    cfg = _resolve_task_config(args)
    repo = _resolve_repo(cfg)

    if args.dry_run:
        # dry-run 有意例外：零写入，因此不取任务锁（只读预览不与其他实例互斥）
        baseline = _select_baseline(args, cfg, repo)
        scan, _current, changes = _scan_and_detect(args, cfg, baseline)
        return _finish_dry_run(args, changes, scan.skipped)

    # 真实执行：确定 repo/task 后立即取锁，锁覆盖 recovery/incomplete 基线选择、
    # 源扫描、变更检测、快照/manifest 写入、续传善后与 retention——不能先扫描
    # 数分钟、甚至先读到另一个活动任务的 incomplete 后才发现锁被占用
    with _TaskLock(repo, cfg.name):
        started = time.monotonic()
        baseline = _select_baseline(args, cfg, repo)
        scan, current, changes = _scan_and_detect(args, cfg, baseline)

        clean_tmp_residue(repo)
        # 先分配唯一空闲 id，再落盘 incomplete manifest——任何 id collision 下
        # 既有快照目录 / manifest 字节 / complete 状态都不被触碰
        snapshot_id = _new_snapshot_id(repo)
        source_root = str(Path(cfg.source).resolve())

        # manifest 状态机：incomplete 落盘 → 物化 → complete 原子提交
        write_manifest(
            repo, create_manifest(snapshot_id, source_root, repo.hash_algorithm, current)
        )
        result = write_snapshot(
            cfg.source,
            repo,
            current,
            changes,
            snapshot_id=snapshot_id,
            previous_snapshot=baseline.previous_snapshot_dir,
            verify_writes=cfg.verify_on_write,
        )

        skipped_paths = {p for p, _ in result.skipped}
        final_current = {k: v for k, v in current.items() if k not in skipped_paths}
        # linked 文件 sha 从基线 manifest 结转（不重算），copied 用写入校验哈希
        merged_hashes = {
            rel: baseline.carried_shas[rel] for rel in result.linked if rel in baseline.carried_shas
        } | result.hashes
        final_manifest = create_manifest(
            snapshot_id, source_root, repo.hash_algorithm, final_current, hashes=merged_hashes
        )
        # 全局 defense-in-depth：当前 verify_on_write=True 的 complete 快照
        # 不允许存在无可信哈希的普通文件条目。skipped/deleted 文件已不在
        # final_current，目录不需要内容哈希；其余文件必须来自可信结转，或
        # 正常 copy/write-verify。若未来回归产生 coverage gap，在此 fail
        # closed（新快照保持 incomplete），绝不现场重哈希副本来生成信任。
        if cfg.verify_on_write:
            unhashed_final = sorted(
                entry.path for entry in final_manifest.entries if not entry.is_dir and not entry.sha
            )
            if unhashed_final:
                raise SnapshotError(
                    f"快照 {snapshot_id} 存在 {len(unhashed_final)} 个未获得"
                    f"可信哈希的文件（首个: {unhashed_final[0]!r}），"
                    "拒绝发布 complete（fail closed）"
                )
        write_manifest(repo, mark_complete(final_manifest))

        # 续传善后：显式删除旧 incomplete（complete 快照被底层拒绝，双保险）
        if baseline.resumed_from:
            from .recovery import discard_incomplete

            discard_incomplete(repo, baseline.resumed_from)

        # 保留策略（dry-run 已在上方返回；此处为真实执行，复核语义在底层）
        plan = build_retention_plan(repo, keep_last=cfg.keep_last, keep_monthly=cfg.keep_monthly)
        retention_deleted = apply_retention_plan(repo, plan)

        duration = time.monotonic() - started
        all_skipped = list(scan.skipped) + list(result.skipped)
        report = {
            "command": "backup",
            "snapshot_id": snapshot_id,
            "status": "complete",
            "source": source_root,
            "duration_seconds": round(duration, 3),
            "full_hash": bool(args.full_hash),
            "resumed_from": baseline.resumed_from,
            "resume_untrusted": list(baseline.untrusted),
            "resume_uncertified": list(baseline.uncertified),
            "changes": {
                "added": changes.added,
                "modified": changes.modified,
                "deleted": changes.deleted,
                "suspected_modified": changes.suspected_modified,
            },
            "linked": list(result.linked),
            "copied": list(result.copied),
            "skipped": [{"path": p, "reason": r} for p, r in all_skipped],
            "bytes_written": result.bytes_written,
            "retention_deleted": list(retention_deleted),
        }
        report_path = _write_report(repo, f"backup-{snapshot_id}", report)

    if _flag(args, "json"):
        report["report_path"] = str(report_path)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _info(args, f"备份完成: {snapshot_id}（{duration:.1f}s）")
        _info(
            args,
            f"  新增 {len(changes.added)} / 修改 {len(changes.modified)}"
            f" / 删除 {len(changes.deleted)} / 疑似修改 {len(changes.suspected_modified)}"
            f"；硬链接复用 {len(result.linked)}，复制 {len(result.copied)}"
            f"（{_human_bytes(result.bytes_written)}）",
        )
        if baseline.resumed_from:
            _info(args, f"  已基于中断备份 {baseline.resumed_from} 续传并清理旧 incomplete")
        if retention_deleted:
            _info(args, f"  保留策略清理: {', '.join(retention_deleted)}")
        if all_skipped:
            _info(args, f"  跳过 {len(all_skipped)} 项（部分完成，明细见报告）:")
            for p, reason in all_skipped[:20]:
                _info(args, f"    - {p}: {reason}")
        _info(args, f"  报告: {report_path}")
    return EXIT_PARTIAL if all_skipped else EXIT_OK


def _finish_dry_run(
    args: argparse.Namespace, changes, scan_skipped: tuple[tuple[str, str], ...]
) -> int:
    """dry-run 变更预览（M8）：只输出，不写入任何内容。

    --json 模式输出单一结构化 JSON 文档（机器可读）；否则人类可读预览。
    """
    if _flag(args, "json"):
        payload = {
            "command": "backup",
            "dry_run": True,
            "changes": {
                "added": list(changes.added),
                "modified": list(changes.modified),
                "deleted": list(changes.deleted),
                "suspected_modified": list(changes.suspected_modified),
                "added_dirs": list(changes.added_dirs),
                "deleted_dirs": list(changes.deleted_dirs),
            },
            "skipped": [{"path": p, "reason": r} for p, r in scan_skipped],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_PARTIAL if scan_skipped else EXIT_OK

    _info(args, "变更预览（dry-run，不写入任何内容）:")
    _info(
        args,
        f"  新增 {len(changes.added)} / 修改 {len(changes.modified)}"
        f" / 删除 {len(changes.deleted)} / 疑似修改 {len(changes.suspected_modified)}"
        f" / 新增目录 {len(changes.added_dirs)} / 删除目录 {len(changes.deleted_dirs)}",
    )
    for title, items in (
        ("新增", changes.added),
        ("修改", changes.modified),
        ("删除", changes.deleted),
        ("疑似修改", changes.suspected_modified),
    ):
        for rel in items[:50]:
            _detail(args, f"    [{title}] {rel}")
    if scan_skipped:
        _info(args, f"  扫描跳过 {len(scan_skipped)} 项:")
        for p, reason in scan_skipped[:20]:
            _info(args, f"    - {p}: {reason}")
    return EXIT_PARTIAL if scan_skipped else EXIT_OK


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


def cmd_verify(args: argparse.Namespace) -> int:
    cfg = _resolve_task_config(args)
    repo = _resolve_repo(cfg)

    if args.all:
        targets = [s.snapshot_id for s in list_manifests(repo) if s.status == STATUS_COMPLETE]
        if not targets:
            _err("仓库中没有 complete 快照可校验")
            return EXIT_ERROR
    elif args.snapshot:
        targets = [args.snapshot]
    else:
        latest = _latest_complete(repo)
        if latest is None:
            _err("仓库中没有 complete 快照可校验")
            return EXIT_ERROR
        targets = [latest.snapshot_id]

    reports = [verify_snapshot(repo, sid, quick=args.quick) for sid in targets]

    failed = False
    report_entries = []
    for rep in reports:
        failed = failed or not rep.ok
        report_entries.append(
            {
                "snapshot_id": rep.snapshot_id,
                "quick": rep.quick,
                "ok": rep.ok,
                "checked_files": rep.checked_files,
                "checked_dirs": rep.checked_dirs,
                "hashed_files": rep.hashed_files,
                "unhashed_entries": rep.unhashed_entries,
                "issues": [
                    {"path": i.path, "kind": i.kind, "detail": i.detail} for i in rep.issues
                ],
                "extras": list(rep.extras),
            }
        )
        # 逐项人类可读结果（--json 模式下 _info 自动走 stderr，stdout 保持纯净）
        mode = "quick" if rep.quick else "full"
        if rep.ok:
            _info(
                args,
                f"{rep.snapshot_id}: 完整（{mode}，校验文件 {rep.checked_files}"
                f"，哈希比对 {rep.hashed_files}）",
            )
        else:
            _info(args, f"{rep.snapshot_id}: 校验失败（{len(rep.issues)} 项问题）:")
            for issue in rep.issues[:20]:
                _info(args, f"    - [{issue.kind}] {issue.path} {issue.detail}")
        if rep.unhashed_entries:
            _detail(args, f"    （{rep.unhashed_entries} 个条目无哈希记录，已跳过哈希比对）")
        if rep.extras:
            _detail(args, f"    （{len(rep.extras)} 个清单外文件，仅报告不影响结论）")

    report = {
        "command": "verify",
        "quick": bool(args.quick),
        "snapshots": report_entries,
        "ok": not failed,
    }
    name = f"verify-{targets[0]}" if len(targets) == 1 else "verify-all"
    report_path = _write_report(repo, name, report)
    if _flag(args, "json"):
        report["report_path"] = str(report_path)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _info(args, f"报告: {report_path}")
    return EXIT_VERIFY_FAILED if failed else EXIT_OK


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


def cmd_restore(args: argparse.Namespace) -> int:
    cfg = _resolve_task_config(args)
    repo = _resolve_repo(cfg)

    # CLI_SPEC §4：--in-place 为危险操作，必须显式 --yes（非法组合 → 用法错误）
    if args.in_place and not _flag(args, "yes"):
        _err("--in-place 是危险操作，必须显式提供 --yes")
        return EXIT_USAGE

    snapshot_id = args.snapshot
    if snapshot_id is None:
        latest = _latest_complete(repo)
        if latest is None:
            _err("仓库中没有 complete 快照可恢复")
            return EXIT_ERROR
        snapshot_id = latest.snapshot_id

    plan = plan_restore(
        repo,
        snapshot_id,
        args.to,
        paths=tuple(args.path or ()),
        overwrite=args.overwrite,
        in_place=args.in_place,
    )

    counts = {"create": 0, "overwrite": 0, "skip": 0, "conflict": 0}
    dirs = 0
    for e in plan.entries:
        if e.is_dir:
            dirs += 1
        else:
            counts[e.action] += 1

    # 计划展示（_info/_detail 在 --json 模式下自动走 stderr，stdout 保持纯净）
    _info(args, f"恢复计划: 快照 {snapshot_id} → {plan.destination}")
    _info(
        args,
        f"  新建 {counts['create']} / 覆盖 {counts['overwrite']}"
        f" / 跳过 {counts['skip']} / 冲突 {counts['conflict']}（目录 {dirs} 个）",
    )
    for e in plan.entries:
        if e.is_dir:
            continue
        if e.action in ("overwrite", "conflict"):
            _info(args, f"    [{e.action}] {e.rel_path} {e.reason}")
        else:
            _detail(args, f"    [{e.action}] {e.rel_path} {e.reason}")

    # 破坏性确认：存在覆盖项时必须显式确认或 --yes（CLI_SPEC §0）
    if counts["overwrite"]:
        _confirm(
            args,
            f"将覆盖 {counts['overwrite']} 个已存在文件（策略 {args.overwrite}），确认执行？",
        )

    result = apply_restore(repo, plan)

    has_issues = bool(result.skipped or result.conflicts or result.errors or result.leftovers)
    summary = {
        "command": "restore",
        "snapshot_id": result.snapshot_id,
        "destination": str(result.destination),
        "restored": list(result.restored),
        "dirs_created": list(result.dirs_created),
        "skipped": [{"path": p, "reason": r} for p, r in result.skipped],
        "conflicts": [{"path": p, "reason": r} for p, r in result.conflicts],
        "errors": [{"path": p, "reason": r} for p, r in result.errors],
        "leftovers": list(result.leftovers),
        "bytes_written": result.bytes_written,
    }
    if _flag(args, "json"):
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _info(
            args,
            f"恢复完成: 写入 {len(result.restored)} 个文件"
            f"（{_human_bytes(result.bytes_written)}），新建目录 {len(result.dirs_created)} 个",
        )
        for title, items in (
            ("跳过", result.skipped),
            ("冲突", result.conflicts),
            ("错误", result.errors),
        ):
            if items:
                _info(args, f"  {title} {len(items)} 项:")
                for p, reason in items[:20]:
                    _info(args, f"    - {p}: {reason}")
        if result.leftovers:
            _info(args, f"  临时文件清理失败残留 {len(result.leftovers)} 个（需手工删除）:")
            for p in result.leftovers:
                _info(args, f"    - {p}")
    return EXIT_PARTIAL if has_issues else EXIT_OK


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    cfg = _resolve_task_config(args)
    repo = _resolve_repo(cfg)
    summaries = list_manifests(repo)

    if _flag(args, "json"):
        payload = [
            {
                "snapshot_id": s.snapshot_id,
                "status": s.status,
                "created_at": s.created_at,
                "stats": {
                    "files": s.stats.files,
                    "dirs": s.stats.dirs,
                    "total_bytes": s.stats.total_bytes,
                },
            }
            for s in summaries
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_OK

    if not summaries:
        _info(args, "仓库中还没有任何快照")
        return EXIT_OK
    for s in summaries:
        mark = "" if s.status == STATUS_COMPLETE else "（incomplete，非完整备份）"
        _info(args, f"{s.snapshot_id}  {s.created_at}  {s.status}{mark}")
        _detail(
            args,
            f"    文件 {s.stats.files} / 目录 {s.stats.dirs}"
            f" / 共 {_human_bytes(s.stats.total_bytes)}",
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# 参数解析与入口
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    # 全局选项：main 与子命令共用（SUPPRESS 默认值避免子解析器覆盖已解析值）
    global_parser = argparse.ArgumentParser(add_help=False)
    global_parser.add_argument(
        "--config", default=argparse.SUPPRESS, help="配置目录（默认 ./.mirrorly）"
    )
    global_parser.add_argument("--task", default=argparse.SUPPRESS, help="任务名（单任务时可省略）")
    global_parser.add_argument(
        "--quiet", action="store_true", default=argparse.SUPPRESS, help="最少输出"
    )
    global_parser.add_argument(
        "--verbose", action="store_true", default=argparse.SUPPRESS, help="详细输出"
    )
    global_parser.add_argument(
        "--no-color",
        action="store_true",
        default=argparse.SUPPRESS,
        help="禁用着色（当前输出本不着色，预留）",
    )
    global_parser.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="机器可读 JSON 输出"
    )

    parser = argparse.ArgumentParser(
        prog="mirrorly",
        parents=[global_parser],
        description="Mirrorly - 个人备份工具（NTFS 快照 + 硬链接，MVP CLI）",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    sp = sub.add_parser("init", parents=[global_parser], help="初始化备份仓库与任务配置")
    sp.add_argument("--source", required=True, help="备份源目录")
    sp.add_argument("--target", required=True, help="备份目标根目录（其下创建 MirrorlyRepo/）")
    sp.add_argument(
        "--filesystem-policy",
        choices=("strict", "warn"),
        default="strict",
        help="非 NTFS 目标策略（默认 strict 拒绝）",
    )
    sp.add_argument("--yes", action="store_true", default=argparse.SUPPRESS, help="跳过交互确认")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("backup", parents=[global_parser], help="执行一次备份，生成新快照")
    sp.add_argument("--dry-run", action="store_true", help="只输出变更预览，不写入任何内容")
    sp.add_argument("--full-hash", action="store_true", help="跳过元数据初筛，全量哈希比对")
    sp.add_argument("--exclude", action="append", help="追加排除规则（可重复）")
    sp.add_argument("--yes", action="store_true", default=argparse.SUPPRESS, help="跳过确认")
    sp.set_defaults(func=cmd_backup)

    sp = sub.add_parser("verify", parents=[global_parser], help="校验备份完整性")
    verify_target = sp.add_mutually_exclusive_group()
    verify_target.add_argument("--snapshot", help="只校验指定快照（默认最近一个 complete）")
    verify_target.add_argument("--all", action="store_true", help="校验所有 complete 快照")
    sp.add_argument("--quick", action="store_true", help="只校验存在性/类型/大小，不重算哈希")
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("restore", parents=[global_parser], help="从快照恢复文件")
    sp.add_argument("--snapshot", help="指定快照（默认最近一个 complete）")
    sp.add_argument("--to", required=True, help="恢复目标目录")
    sp.add_argument("--in-place", action="store_true", help="恢复回原始源路径（须显式 --yes）")
    sp.add_argument(
        "--path",
        action="append",
        help="只恢复指定的文件/子树（可重复；字面 snapshot-relative 路径，非 glob）",
    )
    sp.add_argument(
        "--overwrite",
        choices=("never", "older", "always"),
        default="never",
        help="目标已存在时的策略（默认 never）",
    )
    sp.add_argument("--yes", action="store_true", default=argparse.SUPPRESS, help="跳过确认")
    sp.set_defaults(func=cmd_restore)

    sp = sub.add_parser("list", parents=[global_parser], help="列出快照")
    sp.set_defaults(func=cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口：异常到退出码的统一映射（CLI_SPEC 第 6 节）。"""
    parser = _build_parser()
    args = parser.parse_args(argv)  # 用法错误由 argparse 以退出码 2 处理
    try:
        return args.func(args)
    except KeyboardInterrupt:
        _err("已中断（Ctrl+C）")
        return EXIT_INTERRUPTED
    except _UserAbort as e:
        _err(f"已中止：{e}")
        return EXIT_ABORTED
    except _UsageError as e:
        _err(str(e))
        return EXIT_USAGE
    except _LockBusy as e:
        _err(str(e))
        return EXIT_ABORTED
    except _IdentityMismatch as e:
        _err(str(e))
        return EXIT_IDENTITY
    except RepoFormatError as e:
        _err(str(e))
        return EXIT_IDENTITY
    except (RepoError, ConfigError, ManifestError, SnapshotError, RecoveryError) as e:
        _err(str(e))
        return EXIT_ERROR
    except (RestoreError, RetentionError, OSError) as e:
        _err(str(e))
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
