"""Test-only process fault seams. No test methods are added to the production protocol."""

import argparse
import importlib.abc
import os
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path


class NoCLI(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "mirrorly.cli":
            raise AssertionError("Worker attempted to import CLI orchestration")


sys.meta_path.insert(0, NoCLI())


def main():
    from mirrorly import repo
    from mirrorly.application import setup
    from mirrorly.worker import creation
    from mirrorly.worker.host import WorkerHost
    from mirrorly.worker.launch import qualify, write_all
    from mirrorly.worker.transport import Diagnostics

    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-interpreter", required=True)
    parser.add_argument("--expected-checkout", required=True)
    args = parser.parse_args()
    qualification = qualify(args.expected_interpreter, args.expected_checkout)
    scenario = os.environ["MIRRORLY_TEST_SCENARIO"]
    gate = Path(os.environ["MIRRORLY_TEST_GATE"])
    diagnostics = Diagnostics(lambda data: write_all(2, data))
    sys.stdout = sys.stderr = diagnostics

    def service(request):
        if scenario == "crash":
            os._exit(23)
        if scenario == "blocked":
            (gate / "entered").touch()
            deadline = time.monotonic() + 20
            while not (gate / "release").exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("Test barrier deadline")
                threading.Event().wait(0.01)
        return setup.preflight_setup(request)

    original_write = setup.write_task_config
    original_volume = repo.get_volume_info

    def config_failure(cfg, root):
        if scenario == "create_config_partial":
            path = Path(root) / "config.d" / f"{cfg.name}.toml"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"partial TOML")
        elif scenario == "create_config_complete":
            original_write(cfg, root)
        raise OSError(5, "injected config publication failure")

    def repo_failure(path, data):
        assert path.name == "repo.json" and (path.parent / "lifecycle.json").is_file()
        raise OSError(5, "injected repo publication failure")

    def construct_failure(**kwargs):
        raise ValueError("injected config construction failure")

    def projection_failure(value):
        raise ValueError("injected wire projection failure")

    def wait_create():
        (gate / "entered").touch()
        deadline = time.monotonic() + 20
        while not (gate / "release").exists():
            if time.monotonic() > deadline:
                raise RuntimeError("Test create barrier deadline")
            threading.Event().wait(0.01)

    def blocked_config(cfg, root):
        assert (Path(cfg.target_path) / "MirrorlyRepo/repo.json").is_file()
        wait_create()
        return original_write(cfg, root)

    def create_service(request, *, copy_mode_approved):
        # Test-owned invocation evidence, outside the production method surface.
        with (gate / "create_calls").open("a", encoding="utf-8") as log:
            log.write("create\n")
        if scenario == "create_crash":
            os._exit(23)
        if scenario == "create_blocked":
            wait_create()
        if scenario == "create_blocked_config":
            setup.write_task_config = blocked_config
        if scenario in ("create_config_failure", "create_config_partial", "create_config_complete"):
            setup.write_task_config = config_failure
        elif scenario == "create_repo_failure":
            repo._write_json_atomic = repo_failure
        elif scenario == "create_construct_failure":
            setup.TaskConfig = construct_failure
        elif scenario == "create_approval":

            def exfat(path):
                return replace(original_volume(path), filesystem="exFAT")

            setup.get_volume_info = repo.get_volume_info = exfat
        elif scenario == "create_projection_failure":
            creation.repo_facts = projection_failure
        result = setup.create_backup(request, copy_mode_approved=copy_mode_approved)
        if scenario == "create_unstructured_failure":
            raise OSError(5, "injected after application returned")
        (gate / "create_finished").touch()
        return result

    if scenario == "stderr":
        # Deliberately bypass the lossy diagnostics adapter to stress the C# pipe drain.
        threading.Thread(
            target=lambda: write_all(2, b"\xffdiagnostic\n" * 100000), daemon=True
        ).start()
    host = WorkerHost(
        lambda count: os.read(0, count),
        lambda data: write_all(1, data),
        qualification,
        service=service,
        create_service=create_service,
    )
    return host.run()


if __name__ == "__main__":
    os._exit(main())
