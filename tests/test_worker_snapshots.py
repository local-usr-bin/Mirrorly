"""Read-only, bounded production snapshot collection over real temporary repositories."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from shutil import copyfile
from types import SimpleNamespace

import pytest

from mirrorly.application import queries
from mirrorly.lifecycle import reserve_lifecycle_sequence
from mirrorly.manifest import ManifestStats, ManifestSummary, load_manifest, write_manifest
from mirrorly.worker import protocol, snapshots
from test_worker import intent as intent
from test_worker_backup import backup_intent
from test_worker_backup import execute as run_backup
from test_worker_creation import create, worker


def page(peer, intent, *, after=None, limit=16):
    rid = peer.send(
        "snapshots.list",
        {
            "config_root": intent["config_root"],
            "task": intent["task_name"],
            "after": after,
            "limit": limit,
        },
    )
    accepted = peer.receive()
    assert accepted["request_id"] == rid and accepted["payload"]["phase"] == "accepted"
    terminal = peer.receive()
    assert terminal["request_id"] == rid and terminal["operation_id"] == accepted["operation_id"]
    assert terminal["payload"]["phase"] == "terminal"
    return terminal["payload"]


def test_real_empty_complete_incomplete_and_page_boundaries(tmp_path, intent):
    with worker(tmp_path) as peer:
        assert create(peer, intent)["error"] is None
        empty = page(peer, intent)
        assert empty["error"] is None
        assert empty["result"]["page"] == {
            "selector": intent["task_name"],
            "items": [],
            "next_after": None,
            "latest_complete_snapshot_id": None,
        }
        source = Path(intent["source"])
        (source / "a.txt").write_bytes(b"first")
        first, _ = run_backup(peer, backup_intent(intent))
        (source / "a.txt").write_bytes(b"second")
        second, _ = run_backup(peer, backup_intent(intent))
        assert first["payload"]["result"]["outcome"] == "succeeded"
        assert second["payload"]["result"]["outcome"] == "succeeded"
        first_id = first["payload"]["result"]["facts"]["snapshot_id"]
        second_id = second["payload"]["result"]["facts"]["snapshot_id"]
        context = queries.resolve_task_repository(intent["config_root"], intent["task_name"])
        # The incomplete manifest is a real core manifest, not a GUI fixture row.
        from mirrorly.snapshot import generate_snapshot_id

        incomplete_sequence = reserve_lifecycle_sequence(context.repo)
        incomplete_id = generate_snapshot_id(lifecycle_seq=incomplete_sequence)
        original = load_manifest(context.repo, second_id)
        write_manifest(
            context.repo,
            replace(
                original,
                snapshot_id=incomplete_id,
                lifecycle_seq=incomplete_sequence,
                status="incomplete",
            ),
        )
        all_items = page(peer, intent)["result"]["page"]
        assert {item["status"] for item in all_items["items"]} == {"complete", "incomplete"}
        assert all_items["latest_complete_snapshot_id"] == second_id
        assert all_items["next_after"] is None
        assert {item["snapshot_id"] for item in all_items["items"]} == {
            first_id,
            second_id,
            incomplete_id,
        }
        second_item = next(item for item in all_items["items"] if item["snapshot_id"] == second_id)
        assert second_item["lifecycle_seq"] == 1 and second_item["file_count"] == 1
        assert second_item["logical_bytes"] == 6 and second_item["directory_count"] == 0
        assert (
            second_item["format_version"] == 2 and second_item["resumed_from_snapshot_id"] is None
        )
        # Two selectors load the same TaskConfig.name; the file selector remains
        # the identity of each production query response.
        config_dir = Path(intent["config_root"]) / "config.d"
        copyfile(config_dir / f"{intent['task_name']}.toml", config_dir / "alias.toml")
        alias_page = page(peer, intent | {"task_name": "alias"})["result"]["page"]
        assert alias_page["selector"] == "alias" and alias_page["items"] == all_items["items"]
        first_page = page(peer, intent, limit=1)["result"]["page"]
        assert (
            len(first_page["items"]) == 1
            and first_page["next_after"] == first_page["items"][0]["snapshot_id"]
        )
        next_page = page(peer, intent, after=first_page["next_after"], limit=1)["result"]["page"]
        assert (
            len(next_page["items"]) == 1 and next_page["latest_complete_snapshot_id"] == second_id
        )
        last = page(peer, intent, after=next_page["next_after"], limit=1)["result"]["page"]
        assert len(last["items"]) == 1 and last["next_after"] is None
        assert (
            page(peer, intent, after="removed-cursor")["error"]["code"] == "snapshots_unavailable"
        )


@pytest.mark.parametrize("bad", [0, 17, True, "16", -1])
def test_invalid_page_limit_rejected_before_application(tmp_path, intent, bad):
    with worker(tmp_path) as peer:
        rid = peer.send(
            "snapshots.list",
            {"config_root": intent["config_root"], "task": intent["task_name"], "limit": bad},
        )
        rejection = peer.receive()
        assert rejection["request_id"] == rid
        assert rejection["payload"]["phase"] == "rejected"
        assert rejection["payload"]["error"]["code"] == "invalid_parameters"
        assert rejection["payload"]["error"]["application_invoked"] is False


def test_failures_are_not_empty_success(tmp_path, intent):
    with worker(tmp_path) as peer:
        assert page(peer, intent)["error"]["code"] == "snapshots_unavailable"  # unknown task
        create(peer, intent)
        config = Path(intent["config_root"]) / "config.d" / f"{intent['task_name']}.toml"
        original = config.read_bytes()
        config.write_text("invalid = [", encoding="utf-8")
        assert page(peer, intent)["error"]["code"] == "snapshots_unavailable"
        config.write_bytes(original)
        repo = Path(intent["target"]) / "MirrorlyRepo"
        info = repo / "repo.json"
        original_info = info.read_bytes()
        info.write_text("{}", encoding="utf-8")
        assert page(peer, intent)["error"]["code"] == "snapshots_unavailable"
        info.write_bytes(original_info)
        (repo / "manifests" / "bad.json").write_text("{bad", encoding="utf-8")
        assert page(peer, intent)["error"]["code"] == "snapshots_unavailable"
        (repo / "manifests" / "bad.json").unlink()
        (repo / "manifests").rmdir()
        assert page(peer, intent)["error"]["code"] == "snapshots_unavailable"
        repo.rename(Path(intent["target"]) / "moved-repo")
        assert page(peer, intent)["error"]["code"] == "snapshots_unavailable"


def test_legacy_ambiguity_and_filename_order_never_define_latest(tmp_path, intent, monkeypatch):
    (tmp_path / "manifests").mkdir()
    summaries = (
        ManifestSummary("z-old", "complete", "2025-01-01", ManifestStats(), None, format_version=1),
        ManifestSummary("a-new", "complete", "2026-01-01", ManifestStats(), 1),
    )
    monkeypatch.setattr(
        queries,
        "resolve_task_repository",
        lambda *_: SimpleNamespace(repo=SimpleNamespace(path=tmp_path)),
    )
    monkeypatch.setattr(queries, "list_snapshots", lambda _: summaries)
    result = queries.list_snapshot_page(tmp_path, "documents")
    assert result.entries[0].snapshot_id == "z-old"
    assert result.latest_complete_snapshot_id == "a-new"
    monkeypatch.setattr(
        queries,
        "list_snapshots",
        lambda _: (summaries[0], replace(summaries[1], lifecycle_seq=None, format_version=1)),
    )
    with pytest.raises(Exception, match="legacy"):
        queries.list_snapshot_page(tmp_path, "documents")


def test_large_collection_is_paged_with_safe_frame_margin(tmp_path, monkeypatch):
    (tmp_path / "manifests").mkdir()
    long_time = "t" * 12000
    summaries = tuple(
        ManifestSummary(f"id-{index:05d}", "complete", long_time, ManifestStats(1, 0, 10), index)
        for index in range(1000)
    )
    monkeypatch.setattr(
        queries,
        "resolve_task_repository",
        lambda *_: SimpleNamespace(repo=SimpleNamespace(path=tmp_path)),
    )
    monkeypatch.setattr(queries, "list_snapshots", lambda _: summaries)
    page_value = snapshots.execute((str(tmp_path), "documents", None, 16))
    assert len(page_value["items"]) == 16 and page_value["next_after"] == "id-00015"
    assert page_value["latest_complete_snapshot_id"] == "id-00999"
    encoded = protocol.encode(
        protocol.message(
            "response",
            "session",
            {
                "phase": "terminal",
                "result": {"outcome": "succeeded", "page": page_value},
                "error": None,
            },
            "1",
            "operation",
        )
    )
    assert len(encoded) < 300_000 < protocol.FRAME_BYTES
    assert len(page_value["items"]) < len(summaries)
    monkeypatch.setattr(
        queries, "list_snapshots", lambda _: (replace(summaries[0], created_at="x" * 20000),)
    )
    with pytest.raises(ValueError, match="bounded wire"):
        snapshots.execute((str(tmp_path), "documents", None, 16))
