"""Build-time only: pinned, hash-checked Python/worker payload. Never run on user startup."""

import argparse
import email
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = ROOT / "desktop/packaging/python-runtime.json"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch(artifact, cache):
    cache.mkdir(parents=True, exist_ok=True)
    name = artifact["filename"]
    if Path(name).name != name or not artifact["url"].startswith("https://"):
        raise ValueError("Invalid pinned artifact")
    path = cache / name
    if not path.exists():
        # A failed download never becomes a reusable cache entry.
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                with urllib.request.urlopen(artifact["url"], timeout=60) as response:
                    shutil.copyfileobj(response, stream)
            except BaseException:
                stream.close()
                temporary.unlink(missing_ok=True)
                raise
        try:
            if digest(temporary) != artifact["sha256"]:
                raise ValueError(f"Artifact SHA-256 mismatch: {name}")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    if digest(path) != artifact["sha256"]:
        raise ValueError(f"Cached artifact SHA-256 mismatch: {name}")
    return path


def extract(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for entry in package.infolist():
            target = (destination / entry.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError("Archive path escapes destination")
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(entry) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)


def assemble(output, cache):
    pins = json.loads(PIN.read_text(encoding="utf-8"))
    output = output.resolve()
    app = output / "app"
    python = app / "python"
    worker = app / "worker"
    if python.exists() or worker.exists():
        raise ValueError("Use fresh staging: Python/worker output already exists")
    runtime = fetch(pins["python"], cache)
    wheels = [fetch(artifact, cache) for artifact in pins["wheels"]]
    extract(runtime, python)
    for wheel in wheels:
        extract(wheel, python / "packages")
    # These are the only allowed import roots; no site, .pth hooks, user or cwd.
    (python / "python313._pth").write_text(
        "python313.zip\n.\npackages\n../worker\n", encoding="ascii"
    )
    with tempfile.TemporaryDirectory(prefix="mirrorly-wheel-", dir=cache) as wheel_dir:
        # Fresh build tree prevents setuptools from retaining deleted modules in build/lib.
        source = Path(wheel_dir) / "source"
        source.mkdir()
        for name in ("pyproject.toml", "README.md", "LICENSE"):
            shutil.copy2(ROOT / name, source / name)
        shutil.copytree(
            ROOT / "src/mirrorly",
            source / "src/mirrorly",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "--no-index",
                "--wheel-dir",
                wheel_dir,
                str(source),
            ],
            check=True,
            cwd=ROOT,
        )
        built = list(Path(wheel_dir).glob("mirrorly-*.whl"))
        if len(built) != 1:
            raise ValueError("Expected exactly one Mirrorly wheel")
        extract(built[0], worker)
    distributions = []
    for location in (python / "packages", worker):
        for metadata in sorted(location.glob("*.dist-info/METADATA")):
            message = email.message_from_bytes(metadata.read_bytes())
            distributions.append(
                {
                    "name": message["Name"],
                    "version": message["Version"],
                    "requires_dist": message.get_all("Requires-Dist", []),
                    "license": message["License-Expression"] or message["License"],
                    "metadata": metadata.relative_to(output).as_posix(),
                    "license_files": [
                        p.relative_to(output).as_posix()
                        for p in sorted(metadata.parent.rglob("*"))
                        if p.is_file() and "license" in p.as_posix().lower()
                    ],
                }
            )
    actual = {(d["name"].lower(), d["version"]) for d in distributions}
    expected = {(p["name"].lower(), p["version"]) for p in pins["wheels"]}
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    if actual != expected | {("mirrorly", version)}:
        raise ValueError("Unexpected staged Python distributions")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True))
    source_files = {
        p.relative_to(worker).as_posix(): digest(p)
        for p in sorted(worker.rglob("*"))
        if p.is_file()
    }
    inventory = {
        "source_commit": commit,
        "source_dirty": dirty,
        "worker_files_sha256": source_files,
        "pins": pins,
        "cpython_license": "app/python/LICENSE.txt",
        "distributions": distributions,
    }
    if not (python / "LICENSE.txt").is_file() or any(not d["license_files"] for d in distributions):
        raise ValueError("Missing staged license inventory")
    (output / "python-inventory.json").write_text(
        json.dumps(inventory, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    command = [
        str(python / "python.exe"),
        "-I",
        "-B",
        "-u",
        str(worker / "mirrorly/worker/payload_launch.py"),
        "--expected-interpreter",
        str(python / "python.exe"),
        "--payload-root",
        str(app),
        "--probe",
    ]
    probe = subprocess.run(command, check=True, capture_output=True, text=True, cwd=cache)
    # Origin evidence belongs in the build log, never persisted as launch configuration.
    print(probe.stdout.strip())
    print(f"PASS: payload qualification and license inventory: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    assemble(args.output, args.cache.resolve())
