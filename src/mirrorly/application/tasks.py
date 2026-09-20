"""Task discovery/loading without frontend argument parsing or persistence changes."""

from __future__ import annotations

from pathlib import Path

from ..config import ConfigError, TaskConfig, load_task_config, validate_task_name


class TaskSelectionError(Exception):
    """Invalid or ambiguous task selection, distinct from config loading failures."""


def resolve_task_config(config_root: str | Path, task: str | None = None) -> TaskConfig:
    """解析任务配置：--task 指定；省略时要求 config.d/ 下恰有一个任务。"""
    config_root = Path(config_root)
    config_d = config_root / "config.d"
    if task:
        # --task 直接拼进文件路径，非法值属用法错误（防路径逃逸）
        try:
            validate_task_name(task)
        except ConfigError as e:
            raise TaskSelectionError(f"--task 非法：{e}") from e
        return load_task_config(config_d / f"{task}.toml")
    if not config_d.is_dir():
        raise ConfigError(f"未找到任务配置目录: {config_d}（请先运行 mirrorly init）")
    candidates = sorted(config_d.glob("*.toml"))
    if not candidates:
        raise ConfigError(f"未找到任何任务配置: {config_d}（请先运行 mirrorly init）")
    if len(candidates) > 1:
        names = ", ".join(p.stem for p in candidates)
        raise TaskSelectionError(f"存在多个任务（{names}），必须用 --task 指定")
    return load_task_config(candidates[0])
