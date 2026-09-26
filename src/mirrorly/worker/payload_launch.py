"""Isolated payload entry: no checkout/site/PATH discovery and no runtime installs."""

import argparse
import importlib
import importlib.metadata
import json
import os
import struct
import sys
from pathlib import Path

PYTHON_VERSION = (3, 13, 15)
BLAKE3_VERSION = "1.0.9"
PROTOCOL_VERSION = {"major": 1, "minor": 0}
BLAKE3_ABC = "6437b3ac38465133ffb63b75273a8db548c558465d79db03fd359c6cd5bd9d85"


def _same(actual, expected, label):
    if not actual or Path(actual).resolve() != expected.resolve():
        raise RuntimeError(f"Payload qualification: wrong {label}")


def qualify(interpreter, payload_root):
    if not Path(interpreter).is_absolute() or not Path(payload_root).is_absolute():
        raise RuntimeError("Payload qualification requires absolute paths")
    root = Path(payload_root).resolve()
    python = root / "python"
    worker = root / "worker"
    packages = python / "packages"
    _same(sys.executable, Path(interpreter), "interpreter")
    _same(sys.executable, python / "python.exe", "payload interpreter")
    _same(__file__, worker / "mirrorly/worker/payload_launch.py", "bootstrap")
    if (
        sys.version_info[:3] != PYTHON_VERSION
        or struct.calcsize("P") != 8
        or sys.platform != "win32"
    ):
        raise RuntimeError("Payload qualification: wrong Python version/architecture")
    if not (
        sys.flags.isolated
        and sys.flags.ignore_environment
        and sys.flags.no_user_site
        and sys.flags.no_site
        and sys.dont_write_bytecode
    ):
        raise RuntimeError("Payload qualification: Python isolation flags missing")
    expected_path = [python / "python313.zip", python, packages, worker]
    if [Path(p).resolve() for p in sys.path] != [p.resolve() for p in expected_path]:
        raise RuntimeError("Payload qualification: unexpected import search path")
    if "site" in sys.modules:
        raise RuntimeError("Payload qualification: site must not be initialized")

    origins = {}
    # Import the actual execution graph before advertising a qualified worker.
    for name in (
        "mirrorly",
        "mirrorly.application.setup",
        "mirrorly.application.backup",
        "mirrorly.application.restoration",
        "mirrorly.application.queries",
        "mirrorly.snapshot",
        "mirrorly.restore",
        "mirrorly.hashing",
        "mirrorly.worker.host",
    ):
        module = importlib.import_module(name)
        relative = name.replace(".", "/") + ("/__init__.py" if name == "mirrorly" else ".py")
        _same(module.__file__, worker / relative, name)
        origins[name] = module.__file__
    from mirrorly.worker import protocol

    if protocol.VERSION != PROTOCOL_VERSION:
        raise RuntimeError("Payload qualification: protocol version mismatch")
    import blake3
    from blake3 import blake3 as constructor

    extension = importlib.import_module("blake3.blake3")
    _same(blake3.__file__, packages / "blake3/__init__.py", "blake3 package")
    _same(extension.__file__, packages / "blake3/blake3.cp313-win_amd64.pyd", "blake3 extension")
    dist = importlib.metadata.distribution("blake3")
    _same(dist.locate_file(""), packages, "blake3 distribution")
    if dist.version != BLAKE3_VERSION or constructor(b"abc").hexdigest() != BLAKE3_ABC:
        raise RuntimeError("Payload qualification: BLAKE3 version/digest mismatch")
    from mirrorly import hashing

    if (
        hashing.default_algorithm() != "blake3"
        or hashing.new_hasher().hexdigest() != constructor().hexdigest()
    ):
        raise RuntimeError("Payload qualification: BLAKE3 core path unavailable")
    distribution = importlib.metadata.distribution("mirrorly")
    _same(distribution.locate_file(""), worker, "Mirrorly distribution")
    if distribution.read_text("direct_url.json") is not None:
        raise RuntimeError("Payload qualification: editable/direct-url install forbidden")
    # Catch preloaded/outside modules too, not only the deliberately imported names.
    for name, module in tuple(sys.modules.items()):
        origin = getattr(module, "__file__", None)
        if origin and not any(Path(origin).resolve().is_relative_to(p) for p in (python, worker)):
            raise RuntimeError(f"Payload qualification: outside module {name}")
    if "mirrorly.cli" in sys.modules:
        raise RuntimeError("Payload worker must not import CLI")
    return {
        "mode": "payload",
        "executable": sys.executable,
        "pid": os.getpid(),
        "python_version": ".".join(map(str, PYTHON_VERSION)),
        "architecture": "x64",
        "payload_root": str(root),
        "module_origins": origins,
        "blake3_path": extension.__file__,
        "blake3_version": dist.version,
        "blake3_digest": BLAKE3_ABC,
        "protocol_version": protocol.VERSION,
        "search_path": sys.path,
        "site_enabled": False,
        "bytecode_writes": False,
        "cli_imported": False,
    }


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--expected-interpreter", required=True)
    parser.add_argument("--payload-root", required=True)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    try:
        facts = qualify(args.expected_interpreter, args.payload_root)
        if args.probe:
            print(json.dumps(facts, ensure_ascii=True), flush=True)
            return 0
        from mirrorly.worker.launch import serve

        return serve(facts)
    except Exception as exc:
        sys.stderr.write(f"Payload worker startup failed: {type(exc).__name__}: {exc}\n")
        sys.stderr.flush()
        return 3


if __name__ == "__main__":
    os._exit(main())
