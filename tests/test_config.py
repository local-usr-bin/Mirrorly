"""T-01 测试：TOML 任务配置加载与严格模式（ADR-011）。"""

import pytest

from mirrorly.config import ConfigError, TaskConfig, load_task_config, write_task_config

FULL_CONFIG = """\
[task]
name = "daily"
source = "D:/Data"

[target]
path = "E:/Backups"
filesystem_policy = "warn"

[filter]
exclude = ["*.tmp", "node_modules/"]

[retention]
keep_last = 10
keep_monthly = 6

[verify]
on_write = false
"""

MINIMAL_CONFIG = """\
[task]
name = "daily"
source = "D:/Data"

[target]
path = "E:/Backups"
"""


def _write(tmp_path, text: str):
    f = tmp_path / "daily.toml"
    f.write_text(text, encoding="utf-8")
    return f


class TestLoadValid:
    def test_full_config(self, tmp_path) -> None:
        cfg = load_task_config(_write(tmp_path, FULL_CONFIG))
        assert cfg.name == "daily"
        assert str(cfg.source).replace("\\", "/") == "D:/Data"
        assert cfg.filesystem_policy == "warn"
        assert cfg.exclude == ("*.tmp", "node_modules/")
        assert cfg.keep_last == 10
        assert cfg.keep_monthly == 6
        assert cfg.verify_on_write is False

    def test_minimal_config_applies_defaults(self, tmp_path) -> None:
        cfg = load_task_config(_write(tmp_path, MINIMAL_CONFIG))
        assert cfg.filesystem_policy == "strict"
        assert cfg.exclude == ()
        assert cfg.keep_last == 30
        assert cfg.keep_monthly == 12
        assert cfg.verify_on_write is True


class TestStrictMode:
    def test_unknown_section_rejected(self, tmp_path) -> None:
        bad = MINIMAL_CONFIG + "\n[unknown]\nfoo = 1\n"
        with pytest.raises(ConfigError, match="unknown"):
            load_task_config(_write(tmp_path, bad))

    def test_unknown_nested_key_rejected(self, tmp_path) -> None:
        bad = MINIMAL_CONFIG.replace('path = "E:/Backups"', 'path = "E:/Backups"\nkeep_lst = 5')
        with pytest.raises(ConfigError, match="keep_lst"):
            load_task_config(_write(tmp_path, bad))

    def test_unknown_key_in_unknown_like_section(self, tmp_path) -> None:
        bad = MINIMAL_CONFIG + "\n[retention]\nkeep_daily = 7\n"
        with pytest.raises(ConfigError, match="keep_daily"):
            load_task_config(_write(tmp_path, bad))


class TestValidation:
    def test_missing_required_field(self, tmp_path) -> None:
        bad = MINIMAL_CONFIG.replace('path = "E:/Backups"\n', "")
        with pytest.raises(ConfigError, match="target.path"):
            load_task_config(_write(tmp_path, bad))

    def test_invalid_filesystem_policy(self, tmp_path) -> None:
        bad = MINIMAL_CONFIG + "\n[target.extra]\n"
        # 换一个方式：直接改 policy 值
        bad = MINIMAL_CONFIG.replace(
            '[target]\npath = "E:/Backups"',
            '[target]\npath = "E:/Backups"\nfilesystem_policy = "ignore"',
        )
        with pytest.raises(ConfigError, match="filesystem_policy"):
            load_task_config(_write(tmp_path, bad))

    def test_non_positive_retention_rejected(self, tmp_path) -> None:
        bad = MINIMAL_CONFIG + "\n[retention]\nkeep_last = 0\n"
        with pytest.raises(ConfigError, match="keep_last"):
            load_task_config(_write(tmp_path, bad))

    def test_wrong_type_rejected(self, tmp_path) -> None:
        bad = MINIMAL_CONFIG + '\n[filter]\nexclude = "*.tmp"\n'
        with pytest.raises(ConfigError, match="exclude"):
            load_task_config(_write(tmp_path, bad))

    def test_invalid_toml_syntax(self, tmp_path) -> None:
        with pytest.raises(ConfigError):
            load_task_config(_write(tmp_path, "this is [not toml"))

    def test_missing_file(self, tmp_path) -> None:
        with pytest.raises(ConfigError, match="不存在"):
            load_task_config(tmp_path / "nope.toml")


class TestWrite:
    def test_write_then_load_roundtrip(self, tmp_path) -> None:
        cfg = TaskConfig(
            name="daily",
            source="D:/Data",
            target_path="E:/Backups",
            filesystem_policy="warn",
            exclude=("*.tmp",),
            keep_last=15,
            keep_monthly=3,
            verify_on_write=False,
        )
        out = write_task_config(cfg, tmp_path)
        assert out.name == "daily.toml"
        assert out.parent == tmp_path / "config.d"
        loaded = load_task_config(out)
        assert loaded == cfg

    def test_write_rejects_path_escape_name(self, tmp_path) -> None:
        # 写入边界自身校验：不依赖调用者提前 validate_task_name
        cfg = TaskConfig(name="../evil", source="D:/Data", target_path="E:/B")
        with pytest.raises(ConfigError, match="非法字符"):
            write_task_config(cfg, tmp_path)
        # config.d 内外零写入（连目录都不创建）
        assert not (tmp_path / "config.d").exists()
        assert list(tmp_path.iterdir()) == []

    def test_write_rejects_reserved_device_name(self, tmp_path) -> None:
        cfg = TaskConfig(name="CON", source="D:/Data", target_path="E:/B")
        with pytest.raises(ConfigError, match="保留设备名"):
            write_task_config(cfg, tmp_path)
        assert not (tmp_path / "config.d").exists()

    def test_write_normal_name_still_works(self, tmp_path) -> None:
        cfg = TaskConfig(name="daily-backup_01", source="D:/Data", target_path="E:/B")
        out = write_task_config(cfg, tmp_path)
        assert out.name == "daily-backup_01.toml"
        assert out.exists()
