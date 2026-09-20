"""Phase 2C setup facts, read-only inspection, revalidation and CLI boundaries."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from mirrorly import cli
from mirrorly import repo as repo_mod
from mirrorly.application import setup
from mirrorly.config import TaskConfig, load_task_config
from mirrorly.repo import RepoError, VolumeInfo, load_repo


@pytest.fixture
def request_data(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    return setup.SetupRequest("documents", source, tmp_path / "target", tmp_path / "config")


def _tree(root):
    return {p.relative_to(root): p.read_bytes() if p.is_file() else None for p in root.rglob("*")}


def _exfat(monkeypatch):
    def query(_):
        return VolumeInfo("USB", "12345678", "exFAT")

    monkeypatch.setattr(setup, "get_volume_info", query)
    monkeypatch.setattr(repo_mod, "get_volume_info", query)


def test_preflight_is_read_only_and_returns_checked_volume_and_paths(
    request_data, tmp_path, monkeypatch, capsys
):
    before = _tree(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("preflight attempted mutation or interaction")

    with monkeypatch.context() as patch:
        patch.setattr(setup, "init_repo", forbidden)
        patch.setattr(setup, "write_task_config", forbidden)
        patch.setattr(Path, "mkdir", forbidden)
        patch.setattr(Path, "write_text", forbidden)
        patch.setattr(Path, "write_bytes", forbidden)
        patch.setattr("builtins.input", forbidden)
        result = setup.preflight_setup(request_data)

    assert result.problem is None
    assert result.inputs_valid is True
    assert result.repository_path == request_data.target / repo_mod.REPO_DIR_NAME
    assert result.config_path == request_data.config_root / "config.d" / "documents.toml"
    assert result.volume == repo_mod.get_volume_info(request_data.target)
    assert result.mount_root == setup._volume.get_volume_mount_root(str(request_data.target))
    assert result.repository_volume.guid == setup._volume.get_volume_guid_for_path(
        request_data.target
    )
    assert result.repository_volume.serial == result.volume.serial
    assert result.copy_mode_approval_required is False
    assert _tree(tmp_path) == before
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    ("case", "cause_type", "message"),
    [
        ("name", setup.SetupUsageError, "--task 非法"),
        ("missing_source", setup.SetupUsageError, "--source 不是已存在的目录"),
        ("file_source", setup.SetupUsageError, "--source 不是已存在的目录"),
        ("collision", RepoError, "任务配置已存在"),
        ("repo_inside_source", RepoError, "备份目标仓库"),
        ("source_inside_repo", RepoError, "源目录"),
        ("same_path", RepoError, "备份目标仓库"),
    ],
)
def test_preflight_input_failures_stop_without_writes(
    request_data, tmp_path, monkeypatch, case, cause_type, message
):
    request = request_data
    if case == "name":
        request = replace(request, task_name="../escape")
    elif case == "missing_source":
        request = replace(request, source=tmp_path / "missing")
    elif case == "file_source":
        source_file = tmp_path / "file.txt"
        source_file.write_text("source file", encoding="utf-8")
        request = replace(request, source=source_file)
    elif case == "collision":
        request.config_path.parent.mkdir(parents=True)
        request.config_path.write_bytes(b"existing configuration")
    elif case == "repo_inside_source":
        request = replace(request, target=request.source / "backups")
    elif case == "source_inside_repo":
        source = request.repository_path / "inside"
        source.mkdir(parents=True)
        request = replace(request, source=source)
    elif case == "same_path":
        request.repository_path.mkdir(parents=True)
        request = replace(request, source=request.repository_path)
    before = _tree(tmp_path)

    def no_volume_query(*args):
        pytest.fail("input failures must precede volume queries")

    monkeypatch.setattr(setup, "get_volume_info", no_volume_query)
    result = setup.preflight_setup(request)
    assert result.inputs_valid is False
    assert result.volume is None and result.mount_root is None
    assert result.problem.stage == "inputs"
    assert isinstance(result.problem.cause, cause_type)
    assert message in str(result.problem)
    assert result.problem.repository_initialized is False
    assert result.problem.config_written is False
    assert _tree(tmp_path) == before


@pytest.mark.parametrize("policy", ["strict", "warn"])
def test_preflight_filesystem_policy_and_approval(request_data, tmp_path, monkeypatch, policy):
    _exfat(monkeypatch)
    request = replace(request_data, filesystem_policy=policy)
    before = _tree(tmp_path)
    result = setup.preflight_setup(request)
    assert result.inputs_valid is True
    assert result.volume.filesystem == "exFAT"
    assert result.mount_root is not None
    assert result.copy_mode_approval_required is (policy == "warn")
    if policy == "strict":
        assert result.problem.stage == "repository_check"
        assert "strict 策略拒绝非 NTFS 目标" in str(result.problem)
        assert result.problem.repository_initialized is False
    else:
        assert result.problem is None
        assert result.repository_volume.guid is not None
    assert _tree(tmp_path) == before


@pytest.mark.parametrize("failure", ["mount", "guid", "existing_repo"])
def test_preflight_reports_existing_target_blockers(request_data, tmp_path, monkeypatch, failure):
    def unavailable(*args):
        raise setup._volume.VolumeError("injected volume failure")

    if failure == "mount":
        monkeypatch.setattr(setup._volume, "get_volume_mount_root", unavailable)
    elif failure == "guid":
        monkeypatch.setattr(setup._volume, "get_volume_guid_for_path", unavailable)
    else:
        repo_mod.init_repo(request_data.target)
    before = _tree(tmp_path)
    result = setup.preflight_setup(request_data)
    assert result.problem.stage == ("mount" if failure == "mount" else "repository_check")
    assert isinstance(result.problem.cause, RepoError)
    assert result.problem.repository_initialized is False  # inspection never ran init
    assert _tree(tmp_path) == before


def test_direct_setup_success_preserves_config_anchors_and_relative_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("source").mkdir()
    request = setup.SetupRequest("documents", "source", "target", "config")
    preview = setup.preflight_setup(request)
    assert preview.problem is None
    assert preview.repository_path == Path("target") / "MirrorlyRepo"
    result = setup.create_backup(request)
    expected_rel = os.path.relpath(str(Path("target").resolve()), preview.mount_root)
    expected = TaskConfig(
        name="documents",
        source=str(Path("source").resolve()),
        target_path=str(Path("target").resolve()),
        filesystem_policy="strict",
        volume_guid=result.repo.volume.guid,
        repo_id=result.repo.repo_id,
        repo_dir="." if expected_rel == "." else expected_rel.replace("/", "\\"),
    )
    assert result.task == expected
    assert load_task_config(result.config_path) == expected
    assert result.config_path == Path("config/config.d/documents.toml")
    assert result.repo == load_repo("target")
    assert result.repo.path == preview.repository_path
    assert {p.name for p in result.repo.path.iterdir()} == {
        *repo_mod.SUBDIRS,
        "repo.json",
        "lifecycle.json",
    }
    assert (result.repo.path / "snapshots").is_dir()
    assert list((result.repo.path / "snapshots").iterdir()) == []


@pytest.mark.parametrize("change", ["source", "config", "containment", "mount", "guid", "repo"])
def test_execution_rechecks_after_successful_preflight(request_data, monkeypatch, change):
    request = request_data
    assert setup.preflight_setup(request).problem is None
    if change == "source":
        request.source.rmdir()
    elif change == "config":
        request.config_path.parent.mkdir(parents=True)
        request.config_path.write_bytes(b"created after preview")
    elif change == "containment":
        # Simulate a changed realpath relationship (e.g. retargeted directory junction).
        monkeypatch.setattr(setup, "path_within", lambda *_: True)
    elif change in ("mount", "guid"):

        def unavailable(*args):
            raise setup._volume.VolumeError("volume query failed after preview")

        query = "get_volume_mount_root" if change == "mount" else "get_volume_guid_for_path"
        monkeypatch.setattr(setup._volume, query, unavailable)
    else:
        repo_mod.init_repo(request.target)
    with pytest.raises(setup.SetupFailure) as caught:
        setup.create_backup(request)
    failure = caught.value
    assert (
        failure.stage
        == {
            "source": "inputs",
            "config": "inputs",
            "containment": "inputs",
            "mount": "mount",
            "guid": "repository_init",
            "repo": "repository_init",
        }[change]
    )
    assert failure.config_written is False
    if change == "config":
        assert request.config_path.read_bytes() == b"created after preview"
    else:
        assert not request.config_path.exists()
    assert request.repository_path.exists() is (change == "repo")


def test_execution_rechecks_filesystem_and_requires_explicit_approval(request_data, monkeypatch):
    request = replace(request_data, filesystem_policy="warn")
    preview = setup.preflight_setup(request)
    assert preview.problem is None and preview.copy_mode_approval_required is False
    _exfat(monkeypatch)
    with pytest.raises(setup.CopyModeApprovalRequired) as caught:
        setup.create_backup(request)
    assert caught.value.volume.filesystem == "exFAT"
    assert not request.repository_path.exists() and not request.config_path.exists()
    result = setup.create_backup(request, copy_mode_approved=True)
    assert result.repo.hardlinks is False
    assert result.task.filesystem_policy == "warn"


def test_copy_approval_does_not_bypass_strict_policy(request_data, monkeypatch):
    assert setup.preflight_setup(request_data).problem is None
    _exfat(monkeypatch)
    with pytest.raises(setup.SetupFailure) as caught:
        setup.create_backup(request_data, copy_mode_approved=True)
    assert "strict 策略拒绝非 NTFS" in str(caught.value)
    assert not request_data.repository_path.exists()


@pytest.mark.parametrize("partial_write", [False, True])
def test_config_failure_preserves_initialized_repository_and_unknown_write_outcome(
    request_data, monkeypatch, partial_write
):
    cause = OSError("injected config persistence failure")

    def fail_config(cfg, config_root):
        assert load_repo(cfg.target_path).repo_id == cfg.repo_id
        if partial_write:
            request_data.config_path.parent.mkdir(parents=True)
            request_data.config_path.write_bytes(b"partial TOML")
        raise cause

    monkeypatch.setattr(setup, "write_task_config", fail_config)
    with pytest.raises(setup.SetupFailure) as caught:
        setup.create_backup(request_data)
    failure = caught.value
    assert failure.stage == "config_write" and failure.cause is cause
    assert failure.repository_initialized is True
    assert failure.config_written is None
    assert failure.repo == load_repo(request_data.target)
    assert failure.repository_path == request_data.repository_path
    assert failure.config_path == request_data.config_path
    assert (failure.repo.path / "repo.json").is_file()
    assert (failure.repo.path / "lifecycle.json").is_file()
    assert request_data.config_path.exists() is partial_write
    if partial_write:
        assert request_data.config_path.read_bytes() == b"partial TOML"


def test_init_failure_keeps_partial_core_artifacts_and_reports_unknown(request_data, monkeypatch):
    cause = OSError("repo.json publication failed")

    def fail_publication(path, data):
        assert path.name == "repo.json"
        assert (path.parent / "lifecycle.json").is_file()
        raise cause

    monkeypatch.setattr(repo_mod, "_write_json_atomic", fail_publication)
    with pytest.raises(setup.SetupFailure) as caught:
        setup.create_backup(request_data)
    failure = caught.value
    assert failure.stage == "repository_init" and failure.cause is cause
    assert failure.repo is None
    assert failure.repository_initialized is None
    assert failure.config_written is False
    assert request_data.repository_path.is_dir()
    assert (request_data.repository_path / "lifecycle.json").is_file()
    assert not (request_data.repository_path / "repo.json").exists()
    assert not request_data.config_path.exists()


def test_config_construction_failure_still_reports_acknowledged_repo(request_data, monkeypatch):
    def fail_construction(**kwargs):
        raise ValueError("injected config construction failure")

    monkeypatch.setattr(setup, "TaskConfig", fail_construction)
    with pytest.raises(setup.SetupFailure) as caught:
        setup.create_backup(request_data)
    assert caught.value.stage == "config_construct"
    assert caught.value.repository_initialized is True
    assert caught.value.config_written is False
    assert caught.value.repo == load_repo(request_data.target)


@pytest.mark.parametrize("yes", [False, True])
def test_cli_confirmation_precedes_later_preflight_blocker(request_data, monkeypatch, capsys, yes):
    _exfat(monkeypatch)
    mounts = []

    def fail_mount(*args):
        mounts.append(args)
        raise setup._volume.VolumeError("injected unavailable mount")

    monkeypatch.setattr(setup._volume, "get_volume_mount_root", fail_mount)
    request = replace(request_data, filesystem_policy="warn")
    preview = setup.preflight_setup(request)
    assert preview.copy_mode_approval_required is True
    assert preview.problem.stage == "mount"
    mounts.clear()
    code = cli.main(
        [
            "--config",
            str(request.config_root),
            "init",
            "--source",
            str(request.source),
            "--target",
            str(request.target),
            "--filesystem-policy",
            "warn",
            "--json",
            *(["--yes"] if yes else []),
        ]
    )
    assert code == (1 if yes else 6)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("目标文件系统为 exFAT") == 1
    assert len(mounts) == (1 if yes else 0)
    if yes:
        assert "无法解析目标卷挂载点（M10 卷锚所需）" in captured.err
    else:
        assert "--json 模式不进行交互，请显式提供 --yes" in captured.err
    assert not request.repository_path.exists() and not request.config_path.exists()


def test_cli_rechecks_inputs_after_user_confirmation(request_data, monkeypatch, capsys):
    _exfat(monkeypatch)

    def confirm(prompt):
        assert prompt == "是否仍以整文件复制模式继续？ [y/N] "
        request_data.source.rmdir()
        return "y"

    monkeypatch.setattr("builtins.input", confirm)
    code = cli.main(
        [
            "--config",
            str(request_data.config_root),
            "init",
            "--source",
            str(request_data.source),
            "--target",
            str(request_data.target),
            "--filesystem-policy",
            "warn",
        ]
    )
    assert code == 2
    assert "--source 不是已存在的目录" in capsys.readouterr().err
    assert not request_data.repository_path.exists()


def test_setup_executes_in_isolated_process_without_cli(request_data):
    script = dedent("""
        import json
        import sys
        from pathlib import Path
        assert 'mirrorly.cli' not in sys.modules
        class NoCli:
            def find_spec(self, fullname, path=None, target=None):
                if fullname == 'mirrorly.cli':
                    raise AssertionError('setup imported CLI')
        sys.meta_path.insert(0, NoCli())
        from mirrorly.application import setup
        assert Path(setup.__file__).resolve() == Path(sys.argv[4])
        request = setup.SetupRequest('isolated', sys.argv[1], sys.argv[2], sys.argv[3])
        preview = setup.preflight_setup(request)
        assert preview.problem is None
        assert not preview.repository_path.exists()
        result = setup.create_backup(request)
        assert result.repo.path == preview.repository_path
        assert result.config_path.is_file()
        assert 'mirrorly.cli' not in sys.modules
        print(json.dumps({'repo_id': result.repo.repo_id, 'source': result.task.source}))
    """)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-X",
            "utf8",
            "-c",
            script,
            str(request_data.source),
            str(request_data.target),
            str(request_data.config_root),
            str(Path(__file__).resolve().parents[1] / "src/mirrorly/application/setup.py"),
        ],
        cwd=request_data.source,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "repo_id": load_repo(request_data.target).repo_id,
        "source": str(request_data.source.resolve()),
    }
