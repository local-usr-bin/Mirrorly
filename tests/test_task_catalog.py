"""GUI catalog: scoped durable Python truth, bounded readonly IPC."""

from pathlib import Path

import pytest

from mirrorly.application.tasks import list_tasks
from mirrorly.config import TaskConfig, write_task_config
from mirrorly.worker import catalog, protocol
from test_worker import intent as intent
from test_worker_creation import create, worker


def task(root, selector, name="Documents", source="C:/source"):
    path = root / "config.d" / f"{selector}.toml"
    written = write_task_config(TaskConfig(name=name, source=source, target_path="E:/backup"), root)
    if written != path:
        written.rename(path)
    return path


def test_empty_does_not_create_root(tmp_path):
    root = tmp_path / "gui"
    assert list_tasks(root).entries == ()
    assert not root.exists()


def test_identity_and_damage_are_separate(tmp_path):
    task(tmp_path, "selector", "Different name")
    bad = tmp_path / "config.d" / "bad.toml"
    bad.write_text("invalid TOML !")
    page = list_tasks(tmp_path)
    assert [e.selector for e in page.entries] == ["bad", "selector"]
    assert page.entries[0].task is None and page.entries[0].problem is not None
    assert page.entries[1].task.name == "Different name"
    assert bad.read_text() == "invalid TOML !"


def test_unreadable_entry_not_silently_omitted(tmp_path, monkeypatch):
    from mirrorly.application import tasks

    task(tmp_path, "denied")
    monkeypatch.setattr(
        tasks, "load_task_config", lambda _: (_ for _ in ()).throw(PermissionError("denied"))
    )
    assert isinstance(list_tasks(tmp_path).entries[0].problem, PermissionError)


def test_cwd_and_cli_root_not_imported(tmp_path, monkeypatch):
    cli = tmp_path / ".mirrorly"
    task(cli, "cli")
    gui = tmp_path / "gui"
    task(gui, "gui")
    monkeypatch.chdir(tmp_path)
    assert [e.selector for e in list_tasks(gui).entries] == ["gui"]
    with pytest.raises(ValueError):
        list_tasks(Path("relative"))


def test_pages_are_bounded_and_ordered(tmp_path):
    for index in range(35):
        task(tmp_path, f"task{index:03}")
    after, seen = None, []
    while True:
        page = catalog.execute((str(tmp_path), after))
        assert len(page["entries"]) <= 16
        assert len(protocol.encode(protocol.message("event", "test", page))) < protocol.FRAME_BYTES
        seen.extend(e["selector"] for e in page["entries"])
        after = page["next_after"]
        if after is None:
            break
    assert seen == [f"task{i:03}" for i in range(35)]


def test_large_valid_config_is_problem_not_truncated_truth(tmp_path):
    task(tmp_path, "large", source="C:/" + "a" * 40000)
    assert list_tasks(tmp_path).entries[0].task is not None
    page = catalog.execute((str(tmp_path), None))
    assert page["entries"][0]["task"] is None
    assert page["entries"][0]["problem"] is not None
    protocol.encode(protocol.message("event", "test", page))


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"config_root": "relative"},
        {"config_root": None},
        {"config_root": "C:/absolute", "task": {}},
    ],
)
def test_catalog_intent_only(params):
    with pytest.raises(ValueError):
        catalog.request(params)


def read(peer, root):
    peer.send("tasks.list", {"config_root": str(root)})
    assert peer.receive()["payload"]["phase"] == "accepted"
    reply = peer.receive()["payload"]
    assert reply["error"] is None
    return reply["result"]["catalog"]


def test_create_then_new_session_rediscovers_without_cli_import(tmp_path, intent):
    with worker(tmp_path) as peer:
        assert read(peer, intent["config_root"])["entries"] == []
        create(peer, intent)
        first = read(peer, intent["config_root"])
        assert first["entries"][0]["task"]["name"] == "documents"
    with worker(tmp_path) as peer:
        assert not peer.hello["payload"]["qualification"]["cli_imported"]
        assert read(peer, intent["config_root"]) == first
    assert not list((Path(intent["target"]) / "MirrorlyRepo" / "snapshots").iterdir())


def test_contender_can_read_catalog_while_gate_owned(tmp_path, intent):
    with worker(tmp_path) as first, worker(tmp_path) as second:
        create(first, intent)
        assert len(read(second, intent["config_root"])["entries"]) == 1


def test_mixed_case_cursor_and_uppercase_toml(tmp_path):
    task(tmp_path, "alpha")
    task(tmp_path, "Bravo").rename(tmp_path / "config.d" / "Bravo.TOML")
    first = list_tasks(tmp_path, limit=1)
    assert first.entries[0].selector == "Bravo"
    second = list_tasks(tmp_path, after=first.next_after, limit=1)
    assert second.entries[0].selector == "alpha" and second.next_after is None


def test_catalog_busy_is_not_queued(tmp_path, intent):
    import time

    with worker(tmp_path, "create_blocked_config") as peer:
        peer.send("setup.create", intent | {"copy_mode_approved": False})
        assert peer.receive()["payload"]["phase"] == "accepted"
        deadline = time.monotonic() + 5
        while not (tmp_path / "entered").exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        peer.send("tasks.list", {"config_root": intent["config_root"]})
        reply = peer.receive()["payload"]
        assert reply["error"]["code"] == "busy" and not reply["error"]["application_invoked"]
        (tmp_path / "release").touch()
        assert peer.receive()["payload"]["result"]["outcome"] == "succeeded"
        assert len(read(peer, intent["config_root"])["entries"]) == 1
