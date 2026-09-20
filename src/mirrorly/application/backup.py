"""One shared synchronous Backup transaction; no CLI, worker or progress API.

Optional relocation/resume callbacks preserve existing terminal interaction timing.
They carry only existing notice/decision facts, never file/byte/phase progress.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Literal

from ..config import TaskConfig
from ..lifecycle import reserve_lifecycle_sequence
from ..manifest import (
    Manifest,
    create_manifest,
    latest_sequenced_complete,
    list_manifests,
    load_manifest,
    mark_complete,
    newest_eligible_incomplete,
    write_manifest,
)
from ..recovery import build_resume_baseline, clean_tmp_residue
from ..repo import RepoInfo, migrate_repo_to_v2
from ..retention import apply_retention_plan, build_retention_plan
from ..scan import ChangeSet, PreviousEntry, detect_changes, scan_source
from ..snapshot import SnapshotError, SnapshotResult, generate_snapshot_id, write_snapshot
from . import locking, reports, repositories, tasks


@dataclass(frozen=True)
class BackupRequest:
    config_root: str | Path
    task: str | None = None
    dry_run: bool = False
    full_hash: bool = False
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResumeDecision:
    snapshot_id: str
    created_at: str


class ResumeDecisionRequired(Exception):
    """No resume answer was supplied; never silently accept or reject it."""

    def __init__(self, decision: ResumeDecision) -> None:
        self.decision = decision
        super().__init__(f"Resume decision required: {decision.snapshot_id}")


@dataclass(frozen=True)
class ResumeNotice:
    kind: Literal["available", "selected", "declined"]
    snapshot_id: str
    materialized: int = 0
    missing: int = 0
    untrusted: int = 0
    uncertified: int = 0


@dataclass(frozen=True)
class BackupFacts:
    """Acknowledged facts for this attempt, not claims of a fully current source.

    not_published: this attempt has not called the complete publisher (other
    mutations may exist). unknown: that call was entered but did not return.
    published: it returned successfully. A complete candidate manifest alone is
    not evidence of commit; use commit_state. None on retention/report means no
    successful return was observed, not proof of no partial side effects.
    """

    task: TaskConfig | None = None
    repo: RepoInfo | None = None
    relocated: bool = False
    lifecycle_seq: int | None = None
    snapshot_id: str | None = None
    commit_state: Literal["not_published", "unknown", "published"] = "not_published"
    resumed_from: str | None = None
    changes: ChangeSet | None = None
    scan_skipped: tuple[tuple[str, str], ...] = ()
    materialization: SnapshotResult | None = None
    manifest: Manifest | None = None
    retention_deleted: tuple[str, ...] | None = None
    report_path: Path | None = None


BackupStage = Literal[
    "task",
    "repository",
    "relocation_notice",
    "locking",
    "migration",
    "baseline",
    "scan",
    "temp_cleanup",
    "sequence",
    "snapshot_id",
    "incomplete_publication",
    "materialization",
    "final_manifest",
    "complete_publication",
    "resume_cleanup",
    "retention_plan",
    "retention_apply",
    "report_build",
    "report",
    "unlocking",
]


class BackupFailure(Exception):
    """Original failure and known facts; no rollback or inferred partial statistics."""

    def __init__(self, stage: BackupStage, cause: Exception, facts: BackupFacts) -> None:
        super().__init__(str(cause))
        self.stage = stage
        self.cause = cause
        self.facts = facts


@dataclass(frozen=True)
class DryRunResult:
    facts: BackupFacts
    changes: ChangeSet
    scan_skipped: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class BackupResult:
    facts: BackupFacts
    report: dict
    duration_seconds: float

    @property
    def has_issues(self) -> bool:
        return bool(self.report["skipped"])


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


def _resume_answer(
    decision: ResumeDecision, decide_resume: Callable[[ResumeDecision], bool] | None
) -> bool:
    if decide_resume is None:
        raise ResumeDecisionRequired(decision)
    return decide_resume(decision)


def _select_baseline(
    request: BackupRequest,
    cfg: TaskConfig,
    repo: RepoInfo,
    decide_resume: Callable[[ResumeDecision], bool] | None = None,
    on_resume: Callable[[ResumeNotice], None] | None = None,
) -> _Baseline:
    """按 durable sequence 选择续传/complete 基线；legacy 不自动复用。"""
    summaries = list_manifests(repo)
    latest_inc = newest_eligible_incomplete(summaries)
    if latest_inc is not None:
        if request.dry_run:
            if on_resume is not None:
                on_resume(ResumeNotice("available", latest_inc.snapshot_id))
        elif _resume_answer(
            ResumeDecision(latest_inc.snapshot_id, latest_inc.created_at), decide_resume
        ):
            # B1-2：verify_on_write=True 时对已物化条目做内容等价认证，
            # 获得可信哈希覆盖或拒绝信任旧副本；False 时保持既有语义
            baseline = build_resume_baseline(
                repo,
                latest_inc.snapshot_id,
                source=cfg.source,
                verify_content=cfg.verify_on_write,
            )
            if on_resume is not None:
                on_resume(
                    ResumeNotice(
                        "selected",
                        latest_inc.snapshot_id,
                        len(baseline.previous),
                        len(baseline.missing),
                        len(baseline.untrusted),
                        len(baseline.uncertified),
                    )
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
            if on_resume is not None:
                on_resume(ResumeNotice("declined", latest_inc.snapshot_id))

    # 自动 backup baseline 只允许 sequenced complete。迁移后的 legacy-only
    # repo 必须先全量物化一个 v2 complete，不能猜测 legacy latest 并复用。
    latest = latest_sequenced_complete(summaries)
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


def _scan_and_detect(request: BackupRequest, cfg: TaskConfig, baseline: _Baseline):
    """扫描源并做变更检测（dry-run 与真实执行共用同一逻辑）。"""
    excludes = tuple(cfg.exclude) + tuple(request.exclude or ())
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
    if request.full_hash:
        prev_for_detect = {
            k: PreviousEntry(size=v.size, mtime_ns=-1, sha=v.sha)
            for k, v in prev_for_detect.items()
        }
    changes = detect_changes(cfg.source, current, prev_for_detect, baseline.previous_dirs)
    return scan, current, changes


def _snapshot_id_free(repo: RepoInfo, snapshot_id: str) -> bool:
    """快照 id 未被当前正式或 staging artifact 占用。"""
    return (
        not (repo.path / "snapshots" / snapshot_id).exists()
        and not (repo.path / "manifests" / f"{snapshot_id}.json").exists()
        and not (repo.path / "manifests.tmp" / f"{snapshot_id}.json.tmp").exists()
    )


def _new_snapshot_id(repo: RepoInfo, lifecycle_seq: int) -> str:
    """Mint a UUID identity carrying an already-durable attempt sequence."""

    now = datetime.now()
    for _ in range(100):
        candidate = generate_snapshot_id(now, lifecycle_seq=lifecycle_seq)
        if _snapshot_id_free(repo, candidate):
            return candidate
    raise SnapshotError(
        f"无法分配唯一快照 id：sequence {lifecycle_seq} 的连续 100 个 UUID candidate 均已被占用"
    )


def run_backup(
    request: BackupRequest,
    *,
    decide_resume: Callable[[ResumeDecision], bool] | None = None,
    on_relocation: Callable[[TaskConfig, RepoInfo], None] | None = None,
    on_resume: Callable[[ResumeNotice], None] | None = None,
) -> DryRunResult | BackupResult:
    """Resolve current state and execute one preview or one complete transaction.

    Only the existing resume decision and relocation/resume diagnostics have
    synchronous adapters. In real execution resume callbacks stay inside both
    locks; an omitted decision fails closed. These are not progress observers.
    KeyboardInterrupt/BaseException propagate normally, including lock unwinding.
    """
    facts = BackupFacts()
    stage: BackupStage = "task"
    try:
        cfg = tasks.resolve_task_config(request.config_root, request.task)
        facts = replace(facts, task=cfg)
        stage = "repository"
        resolution = repositories.resolve_repo(cfg)
        repo = resolution.repo
        facts = replace(facts, repo=repo, relocated=resolution.relocated)
        if resolution.relocated and on_relocation is not None:
            stage = "relocation_notice"
            on_relocation(cfg, repo)

        if request.dry_run:
            # Preserve zero-write preview: no locks, migration, cleanup or publication.
            stage = "baseline"
            baseline = _select_baseline(request, cfg, repo, decide_resume, on_resume)
            stage = "scan"
            scan, _current, changes = _scan_and_detect(request, cfg, baseline)
            facts = replace(facts, changes=changes, scan_skipped=scan.skipped)
            return DryRunResult(facts, changes, scan.skipped)

        # Keep task -> repository writer ordering and the entire finalization scope.
        stage = "locking"
        with locking.TaskLock(repo, cfg.name), locking.RepoWriterLock(repo):
            stage = "migration"
            repo = migrate_repo_to_v2(repo)
            facts = replace(facts, repo=repo)
            started = time.monotonic()
            stage = "baseline"
            baseline = _select_baseline(request, cfg, repo, decide_resume, on_resume)
            facts = replace(facts, resumed_from=baseline.resumed_from)
            stage = "scan"
            scan, current, changes = _scan_and_detect(request, cfg, baseline)

            facts = replace(facts, changes=changes, scan_skipped=scan.skipped)
            stage = "temp_cleanup"
            clean_tmp_residue(repo)
            # 先分配唯一空闲 id，再落盘 incomplete manifest——任何 id collision 下
            # 既有快照目录 / manifest 字节 / complete 状态都不被触碰
            stage = "sequence"
            lifecycle_seq = reserve_lifecycle_sequence(repo)
            facts = replace(facts, lifecycle_seq=lifecycle_seq)
            stage = "snapshot_id"
            snapshot_id = _new_snapshot_id(repo, lifecycle_seq)
            facts = replace(facts, snapshot_id=snapshot_id)
            source_root = str(Path(cfg.source).resolve())

            # manifest 状态机：incomplete 落盘 → 物化 → complete 原子提交
            stage = "incomplete_publication"
            write_manifest(
                repo,
                create_manifest(
                    snapshot_id,
                    source_root,
                    repo.hash_algorithm,
                    current,
                    lifecycle_seq=lifecycle_seq,
                    resumed_from_snapshot_id=baseline.resumed_from,
                ),
            )
            stage = "materialization"
            result = write_snapshot(
                cfg.source,
                repo,
                current,
                changes,
                snapshot_id=snapshot_id,
                previous_snapshot=baseline.previous_snapshot_dir,
                verify_writes=cfg.verify_on_write,
            )

            facts = replace(facts, materialization=result)
            stage = "final_manifest"
            skipped_paths = {p for p, _ in result.skipped}
            final_current = {k: v for k, v in current.items() if k not in skipped_paths}
            # linked 文件 sha 从基线 manifest 结转（不重算），copied 用写入校验哈希
            merged_hashes = {
                rel: baseline.carried_shas[rel]
                for rel in result.linked
                if rel in baseline.carried_shas
            } | result.hashes
            final_manifest = create_manifest(
                snapshot_id,
                source_root,
                repo.hash_algorithm,
                final_current,
                hashes=merged_hashes,
                lifecycle_seq=lifecycle_seq,
                resumed_from_snapshot_id=baseline.resumed_from,
            )
            facts = replace(facts, manifest=final_manifest)
            # 全局 defense-in-depth：当前 verify_on_write=True 的 complete 快照
            # 不允许存在无可信哈希的普通文件条目。skipped/deleted 文件已不在
            # final_current，目录不需要内容哈希；其余文件必须来自可信结转，或
            # 正常 copy/write-verify。若未来回归产生 coverage gap，在此 fail
            # closed（新快照保持 incomplete），绝不现场重哈希副本来生成信任。
            if cfg.verify_on_write:
                unhashed_final = sorted(
                    entry.path
                    for entry in final_manifest.entries
                    if not entry.is_dir and not entry.sha
                )
                if unhashed_final:
                    raise SnapshotError(
                        f"快照 {snapshot_id} 存在 {len(unhashed_final)} 个未获得"
                        f"可信哈希的文件（首个: {unhashed_final[0]!r}），"
                        "拒绝发布 complete（fail closed）"
                    )
            complete_manifest = mark_complete(final_manifest)
            stage = "complete_publication"
            facts = replace(facts, manifest=complete_manifest, commit_state="unknown")
            write_manifest(repo, complete_manifest)
            facts = replace(facts, commit_state="published")

            # 续传善后：显式删除旧 incomplete（complete 快照被底层拒绝，双保险）
            stage = "resume_cleanup"
            if baseline.resumed_from:
                from ..recovery import discard_incomplete

                discard_incomplete(repo, baseline.resumed_from)

            # 保留策略（dry-run 已在上方返回；此处为真实执行，复核语义在底层）
            stage = "retention_plan"
            plan = build_retention_plan(
                repo, keep_last=cfg.keep_last, keep_monthly=cfg.keep_monthly
            )
            stage = "retention_apply"
            retention_deleted = apply_retention_plan(repo, plan)
            facts = replace(facts, retention_deleted=tuple(retention_deleted))

            stage = "report_build"
            duration = time.monotonic() - started
            all_skipped = list(scan.skipped) + list(result.skipped)
            report = {
                "command": "backup",
                "snapshot_id": snapshot_id,
                "status": "complete",
                "source": source_root,
                "duration_seconds": round(duration, 3),
                "full_hash": bool(request.full_hash),
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
            stage = "report"
            report_path = reports.write_report(repo, f"backup-{snapshot_id}", report)
            facts = replace(facts, report_path=report_path)
            stage = "unlocking"
        return BackupResult(facts, report, duration)
    except Exception as exc:
        raise BackupFailure(stage, exc, facts) from exc
