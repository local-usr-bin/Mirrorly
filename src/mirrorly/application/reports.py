"""Mandatory report publication; callers own report data and frontend formatting."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from ..repo import RepoError, RepoInfo
from ..scan import to_long_path


class ReportPublicationError(RepoError):
    """报告 JSON 未能完成原子 publication。"""


def write_report(repo: RepoInfo, name: str, data: dict) -> Path:
    """报告落盘到仓库 logs/（M9）：临时文件 + 原子改名（不产生半个 JSON）。

    文件名带微秒时间戳；仍撞名（同微秒）时追加 ``-01`` 等后缀，
    绝不静默覆盖已有报告。
    """
    logs_dir = repo.path / "logs"
    tmp: Path | None = None
    owns_tmp = False
    try:
        Path(to_long_path(logs_dir)).mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
        final = logs_dir / f"{name}-{ts}.json"
        for n in range(1, 100):
            if not os.path.exists(to_long_path(final)):
                break
            final = logs_dir / f"{name}-{ts}-{n:02d}.json"
        else:
            raise RepoError(f"无法分配唯一报告文件名: {name}-{ts}")

        tmp = final.with_suffix(final.suffix + ".tmp")
        payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        # ``x`` 模式同时建立本次调用对 temp 的 ownership；若同名 temp
        # 已存在则 fail closed，异常清理不得删除其他调用留下的文件。
        stream = open(to_long_path(tmp), "x", encoding="utf-8", newline="\n")
        owns_tmp = True
        with stream:
            stream.write(payload)
        os.replace(to_long_path(tmp), to_long_path(final))
        return final
    except OSError as exc:
        if owns_tmp and tmp is not None:
            try:
                os.remove(to_long_path(tmp))
            except OSError:
                # Cleanup is best-effort and must not replace the publication error.
                pass
        raise ReportPublicationError(f"报告发布失败: {tmp or logs_dir}（{exc}）") from exc
