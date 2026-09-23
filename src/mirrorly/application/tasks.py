"""Task discovery/loading without frontend argument parsing or persistence changes."""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class CatalogEntry:
    selector: str
    config_path: Path
    task: TaskConfig | None
    problem: Exception | None = None


@dataclass(frozen=True)
class TaskCatalog:
    entries: tuple[CatalogEntry, ...]
    next_after: str | None


def list_tasks(
    config_root: str | Path, *, after: str | None = None, limit: int = 16
) -> TaskCatalog:
    """Read only this explicit root; no repository resolution, repair or CLI discovery.

    Pages are ordered by config filename (selection identity), not TaskConfig.name.
    They are observations, not a snapshot against concurrent config edits.
    """
    root = Path(config_root)
    if not root.is_absolute() or not 1 <= limit <= 16:
        raise ValueError("Explicit absolute config root and page limit 1..16 required")
    directory = root / "config.d"
    try:
        candidates = sorted(
            (p for p in directory.iterdir() if p.suffix.casefold() == ".toml"),
            key=lambda p: p.name,
        )
    except FileNotFoundError:
        return TaskCatalog((), None)
    candidates = [p for p in candidates if after is None or p.name > after]
    entries = []
    for path in candidates[:limit]:
        try:
            validate_task_name(path.stem)
            task = load_task_config(path)
            entries.append(CatalogEntry(path.stem, path, task))
        except (ConfigError, OSError, UnicodeError) as exc:
            entries.append(CatalogEntry(path.stem, path, None, exc))
    return TaskCatalog(
        tuple(entries), candidates[limit - 1].name if len(candidates) > limit else None
    )
