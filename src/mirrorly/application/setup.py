"""Read-only setup inspection and the existing non-atomic init sequence.

No frontend prompts, presentation state or authorization from cached preflight.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .. import volume as _volume
from ..config import ConfigError, TaskConfig, validate_task_name, write_task_config
from ..repo import (
    HARDLINK_FILESYSTEMS,
    REPO_DIR_NAME,
    RepoError,
    RepoInfo,
    VolumeInfo,
    get_volume_info,
    init_repo,
    inspect_init_target,
)
from .repositories import path_within

SetupStage = Literal[
    "inputs",
    "volume",
    "mount",
    "repository_check",
    "repository_init",
    "config_construct",
    "config_write",
]


@dataclass(frozen=True)
class SetupRequest:
    task_name: str
    source: str | Path
    target: str | Path
    config_root: str | Path
    filesystem_policy: str = "strict"

    @property
    def repository_path(self) -> Path:
        return Path(self.target) / REPO_DIR_NAME

    @property
    def config_path(self) -> Path:
        return Path(self.config_root) / "config.d" / f"{self.task_name}.toml"


class SetupUsageError(Exception):
    """The existing invalid-task/source cases; the CLI maps these to exit 2."""


class CopyModeApprovalRequired(Exception):
    """Warn policy needs explicit approval before mount lookup or any writes."""

    def __init__(self, volume: VolumeInfo) -> None:
        self.volume = volume
        super().__init__("Non-NTFS copy mode requires explicit approval")


class SetupFailure(Exception):
    """Stage/cause and acknowledged effects, never a rollback claim.

    None in the completion properties means unknown, not False. An init failure
    may leave directories/metadata; a config-write failure may leave TOML bytes.
    Completion means acknowledged success in this call, not on-disk existence.
    Interrupts (BaseException) keep their existing propagation semantics.
    """

    def __init__(
        self,
        request: SetupRequest,
        stage: SetupStage,
        cause: Exception,
        repo: RepoInfo | None = None,
    ) -> None:
        super().__init__(str(cause))
        self.stage = stage
        self.cause = cause
        self.repository_path = request.repository_path
        self.config_path = request.config_path
        self.repo = repo

    @property
    def repository_initialized(self) -> bool | None:
        if self.repo is not None:
            return True
        return None if self.stage == "repository_init" else False

    @property
    def config_written(self) -> bool | None:
        return None if self.stage == "config_write" else False


@dataclass(frozen=True)
class SetupPreflight:
    repository_path: Path
    config_path: Path
    # All four input checks passed: name, source, collision and containment.
    inputs_valid: bool = False
    # Initial filesystem query; repository_volume includes the later GUID query.
    volume: VolumeInfo | None = None
    mount_root: str | None = None
    repository_volume: VolumeInfo | None = None
    copy_mode_approval_required: bool = False
    # First blocking problem in the existing check order; not an exhaustive list.
    problem: SetupFailure | None = None


@dataclass(frozen=True)
class SetupResult:
    task: TaskConfig
    repo: RepoInfo
    config_path: Path


def _validate_inputs(request: SetupRequest) -> None:
    try:
        validate_task_name(request.task_name)
    except ConfigError as exc:
        raise SetupUsageError(f"--task 非法：{exc}") from exc
    source = Path(request.source)
    if not source.is_dir():
        raise SetupUsageError(f"--source 不是已存在的目录: {source}")
    if request.config_path.exists():
        raise RepoError(f"任务配置已存在: {request.config_path}（如需重建请先手工删除）")
    prospective_repo = request.repository_path
    if path_within(prospective_repo, source):
        raise RepoError(f"备份目标仓库 {prospective_repo} 位于源目录 {source} 内，拒绝初始化")
    if path_within(source, prospective_repo):
        raise RepoError(f"源目录 {source} 位于备份目标仓库 {prospective_repo} 内，拒绝初始化")


def _needs_copy_approval(request: SetupRequest, volume: VolumeInfo) -> bool:
    return volume.filesystem not in HARDLINK_FILESYSTEMS and request.filesystem_policy == "warn"


def _mount_root(target: Path) -> str:
    try:
        return _volume.get_volume_mount_root(str(target.resolve()))
    except _volume.VolumeError as exc:
        raise RepoError(f"无法解析目标卷挂载点（M10 卷锚所需）: {exc}") from exc


def preflight_setup(request: SetupRequest) -> SetupPreflight:
    """Observe current checks/facts without writes, prompts, locks or reservation.

    Paths retain the existing caller-relative meaning. Missing fields were not
    successfully checked. Approval can be required even when a later check fails.
    Execution takes the request, never this inspection as a validation token.
    """
    inputs_valid = False
    volume = None
    mount_root = None
    repository_volume = None
    approval = False
    problem = None
    stage: SetupStage = "inputs"
    try:
        _validate_inputs(request)
        inputs_valid = True
        stage = "volume"
        volume = get_volume_info(Path(request.target))
        approval = _needs_copy_approval(request, volume)
        stage = "mount"
        mount_root = _mount_root(Path(request.target))
        stage = "repository_check"
        repository_volume = inspect_init_target(
            request.target, filesystem_policy=request.filesystem_policy
        )
    except Exception as exc:
        problem = SetupFailure(request, stage, exc)
    return SetupPreflight(
        repository_path=request.repository_path,
        config_path=request.config_path,
        inputs_valid=inputs_valid,
        volume=volume,
        mount_root=mount_root,
        repository_volume=repository_volume,
        copy_mode_approval_required=approval,
        problem=problem,
    )


def create_backup(request: SetupRequest, *, copy_mode_approved: bool = False) -> SetupResult:
    """Run the existing init sequence, rechecking inputs on every invocation.

    CopyModeApprovalRequired is raised before any writes. A caller that obtains
    approval invokes this again with an explicit True; no saved checks are reused.
    This is not an atomic transaction and never rolls back initialized repositories.
    """
    repo = None
    stage: SetupStage = "inputs"
    try:
        _validate_inputs(request)
        target = Path(request.target)
        stage = "volume"
        volume = get_volume_info(target)
        if _needs_copy_approval(request, volume) and not copy_mode_approved:
            raise CopyModeApprovalRequired(volume)
        stage = "mount"
        mount_root = _mount_root(target)
        stage = "repository_init"
        # Approval is handled above; strict rejection remains in the lower-level checks.
        repo = init_repo(target, filesystem_policy=request.filesystem_policy, assume_yes=True)
        stage = "config_construct"
        assert repo.volume.guid is not None
        rel = os.path.relpath(str(target.resolve()), mount_root)
        repo_dir = "." if rel == "." else rel.replace("/", "\\")
        cfg = TaskConfig(
            name=request.task_name,
            source=str(Path(request.source).resolve()),
            target_path=str(target.resolve()),
            filesystem_policy=request.filesystem_policy,
            volume_guid=repo.volume.guid,
            repo_id=repo.repo_id,
            repo_dir=repo_dir,
        )
        stage = "config_write"
        written = write_task_config(cfg, Path(request.config_root))
        return SetupResult(task=cfg, repo=repo, config_path=written)
    except CopyModeApprovalRequired:
        raise
    except Exception as exc:
        raise SetupFailure(request, stage, exc, repo) from exc
