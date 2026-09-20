"""Phase 2E application boundaries; existing core/CLI characterization stays authoritative."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from textwrap import dedent

import pytest

from mirrorly import cli
from mirrorly import restore as restore_core
from mirrorly.application import (
    backup,
    locking,
    queries,
    reports,
    repositories,
    restoration,
    setup,
    verification,
)
from mirrorly.config import write_task_config
from mirrorly.manifest import ManifestError, list_manifests
from mirrorly.repo import RepoError
from mirrorly.snapshot import generate_snapshot_id


@pytest.fixture
def workspace(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_bytes(b"alpha")
    (source / "b.txt").write_bytes(b"beta")
    config = tmp_path / "config"
    initialized = setup.create_backup(
        setup.SetupRequest("documents", source, tmp_path / "target", config)
    )
    return config, initialized


@pytest.fixture
def populated(workspace):
    config, initialized = workspace
    backup.run_backup(backup.BackupRequest(config))
    return queries.resolve_task_repository(config), initialized


def _tree(root):
    return {p.relative_to(root): p.read_bytes() if p.is_file() else None for p in root.rglob("*")}


def test_list_is_read_only_and_preserves_filename_order_distinct_from_default_selection(
    workspace, tmp_path, monkeypatch, capsys
):
    config, initialized = workspace

    def reverse_clock(now=None, *, lifecycle_seq):
        return generate_snapshot_id(
            datetime(2030 - lifecycle_seq, 1, 1), lifecycle_seq=lifecycle_seq
        )

    monkeypatch.setattr(backup, "generate_snapshot_id", reverse_clock)
    first = backup.run_backup(backup.BackupRequest(config))
    second = backup.run_backup(backup.BackupRequest(config))
    context = queries.resolve_task_repository(config)
    before = _tree(tmp_path)
    summaries = queries.list_snapshots(context)
    assert summaries == tuple(list_manifests(initialized.repo))
    assert [s.snapshot_id for s in summaries] == [second.facts.snapshot_id, first.facts.snapshot_id]
    assert [s.lifecycle_seq for s in summaries] == [1, 0]
    assert queries.latest_complete(context.repo).snapshot_id == second.facts.snapshot_id
    assert context.task == initialized.task and context.repo.repo_id == initialized.repo.repo_id
    assert context.relocated is False
    assert _tree(tmp_path) == before
    assert capsys.readouterr() == ("", "")


def test_all_verification_calls_precede_notices_and_mandatory_report(workspace, monkeypatch):
    config, _ = workspace
    for _ in range(2):
        backup.run_backup(backup.BackupRequest(config))
    context = queries.resolve_task_repository(config)
    targets = tuple(s.snapshot_id for s in queries.list_snapshots(context))
    events = []
    original_verify = verification.verify_snapshot
    original_report = reports.write_report

    def verify(repo, sid, **kwargs):
        events.append(("verify", sid))
        return original_verify(repo, sid, **kwargs)

    def publish(repo, name, payload):
        events.append(("report", name))
        return original_report(repo, name, payload)

    monkeypatch.setattr(verification, "verify_snapshot", verify)
    monkeypatch.setattr(reports, "write_report", publish)
    result = verification.verify_snapshots(
        context,
        all_snapshots=True,
        on_verified=lambda rep: events.append(("notice", rep.snapshot_id)),
    )
    assert events == [
        *[("verify", sid) for sid in targets],
        *[("notice", sid) for sid in targets],
        ("report", "verify-all"),
    ]
    assert result.facts.targets == targets
    assert result.facts.verification_completed is True and result.facts.ok is True
    assert result.report["snapshots"][0]["hashed_files"] == 2
    assert "report_path" not in result.report
    assert json.loads(result.facts.report_path.read_text(encoding="utf-8")) == result.report


@pytest.mark.parametrize("quick", [False, True])
def test_verify_ok_keeps_unhashed_and_extras_distinct_from_integrity_issues(workspace, quick):
    config, initialized = workspace
    write_task_config(replace(initialized.task, verify_on_write=False), config)
    saved = backup.run_backup(backup.BackupRequest(config))
    snapshot_path = initialized.repo.path / "snapshots" / saved.facts.snapshot_id
    (snapshot_path / "extra.txt").write_text("test extra", encoding="utf-8")
    context = queries.resolve_task_repository(config)
    result = verification.verify_snapshots(context, quick=quick)
    rep = result.facts.reports[0]
    assert rep.ok is True and result.facts.ok is True
    assert rep.hashed_files == 0
    assert rep.unhashed_entries == (0 if quick else 2)
    assert rep.extras == ("extra.txt",)
    assert rep.quick == quick
    assert result.facts.report_path.is_file()


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("written_before_error", [False, True])
def test_report_failure_preserves_completed_verification_and_original_cause(
    populated, monkeypatch, missing, written_before_error
):
    context, _ = populated
    sid = queries.latest_complete(context.repo).snapshot_id
    if missing:
        (context.repo.path / "snapshots" / sid / "a.txt").unlink()
    original = reports.write_report
    cause = reports.ReportPublicationError("injected publication failure")
    paths = []

    def publish(*args, **kwargs):
        if written_before_error:
            paths.append(original(*args, **kwargs))
        raise cause

    monkeypatch.setattr(reports, "write_report", publish)
    notices = []
    with pytest.raises(verification.VerificationFailure) as caught:
        verification.verify_snapshots(context, on_verified=notices.append)
    failure = caught.value
    assert failure.stage == "report" and failure.cause is cause
    assert failure.facts.verification_completed is True
    assert failure.facts.ok is (not missing)
    assert failure.facts.reports == tuple(notices)
    assert failure.facts.targets == (sid,)
    assert failure.facts.report_path is None  # no acknowledged path, NOT zero-side-effect proof
    if missing:
        assert failure.facts.reports[0].issues[0].kind == "missing"
    assert len(paths) == int(written_before_error)
    assert all(path.is_file() for path in paths)


def test_interrupted_batch_keeps_only_returned_facts_without_premature_notices(
    workspace, monkeypatch, capsys
):
    config, _ = workspace
    for _ in range(2):
        backup.run_backup(backup.BackupRequest(config))
    context = queries.resolve_task_repository(config)
    targets = tuple(s.snapshot_id for s in queries.list_snapshots(context))
    original = verification.verify_snapshot
    before = _tree(context.repo.path)
    cause = OSError("second target failed")
    notices = []

    def verify(repo, sid, **kwargs):
        if sid == targets[1]:
            raise cause
        return original(repo, sid, **kwargs)

    monkeypatch.setattr(verification, "verify_snapshot", verify)
    with pytest.raises(verification.VerificationFailure) as caught:
        verification.verify_snapshots(context, all_snapshots=True, on_verified=notices.append)
    facts = caught.value.facts
    assert caught.value.stage == "verification" and caught.value.cause is cause
    assert facts.verification_completed is False and facts.ok is None
    assert tuple(rep.snapshot_id for rep in facts.reports) == targets[:1]
    assert facts.targets == targets and notices == []
    assert _tree(context.repo.path) == before
    assert cli.main(["--config", str(config), "verify", "--all", "--json"]) == 1
    assert capsys.readouterr() == ("", "错误：second target failed\n")


@pytest.mark.parametrize("all_snapshots", [False, True])
def test_no_complete_target_stops_without_report(workspace, tmp_path, all_snapshots):
    config, _ = workspace
    context = queries.resolve_task_repository(config)
    before = _tree(tmp_path)
    with pytest.raises(verification.VerificationFailure) as caught:
        verification.verify_snapshots(context, all_snapshots=all_snapshots)
    assert caught.value.stage == "selection"
    assert isinstance(caught.value.cause, queries.NoCompleteSnapshot)
    assert caught.value.facts.verification_completed is False
    with pytest.raises(queries.NoCompleteSnapshot):
        restoration.prepare_restore(context, tmp_path / "output")
    assert _tree(tmp_path) == before


def test_prepare_is_read_only_and_execute_safe_merge_preserves_extra_files(populated, tmp_path):
    context, _ = populated
    destination = tmp_path / "output"
    destination.mkdir()
    (destination / "a.txt").write_bytes(b"existing")
    (destination / "extra.txt").write_bytes(b"extra")
    before = _tree(tmp_path)
    prepared = restoration.prepare_restore(context, destination)
    assert _tree(tmp_path) == before
    assert prepared.summary == restoration.RestoreSummary(1, 0, 1, 0, 0)
    assert prepared.plan.overwrite == "never"
    assert prepared.plan.destination == destination.resolve()
    outcome = restoration.execute_restore(prepared)
    assert outcome.has_issues is True
    assert outcome.result.restored == ("b.txt",)
    assert outcome.result.skipped[0][0] == "a.txt"
    assert (destination / "a.txt").read_bytes() == b"existing"
    assert (destination / "extra.txt").read_bytes() == b"extra"
    assert (destination / "b.txt").read_bytes() == b"beta"


def test_planned_overwrite_requires_explicit_approval_then_uses_core(populated, tmp_path):
    context, _ = populated
    destination = tmp_path / "output"
    destination.mkdir()
    (destination / "a.txt").write_bytes(b"existing")
    prepared = restoration.prepare_restore(context, destination, overwrite="always")
    before = _tree(tmp_path)
    with pytest.raises(restoration.OverwriteApprovalRequired):
        restoration.execute_restore(prepared)
    assert _tree(tmp_path) == before
    assert prepared.summary.overwrite == 1
    outcome = restoration.execute_restore(prepared, overwrite_approved=True)
    assert outcome.has_issues is False
    assert outcome.result.restored == ("a.txt", "b.txt")
    assert (destination / "a.txt").read_bytes() == b"alpha"


@pytest.mark.parametrize("change", ["destination_upgrade", "manifest_digest", "invalid_plan"])
def test_apply_still_revalidates_and_rejects_stale_or_invalid_intent(populated, tmp_path, change):
    context, _ = populated
    destination = tmp_path / "output"
    prepared = restoration.prepare_restore(context, destination, overwrite="always")
    if change == "destination_upgrade":
        destination.mkdir()
        (destination / "a.txt").write_bytes(b"created after plan")
    elif change == "manifest_digest":
        path = context.repo.path / "manifests" / f"{prepared.plan.snapshot_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["created_at"] = "2000-01-01T00:00:00+00:00"
        path.write_text(json.dumps(data), encoding="utf-8")
    else:
        plan = replace(prepared.plan, entries=(replace(prepared.plan.entries[0], action="bogus"),))
        prepared = replace(prepared, plan=plan)
    before = _tree(tmp_path)
    with pytest.raises(restoration.RestoreExecutionFailure) as caught:
        restoration.execute_restore(prepared)
    assert caught.value.prepared is prepared
    assert isinstance(caught.value.cause, restore_core.RestoreError)
    assert _tree(tmp_path) == before


def test_partial_restore_keeps_core_errors_and_continues_other_files(
    populated, tmp_path, monkeypatch
):
    context, _ = populated
    original = restore_core._restore_one_file

    def copy(src, *args, **kwargs):
        if Path(src).name == "a.txt":
            raise OSError("injected copy failure")
        return original(src, *args, **kwargs)

    monkeypatch.setattr(restore_core, "_restore_one_file", copy)
    prepared = restoration.prepare_restore(context, tmp_path / "output")
    outcome = restoration.execute_restore(prepared)
    assert outcome.has_issues is True
    assert outcome.result.errors == (("a.txt", "injected copy failure"),)
    assert outcome.result.restored == ("b.txt",)
    assert outcome.result.leftovers == ()
    assert (tmp_path / "output" / "b.txt").read_bytes() == b"beta"


def test_apply_raising_after_mutation_does_not_invent_counts_or_rollback(
    populated, tmp_path, monkeypatch
):
    context, _ = populated
    original = restoration.apply_restore
    cause = OSError("injected after apply")

    def fail(repo, plan):
        original(repo, plan)
        raise cause

    monkeypatch.setattr(restoration, "apply_restore", fail)
    prepared = restoration.prepare_restore(context, tmp_path / "output")
    with pytest.raises(restoration.RestoreExecutionFailure) as caught:
        restoration.execute_restore(prepared)
    assert caught.value.cause is cause and caught.value.prepared is prepared
    assert not hasattr(caught.value, "result")
    assert (tmp_path / "output" / "a.txt").read_bytes() == b"alpha"
    assert (tmp_path / "output" / "b.txt").read_bytes() == b"beta"


def test_no_new_locks_or_reports_on_query_restore(populated, tmp_path, monkeypatch):
    context, _ = populated

    def forbidden(*args, **kwargs):
        pytest.fail("query/restore must not gain locks or mandatory reports")

    monkeypatch.setattr(locking, "TaskLock", forbidden)
    monkeypatch.setattr(locking, "RepoWriterLock", forbidden)
    queries.list_snapshots(context)
    verification.verify_snapshots(context)  # report required here, but no new locks
    monkeypatch.setattr(reports, "write_report", forbidden)
    prepared = restoration.prepare_restore(context, tmp_path / "output")
    assert restoration.execute_restore(prepared).has_issues is False


@pytest.mark.parametrize("command", ["list", "verify", "restore"])
def test_relocation_notice_precedes_later_command_errors(
    workspace, tmp_path, monkeypatch, capsys, command
):
    config, initialized = workspace
    original = repositories.resolve_repo

    def relocate(cfg):
        return replace(original(cfg), relocated=True)

    def fail(*args, **kwargs):
        raise ManifestError("injected selection failure")

    monkeypatch.setattr(repositories, "resolve_repo", relocate)
    monkeypatch.setattr(queries, "list_manifests", fail)
    args = ["--config", str(config), command, "--quiet", "--json"]
    if command == "restore":
        args += ["--to", str(tmp_path / "output")]
    assert cli.main(args) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"目标卷已重定位: {initialized.task.target_path} → {initialized.repo.path.parent}"
        "（卷标识匹配；配置未自动修改）\n错误：injected selection failure\n"
    )


def test_restore_cli_usage_check_stays_after_resolution_before_selection(
    workspace, tmp_path, monkeypatch, capsys
):
    config, _ = workspace
    args = ["--config", str(config), "restore", "--to", str(tmp_path / "output"), "--in-place"]

    def selection_forbidden(*args):
        pytest.fail("selection must not precede the CLI in-place usage check")

    monkeypatch.setattr(queries, "latest_complete", selection_forbidden)
    assert cli.main(args) == 2
    assert capsys.readouterr() == ("", "错误：--in-place 是危险操作，必须显式提供 --yes\n")

    def repository_failed(cfg):
        raise RepoError("injected resolver failure")

    monkeypatch.setattr(repositories, "resolve_repo", repository_failed)
    assert cli.main(args) == 1
    assert capsys.readouterr() == ("", "错误：injected resolver failure\n")


@pytest.mark.parametrize("answer", ["y", "n"])
def test_cli_restore_presents_then_confirms_then_applies_same_plan(
    populated, tmp_path, monkeypatch, capsys, answer
):
    _, initialized = populated
    destination = tmp_path / "output"
    destination.mkdir()
    (destination / "a.txt").write_bytes(b"existing")
    original_plan = restoration.plan_restore
    original_apply = restoration.apply_restore
    plans = []
    events = []

    def plan(*args, **kwargs):
        value = original_plan(*args, **kwargs)
        plans.append(value)
        events.append("plan")
        return value

    def confirm(prompt):
        assert "恢复计划:" in capsys.readouterr().out
        assert prompt == "将覆盖 1 个已存在文件（策略 always），确认执行？ [y/N] "
        assert (destination / "a.txt").read_bytes() == b"existing"
        events.append("confirm")
        return answer

    def apply(repo, plan):
        assert plan is plans[0]
        events.append("apply")
        return original_apply(repo, plan)

    monkeypatch.setattr(restoration, "plan_restore", plan)
    monkeypatch.setattr(restoration, "apply_restore", apply)
    monkeypatch.setattr("builtins.input", confirm)
    code = cli.main(
        [
            "--config",
            str(initialized.config_path.parent.parent),
            "restore",
            "--to",
            str(destination),
            "--overwrite",
            "always",
        ]
    )
    assert code == (0 if answer == "y" else 6)
    assert events == ["plan", "confirm", *(["apply"] if answer == "y" else [])]
    assert (destination / "a.txt").read_bytes() == (b"alpha" if answer == "y" else b"existing")


@pytest.mark.parametrize("seam", ["plan_restore", "apply_restore"])
def test_cli_restore_fault_injection_reaches_new_implementation_boundary(
    populated, tmp_path, monkeypatch, capsys, seam
):
    _, initialized = populated
    calls = []

    def fail(*args, **kwargs):
        calls.append(seam)
        raise restore_core.RestoreError("injected restore boundary failure")

    monkeypatch.setattr(restoration, seam, fail)
    assert (
        cli.main(
            [
                "--config",
                str(initialized.config_path.parent.parent),
                "restore",
                "--to",
                str(tmp_path / "output"),
                "--quiet",
                "--json",
            ]
        )
        == 1
    )
    assert calls == [seam]
    assert capsys.readouterr() == ("", "错误：injected restore boundary failure\n")


def test_direct_operations_in_isolated_subprocess_without_cli(populated, tmp_path):
    _, initialized = populated
    script = dedent("""
        import sys
        from pathlib import Path

        class ForbidCli:
            def find_spec(self, fullname, path=None, target=None):
                if fullname == 'mirrorly.cli':
                    raise AssertionError('application imported CLI')

        sys.meta_path.insert(0, ForbidCli())
        from mirrorly.application import queries, verification, restoration, reports
        expected = Path(sys.argv[2]) / 'src/mirrorly/application'
        for module in (queries, verification, restoration):
            assert Path(module.__file__).resolve().parent == expected
        context = queries.resolve_task_repository(sys.argv[1])
        assert len(queries.list_snapshots(context)) == 1
        assert verification.verify_snapshots(context).facts.ok is True
        original = reports.write_report
        cause = reports.ReportPublicationError('injected in isolated process')
        def fail(*args, **kwargs):
            raise cause
        reports.write_report = fail
        try:
            verification.verify_snapshots(context)
        except verification.VerificationFailure as failure:
            assert failure.cause is cause
            assert failure.facts.verification_completed and failure.facts.ok
            assert failure.facts.report_path is None
        else:
            raise AssertionError('mandatory report failure was ignored')
        finally:
            reports.write_report = original
        destination = Path(sys.argv[3])
        prepared = restoration.prepare_restore(context, destination)
        assert prepared.summary.create == 2
        assert not destination.exists()
        result = restoration.execute_restore(prepared)
        assert result.result.restored == ('a.txt', 'b.txt') and not result.has_issues
        partial = restoration.execute_restore(restoration.prepare_restore(context, destination))
        assert partial.has_issues and len(partial.result.skipped) == 2
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
            str(initialized.config_path.parent.parent),
            str(Path(__file__).resolve().parents[1]),
            str(tmp_path / "isolated-output"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
