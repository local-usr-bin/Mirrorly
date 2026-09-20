"""Test-only process fault seams. No test methods are added to the production protocol."""

import argparse
import importlib.abc
import os
import sys
import threading
import time
from pathlib import Path


class NoCLI(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "mirrorly.cli":
            raise AssertionError("Worker attempted to import CLI orchestration")


sys.meta_path.insert(0, NoCLI())


def main():
    from mirrorly.application import setup
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
    )
    return host.run()


if __name__ == "__main__":
    os._exit(main())
