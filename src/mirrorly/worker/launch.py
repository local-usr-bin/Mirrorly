"""Explicit development host entry point: python -I -u <absolute launch.py> ...

Payload deployment uses payload_launch.py; development qualification stays strict.
"""

import argparse
import json
import os
import sys
from importlib.metadata import distribution
from pathlib import Path


def qualify(interpreter, checkout):
    import mirrorly
    from mirrorly.application import setup

    root = Path(checkout).resolve()
    if (
        not Path(interpreter).is_absolute()
        or Path(sys.executable).resolve() != Path(interpreter).resolve()
    ):
        raise RuntimeError("Wrong development interpreter")
    if (
        not Path(checkout).is_absolute()
        or Path(mirrorly.__file__).resolve() != root / "src/mirrorly/__init__.py"
    ):
        raise RuntimeError("Wrong Mirrorly checkout")
    if Path(setup.__file__).resolve() != root / "src/mirrorly/application/setup.py":
        raise RuntimeError("Wrong setup service checkout")
    origin = json.loads(distribution("mirrorly").read_text("direct_url.json") or "null")
    if (
        not origin
        or not origin.get("dir_info", {}).get("editable")
        or origin.get("url", "").rstrip("/").casefold() != root.as_uri().casefold()
    ):
        raise RuntimeError("Wrong editable origin")
    if "mirrorly.cli" in sys.modules:
        raise RuntimeError("Production worker must not import CLI")
    return {
        "executable": sys.executable,
        "package_path": mirrorly.__file__,
        "setup_path": setup.__file__,
        "editable_origin": origin,
        "cli_imported": False,
    }


def write_all(fd, data):
    while data:
        count = os.write(fd, data)
        if count <= 0:
            raise OSError("Closed output pipe")
        data = data[count:]


def serve(qualification):
    """Shared transport/host entry, after mode-specific qualification succeeds."""
    from mirrorly.worker.host import WorkerHost
    from mirrorly.worker.transport import Diagnostics

    diagnostics = Diagnostics(lambda data: write_all(2, data))
    sys.stdout = sys.stderr = diagnostics
    return WorkerHost(
        lambda count: os.read(0, count), lambda data: write_all(1, data), qualification
    ).run()


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--expected-interpreter", required=True)
    parser.add_argument("--expected-checkout", required=True)
    args = parser.parse_args()
    try:
        qualification = qualify(args.expected_interpreter, args.expected_checkout)
        return serve(qualification)
    except Exception as exc:
        # Startup failure: no application call admitted, no stdout contamination.
        sys.stderr.write(f"Worker startup failed: {type(exc).__name__}: {exc}\n")
        return 3


if __name__ == "__main__":
    code = main()
    # Host has joined its application executor. Do not wait for blocked daemon pipe
    # pumps or Python buffered-stdio finalization. This is never active-op cancellation.
    os._exit(code)
