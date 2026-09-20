"""Existing exclusive task/repository locks; callers own acquisition scope/order."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from ..repo import RepoInfo


class LockBusy(Exception):
    """任务锁被占用（另一实例运行中）→ 退出码 6。"""


class _ExclusiveFileLock:
    """以 O_EXCL lockfile 提供保守的跨进程互斥；不自动清理 stale lock。"""

    def __init__(self, path: Path, busy_message: str) -> None:
        self._path = path
        self._busy_message = busy_message

    def __enter__(self) -> _ExclusiveFileLock:
        try:
            fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as e:
            raise LockBusy(self._busy_message) from e
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"pid={os.getpid()} acquired_at={datetime.now().isoformat()}\n")
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            self._path.unlink()
        except OSError:
            pass


class TaskLock(_ExclusiveFileLock):
    """任务锁（locks/<task>.lock）；保留 task identity / UX 互斥语义。

    MVP 语义：锁文件存在即视为另一实例运行中，不做 stale 自动清理
    （崩溃残留由用户确认后手工删除，避免误判活人锁）。
    """

    def __init__(self, repo: RepoInfo, task_name: str) -> None:
        path = repo.path / "locks" / f"{task_name}.lock"
        super().__init__(
            path,
            f"任务锁被占用: {path}（另一实例可能正在运行；确认无实例运行后可手工删除该锁文件）",
        )


class RepoWriterLock(_ExclusiveFileLock):
    """仓库写锁；序列化同一 repo 的所有 non-dry-run backup transaction。

    独立子命名空间 ``locks/repo-writer/active.lock`` 不会与合法的
    ``locks/<task>.lock`` 身份碰撞。与任务锁一致，stale lock 只允许人工确认
    后清理；正常/异常退出时只移除本进程创建的 lockfile，命名空间持久保留。
    """

    def __init__(self, repo: RepoInfo) -> None:
        self._namespace = repo.path / "locks" / "repo-writer"
        path = self._namespace / "active.lock"
        super().__init__(
            path,
            f"仓库写锁被占用: {path}（同一仓库正由另一备份事务修改；"
            "确认无实例运行后可手工删除该锁文件）",
        )

    def __enter__(self) -> RepoWriterLock:
        self._namespace.mkdir(exist_ok=True)
        super().__enter__()
        return self
