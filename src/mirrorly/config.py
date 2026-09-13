"""任务配置加载（T-01，ADR-011）。

格式：TOML（``tomllib`` 标准库解析，零依赖）。
结构：``config.toml``（全局默认，预留）+ ``config.d/<task>.toml``（每任务一文件）。
解析为严格模式：未知配置节/键一律报错，防止拼写错误静默失效。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

#: 允许的配置节与键（严格模式白名单）
_VALID_SECTIONS: dict[str, set[str]] = {
    "task": {"name", "source"},
    "target": {"path", "filesystem_policy"},
    "filter": {"exclude"},
    "retention": {"keep_last", "keep_monthly"},
    "verify": {"on_write"},
}

_REQUIRED_KEYS = (("task", "name"), ("task", "source"), ("target", "path"))
FILESYSTEM_POLICIES = ("strict", "warn")

DEFAULT_KEEP_LAST = 30
DEFAULT_KEEP_MONTHLY = 12


class ConfigError(Exception):
    """配置解析/校验错误。"""


@dataclass(frozen=True)
class TaskConfig:
    """单个备份任务的配置（ADR-007：多任务实体，MVP 单任务实现）。"""

    name: str
    source: str
    target_path: str
    filesystem_policy: str = "strict"
    exclude: tuple[str, ...] = field(default_factory=tuple)
    keep_last: int = DEFAULT_KEEP_LAST
    keep_monthly: int = DEFAULT_KEEP_MONTHLY
    verify_on_write: bool = True


def load_task_config(path: str | Path) -> TaskConfig:
    """加载并校验任务配置文件（严格模式）。"""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"配置文件不存在: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"TOML 语法错误: {path}: {e}") from e

    _check_unknown_keys(data, path)
    _check_required_keys(data, path)

    task = data.get("task", {})
    target = data.get("target", {})
    filter_ = data.get("filter", {})
    retention = data.get("retention", {})
    verify = data.get("verify", {})

    policy = target.get("filesystem_policy", "strict")
    if policy not in FILESYSTEM_POLICIES:
        raise ConfigError(
            f"{path}: target.filesystem_policy 非法: {policy!r}"
            f"（允许: {' / '.join(FILESYSTEM_POLICIES)}）"
        )

    exclude = filter_.get("exclude", [])
    if not (isinstance(exclude, list) and all(isinstance(p, str) for p in exclude)):
        raise ConfigError(f"{path}: filter.exclude 必须是字符串数组")

    keep_last = retention.get("keep_last", DEFAULT_KEEP_LAST)
    keep_monthly = retention.get("keep_monthly", DEFAULT_KEEP_MONTHLY)
    for key, val in (("retention.keep_last", keep_last), ("retention.keep_monthly", keep_monthly)):
        if not isinstance(val, int) or isinstance(val, bool) or val < 1:
            raise ConfigError(f"{path}: {key} 必须是正整数，得到 {val!r}")

    on_write = verify.get("on_write", True)
    if not isinstance(on_write, bool):
        raise ConfigError(f"{path}: verify.on_write 必须是布尔值")

    for key, val in (
        ("task.name", task["name"]),
        ("task.source", task["source"]),
        ("target.path", target["path"]),
    ):
        if not isinstance(val, str) or not val:
            raise ConfigError(f"{path}: {key} 必须是非空字符串")

    return TaskConfig(
        name=task["name"],
        source=task["source"],
        target_path=target["path"],
        filesystem_policy=policy,
        exclude=tuple(exclude),
        keep_last=keep_last,
        keep_monthly=keep_monthly,
        verify_on_write=on_write,
    )


def write_task_config(cfg: TaskConfig, config_root: str | Path) -> Path:
    """将任务配置写入 ``<config_root>/config.d/<name>.toml``，返回文件路径。"""
    out_dir = Path(config_root) / "config.d"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{cfg.name}.toml"
    out.write_text(dump_task_config(cfg), encoding="utf-8")
    return out


def dump_task_config(cfg: TaskConfig) -> str:
    """序列化为 TOML 文本（供 init 生成默认配置）。"""
    excludes = ", ".join(_toml_basic_str(p) for p in cfg.exclude)
    return (
        "# Mirrorly 备份任务配置（ADR-011：严格模式，未知键会被拒绝）\n"
        "[task]\n"
        f"name = {_toml_basic_str(cfg.name)}\n"
        f"source = {_toml_basic_str(cfg.source)}\n"
        "\n"
        "[target]\n"
        f"path = {_toml_basic_str(cfg.target_path)}\n"
        f"filesystem_policy = {_toml_basic_str(cfg.filesystem_policy)}"
        "  # strict: 非 NTFS 拒绝；warn: 提示后由用户确认继续\n"
        "\n"
        "[filter]\n"
        f"exclude = [{excludes}]\n"
        "\n"
        "[retention]\n"
        f"keep_last = {cfg.keep_last}\n"
        f"keep_monthly = {cfg.keep_monthly}  # 每月至少保留 1 个的月数\n"
        "\n"
        "[verify]\n"
        f"on_write = {str(cfg.verify_on_write).lower()}  # 写入即哈希校验（ADR-009）\n"
    )


def _toml_basic_str(value: str) -> str:
    """TOML 基础字符串字面量（转义反斜杠/引号/控制字符——Windows 路径含 ``\\``）。"""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _check_unknown_keys(data: dict, path: Path) -> None:
    for section, values in data.items():
        if section not in _VALID_SECTIONS:
            raise ConfigError(f"{path}: 未知配置节 [{section}]")
        if not isinstance(values, dict):
            raise ConfigError(f"{path}: 配置节 [{section}] 必须是表")
        for key in values:
            if key not in _VALID_SECTIONS[section]:
                raise ConfigError(f"{path}: 未知配置键 {section}.{key}")


def _check_required_keys(data: dict, path: Path) -> None:
    for section, key in _REQUIRED_KEYS:
        if key not in data.get(section, {}):
            raise ConfigError(f"{path}: 缺少必填配置 {section}.{key}")
