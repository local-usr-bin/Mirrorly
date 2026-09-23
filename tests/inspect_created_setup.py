"""Read-only subprocess oracle for C# mutation tests; use Python's real readers."""

import argparse
import json
import os
import sys
from pathlib import Path

from mirrorly.config import load_task_config
from mirrorly.repo import load_repo
from mirrorly.volume import get_volume_mount_root
from mirrorly.worker.launch import qualify

parser = argparse.ArgumentParser()
for field in (
    "expected-interpreter",
    "expected-checkout",
    "source",
    "target",
    "config-root",
    "task-name",
):
    parser.add_argument(f"--{field}", required=True)
args = parser.parse_args()
qualify(args.expected_interpreter, args.expected_checkout)
path = Path(args.config_root) / "config.d" / f"{args.task_name}.toml"
task = load_task_config(path)
repo = load_repo(args.target)
assert task.name == args.task_name
assert task.source == str(Path(args.source).resolve())
assert task.target_path == str(Path(args.target).resolve())
assert task.repo_id == repo.repo_id and task.volume_guid == repo.volume.guid
relative = os.path.relpath(task.target_path, get_volume_mount_root(task.target_path))
assert task.repo_dir == ("." if relative == "." else relative.replace("/", "\\"))
assert not list((repo.path / "snapshots").iterdir())
assert "mirrorly.cli" not in sys.modules
print(json.dumps({"repo_id": repo.repo_id, "config_path": str(path), "source": task.source}))
