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
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from mirrorly import cli
from mirrorly import manifest as manifest_mod
from mirrorly import snapshot as snapshot_mod
from mirrorly.cli import main
from mirrorly.config import (
    ConfigError,
    TaskConfig,
    load_task_config,
    validate_task_name,
    write_task_config,
)
from mirrorly.hashing import hash_file
from mirrorly.manifest import (
    create_manifest,
    list_manifests,
    load_manifest,
    mark_complete,
    write_manifest,
)
from mirrorly.repo import RepoError, VolumeInfo, init_repo, load_repo
from mirrorly.retention import build_retention_plan
from mirrorly.scan import detect_changes, scan_source, to_long_path
from mirrorly.snapshot import SNAPSHOT_ORDINAL_MAX, SnapshotError, write_snapshot
from mirrorly.verify import verify_snapshot

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

    def next_id(now=None, *, ordinal=0):
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


def _freeze_snapshot_clock(monkeypatch, when: datetime, *, manifests: bool = False) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return when.replace(tzinfo=None)
            aware = when if when.tzinfo is not None else when.replace(tzinfo=UTC)
            return aware.astimezone(tz)

    monkeypatch.setattr(cli, "datetime", FrozenDateTime)
    if manifests:
        monkeypatch.setattr(manifest_mod, "datetime", FrozenDateTime)


def _freeze_snapshot_uuids(monkeypatch, values: list[str]) -> None:
    sequence = iter(UUID(hex=value) for value in values)
    monkeypatch.setattr(snapshot_mod.uuid, "uuid4", lambda: next(sequence))


def _assert_repo_writer_idle(repo) -> None:
    namespace = repo.path / "locks" / "repo-writer"
    assert namespace.is_dir()
    assert list(namespace.iterdir()) == []
    assert not list((repo.path / "locks").glob("*.lock"))


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
        # task config 存在性检查必须先于 init_repo：失败时零仓库写入
        cfg_file = ws["config"] / "config.d" / "default.toml"
        cfg_file.parent.mkdir(parents=True)
        cfg_file.write_bytes(b"[task]\n")
        before = cfg_file.read_bytes()
        assert _init(ws) == 1
        assert not (ws["target"] / "MirrorlyRepo").exists()
        # target 树下不出现任何 repo.json
        assert list(ws["target"].rglob("repo.json")) == []
        # 原 task config 字节保持不变
        assert cfg_file.read_bytes() == before


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

    def test_staging_metadata_failure_keeps_snapshot_incomplete(
        self, ws, snap_ids, monkeypatch
    ) -> None:
        _write(ws["src"] / "a.txt", b"alpha")
        assert _init(ws) == 0
        real_utime = snapshot_mod.os.utime

        def fail_staging_mtime(path, *args, **kwargs):
            if str(path).endswith("a.txt.mrtmp"):
                raise OSError(28, "simulated staging metadata failure")
            return real_utime(path, *args, **kwargs)

        monkeypatch.setattr(snapshot_mod.os, "utime", fail_staging_mtime)
        assert _run(ws, "backup", "--yes") == 1

        repo = _repo(ws)
        manifests = list_manifests(repo)
        assert len(manifests) == 1 and manifests[0].status == "incomplete"
        snapshot = repo.path / "snapshots" / manifests[0].snapshot_id
        assert not (snapshot / "a.txt").exists()
        assert not (snapshot / "a.txt.mrtmp").exists()
        assert list((repo.path / "logs").glob("backup-*.json")) == []

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
        before_locks = sorted(os.listdir(to_long_path(repo.path / "locks")))
        assert _run(ws, "backup", "--dry-run") == 0
        assert sorted(os.listdir(to_long_path(repo.path / "snapshots"))) == before_snaps
        assert sorted(os.listdir(to_long_path(repo.path / "logs"))) == before_logs
        assert sorted(os.listdir(to_long_path(repo.path / "locks"))) == before_locks

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
        _assert_repo_writer_idle(_repo(ws))

    def test_keyboard_interrupt_exit_130_and_cleanup(self, backed_up, monkeypatch) -> None:
        ws = backed_up

        def boom(*a, **kw):
            raise KeyboardInterrupt

        monkeypatch.setattr(cli, "write_snapshot", boom)
        assert _run(ws, "backup", "--yes") == 130
        repo = _repo(ws)
        # 锁已释放；中断态 manifest 保留（可被下次续传），不是 complete
        _assert_repo_writer_idle(repo)
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
    def test_collision_mints_fresh_uuid_and_preserves_existing(self, ws, monkeypatch) -> None:
        _write(ws["src"] / "a.txt", b"alpha")
        assert _init(ws) == 0
        repo = _repo(ws)
        prefix = "2026-09-13_100000"
        _freeze_snapshot_clock(monkeypatch, datetime(2026, 9, 13, 10, 0, 0))
        first_uuid = UUID("11111111-1111-4111-8111-111111111111")
        collision_uuid = UUID("22222222-2222-4222-8222-222222222222")
        fresh_uuid = UUID("33333333-3333-4333-8333-333333333333")
        uuids = iter((first_uuid, collision_uuid, fresh_uuid))
        collision = f"{prefix}-u000001-{collision_uuid.hex}"

        def next_uuid():
            value = next(uuids)
            if value == collision_uuid:
                # high-water scan 后、完整 candidate 检查前出现外部 artifact，
                # 稳定复现 full-id collision；retry 必须留在同一 ordinal。
                (repo.path / "snapshots" / collision).mkdir()
            return value

        monkeypatch.setattr(snapshot_mod.uuid, "uuid4", next_uuid)
        occupied = f"{prefix}-u000000-{first_uuid.hex}"
        fresh = f"{prefix}-u000001-{fresh_uuid.hex}"
        assert _run(ws, "backup", "--yes") == 0
        first_manifest = repo.path / "manifests" / f"{occupied}.json"
        first_manifest_bytes = first_manifest.read_bytes()
        first_snap_file = repo.path / "snapshots" / occupied / "a.txt"
        first_snap_bytes = first_snap_file.read_bytes()

        # 第二次备份的 u000001 candidate 撞到外部 artifact 后，只 retry UUID；
        # 不推进到 u000002，也不覆盖已有 lifecycle。
        _write(ws["src"] / "a.txt", b"alpha-v2")
        assert _run(ws, "backup", "--yes") == 0

        # 既有快照零污染：manifest 字节、目录内容、complete 状态全部不变
        assert first_manifest.read_bytes() == first_manifest_bytes
        assert first_snap_file.read_bytes() == first_snap_bytes
        assert load_manifest(repo, occupied).status == "complete"

        ids = {s.snapshot_id for s in list_manifests(repo)}
        assert ids == {occupied, fresh}
        assert (repo.path / "snapshots" / collision).is_dir()
        assert load_manifest(repo, fresh).status == "complete"
        assert (repo.path / "snapshots" / fresh / "a.txt").read_bytes() == b"alpha-v2"

    def test_retention_does_not_rebind_deleted_lifecycle_id(self, ws, monkeypatch) -> None:
        _write(ws["src"] / "base.txt", b"base")
        assert _init(ws) == 0
        repo = _repo(ws)
        cfg = load_task_config(ws["config"] / "config.d" / "default.toml")
        write_task_config(replace(cfg, keep_last=2, keep_monthly=1), ws["config"])

        prefix = "2026-09-13_100000"
        _freeze_snapshot_clock(monkeypatch, datetime(2026, 9, 13, 10, 0, 0))
        uuid_hexes = [
            "ffffffffffff4fffbfffffffffffffff",
            "8888888888884888a888888888888888",
            "00000000000040008000000000000000",
            "11111111111141119111111111111111",
        ]
        _freeze_snapshot_uuids(monkeypatch, uuid_hexes)
        lifecycle_ids = [
            f"{prefix}-u{ordinal:06d}-{uuid_hex}" for ordinal, uuid_hex in enumerate(uuid_hexes)
        ]

        report_ids: list[str] = []
        first_report: Path | None = None
        for i, expected_id in enumerate(lifecycle_ids):
            _write(ws["src"] / f"v{i}.txt", f"version-{i}".encode())
            before_reports = set((repo.path / "logs").glob("backup-*.json"))
            assert _run(ws, "backup", "--yes") == 0
            new_reports = set((repo.path / "logs").glob("backup-*.json")) - before_reports
            assert len(new_reports) == 1
            report_path = new_reports.pop()
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            assert payload["snapshot_id"] == expected_id
            report_ids.append(payload["snapshot_id"])
            if i == 0:
                first_report = report_path
            if i == 2:
                assert not (repo.path / "manifests" / f"{lifecycle_ids[0]}.json").exists()
                assert not (repo.path / "snapshots" / lifecycle_ids[0]).exists()

        assert len(set(lifecycle_ids)) == 4
        assert report_ids == lifecycle_ids
        assert len(set(report_ids)) == 4
        assert first_report is not None and first_report.is_file()
        assert (
            json.loads(first_report.read_text(encoding="utf-8"))["snapshot_id"] == lifecycle_ids[0]
        )

        remaining = {s.snapshot_id for s in list_manifests(repo)}
        assert remaining == set(lifecycle_ids[-2:])
        assert lifecycle_ids[3] != lifecycle_ids[0]
        assert "-u000003-" in lifecycle_ids[3]  # A 的 u000000 hole 不得复用
        for sid in remaining:
            verification = verify_snapshot(repo, sid)
            assert verification.ok
            assert verification.unhashed_entries == 0

        # A 的持久报告仍在，但 A 已被 retention 删除；旧 selector 必须 not-found，
        # 不能静默作用于 D generation。
        assert _run(ws, "verify", "--snapshot", lifecycle_ids[0]) == 1
        destination = ws["src"].parent / "old-a-restore"
        assert (
            _run(
                ws,
                "restore",
                "--snapshot",
                lifecycle_ids[0],
                "--to",
                str(destination),
                "--yes",
            )
            == 1
        )
        assert not destination.exists()
        assert (repo.path / "snapshots" / lifecycle_ids[3] / "v3.txt").read_bytes() == b"version-3"

    def test_legacy_id_accepted_by_verify_restore_and_retention(self, ws, monkeypatch) -> None:
        _write(ws["src"] / "a.txt", b"alpha")
        assert _init(ws) == 0
        repo = _repo(ws)
        cfg = load_task_config(ws["config"] / "config.d" / "default.toml")
        write_task_config(replace(cfg, keep_last=1, keep_monthly=1), ws["config"])
        legacy = "2026-09-13_100000-01"
        current = "2026-09-13_100000-u000000-eeeeeeeeeeee4eee8eeeeeeeeeeeeeee"
        candidates = iter((legacy, current))
        monkeypatch.setattr(
            cli,
            "generate_snapshot_id",
            lambda now=None, *, ordinal=0: next(candidates),
        )
        assert _run(ws, "backup", "--yes") == 0
        assert load_manifest(repo, legacy).status == "complete"
        assert _run(ws, "verify", "--snapshot", legacy) == 0
        dest = ws["src"].parent / "out"
        assert _run(ws, "restore", "--snapshot", legacy, "--to", str(dest), "--yes") == 0
        assert (dest / "a.txt").read_bytes() == b"alpha"

        _write(ws["src"] / "a.txt", b"alpha-v2")
        assert _run(ws, "backup", "--yes") == 0
        assert [s.snapshot_id for s in list_manifests(repo)] == [current]
        assert not (repo.path / "manifests" / f"{legacy}.json").exists()
        assert not (repo.path / "snapshots" / legacy).exists()

    def test_unallocatable_id_fails_with_zero_writes(self, ws, monkeypatch) -> None:
        _write(ws["src"] / "a.txt", b"alpha")
        assert _init(ws) == 0
        repo = _repo(ws)
        occupied = "2026-09-13_100000-u000000-ffffffffffff4fff8fffffffffffffff"
        monkeypatch.setattr(
            cli,
            "generate_snapshot_id",
            lambda now=None, *, ordinal=0: occupied,
        )
        assert _run(ws, "backup", "--yes") == 0
        before_manifest = (repo.path / "manifests" / f"{occupied}.json").read_bytes()
        before_file = (repo.path / "snapshots" / occupied / "a.txt").read_bytes()
        before_reports = sorted((repo.path / "logs").glob("backup-*.json"))

        _write(ws["src"] / "a.txt", b"alpha-v2")
        assert _run(ws, "backup", "--yes") == 1  # 安全失败
        assert (repo.path / "manifests" / f"{occupied}.json").read_bytes() == before_manifest
        assert (repo.path / "snapshots" / occupied / "a.txt").read_bytes() == before_file
        assert sorted((repo.path / "logs").glob("backup-*.json")) == before_reports
        assert [s.snapshot_id for s in list_manifests(repo)] == [occupied]

    def test_ordinal_high_water_includes_incomplete_orphan_and_temp(self, ws, monkeypatch) -> None:
        _write(ws["src"] / "a.txt", b"alpha")
        assert _init(ws) == 0
        repo = _repo(ws)
        prefix = "2026-09-13_100000"
        _freeze_snapshot_clock(monkeypatch, datetime(2026, 9, 13, 10, 0, 0))

        current = scan_source(ws["src"], ()).entries
        incomplete = f"{prefix}-u000003-33333333333343338333333333333333"
        write_manifest(
            repo,
            create_manifest(incomplete, str(ws["src"]), repo.hash_algorithm, current),
        )
        orphan = f"{prefix}-u000005-55555555555545559555555555555555"
        (repo.path / "snapshots" / orphan).mkdir()
        staged = f"{prefix}-u000007-77777777777747779777777777777777"
        (repo.path / "manifests.tmp" / f"{staged}.json.tmp").write_text("partial")

        next_uuid = "8888888888884888a888888888888888"
        _freeze_snapshot_uuids(monkeypatch, [next_uuid])
        allocated = cli._new_snapshot_id(repo)
        assert allocated == f"{prefix}-u000008-{next_uuid}"
        assert not cli._snapshot_id_free(repo, staged)

    def test_ordinal_exhaustion_fails_closed(self, ws, monkeypatch) -> None:
        assert _init(ws) == 0
        repo = _repo(ws)
        prefix = "2026-09-13_100000"
        _freeze_snapshot_clock(monkeypatch, datetime(2026, 9, 13, 10, 0, 0))
        occupied = f"{prefix}-u{SNAPSHOT_ORDINAL_MAX:06d}-ffffffffffff4fffbfffffffffffffff"
        (repo.path / "snapshots" / occupied).mkdir()

        def unexpected_uuid():
            pytest.fail("ordinal exhaustion must fail before minting a UUID")

        monkeypatch.setattr(snapshot_mod.uuid, "uuid4", unexpected_uuid)
        with pytest.raises(SnapshotError, match="ordinal 已达到上限"):
            cli._new_snapshot_id(repo)

    def test_legacy_ids_sort_before_new_ordinals_for_equal_created_at(self, ws) -> None:
        _write(ws["src"] / "a.txt", b"alpha")
        assert _init(ws) == 0
        repo = _repo(ws)
        current = scan_source(ws["src"], ()).entries
        digest = hash_file(ws["src"] / "a.txt", repo.hash_algorithm)
        prefix = "2026-09-13_100000"
        ids = [
            prefix,
            f"{prefix}-01",
            f"{prefix}-02",
            f"{prefix}-u000000-ffffffffffff4fffbfffffffffffffff",
            f"{prefix}-u000001-00000000000040008000000000000000",
        ]
        created_at = "2026-09-13T10:00:00.000000+00:00"
        for snapshot_id in ids:
            snapshot_dir = repo.path / "snapshots" / snapshot_id
            snapshot_dir.mkdir()
            (snapshot_dir / "a.txt").write_bytes(b"alpha")
            manifest = create_manifest(
                snapshot_id,
                str(ws["src"]),
                repo.hash_algorithm,
                current,
                hashes={"a.txt": digest},
            )
            write_manifest(repo, mark_complete(replace(manifest, created_at=created_at)))

        assert cli._latest_complete(repo).snapshot_id == ids[-1]
        plan = build_retention_plan(repo, keep_last=2, keep_monthly=0)
        assert plan.keep == tuple(ids[-2:])
        assert plan.delete == tuple(ids[:-2])

    def test_equal_created_at_uses_latest_baseline_and_preserves_source(
        self, ws, monkeypatch, capsys
    ) -> None:
        assert _init(ws) == 0
        repo = _repo(ws)
        fixed = datetime(2026, 9, 13, 10, 0, 0)
        _freeze_snapshot_clock(monkeypatch, fixed, manifests=True)
        uuid_hexes = [
            "ffffffffffff4fffbfffffffffffffff",
            "8888888888884888a888888888888888",
            "00000000000040008000000000000000",
            "11111111111141119111111111111111",
        ]
        _freeze_snapshot_uuids(monkeypatch, uuid_hexes)
        prefix = "2026-09-13_100000"
        ids = [f"{prefix}-u{ordinal:06d}-{uuid_hex}" for ordinal, uuid_hex in enumerate(uuid_hexes)]
        fixed_mtime = 1_700_000_000_000_000_000

        for content, mtime in (
            (b"A", fixed_mtime),
            (b"B", fixed_mtime + 1_000_000_000),
            (b"C", fixed_mtime),
        ):
            _write(ws["src"] / "data.txt", content)
            os.utime(ws["src"] / "data.txt", ns=(mtime, mtime))
            assert (ws["src"] / "data.txt").stat().st_mtime_ns == mtime
            assert _run(ws, "backup", "--yes") == 0

        assert len({load_manifest(repo, sid).created_at for sid in ids[:3]}) == 1
        assert cli._latest_complete(repo).snapshot_id == ids[2]
        plan = build_retention_plan(repo, keep_last=2, keep_monthly=0)
        assert plan.keep == (ids[1], ids[2])
        assert plan.delete == (ids[0],)

        capsys.readouterr()
        assert _run(ws, "verify", "--json") == 0
        verify_payload = json.loads(capsys.readouterr().out)
        assert [entry["snapshot_id"] for entry in verify_payload["snapshots"]] == [ids[2]]

        destination = ws["src"].parent / "latest-restore"
        assert _run(ws, "restore", "--to", str(destination), "--yes") == 0
        assert (destination / "data.txt").read_bytes() == b"C"

        # 当前 source 与 logical latest C 相同；A 与 source size/mtime 相同但
        # bytes 不同。下一次真实 backup 必须以 C 为 baseline，不能 hardlink A。
        assert _run(ws, "backup", "--yes") == 0
        a_file = repo.path / "snapshots" / ids[0] / "data.txt"
        c_file = repo.path / "snapshots" / ids[2] / "data.txt"
        d_file = repo.path / "snapshots" / ids[3] / "data.txt"
        assert d_file.read_bytes() == b"C"
        assert os.path.samefile(d_file, c_file)
        assert not os.path.samefile(d_file, a_file)

        report_path = next((repo.path / "logs").glob(f"backup-{ids[3]}-*.json"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["linked"] == ["data.txt"]
        assert report["copied"] == []
        final_manifest = load_manifest(repo, ids[3], require_complete=True)
        final_entry = next(entry for entry in final_manifest.entries if entry.path == "data.txt")
        assert final_entry.sha == hash_file(c_file, repo.hash_algorithm)
        verification = verify_snapshot(repo, ids[3])
        assert verification.ok
        assert verification.unhashed_entries == 0


class TestB1SnapshotImmutability:
    """Gate B1-1 回归：后续 backup 不得修改/删除历史 complete snapshot 中
    文件名以 .mrtmp 结尾的用户数据（temp residue ownership 按 manifest 判定）。"""

    def test_user_mrtmp_file_survives_second_backup(self, ws, snap_ids) -> None:
        _write(ws["src"] / "keep.mrtmp", b"keep-payload")
        _write(ws["src"] / "normal.txt", b"normal")
        assert _init(ws) == 0
        assert _run(ws, "backup", "--yes") == 0
        repo = _repo(ws)
        sid1 = list_manifests(repo)[0].snapshot_id
        snap1_keep = repo.path / "snapshots" / sid1 / "keep.mrtmp"
        assert snap1_keep.read_bytes() == b"keep-payload"

        _write(ws["src"] / "trigger.txt", b"trigger")
        assert _run(ws, "backup", "--yes") == 0

        # 旧 complete 快照零修改（B1-1 冻结行为）
        assert snap1_keep.read_bytes() == b"keep-payload"
        manifests = {m.snapshot_id: m for m in list_manifests(repo)}
        assert manifests[sid1].status == "complete"
        # 新快照正常
        sid2 = [s for s in manifests if s != sid1][0]
        snap2 = repo.path / "snapshots" / sid2
        assert (snap2 / "keep.mrtmp").read_bytes() == b"keep-payload"
        assert (snap2 / "normal.txt").read_bytes() == b"normal"
        assert (snap2 / "trigger.txt").read_bytes() == b"trigger"
        # verify --all 对全部历史快照成功
        assert _run(ws, "verify", "--all") == 0


class TestB12ResumeHashCoverage:
    """Gate B1-2 回归：verify_on_write=True 时 crash→resume 发布的 complete
    快照不得因 PRE_FILE 复用而静默 sha=None（full verify false negative）；
    crash 后被损坏的 recovered 副本不得被重新哈希认证为正确备份数据。"""

    def _make_interrupted(self, ws, post_files, verify_writes=True):
        """用 production 写路径构造真实中断现场：incomplete manifest（物化前
        落盘，全 entry sha=None）+ 部分物化的快照目录（post_files 模拟中断
        时未来得及复制的文件，删除后处于正常中断态）。"""
        repo = _repo(ws)
        current = scan_source(ws["src"], ()).entries
        changes = detect_changes(ws["src"], current, None)
        write_manifest(
            repo, create_manifest("snap-inc", str(ws["src"]), repo.hash_algorithm, current)
        )
        write_snapshot(
            ws["src"],
            repo,
            current,
            changes,
            snapshot_id="snap-inc",
            verify_writes=verify_writes,
        )
        for rel in post_files:
            (repo.path / "snapshots" / "snap-inc" / rel).unlink()
        return repo

    def _final_manifest(self, repo):
        summaries = list_manifests(repo)
        assert len(summaries) == 1  # 旧 incomplete 已被善后删除
        m = load_manifest(repo, summaries[0].snapshot_id)
        assert m.status == "complete"
        return m

    def _backup_report(self, repo, snapshot_id) -> dict:
        return json.loads(
            list((repo.path / "logs").glob(f"backup-{snapshot_id}-*.json"))[0].read_text(
                encoding="utf-8"
            )
        )

    def test_resume_grants_full_hash_coverage(self, ws, snap_ids) -> None:
        _write(ws["src"] / "pre0.txt", b"pre-zero")
        _write(ws["src"] / "pre1.txt", b"pre-one")
        _write(ws["src"] / "post0.txt", b"post-zero")
        assert _init(ws) == 0
        repo = self._make_interrupted(ws, ["post0.txt"])
        assert _run(ws, "backup", "--yes") == 0

        m = self._final_manifest(repo)
        assert m.snapshot_id != "snap-inc"
        entries = {e.path: e for e in m.entries if not e.is_dir}
        # Test A：全部文件 entry 获得有效哈希覆盖，无 sha=None 静默降级
        assert set(entries) == {"pre0.txt", "pre1.txt", "post0.txt"}
        assert all(e.sha for e in entries.values())
        # Test C：认证哈希 == 当前源内容哈希（source↔recovered 比较真实执行，
        # 不是对副本的盲目重签名）
        assert entries["pre0.txt"].sha == hash_file(
            to_long_path(ws["src"] / "pre0.txt"), repo.hash_algorithm
        )
        # 复用优化保留：内容相等 → 硬链接复用而非重拷
        rep = self._backup_report(repo, m.snapshot_id)
        assert rep["resumed_from"] == "snap-inc"
        assert sorted(rep["linked"]) == ["pre0.txt", "pre1.txt"]
        assert rep["copied"] == ["post0.txt"]
        assert rep["resume_untrusted"] == []
        # 旧 incomplete 已善后；final 内容与源一致
        assert not (repo.path / "snapshots" / "snap-inc").exists()
        assert (repo.path / "snapshots" / m.snapshot_id / "pre0.txt").read_bytes() == b"pre-zero"

        # Test E：对本应有哈希覆盖的文件做同尺寸损坏，full verify 必须检出
        victim = repo.path / "snapshots" / m.snapshot_id / "pre0.txt"
        original = victim.read_bytes()
        victim.write_bytes(bytes([original[0] ^ 0xFF]) + original[1:])
        assert _run(ws, "verify", "--snapshot", m.snapshot_id) == 4

    def test_corrupted_recovered_copy_never_certified(self, ws, snap_ids) -> None:
        _write(ws["src"] / "pre0.txt", b"pre-zero")
        _write(ws["src"] / "pre1.txt", b"pre-one")
        _write(ws["src"] / "post0.txt", b"post-zero")
        assert _init(ws) == 0
        repo = self._make_interrupted(ws, ["post0.txt"])
        # crash 后 / resume 前：同尺寸损坏 incomplete 快照中的 pre0
        victim = repo.path / "snapshots" / "snap-inc" / "pre0.txt"
        original = victim.read_bytes()
        corrupted = bytes([original[0] ^ 0xFF]) + original[1:]
        assert corrupted != original and len(corrupted) == len(original)
        victim.write_bytes(corrupted)
        source_stat = os.stat(to_long_path(ws["src"] / "pre0.txt"))
        os.utime(
            to_long_path(victim),
            ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
        )

        assert _run(ws, "backup", "--yes") == 0
        m = self._final_manifest(repo)
        entries = {e.path: e for e in m.entries if not e.is_dir}
        rep = self._backup_report(repo, m.snapshot_id)
        # Test B/D：坏副本不被信任——排除复用、走正常 write-verify 重拷，
        # 最终内容来自当前可信源而非坏副本
        assert rep["resume_untrusted"] == ["pre0.txt"]
        assert "pre0.txt" in rep["copied"]
        assert "pre0.txt" not in rep["linked"]
        final_bytes = (repo.path / "snapshots" / m.snapshot_id / "pre0.txt").read_bytes()
        assert final_bytes == b"pre-zero"
        assert final_bytes != corrupted
        assert entries["pre0.txt"].sha == hash_file(
            to_long_path(ws["src"] / "pre0.txt"), repo.hash_algorithm
        )
        assert all(e.sha for e in entries.values())
        assert _run(ws, "verify", "--snapshot", m.snapshot_id) == 0

    def test_recovered_mtime_mismatch_is_rematerialized(self, ws, snap_ids) -> None:
        """Legacy post-replace residue with correct bytes but wrong mtime is not linked."""
        _write(ws["src"] / "pre0.txt", b"pre-zero")
        expected_mtime = 1_710_000_000_000_000_000
        os.utime(
            to_long_path(ws["src"] / "pre0.txt"),
            ns=(expected_mtime, expected_mtime),
        )
        assert _init(ws) == 0
        repo = _repo(ws)
        current = scan_source(ws["src"], ()).entries
        write_manifest(
            repo, create_manifest("snap-inc", str(ws["src"]), repo.hash_algorithm, current)
        )
        recovered = repo.path / "snapshots" / "snap-inc" / "pre0.txt"
        recovered.parent.mkdir(parents=True)
        recovered.write_bytes((ws["src"] / "pre0.txt").read_bytes())
        wrong_mtime = current["pre0.txt"].mtime_ns + 10_000_000
        os.utime(to_long_path(recovered), ns=(wrong_mtime, wrong_mtime))
        recovered_inode = os.stat(to_long_path(recovered)).st_ino

        assert _run(ws, "backup", "--yes") == 0

        m = self._final_manifest(repo)
        entry = next(e for e in m.entries if e.path == "pre0.txt")
        final = repo.path / "snapshots" / m.snapshot_id / "pre0.txt"
        report = self._backup_report(repo, m.snapshot_id)
        final_stat = os.stat(to_long_path(final))
        source_stat = os.stat(to_long_path(ws["src"] / "pre0.txt"))
        assert report["resume_uncertified"] == ["pre0.txt"]
        assert report["linked"] == []
        assert report["copied"] == ["pre0.txt"]
        assert report["changes"]["modified"] == ["pre0.txt"]
        assert final_stat.st_ino != recovered_inode
        assert final.read_bytes() == (ws["src"] / "pre0.txt").read_bytes()
        assert abs(final_stat.st_mtime_ns - source_stat.st_mtime_ns) <= 100
        assert entry.mtime_ns == source_stat.st_mtime_ns
        assert entry.sha == hash_file(to_long_path(final), repo.hash_algorithm)
        verification = verify_snapshot(repo, m.snapshot_id)
        assert verification.ok
        assert verification.hashed_files == verification.checked_files == 1
        assert verification.unhashed_entries == 0

    def test_shared_recovered_mtime_mismatch_preserves_complete_inode(self, ws, snap_ids) -> None:
        """Metadata repair must recopy, never utime a recovered hardlink in place."""
        _write(ws["src"] / "shared.txt", b"shared-content")
        old_mtime = 1_700_000_000_000_000_000
        os.utime(
            to_long_path(ws["src"] / "shared.txt"),
            ns=(old_mtime, old_mtime),
        )
        assert _init(ws) == 0
        assert _run(ws, "backup", "--yes") == 0
        repo = _repo(ws)
        old_summary = next(s for s in list_manifests(repo) if s.status == "complete")
        old_manifest_path = repo.path / "manifests" / f"{old_summary.snapshot_id}.json"
        old_manifest_bytes = old_manifest_path.read_bytes()
        old_file = repo.path / "snapshots" / old_summary.snapshot_id / "shared.txt"
        old_initial_stat = os.stat(to_long_path(old_file))
        old_content = old_file.read_bytes()

        expected_mtime = old_initial_stat.st_mtime_ns + 10_000_000_000
        os.utime(
            to_long_path(ws["src"] / "shared.txt"),
            ns=(expected_mtime, expected_mtime),
        )
        current = scan_source(ws["src"], ()).entries
        write_manifest(
            repo, create_manifest("snap-inc", str(ws["src"]), repo.hash_algorithm, current)
        )
        recovered = repo.path / "snapshots" / "snap-inc" / "shared.txt"
        recovered.parent.mkdir(parents=True)
        os.link(to_long_path(old_file), to_long_path(recovered))
        old_before_resume = os.stat(to_long_path(old_file))
        recovered_before_resume = os.stat(to_long_path(recovered))
        assert old_before_resume.st_ino == recovered_before_resume.st_ino
        assert recovered_before_resume.st_mtime_ns != current["shared.txt"].mtime_ns
        assert old_file.read_bytes() == old_content

        assert _run(ws, "backup", "--yes") == 0

        summaries = list_manifests(repo)
        assert {s.status for s in summaries} == {"complete"}
        assert len(summaries) == 2
        new_summary = next(s for s in summaries if s.snapshot_id != old_summary.snapshot_id)
        new_manifest = load_manifest(repo, new_summary.snapshot_id, require_complete=True)
        new_entry = next(e for e in new_manifest.entries if e.path == "shared.txt")
        new_file = repo.path / "snapshots" / new_summary.snapshot_id / "shared.txt"
        new_stat = os.stat(to_long_path(new_file))
        source_stat = os.stat(to_long_path(ws["src"] / "shared.txt"))
        old_after_resume = os.stat(to_long_path(old_file))
        report = self._backup_report(repo, new_summary.snapshot_id)

        assert report["resume_uncertified"] == ["shared.txt"]
        assert report["linked"] == []
        assert report["copied"] == ["shared.txt"]
        assert old_manifest_path.read_bytes() == old_manifest_bytes
        assert old_file.read_bytes() == old_content
        assert old_after_resume.st_ino == old_before_resume.st_ino == old_initial_stat.st_ino
        assert old_after_resume.st_mtime_ns == old_before_resume.st_mtime_ns
        assert new_stat.st_ino != old_after_resume.st_ino
        assert new_file.read_bytes() == (ws["src"] / "shared.txt").read_bytes()
        assert abs(new_stat.st_mtime_ns - source_stat.st_mtime_ns) <= 100
        assert new_entry.mtime_ns == source_stat.st_mtime_ns
        assert new_entry.sha == hash_file(to_long_path(new_file), repo.hash_algorithm)
        verification = verify_snapshot(repo, new_summary.snapshot_id)
        assert verification.ok
        assert verification.hashed_files == verification.checked_files == 1
        assert verification.unhashed_entries == 0

    def test_verify_on_write_false_semantics_preserved(self, ws, snap_ids) -> None:
        _write(ws["src"] / "pre0.txt", b"pre-zero")
        _write(ws["src"] / "post0.txt", b"post-zero")
        assert _init(ws) == 0
        # 用户显式关闭 write verification（既有产品语义）
        cfg_file = ws["config"] / "config.d" / "default.toml"
        cfg_file.write_text(
            cfg_file.read_text(encoding="utf-8").replace("on_write = true", "on_write = false"),
            encoding="utf-8",
        )
        repo = self._make_interrupted(ws, ["post0.txt"], verify_writes=False)
        assert _run(ws, "backup", "--yes") == 0
        m = self._final_manifest(repo)
        entries = {e.path: e for e in m.entries if not e.is_dir}
        # Test F：不强制哈希覆盖，sha=None 仍是该配置下的合法状态
        assert all(e.sha is None for e in entries.values())
        assert _run(ws, "verify", "--snapshot", m.snapshot_id) == 0

    def test_transient_stat_failure_no_unhashed_reuse(self, ws, snap_ids, monkeypatch) -> None:
        """B1-2 blocker：认证阶段源 stat 瞬时失败（如 sharing violation）→
        resume 数据流中源恢复可读且 size/mtime 满足 unchanged 条件——
        该条目不得重新成为「可 silent reuse 但无可信 hash」的 entry。"""
        _write(ws["src"] / "pre0.txt", b"pre-zero")
        _write(ws["src"] / "pre1.txt", b"pre-one")
        _write(ws["src"] / "post0.txt", b"post-zero")
        assert _init(ws) == 0
        repo = self._make_interrupted(ws, ["post0.txt"])

        # 认证阶段对 pre0 源文件的 os.stat 瞬时失败；其后的扫描/复制阶段
        # （os.scandir 的 DirEntry.stat 不经过 os.stat）源恢复正常可读
        real_stat = os.stat
        victim_src = to_long_path(ws["src"] / "pre0.txt")
        fired = []

        def flaky_stat(path, *args, **kwargs):
            if str(path) == victim_src and not fired:
                fired.append(True)
                raise OSError(13, "transient sharing violation", str(path))
            return real_stat(path, *args, **kwargs)

        monkeypatch.setattr(os, "stat", flaky_stat)

        assert _run(ws, "backup", "--yes") == 0

        m = self._final_manifest(repo)
        entries = {e.path: e for e in m.entries if not e.is_dir}
        # 核心断言：不允许任何普通文件 entry 以 sha=None 进入 complete manifest
        assert all(e.sha for e in entries.values()), {p: e.sha for p, e in entries.items()}
        # pre0 未经认证不得复用：走正常 copy/write-verify 路径
        rep = self._backup_report(repo, m.snapshot_id)
        assert rep["resume_uncertified"] == ["pre0.txt"]
        assert "pre0.txt" in rep["copied"]
        assert "pre0.txt" not in rep["linked"]
        assert sorted(rep["linked"]) == ["pre1.txt"]
        # 最终内容来自当前源；full verify 完整通过
        assert (repo.path / "snapshots" / m.snapshot_id / "pre0.txt").read_bytes() == b"pre-zero"
        assert _run(ws, "verify", "--snapshot", m.snapshot_id) == 0

    def test_metadata_reversal_no_unhashed_reuse(self, ws, snap_ids, monkeypatch) -> None:
        """B1-2 blocker：认证瞬间源 mtime 与清单不一致（状态反转前半段），
        随后源恢复原 mtime 满足 unchanged 条件（后半段）——同样不得
        进入未哈希的 silent reuse。"""
        _write(ws["src"] / "pre0.txt", b"pre-zero")
        _write(ws["src"] / "pre1.txt", b"pre-one")
        _write(ws["src"] / "post0.txt", b"post-zero")
        assert _init(ws) == 0
        repo = self._make_interrupted(ws, ["post0.txt"])

        # 认证阶段的 os.stat 谎报 mtime（+1ms）；真实扫描（DirEntry.stat）
        # 读到与清单一致的原始 mtime → 构成完整的状态反转
        real_stat = os.stat
        victim_src = to_long_path(ws["src"] / "pre0.txt")
        fired = []

        def lying_stat(path, *args, **kwargs):
            if str(path) == victim_src and not fired:
                fired.append(True)
                st = real_stat(path, *args, **kwargs)
                return SimpleNamespace(st_size=st.st_size, st_mtime_ns=st.st_mtime_ns + 1_000_000)
            return real_stat(path, *args, **kwargs)

        monkeypatch.setattr(os, "stat", lying_stat)

        assert _run(ws, "backup", "--yes") == 0

        m = self._final_manifest(repo)
        entries = {e.path: e for e in m.entries if not e.is_dir}
        assert all(e.sha for e in entries.values()), {p: e.sha for p, e in entries.items()}
        rep = self._backup_report(repo, m.snapshot_id)
        assert rep["resume_uncertified"] == ["pre0.txt"]
        assert "pre0.txt" in rep["copied"]
        assert "pre0.txt" not in rep["linked"]
        assert (repo.path / "snapshots" / m.snapshot_id / "pre0.txt").read_bytes() == b"pre-zero"
        assert _run(ws, "verify", "--snapshot", m.snapshot_id) == 0

    def test_pre_publication_guard_fails_closed(self, ws, snap_ids, monkeypatch) -> None:
        """B1-2 defense-in-depth：模拟未来回归（认证哈希丢失，且
        sha=None 条目重新被错误判为 unchanged）→ 发布前终检必须
        fail closed，complete manifest 不得发布。"""
        from dataclasses import replace

        _write(ws["src"] / "pre0.txt", b"pre-zero")
        _write(ws["src"] / "post0.txt", b"post-zero")
        assert _init(ws) == 0
        repo = self._make_interrupted(ws, ["post0.txt"])

        real_build = cli.build_resume_baseline

        def regressed_build(repo_, snapshot_id, **kwargs):
            baseline = real_build(repo_, snapshot_id, **kwargs)
            # 模拟回归：认证结果全部丢失，条目留在 previous 且不标记 uncertified
            return replace(
                baseline,
                uncertified=(),
                previous={p: replace(e, sha=None) for p, e in baseline.previous.items()},
            )

        monkeypatch.setattr(cli, "build_resume_baseline", regressed_build)

        real_detect = cli.detect_changes

        def regressed_detect(*args, **kwargs):
            changes = real_detect(*args, **kwargs)
            # 同时模拟 eligibility guard 回归：把未认证 pre0 从 modified
            # 错误恢复成 unchanged，使其硬链接但没有 carried hash。
            return replace(changes, modified=[p for p in changes.modified if p != "pre0.txt"])

        monkeypatch.setattr(cli, "detect_changes", regressed_detect)

        # fail closed：SnapshotError → exit 1，不发布 complete
        assert _run(ws, "backup", "--yes") == 1
        # 新快照保持 incomplete（可再次续传），不产生新的 complete manifest
        summaries = list_manifests(repo)
        assert {s.snapshot_id for s in summaries} == {"snap-inc", "2026-09-13_000001"}
        assert all(s.status == "incomplete" for s in summaries)


class TestVerifyOnWriteHashCoverageTransition:
    """Gate B：False→True 时无哈希 complete 基线必须安全重物化。"""

    @staticmethod
    def _set_verify_on_write(ws, enabled: bool) -> None:
        cfg_file = ws["config"] / "config.d" / "default.toml"
        current = cfg_file.read_text(encoding="utf-8")
        old = f"on_write = {str(not enabled).lower()}"
        new = f"on_write = {str(enabled).lower()}"
        assert old in current
        cfg_file.write_text(current.replace(old, new), encoding="utf-8")

    @staticmethod
    def _complete_manifests(repo):
        summaries = sorted(
            (s for s in list_manifests(repo) if s.status == "complete"),
            key=lambda s: s.snapshot_id,
        )
        return [load_manifest(repo, s.snapshot_id) for s in summaries]

    @staticmethod
    def _backup_report(repo, snapshot_id: str) -> dict:
        return json.loads(
            list((repo.path / "logs").glob(f"backup-{snapshot_id}-*.json"))[0].read_text(
                encoding="utf-8"
            )
        )

    @staticmethod
    def _replace_same_size(path: Path, replacement: bytes) -> None:
        """以同尺寸 sibling replacement 打断 hardlink，避免污染其他快照。"""
        original = path.read_bytes()
        assert replacement != original and len(replacement) == len(original)
        stat_before = path.stat()
        temp = path.with_name(path.name + ".corrupt-replacement")
        temp.write_bytes(replacement)
        os.utime(temp, ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns))
        os.replace(temp, path)
        assert path.read_bytes() == replacement
        assert path.stat().st_size == stat_before.st_size
        assert path.stat().st_mtime_ns == stat_before.st_mtime_ns

    def test_false_to_true_unchanged_recopies_and_full_verify_detects_corruption(
        self, ws, snap_ids
    ) -> None:
        _write(ws["src"] / "alpha.txt", b"alpha-deterministic")
        _write(ws["src"] / "nested" / "beta.bin", bytes(range(32)))
        assert _init(ws) == 0
        self._set_verify_on_write(ws, False)
        assert _run(ws, "backup", "--yes") == 0
        repo = _repo(ws)
        first = self._complete_manifests(repo)[0]
        first_entries = {e.path: e for e in first.entries if not e.is_dir}
        assert all(e.sha is None for e in first_entries.values())

        source_metadata = {
            rel: (entry.size, entry.mtime_ns)
            for rel, entry in scan_source(ws["src"], ()).entries.items()
            if not entry.is_dir
        }
        self._set_verify_on_write(ws, True)
        assert _run(ws, "backup", "--yes") == 0
        first, second = self._complete_manifests(repo)
        second_entries = {e.path: e for e in second.entries if not e.is_dir}
        assert source_metadata == {
            rel: (entry.size, entry.mtime_ns)
            for rel, entry in scan_source(ws["src"], ()).entries.items()
            if not entry.is_dir
        }
        assert all(e.sha for e in second_entries.values())
        for rel, entry in second_entries.items():
            assert entry.sha == hash_file(to_long_path(ws["src"] / rel), repo.hash_algorithm)

        report = self._backup_report(repo, second.snapshot_id)
        assert report["linked"] == []
        assert sorted(report["copied"]) == ["alpha.txt", "nested/beta.bin"]
        assert sorted(report["changes"]["modified"]) == ["alpha.txt", "nested/beta.bin"]
        assert not os.path.samefile(
            repo.path / "snapshots" / first.snapshot_id / "alpha.txt",
            repo.path / "snapshots" / second.snapshot_id / "alpha.txt",
        )

        before = verify_snapshot(repo, second.snapshot_id)
        assert before.ok
        assert before.hashed_files == before.checked_files == 2
        assert before.unhashed_entries == 0

        victim = repo.path / "snapshots" / second.snapshot_id / "alpha.txt"
        original = victim.read_bytes()
        self._replace_same_size(victim, bytes([original[0] ^ 0xFF]) + original[1:])
        after = verify_snapshot(repo, second.snapshot_id)
        assert not after.ok
        assert after.hashed_files == after.checked_files == 2
        assert after.unhashed_entries == 0
        assert [(issue.path, issue.kind) for issue in after.issues] == [("alpha.txt", "corrupt")]
        assert _run(ws, "verify", "--snapshot", second.snapshot_id) == 4

    def test_corrupted_unhashed_complete_candidate_is_not_reused(self, ws, snap_ids) -> None:
        source_bytes = b"trusted-source-content"
        _write(ws["src"] / "victim.bin", source_bytes)
        assert _init(ws) == 0
        self._set_verify_on_write(ws, False)
        assert _run(ws, "backup", "--yes") == 0
        repo = _repo(ws)
        first = self._complete_manifests(repo)[0]
        candidate = repo.path / "snapshots" / first.snapshot_id / "victim.bin"
        corrupted = bytes([source_bytes[0] ^ 0xFF]) + source_bytes[1:]
        self._replace_same_size(candidate, corrupted)
        assert (ws["src"] / "victim.bin").read_bytes() == source_bytes

        self._set_verify_on_write(ws, True)
        assert _run(ws, "backup", "--yes") == 0
        _first, second = self._complete_manifests(repo)
        final = repo.path / "snapshots" / second.snapshot_id / "victim.bin"
        entry = next(e for e in second.entries if e.path == "victim.bin")
        report = self._backup_report(repo, second.snapshot_id)
        assert report["copied"] == ["victim.bin"]
        assert report["linked"] == []
        assert final.read_bytes() == source_bytes
        assert final.read_bytes() != corrupted
        assert entry.sha == hash_file(to_long_path(ws["src"] / "victim.bin"))
        assert verify_snapshot(repo, second.snapshot_id).ok

    def test_current_false_preserves_unhashed_hardlink_reuse(self, ws, snap_ids) -> None:
        _write(ws["src"] / "stable.txt", b"stable")
        assert _init(ws) == 0
        self._set_verify_on_write(ws, False)
        assert _run(ws, "backup", "--yes") == 0
        assert _run(ws, "backup", "--yes") == 0
        repo = _repo(ws)
        first, second = self._complete_manifests(repo)
        assert all(e.sha is None for e in first.entries if not e.is_dir)
        assert all(e.sha is None for e in second.entries if not e.is_dir)
        report = self._backup_report(repo, second.snapshot_id)
        assert report["linked"] == ["stable.txt"]
        assert report["copied"] == []
        assert os.path.samefile(
            repo.path / "snapshots" / first.snapshot_id / "stable.txt",
            repo.path / "snapshots" / second.snapshot_id / "stable.txt",
        )
        verification = verify_snapshot(repo, second.snapshot_id)
        assert verification.ok
        assert verification.hashed_files == 0
        assert verification.unhashed_entries == 1

    def test_true_to_true_unchanged_carries_hash_and_hardlinks(self, ws, snap_ids) -> None:
        _write(ws["src"] / "stable.txt", b"stable")
        assert _init(ws) == 0
        assert _run(ws, "backup", "--yes") == 0
        assert _run(ws, "backup", "--yes") == 0
        repo = _repo(ws)
        first, second = self._complete_manifests(repo)
        first_entry = next(e for e in first.entries if e.path == "stable.txt")
        second_entry = next(e for e in second.entries if e.path == "stable.txt")
        assert first_entry.sha
        assert second_entry.sha == first_entry.sha
        report = self._backup_report(repo, second.snapshot_id)
        assert report["linked"] == ["stable.txt"]
        assert report["copied"] == []
        assert os.path.samefile(
            repo.path / "snapshots" / first.snapshot_id / "stable.txt",
            repo.path / "snapshots" / second.snapshot_id / "stable.txt",
        )
        verification = verify_snapshot(repo, second.snapshot_id)
        assert verification.ok
        assert verification.hashed_files == verification.checked_files == 1
        assert verification.unhashed_entries == 0

    def test_global_publication_guard_rejects_missing_hash(self, ws, snap_ids, monkeypatch) -> None:
        """模拟写入校验哈希在 merge 前丢失；任何 True backup 都必须 fail closed。"""
        from dataclasses import replace

        _write(ws["src"] / "file.txt", b"content")
        assert _init(ws) == 0
        repo = _repo(ws)
        real_write = cli.write_snapshot

        def drop_hashes(*args, **kwargs):
            return replace(real_write(*args, **kwargs), hashes={})

        monkeypatch.setattr(cli, "write_snapshot", drop_hashes)
        assert _run(ws, "backup", "--yes") == 1
        summaries = list_manifests(repo)
        assert len(summaries) == 1
        assert summaries[0].status == "incomplete"


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

    def test_repo_writer_lock_busy_before_recovery_or_scan(self, backed_up, monkeypatch) -> None:
        ws = backed_up
        repo = _repo(ws)
        namespace = repo.path / "locks" / "repo-writer"
        namespace.mkdir(exist_ok=True)
        writer_lock = namespace / "active.lock"
        writer_lock.write_text("pid=999999", encoding="utf-8")
        before_manifests = sorted(os.listdir(to_long_path(repo.path / "manifests")))
        before_snapshots = sorted(os.listdir(to_long_path(repo.path / "snapshots")))
        before_reports = sorted(os.listdir(to_long_path(repo.path / "logs")))

        def forbidden(*a, **k):
            raise AssertionError("仓库写锁被占用时不应触发 recovery/扫描")

        monkeypatch.setattr(cli, "scan_recovery", forbidden)
        monkeypatch.setattr(cli, "scan_source", forbidden)
        assert _run(ws, "backup", "--yes") == 6
        assert writer_lock.is_file()
        assert not (repo.path / "locks" / "default.lock").exists()
        assert sorted(os.listdir(to_long_path(repo.path / "manifests"))) == before_manifests
        assert sorted(os.listdir(to_long_path(repo.path / "snapshots"))) == before_snapshots
        assert sorted(os.listdir(to_long_path(repo.path / "logs"))) == before_reports

    def test_repo_writer_namespace_cannot_collide_with_task_lock(self, backed_up) -> None:
        repo = _repo(backed_up)
        task_lock = repo.path / "locks" / "repo-writer.lock"
        writer_lock = repo.path / "locks" / "repo-writer" / "active.lock"
        assert task_lock != writer_lock

        with cli._TaskLock(repo, "repo-writer"), cli._RepoWriterLock(repo):
            assert task_lock.is_file()
            assert writer_lock.is_file()
        _assert_repo_writer_idle(repo)

    def test_repo_writer_namespace_persists_across_reacquire(self, backed_up) -> None:
        repo = _repo(backed_up)
        namespace = repo.path / "locks" / "repo-writer"
        writer_lock = namespace / "active.lock"

        with cli._RepoWriterLock(repo):
            assert writer_lock.is_file()
        assert namespace.is_dir()
        assert not writer_lock.exists()

        with cli._RepoWriterLock(repo):
            assert writer_lock.is_file()
        assert namespace.is_dir()
        assert not writer_lock.exists()

    def test_repo_writer_locks_are_independent_across_repositories(
        self, backed_up, tmp_path
    ) -> None:
        repo1 = _repo(backed_up)
        target2 = tmp_path / "independent-target"
        target2.mkdir()
        repo2 = init_repo(target2, assume_yes=True)

        with cli._RepoWriterLock(repo1), cli._RepoWriterLock(repo2):
            assert (repo1.path / "locks" / "repo-writer" / "active.lock").is_file()
            assert (repo2.path / "locks" / "repo-writer" / "active.lock").is_file()
        _assert_repo_writer_idle(repo1)
        _assert_repo_writer_idle(repo2)

    def test_repo_writer_lock_released_after_handled_error(self, backed_up, monkeypatch) -> None:
        ws = backed_up
        repo = _repo(ws)

        def boom(*a, **kw):
            raise cli.SnapshotError("simulated handled backup error")

        monkeypatch.setattr(cli, "scan_source", boom)
        assert _run(ws, "backup", "--yes") == 1
        _assert_repo_writer_idle(repo)
        with cli._RepoWriterLock(repo):
            assert (repo.path / "locks" / "repo-writer" / "active.lock").is_file()
        _assert_repo_writer_idle(repo)

    def test_different_task_can_backup_after_normal_release(self, backed_up) -> None:
        ws = backed_up
        repo = _repo(ws)
        cfg = load_task_config(ws["config"] / "config.d" / "default.toml")
        write_task_config(replace(cfg, name="taskB"), ws["config"])

        assert _run(ws, "--task", "taskB", "backup", "--yes") == 0
        _assert_repo_writer_idle(repo)
        complete = [m for m in list_manifests(repo) if m.status == "complete"]
        assert len(complete) == 2

    def test_cross_task_resume_cannot_discard_active_backup(self, ws, snap_ids, tmp_path) -> None:
        """CONCURRENCY-1: repo writer lock closes the confirmed false-complete race."""
        _write(ws["src"] / "base.txt", b"baseline")
        assert _init(ws, "--task", "taskA") == 0
        cfg_a = load_task_config(ws["config"] / "config.d" / "taskA.toml")
        write_task_config(replace(cfg_a, name="taskB"), ws["config"])
        assert _run(ws, "--task", "taskA", "backup", "--yes") == 0
        _write(ws["src"] / "new.txt", b"new payload")

        worker = tmp_path / "pause_after_write_snapshot.py"
        marker = tmp_path / "after-write.marker"
        release = tmp_path / "release.marker"
        worker.write_text(
            """from pathlib import Path
import os
import sys
import time
from mirrorly import cli

config, marker, release = map(Path, sys.argv[1:])
real_write_snapshot = cli.write_snapshot

def pause_after_write(*args, **kwargs):
    result = real_write_snapshot(*args, **kwargs)
    marker.write_text(str(os.getpid()), encoding="utf-8")
    deadline = time.monotonic() + 30
    while not release.exists():
        if time.monotonic() > deadline:
            raise TimeoutError("test barrier release timeout")
        time.sleep(0.01)
    return result

cli.write_snapshot = pause_after_write
raise SystemExit(cli.main([
    "--config", str(config), "--task", "taskA", "backup", "--yes", "--json"
]))
""",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc_a = subprocess.Popen(
            [sys.executable, "-B", str(worker), str(ws["config"]), str(marker), str(release)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
        )
        a_stdout = a_stderr = ""
        try:
            deadline = time.monotonic() + 30
            while not marker.exists():
                if proc_a.poll() is not None:
                    a_stdout, a_stderr = proc_a.communicate()
                    pytest.fail(
                        f"taskA exited before deterministic barrier: "
                        f"{proc_a.returncode}\n{a_stdout}\n{a_stderr}"
                    )
                if time.monotonic() > deadline:
                    pytest.fail("taskA did not reach after-write_snapshot barrier")
                time.sleep(0.01)

            repo = _repo(ws)
            before = list_manifests(repo)
            incomplete = [m for m in before if m.status == "incomplete"]
            assert len(incomplete) == 1
            active_id = incomplete[0].snapshot_id
            active_manifest = repo.path / "manifests" / f"{active_id}.json"
            active_tree = repo.path / "snapshots" / active_id
            expected_files = ["base.txt", "new.txt"]
            assert sorted(p.name for p in active_tree.iterdir()) == expected_files
            before_manifest_bytes = active_manifest.read_bytes()
            before_snapshot_dirs = sorted(p.name for p in (repo.path / "snapshots").iterdir())
            before_reports = sorted(p.name for p in (repo.path / "logs").glob("backup-*.json"))

            proc_b = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "mirrorly",
                    "--config",
                    str(ws["config"]),
                    "--task",
                    "taskB",
                    "backup",
                    "--yes",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=30,
            )
            assert proc_b.returncode == 6
            assert "仓库写锁被占用" in proc_b.stderr
            assert active_manifest.read_bytes() == before_manifest_bytes
            assert sorted(p.name for p in active_tree.iterdir()) == expected_files
            assert sorted(p.name for p in (repo.path / "snapshots").iterdir()) == (
                before_snapshot_dirs
            )
            assert sorted(p.name for p in (repo.path / "logs").glob("backup-*.json")) == (
                before_reports
            )
            assert [m.snapshot_id for m in list_manifests(repo) if m.status == "incomplete"] == [
                active_id
            ]
        finally:
            release.write_text("continue", encoding="utf-8")
            if proc_a.poll() is None:
                try:
                    a_stdout, a_stderr = proc_a.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    proc_a.kill()
                    a_stdout, a_stderr = proc_a.communicate()
            elif not a_stdout and not a_stderr:
                a_stdout, a_stderr = proc_a.communicate()

        assert proc_a.returncode == 0, a_stdout + a_stderr
        payload_a = json.loads(a_stdout)
        assert payload_a["snapshot_id"] == active_id
        final_manifest = load_manifest(repo, active_id, require_complete=True)
        assert all(entry.sha for entry in final_manifest.entries if not entry.is_dir)
        assert active_tree.is_dir()
        assert sorted(p.name for p in active_tree.iterdir()) == expected_files
        verified = verify_snapshot(repo, active_id)
        assert verified.ok
        assert verified.hashed_files == verified.checked_files == 2
        assert verified.unhashed_entries == 0
        assert not [m for m in list_manifests(repo) if m.status == "incomplete"]
        _assert_repo_writer_idle(repo)


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

    def test_source_is_drive_root_rejected_zero_writes(self, tmp_path, ws) -> None:
        # source = 当前盘符根、target = 同盘普通目录：prospective repo 位于
        # source 内，必须 preflight 拒绝且零仓库写入（init 本身不扫描盘根）
        drive = os.path.splitdrive(str(tmp_path))[0]
        assert drive, "测试环境应位于带盘符的路径"
        code = main(
            [
                "--config",
                str(ws["config"]),
                "init",
                "--source",
                drive + os.sep,
                "--target",
                str(tmp_path),
                "--yes",
            ]
        )
        assert code == 1
        assert not (tmp_path / "MirrorlyRepo").exists()
        assert list(tmp_path.rglob("repo.json")) == []
        # 未创建 task config（config.d 不存在）
        assert not (ws["config"] / "config.d").exists()


class TestPathWithin:
    """_path_within 纯单元（commonpath 语义，覆盖 Windows 卷根）。"""

    def test_drive_root_contains_subdir(self, tmp_path) -> None:
        # 旧 startswith(p + os.sep) 实现的 bug：realpath(卷根) 已以
        # 反斜杠结尾，拼接后前缀失配 → 卷根 containment 被绕过
        root = Path(os.path.splitdrive(str(tmp_path))[0] + os.sep)
        assert cli._path_within(tmp_path, root) is True
        # 反向不成立：卷根不位于其子目录内
        assert cli._path_within(root, tmp_path) is False

    def test_equal_paths(self, tmp_path) -> None:
        assert cli._path_within(tmp_path, tmp_path) is True

    def test_nested_paths(self, tmp_path) -> None:
        inner = tmp_path / "a" / "b"
        assert cli._path_within(inner, tmp_path) is True
        assert cli._path_within(inner, tmp_path / "a") is True
        assert cli._path_within(tmp_path / "a", inner) is False

    def test_sibling_paths_false(self, tmp_path) -> None:
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        assert cli._path_within(a, b) is False
        assert cli._path_within(b, a) is False

    def test_different_drives_false(self) -> None:
        # 不存在的路径 realpath 原样返回；跨盘 commonpath ValueError → False
        assert cli._path_within(Path("C:/data"), Path("D:/backup")) is False

    def test_unc_root_contains_subdir(self) -> None:
        assert cli._path_within(Path("//server/share/data"), Path("//server/share")) is True
        assert cli._path_within(Path("//server/share"), Path("//other/share")) is False


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


# ---------------------------------------------------------------------------
# M10 卷锚 resolver 状态机（T-10 M10 修订：GUID 主锚 + repo_id/serial 确认）
# ---------------------------------------------------------------------------

_FAKE_GUID_A = "\\\\?\\Volume{11111111-2222-3333-4444-555555555555}\\"
_FAKE_GUID_B = "\\\\?\\Volume{99999999-8888-7777-6666-555555555555}\\"
_OTHER_REPO_ID = "ffffffffffffffffffffffffffffffff"  # 32-hex canonical 形式


def _make_repo_at(base: Path):
    """在 base（target 根）下创建真实仓库，返回 RepoInfo。"""
    src = base.parent / f"src_{base.name}"
    src.mkdir(parents=True, exist_ok=True)
    (src / "seed.txt").write_bytes(b"seed")
    return init_repo(base)


def _anchor_cfg(path, guid, repo_id, repo_dir) -> TaskConfig:
    return TaskConfig(
        name="default",
        source="C:/whatever",
        target_path=str(path),
        volume_guid=guid,
        repo_id=repo_id,
        repo_dir=repo_dir,
    )


def _rel_repo_dir(target: Path) -> str:
    """target 的卷内相对路径（与 cmd_init 同口径）。"""
    from mirrorly import volume as vol

    mount_root = vol.get_volume_mount_root(str(target))
    rel = os.path.relpath(str(target), mount_root)
    return "." if rel == "." else rel.replace("/", "\\")


def _tree_fp(root: Path) -> dict:
    """目录树指纹（路径 → 内容哈希），用于零写入断言。"""
    import hashlib

    fp = {}
    for p in sorted(Path(root).rglob("*")):
        fp[str(p)] = hashlib.sha256(p.read_bytes() if p.is_file() else b"<dir>").hexdigest()
    return fp


class TestM10Resolver:
    def test_case1_anchored_fast_path(self, tmp_path) -> None:
        target = tmp_path / "t" / "Backup"
        info = _make_repo_at(target)
        cfg = _anchor_cfg(target, info.volume.guid, info.repo_id, _rel_repo_dir(target))
        repo = cli._resolve_repo(cfg)
        assert repo.path == target / "MirrorlyRepo"
        assert repo.repo_id == info.repo_id

    def test_case2_real_api_relocation_stale_path(self, tmp_path) -> None:
        """真实 Windows API 链路重定位：配置 path 失联（仿真盘符漂移），不改配置。"""
        target = tmp_path / "t" / "Backup"
        info = _make_repo_at(target)
        stale = tmp_path / "t" / "gone"  # 旧盘符位置（已不存在）
        cfg = _anchor_cfg(stale, info.volume.guid, info.repo_id, _rel_repo_dir(target))
        repo = cli._resolve_repo(cfg)  # 真实 get_mount_roots：C:\ → 原位置
        assert repo.path == target / "MirrorlyRepo"

    def test_case2_fake_mount_root_relocation(self, tmp_path, monkeypatch) -> None:
        """仿真 E 盘 Backup：fake mount root 指向同一仓库，自动重定位成功。"""
        fake_e = tmp_path / "fakeE"
        info = _make_repo_at(fake_e / "Backup")
        monkeypatch.setattr("mirrorly.volume.get_mount_roots", lambda g: [str(fake_e) + "\\"])
        cfg = _anchor_cfg(Path("D:/Backup"), _FAKE_GUID_A, info.repo_id, "Backup")
        repo = cli._resolve_repo(cfg)
        assert repo.path == fake_e / "Backup" / "MirrorlyRepo"

    def test_case3_wrong_volume_at_old_path_zero_write(self, tmp_path, monkeypatch) -> None:
        """旧盘符被另一卷占用（guid 不匹配）→ 不写旧路径，搜索 expected 卷继续。"""
        occupied = tmp_path / "occupied"  # 旧盘符位置：被另一（不同 guid）卷占用
        _make_repo_at(occupied)
        fake_e = tmp_path / "fakeE"
        expected = _make_repo_at(fake_e / "Backup")
        before = _tree_fp(occupied)

        monkeypatch.setattr("mirrorly.volume.get_volume_guid_for_path", lambda p: _FAKE_GUID_B)
        monkeypatch.setattr("mirrorly.volume.get_mount_roots", lambda g: [str(fake_e) + "\\"])
        cfg = _anchor_cfg(occupied, _FAKE_GUID_A, expected.repo_id, "Backup")
        repo = cli._resolve_repo(cfg)
        assert repo.path == fake_e / "Backup" / "MirrorlyRepo"
        assert _tree_fp(occupied) == before  # 旧路径零写入

    def test_case4_guid_volume_correct_repo_id_mismatch(self, tmp_path, monkeypatch) -> None:
        fake_e = tmp_path / "fakeE"
        _make_repo_at(fake_e / "Backup")
        monkeypatch.setattr("mirrorly.volume.get_mount_roots", lambda g: [str(fake_e) + "\\"])
        cfg = _anchor_cfg(Path("D:/Backup"), _FAKE_GUID_A, _OTHER_REPO_ID, "Backup")
        with pytest.raises(cli._IdentityMismatch, match="仓库 id 不匹配"):
            cli._resolve_repo(cfg)

    def test_case5_multiple_distinct_candidates_rejected(self, tmp_path, monkeypatch) -> None:
        """两个不同 repo 候选（同 repo_id 的仓库副本）→ 拒绝猜测。"""
        import shutil

        fake_e = tmp_path / "fakeE"
        info = _make_repo_at(fake_e / "Backup")
        fake_f = tmp_path / "fakeF"
        shutil.copytree(str(fake_e / "Backup"), str(fake_f / "Backup"))  # 同 repo_id 副本
        monkeypatch.setattr(
            "mirrorly.volume.get_mount_roots", lambda g: [str(fake_e) + "\\", str(fake_f) + "\\"]
        )
        cfg = _anchor_cfg(Path("D:/Backup"), _FAKE_GUID_A, info.repo_id, "Backup")
        with pytest.raises(cli._IdentityMismatch, match="不唯一"):
            cli._resolve_repo(cfg)

    def test_case5f_same_repo_two_mount_roots_not_ambiguous(self, tmp_path, monkeypatch) -> None:
        """同一卷多个挂载点指向同一 repo（samefile 去重）→ 不视为歧义。"""
        fake_e = tmp_path / "fakeE"
        info = _make_repo_at(fake_e / "Backup")
        roots = [str(fake_e) + "\\", str(fake_e).upper() + "\\"]
        monkeypatch.setattr("mirrorly.volume.get_mount_roots", lambda g: roots)
        cfg = _anchor_cfg(Path("D:/Backup"), _FAKE_GUID_A, info.repo_id, "Backup")
        repo = cli._resolve_repo(cfg)
        assert repo.path == fake_e / "Backup" / "MirrorlyRepo"

    def test_case6_and_e_no_downgrade_real_api(self, tmp_path) -> None:
        """GUID 未挂载 → fail closed；即使系统存在 serial+repo_id 完全一致的候选。

        真实 API（不 monkeypatch）：_FAKE_GUID_B 无当前挂载点，而 expected
        repo_id/repo_dir/serial 的真实仓库就在 C: 上——证明没有 serial 降级认领。
        """
        target = tmp_path / "t" / "Backup"
        info = _make_repo_at(target)
        cfg = _anchor_cfg(Path("D:/Backup"), _FAKE_GUID_B, info.repo_id, _rel_repo_dir(target))
        with pytest.raises(cli._IdentityMismatch, match="未连接或卷锚已失效"):
            cli._resolve_repo(cfg)

    def test_case7_volume_found_repo_missing(self, tmp_path, monkeypatch) -> None:
        fake_e = tmp_path / "fakeE"
        (fake_e / "Backup").mkdir(parents=True)  # 卷在、目录在、仓库缺失
        monkeypatch.setattr("mirrorly.volume.get_mount_roots", lambda g: [str(fake_e) + "\\"])
        cfg = _anchor_cfg(Path("D:/Backup"), _FAKE_GUID_A, _OTHER_REPO_ID, "Backup")
        with pytest.raises(RepoError, match="预期仓库路径缺失"):
            cli._resolve_repo(cfg)
        assert list((fake_e / "Backup").iterdir()) == []  # 不自动 init：零新增

    def test_legacy_missing_path_fail_closed_with_hint(self, tmp_path) -> None:
        cfg = _anchor_cfg(tmp_path / "gone", None, None, None)
        with pytest.raises(RepoError, match="legacy"):
            cli._resolve_repo(cfg)

    def test_d_runtime_repo_dir_escape_containment(self, tmp_path, monkeypatch) -> None:
        """直接构造 TaskConfig 篡改 repo_dir：入口校验 + join 后 containment 双防线。"""
        fake_e = tmp_path / "fakeE"
        fake_e.mkdir()
        # 防线 1：_resolve_repo 入口 validate_anchor（防 library API 绕过 load/write）
        cfg = _anchor_cfg(Path("D:/Backup"), _FAKE_GUID_A, _OTHER_REPO_ID, "../escape")
        with pytest.raises(ConfigError, match="repo_dir"):
            cli._resolve_repo(cfg)
        # 防线 2：_search_anchored join 后 containment 复验（不信任单次校验）
        monkeypatch.setattr("mirrorly.volume.get_mount_roots", lambda g: [str(fake_e) + "\\"])
        bad_cfg = _anchor_cfg(Path("D:/Backup"), _FAKE_GUID_A, _OTHER_REPO_ID, "..\\escape")
        with pytest.raises(cli._IdentityMismatch, match="逃逸"):
            cli._search_anchored(bad_cfg)
