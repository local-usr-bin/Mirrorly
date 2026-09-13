"""恢复引擎（T-08，TR-4 防覆盖 / CLI_SPEC 第 4 节）。

两阶段模型：``plan_restore`` 产出 RestorePlan（**批准的意图**，不是可信事实源），
``apply_restore`` 执行前不信任 plan——重新加载 complete manifest、校验 manifest
指纹（digest）、重新验证安全边界、重跑规划，并按 **no-upgrade 对账**：

- 重算出的每个动作破坏性不得超过已批准 plan（skip < create < overwrite）；
- 任何破坏性升级（如批准 skip 而重算要求 overwrite）一律判 stale，
  整体拒绝、零写入；
- 破坏性持平或降级（如批准 overwrite 而重算只需 skip）按重算结果执行。

安全纪律（不可违反）：

1. **manifest 是边界，不是免校验凭据**：manifest 条目路径逐一过 canonical
   校验（防篡改 manifest 把 ``../`` 写进恢复目标）；selector 为字面
   snapshot-relative 路径，**不做 glob/fnmatch 扩展语义**；
2. **canonical Windows 路径校验**：空段、``.``/``..`` 段、绝对路径、
   控制字符、``<>:"|?*``、尾随点/空格、保留设备名（CON PRN AUX NUL
   COM1-9 LPT1-9 COM¹ COM² COM³ LPT¹ LPT² LPT³，含带扩展名形式）一律非法；
   component 长度按 **UTF-16 code unit** 计（上限 255），不把 Python
   ``len()`` 当作 Windows 底层长度语义（非 BMP 字符占 2 个 code unit）；
3. **reparse 双侧防护**：snapshot 读侧与 destination 写侧遇到 reparse
   point（junction/symlink 等）保守拒绝——这是**有意的兼容性限制**，不做
   跨链接恢复；check-then-open 的残余 TOCTOU 风险在 MVP 中**明确接受**
   （handle-based no-follow 留待 MVP 后评估，OQ-2）；
4. **目标边界**：destination 落在仓库目录内一律拒绝；destination 解析后
   等于 manifest 记录的 source_root（同一实际位置）时必须显式 in_place；
   source_root 的普通子目录允许恢复（无需 in_place）；
5. **防覆盖**：never 跳过 / older 仅当目标 mtime **严格更旧**才覆盖 /
   always 覆盖普通文件；file/dir 类型冲突即使 always 也**不得自动删除
   用户目录**（或文件），记 conflict 跳过；
6. **写入方式**：目标 parent 下 ``tempfile.mkstemp`` 独占创建临时文件
   （短固定前缀，不借用 final filename 作 prefix——合法 final 名可能已
   接近 component 长度上限，追加随机串会溢出）；flush + fsync + close 后
   ``os.replace`` 原子改名；cleanup 只按本次运行明确记录的精确临时路径，
   绝不扫描后缀批量删除；cleanup 失败记入 leftovers；
7. **partial restore**：单文件失败记入 errors 继续，不做整体回滚；
8. 恢复产物为普通文件，**不自动执行 full verify**（Q2 裁定）。

边界：CLI 交互（确认提示、退出码）属 T-09，本层只提供结构化 plan/result。
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .hashing import hash_file
from .manifest import Manifest, load_manifest
from .repo import RepoInfo
from .scan import to_long_path

#: 覆盖策略取值（CLI_SPEC：--overwrite never|older|always，默认 never）
OVERWRITE_POLICIES = ("never", "older", "always")

#: Windows 单 path component 上限（NTFS/exFAT 均为 255 个 UTF-16 code unit）
MAX_COMPONENT_UTF16 = 255

#: 临时文件前后缀：短固定前缀 + mkstemp 随机串，不含 final filename（防溢出）
_TMP_PREFIX = ".mirrorly-restore-"
_TMP_SUFFIX = ".mrtmp"

#: Windows 保留设备名（大小写不敏感；含上位数字形式，含带扩展名形式）
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

#: Windows 文件名禁止字符（另加所有 < 0x20 的控制字符）
_FORBIDDEN_CHARS = frozenset('<>:"|?*')

#: 动作破坏性等级（no-upgrade 对账用）：数值越大破坏性越强
ACTION_SKIP = "skip"
ACTION_CREATE = "create"
ACTION_OVERWRITE = "overwrite"
ACTION_CONFLICT = "conflict"  # 类型冲突，任何策略下都不动作
_ACTION_RANK = {ACTION_SKIP: 0, ACTION_CONFLICT: 0, ACTION_CREATE: 1, ACTION_OVERWRITE: 2}

#: reparse point 文件属性位（Windows FILE_ATTRIBUTE_REPARSE_POINT）
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class RestoreError(Exception):
    """恢复相关错误（安全检查拒绝、stale plan、manifest 非法等）。"""


@dataclass(frozen=True)
class RestoreEntry:
    """计划中单个条目的恢复动作（路径为快照相对 POSIX 风格字符串）。"""

    rel_path: str
    dest_path: Path
    is_dir: bool
    action: str  # skip / create / overwrite / conflict
    reason: str = ""


@dataclass(frozen=True)
class RestorePlan:
    """批准的恢复意图。

    manifest_digest 为计划所依据 manifest 文件的哈希（仓库算法）；apply 阶段
    重算比对，不一致即 stale。apply 不信任本对象的 entries——会以相同参数
    重跑规划并做 no-upgrade 对账。
    """

    snapshot_id: str
    destination: Path
    in_place: bool
    overwrite: str
    paths: tuple[str, ...]  # 字面 selector（空元组 = 整快照）
    entries: tuple[RestoreEntry, ...]
    manifest_digest: str


@dataclass(frozen=True)
class RestoreResult:
    """一次恢复执行的报告（partial restore：errors 非空不代表整体失败）。"""

    snapshot_id: str
    destination: Path
    restored: tuple[str, ...] = ()  # 实际写入（create + overwrite）的文件
    dirs_created: tuple[str, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()  # (相对路径, 原因)
    conflicts: tuple[tuple[str, str], ...] = ()  # 类型冲突，未动作
    errors: tuple[tuple[str, str], ...] = ()  # 执行期单文件失败
    leftovers: tuple[str, ...] = ()  # 清理失败的临时文件绝对路径
    bytes_written: int = 0


# ---------------------------------------------------------------------------
# canonical 路径校验
# ---------------------------------------------------------------------------


def _utf16_units(s: str) -> int:
    """按 Windows 底层长度语义计 UTF-16 code unit 数（非 BMP 字符算 2）。"""
    return len(s.encode("utf-16-le")) // 2


def validate_canonical_rel_path(rel: str, *, what: str = "路径") -> PurePosixPath:
    """校验 snapshot-relative 字面路径的 canonical 合法性，非法抛 RestoreError。

    规则：POSIX 风格相对路径；无空段/``.``/``..`` 段；非绝对路径；无控制
    字符与 Windows 禁止字符；无尾随点/空格；无保留设备名（含带扩展名形式）；
    每个 component 不超过 255 个 UTF-16 code unit。
    """
    if not rel:
        raise RestoreError(f"{what}不能为空")
    if rel.startswith("/"):
        raise RestoreError(f"{what}必须是相对路径: {rel!r}")
    if len(rel) > 1 and rel[1] == ":":
        raise RestoreError(f"{what}不允许盘符绝对路径: {rel!r}")
    parts = rel.split("/")
    for part in parts:
        if not part:
            raise RestoreError(f"{what}含空路径段: {rel!r}")
        if part in (".", ".."):
            raise RestoreError(f"{what}不允许 . / .. 路径段: {rel!r}")
        if part != part.rstrip(" ."):
            raise RestoreError(f"{what}的路径段不允许尾随点或空格: {rel!r}")
        if any(ord(c) < 0x20 or c in _FORBIDDEN_CHARS for c in part):
            raise RestoreError(f"{what}含 Windows 禁止字符: {rel!r}")
        stem = part.split(".", 1)[0].upper()
        if stem in _RESERVED_DEVICE_NAMES:
            raise RestoreError(f"{what}含保留设备名: {rel!r}")
        if _utf16_units(part) > MAX_COMPONENT_UTF16:
            raise RestoreError(
                f"{what}的路径段超过 {MAX_COMPONENT_UTF16} 个 UTF-16 code unit: {rel!r}"
            )
    return PurePosixPath(rel)


def normalize_selector(raw: str) -> str:
    """把用户输入的 selector 归一为 POSIX 风格（``\\`` 视为分隔符）后校验。"""
    rel = raw.replace("\\", "/")
    validate_canonical_rel_path(rel, what="--path")
    return rel


def _validate_snapshot_id(snapshot_id: str) -> None:
    """路径安全：快照 id 必须是单段相对名称（第三份 validator，Q1 裁定暂接受）。"""
    if (
        not snapshot_id
        or "/" in snapshot_id
        or "\\" in snapshot_id
        or snapshot_id in (".", "..")
        or (len(snapshot_id) > 1 and snapshot_id[1] == ":")
    ):
        raise RestoreError(f"非法快照 id: {snapshot_id!r}")


# ---------------------------------------------------------------------------
# reparse 防护（双侧保守拒绝；check-then-open 残余 TOCTOU 为已接受的 MVP 限制）
# ---------------------------------------------------------------------------


def _file_attributes(path_lp: str) -> int | None:
    """读取路径的文件属性位；路径不存在返回 None。

    优先 os.lstat（对符号链接识别最可靠：S_ISLNK / st_file_attributes）；
    lstat 失败时在 Windows 上回退 GetFileAttributesW（扩展前缀失败再退普通
    路径）。两者都失败视为不存在。
    """
    try:
        st = os.lstat(path_lp)
    except OSError:
        st = None
    if st is not None:
        if stat.S_ISLNK(st.st_mode):
            return _FILE_ATTRIBUTE_REPARSE_POINT
        attrs = getattr(st, "st_file_attributes", None)
        return attrs if attrs is not None else 0
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        func = ctypes.windll.kernel32.GetFileAttributesW
        func.restype = wintypes.DWORD  # INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
        attrs = func(path_lp)
        if attrs == 0xFFFFFFFF and path_lp.startswith("\\\\?\\"):
            attrs = func(path_lp[4:])  # 扩展前缀失败时回退普通路径
        if attrs == 0xFFFFFFFF:  # INVALID_FILE_ATTRIBUTES
            return None
        return attrs
    return None


def _is_reparse(path_lp: str) -> bool:
    """路径（已加 long-path 前缀）是否为 reparse point（symlink/junction 等）。"""
    attrs = _file_attributes(path_lp)
    return bool(attrs and attrs & _FILE_ATTRIBUTE_REPARSE_POINT)


def _check_no_reparse_chain(root: Path, target: Path) -> None:
    """检查 target 自身及其在 root 之下的全部祖先都不是 reparse point。

    root 本身必须是已确认安全或被检查的目录；target 不存在时仅检查存在的祖先。
    """
    root = Path(os.path.normpath(str(root)))
    target = Path(os.path.normpath(str(target)))
    try:
        rel = target.relative_to(root)
    except ValueError:
        raise RestoreError(f"内部错误: {target} 不在 {root} 之下") from None
    current = root
    for part in rel.parts:
        current = current / part
        lp = to_long_path(current)
        attrs = _file_attributes(lp)
        if attrs is None:
            break  # 往下的层级尚不存在，由我们创建，安全
        if attrs & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise RestoreError(f"路径含 reparse point（链接/交接点），保守拒绝: {current}")


# ---------------------------------------------------------------------------
# 目标边界
# ---------------------------------------------------------------------------


def _norm(path: Path) -> str:
    """大小写不敏感的规范化绝对路径（比较用；不解析链接——链接一律拒绝）。"""
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def _check_destination(
    repo: RepoInfo, destination: Path, manifest: Manifest, in_place: bool
) -> None:
    """目标边界：仓库内拒绝；等于原 source_root 必须 in_place；普通子目录允许。"""
    dest_norm = _norm(destination)
    repo_norm = _norm(repo.path)
    if dest_norm == repo_norm or dest_norm.startswith(repo_norm + os.sep):
        raise RestoreError(f"恢复目标不能位于仓库目录内: {destination}")
    source_norm = _norm(Path(manifest.source_root))
    if dest_norm == source_norm and not in_place:
        raise RestoreError(
            f"恢复目标即原始源路径 {manifest.source_root}（同一实际位置），必须显式指定 in_place"
        )
    dest_attrs = _file_attributes(to_long_path(destination))
    if dest_attrs is not None and dest_attrs & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise RestoreError(f"恢复目标为 reparse point（链接/交接点），保守拒绝: {destination}")


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


def _manifest_digest(repo: RepoInfo, snapshot_id: str) -> str:
    """manifest 文件内容的哈希（仓库算法），作为 plan 的事实指纹。"""
    return hash_file(repo.path / "manifests" / f"{snapshot_id}.json", repo.hash_algorithm)


def _select_entries(manifest: Manifest, selectors: tuple[str, ...]) -> list:
    """按字面 selector 过滤 manifest 条目：命中文件取该文件，命中目录取整棵子树。"""
    if not selectors:
        return list(manifest.entries)
    selected = []
    for entry in manifest.entries:
        for sel in selectors:
            if entry.path == sel or entry.path.startswith(sel + "/"):
                selected.append(entry)
                break
    matched = {e.path for e in selected}
    for sel in selectors:
        # selector 必须至少命中一个条目（显式报错，不静默空恢复）
        if not any(p == sel or p.startswith(sel + "/") for p in matched):
            raise RestoreError(f"--path 未命中快照中的任何条目: {sel!r}")
    return selected


def _plan_entries(
    manifest: Manifest,
    snap_dir: Path,
    destination: Path,
    selectors: tuple[str, ...],
    overwrite: str,
) -> tuple[RestoreEntry, ...]:
    """核心规划：逐条目判定动作。plan 与 apply 共用（apply 重跑规划）。"""
    entries: list[RestoreEntry] = []
    for m_entry in _select_entries(manifest, selectors):
        rel = m_entry.path
        validate_canonical_rel_path(rel, what="manifest 条目路径")
        dest = destination / Path(rel)
        dest_lp = to_long_path(dest)

        # snapshot 读侧 reparse 防护（条目自身 + 快照内祖先链）：
        # 快照内出现 reparse 属完整性异常，整体拒绝
        _check_no_reparse_chain(snap_dir, snap_dir / Path(rel))
        # destination 写侧 reparse 防护（已存在的祖先链/目标自身）：
        # 用户目录里的链接不拖垮整体，逐条记 conflict
        try:
            _check_no_reparse_chain(destination, dest)
        except RestoreError as e:
            entries.append(RestoreEntry(rel, dest, m_entry.is_dir, ACTION_CONFLICT, str(e)))
            continue

        if m_entry.is_dir:
            if not os.path.lexists(dest_lp):
                entries.append(RestoreEntry(rel, dest, True, ACTION_CREATE))
            elif os.path.isdir(dest_lp):
                entries.append(RestoreEntry(rel, dest, True, ACTION_SKIP, "目录已存在"))
            else:
                entries.append(
                    RestoreEntry(
                        rel, dest, True, ACTION_CONFLICT, "目标已存在同名文件，拒绝删除用户文件"
                    )
                )
            continue

        # 文件条目：父链上存在非目录祖先则无法落位
        blocker = _first_non_dir_ancestor(destination, dest.parent)
        if blocker is not None:
            entries.append(
                RestoreEntry(
                    rel, dest, False, ACTION_CONFLICT, f"目标父路径被同名文件阻挡: {blocker}"
                )
            )
            continue

        if not os.path.lexists(dest_lp):
            entries.append(RestoreEntry(rel, dest, False, ACTION_CREATE))
            continue
        if not os.path.isfile(dest_lp):
            entries.append(
                RestoreEntry(
                    rel, dest, False, ACTION_CONFLICT, "目标已存在同名目录，拒绝删除用户目录"
                )
            )
            continue
        # 目标已存在同名普通文件：按覆盖策略
        if overwrite == "never":
            entries.append(RestoreEntry(rel, dest, False, ACTION_SKIP, "目标已存在（never）"))
        elif overwrite == "always":
            entries.append(RestoreEntry(rel, dest, False, ACTION_OVERWRITE))
        else:  # older：仅当目标 mtime 严格更旧
            dest_mtime = os.stat(dest_lp).st_mtime_ns
            if dest_mtime < m_entry.mtime_ns:
                entries.append(RestoreEntry(rel, dest, False, ACTION_OVERWRITE, "目标更旧"))
            else:
                entries.append(
                    RestoreEntry(rel, dest, False, ACTION_SKIP, "目标不旧于快照（older）")
                )
    # 目录先于文件、浅层先于深层，保证父目录先创建
    entries.sort(key=lambda e: (not e.is_dir, e.rel_path.count("/"), e.rel_path))
    return tuple(entries)


def _first_non_dir_ancestor(root: Path, parent: Path) -> Path | None:
    """root..parent 链上第一个「存在但不是目录」的祖先（无则 None）。"""
    root_norm = Path(os.path.normpath(str(root)))
    current = Path(os.path.normpath(str(parent)))
    blockers: list[Path] = []
    while True:
        lp = to_long_path(current)
        if os.path.lexists(lp) and not os.path.isdir(lp):
            blockers.append(current)
        if current == root_norm:
            break
        current = current.parent
    return blockers[-1] if blockers else None


def plan_restore(
    repo: RepoInfo,
    snapshot_id: str,
    destination: str | Path,
    *,
    paths: tuple[str, ...] = (),
    overwrite: str = "never",
    in_place: bool = False,
) -> RestorePlan:
    """产出恢复计划（批准的意图）。只读，不写任何内容。

    - manifest 必须 complete（ManifestError 透传）；
    - paths 为字面 snapshot-relative 路径（文件命中自身、目录命中整棵子树），
      无 glob 语义，未命中任何条目即报错；
    - 安全边界（canonical 路径、reparse、目标边界）在此即全量校验，
      apply 阶段会再次校验（不信任 plan）。
    """
    if overwrite not in OVERWRITE_POLICIES:
        allowed = " / ".join(OVERWRITE_POLICIES)
        raise RestoreError(f"非法覆盖策略: {overwrite!r}（允许: {allowed}）")
    _validate_snapshot_id(snapshot_id)
    destination = Path(destination)
    selectors = tuple(normalize_selector(p) for p in paths)

    manifest = load_manifest(repo, snapshot_id, require_complete=True)
    snap_dir = repo.path / "snapshots" / snapshot_id
    if not snap_dir.is_dir():
        raise RestoreError(f"快照目录不存在: {snap_dir}")

    _check_destination(repo, destination, manifest, in_place)
    entries = _plan_entries(manifest, snap_dir, destination, selectors, overwrite)
    return RestorePlan(
        snapshot_id=snapshot_id,
        destination=destination,
        in_place=in_place,
        overwrite=overwrite,
        paths=selectors,
        entries=entries,
        manifest_digest=_manifest_digest(repo, snapshot_id),
    )


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def apply_restore(repo: RepoInfo, plan: RestorePlan) -> RestoreResult:
    """执行恢复计划（partial restore，不做整体回滚）。

    不信任 plan（RestorePlan 只是批准的意图）：
    1. 重新 load_manifest(require_complete=True) 并重算 manifest digest，
       与 plan.manifest_digest 不一致 → stale，整体拒绝零写入；
    2. 以 plan 的相同参数重跑规划（安全边界全量重验）；
    3. no-upgrade 对账：重算动作的破坏性逐项不得超过已批准 plan，
       任一升级 → stale，整体拒绝零写入；
    4. 按重算结果执行：目录创建；文件经独占临时文件 + fsync + 原子改名；
       单文件失败记入 errors 继续。
    """
    _validate_snapshot_id(plan.snapshot_id)

    # 1. 事实源复核：manifest 状态 + 指纹
    manifest = load_manifest(repo, plan.snapshot_id, require_complete=True)
    if _manifest_digest(repo, plan.snapshot_id) != plan.manifest_digest:
        raise RestoreError(
            f"stale plan: manifest 在计划后发生变化（digest 不一致），整体拒绝: {plan.snapshot_id}"
        )

    snap_dir = repo.path / "snapshots" / plan.snapshot_id
    if not snap_dir.is_dir():
        raise RestoreError(f"快照目录不存在: {snap_dir}")

    # 2. 重跑规划（含 canonical / reparse / 目标边界全量重验）
    _check_destination(repo, plan.destination, manifest, plan.in_place)
    recomputed = _plan_entries(manifest, snap_dir, plan.destination, plan.paths, plan.overwrite)

    # 3. no-upgrade 对账
    _reconcile_no_upgrade(plan.entries, recomputed)

    # 4. 执行
    restored: list[str] = []
    dirs_created: list[str] = []
    skipped: list[tuple[str, str]] = []
    conflicts: list[tuple[str, str]] = []
    errors: list[tuple[str, str]] = []
    leftovers: list[str] = []
    bytes_written = 0
    manifest_map = {e.path: e for e in manifest.entries}

    for entry in recomputed:
        if entry.action == ACTION_SKIP:
            skipped.append((entry.rel_path, entry.reason))
            continue
        if entry.action == ACTION_CONFLICT:
            conflicts.append((entry.rel_path, entry.reason))
            continue
        try:
            if entry.is_dir:
                Path(to_long_path(entry.dest_path)).mkdir(parents=True, exist_ok=True)
                dirs_created.append(entry.rel_path)
            else:
                src_lp = to_long_path(snap_dir / Path(entry.rel_path))
                if not os.path.isfile(src_lp):
                    raise RestoreError("快照内文件缺失或类型不符（快照可能损坏）")
                # 读侧最终防线：写入前复查源非 reparse
                if _is_reparse(src_lp):
                    raise RestoreError("快照内条目为 reparse point，保守拒绝")
                m_entry = manifest_map[entry.rel_path]
                _restore_one_file(src_lp, entry.dest_path, m_entry.mtime_ns, leftovers)
                restored.append(entry.rel_path)
                bytes_written += m_entry.size
        except (OSError, RestoreError) as e:
            errors.append((entry.rel_path, str(e)))

    return RestoreResult(
        snapshot_id=plan.snapshot_id,
        destination=plan.destination,
        restored=tuple(restored),
        dirs_created=tuple(dirs_created),
        skipped=tuple(skipped),
        conflicts=tuple(conflicts),
        errors=tuple(errors),
        leftovers=tuple(leftovers),
        bytes_written=bytes_written,
    )


def _reconcile_no_upgrade(
    approved: tuple[RestoreEntry, ...], recomputed: tuple[RestoreEntry, ...]
) -> None:
    """no-upgrade 对账：重算结果不得比已批准 plan 更具破坏性。

    条目集合不一致（计划外新增/消失）或任一动作破坏性升级 → stale。
    """
    approved_map = {e.rel_path: e for e in approved}
    recomputed_map = {e.rel_path: e for e in recomputed}
    if approved_map.keys() != recomputed_map.keys():
        raise RestoreError("stale plan: 重跑规划与已批准计划的条目集合不一致，整体拒绝")
    for rel, new_entry in recomputed_map.items():
        old_rank = _ACTION_RANK[approved_map[rel].action]
        new_rank = _ACTION_RANK[new_entry.action]
        if new_rank > old_rank:
            raise RestoreError(
                f"stale plan: {rel} 的动作从 {approved_map[rel].action} 升级为 "
                f"{new_entry.action}（破坏性升级一律拒绝），请重新确认计划"
            )


def _restore_one_file(src_lp: str, dest: Path, mtime_ns: int, leftovers: list[str]) -> None:
    """单文件恢复：独占临时文件 → fsync → 原子改名 → mtime 保真。

    临时文件由 tempfile.mkstemp 在目标 parent 下以短固定前缀独占创建
    （不含 final filename，避免长文件名溢出 component 上限）；cleanup 只
    针对本次记录的精确临时路径，绝不扫描后缀批量删除；cleanup 失败时
    残留路径记入 leftovers 后再抛错（不静默）。
    """
    parent = dest.parent
    Path(to_long_path(parent)).mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=_TMP_PREFIX, suffix=_TMP_SUFFIX, dir=to_long_path(parent)
    )
    try:
        with open(src_lp, "rb") as fin, os.fdopen(fd, "wb") as fout:
            while chunk := fin.read(1024 * 1024):
                fout.write(chunk)
            fout.flush()
            os.fsync(fout.fileno())
        os.replace(tmp_name, to_long_path(dest))
        # mtime 保真（Windows FILETIME 100ns 粒度截断属平台限制，同 T-03）
        os.utime(to_long_path(dest), ns=(os.stat(to_long_path(dest)).st_atime_ns, mtime_ns))
    except OSError:
        try:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
        except OSError:
            leftovers.append(tmp_name)
        raise
