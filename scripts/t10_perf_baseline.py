"""T-10 性能基线（MVP_TASKS T-10 验收标准 #4）。

非优化任务：只留下今后可比较的 baseline。冻结要求「10 GB 级混合数据集
（含 >4 GB 大文件、万级小文件）备份耗时与空间占用记录在案」。

两种 workload：

- A（mixed，冻结口径）：>4 GB 大文件 + 万级小文件 + 中型文件，总 ~9.2 GB
- B（fewer-larger）：16 × 256 MiB，总 4 GB

测量项：首次备份 / unchanged 二次备份 / 小变更三次备份 / full verify /
quick verify / 整快照 restore 的耗时；bytes_written（备份报告）；
hardlink 复用文件数（报告 linked）；各阶段卷剩余空间（物理占用变化）。

用法：
    python scripts/t10_perf_baseline.py --workload A --out <result.json>

注意：本脚本自身清理使用普通路径（OS 临时目录内），产品内部无 counted
删除操作（默认 keep_last=30 不触发 retention）。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

PY = sys.executable
CONSOLE = Path(sys.executable).parent / "Scripts" / "mirrorly.exe"
CLI: list[str] = [str(CONSOLE)] if CONSOLE.exists() else [PY, "-m", "mirrorly"]
ENV = {**os.environ, "PYTHONUTF8": "1"}
MiB = 1024 * 1024


def _run(*args: str, timeout: int = 3600) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [*CLI, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=ENV,
        timeout=timeout,
    )
    return proc


def _free_gb(path: Path) -> float:
    return shutil.disk_usage(str(path)).free / 2**30


def _lp(path: Path | str) -> str:
    """绝对路径直接返回（性能脚本不构造 >260 路径）。"""
    return str(path)


def _write_streaming(path: Path, size: int, seed_note: str = "") -> None:
    os.makedirs(_lp(path.parent), exist_ok=True)
    block = os.urandom(4 * MiB)
    remaining = size
    with open(_lp(path), "wb") as f:
        while remaining > 0:
            n = min(remaining, len(block))
            f.write(block[:n])
            remaining -= n
    del block


def _latest_backup_report(repo: Path) -> dict:
    reports = sorted((repo / "logs").glob("backup-*.json"))
    if not reports:
        raise RuntimeError("未找到备份报告")
    return json.loads(reports[-1].read_text(encoding="utf-8"))


def _snapshot_count(repo: Path) -> int:
    return len(list((repo / "manifests").glob("*.json")))


def build_workload(kind: str, src: Path) -> dict:
    """构造数据集，返回构成描述。"""
    spec: dict = {"kind": kind}
    t0 = time.perf_counter()
    if kind == "A":
        _write_streaming(src / "big" / "huge.bin", int(4.5 * 1024 * MiB))
        spec["big_file_gb"] = 4.5
        small_root = src / "small"
        for i in range(10_000):
            _write_streaming(small_root / f"{i // 100:03d}" / f"f{i:05d}.bin", 256 * 1024)
        spec["small_files"] = 10_000
        for i in range(4):
            _write_streaming(src / "medium" / f"m{i}.bin", 512 * MiB)
        spec["medium_files"] = 4
        (src / "docs").mkdir(exist_ok=True)
        (src / "docs" / "说明.md").write_text("性能基线数据集\n", encoding="utf-8")
        (src / "empty_dir").mkdir(exist_ok=True)
    elif kind == "B":
        for i in range(16):
            _write_streaming(src / f"large-{i:02d}" / "data.bin", 256 * MiB)
        spec["large_files"] = 16
    else:
        raise ValueError(kind)
    n_files = sum(len(fs) for _, _, fs in os.walk(str(src)))
    logical = sum(
        os.stat(os.path.join(dp, f)).st_size for dp, _, fs in os.walk(str(src)) for f in fs
    )
    spec["total_files"] = n_files
    spec["logical_bytes"] = logical
    spec["dataset_gen_seconds"] = round(time.perf_counter() - t0, 1)
    return spec


def volume_info(path: Path) -> dict:
    import ctypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    buf = ctypes.create_unicode_buffer(261)
    fsbuf = ctypes.create_unicode_buffer(261)
    serial = wintypes.DWORD()
    ok = k32.GetVolumeInformationW(
        str(path.anchor), buf, 261, ctypes.byref(serial), None, None, fsbuf, 261
    )
    return {
        "root": str(path.anchor),
        "label": buf.value if ok else "unknown",
        "filesystem": fsbuf.value if ok else "unknown",
        "serial": f"{serial.value:08X}" if ok else "unknown",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workload", choices=["A", "B"], required=True)
    ap.add_argument("--out", required=True, help="结果 JSON 输出路径")
    args = ap.parse_args()

    base = Path(os.environ["LOCALAPPDATA"]) / "Temp" / "mirrorly_t10_perf" / args.workload
    if base.exists():
        shutil.rmtree(str(base))  # 普通路径 + OS 临时目录：设计内豁免
    src = base / "src"
    cfg_root = base / "cfg"
    target = base / "target"
    repo = target / "MirrorlyRepo"
    src.mkdir(parents=True)

    result: dict = {
        "workload": args.workload,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "mirrorly": _run("--version").stdout.strip(),
            "cwd_git_rev": subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).parent.parent),
            ).stdout.strip(),
            "volume": volume_info(base),
            "free_gb_before": round(_free_gb(base), 2),
            "scratch": str(base),
        },
    }
    spec = build_workload(args.workload, src)
    result["dataset"] = spec
    result["environment"]["free_gb_after_dataset"] = round(_free_gb(base), 2)

    # init
    t0 = time.perf_counter()
    r = _run(
        "--config", str(cfg_root), "init", "--source", str(src), "--target", str(target), "--yes"
    )
    assert r.returncode == 0, r.stdout + r.stderr
    result["init_seconds"] = round(time.perf_counter() - t0, 2)

    def timed_backup(label: str, mutate=None) -> dict:
        if mutate:
            mutate()
        t = time.perf_counter()
        r = _run("--config", str(cfg_root), "backup", "--yes")
        assert r.returncode == 0, f"[{label}] " + r.stdout + r.stderr
        dt = time.perf_counter() - t
        rep = _latest_backup_report(repo)
        return {
            "seconds": round(dt, 2),
            "snapshot_id": rep["snapshot_id"],
            "bytes_written": rep["bytes_written"],
            "linked_files": len(rep["linked"]),
            "copied_files": len(rep["copied"]),
            "free_gb_after": round(_free_gb(base), 2),
        }

    # backup #1：全量
    result["backup1_full"] = timed_backup("backup1")
    # backup #2：unchanged
    result["backup2_unchanged"] = timed_backup("backup2")

    # backup #3：小变更（+100 小文件 / 1 中型文件改写）
    def mutate() -> None:
        if args.workload == "A":
            for i in range(100):
                _write_streaming(src / "small" / "chg" / f"c{i:03d}.bin", 256 * 1024)
            _write_streaming(src / "medium" / "m0.bin", 512 * MiB)
        else:
            _write_streaming(src / "large-00" / "data.bin", 256 * MiB)

    result["backup3_small_change"] = timed_backup("backup3", mutate)

    def timed_cmd(label: str, *cli_args: str) -> dict:
        t = time.perf_counter()
        r = _run("--config", str(cfg_root), *cli_args)
        assert r.returncode == 0, f"[{label}] " + r.stdout + r.stderr
        return {
            "seconds": round(time.perf_counter() - t, 2),
            "free_gb_after": round(_free_gb(base), 2),
        }

    result["verify_full_all"] = timed_cmd("verify", "verify", "--all")
    result["verify_quick_all"] = timed_cmd("verify-quick", "verify", "--all", "--quick")
    # restore 整快照到空目录
    sid = result["backup3_small_change"]["snapshot_id"]
    dest = base / "restore"
    t = time.perf_counter()
    r = _run("--config", str(cfg_root), "restore", "--snapshot", sid, "--to", str(dest), "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    result["restore_full"] = {
        "seconds": round(time.perf_counter() - t, 2),
        "free_gb_after": round(_free_gb(base), 2),
    }
    result["snapshots_kept"] = _snapshot_count(repo)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 清理（普通路径，OS 临时目录豁免；保留结果 JSON）
    shutil.rmtree(str(base))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
