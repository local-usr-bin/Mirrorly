"""Phase 2B application entry points; CLI compatibility stays in test_cli.py."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from mirrorly.application import repositories, tasks
from mirrorly.config import ConfigError, TaskConfig, write_task_config
from mirrorly.repo import init_repo


def test_application_usage_in_fresh_process_without_cli(tmp_path):
    repo = init_repo(tmp_path / "target")
    cfg = TaskConfig(name="example", source="relative-source", target_path=str(repo.path.parent))
    config_root = tmp_path / "config"
    write_task_config(cfg, config_root)
    script = dedent("""
        import json
        import sys
        from pathlib import Path

        assert 'mirrorly.cli' not in sys.modules

        class ForbidCli:
            def find_spec(self, fullname, path=None, target=None):
                if fullname == 'mirrorly.cli':
                    raise AssertionError('application must not import CLI')

        sys.meta_path.insert(0, ForbidCli())
        from mirrorly.application import locking, reports, repositories, tasks

        expected = Path(sys.argv[2]) / 'src' / 'mirrorly' / 'application'
        for module in (locking, reports, repositories, tasks):
            assert Path(module.__file__).resolve().parent == expected
        cfg = tasks.resolve_task_config(sys.argv[1])
        result = repositories.resolve_repo(cfg)
        assert result.relocated is False
        repo = result.repo
        with locking.TaskLock(repo, cfg.name), locking.RepoWriterLock(repo):
            assert (repo.path / 'locks' / 'example.lock').is_file()
            assert (repo.path / 'locks' / 'repo-writer' / 'active.lock').is_file()
            data = {'command': 'application-test', 'items': ['one', 'two']}
            report_path = reports.write_report(repo, 'application-test', data)
            assert json.loads(report_path.read_text(encoding='utf-8')) == data
            assert 'report_path' not in data
        assert not (repo.path / 'locks' / 'example.lock').exists()
        assert not (repo.path / 'locks' / 'repo-writer' / 'active.lock').exists()
        assert 'mirrorly.cli' not in sys.modules
    """)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-X",
            "utf8",
            "-c",
            script,
            str(config_root),
            str(Path(__file__).resolve().parents[1]),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
    assert len(list((repo.path / "logs").glob("application-test-*.json"))) == 1


@pytest.mark.parametrize("selection", [None, "", "lookup"])
def test_task_resolution_keeps_relative_paths_and_recorded_identity(
    tmp_path, monkeypatch, selection
):
    monkeypatch.chdir(tmp_path)
    cfg = TaskConfig(name="recorded", source="relative-source", target_path="relative-target")
    filename = write_task_config(cfg, "relative-config")
    filename.rename(filename.with_name("lookup.toml"))
    # Selection uses the filename; task identity still comes from TOML, not the selector.
    assert tasks.resolve_task_config("relative-config", selection) == cfg


@pytest.mark.parametrize(
    ("selection", "directory_exists", "error", "message"),
    [
        (None, False, ConfigError, "未找到任务配置目录"),
        (None, True, ConfigError, "未找到任何任务配置"),
        ("missing", True, ConfigError, "配置文件不存在"),
        ("../escape", False, tasks.TaskSelectionError, "--task 非法"),
    ],
)
def test_task_discovery_failure_categories(
    tmp_path, capsys, selection, directory_exists, error, message
):
    root = tmp_path / "config"
    if directory_exists:
        (root / "config.d").mkdir(parents=True)
    with pytest.raises(error, match=message):
        tasks.resolve_task_config(root, selection)
    assert capsys.readouterr() == ("", "")


def test_multiple_tasks_require_selection_and_preserve_sorted_diagnostic(tmp_path):
    for name in ("zulu", "alpha"):
        write_task_config(TaskConfig(name=name, source="src", target_path="target"), tmp_path)
    with pytest.raises(tasks.TaskSelectionError) as error:
        tasks.resolve_task_config(tmp_path)
    assert str(error.value) == "存在多个任务（alpha, zulu），必须用 --task 指定"
    assert tasks.resolve_task_config(tmp_path, "zulu").name == "zulu"


def test_cli_adapter_keeps_dot_mirrorly_relative_to_cwd(tmp_path, monkeypatch):
    from argparse import Namespace

    from mirrorly import cli

    monkeypatch.chdir(tmp_path)
    cfg = TaskConfig(name="local", source="relative-source", target_path="relative-target")
    write_task_config(cfg, Path(".mirrorly"))
    assert cli._resolve_task_config(Namespace()) == cfg
    assert cli._resolve_task_config(Namespace(config="", task="")) == cfg


@pytest.mark.parametrize("mode", ["legacy", "anchored", "relocated"])
def test_repository_result_separates_relocation_from_cli_notification(
    tmp_path, monkeypatch, capsys, mode
):
    from mirrorly import cli

    target = tmp_path / "target"
    repo = init_repo(target)
    guid = "\\\\?\\Volume{11111111-2222-3333-4444-555555555555}\\"
    anchored = mode != "legacy"
    cfg = TaskConfig(
        name="example",
        source="relative-source",
        target_path=str(tmp_path / "stale" if mode == "relocated" else target),
        volume_guid=guid if anchored else None,
        repo_id=repo.repo_id if anchored else None,
        repo_dir="target" if anchored else None,
    )
    config_root = tmp_path / "config"
    config_file = write_task_config(cfg, config_root)
    original_config = config_file.read_bytes()
    monkeypatch.setattr(repositories._volume, "get_volume_guid_for_path", lambda _: guid)
    monkeypatch.setattr(repositories._volume, "get_mount_roots", lambda _: [str(tmp_path)])

    result = repositories.resolve_repo(cfg)
    assert result.repo == repo
    assert result.relocated is (mode == "relocated")
    assert capsys.readouterr() == ("", "")

    # Even --quiet --json retains the existing relocation notice on stderr only.
    assert cli.main(["--config", str(config_root), "list", "--quiet", "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == []
    expected = (
        f"目标卷已重定位: {cfg.target_path} → {repo.path.parent}（卷标识匹配；配置未自动修改）\n"
        if mode == "relocated"
        else ""
    )
    assert captured.err == expected
    assert config_file.read_bytes() == original_config
    assert cfg.target_path == str(tmp_path / "stale" if mode == "relocated" else target)
