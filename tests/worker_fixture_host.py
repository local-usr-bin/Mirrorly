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
    from mirrorly import recovery, repo
    from mirrorly.application import backup as backup_app
    from mirrorly.application import reports, setup
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

    backup_calls = 0

    def backup_service(request, **callbacks):
        nonlocal backup_calls
        backup_calls += 1
        with (gate / "backup_calls").open("a", encoding="utf-8") as log:
            log.write("backup\n")
        if scenario == "backup_crash":
            os._exit(23)
        if scenario == "backup_block_scan":
            original_scan = backup_app.scan_source

            def blocked_scan(*args, **kwargs):
                wait_create()
                return original_scan(*args, **kwargs)

            backup_app.scan_source = blocked_scan
        if (
            scenario.startswith("backup_resume_")
            or scenario in ("backup_notice_drop", "backup_notice_saturated")
        ) and backup_calls == 1:
            original_snapshot = backup_app.write_snapshot

            def leave_incomplete(*args, **kwargs):
                original_snapshot(*args, **kwargs)
                raise OSError("injected after materialization for Resume")

            backup_app.write_snapshot = leave_incomplete
            try:
                return backup_app.run_backup(request, **callbacks)
            finally:
                backup_app.write_snapshot = original_snapshot
        if scenario == "backup_resume_cleanup_failure":

            def failed_cleanup(*args, **kwargs):
                raise OSError("injected resumed-incomplete cleanup failure")

            recovery.discard_incomplete = failed_cleanup
        if scenario == "backup_retention_failure":

            def failed_retention(*args, **kwargs):
                raise OSError("injected retention planning failure")

            backup_app.build_retention_plan = failed_retention
        if scenario == "backup_materialization_failure":

            def failed_materialization(*args, **kwargs):
                raise OSError("injected before complete publication")

            backup_app.write_snapshot = failed_materialization
        if scenario == "backup_report_failure":

            def failed_report(*args, **kwargs):
                raise reports.ReportPublicationError("injected mandatory report failure")

            reports.write_report = failed_report
        if scenario == "backup_with_issues":
            original_scan = backup_app.scan_source

            def scan_with_skipped(*args, **kwargs):
                scan = original_scan(*args, **kwargs)
                return replace(scan, skipped=scan.skipped + (("unreadable.txt", "injected skip"),))

            backup_app.scan_source = scan_with_skipped
        if scenario == "backup_publication_unknown":
            original_manifest = backup_app.write_manifest

            def uncertain_publication(repo_info, manifest):
                original_manifest(repo_info, manifest)
                if manifest.status == "complete":
                    raise OSError("injected after complete publisher write")

            backup_app.write_manifest = uncertain_publication
        return backup_app.run_backup(request, **callbacks)

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
        backup_service=backup_service,
        resume_seconds=0.15 if scenario == "backup_resume_timeout" else 600,
    )
    if scenario == "backup_notice_drop":
        host.out.notice = lambda _value: (_ for _ in ()).throw(OSError("notice pipe failed"))
    elif scenario == "backup_notice_saturated":
        host.out.notice = lambda _value: False  # Bounded queue cannot accept a notice.
    return host.run()


if __name__ == "__main__":
    os._exit(main())
