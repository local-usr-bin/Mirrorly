"""Fail closed when a regression run imports a different Mirrorly checkout.

Run directly with the intended interpreter, or via tests/conftest.py before collection.
No sys.path/PYTHONPATH injection: the installed environment must resolve the checkout.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = ("mirrorly", "mirrorly.cli", "mirrorly.__main__")
PROBE = """
import importlib, json, sys
from importlib.metadata import distribution
modules = {name: importlib.import_module(name).__file__
           for name in ('mirrorly', 'mirrorly.cli', 'mirrorly.__main__')}
origin = distribution('mirrorly').read_text('direct_url.json')
print(json.dumps({'executable': sys.executable, 'modules': modules,
                  'origin': json.loads(origin) if origin else None}))
"""


def require_checkout(modules: dict[str, str], root: Path = ROOT) -> None:
    for name in MODULES:
        filename = "__init__.py" if name == "mirrorly" else name.rsplit(".", 1)[1] + ".py"
        expected = (root / "src" / "mirrorly" / filename).resolve()
        actual = Path(modules[name]).resolve()
        if actual != expected:
            raise RuntimeError(f"Wrong Mirrorly checkout: {name} = {actual}; expected {expected}")


def _run(arguments: list[str], *, env=None) -> subprocess.CompletedProcess:
    result = subprocess.run(
        arguments, cwd=ROOT, env=env, capture_output=True, encoding="utf-8", timeout=30
    )
    if result.returncode:
        raise RuntimeError(f"Checkout qualification failed: {arguments!r}\n{result.stderr}")
    return result


def qualify_checkout() -> dict:
    require_checkout({name: importlib.import_module(name).__file__ for name in MODULES})
    probes = {}
    # Check both the existing CLI/E2E startup and the isolated worker-style startup.
    for label, flags in (("normal", []), ("isolated", ["-I"])):
        probe = json.loads(_run([sys.executable, *flags, "-B", "-c", PROBE]).stdout)
        require_checkout(probe["modules"])
        if Path(probe["executable"]).resolve() != Path(sys.executable).resolve():
            raise RuntimeError("CLI subprocess used a different Python interpreter")
        origin = probe["origin"]
        if origin and origin.get("dir_info", {}).get("editable"):
            if origin["url"].rstrip("/").casefold() != ROOT.as_uri().casefold():
                raise RuntimeError(f"Wrong editable origin: {origin!r}; expected {ROOT.as_uri()}")
        probes[label] = probe

    # Trace actual entry-point execution, not just --version (identical across checkouts).
    entries = {"python -m mirrorly": [sys.executable, "-I", "-B", "-v", "-m", "mirrorly"]}
    console = Path(sys.executable).parent / "Scripts" / "mirrorly.exe"
    if console.exists():
        entries["console"] = [str(console)]
    traces = {}
    for label, command in entries.items():
        result = _run(
            [*command, "--version"],
            env={**os.environ, "PYTHONVERBOSE": "1", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        for filename in ("__init__.py", "__main__.py", "cli.py"):
            expected = str(ROOT / "src" / "mirrorly" / filename)
            # CPython prints source paths directly or repr-escaped beside cached bytecode.
            if not any(
                p.casefold() in result.stderr.casefold() for p in (expected, repr(expected)[1:-1])
            ):
                raise RuntimeError(f"{label} did not load {expected}; trace:\n{result.stderr}")
        traces[label] = {"command": command, "version": result.stdout.strip()}
    return {"root": str(ROOT), "probes": probes, "entry_points": traces}


if __name__ == "__main__":
    print(json.dumps(qualify_checkout(), indent=2, ensure_ascii=False))
