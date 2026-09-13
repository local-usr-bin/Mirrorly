"""T-09 CLI 集成测试（CLI_SPEC v1.0 / MVP_TASKS T-09）。

覆盖：五命令解析与执行、console entry point、help/参数错误、默认值、
确认 yes/no、--yes、退出码全映射（0/1/2/3/4/5/6/130）、backup 成功/partial、
verify 成功/失败、restore 计划展示/确认/执行/partial、list、repo/卷错误、
Unicode、long path、报告落盘、任务锁、incomplete 续传。

策略：用户交互与错误分支优先 mock（monkeypatch input / 底层函数），
另加真实临时仓库的端到端集成用例。所有测试通过 cli.main(argv) 驱动，
subprocess 仅用于 entry point 验证。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from mirrorly import cli
from mirrorly.cli import main
from mirrorly.config import ConfigError, validate_task_name
from mirrorly.manifest import list_manifests, load_manifest
from mirrorly.repo import VolumeInfo, load_repo
from mirrorly.scan import to_long_path

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _write(path: Path, data: bytes = b"x") -> None:
    Path(to_long_path(path.parent)).mkdir(parents=True, exist_ok=True)
    with open(to_long_path(path), "wb") as f:
        f.write(data)


def _fake_exfat(monkeypatch) -> None:
    """把 CLI 层与 repo 模块内部的卷信息查询都 mock 成 exFAT。"""
    fake = lambda p: VolumeInfo("USB", "DEADBEEF", "exFAT")  # noqa: E731
    monkeypatch.setattr(cli, "get_volume_info", fake)
    import mirrorly.repo as repo_mod

    monkeypatch.setattr(repo_mod, "get_volume_info", fake)


@pytest.fixture()
def ws(tmp_path):
    """工作区：src/（源）、target/（目标）、cfg/（配置根）。"""
    src = tmp_path / "src"
    src.mkdir()
    return {"src": src, "target": tmp_path / "target", "config": tmp_path / "cfg"}


def _init(ws, *extra: str) -> int:
    return main(
        [
            "--config",
            str(ws["config"]),
            "init",
            "--source",
            str(ws["src"]),
            "--target",
            str(ws["target"]),
            "--yes",
            *extra,
        ]
    )


def _run(ws, *argv: str) -> int:
    return main(["--config", str(ws["config"]), *argv])


@pytest.fixture()
def snap_ids(monkeypatch):
    """快照 id 序列（避免同秒 id 碰撞）。"""
    counter = {"n": 0}

    def next_id(now=None):
        counter["n"] += 1
        return f"2026-09-13_0000{counter['n']:02d}"

    monkeypatch.setattr(cli, "generate_snapshot_id", next_id)
    return counter


@pytest.fixture()
def backed_up(ws, snap_ids):
    """已 init 且完成一次备份（a.txt + sub/b.txt）的工作区。"""
    _write(ws["src"] / "a.txt", b"alpha")
    _write(ws["src"] / "sub" / "b.txt", b"beta")
    assert _init(ws) == 0
    assert _run(ws, "backup", "--yes") == 0
    return ws


def _repo(ws):
    return load_repo(ws["target"])


def _tamper_repo_json(ws, **changes) -> None:
    repo_json = ws["target"] / "MirrorlyRepo" / "repo.json"
    data = json.loads(repo_json.read_text(encoding="utf-8"))
    for key, value in changes.items():
        if key == "serial":
            data["volume"]["serial"] = value
        else:
            data[key] = value
    repo_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _answer(monkeypatch, text: str) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt="": text)


# ---------------------------------------------------------------------------
# 解析层：help / 参数错误 / entry point
# ---------------------------------------------------------------------------


class TestParsing:
    @pytest.mark.parametrize("cmd", ["init", "backup", "verify", "restore", "list"])
    def test_help_each_command(self, cmd, capsys) -> None:
        with pytest.raises(SystemExit) as exc:
            main([cmd, "--help"])
        assert exc.value.code == 0
        assert cmd in capsys.readouterr().out

    def test_top_help(self, capsys) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0
        assert "init" in capsys.readouterr().out

    def test_no_command_is_usage_error(self) -> None:
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code == 2

    def test_unknown_option_is_usage_error(self) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["backup", "--bogus"])
        assert exc.value.code == 2

    def test_init_missing_required_is_usage_error(self) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["init", "--source", "x"])
        assert exc.value.code == 2

    def test_restore_missing_to_is_usage_error(self) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["restore", "--snapshot", "s1"])
        assert exc.value.code == 2

    def test_illegal_overwrite_choice_is_usage_error(self) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["restore", "--to", "x", "--overwrite", "sometimes"])
        assert exc.value.code == 2

    def test_version(self, capsys) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0
        assert "mirrorly" in capsys.readouterr().out

    def test_global_options_after_subcommand(self, ws, backed_up, capsys) -> None:
        # 全局选项写在子命令之后同样生效（parents 解析）
        assert main(["list", "--config", str(ws["config"])]) == 0
        assert "2026-09-13" in capsys.readouterr().out


class TestEntryPoint:
    def test_python_dash_m_mirrorly_help(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "mirrorly", "--version"],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0
        assert "mirrorly" in proc.stdout

    def test_console_script(self) -> None:
        script = Path(sys.executable).parent / "Scripts" / "mirrorly.exe"
        if not script.exists():
            pytest.skip("console script 未安装（非 editable 环境）")
        proc = subprocess.run([str(script), "--version"], capture_output=True, text=True)
        assert proc.returncode == 0
        assert "mirrorly" in proc.stdout


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


class TestInit:
    def test_happy_path(self, ws, capsys) -> None:
        assert _init(ws) == 0
        out = capsys.readouterr().out
        assert "仓库已初始化" in out
        repo = _repo(ws)
        assert repo.path == ws["target"] / "MirrorlyRepo"
        cfg_file = ws["config"] / "config.d" / "default.toml"
        assert cfg_file.exists()
        text = cfg_file.read_text(encoding="utf-8")
        assert "source" in text and "path" in text

    def test_reinit_rejected_exit_1(self, ws) -> None:
        assert _init(ws) == 0
        assert _init(ws) == 1

    def test_source_not_dir_exit_2(self, ws) -> None:
        ws["src"].rmdir()
        assert _init(ws) == 2

    def test_json_output(self, ws, capsys) -> None:
        assert _init(ws, "--json") == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["hardlinks"] is True
        assert payload["filesystem"] == "NTFS"

    def test_strict_rejects_non_ntfs_exit_1(self, ws, monkeypatch) -> None:
        _fake_exfat(monkeypatch)
        assert _init(ws) == 1
        assert not (ws["target"] / "MirrorlyRepo").exists()

    def test_warn_non_ntfs_yes_proceeds(self, ws, monkeypatch, capsys) -> None:
        _fake_exfat(monkeypatch)
        assert _init(ws, "--filesystem-policy", "warn") == 0
        assert _repo(ws).hardlinks is False

    def test_warn_non_ntfs_decline_exit_6(self, ws, monkeypatch) -> None:
        _fake_exfat(monkeypatch)
        _answer(monkeypatch, "n")
        code = main(
            [
                "--config",
                str(ws["config"]),
                "init",
                "--source",
                str(ws["src"]),
                "--target",
                str(ws["target"]),
                "--filesystem-policy",
                "warn",
            ]
        )
        assert code == 6
        assert not (ws["target"] / "MirrorlyRepo").exists()

    def test_existing_task_config_rejected(self, ws) -> None:
        cfg_file = ws["config"] / "config.d" / "default.toml"
        cfg_file.parent.mkdir(parents=True)
        cfg_file.write_text("[task]\n", encoding="utf-8")
        assert _init(ws) == 1


# ---------------------------------------------------------------------------
# backup
# ---------------------------------------------------------------------------


class TestBackup:
    def test_first_backup_exit_0_and_report(self, ws, snap_ids, capsys) -> None:
        _write(ws["src"] / "a.txt", b"alpha")
        assert _init(ws) == 0
        assert _run(ws, "backup", "--yes") == 0
        out = capsys.readouterr().out
        assert "备份完成" in out
        repo = _repo(ws)
        manifests = list_manifests(repo)
        assert len(manifests) == 1 and manifests[0].status == "complete"
        # 报告落盘（M9）
        reports = list((repo.path / "logs").glob("backup-*.json"))
        assert len(reports) == 1
        payload = json.loads(reports[0].read_text(encoding="utf-8"))
        assert payload["status"] == "complete"
        assert payload["changes"]["added"] == ["a.txt"]
        assert payload["skipped"] == []
        # 快照内容一致
        assert (
            repo.path / "snapshots" / manifests[0].snapshot_id / "a.txt"
        ).read_bytes() == b"alpha"

    def test_second_backup_hardlink_reuse(self, backed_up, capsys) -> None:
        ws = backed_up
        assert _run(ws, "backup", "--yes") == 0
        capsys.readouterr()
        repo = _repo(ws)
        ids = [s.snapshot_id for s in list_manifests(repo)]
        assert len(ids) == 2
        f1 = repo.path / "snapshots" / ids[0] / "a.txt"
        f2 = repo.path / "snapshots" / ids[1] / "a.txt"
        assert os.stat(to_long_path(f1)).st_ino == os.stat(to_long_path(f2)).st_ino

    def test_dry_run_writes_nothing(self, backed_up) -> None:
        ws = backed_up
        _write(ws["src"] / "new.txt", b"new")
        repo = _repo(ws)
        before_snaps = sorted(os.listdir(to_long_path(repo.path / "snapshots")))
        before_logs = sorted(os.listdir(to_long_path(repo.path / "logs")))
        assert _run(ws, "backup", "--dry-run") == 0
        assert sorted(os.listdir(to_long_path(repo.path / "snapshots"))) == before_snaps
        assert sorted(os.listdir(to_long_path(repo.path / "logs"))) == before_logs
        assert sorted(os.listdir(to_long_path(repo.path / "locks"))) == []

    def test_dry_run_preview_output(self, backed_up, capsys) -> None:
        ws = backed_up
        _write(ws["src"] / "new.txt", b"new")
        assert _run(ws, "backup", "--dry-run") == 0
        out = capsys.readouterr().out
        assert "dry-run" in out and "新增 1" in out

    def test_partial_backup_exit_3(self, backed_up, monkeypatch) -> None:
        ws = backed_up
        _write(ws["src"] / "flaky.txt", b"data")
        orig = cli.write_snapshot

        def fake_write(*a, **kw):
            result = orig(*a, **kw)
            # 模拟 TR-4 变动中文件跳过（不改动实际写盘结果，仅构造报告态）
            from dataclasses import replace

            return replace(result, skipped=(("flaky.txt", "复制期间源文件发生变动，已跳过"),))

        monkeypatch.setattr(cli, "write_snapshot", fake_write)
        assert _run(ws, "backup", "--yes") == 3
        repo = _repo(ws)
        reports = sorted((repo.path / "logs").glob("backup-*.json"))
        payload = json.loads(reports[-1].read_text(encoding="utf-8"))
        assert payload["skipped"][0]["path"] == "flaky.txt"

    def test_full_hash_forces_rehash(self, backed_up, capsys) -> None:
        ws = backed_up
        # 内容未变的文件在 full-hash 模式下经哈希复核归入 suspected_modified
        assert _run(ws, "backup", "--yes", "--full-hash") == 0
        repo = _repo(ws)
        reports = sorted((repo.path / "logs").glob("backup-*.json"))
        payload = json.loads(reports[-1].read_text(encoding="utf-8"))
        assert payload["full_hash"] is True
        assert "a.txt" in payload["changes"]["suspected_modified"]

    def test_exclude_merges_with_config(self, ws, snap_ids) -> None:
        _write(ws["src"] / "keep.txt", b"k")
        _write(ws["src"] / "skip.tmp", b"s")
        assert _init(ws) == 0
        assert _run(ws, "backup", "--yes", "--exclude", "*.tmp") == 0
        repo = _repo(ws)
        sid = list_manifests(repo)[0].snapshot_id
        assert (repo.path / "snapshots" / sid / "keep.txt").exists()
        assert not (repo.path / "snapshots" / sid / "skip.tmp").exists()

    def test_retention_applied(self, backed_up) -> None:
        ws = backed_up
        # 配置 keep_last=1：再备份两次后最早快照被清理
        cfg_file = ws["config"] / "config.d" / "default.toml"
        text = cfg_file.read_text(encoding="utf-8").replace("keep_last = 30", "keep_last = 1")
        cfg_file.write_text(text, encoding="utf-8")
        assert _run(ws, "backup", "--yes") == 0
        assert _run(ws, "backup", "--yes") == 0
        ids = [s.snapshot_id for s in list_manifests(_repo(ws))]
        assert len(ids) == 1

    def test_lock_busy_exit_6(self, backed_up) -> None:
        ws = backed_up
        lock = _repo(ws).path / "locks" / "default.lock"
        lock.write_text("pid=999999", encoding="utf-8")
        assert _run(ws, "backup", "--yes") == 6

    def test_lock_released_after_success(self, backed_up) -> None:
        ws = backed_up
        assert _run(ws, "backup", "--yes") == 0
        assert sorted(os.listdir(to_long_path(_repo(ws).path / "locks"))) == []

    def test_keyboard_interrupt_exit_130_and_cleanup(self, backed_up, monkeypatch) -> None:
        ws = backed_up

        def boom(*a, **kw):
            raise KeyboardInterrupt

        monkeypatch.setattr(cli, "write_snapshot", boom)
        assert _run(ws, "backup", "--yes") == 130
        repo = _repo(ws)
        # 锁已释放；中断态 manifest 保留（可被下次续传），不是 complete
        assert sorted(os.listdir(to_long_path(repo.path / "locks"))) == []
        manifests = list_manifests(repo)
        assert any(m.status == "incomplete" for m in manifests)

    def test_volume_mismatch_exit_5(self, backed_up) -> None:
        _tamper_repo_json(backed_up, serial="00000000")
        assert _run(backed_up, "backup", "--yes") == 5

    def test_format_version_mismatch_exit_5(self, backed_up) -> None:
        _tamper_repo_json(backed_up, format_version=999)
        assert _run(backed_up, "backup", "--yes") == 5

    def test_missing_repo_exit_1(self, ws, snap_ids) -> None:
        import shutil

        assert _init(ws) == 0
        # 测试路径很短，用普通路径删除（带 \\?\ 前缀会绕过 OS 临时目录豁免）
        shutil.rmtree(ws["target"] / "MirrorlyRepo")
        assert _run(ws, "backup", "--yes") == 1

    def test_no_config_exit_1(self, tmp_path) -> None:
        assert main(["--config", str(tmp_path / "nope"), "backup", "--yes"]) == 1

    def test_multiple_tasks_require_task_flag_exit_2(self, ws, snap_ids) -> None:
        assert _init(ws) == 0
        second = ws["config"] / "config.d" / "second.toml"
        second.write_text(
            (ws["config"] / "config.d" / "default.toml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        assert _run(ws, "backup", "--yes") == 2
        assert _run(ws, "--task", "default", "backup", "--yes") == 0


class TestBackupResume:
    def _make_incomplete(self, ws) -> str:
        """构造中断态：manifest incomplete（a.txt 已物化，b.txt 未复制）。"""
        from mirrorly.manifest import create_manifest, write_manifest
        from mirrorly.scan import scan_source

        repo = _repo(ws)
        current = scan_source(ws["src"], ()).entries
        snap_dir = repo.path / "snapshots" / "2026-09-13_000000"
        snap_dir.mkdir(parents=True)
        (snap_dir / "a.txt").write_bytes((ws["src"] / "a.txt").read_bytes())
        (snap_dir / "sub").mkdir()
        write_manifest(
            repo, create_manifest("2026-09-13_000000", str(ws["src"]), repo.hash_algorithm, current)
        )
        return "2026-09-13_000000"

    def test_resume_with_yes(self, backed_up, snap_ids) -> None:
        ws = backed_up
        inc_id = self._make_incomplete(ws)
        assert _run(ws, "backup", "--yes") == 0
        repo = _repo(ws)
        manifests = list_manifests(repo)
        # 旧 incomplete 已善后，只剩原有 complete + 新 complete
        assert all(m.status == "complete" for m in manifests)
        assert inc_id not in {m.snapshot_id for m in manifests}
        reports = sorted((repo.path / "logs").glob("backup-*.json"))
        payload = json.loads(reports[-1].read_text(encoding="utf-8"))
        assert payload["resumed_from"] == inc_id

    def test_resume_declined_fresh_backup(self, backed_up, monkeypatch) -> None:
        ws = backed_up
        inc_id = self._make_incomplete(ws)
        _answer(monkeypatch, "n")
        assert _run(ws, "backup") == 0
        manifests = list_manifests(_repo(ws))
        # incomplete 保留不动，新备份从头完成
        assert inc_id in {m.snapshot_id for m in manifests}

    def test_resume_prompt_noninteractive_aborts_6(self, backed_up, monkeypatch) -> None:
        ws = backed_up
        self._make_incomplete(ws)

        def eof(prompt=""):
            raise EOFError

        monkeypatch.setattr("builtins.input", eof)
        assert _run(ws, "backup") == 6


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


class TestVerify:
    def test_verify_ok_exit_0(self, backed_up, capsys) -> None:
        assert _run(backed_up, "verify") == 0
        out = capsys.readouterr().out
        assert "完整" in out

    def test_verify_report_written(self, backed_up) -> None:
        assert _run(backed_up, "verify") == 0
        reports = list((_repo(backed_up).path / "logs").glob("verify-*.json"))
        assert len(reports) == 1
        payload = json.loads(reports[0].read_text(encoding="utf-8"))
        assert payload["ok"] is True

    def test_verify_tampered_exit_4(self, backed_up, capsys) -> None:
        ws = backed_up
        repo = _repo(ws)
        sid = list_manifests(repo)[0].snapshot_id
        target = repo.path / "snapshots" / sid / "a.txt"
        # 历史快照只读语义：测试直接篡改以模拟损坏
        target.write_bytes(b"corrupted!")
        assert _run(ws, "verify") == 4
        out = capsys.readouterr().out
        assert "a.txt" in out

    def test_verify_missing_file_exit_4(self, backed_up) -> None:
        ws = backed_up
        repo = _repo(ws)
        sid = list_manifests(repo)[0].snapshot_id
        (repo.path / "snapshots" / sid / "sub" / "b.txt").unlink()
        assert _run(ws, "verify") == 4

    def test_verify_quick_ok(self, backed_up) -> None:
        assert _run(backed_up, "verify", "--quick") == 0

    def test_verify_specific_snapshot(self, backed_up) -> None:
        sid = list_manifests(_repo(backed_up))[0].snapshot_id
        assert _run(backed_up, "verify", "--snapshot", sid) == 0

    def test_verify_all(self, backed_up) -> None:
        assert _run(backed_up, "backup", "--yes") == 0
        assert _run(backed_up, "verify", "--all") == 0

    def test_verify_no_snapshot_exit_1(self, ws, snap_ids) -> None:
        assert _init(ws) == 0
        assert _run(ws, "verify") == 1

    def test_verify_incomplete_rejected_exit_1(self, backed_up) -> None:
        ws = backed_up
        from mirrorly.manifest import create_manifest, write_manifest

        repo = _repo(ws)
        write_manifest(
            repo, create_manifest("2026-09-13_009999", str(ws["src"]), repo.hash_algorithm, {})
        )
        assert _run(ws, "verify", "--snapshot", "2026-09-13_009999") == 1

    def test_verify_json_output(self, backed_up, capsys) -> None:
        assert _run(backed_up, "verify", "--json") == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["snapshots"][0]["checked_files"] == 2


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


class TestRestore:
    def test_restore_to_empty_dir_exit_0(self, backed_up, capsys) -> None:
        ws = backed_up
        dest = ws["src"].parent / "out"
        assert _run(ws, "restore", "--to", str(dest), "--yes") == 0
        out = capsys.readouterr().out
        assert "恢复计划" in out and "恢复完成" in out
        assert (dest / "a.txt").read_bytes() == b"alpha"
        assert (dest / "sub" / "b.txt").read_bytes() == b"beta"

    def test_restore_plan_displayed_before_confirm(self, backed_up, monkeypatch, capsys) -> None:
        ws = backed_up
        dest = ws["src"].parent / "out"
        _write(dest / "a.txt", b"existing-newer")
        _answer(monkeypatch, "n")
        code = _run(ws, "restore", "--to", str(dest), "--overwrite", "always")
        out = capsys.readouterr().out
        assert code == 6  # 用户拒绝覆盖
        assert "覆盖 1" in out  # 确认前已展示覆盖清单
        assert (dest / "a.txt").read_bytes() == b"existing-newer"  # 未被触碰

    def test_restore_overwrite_confirmed(self, backed_up, monkeypatch) -> None:
        ws = backed_up
        dest = ws["src"].parent / "out"
        _write(dest / "a.txt", b"existing")
        _answer(monkeypatch, "y")
        assert _run(ws, "restore", "--to", str(dest), "--overwrite", "always") == 0
        assert (dest / "a.txt").read_bytes() == b"alpha"

    def test_restore_never_skips_existing_exit_3(self, backed_up) -> None:
        ws = backed_up
        dest = ws["src"].parent / "out"
        _write(dest / "a.txt", b"existing")
        assert _run(ws, "restore", "--to", str(dest), "--yes") == 3
        assert (dest / "a.txt").read_bytes() == b"existing"
        assert (dest / "sub" / "b.txt").read_bytes() == b"beta"  # 其余照常恢复

    def test_restore_path_literal_selector(self, backed_up) -> None:
        ws = backed_up
        dest = ws["src"].parent / "out"
        assert _run(ws, "restore", "--to", str(dest), "--path", "sub", "--yes") == 0
        assert (dest / "sub" / "b.txt").exists()
        assert not (dest / "a.txt").exists()

    def test_restore_path_windows_separator_normalized(self, backed_up) -> None:
        ws = backed_up
        dest = ws["src"].parent / "out"
        assert _run(ws, "restore", "--to", str(dest), "--path", "sub\\b.txt", "--yes") == 0
        assert (dest / "sub" / "b.txt").exists()

    def test_restore_path_no_match_exit_1(self, backed_up) -> None:
        assert (
            _run(
                backed_up,
                "restore",
                "--to",
                str(backed_up["src"].parent / "o"),
                "--path",
                "nope",
                "--yes",
            )
            == 1
        )

    def test_restore_to_source_without_in_place_exit_1(self, backed_up) -> None:
        ws = backed_up
        assert _run(ws, "restore", "--to", str(ws["src"]), "--yes") == 1

    def test_in_place_requires_yes_exit_2(self, backed_up) -> None:
        ws = backed_up
        code = _run(ws, "restore", "--to", str(ws["src"]), "--in-place")
        assert code == 2

    def test_in_place_with_yes(self, backed_up) -> None:
        ws = backed_up
        (ws["src"] / "a.txt").unlink()
        # a.txt 恢复成功；sub/b.txt 已存在且策略 never 被跳过 → 部分完成退出码 3
        assert _run(ws, "restore", "--to", str(ws["src"]), "--in-place", "--yes") == 3
        assert (ws["src"] / "a.txt").read_bytes() == b"alpha"
        assert (ws["src"] / "sub" / "b.txt").read_bytes() == b"beta"

    def test_restore_default_latest_snapshot(self, backed_up) -> None:
        ws = backed_up
        _write(ws["src"] / "c.txt", b"gamma")
        assert _run(ws, "backup", "--yes") == 0
        dest = ws["src"].parent / "out"
        assert _run(ws, "restore", "--to", str(dest), "--yes") == 0
        assert (dest / "c.txt").read_bytes() == b"gamma"

    def test_restore_into_repo_rejected(self, backed_up) -> None:
        ws = backed_up
        inside = _repo(ws).path / "snapshots"
        assert _run(ws, "restore", "--to", str(inside), "--yes") == 1

    def test_restore_no_snapshot_exit_1(self, ws, snap_ids) -> None:
        assert _init(ws) == 0
        assert _run(ws, "restore", "--to", str(ws["src"].parent / "out"), "--yes") == 1

    def test_restore_json_output(self, backed_up, capsys) -> None:
        dest = backed_up["src"].parent / "out"
        assert _run(backed_up, "restore", "--to", str(dest), "--yes", "--json") == 0
        payload = json.loads(capsys.readouterr().out)
        assert sorted(payload["restored"]) == ["a.txt", "sub/b.txt"]


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestList:
    def test_list_empty_repo(self, ws, snap_ids, capsys) -> None:
        assert _init(ws) == 0
        assert _run(ws, "list") == 0
        assert "还没有任何快照" in capsys.readouterr().out

    def test_list_shows_snapshots(self, backed_up, capsys) -> None:
        assert _run(backed_up, "list") == 0
        out = capsys.readouterr().out
        assert "2026-09-13_000001" in out and "complete" in out

    def test_list_marks_incomplete(self, backed_up, capsys) -> None:
        ws = backed_up
        from mirrorly.manifest import create_manifest, write_manifest

        repo = _repo(ws)
        write_manifest(
            repo, create_manifest("2026-09-13_009998", str(ws["src"]), repo.hash_algorithm, {})
        )
        assert _run(ws, "list") == 0
        out = capsys.readouterr().out
        assert "incomplete" in out and "非完整备份" in out

    def test_list_verbose_stats(self, backed_up, capsys) -> None:
        assert _run(backed_up, "list", "--verbose") == 0
        out = capsys.readouterr().out
        assert "文件 2" in out

    def test_list_json(self, backed_up, capsys) -> None:
        assert _run(backed_up, "list", "--json") == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload[0]["snapshot_id"] == "2026-09-13_000001"
        assert payload[0]["stats"]["files"] == 2


# ---------------------------------------------------------------------------
# Unicode / long path / 其他
# ---------------------------------------------------------------------------


class TestPaths:
    def test_unicode_filenames_roundtrip(self, ws, snap_ids) -> None:
        _write(ws["src"] / "中文目录" / "数据文件.txt", "中文内容".encode())
        _write(ws["src"] / "emoji-😀.txt", b"e")
        assert _init(ws) == 0
        assert _run(ws, "backup", "--yes") == 0
        dest = ws["src"].parent / "恢复输出"
        assert _run(ws, "restore", "--to", str(dest), "--yes") == 0
        assert (dest / "中文目录" / "数据文件.txt").read_bytes() == "中文内容".encode()
        assert (dest / "emoji-😀.txt").read_bytes() == b"e"

    @pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows 长路径场景")
    def test_long_path_backup(self, ws, snap_ids) -> None:
        deep = ws["src"]
        while len(str(deep / "long.txt")) <= 260:
            deep = deep / ("d" * 40)
        _write(deep / "long.txt", b"long")
        assert _init(ws) == 0
        assert _run(ws, "backup", "--yes") == 0
        repo = _repo(ws)
        sid = list_manifests(repo)[0].snapshot_id
        rel = (deep / "long.txt").relative_to(ws["src"])
        assert Path(to_long_path(repo.path / "snapshots" / sid / rel)).exists()
        assert _run(ws, "verify") == 0

    def test_quiet_suppresses_info(self, backed_up, capsys) -> None:
        assert _run(backed_up, "backup", "--yes", "--quiet") == 0
        assert capsys.readouterr().out == ""

    def test_cli_does_not_weaken_restore_safety(self, backed_up) -> None:
        # CLI 只是编排：stale plan 等底层安全语义不变——伪造计划仍被底层拒绝
        from dataclasses import replace

        from mirrorly.restore import RestoreError, apply_restore, plan_restore

        ws = backed_up
        repo = _repo(ws)
        sid = list_manifests(repo)[0].snapshot_id
        dest = ws["src"].parent / "out"
        _write(dest / "a.txt", b"user-data")
        plan = plan_restore(repo, sid, dest, overwrite="never")
        forged = replace(plan, overwrite="bogus")
        with pytest.raises(RestoreError):
            apply_restore(repo, forged)


# ---------------------------------------------------------------------------
# T-09 integration hardening（follow-up）
# ---------------------------------------------------------------------------


def _make_incomplete_snapshot(ws, snap_id: str = "2026-09-13_000000") -> str:
    """构造中断态：incomplete manifest（a.txt 已物化，b.txt 未复制）。"""
    from mirrorly.manifest import create_manifest, write_manifest
    from mirrorly.scan import scan_source

    repo = _repo(ws)
    current = scan_source(str(ws["src"]), ()).entries
    snap_dir = repo.path / "snapshots" / snap_id
    snap_dir.mkdir(parents=True)
    (snap_dir / "a.txt").write_bytes((ws["src"] / "a.txt").read_bytes())
    (snap_dir / "sub").mkdir()
    write_manifest(repo, create_manifest(snap_id, str(ws["src"]), repo.hash_algorithm, current))
    return snap_id


class TestSnapshotIdCollision:
    def test_collision_gets_suffix_and_preserves_existing(self, ws, monkeypatch) -> None:
        _write(ws["src"] / "a.txt", b"alpha")
        assert _init(ws) == 0
        # 强制 id 生成器连续返回同一个 id（模拟秒级碰撞）
        monkeypatch.setattr(cli, "generate_snapshot_id", lambda now=None: "2026-09-13_100000")
        assert _run(ws, "backup", "--yes") == 0
        repo = _repo(ws)
        first_manifest = repo.path / "manifests" / "2026-09-13_100000.json"
        first_manifest_bytes = first_manifest.read_bytes()
        first_snap_file = repo.path / "snapshots" / "2026-09-13_100000" / "a.txt"
        first_snap_bytes = first_snap_file.read_bytes()

        # 第二次备份强制撞到同一个已有 complete id
        _write(ws["src"] / "a.txt", b"alpha-v2")
        assert _run(ws, "backup", "--yes") == 0

        # 既有快照零污染：manifest 字节、目录内容、complete 状态全部不变
        assert first_manifest.read_bytes() == first_manifest_bytes
        assert first_snap_file.read_bytes() == first_snap_bytes
        assert load_manifest(repo, "2026-09-13_100000").status == "complete"

        # 新备份获得唯一后缀 id 并正常完成
        ids = [s.snapshot_id for s in list_manifests(repo)]
        assert "2026-09-13_100000-01" in ids
        assert load_manifest(repo, "2026-09-13_100000-01").status == "complete"
        assert (
            repo.path / "snapshots" / "2026-09-13_100000-01" / "a.txt"
        ).read_bytes() == b"alpha-v2"

    def test_collision_chain_skips_taken_suffixes(self, ws, monkeypatch) -> None:
        _write(ws["src"] / "a.txt", b"alpha")
        assert _init(ws) == 0
        monkeypatch.setattr(cli, "generate_snapshot_id", lambda now=None: "2026-09-13_100000")
        assert _run(ws, "backup", "--yes") == 0
        assert _run(ws, "backup", "--yes") == 0
        assert _run(ws, "backup", "--yes") == 0
        ids = {s.snapshot_id for s in list_manifests(_repo(ws))}
        assert ids == {
            "2026-09-13_100000",
            "2026-09-13_100000-01",
            "2026-09-13_100000-02",
        }

    def test_suffixed_id_accepted_by_verify_and_restore(self, ws, monkeypatch) -> None:
        _write(ws["src"] / "a.txt", b"alpha")
        assert _init(ws) == 0
        monkeypatch.setattr(cli, "generate_snapshot_id", lambda now=None: "2026-09-13_100000")
        assert _run(ws, "backup", "--yes") == 0
        assert _run(ws, "backup", "--yes") == 0
        suffixed = "2026-09-13_100000-01"
        # 后缀 id 必须兼容 verify / restore 的 snapshot-id 校验
        assert _run(ws, "verify", "--snapshot", suffixed) == 0
        dest = ws["src"].parent / "out"
        assert _run(ws, "restore", "--snapshot", suffixed, "--to", str(dest), "--yes") == 0
        assert (dest / "a.txt").read_bytes() == b"alpha"

    def test_unallocatable_id_fails_with_zero_writes(self, ws, monkeypatch) -> None:
        _write(ws["src"] / "a.txt", b"alpha")
        assert _init(ws) == 0
        repo = _repo(ws)
        # 占满基准 id 与全部 99 个后缀候选（complete manifest，内容合法）
        from mirrorly.manifest import create_manifest, mark_complete, write_manifest

        for n in range(100):
            sid = "2026-09-13_100000" if n == 0 else f"2026-09-13_100000-{n:02d}"
            write_manifest(
                repo,
                mark_complete(create_manifest(sid, str(ws["src"]), repo.hash_algorithm, {})),
            )
        before = sorted(os.listdir(to_long_path(repo.path / "manifests")))
        monkeypatch.setattr(cli, "generate_snapshot_id", lambda now=None: "2026-09-13_100000")
        assert _run(ws, "backup", "--yes") == 1  # 安全失败
        # 零写入：manifest 集合不变、没有新快照目录
        assert sorted(os.listdir(to_long_path(repo.path / "manifests"))) == before
        assert sorted(os.listdir(to_long_path(repo.path / "snapshots"))) == []


class TestBackupLockScope:
    def test_lock_busy_before_any_scan(self, backed_up, monkeypatch) -> None:
        ws = backed_up
        lock = _repo(ws).path / "locks" / "default.lock"
        lock.write_text("pid=999999", encoding="utf-8")

        def forbidden(*a, **k):
            raise AssertionError("锁被占用时不应触发 recovery/扫描")

        monkeypatch.setattr(cli, "scan_recovery", forbidden)
        monkeypatch.setattr(cli, "scan_source", forbidden)
        assert _run(ws, "backup", "--yes") == 6

    def test_dry_run_does_not_need_lock(self, backed_up) -> None:
        # dry-run 有意例外：零写入，不取锁也不触碰他人锁文件
        ws = backed_up
        lock = _repo(ws).path / "locks" / "default.lock"
        lock.write_text("pid=999999", encoding="utf-8")
        assert _run(ws, "backup", "--dry-run") == 0
        assert lock.read_text(encoding="utf-8") == "pid=999999"
        assert len(list_manifests(_repo(ws))) == 1


class TestJsonPurity:
    def _init_argv(self, ws, *extra: str) -> list[str]:
        return [
            "--config",
            str(ws["config"]),
            "init",
            "--source",
            str(ws["src"]),
            "--target",
            str(ws["target"]),
            *extra,
        ]

    def test_init_warn_json_yes(self, ws, monkeypatch, capsys) -> None:
        _fake_exfat(monkeypatch)
        code = main(self._init_argv(ws, "--filesystem-policy", "warn", "--yes", "--json"))
        assert code == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)  # stdout 为单一 JSON 文档
        assert payload["hardlinks"] is False
        assert "exFAT" in captured.err  # 降级警告走 stderr

    def test_init_warn_json_no_yes_exit_6_empty_stdout(self, ws, monkeypatch, capsys) -> None:
        _fake_exfat(monkeypatch)
        code = main(self._init_argv(ws, "--filesystem-policy", "warn", "--json"))
        assert code == 6
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err  # 诊断写 stderr
        assert not (ws["target"] / "MirrorlyRepo").exists()

    def test_backup_resume_json_yes(self, backed_up, monkeypatch, capsys) -> None:
        ws = backed_up
        inc_id = _make_incomplete_snapshot(ws)
        # 本测试聚焦 stdout JSON 纯度，不依赖旧 incomplete 的物理删除
        # （续传善后的端到端集成由 TestBackupResume 专门覆盖）
        monkeypatch.setattr("mirrorly.recovery.discard_incomplete", lambda *a, **k: None)
        assert _run(ws, "backup", "--yes", "--json") == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["resumed_from"] == inc_id

    def test_backup_resume_json_no_yes_exit_6_empty_stdout(self, backed_up, capsys) -> None:
        ws = backed_up
        _make_incomplete_snapshot(ws)
        assert _run(ws, "backup", "--json") == 6
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err

    def test_backup_dry_run_json(self, backed_up, capsys) -> None:
        ws = backed_up
        _write(ws["src"] / "new.txt", b"n")
        assert _run(ws, "backup", "--dry-run", "--json") == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["dry_run"] is True
        assert "new.txt" in payload["changes"]["added"]
        assert len(list_manifests(_repo(ws))) == 1  # 零写入

    def test_restore_overwrite_json_yes(self, backed_up, capsys) -> None:
        ws = backed_up
        dest = ws["src"].parent / "out"
        _write(dest / "a.txt", b"existing")
        code = _run(ws, "restore", "--to", str(dest), "--overwrite", "always", "--yes", "--json")
        assert code == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert "a.txt" in payload["restored"]
        assert "恢复计划" in captured.err  # 计划展示转 stderr，stdout 纯净

    def test_restore_overwrite_json_no_yes_exit_6_empty_stdout(self, backed_up, capsys) -> None:
        ws = backed_up
        dest = ws["src"].parent / "out"
        _write(dest / "a.txt", b"existing")
        code = _run(ws, "restore", "--to", str(dest), "--overwrite", "always", "--json")
        assert code == 6
        captured = capsys.readouterr()
        assert captured.out == ""
        assert (dest / "a.txt").read_bytes() == b"existing"  # 未授权即不动作

    def test_verify_failed_json_single_doc(self, backed_up, capsys) -> None:
        ws = backed_up
        repo = _repo(ws)
        sid = list_manifests(repo)[0].snapshot_id
        (repo.path / "snapshots" / sid / "a.txt").write_bytes(b"tampered")
        assert _run(ws, "verify", "--json") == 4
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False

    def test_partial_backup_json_single_doc(self, backed_up, monkeypatch, capsys) -> None:
        ws = backed_up
        _write(ws["src"] / "flaky.txt", b"data")
        orig = cli.write_snapshot

        def fake_write(*a, **kw):
            result = orig(*a, **kw)
            from dataclasses import replace

            return replace(result, skipped=(("flaky.txt", "复制期间源文件发生变动，已跳过"),))

        monkeypatch.setattr(cli, "write_snapshot", fake_write)
        assert _run(ws, "backup", "--yes", "--json") == 3
        payload = json.loads(capsys.readouterr().out)
        assert payload["skipped"][0]["path"] == "flaky.txt"


class TestInitPreflight:
    def test_target_inside_source_rejected_zero_writes(self, ws) -> None:
        code = main(
            [
                "--config",
                str(ws["config"]),
                "init",
                "--source",
                str(ws["src"]),
                "--target",
                str(ws["src"] / "backup-target"),
                "--yes",
            ]
        )
        assert code == 1
        assert not (ws["src"] / "backup-target").exists()

    def test_source_inside_prospective_repo_rejected(self, ws) -> None:
        src = ws["target"] / "MirrorlyRepo" / "data"
        src.mkdir(parents=True)
        code = main(
            [
                "--config",
                str(ws["config"]),
                "init",
                "--source",
                str(src),
                "--target",
                str(ws["target"]),
                "--yes",
            ]
        )
        assert code == 1
        assert not (ws["target"] / "MirrorlyRepo" / "repo.json").exists()

    def test_sibling_source_target_unaffected(self, ws) -> None:
        # 正常 sibling 布局不受互相包含检查影响（所有 happy-path 用例亦覆盖）
        assert _init(ws) == 0
        assert (ws["target"] / "MirrorlyRepo" / "repo.json").exists()


class TestTaskNameValidation:
    @pytest.mark.parametrize(
        "bad",
        [
            "../evil",
            "a/b",
            "a\\b",
            "a:b",
            "..",
            ".",
            "CON",
            "con.txt",
            "lpt1",
            "com²",
            "LPT³",
            "name.",
            "name ",
            "a<b",
            "a|b",
            "",
        ],
    )
    def test_dangerous_names_rejected(self, bad: str) -> None:
        with pytest.raises(ConfigError):
            validate_task_name(bad)

    @pytest.mark.parametrize("good", ["default", "my-task_1", "工作盘", "a.b", "A1"])
    def test_normal_names_accepted(self, good: str) -> None:
        validate_task_name(good)

    def test_hand_edited_toml_evil_name_rejected_before_lock(self, backed_up) -> None:
        ws = backed_up
        cfg_file = ws["config"] / "config.d" / "default.toml"
        text = cfg_file.read_text(encoding="utf-8").replace('name = "default"', 'name = "../evil"')
        assert 'name = "../evil"' in text
        cfg_file.write_text(text, encoding="utf-8")
        assert _run(ws, "backup", "--yes") == 1
        repo = _repo(ws)
        # 锁路径不得逃出 locks/（仓库根/父目录均不得出现 evil.lock）
        assert not (repo.path / "evil.lock").exists()
        assert not (repo.path.parent / "evil.lock").exists()

    def test_cli_task_arg_path_escape_rejected_exit_2(self, backed_up) -> None:
        assert _run(backed_up, "--task", "../evil", "backup", "--yes") == 2

    def test_init_evil_task_name_exit_2_zero_writes(self, ws) -> None:
        code = main(
            [
                "--config",
                str(ws["config"]),
                "init",
                "--source",
                str(ws["src"]),
                "--target",
                str(ws["target"]),
                "--task",
                "../evil",
                "--yes",
            ]
        )
        assert code == 2
        assert not (ws["target"] / "MirrorlyRepo").exists()
        assert not (ws["config"] / "config.d" / "evil.toml").exists()
        assert not (ws["config"] / "evil.toml").exists()


class TestCliContractGaps:
    def test_verify_snapshot_and_all_mutually_exclusive(self, backed_up) -> None:
        with pytest.raises(SystemExit) as exc:
            _run(backed_up, "verify", "--snapshot", "x", "--all")
        assert exc.value.code == 2

    def test_verify_reports_never_overwrite(self, backed_up, monkeypatch) -> None:
        ws = backed_up

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 9, 13, 13, 0, 0)

        monkeypatch.setattr(cli, "datetime", FrozenDatetime)
        assert _run(ws, "verify") == 0
        assert _run(ws, "verify") == 0
        reports = sorted((_repo(ws).path / "logs").glob("verify-*.json"))
        # 同时间戳也不覆盖：第二份报告获得唯一后缀
        assert len(reports) == 2
        assert reports[0].name != reports[1].name
        for r in reports:
            assert json.loads(r.read_text(encoding="utf-8"))["command"] == "verify"
