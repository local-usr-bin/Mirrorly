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


# ---------------------------------------------------------------------------
# M10 卷锚（all-or-none + 格式校验）
# ---------------------------------------------------------------------------

_VALID_GUID = "\\\\?\\Volume{12345678-1234-1234-1234-123456789abc}\\"
_VALID_REPO_ID = "0123456789abcdef0123456789abcdef"  # uuid4().hex canonical 形式


def _anchored(
    *,
    volume_guid: str | None = _VALID_GUID,
    repo_id: str | None = _VALID_REPO_ID,
    repo_dir: str | None = "Backups",
) -> str:
    """生成 anchored 配置文本；参数传 None 表示省略该键（构造 partial 组合）。

    锚值统一用 TOML literal string（单引号）：反斜杠不转义，值原样进入解析结果。
    """
    lines: list[str] = []
    if volume_guid is not None:
        lines.append(f"volume_guid = '{volume_guid}'")
    if repo_id is not None:
        lines.append(f"repo_id = '{repo_id}'")
    if repo_dir is not None:
        lines.append(f"repo_dir = '{repo_dir}'")
    if not lines:
        return MINIMAL_CONFIG
    anchor = "\n".join(lines)
    return MINIMAL_CONFIG.replace('path = "E:/Backups"', f'path = "E:/Backups"\n{anchor}')


class TestAnchorValidation:
    def test_valid_anchor_config_loads(self, tmp_path) -> None:
        cfg = load_task_config(_write(tmp_path, _anchored()))
        assert cfg.volume_guid == _VALID_GUID
        assert cfg.repo_id == _VALID_REPO_ID
        assert cfg.repo_dir == "Backups"

    def test_legacy_config_has_no_anchor(self, tmp_path) -> None:
        cfg = load_task_config(_write(tmp_path, MINIMAL_CONFIG))
        assert cfg.volume_guid is None
        assert cfg.repo_id is None
        assert cfg.repo_dir is None

    # A. partial anchor → ConfigError（拒绝静默降级）
    def test_partial_anchor_guid_only(self, tmp_path) -> None:
        with pytest.raises(ConfigError, match="卷锚字段必须同时存在"):
            load_task_config(_write(tmp_path, _anchored(repo_id=None, repo_dir=None)))

    def test_partial_anchor_missing_repo_dir(self, tmp_path) -> None:
        with pytest.raises(ConfigError, match="缺 repo_dir"):
            load_task_config(_write(tmp_path, _anchored(repo_dir=None)))

    def test_partial_anchor_repo_id_only(self, tmp_path) -> None:
        with pytest.raises(ConfigError, match="缺 volume_guid, repo_dir"):
            load_task_config(_write(tmp_path, _anchored(volume_guid=None, repo_dir=None)))

    def test_partial_anchor_repo_dir_only(self, tmp_path) -> None:
        with pytest.raises(ConfigError, match="卷锚字段必须同时存在"):
            load_task_config(_write(tmp_path, _anchored(volume_guid=None, repo_id=None)))

    # B. 非法 volume_guid → ConfigError
    @pytest.mark.parametrize(
        "bad_guid",
        [
            "\\\\?\\Device\\HarddiskVolume3",  # 非 Volume GUID namespace
            "\\\\?\\Volume{12345678-1234-1234-1234-123456789abc}",  # 缺尾反斜杠
            "\\\\?\\Volume{not-a-guid}\\",  # 非 GUID 内容
            "\\\\\\\\?\\\\Volume{12345678-1234-1234-1234-123456789abc}\\\\",  # 双反斜杠
            "C:\\",  # 盘符路径
        ],
    )
    def test_invalid_volume_guid(self, tmp_path, bad_guid: str) -> None:
        with pytest.raises(ConfigError, match="volume_guid 非法"):
            load_task_config(_write(tmp_path, _anchored(volume_guid=bad_guid)))

    def test_invalid_repo_id(self, tmp_path) -> None:
        for bad in (
            "not-hex-at-all",
            "0123456789abcdef0123456789abcde",  # 31 位
            "G123456789abcdef0123456789abcdef",  # 非十六进制字符
        ):
            with pytest.raises(ConfigError, match="repo_id 非法"):
                load_task_config(_write(tmp_path, _anchored(repo_id=bad)))

    # C. repo_dir 路径逃逸 → ConfigError
    @pytest.mark.parametrize(
        "bad_dir",
        [
            "../x",  # 父目录逃逸
            "a/../b",
            "..",
            "C:/x",  # 盘符限定
            "C:\\x",
            "\\\\server\\share",  # UNC
            "/abs",  # 绝对路径
            "a//b",  # 空段
            "trailing.",  # 尾点段
            "bad<name",  # 非法字符
        ],
    )
    def test_repo_dir_escape_rejected(self, tmp_path, bad_dir: str) -> None:
        with pytest.raises(ConfigError, match="repo_dir"):
            load_task_config(_write(tmp_path, _anchored(repo_dir=bad_dir)))

    def test_repo_dir_volume_root_dot_allowed(self, tmp_path) -> None:
        cfg = load_task_config(_write(tmp_path, _anchored(repo_dir=".")))
        assert cfg.repo_dir == "."

    def test_repo_dir_slash_normalized_to_backslash(self, tmp_path) -> None:
        cfg = load_task_config(_write(tmp_path, _anchored(repo_dir="Backups/daily")))
        assert cfg.repo_dir == "Backups\\daily"


class TestAnchorWriteBoundary:
    def test_anchored_roundtrip_via_write(self, tmp_path) -> None:
        cfg = TaskConfig(
            name="daily",
            source="D:/Data",
            target_path="E:/Backups",
            volume_guid=_VALID_GUID,
            repo_id=_VALID_REPO_ID,
            repo_dir="Backups",
        )
        out = write_task_config(cfg, tmp_path)
        loaded = load_task_config(out)
        assert loaded == cfg

    def test_write_rejects_partial_anchor_zero_write(self, tmp_path) -> None:
        # 写入边界：library API 直接构造 partial 锚 → 拒绝且零写入
        cfg = TaskConfig(
            name="daily",
            source="D:/Data",
            target_path="E:/Backups",
            volume_guid=_VALID_GUID,
        )
        with pytest.raises(ConfigError, match="卷锚字段必须同时存在"):
            write_task_config(cfg, tmp_path)
        assert not (tmp_path / "config.d").exists()

    def test_write_rejects_bad_repo_dir_zero_write(self, tmp_path) -> None:
        cfg = TaskConfig(
            name="daily",
            source="D:/Data",
            target_path="E:/Backups",
            volume_guid=_VALID_GUID,
            repo_id=_VALID_REPO_ID,
            repo_dir="../escape",
        )
        with pytest.raises(ConfigError, match="repo_dir"):
            write_task_config(cfg, tmp_path)
        assert not (tmp_path / "config.d").exists()
