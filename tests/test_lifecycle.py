"""WALL-CLOCK-ORDER-1 Phase 1A durable lifecycle sequencing regressions."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from mirrorly import lifecycle as lifecycle_mod
from mirrorly import repo as repo_mod
from mirrorly.durable import write_json_durable
from mirrorly.lifecycle import (
    EXHAUSTED_NEXT_SEQUENCE,
    MAX_LIFECYCLE_SEQUENCE,
    LifecycleStateError,
    lifecycle_state_path,
    load_lifecycle_state,
    reserve_lifecycle_sequence,
)
from mirrorly.manifest import create_manifest, mark_complete, write_manifest
from mirrorly.repo import RepoInfo, VolumeInfo, init_repo, load_repo, migrate_repo_to_v2
from mirrorly.retention import RetentionPlan, apply_retention_plan

_NTFS = VolumeInfo(label="BackupDisk", serial="A1B2C3D4", filesystem="NTFS")


def _repo(tmp_path: Path) -> RepoInfo:
    return init_repo(tmp_path / "target", volume_info_provider=lambda _path: _NTFS)


def _state_payload(repo: RepoInfo, next_sequence: object) -> dict:
    return {
        "format_version": 1,
        "repo_id": repo.repo_id,
        "next_sequence": next_sequence,
    }


def _write_state(repo: RepoInfo, next_sequence: object) -> None:
    lifecycle_state_path(repo).write_text(
        json.dumps(_state_payload(repo, next_sequence)) + "\n",
        encoding="utf-8",
    )


def _snapshot_id(sequence: int, suffix: str = "1") -> str:
    identities = {
        "0": "00000000000040008000000000000000",
        "1": "11111111111141118111111111111111",
        "2": "22222222222242228222222222222222",
    }
    return f"2026-09-17_120000-s{sequence:020d}-{identities.get(suffix, identities['1'])}"


def _publish_empty_manifest(
    repo: RepoInfo,
    sequence: int,
    *,
    complete: bool = False,
    resumed_from: str | None = None,
) -> str:
    snapshot_id = _snapshot_id(sequence, format(sequence % 16, "x"))
    manifest = create_manifest(
        snapshot_id,
        "C:/source",
        repo.hash_algorithm,
        {},
        lifecycle_seq=sequence,
        resumed_from_snapshot_id=resumed_from,
    )
    write_manifest(repo, mark_complete(manifest) if complete else manifest)
    (repo.path / "snapshots" / snapshot_id).mkdir()
    return snapshot_id


def _legacy_manifest(repo: RepoInfo, snapshot_id: str):
    placeholder = "2000-01-01_000000-s00000000000000000000-00000000000040008000000000000000"
    manifest = create_manifest(
        placeholder,
        "C:/source",
        repo.hash_algorithm,
        {},
        lifecycle_seq=0,
    )
    return replace(manifest, snapshot_id=snapshot_id, lifecycle_seq=None, format_version=1)


def _write_legacy_manifest(repo: RepoInfo, manifest) -> Path:
    """Write raw v1 JSON without using the v2-only production writer."""

    assert manifest.format_version == 1
    data = {
        "format_version": 1,
        "snapshot_id": manifest.snapshot_id,
        "created_at": manifest.created_at,
        "source_root": manifest.source_root,
        "hash_algorithm": manifest.hash_algorithm,
        "status": manifest.status,
        "stats": asdict(manifest.stats),
        "entries": [
            {
                "path": entry.path,
                "type": "dir" if entry.is_dir else "file",
                "size": entry.size,
                "mtime_ns": entry.mtime_ns,
                "sha": entry.sha,
            }
            for entry in manifest.entries
        ],
    }
    path = repo.path / "manifests" / f"{manifest.snapshot_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _downgrade_repo_json(repo: RepoInfo) -> RepoInfo:
    path = repo.path / repo_mod.REPO_INFO_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    data["format_version"] = repo_mod.LEGACY_FORMAT_VERSION
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")
    return load_repo(repo.path.parent)


class TestLifecycleStateInitialization:
    def test_new_repo_is_v2_with_mandatory_initial_state(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        assert repo.format_version == 2
        assert (
            json.loads((repo.path / "repo.json").read_text(encoding="utf-8"))["format_version"] == 2
        )
        assert json.loads(lifecycle_state_path(repo).read_text(encoding="utf-8")) == {
            "format_version": 1,
            "repo_id": repo.repo_id,
            "next_sequence": 0,
        }
        assert load_repo(repo.path.parent) == repo

    def test_v2_missing_or_malformed_state_fails_closed(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        state_path = lifecycle_state_path(repo)
        state_path.unlink()
        with pytest.raises(LifecycleStateError, match="缺少"):
            load_repo(repo.path.parent)

        state_path.write_text("{truncated", encoding="utf-8")
        with pytest.raises(LifecycleStateError, match="损坏"):
            load_repo(repo.path.parent)


class TestLifecycleReservation:
    def test_reservation_durably_increments_and_leaves_no_temp(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        assert reserve_lifecycle_sequence(repo) == 0
        assert reserve_lifecycle_sequence(repo) == 1
        assert load_lifecycle_state(repo).next_sequence == 2
        assert not lifecycle_state_path(repo).with_suffix(".json.tmp").exists()

    def test_failure_before_publication_does_not_consume_sequence(
        self, tmp_path, monkeypatch
    ) -> None:
        repo = _repo(tmp_path)
        real_write = lifecycle_mod.write_json_durable

        def fail_before(_path, _data):
            raise OSError("before durable publication")

        monkeypatch.setattr(lifecycle_mod, "write_json_durable", fail_before)
        with pytest.raises(LifecycleStateError, match="reservation"):
            reserve_lifecycle_sequence(repo)
        monkeypatch.setattr(lifecycle_mod, "write_json_durable", real_write)
        assert load_lifecycle_state(repo).next_sequence == 0
        assert reserve_lifecycle_sequence(repo) == 0

    def test_publication_then_reported_failure_creates_permanent_gap(
        self, tmp_path, monkeypatch
    ) -> None:
        repo = _repo(tmp_path)
        real_write = lifecycle_mod.write_json_durable

        def publish_then_fail(path, data):
            real_write(path, data)
            raise OSError("caller did not observe success")

        monkeypatch.setattr(lifecycle_mod, "write_json_durable", publish_then_fail)
        with pytest.raises(LifecycleStateError, match="reservation"):
            reserve_lifecycle_sequence(repo)
        monkeypatch.setattr(lifecycle_mod, "write_json_durable", real_write)
        assert load_lifecycle_state(repo).next_sequence == 1
        assert reserve_lifecycle_sequence(repo) == 1

    def test_each_resumed_physical_attempt_gets_a_new_sequence(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        first = reserve_lifecycle_sequence(repo)
        first_id = _publish_empty_manifest(repo, first)

        resumed = reserve_lifecycle_sequence(repo)
        resumed_id = _publish_empty_manifest(repo, resumed, resumed_from=first_id)

        resumed_again = reserve_lifecycle_sequence(repo)
        third_id = _publish_empty_manifest(repo, resumed_again, resumed_from=resumed_id)

        assert (first, resumed, resumed_again) == (0, 1, 2)
        assert len({first_id, resumed_id, third_id}) == 3
        assert load_lifecycle_state(repo).next_sequence == 3

    def test_complete_and_retention_delete_never_reduce_high_water(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        first = reserve_lifecycle_sequence(repo)
        first_id = _publish_empty_manifest(repo, first, complete=True)
        second = reserve_lifecycle_sequence(repo)
        second_id = _publish_empty_manifest(repo, second, complete=True)

        removed = apply_retention_plan(
            repo,
            RetentionPlan(keep=(second_id,), delete=(first_id,)),
        )
        assert removed == (first_id,)
        assert not (repo.path / "manifests" / f"{first_id}.json").exists()
        assert reserve_lifecycle_sequence(repo) == 2

    def test_uint64_max_can_be_reserved_once_then_exhaustion_fails_closed(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        _write_state(repo, MAX_LIFECYCLE_SEQUENCE)
        assert reserve_lifecycle_sequence(repo) == MAX_LIFECYCLE_SEQUENCE
        assert load_lifecycle_state(repo).next_sequence == EXHAUSTED_NEXT_SEQUENCE
        with pytest.raises(LifecycleStateError, match="耗尽"):
            reserve_lifecycle_sequence(repo)


class TestLifecycleStateValidation:
    @pytest.mark.parametrize(
        ("mutator", "match"),
        [
            (lambda repo, data: {**data, "format_version": 2}, "格式版本"),
            (lambda repo, data: {**data, "repo_id": "other"}, "repo_id"),
            (lambda repo, data: {**data, "next_sequence": -1}, "超出范围"),
            (
                lambda repo, data: {
                    **data,
                    "next_sequence": EXHAUSTED_NEXT_SEQUENCE + 1,
                },
                "超出范围",
            ),
            (lambda repo, data: {**data, "next_sequence": True}, "必须是整数"),
            (lambda repo, data: {**data, "next_sequence": "1"}, "必须是整数"),
        ],
    )
    def test_invalid_state_matrix(self, tmp_path, mutator, match) -> None:
        repo = _repo(tmp_path)
        path = lifecycle_state_path(repo)
        data = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps(mutator(repo, data)) + "\n", encoding="utf-8")
        with pytest.raises(LifecycleStateError, match=match):
            load_repo(repo.path.parent)

    def test_detects_state_rollback_against_observed_v2_manifest(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        sequence = reserve_lifecycle_sequence(repo)
        _publish_empty_manifest(repo, sequence, complete=True)
        _write_state(repo, sequence)
        with pytest.raises(LifecycleStateError, match="rollback/corruption"):
            load_repo(repo.path.parent)

    def test_snapshot_id_and_manifest_sequence_mismatch_fails_closed(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        sequence = reserve_lifecycle_sequence(repo)
        path = repo.path / "manifests" / f"{_snapshot_id(sequence)}.json"
        payload = {
            "format_version": 2,
            "snapshot_id": _snapshot_id(sequence),
            "created_at": "2026-09-17T12:00:00+00:00",
            "source_root": "C:/source",
            "hash_algorithm": repo.hash_algorithm,
            "status": "complete",
            "stats": {"files": 0, "dirs": 0, "total_bytes": 0},
            "lifecycle_seq": sequence + 1,
            "entries": [],
        }
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        with pytest.raises(LifecycleStateError, match="不一致"):
            load_repo(repo.path.parent)


class TestLegacyMigration:
    def test_v1_without_state_migrates_and_preserves_legacy_manifest_bytes(self, tmp_path) -> None:
        repo = _downgrade_repo_json(_repo(tmp_path))
        lifecycle_state_path(repo).unlink()
        legacy = _legacy_manifest(repo, "2026-09-16_120000-01")
        _write_legacy_manifest(repo, mark_complete(legacy))
        manifest_path = repo.path / "manifests" / "2026-09-16_120000-01.json"
        before = manifest_path.read_bytes()

        migrated = migrate_repo_to_v2(repo)
        assert migrated.format_version == 2
        assert load_lifecycle_state(migrated).next_sequence == 0
        assert manifest_path.read_bytes() == before

    def test_v1_with_valid_initial_state_finishes_interrupted_migration(self, tmp_path) -> None:
        repo = _downgrade_repo_json(_repo(tmp_path))
        assert load_lifecycle_state(repo).next_sequence == 0
        assert migrate_repo_to_v2(repo).format_version == 2

    def test_v1_with_noninitial_state_fails_closed(self, tmp_path) -> None:
        repo = _downgrade_repo_json(_repo(tmp_path))
        _write_state(repo, 1)
        with pytest.raises(LifecycleStateError, match="non-initial"):
            migrate_repo_to_v2(repo)

    def test_migration_state_published_but_repo_upgrade_failed_is_retryable(
        self, tmp_path, monkeypatch
    ) -> None:
        repo = _downgrade_repo_json(_repo(tmp_path))
        lifecycle_state_path(repo).unlink()
        real_publish = repo_mod._write_json_atomic
        monkeypatch.setattr(
            repo_mod,
            "_write_json_atomic",
            lambda _path, _data: (_ for _ in ()).throw(OSError("repo publish failed")),
        )
        with pytest.raises(OSError, match="repo publish failed"):
            migrate_repo_to_v2(repo)
        assert load_repo(repo.path.parent).format_version == 1
        assert load_lifecycle_state(repo).next_sequence == 0

        monkeypatch.setattr(repo_mod, "_write_json_atomic", real_publish)
        assert migrate_repo_to_v2(load_repo(repo.path.parent)).format_version == 2

    def test_migration_repo_published_then_reported_failure_is_normal_v2(
        self, tmp_path, monkeypatch
    ) -> None:
        repo = _downgrade_repo_json(_repo(tmp_path))
        lifecycle_state_path(repo).unlink()
        real_publish = repo_mod._write_json_atomic

        def publish_then_fail(path, data):
            real_publish(path, data)
            raise OSError("post-publication observation failure")

        monkeypatch.setattr(repo_mod, "_write_json_atomic", publish_then_fail)
        with pytest.raises(OSError, match="post-publication"):
            migrate_repo_to_v2(repo)
        loaded = load_repo(repo.path.parent)
        assert loaded.format_version == 2
        assert load_lifecycle_state(loaded).next_sequence == 0


class TestDurablePublicationPrimitive:
    def test_flush_precedes_namespace_replace(self, tmp_path, monkeypatch) -> None:
        path = tmp_path / "state.json"
        events: list[str] = []
        import mirrorly.durable as durable_mod

        real_fsync = durable_mod.os.fsync

        def record_fsync(fd):
            events.append("fsync")
            real_fsync(fd)

        def record_replace(source, destination):
            events.append("replace")
            source.replace(destination)

        monkeypatch.setattr(durable_mod.os, "fsync", record_fsync)
        monkeypatch.setattr(durable_mod, "_replace_file_durable", record_replace)
        write_json_durable(path, {"ok": True})
        assert events == ["fsync", "replace"]
        assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}

    def test_windows_write_through_flags_are_frozen(self) -> None:
        import mirrorly.durable as durable_mod

        assert durable_mod._MOVEFILE_REPLACE_EXISTING == 0x1
        assert durable_mod._MOVEFILE_WRITE_THROUGH == 0x8
