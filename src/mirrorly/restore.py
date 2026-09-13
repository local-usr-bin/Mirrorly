"""恢复引擎（T-08，TR-4 防覆盖 / CLI_SPEC 第 4 节）。

两阶段模型：``plan_restore`` 产出 RestorePlan（**批准的意图**，不是可信事实源），
``apply_restore`` 执行前不信任 plan——重新校验 plan 参数本身、重新加载 complete
manifest、校验 manifest 指纹（digest）、重新验证安全边界、重跑规划，并按
**no-upgrade 对账**：

- 重算出的每个动作破坏性不得超过已批准 plan（skip < create < overwrite）；
- 任何破坏性升级（如批准 skip 而重算要求 overwrite）一律判 stale，
  整体拒绝、零写入；
- 破坏性持平或降级（如批准 overwrite 而重算只需 skip）按重算结果执行。

安全纪律（不可违反）：

1. **manifest 是边界，不是免校验凭据**：load 后、selector 过滤**之前**，
   全量校验所有 manifest 条目路径的 canonical 合法性（防篡改 manifest 把
   ``../`` 写进恢复目标——哪怕该条目未被选中），并拒绝重复条目路径（防
   字典折叠/同路径重复执行）；selector 为字面 snapshot-relative 路径，
   **不做 glob/fnmatch 扩展语义**；
2. **canonical Windows 路径校验**：POSIX 风格相对路径（**反斜杠一律
   非法**——manifest 中出现 ``\\`` 属异常，fail closed；用户 ``--path``
   输入的 Windows 反斜杠由 ``normalize_selector`` 先行归一）；空段、
   ``.``/``..`` 段、绝对路径、控制字符、``<>:"|?*``、尾随点/空格、
   保留设备名（CON PRN AUX NUL COM1-9 LPT1-9 COM¹ COM² COM³ LPT¹ LPT²
   LPT³，含带扩展名形式）一律非法；component 长度按 **UTF-16 code
   unit** 计（上限 255），不把 Python ``len()`` 当作 Windows 底层长度
   语义（非 BMP 字符占 2 个 code unit）；同一份 manifest 内拒绝
   casefold 后冲突的路径（Windows case-insensitive collision 防护）；
3. **reparse 双侧防护**：snapshot 读侧与 destination 写侧遇到 reparse
   point（junction/symlink 等）保守拒绝——这是**有意的兼容性限制**，不做
   跨链接恢复；check-then-open 的残余 TOCTOU 风险在 MVP 中**明确接受**
   （handle-based no-follow 留待 MVP 后评估，OQ-2）；
4. **目标边界（按实际文件系统身份判定）**：destination 落在仓库目录内一律
   拒绝——比较同时使用 normpath/normcase 与 realpath 解析后的路径，加上
   已存在路径的 ``os.path.samefile`` 真实身份比较，**经 junction/symlink
   等别名到达仓库不能绕过边界**；destination 与 manifest 记录的 source_root
   为同一实际位置（含别名路径）时必须显式 in_place；source_root 的普通子
   目录允许恢复（无需 in_place）；
5. **防覆盖**：never 跳过 / older 仅当目标 mtime **严格更旧**才覆盖 /
   always 覆盖普通文件；file/dir 类型冲突即使 always 也**不得自动删除
   用户目录**（或文件），记 conflict 跳过；
6. **紧邻 I/O 逐条重验 + commit 前最终复核**：plan 阶段与 apply 执行阶段
   共享同一条目分类器（``_classify_entry``）。apply 在整个 batch 重跑规划
   后，对**每个即将产生写操作的条目**，在真正 I/O 前再跑一次分类器：重查
   destination 存在性/类型、当前覆盖决策、destination root/祖先/leaf 与
   snapshot root/祖先/leaf 的 reparse 状态，并与批准动作做单条 no-upgrade
   比较——升级即拒绝该条（不覆盖），降级/持平按当前实况执行。大文件复制
   可能耗时很长，因此 temp staging 完成后、``os.replace`` 之前还会做
   **commit 前最终复核**（以本条 fresh action 为基线再跑一次分类器）：
   升级/变 skip/变 conflict 一律不 commit（temp 精确清理），真正接受的
   TOCTOU 收敛为「final check → os.replace」的小窗口，而不是「fresh
   check → 数分钟复制 → replace」的大窗口；
7. **写入方式**：目标 parent 下 ``tempfile.mkstemp`` 独占创建临时文件
   （短固定前缀，不借用 final filename 作 prefix——合法 final 名可能已
   接近 component 长度上限，追加随机串会溢出）；flush + fsync + close 后
   **先在临时文件上设置目标 mtime**，经 commit 前复核后再 ``os.replace``
   原子改名——replace 是单文件唯一 commit point：replace 前任何失败旧
   目标不动，replace 后不存在「必需步骤失败导致已覆盖却报失败」的窗口。
   cleanup 只按本次运行明确记录的精确临时路径，绝不扫描后缀批量删除；
   cleanup 失败记入 leftovers；
8. **属性查询 fail closed**：只有 FileNotFoundError / NotADirectoryError
   （及 Windows ERROR_FILE_NOT_FOUND / ERROR_PATH_NOT_FOUND）才视为
   「不存在」；权限或其他无法判断属性的错误一律 RestoreError，不带着
   未知状态继续写入；
9. **plan 冻结路径**：plan 阶段把 destination 固化为稳定的绝对路径存入
   RestorePlan，apply 只使用该冻结位置（拒绝相对路径），不受 apply 时
   进程 cwd 影响；apply 开头还重新校验 overwrite 取值、paths 的 canonical
   合法性与 entry action 合法集，伪造参数走不进任何分支；
10. **partial restore**：单文件失败记入 errors 继续，不做整体回滚；
11. 恢复产物为普通文件，**不自动执行 full verify**（Q2 裁定）。

边界：CLI 交互（确认提示、退出码）属 T-09，本层只提供结构化 plan/result。
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .hashing import hash_file
from .manifest import Manifest, ManifestEntry, load_manifest
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

#: Windows GetLastError：文件/路径不存在（仅此两者视为「不存在」）
_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3


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

    destination 为 plan 阶段冻结的稳定绝对路径（apply 不受进程 cwd 影响）；
    manifest_digest 为计划所依据 manifest 文件的哈希（仓库算法）。apply 阶段
    不信任本对象的任何字段：参数合法性重验、manifest 重载、digest 比对、
    安全边界重验、重跑规划并做 no-upgrade 对账。
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
    conflicts: tuple[tuple[str, str], ...] = ()  # 类型冲突/破坏性升级拒绝，未动作
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
    if "\\" in rel:
        # canonical snapshot-relative POSIX path：反斜杠属异常，fail closed
        # （用户 --path 输入的 Windows 反斜杠由 normalize_selector 先行归一，
        #  不经过本拒绝路径）
        raise RestoreError(f"{what}不允许反斜杠（须为 POSIX 风格相对路径）: {rel!r}")
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
    """路径安全：快照 id 必须是单段 canonical Windows 名称（第三份 validator，Q1 裁定暂接受）。

    生成格式 ``%Y-%m-%d_%H%M%S`` 不含冒号；Windows 上 ``:`` 同时是 NTFS
    alternate data stream 分隔符，任何冒号一律拒绝。复用单组件 canonical
    规则（保留设备名、尾随点/空格、控制字符、禁止字符、长度上限）避免
    落入 Windows 特殊名字语义。
    """
    if "/" in snapshot_id:
        raise RestoreError(f"非法快照 id（必须单段）: {snapshot_id!r}")
    try:
        validate_canonical_rel_path(snapshot_id, what="快照 id")
    except RestoreError as e:
        raise RestoreError(f"非法快照 id: {snapshot_id!r}（{e}）") from e


def _validate_manifest_paths(manifest: Manifest) -> None:
    """全量校验 manifest 条目路径（selector 过滤之前调用）。

    任一非法路径（含未被 selector 选中的条目）→ RestoreError 整体拒绝；
    重复路径同样拒绝（manifest 层不检测重复，防字典折叠/同路径重复执行）。
    """
    seen_exact: set[str] = set()
    seen_folded: set[str] = set()
    for e in manifest.entries:
        validate_canonical_rel_path(e.path, what="manifest 条目路径")
        if e.path in seen_exact:
            raise RestoreError(f"manifest 含重复条目路径（拒绝重复执行/字典折叠）: {e.path!r}")
        # Windows case-insensitive collision 防护（fail closed）：casefold 后
        # 冲突即拒绝——不模拟 NTFS 内核级名字规则，保守而可解释
        folded = e.path.casefold()
        if folded in seen_folded:
            raise RestoreError(
                f"manifest 含 Windows 大小写冲突条目路径（fail closed，拒绝执行）: {e.path!r}"
            )
        seen_exact.add(e.path)
        seen_folded.add(folded)


# ---------------------------------------------------------------------------
# reparse 防护（双侧保守拒绝；check-then-open 残余 TOCTOU 为已接受的 MVP 限制）
# ---------------------------------------------------------------------------


def _file_attributes(path_lp: str) -> int | None:
    """读取路径的文件属性位；路径**明确不存在**返回 None。

    fail closed：只有 FileNotFoundError / NotADirectoryError（lstat 侧）或
    Windows ERROR_FILE_NOT_FOUND / ERROR_PATH_NOT_FOUND（GetFileAttributesW
    侧）才视为「不存在」；权限或其他无法判断属性的错误一律抛 RestoreError，
    绝不带着未知状态继续写入。

    优先 os.lstat（对符号链接识别最可靠：S_ISLNK / st_file_attributes）；
    lstat 报不存在时在 Windows 上回退 GetFileAttributesW（use_last_error
    读取真实错误码；扩展前缀失败再退普通路径）。
    """
    try:
        st = os.lstat(path_lp)
    except (FileNotFoundError, NotADirectoryError):
        st = None
    except OSError as e:
        raise RestoreError(f"无法读取路径属性（fail closed，拒绝继续）: {path_lp}（{e}）") from e
    if st is not None:
        if stat.S_ISLNK(st.st_mode):
            return _FILE_ATTRIBUTE_REPARSE_POINT
        attrs = getattr(st, "st_file_attributes", None)
        return attrs if attrs is not None else 0
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        func = kernel32.GetFileAttributesW
        func.restype = wintypes.DWORD  # INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
        func.argtypes = [wintypes.LPCWSTR]
        attrs = func(path_lp)
        if attrs == 0xFFFFFFFF and path_lp.startswith("\\\\?\\"):
            attrs = func(path_lp[4:])  # 扩展前缀失败时回退普通路径
        if attrs == 0xFFFFFFFF:
            err = ctypes.get_last_error()
            if err in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND):
                return None
            raise RestoreError(
                f"无法读取路径属性（GetFileAttributesW 错误 {err}，fail closed）: {path_lp}"
            )
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
        attrs = _file_attributes(to_long_path(current))
        if attrs is None:
            break  # 往下的层级尚不存在，由我们创建，安全
        if attrs & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise RestoreError(f"路径含 reparse point（链接/交接点），保守拒绝: {current}")


# ---------------------------------------------------------------------------
# 目标边界（按实际文件系统身份判定，别名路径不能绕过）
# ---------------------------------------------------------------------------


def _norm(path: Path) -> str:
    """大小写不敏感的规范化绝对路径（比较用；不解析链接）。"""
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def _real_norm(path: Path) -> str:
    """realpath 解析已存在祖先中的 junction/symlink 后的规范化路径（比较用）。"""
    return os.path.normcase(os.path.realpath(os.path.abspath(str(path))))


def _same_actual_location(a: Path, b: Path) -> bool:
    """两路径是否指向同一实际位置：已存在优先 samefile 真实身份比较，
    否则退 realpath 规范化比较（覆盖别名/链接解析后的路径相等）。"""
    try:
        return os.path.samefile(str(a), str(b))
    except OSError:
        return _real_norm(a) == _real_norm(b)


def _check_destination(
    repo: RepoInfo, destination: Path, manifest: Manifest, in_place: bool
) -> None:
    """目标边界：仓库内拒绝（含别名到达）；等于原 source_root（同一实际位置，
    含别名）必须 in_place；普通子目录允许。"""
    dest_variants = {_norm(destination), _real_norm(destination)}
    repo_variants = {_norm(repo.path), _real_norm(repo.path)}
    for d in dest_variants:
        for r in repo_variants:
            if d == r or d.startswith(r + os.sep):
                raise RestoreError(f"恢复目标不能位于仓库目录内: {destination}")
    if _same_actual_location(destination, Path(manifest.source_root)) and not in_place:
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


def _select_entries(manifest: Manifest, selectors: tuple[str, ...]) -> list[ManifestEntry]:
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


def _classify_entry(
    m_entry: ManifestEntry,
    snap_dir: Path,
    destination: Path,
    overwrite: str,
) -> RestoreEntry:
    """单条目动作分类器——plan 阶段与 apply 紧邻 I/O 前共享同一判断逻辑。

    检查：条目路径 canonical、snapshot root/祖先链/leaf reparse（抛
    RestoreError，快照完整性异常）、destination root reparse（抛
    RestoreError）、destination 祖先链/leaf reparse（逐条 conflict）、
    目标存在性与类型、覆盖策略（never / older 严格更旧 / always）。
    """
    rel = m_entry.path
    validate_canonical_rel_path(rel, what="manifest 条目路径")
    dest = destination / Path(rel)
    dest_lp = to_long_path(dest)

    # snapshot 读侧 reparse 防护（root + 快照内祖先链 + 条目自身）：
    # 快照内出现 reparse 属完整性异常，抛错（plan 整体拒绝 / apply 单条 error）
    snap_root_attrs = _file_attributes(to_long_path(snap_dir))
    if snap_root_attrs is not None and snap_root_attrs & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise RestoreError(f"快照根为 reparse point（链接/交接点），保守拒绝: {snap_dir}")
    _check_no_reparse_chain(snap_dir, snap_dir / Path(rel))

    # destination 写侧 root reparse 防护：抛错（plan 整体拒绝 / apply 单条 error）
    dest_root_attrs = _file_attributes(to_long_path(destination))
    if dest_root_attrs is not None and dest_root_attrs & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise RestoreError(f"恢复目标为 reparse point（链接/交接点），保守拒绝: {destination}")

    # destination 写侧祖先链/目标自身 reparse 防护：
    # 用户目录里的链接不拖垮整体，逐条记 conflict
    try:
        _check_no_reparse_chain(destination, dest)
    except RestoreError as e:
        return RestoreEntry(rel, dest, m_entry.is_dir, ACTION_CONFLICT, str(e))

    if m_entry.is_dir:
        if not os.path.lexists(dest_lp):
            return RestoreEntry(rel, dest, True, ACTION_CREATE)
        if os.path.isdir(dest_lp):
            return RestoreEntry(rel, dest, True, ACTION_SKIP, "目录已存在")
        return RestoreEntry(
            rel, dest, True, ACTION_CONFLICT, "目标已存在同名文件，拒绝删除用户文件"
        )

    # 文件条目：父链上存在非目录祖先则无法落位
    blocker = _first_non_dir_ancestor(destination, dest.parent)
    if blocker is not None:
        return RestoreEntry(
            rel, dest, False, ACTION_CONFLICT, f"目标父路径被同名文件阻挡: {blocker}"
        )

    if not os.path.lexists(dest_lp):
        return RestoreEntry(rel, dest, False, ACTION_CREATE)
    if not os.path.isfile(dest_lp):
        return RestoreEntry(
            rel, dest, False, ACTION_CONFLICT, "目标已存在同名目录，拒绝删除用户目录"
        )
    # 目标已存在同名普通文件：按覆盖策略
    if overwrite == "never":
        return RestoreEntry(rel, dest, False, ACTION_SKIP, "目标已存在（never）")
    if overwrite == "always":
        return RestoreEntry(rel, dest, False, ACTION_OVERWRITE)
    # older：仅当目标 mtime 严格更旧
    dest_mtime = os.stat(dest_lp).st_mtime_ns
    if dest_mtime < m_entry.mtime_ns:
        return RestoreEntry(rel, dest, False, ACTION_OVERWRITE, "目标更旧")
    return RestoreEntry(rel, dest, False, ACTION_SKIP, "目标不旧于快照（older）")


def _plan_entries(
    manifest: Manifest,
    snap_dir: Path,
    destination: Path,
    selectors: tuple[str, ...],
    overwrite: str,
) -> tuple[RestoreEntry, ...]:
    """核心规划：逐条目判定动作。plan 与 apply 共用（apply 重跑规划）。"""
    entries = [
        _classify_entry(m_entry, snap_dir, destination, overwrite)
        for m_entry in _select_entries(manifest, selectors)
    ]
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

    - manifest 必须 complete（ManifestError 透传）；load 后、selector 过滤前
      全量校验所有条目路径（含未选中条目）并拒绝重复路径；
    - paths 为字面 snapshot-relative 路径（文件命中自身、目录命中整棵子树），
      无 glob 语义，未命中任何条目即报错；
    - destination 在此**冻结为稳定的绝对路径**存入 plan（apply 不受 cwd 影响）；
    - 安全边界（canonical 路径、reparse、目标边界）在此即全量校验，
      apply 阶段会再次校验（不信任 plan）。
    """
    if overwrite not in OVERWRITE_POLICIES:
        allowed = " / ".join(OVERWRITE_POLICIES)
        raise RestoreError(f"非法覆盖策略: {overwrite!r}（允许: {allowed}）")
    _validate_snapshot_id(snapshot_id)
    # 冻结稳定绝对路径：apply 只能使用此位置，不受 apply 时进程 cwd 影响
    destination = Path(os.path.abspath(str(destination)))
    selectors = tuple(normalize_selector(p) for p in paths)

    manifest = load_manifest(repo, snapshot_id, require_complete=True)
    _validate_manifest_paths(manifest)
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
    0. 重新校验 plan 参数本身：overwrite 合法集、paths canonical、
       entry action 合法集、destination 必须是 plan 冻结的绝对路径；
    1. 重新 load_manifest(require_complete=True)、全量校验条目路径，
       并重算 manifest digest 与 plan.manifest_digest 比对，
       不一致 → stale，整体拒绝零写入；
    2. 以 plan 的相同参数重跑规划（安全边界全量重验）；
    3. no-upgrade 对账：重算动作的破坏性逐项不得超过已批准 plan，
       任一升级 → stale，整体拒绝零写入；
    4. 执行：对**每个即将写入的条目**紧邻 I/O 前再跑一次共享分类器
       （重查目标存在性/类型/覆盖决策/双侧 reparse），单条破坏性升级
       即拒绝该条（记 conflict，不覆盖）；目录创建；文件经独占临时文件
       + fsync + temp 上设置 mtime + 原子改名；单文件失败记入 errors 继续。
    """
    # 0. plan 参数复核（伪造参数走不进任何分支）
    _validate_snapshot_id(plan.snapshot_id)
    if plan.overwrite not in OVERWRITE_POLICIES:
        allowed = " / ".join(OVERWRITE_POLICIES)
        raise RestoreError(f"非法覆盖策略: {plan.overwrite!r}（允许: {allowed}）")
    if not plan.destination.is_absolute():
        raise RestoreError(f"plan.destination 必须是 plan 阶段冻结的绝对路径: {plan.destination!r}")
    for sel in plan.paths:
        validate_canonical_rel_path(sel, what="plan.paths")
    for e in plan.entries:
        if e.action not in _ACTION_RANK:
            raise RestoreError(f"plan 含非法动作: {e.action!r}")

    # 1. 事实源复核：manifest 状态 + 全量路径校验 + 指纹
    manifest = load_manifest(repo, plan.snapshot_id, require_complete=True)
    _validate_manifest_paths(manifest)
    if _manifest_digest(repo, plan.snapshot_id) != plan.manifest_digest:
        raise RestoreError(
            f"stale plan: manifest 在计划后发生变化（digest 不一致），整体拒绝: {plan.snapshot_id}"
        )

    snap_dir = repo.path / "snapshots" / plan.snapshot_id
    if not snap_dir.is_dir():
        raise RestoreError(f"快照目录不存在: {snap_dir}")

    # 2. 重跑规划（含 canonical / reparse / 目标边界全量重验）
    destination = plan.destination
    _check_destination(repo, destination, manifest, plan.in_place)
    recomputed = _plan_entries(manifest, snap_dir, destination, plan.paths, plan.overwrite)

    # 3. no-upgrade 对账
    _reconcile_no_upgrade(plan.entries, recomputed)

    # 4. 执行（逐条紧邻 I/O 重验）
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
        m_entry = manifest_map[entry.rel_path]
        # 紧邻 I/O 前重验：与 plan 共享同一分类器，防「batch 规划 → 实际写入」
        # 之间目标状态变化被旧决策静默覆盖
        try:
            fresh = _classify_entry(m_entry, snap_dir, destination, plan.overwrite)
        except RestoreError as e:
            errors.append((entry.rel_path, f"执行前重验失败: {e}"))
            continue
        if fresh.action == ACTION_SKIP:
            skipped.append((fresh.rel_path, fresh.reason))
            continue
        if fresh.action == ACTION_CONFLICT:
            conflicts.append((fresh.rel_path, fresh.reason))
            continue
        if _ACTION_RANK[fresh.action] > _ACTION_RANK[entry.action]:
            conflicts.append(
                (
                    entry.rel_path,
                    f"执行前状态变化导致破坏性升级（{entry.action} → {fresh.action}），拒绝写入",
                )
            )
            continue
        try:
            if fresh.is_dir:
                Path(to_long_path(fresh.dest_path)).mkdir(parents=True, exist_ok=True)
                dirs_created.append(fresh.rel_path)
            else:
                src_lp = to_long_path(snap_dir / Path(fresh.rel_path))
                if not os.path.isfile(src_lp):
                    raise RestoreError("快照内文件缺失或类型不符（快照可能损坏）")
                # 读侧最终防线：写入前复查源非 reparse
                if _is_reparse(src_lp):
                    raise RestoreError("快照内条目为 reparse point，保守拒绝")
                veto = _restore_one_file(
                    src_lp,
                    fresh.dest_path,
                    m_entry.mtime_ns,
                    leftovers,
                    pre_commit=lambda fresh=fresh, m_entry=m_entry: _final_recheck(
                        m_entry, snap_dir, destination, plan.overwrite, fresh.action
                    ),
                )
                if veto is None:
                    restored.append(fresh.rel_path)
                    bytes_written += m_entry.size
                elif veto[0] == ACTION_SKIP:
                    skipped.append((fresh.rel_path, veto[1]))
                else:
                    conflicts.append((fresh.rel_path, veto[1]))
        except (OSError, RestoreError) as e:
            errors.append((fresh.rel_path, str(e)))

    return RestoreResult(
        snapshot_id=plan.snapshot_id,
        destination=destination,
        restored=tuple(restored),
        dirs_created=tuple(dirs_created),
        skipped=tuple(skipped),
        conflicts=tuple(conflicts),
        errors=tuple(errors),
        leftovers=tuple(leftovers),
        bytes_written=bytes_written,
    )


def _final_recheck(
    m_entry: ManifestEntry,
    snap_dir: Path,
    destination: Path,
    overwrite: str,
    baseline_action: str,
) -> tuple[str, str] | None:
    """commit 前最终复核（temp staging 完成后、os.replace 前调用）。

    以本条开始真正执行时的 fresh action 为基线重新分类 destination 当前
    状态（存在性/类型/覆盖决策/双侧 reparse）：

    - final 为 skip/conflict → 不 commit，按 final 报告；
    - final 比基线更具破坏性（如 create → overwrite）→ 不 commit，
      记 conflict（破坏性升级，绝不覆盖新出现的文件）；
    - final 持平或降级（更安全）→ 允许 commit。
    """
    final = _classify_entry(m_entry, snap_dir, destination, overwrite)
    if final.action in (ACTION_SKIP, ACTION_CONFLICT):
        return final.action, f"commit 前状态变化: {final.reason}"
    if _ACTION_RANK[final.action] > _ACTION_RANK[baseline_action]:
        return (
            ACTION_CONFLICT,
            f"commit 前状态变化导致破坏性升级（{baseline_action} → {final.action}），拒绝覆盖",
        )
    return None


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


def _restore_one_file(
    src_lp: str,
    dest: Path,
    mtime_ns: int,
    leftovers: list[str],
    pre_commit: Callable[[], tuple[str, str] | None] | None = None,
) -> tuple[str, str] | None:
    """单文件恢复：独占临时文件 → fsync → temp 上设置 mtime → commit 前复核 → 原子改名。

    ``os.replace`` 是单文件唯一 commit point：replace 前任何失败（含 mtime
    设置失败、pre-commit 复核否决）旧目标保持不动。

    pre_commit：可选回调，在 temp 完整准备好（写入 + flush + fsync + close
    + mtime 设置完成）之后、``os.replace`` 之前调用，对 destination 状态做
    最终复核。返回 None 允许 commit；返回 ``(action, reason)`` 则**不
    commit**——temp 按本次 ownership 精确清理（失败进 leftovers）后，本函数
    把 ``(action, reason)`` 返回给调用方报告。回调抛异常视为失败（cleanup
    后上抛）。这样真正接受的 TOCTOU 收敛为「final check → os.replace」的
    小窗口，而不是「fresh check → 数分钟复制 → replace」的大窗口（大文件
    复制期间出现的同名文件绝不被静默覆盖）。

    临时文件由 tempfile.mkstemp 在目标 parent 下以短固定前缀独占创建
    （不含 final filename，避免长文件名溢出 component 上限）；cleanup 只
    针对本次记录的精确临时路径，绝不扫描后缀批量删除。

    返回 None 表示已提交；返回 ``(action, reason)`` 表示 pre-commit 复核否决。
    """
    parent = dest.parent
    Path(to_long_path(parent)).mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=_TMP_PREFIX, suffix=_TMP_SUFFIX, dir=to_long_path(parent)
    )
    fd_owned = True  # mkstemp 返回的原始 fd；所有权转移给 fout 后置 False
    try:
        with open(src_lp, "rb") as fin:
            with os.fdopen(fd, "wb") as fout:
                fd_owned = False  # 此后 fd 由 with 负责关闭
                while chunk := fin.read(1024 * 1024):
                    fout.write(chunk)
                fout.flush()
                os.fsync(fout.fileno())
        # mtime 保真落在 temp 上（replace 之前）；Windows FILETIME 100ns
        # 粒度截断属平台限制（同 T-03）
        os.utime(tmp_name, ns=(os.stat(tmp_name).st_atime_ns, mtime_ns))
        # commit 前最终复核：否决则不 replace（temp 清理后由调用方报告）
        if pre_commit is not None:
            veto = pre_commit()
            if veto is not None:
                try:
                    os.remove(tmp_name)
                except OSError:
                    leftovers.append(tmp_name)
                return veto
        os.replace(tmp_name, to_long_path(dest))
        return None
    except (OSError, RestoreError):
        if fd_owned:
            # source open 失败等未转移所有权的路径：原始 fd 必须显式关闭，
            # 否则 Windows 上 temp 文件被占用无法清理（且无主 fd 泄漏）
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
        except OSError:
            leftovers.append(tmp_name)
        raise
