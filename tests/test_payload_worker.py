"""Real isolated CPython payload tests. Set MIRRORLY_TEST_PAYLOAD to an assembled P2 root."""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_worker import Peer


@pytest.fixture
def payload(tmp_path):
    supplied = os.environ.get("MIRRORLY_TEST_PAYLOAD")
    if not supplied:
        pytest.skip(
            "P2 artifact test: build with Build-WorkerPoC.ps1 and set MIRRORLY_TEST_PAYLOAD"
        )
    original = Path(supplied)
    assert (original / "app/python/python.exe").is_file(), "Invalid P2 test artifact"
    destination = tmp_path / "中文 软件" / "app"
    for name in ("python", "worker"):
        shutil.copytree(original / "app" / name, destination / name)
    return destination


def command(app, *, probe=True):
    exe = str(app / "python/python.exe")
    result = [
        exe,
        "-I",
        "-B",
        "-u",
        str(app / "worker/mirrorly/worker/payload_launch.py"),
        "--expected-interpreter",
        exe,
        "--payload-root",
        str(app),
    ]
    return result + (["--probe"] if probe else [])


def run(app, **kwargs):
    return subprocess.run(command(app), capture_output=True, text=True, timeout=20, **kwargs)


def test_real_isolated_runtime_hostile_environment_and_readonly_files(payload, tmp_path):
    fake = tmp_path / "hostile"
    fake.mkdir()
    (fake / "mirrorly.py").write_text("raise RuntimeError('outside mirrorly loaded')")
    (fake / "sitecustomize.py").write_text("raise RuntimeError('site loaded')")
    env = {
        **os.environ,
        "PATH": str(Path(sys.executable).parent),
        "PYTHONPATH": str(fake),
        "PYTHONHOME": str(fake),
        "PYTHONUSERBASE": str(fake),
        "VIRTUAL_ENV": str(fake),
        "CONDA_PREFIX": str(fake),
        "PYTHONSTARTUP": str(fake / "sitecustomize.py"),
    }
    files = [p for p in payload.rglob("*") if p.is_file()]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    try:
        for file in files:
            file.chmod(0o444)
        result = run(payload, cwd=fake, env=env)
        assert result.returncode == 0, result.stderr
        facts = json.loads(result.stdout)
        assert facts["python_version"] == "3.13.15" and facts["architecture"] == "x64"
        assert facts["site_enabled"] is False and facts["bytecode_writes"] is False
        assert (
            facts["blake3_digest"]
            == "6437b3ac38465133ffb63b75273a8db548c558465d79db03fd359c6cd5bd9d85"
        )
        assert all(Path(p).is_relative_to(payload) for p in facts["module_origins"].values())
        assert Path(facts["blake3_path"]).is_relative_to(payload / "python/packages")
        assert not list(payload.rglob("__pycache__"))
        assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    finally:
        for file in files:
            file.chmod(0o666)


@pytest.mark.parametrize(
    "fault",
    [
        "missing_blake3",
        "unloadable_blake3",
        "wrong_protocol",
        "wrong_python_version",
        "outside_import_path",
        "editable_metadata",
        "wrong_worker_root",
        "wrong_interpreter",
        "missing_worker",
        "missing_python",
        "site_enabled",
    ],
)
def test_bad_payload_fails_before_any_hello(payload, tmp_path, fault):
    worker = payload / "worker/mirrorly"
    extension = payload / "python/packages/blake3/blake3.cp313-win_amd64.pyd"
    args = command(payload, probe=False)
    if fault == "missing_blake3":
        extension.unlink()
    elif fault == "unloadable_blake3":
        extension.write_bytes(b"invalid native extension")
    elif fault == "wrong_protocol":
        protocol = worker / "worker/protocol.py"
        protocol.write_text(protocol.read_text().replace('"major": 1', '"major": 999'))
    elif fault == "wrong_python_version":
        launch = worker / "worker/payload_launch.py"
        launch.write_text(launch.read_text().replace("(3, 13, 15)", "(3, 13, 0)"))
    elif fault == "outside_import_path":
        pth = payload / "python/python313._pth"
        pth.write_text(pth.read_text() + str(tmp_path) + "\n")
    elif fault == "editable_metadata":
        metadata = next((payload / "worker").glob("mirrorly-*.dist-info"))
        (metadata / "direct_url.json").write_text('{"dir_info":{"editable":true}}')
    elif fault == "wrong_worker_root":
        args[-1] = str(tmp_path)
    elif fault == "wrong_interpreter":
        args[args.index("--expected-interpreter") + 1] = sys.executable
    elif fault == "missing_worker":
        (worker / "application/setup.py").unlink()
    elif fault == "missing_python":
        (payload / "python/python.exe").unlink()
        with pytest.raises(FileNotFoundError):
            subprocess.run(args, check=True)
        return
    elif fault == "site_enabled":
        pth = payload / "python/python313._pth"
        pth.write_text(pth.read_text() + "import site\n")
    result = subprocess.run(args, input=b"", capture_output=True, timeout=20)
    assert result.returncode == 3
    assert not result.stdout, "Unqualified worker advertised a hello"
    assert b"Payload worker startup failed" in result.stderr


@pytest.mark.parametrize("module", ["mirrorly", "mirrorly.application.backup", "blake3.blake3"])
def test_actually_imported_outside_module_is_rejected(payload, tmp_path, module):
    # Actual outside module executed in the subprocess, without mocking qualify().
    outside = tmp_path / "outside.py"
    outside.write_text("outside = True\n")
    code = """
import importlib.util,sys
from mirrorly.worker.payload_launch import qualify
importlib.import_module(sys.argv[3])
spec=importlib.util.spec_from_file_location(sys.argv[3],sys.argv[4])
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.modules[sys.argv[3]]=module
qualify(sys.executable,sys.argv[1])
"""
    result = subprocess.run(
        [
            str(payload / "python/python.exe"),
            "-I",
            "-B",
            "-c",
            code,
            str(payload),
            "unused",
            module,
            str(outside),
        ],
        capture_output=True,
        timeout=20,
    )
    assert result.returncode != 0 and b"qualification" in result.stderr


def test_payload_real_worker_protocol_and_relocation(payload, tmp_path):
    for app in (payload, tmp_path / "新的 位置" / "app"):
        if app != payload:
            app.parent.mkdir()
            shutil.move(str(payload), str(app))
        child = subprocess.Popen(
            command(app, probe=False),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=tmp_path,
        )
        peer = Peer(child)
        try:
            facts = peer.hello["payload"]["qualification"]
            assert facts["pid"] == child.pid
            assert Path(facts["executable"]) == app / "python/python.exe"
            peer.initialize()
            peer.send("ping")
            assert peer.receive()["payload"]["result"]["reply"] == "pong"
            peer.send("status")
            assert peer.receive()["payload"]["error"] is None
            peer.send("tasks.list", {"config_root": str(tmp_path / "user-data")})
            accepted = peer.receive()
            assert accepted["payload"]["phase"] == "accepted"
            assert peer.receive()["payload"]["error"] is None
            peer.send("worker.shutdown")
            assert peer.receive()["payload"]["error"] is None
        finally:
            peer.close()
        assert not peer.errors


def test_build_cache_mismatch_is_not_trusted_or_replaced(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "payload_build", Path(__file__).resolve().parents[1] / "scripts/build_worker_payload.py"
    )
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    file = tmp_path / "pinned.zip"
    file.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        build.fetch(
            {
                "filename": file.name,
                "url": "https://example.invalid/pinned.zip",
                "sha256": "0" * 64,
            },
            tmp_path,
        )
    assert file.read_bytes() == b"wrong"
