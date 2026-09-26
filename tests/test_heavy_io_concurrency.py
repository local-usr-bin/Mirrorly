"""Guard against deliberate multi-file data-I/O fan-out in GUI operations.

The probe wraps real leaf operations, never the outer per-file loop. Its first
entry waits for another leaf to enter, with a timeout so serial execution can
continue. A future parallel loop therefore exposes overlap without relying on
random sleeps or replacing the actual Backup/Restore implementation.
"""

from __future__ import annotations

import os
import threading
from collections import Counter
from functools import wraps

import pytest

from mirrorly import recovery, restore, scan, snapshot
from mirrorly.application import backup, setup
from mirrorly.snapshot import SnapshotError


class _HeavyIOProbe:
    def __init__(self, *, hold_first: bool = True) -> None:
        self._lock = threading.Lock()
        self._depth_by_thread: dict[int, int] = {}
        self._hold_first = hold_first
        self._first_held = False
        self.first_entered = threading.Event()
        self._second_entered = threading.Event()
        self.active_heavy_io = 0
        self.max_active_heavy_io = 0
        self.calls: Counter[str] = Counter()
        self.paths: dict[str, list[str]] = {}

    def call(self, label, operation, *args, **kwargs):
        thread_id = threading.get_ident()
        hold = False
        with self._lock:
            depth = self._depth_by_thread.get(thread_id, 0)
            self._depth_by_thread[thread_id] = depth + 1
            self.calls[label] += 1
            if args:
                self.paths.setdefault(label, []).append(str(args[0]))
            if depth == 0:
                self.active_heavy_io += 1
                self.max_active_heavy_io = max(self.max_active_heavy_io, self.active_heavy_io)
                if self._hold_first and not self._first_held:
                    self._first_held = True
                    hold = True
                    self.first_entered.set()
                elif self.active_heavy_io > 1:
                    self._second_entered.set()
        try:
            if hold:
                # A second thread releases the first immediately. Serial code
                # reaches this bounded timeout, then runs the real leaf I/O.
                self._second_entered.wait(timeout=2)
            return operation(*args, **kwargs)
        finally:
            with self._lock:
                depth = self._depth_by_thread[thread_id] - 1
                if depth:
                    self._depth_by_thread[thread_id] = depth
                else:
                    del self._depth_by_thread[thread_id]
                    self.active_heavy_io -= 1


def _instrument(monkeypatch, probe, module, symbol, label):
    original = getattr(module, symbol)

    @wraps(original)
    def guarded(*args, **kwargs):
        return probe.call(label, original, *args, **kwargs)

    monkeypatch.setattr(module, symbol, guarded)


def _instrument_backup(monkeypatch, probe):
    _instrument(monkeypatch, probe, snapshot, "_copy_file_atomic", "copy")
    _instrument(monkeypatch, probe, snapshot, "hash_file", "write_hash")
    _instrument(monkeypatch, probe, scan, "hash_file", "change_hash")
    _instrument(monkeypatch, probe, recovery, "hash_file", "resume_hash")


def _workspace(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in ("a.bin", "b.bin", "c.bin"):
        (source / name).write_bytes(name.encode() * 1024)
    created = setup.create_backup(
        setup.SetupRequest("documents", source, tmp_path / "target", tmp_path / "config")
    )
    assert created.task.verify_on_write is True
    return source, created, backup.BackupRequest(tmp_path / "config")


def test_probe_detects_parallel_leaves_without_counting_synchronous_nesting():
    nested = _HeavyIOProbe(hold_first=False)
    nested.call("outer", lambda: nested.call("inner", lambda: None))
    assert nested.max_active_heavy_io == 1
    assert nested.active_heavy_io == 0

    parallel = _HeavyIOProbe()
    first = threading.Thread(target=lambda: parallel.call("first", lambda: None))
    second = threading.Thread(target=lambda: parallel.call("second", lambda: None))
    first.start()
    assert parallel.first_entered.wait(timeout=2)
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)
    assert not first.is_alive() and not second.is_alive()
    assert parallel.max_active_heavy_io == 2
    assert parallel.active_heavy_io == 0


def test_multifile_backup_copy_and_write_verification_do_not_overlap(tmp_path, monkeypatch):
    source, created, request = _workspace(tmp_path)
    probe = _HeavyIOProbe()
    _instrument_backup(monkeypatch, probe)

    result = backup.run_backup(request)

    assert result.facts.materialization.copied == ("a.bin", "b.bin", "c.bin")
    assert probe.calls["copy"] == 3
    assert probe.calls["write_hash"] == 6  # Source and snapshot each read once per file.
    assert probe.calls["change_hash"] == probe.calls["resume_hash"] == 0
    assert all(str(source) in path for path in probe.paths["copy"])
    assert sum("source" in path for path in probe.paths["write_hash"]) == 3
    assert sum("snapshots" in path for path in probe.paths["write_hash"]) == 3
    assert result.facts.repo.repo_id == created.repo.repo_id
    assert probe.max_active_heavy_io == 1
    assert probe.active_heavy_io == 0


def test_change_detection_reads_finish_before_multifile_materialization(tmp_path, monkeypatch):
    source, _, request = _workspace(tmp_path)
    backup.run_backup(request)
    changed = source / "a.bin"
    before = changed.stat()
    changed.write_bytes(b"new-a" * 1024)  # Same size, different content.
    os.utime(changed, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))
    (source / "d.bin").write_bytes(b"new-d" * 1024)
    probe = _HeavyIOProbe()
    _instrument_backup(monkeypatch, probe)

    result = backup.run_backup(request)

    assert result.facts.changes.modified == ["a.bin"]
    assert result.facts.materialization.copied == ("a.bin", "d.bin")
    assert probe.calls["change_hash"] == 1
    assert probe.calls["copy"] == 2
    assert probe.calls["write_hash"] == 4
    assert probe.max_active_heavy_io == 1
    assert probe.active_heavy_io == 0


def test_resume_content_reads_finish_before_new_file_copy(tmp_path, monkeypatch):
    source, _, request = _workspace(tmp_path)
    original = backup.write_snapshot

    def interrupt_after_materialization(*args, **kwargs):
        original(*args, **kwargs)
        raise SnapshotError("test interruption after materialization")

    with monkeypatch.context() as patch:
        patch.setattr(backup, "write_snapshot", interrupt_after_materialization)
        with pytest.raises(backup.BackupFailure) as failed:
            backup.run_backup(request)
    assert failed.value.stage == "materialization"
    (source / "d.bin").write_bytes(b"new-d" * 1024)
    probe = _HeavyIOProbe()
    _instrument_backup(monkeypatch, probe)

    result = backup.run_backup(request, decide_resume=lambda _: True)

    assert result.facts.resumed_from == failed.value.facts.snapshot_id
    assert result.facts.materialization.copied == ("d.bin",)
    assert probe.calls["resume_hash"] == 6  # Source and recovered file for each prior entry.
    assert probe.calls["copy"] == 1
    assert probe.calls["write_hash"] == 2
    assert probe.max_active_heavy_io == 1
    assert probe.active_heavy_io == 0


def test_multifile_restore_staging_does_not_overlap(tmp_path, monkeypatch):
    _, _, request = _workspace(tmp_path)
    backed_up = backup.run_backup(request)
    destination = tmp_path / "restored"
    plan = restore.plan_restore(backed_up.facts.repo, backed_up.facts.snapshot_id, destination)
    probe = _HeavyIOProbe()
    _instrument(monkeypatch, probe, restore, "hash_file", "manifest_hash")
    _instrument(monkeypatch, probe, restore, "_restore_one_file", "restore_file")

    result = restore.apply_restore(backed_up.facts.repo, plan)

    assert result.restored == ("a.bin", "b.bin", "c.bin")
    assert probe.calls["manifest_hash"] == 1
    assert probe.calls["restore_file"] == 3
    assert (destination / "a.bin").read_bytes() == b"a.bin" * 1024
    assert probe.max_active_heavy_io == 1
    assert probe.active_heavy_io == 0
