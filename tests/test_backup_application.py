"""Phase 2D: direct transaction use, acknowledged commit facts and original order.

CLI characterization remains in test_cli.py; no alternative backup implementation.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from mirrorly import recovery
from mirrorly.application import backup, locking, reports, repositories, setup, tasks
from mirrorly.lifecycle import load_lifecycle_state
from mirrorly.manifest import ManifestError, list_manifests, load_manifest
from mirrorly.snapshot import SnapshotError
from mirrorly.verify import verify_snapshot


@pytest.fixture
def workspace(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_bytes(b"alpha")
    (source / "b.txt").write_bytes(b"beta")
    result = setup.create_backup(
        setup.SetupRequest("documents", source, tmp_path / "target", tmp_path / "config")
    )
    return backup.BackupRequest(tmp_path / "config"), result


def _lock_paths(repo):
    return (
        repo.path / "locks" / "documents.lock",
        repo.path / "locks" / "repo-writer" / "active.lock",
    )


def _assert_idle(repo):
    assert all(not path.exists() for path in _lock_paths(repo))


def _tree(root):
    return {p.relative_to(root): p.read_bytes() if p.is_file() else None for p in root.rglob("*")}


def _leave_materialized_incomplete(request, monkeypatch):
    original = backup.write_snapshot

    def fail_after_write(*args, **kwargs):
        original(*args, **kwargs)
        raise SnapshotError("injected after materialization")

    with monkeypatch.context() as patch:
        patch.setattr(backup, "write_snapshot", fail_after_write)
        with pytest.raises(backup.BackupFailure) as caught:
            backup.run_backup(request)
    assert caught.value.stage == "materialization"
    assert caught.value.facts.commit_state == "not_published"
    # The call raised after writing files; no successful materialization return is known.
    assert caught.value.facts.materialization is None
    return caught.value.facts.snapshot_id


def test_direct_dry_run_is_zero_mutation_and_real_execution_rescans(
    workspace, tmp_path, monkeypatch, capsys
):
    request, initialized = workspace
    incomplete = _leave_materialized_incomplete(request, monkeypatch)
    before = _tree(tmp_path)
    notices = []

    def forbidden(*args, **kwargs):
        pytest.fail("dry-run must not mutate or ask a resume decision")

    with monkeypatch.context() as patch:
        for module, names in (
            (locking, ("TaskLock", "RepoWriterLock")),
            (
                backup,
                (
                    "migrate_repo_to_v2",
                    "clean_tmp_residue",
                    "reserve_lifecycle_sequence",
                    "write_manifest",
                    "write_snapshot",
                    "build_retention_plan",
                    "apply_retention_plan",
                ),
            ),
            (reports, ("write_report",)),
            (recovery, ("discard_incomplete",)),
        ):
            for name in names:
                patch.setattr(module, name, forbidden)
        preview = backup.run_backup(
            replace(request, dry_run=True), decide_resume=forbidden, on_resume=notices.append
        )
    assert isinstance(preview, backup.DryRunResult)
    assert preview.changes.added == ["a.txt", "b.txt"]
    assert preview.facts.commit_state == "not_published"
    assert preview.facts.snapshot_id is preview.facts.report_path is None
    assert notices == [backup.ResumeNotice("available", incomplete)]
    assert _tree(tmp_path) == before
    (Path(initialized.task.source) / "later.txt").write_bytes(b"after preview")
    actual = backup.run_backup(request, decide_resume=lambda _: False)
    assert actual.facts.changes.added == ["a.txt", "b.txt", "later.txt"]
    assert capsys.readouterr() == ("", "")


def test_direct_success_preserves_report_representation_and_integrity(workspace, capsys):
    request, initialized = workspace
    result = backup.run_backup(request)
    assert isinstance(result, backup.BackupResult)
    assert result.facts.task == initialized.task
    assert result.facts.repo.repo_id == initialized.repo.repo_id
    assert result.facts.commit_state == "published"
    assert result.facts.lifecycle_seq == 0
    assert result.facts.materialization.copied == ("a.txt", "b.txt")
    assert result.facts.manifest.stats.files == 2
    assert result.facts.retention_deleted == ()
    assert result.has_issues is False
    assert "report_path" not in result.report
    assert json.loads(result.facts.report_path.read_text(encoding="utf-8")) == result.report
    assert verify_snapshot(result.facts.repo, result.facts.snapshot_id).ok
    assert capsys.readouterr() == ("", "")
    _assert_idle(initialized.repo)


def test_complete_with_scan_issues_is_not_collapsed_into_clean_success(workspace, monkeypatch):
    request, _ = workspace
    original = backup.scan_source

    def skipped(*args, **kwargs):
        return replace(original(*args, **kwargs), skipped=(("unreadable", "access denied"),))

    monkeypatch.setattr(backup, "scan_source", skipped)
    result = backup.run_backup(request)
    assert result.has_issues is True
    assert result.facts.commit_state == "published"
    assert result.facts.scan_skipped == (("unreadable", "access denied"),)
    assert result.report["skipped"] == [{"path": "unreadable", "reason": "access denied"}]


def test_failure_before_complete_preserves_incomplete_and_does_not_claim_rollback(
    workspace, monkeypatch
):
    request, initialized = workspace
    incomplete = _leave_materialized_incomplete(request, monkeypatch)
    manifest = load_manifest(initialized.repo, incomplete)
    assert manifest.status == "incomplete"
    assert (initialized.repo.path / "snapshots" / incomplete / "a.txt").read_bytes() == b"alpha"
    assert load_lifecycle_state(initialized.repo).next_sequence == 1
    _assert_idle(initialized.repo)


def test_sequence_remains_reserved_when_id_allocation_fails(workspace, monkeypatch):
    request, initialized = workspace
    cause = SnapshotError("injected id failure")

    def fail(*args):
        raise cause

    monkeypatch.setattr(backup, "_new_snapshot_id", fail)
    with pytest.raises(backup.BackupFailure) as caught:
        backup.run_backup(request)
    failure = caught.value
    assert failure.stage == "snapshot_id" and failure.cause is cause
    assert failure.facts.commit_state == "not_published"
    assert failure.facts.lifecycle_seq == 0 and failure.facts.snapshot_id is None
    assert load_lifecycle_state(initialized.repo).next_sequence == 1
    assert list_manifests(initialized.repo) == []
    _assert_idle(initialized.repo)


@pytest.mark.parametrize("published_on_disk", [False, True])
def test_complete_publication_exception_is_unknown_even_if_disk_changed(
    workspace, monkeypatch, published_on_disk
):
    request, initialized = workspace
    original = backup.write_manifest
    cause = ManifestError("injected complete publisher failure")

    def publish(repo, manifest):
        if manifest.status == "complete":
            if published_on_disk:
                original(repo, manifest)
            raise cause
        return original(repo, manifest)

    monkeypatch.setattr(backup, "write_manifest", publish)
    with pytest.raises(backup.BackupFailure) as caught:
        backup.run_backup(request)
    failure = caught.value
    assert failure.stage == "complete_publication" and failure.cause is cause
    assert failure.facts.commit_state == "unknown"
    assert failure.facts.manifest.status == "complete"  # candidate is NOT commit proof
    assert failure.facts.materialization.copied == ("a.txt", "b.txt")
    assert failure.facts.retention_deleted is failure.facts.report_path is None
    actual = load_manifest(initialized.repo, failure.facts.snapshot_id)
    assert actual.status == ("complete" if published_on_disk else "incomplete")
    assert list((initialized.repo.path / "logs").iterdir()) == []
    _assert_idle(initialized.repo)


def test_failure_constructing_complete_is_before_publication(workspace, monkeypatch):
    request, initialized = workspace

    def fail(_):
        raise ManifestError("injected pure construction failure")

    monkeypatch.setattr(backup, "mark_complete", fail)
    with pytest.raises(backup.BackupFailure) as caught:
        backup.run_backup(request)
    assert caught.value.facts.commit_state == "not_published"
    assert load_manifest(initialized.repo, caught.value.facts.snapshot_id).status == "incomplete"
    _assert_idle(initialized.repo)


@pytest.mark.parametrize("published_on_disk", [False, True])
def test_cli_complete_publication_failure_keeps_original_exit_and_output(
    workspace, monkeypatch, capsys, published_on_disk
):
    from mirrorly import cli

    request, initialized = workspace
    original = backup.write_manifest

    def publish(repo, manifest):
        if manifest.status == "complete":
            if published_on_disk:
                original(repo, manifest)
            raise ManifestError("injected publisher error")
        return original(repo, manifest)

    monkeypatch.setattr(backup, "write_manifest", publish)
    assert cli.main(["--config", str(request.config_root), "backup", "--json", "--yes"]) == 1
    assert capsys.readouterr() == ("", "错误：injected publisher error\n")
    assert list_manifests(initialized.repo)[0].status == (
        "complete" if published_on_disk else "incomplete"
    )
    assert list((initialized.repo.path / "logs").iterdir()) == []
    _assert_idle(initialized.repo)


@pytest.mark.parametrize("stage", ["resume_cleanup", "retention_plan", "retention_apply", "report"])
def test_post_commit_failure_keeps_known_facts_and_original_cause(workspace, monkeypatch, stage):
    request, initialized = workspace
    incomplete = _leave_materialized_incomplete(request, monkeypatch)
    seams = {
        "resume_cleanup": (recovery, "discard_incomplete"),
        "retention_plan": (backup, "build_retention_plan"),
        "retention_apply": (backup, "apply_retention_plan"),
        "report": (reports, "write_report"),
    }
    cause = reports.ReportPublicationError("report failed") if stage == "report" else OSError(stage)

    def fail(*args, **kwargs):
        assert all(path.is_file() for path in _lock_paths(initialized.repo))
        raise cause

    monkeypatch.setattr(*seams[stage], fail)
    with pytest.raises(backup.BackupFailure) as caught:
        backup.run_backup(request, decide_resume=lambda _: True)
    failure = caught.value
    facts = failure.facts
    assert failure.stage == stage and failure.cause is cause
    assert facts.commit_state == "published" and facts.snapshot_id != incomplete
    assert facts.repo.repo_id == initialized.repo.repo_id
    assert facts.task == initialized.task and facts.resumed_from == incomplete
    assert facts.manifest.stats.files == 2
    assert facts.materialization.linked == ("a.txt", "b.txt")
    assert facts.changes.added == []
    assert facts.retention_deleted == (() if stage == "report" else None)
    assert facts.report_path is None
    assert verify_snapshot(initialized.repo, facts.snapshot_id).ok
    assert (initialized.repo.path / "snapshots" / incomplete).exists() == (
        stage == "resume_cleanup"
    )
    _assert_idle(initialized.repo)


def test_missing_resume_decision_fails_closed_under_locks(workspace, monkeypatch):
    request, initialized = workspace
    incomplete = _leave_materialized_incomplete(request, monkeypatch)
    before = load_lifecycle_state(initialized.repo)
    with pytest.raises(backup.BackupFailure) as caught:
        backup.run_backup(request)
    failure = caught.value
    assert failure.stage == "baseline"
    assert isinstance(failure.cause, backup.ResumeDecisionRequired)
    assert failure.cause.decision.snapshot_id == incomplete
    assert failure.facts.commit_state == "not_published"
    assert load_lifecycle_state(initialized.repo) == before
    _assert_idle(initialized.repo)


def test_full_transaction_order_and_synchronous_resume_stay_inside_both_locks(
    workspace, monkeypatch
):
    request, initialized = workspace
    incomplete = _leave_materialized_incomplete(request, monkeypatch)
    calls = []
    task_lock, repo_lock = _lock_paths(initialized.repo)
    original_enter = locking._ExclusiveFileLock.__enter__
    original_exit = locking._ExclusiveFileLock.__exit__

    def enter(lock):
        if lock._path == repo_lock:
            assert task_lock.is_file()
        value = original_enter(lock)
        calls.append("lock-task" if lock._path == task_lock else "lock-repo")
        return value

    def leave(lock, *exc):
        assert task_lock.is_file()
        assert repo_lock.exists() == (lock._path == repo_lock)
        original_exit(lock, *exc)
        calls.append("unlock-task" if lock._path == task_lock else "unlock-repo")

    monkeypatch.setattr(locking._ExclusiveFileLock, "__enter__", enter)
    monkeypatch.setattr(locking._ExclusiveFileLock, "__exit__", leave)

    def resolve(original, label):
        def call(*args, **kwargs):
            _assert_idle(initialized.repo)
            calls.append(label)
            return original(*args, **kwargs)

        return call

    for module, name in ((tasks, "resolve_task_config"), (repositories, "resolve_repo")):
        monkeypatch.setattr(module, name, resolve(getattr(module, name), name))

    def record(label):
        assert task_lock.is_file() and repo_lock.is_file()
        calls.append(label)

    def wrap(original, label):
        def call(*args, **kwargs):
            record(label)
            return original(*args, **kwargs)

        return call

    for module, name in (
        (backup, "migrate_repo_to_v2"),
        (backup, "_select_baseline"),
        (backup, "build_resume_baseline"),
        (backup, "scan_source"),
        (backup, "detect_changes"),
        (backup, "clean_tmp_residue"),
        (backup, "reserve_lifecycle_sequence"),
        (backup, "_new_snapshot_id"),
        (backup, "write_snapshot"),
        (backup, "mark_complete"),
        (recovery, "discard_incomplete"),
        (backup, "build_retention_plan"),
        (backup, "apply_retention_plan"),
        (reports, "write_report"),
    ):
        monkeypatch.setattr(module, name, wrap(getattr(module, name), name))

    original_publish = backup.write_manifest

    def publish(repo, manifest):
        record("publish-" + manifest.status)
        return original_publish(repo, manifest)

    monkeypatch.setattr(backup, "write_manifest", publish)

    def decide(decision):
        record("decision")
        assert decision == backup.ResumeDecision(
            incomplete, load_manifest(initialized.repo, incomplete).created_at
        )
        return True

    def notice(message):
        record("notice")
        assert message.kind == "selected" and message.snapshot_id == incomplete

    backup.run_backup(request, decide_resume=decide, on_resume=notice)
    assert calls == [
        "resolve_task_config",
        "resolve_repo",
        "lock-task",
        "lock-repo",
        "migrate_repo_to_v2",
        "_select_baseline",
        "decision",
        "build_resume_baseline",
        "notice",
        "scan_source",
        "detect_changes",
        "clean_tmp_residue",
        "reserve_lifecycle_sequence",
        "_new_snapshot_id",
        "publish-incomplete",
        "write_snapshot",
        "mark_complete",
        "publish-complete",
        "discard_incomplete",
        "build_retention_plan",
        "apply_retention_plan",
        "write_report",
        "unlock-repo",
        "unlock-task",
    ]
    _assert_idle(initialized.repo)


def test_relocation_notice_precedes_locks_and_survives_later_failure(workspace, monkeypatch):
    request, initialized = workspace
    original = repositories.resolve_repo
    events = []

    def relocated(cfg):
        return replace(original(cfg), relocated=True)

    def notice(cfg, repo):
        assert cfg == initialized.task and repo.repo_id == initialized.repo.repo_id
        _assert_idle(repo)
        events.append("relocated")

    def fail(repo):
        assert all(p.is_file() for p in _lock_paths(repo))
        events.append("migration")
        raise OSError("injected migration failure")

    monkeypatch.setattr(repositories, "resolve_repo", relocated)
    monkeypatch.setattr(backup, "migrate_repo_to_v2", fail)
    with pytest.raises(backup.BackupFailure) as caught:
        backup.run_backup(request, on_relocation=notice)
    assert events == ["relocated", "migration"]
    assert caught.value.facts.relocated is True
    assert caught.value.facts.commit_state == "not_published"
    _assert_idle(initialized.repo)


def test_keyboard_interrupt_is_not_a_failure_or_cancellation_result(workspace, monkeypatch):
    request, initialized = workspace

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(backup, "write_snapshot", interrupt)
    with pytest.raises(KeyboardInterrupt):
        backup.run_backup(request)
    assert list_manifests(initialized.repo)[0].status == "incomplete"
    _assert_idle(initialized.repo)


def test_direct_service_in_isolated_subprocess_forbids_cli_import(workspace, tmp_path):
    request, initialized = workspace
    script = dedent("""
        import sys
        from pathlib import Path

        class ForbidCli:
            def find_spec(self, fullname, path=None, target=None):
                if fullname == 'mirrorly.cli':
                    raise AssertionError('application imported CLI')

        sys.meta_path.insert(0, ForbidCli())
        from mirrorly.application import backup
        expected = Path(sys.argv[2]) / 'src/mirrorly/application/backup.py'
        assert Path(backup.__file__).resolve() == expected
        request = backup.BackupRequest(sys.argv[1], dry_run=True)
        assert isinstance(backup.run_backup(request), backup.DryRunResult)
        result = backup.run_backup(backup.BackupRequest(sys.argv[1]))
        assert result.facts.commit_state == 'published'
        assert result.has_issues is False
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
            str(request.config_root),
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
    assert list_manifests(initialized.repo)[0].status == "complete"


def test_selection_filename_does_not_change_recorded_task_lock_identity(workspace, monkeypatch):
    request, initialized = workspace
    initialized.config_path.rename(initialized.config_path.with_name("selection.toml"))
    seen = []
    original = backup.scan_source

    def scan(*args, **kwargs):
        seen.append(tuple(p.exists() for p in _lock_paths(initialized.repo)))
        assert not (initialized.repo.path / "locks" / "selection.lock").exists()
        return original(*args, **kwargs)

    monkeypatch.setattr(backup, "scan_source", scan)
    result = backup.run_backup(replace(request, task="selection"))
    assert result.facts.task == tasks.resolve_task_config(request.config_root, "selection")
    assert result.facts.task.name == "documents"
    assert seen == [(True, True)]
