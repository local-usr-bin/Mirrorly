"""Mirrorly 命令行接口（T-09，CLI_SPEC v1.0 / MVP_TASKS T-09）。

职责边界：本模块保留参数解析、确认流程、输出与退出码映射、命令编排，
setup/init、backup、list/verify/restore 编排、解析、锁与报告落盘由 application 层提供；
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
import sys
from pathlib import Path

from . import __version__
from .application import backup as app_backup
from .application import locking, queries, repositories, restoration, setup, tasks, verification
from .application import reports as app_reports
from .config import (
    ConfigError,
    TaskConfig,
)
from .manifest import (
    STATUS_COMPLETE,
    ManifestError,
)
from .recovery import RecoveryError
from .repo import (
    RepoError,
    RepoFormatError,
    RepoInfo,
)
from .restore import RestoreError
from .retention import RetentionError
from .snapshot import (
    SnapshotError,
)
from .verify import VerifyReport

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


def _notify_relocation(cfg: TaskConfig, repo: RepoInfo) -> None:
    """重定位提示：始终走 stderr（--json 下 stdout 仍保持单一 JSON 文档）。"""
    print(
        f"目标卷已重定位: {cfg.target_path} → {repo.path.parent}（卷标识匹配；配置未自动修改）",
        file=sys.stderr,
    )


def _resolve_query_context(args: argparse.Namespace) -> queries.TaskRepository:
    """CLI defaults, selection-error mapping and relocation presentation only."""
    config_root = Path(getattr(args, "config", None) or DEFAULT_CONFIG_ROOT)
    try:
        context = queries.resolve_task_repository(config_root, getattr(args, "task", None))
    except tasks.TaskSelectionError as exc:
        raise _UsageError(str(exc)) from exc
    if context.relocated:
        _notify_relocation(context.task, context.repo)
    return context


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    request = setup.SetupRequest(
        task_name=getattr(args, "task", None) or DEFAULT_TASK_NAME,
        source=args.source,
        target=args.target,
        config_root=Path(getattr(args, "config", None) or DEFAULT_CONFIG_ROOT),
        filesystem_policy=args.filesystem_policy,
    )
    try:
        try:
            result = setup.create_backup(request)
        except setup.CopyModeApprovalRequired as approval:
            # Preserve warning/prompt order: confirmation precedes mount/repo checks.
            volume = approval.volume
            _info(
                args,
                f"目标文件系统为 {volume.filesystem}，不支持硬链接：\n"
                "  - 将无法跨快照共享未变更文件的存储（空间占用显著增加）；\n"
                "  - 备份将以整文件复制模式运行。\n"
                "建议将目标盘转换为 NTFS 后重新 init。",
            )
            _confirm(args, "是否仍以整文件复制模式继续？")
            result = setup.create_backup(request, copy_mode_approved=True)
    except setup.SetupFailure as failure:
        if isinstance(failure.cause, setup.SetupUsageError):
            _err(str(failure.cause))
            return EXIT_USAGE
        # Keep existing CLI exception/exit mapping, not a new DTO/JSON error schema.
        raise failure.cause from None

    info = result.repo
    written = result.config_path
    policy = request.filesystem_policy
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


def _present_resume_notice(args: argparse.Namespace, notice: app_backup.ResumeNotice) -> None:
    """Keep existing notice text/channels at the transaction's original call sites."""
    if notice.kind == "available":
        _info(args, f"检测到中断的备份 {notice.snapshot_id}（正式执行时将提示续传）")
    elif notice.kind == "selected":
        _info(
            args,
            f"将以 {notice.snapshot_id} 为基线续传"
            f"（已物化 {notice.materialized} 个文件，待补 {notice.missing} 个）",
        )
        if notice.untrusted:
            _info(
                args,
                f"  {notice.untrusted} 个已物化文件内容与当前源不一致"
                "（中断后副本可能被损坏），不信任旧副本，将重新复制",
            )
        if notice.uncertified:
            _info(
                args,
                f"  {notice.uncertified} 个已物化文件未能完成内容认证"
                "（源暂不可读或元数据与清单不一致），将重新复制",
            )
    else:
        _info(args, "不续传，从头开始新备份（incomplete 快照保留不动）")


def cmd_backup(args: argparse.Namespace) -> int:
    request = app_backup.BackupRequest(
        config_root=Path(getattr(args, "config", None) or DEFAULT_CONFIG_ROOT),
        task=getattr(args, "task", None),
        dry_run=args.dry_run,
        full_hash=args.full_hash,
        exclude=tuple(args.exclude or ()),
    )

    def decide_resume(decision: app_backup.ResumeDecision) -> bool:
        return _ask(
            args,
            f"检测到中断的备份 {decision.snapshot_id}"
            f"（{decision.created_at}），是否以其为基线续传？"
            "（选 n 将从头开始新备份，incomplete 保留不动）",
        )

    try:
        outcome = app_backup.run_backup(
            request,
            decide_resume=decide_resume,
            on_relocation=_notify_relocation,
            on_resume=lambda notice: _present_resume_notice(args, notice),
        )
    except app_backup.BackupFailure as failure:
        if failure.stage == "task" and isinstance(failure.cause, tasks.TaskSelectionError):
            raise _UsageError(str(failure.cause)) from failure.cause
        if failure.stage == "report" and isinstance(
            failure.cause, app_reports.ReportPublicationError
        ):
            raise RepoError(
                f"快照 {failure.facts.snapshot_id} 已成功提交（complete manifest 已发布），"
                f"但必需报告发布失败；备份命令返回错误: {failure.cause}"
            ) from failure.cause
        # Preserve main()'s existing exception/exit mapping; do not serialize the DTO.
        raise failure.cause from None

    if isinstance(outcome, app_backup.DryRunResult):
        return _finish_dry_run(args, outcome.changes, outcome.scan_skipped)

    report = dict(outcome.report)
    snapshot_id = outcome.facts.snapshot_id
    changes = outcome.facts.changes
    result = outcome.facts.materialization
    duration = outcome.duration_seconds
    resumed_from = outcome.facts.resumed_from
    retention_deleted = outcome.facts.retention_deleted
    report_path = outcome.facts.report_path
    all_skipped = list(outcome.facts.scan_skipped) + list(result.skipped)
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
        if resumed_from:
            _info(args, f"  已基于中断备份 {resumed_from} 续传并清理旧 incomplete")
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


def _present_verification(args: argparse.Namespace, rep: VerifyReport) -> None:
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


def cmd_verify(args: argparse.Namespace) -> int:
    context = _resolve_query_context(args)
    try:
        result = verification.verify_snapshots(
            context,
            snapshot=args.snapshot,
            all_snapshots=args.all,
            quick=args.quick,
            on_verified=lambda rep: _present_verification(args, rep),
        )
    except verification.VerificationFailure as failure:
        if failure.stage == "selection" and isinstance(failure.cause, queries.NoCompleteSnapshot):
            _err("仓库中没有 complete 快照可校验")
            return EXIT_ERROR
        if failure.stage == "report" and isinstance(
            failure.cause, app_reports.ReportPublicationError
        ):
            outcome = "失败" if not failure.facts.ok else "通过"
            raise RepoError(
                f"校验已完成（结果: {outcome}），但必需报告发布失败；"
                f"verify 命令返回错误: {failure.cause}"
            ) from failure.cause
        raise failure.cause from None
    report = dict(result.report)
    report_path = result.facts.report_path
    failed = not result.facts.ok
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
    context = _resolve_query_context(args)

    # CLI_SPEC §4：--in-place 为危险操作，必须显式 --yes（非法组合 → 用法错误）
    if args.in_place and not _flag(args, "yes"):
        _err("--in-place 是危险操作，必须显式提供 --yes")
        return EXIT_USAGE

    try:
        prepared = restoration.prepare_restore(
            context,
            args.to,
            snapshot=args.snapshot,
            paths=tuple(args.path or ()),
            overwrite=args.overwrite,
            in_place=args.in_place,
        )
    except queries.NoCompleteSnapshot:
        _err("仓库中没有 complete 快照可恢复")
        return EXIT_ERROR
    plan = prepared.plan
    snapshot_id = plan.snapshot_id
    facts = prepared.summary
    counts = {
        "create": facts.create,
        "overwrite": facts.overwrite,
        "skip": facts.skip,
        "conflict": facts.conflict,
    }
    dirs = facts.dirs

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
    overwrite_approved = False
    if counts["overwrite"]:
        _confirm(
            args,
            f"将覆盖 {counts['overwrite']} 个已存在文件（策略 {args.overwrite}），确认执行？",
        )
        overwrite_approved = True

    try:
        outcome = restoration.execute_restore(prepared, overwrite_approved=overwrite_approved)
    except restoration.RestoreExecutionFailure as failure:
        raise failure.cause from None
    result = outcome.result
    has_issues = outcome.has_issues
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
    context = _resolve_query_context(args)
    summaries = queries.list_snapshots(context)

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
    except locking.LockBusy as e:
        _err(str(e))
        return EXIT_ABORTED
    except repositories.IdentityMismatch as e:
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
