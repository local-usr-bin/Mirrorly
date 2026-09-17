"""T-10 端到端验收（MVP_TASKS T-10 / CLI_SPEC v1.0 / TR-5 / TR-7 / M10）。

与 test_cli.py 的区别：本文件全部通过真实进程入口（console script 或
``python -m mirrorly``）+ 真实 NTFS 文件系统执行，不 mock 业务逻辑、
不直接调用 library API 做主流程。测试辅助（数据集生成、目录指纹、
进程轮询、长路径前缀）不属于业务逻辑。

验收矩阵（对应 docs/MVP_ACCEPTANCE.md）：

- 主流程：init → backup#1 → verify → 变更（增/删/改/保留）→ dry-run
  （零写入）→ backup#2（immutability + hardlink 复用）→ list
- restore：整快照字节级一致、--path 文件/子树、overwrite never/always
- verify：主动损坏 → exit 4、报告定位
- TR-5 中断恢复：真实 subprocess kill → incomplete → 续传（REAL E2E）
- M10 卷身份：错误 serial → exit 5 零写入；盘符漂移自动重定位（真实 GUID
  链路 + 配置 path 失联仿真）；GUID 未挂载零写入；仓库缺失不自动 init
- retention：keep_last=2 多快照清理后 verify 全过
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from mirrorly.scan import to_long_path

# ---------------------------------------------------------------------------
# 真实 CLI 入口
# ---------------------------------------------------------------------------

_CONSOLE = Path(sys.executable).parent / "Scripts" / "mirrorly.exe"
#: 主流程一律用 console script（真实 entry point）；无法安装时回退
#: python -m mirrorly（CLI_SPEC §0：两者等价）。
CLI: list[str] = [str(_CONSOLE)] if _CONSOLE.exists() else [sys.executable, "-m", "mirrorly"]

_ENV = {**os.environ, "PYTHONUTF8": "1"}


def run_cli(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    """真实 CLI 子进程（UTF-8 输出，避免管道下 GBK 乱码）。"""
    return subprocess.run(
        [*CLI, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_ENV,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# 测试辅助（非业务逻辑）
# ---------------------------------------------------------------------------


def _lp(path: Path | str) -> str:
    r"""长路径安全访问：\\?\ 前缀（与产品 to_long_path 同源，供测试读/写）。"""
    return to_long_path(path)


def _read_task_config(cfg_file: Path) -> dict:
    """解析 config.d TOML（路径在 TOML basic string 中是转义存储的，不能做子串断言）。"""
    import tomllib

    return tomllib.loads(cfg_file.read_text(encoding="utf-8"))


def _volume_serial(path: Path) -> str:
    """读取路径所在卷的卷序列号（真实 GetVolumeInformationW）。"""
    import ctypes

    root = str(path.anchor)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    buf = ctypes.create_unicode_buffer(261)
    serial = wintypes.DWORD()
    if not k32.GetVolumeInformationW(root, buf, 261, ctypes.byref(serial), None, None, None, 261):
        raise OSError(f"GetVolumeInformationW 失败: {root}")
    return f"{serial.value:08X}"


def _tree_fingerprint(root: Path) -> dict:
    """目录树指纹：相对路径 → (size, mtime_ns, sha256)；含目录集合。

    用于「零写入」「快照不可变」逐字节断言（mtime 变化即视为被写）。
    """
    files: dict[str, tuple] = {}
    dirs: set[str] = set()
    root_str = _lp(root)
    for dirpath, _dirnames, filenames in os.walk(root_str):
        rel_dir = os.path.relpath(dirpath, root_str)
        if rel_dir != ".":
            dirs.add(rel_dir.replace("\\", "/"))
        for name in filenames:
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, root_str).replace("\\", "/")
            st = os.stat(p)
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            files[rel] = (st.st_size, st.st_mtime_ns, h.hexdigest())
    return {"files": files, "dirs": dirs}


def _manifest_ids(repo: Path) -> list[str]:
    return sorted(p.stem for p in (repo / "manifests").glob("*.json"))


def _sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(_lp(path), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_dataset(root: Path) -> dict:
    """小型真实数据集：文本/二进制/多层/空目录/Unicode/emoji/长路径。

    返回 {相对路径: 内容}，供备份/恢复对账。unchanged 文件用于观察
    硬链接复用；to_modify/to_delete 供 backup#2 变更场景。
    """
    root.mkdir(parents=True, exist_ok=True)
    entries: dict[str, bytes] = {}

    def put(rel: str, data: bytes) -> None:
        p = root / rel
        pd = _lp(p.parent)
        os.makedirs(pd, exist_ok=True)
        with open(_lp(p), "wb") as f:
            f.write(data)
        entries[rel.replace("\\", "/")] = data

    put("docs/readme.txt", b"hello mirrorly\n")
    put("docs/notes/计划.md", "中文内容：项目计划\n".encode())
    put("docs/notes/emoji-🎉.txt", "emoji 文件名 🎉\n".encode())
    put("bin/blob.bin", bytes(range(256)) * 4096)  # 1 MiB 二进制
    put("bin/to_modify.bin", b"M" * 300_000)
    put("docs/to_delete.txt", b"delete me after backup #1\n")
    put("photos/unchanged/big.bin", os.urandom(4 * 1024 * 1024))  # 4 MiB 硬链接观察位
    put("photos/unchanged/small.txt", b"unchanged small file\n")
    os.mkdir(_lp(root / "docs" / "empty_dir"))  # 空目录
    os.mkdir(_lp(root / "photos" / "空目录"))  # Unicode 空目录

    # 长路径（> 260 字符）：逐层填充直到最终路径超过 260
    deep = root / "longpath"
    seg = "d" * 80
    while len(str(deep)) < 240:
        deep = deep / seg
    put(str(deep.relative_to(root)) + "/deep-file.txt", b"deep long path content\n")

    return entries


def _compare_trees(a: Path, b: Path) -> list[str]:
    """递归比较两棵树：文件集合/字节/目录集合，返回差异列表（空即一致）。"""
    fa = _tree_fingerprint(a)
    fb = _tree_fingerprint(b)
    diffs: list[str] = []
    for rel in sorted(set(fa["files"]) | set(fb["files"])):
        if rel not in fa["files"]:
            diffs.append(f"仅 {b.name} 有: {rel}")
        elif rel not in fb["files"]:
            diffs.append(f"仅 {a.name} 有: {rel}")
        elif fa["files"][rel][2] != fb["files"][rel][2]:
            diffs.append(f"字节不一致: {rel}")
    for rel in sorted(fa["dirs"] ^ fb["dirs"]):
        diffs.append(f"目录集合不一致: {rel}")
    return diffs


# ---------------------------------------------------------------------------
# A–F 主流程：init → backup#1 → verify → 变更 → dry-run → backup#2 → list
# （单一场景逐阶段推进：任一步失败即终止验收，assert 消息标明阶段）
# ---------------------------------------------------------------------------


class TestMainLifecycle:
    def test_full_lifecycle(self, tmp_path) -> None:
        src = tmp_path / "src"
        cfg_root = tmp_path / "cfg"
        target = tmp_path / "target"
        repo = target / "MirrorlyRepo"
        dataset = _make_dataset(src)

        def cli(*args: str) -> subprocess.CompletedProcess:
            return run_cli("--config", str(cfg_root), *args)

        # ---- A. init ----
        proc = cli("init", "--source", str(src), "--target", str(target), "--yes")
        assert proc.returncode == 0, "[A init] " + proc.stdout + proc.stderr
        assert (repo / "repo.json").is_file(), "[A init] repo.json 缺失"
        info = json.loads((repo / "repo.json").read_text(encoding="utf-8"))
        assert info["volume"]["serial"] == _volume_serial(target), "[A init] 卷身份记录错误"
        assert info["hash_algorithm"] == "blake3"
        cfg_file = cfg_root / "config.d" / "default.toml"
        assert cfg_file.is_file()
        cfg_data = _read_task_config(cfg_file)
        assert cfg_data["task"]["source"] == str(src.resolve())
        assert cfg_data["target"]["path"] == str(target.resolve())
        assert {p.name for p in repo.iterdir()} == {
            "snapshots",
            "manifests",
            "manifests.tmp",
            "locks",
            "logs",
            "lifecycle.json",
            "repo.json",
        }
        assert list((repo / "snapshots").iterdir()) == []

        # ---- B. backup #1 + verify full ----
        proc = cli("backup", "--yes")
        assert proc.returncode == 0, "[B backup#1] " + proc.stdout + proc.stderr
        ids = _manifest_ids(repo)
        assert len(ids) == 1, "[B backup#1] 快照数异常"
        s1 = ids[0]
        m1 = json.loads((repo / "manifests" / f"{s1}.json").read_text(encoding="utf-8"))
        assert m1["status"] == "complete", "[B backup#1] manifest 非 complete"
        n_files = sum(1 for e in m1["entries"] if e.get("type") != "dir")
        # 长路径安全遍历（普通 rglob 看不到 >260 路径的 deep-file.txt）
        snap_files = _tree_fingerprint(repo / "snapshots" / s1)["files"]
        assert n_files == len(dataset) == len(snap_files), "[B backup#1] 条目数不对应"
        reports = list((repo / "logs").glob(f"backup-{s1}*.json"))
        assert len(reports) == 1, "[B backup#1] 报告缺失"
        report = json.loads(reports[0].read_text(encoding="utf-8"))
        assert report["status"] == "complete" and report["bytes_written"] > 0
        proc = cli("verify", "--all")
        assert proc.returncode == 0, "[B backup#1 verify] " + proc.stdout + proc.stderr
        s1_fp = _tree_fingerprint(repo / "snapshots" / s1)
        m1_sha = _sha256_file(repo / "manifests" / f"{s1}.json")

        # ---- C. 数据集变更：新增 + 修改 + 删除 + 目录结构变化 ----
        (src / "new_file.txt").write_bytes(b"newly added\n")
        (src / "bin" / "to_modify.bin").write_bytes(b"X" * 350_000)
        (src / "docs" / "to_delete.txt").unlink()
        (src / "photos" / "unchanged_copy").mkdir()
        (src / "photos" / "unchanged_copy" / "moved.txt").write_bytes(
            (src / "photos" / "unchanged" / "small.txt").read_bytes()
        )

        # ---- D. dry-run：正确预览 + 零持久写入 ----
        before = _tree_fingerprint(repo)
        proc = cli("backup", "--dry-run")
        assert proc.returncode == 0, "[D dry-run] " + proc.stdout + proc.stderr
        assert "新增 2 / 修改 1 / 删除 1" in (proc.stdout + proc.stderr), "[D dry-run] 统计预览异常"
        assert _tree_fingerprint(repo) == before, "[D dry-run] 产生了持久写入"
        assert not list((repo / "locks").glob("*.lock")), "[D dry-run] 产生锁文件"
        # --json 明细（stdout 单一 JSON 文档；文件级列表见明细）
        proc = cli("backup", "--dry-run", "--json")
        assert proc.returncode == 0, "[D dry-run json] " + proc.stdout + proc.stderr
        preview = json.loads(proc.stdout)
        assert preview["dry_run"] is True
        assert set(preview["changes"]["added"]) >= {
            "new_file.txt",
            "photos/unchanged_copy/moved.txt",
        }
        assert preview["changes"]["modified"] == ["bin/to_modify.bin"]
        assert preview["changes"]["deleted"] == ["docs/to_delete.txt"]
        assert _tree_fingerprint(repo) == before, "[D dry-run json] 产生了持久写入"

        # ---- E. backup #2：immutability + hardlink 复用 ----
        proc = cli("backup", "--yes")
        assert proc.returncode == 0, "[E backup#2] " + proc.stdout + proc.stderr
        ids = _manifest_ids(repo)
        assert len(ids) == 2, "[E backup#2] 快照数异常"
        s2 = [i for i in ids if i != s1][0]
        for i in ids:
            m = json.loads((repo / "manifests" / f"{i}.json").read_text(encoding="utf-8"))
            assert m["status"] == "complete", f"[E backup#2] {i} 非 complete"
        # 旧快照不可变：文件树 + manifest 逐字节不变
        assert _tree_fingerprint(repo / "snapshots" / s1) == s1_fp, "[E] 旧快照被改动"
        assert _sha256_file(repo / "manifests" / f"{s1}.json") == m1_sha, "[E] 旧 manifest 被改动"
        m2 = json.loads((repo / "manifests" / f"{s2}.json").read_text(encoding="utf-8"))
        paths2 = {e["path"] for e in m2["entries"]}
        assert "docs/to_delete.txt" not in paths2, "[E] 删除文件仍在新快照"
        assert "new_file.txt" in paths2, "[E] 新增文件缺失"
        # unchanged 硬链接复用：同一 file index（NTFS st_ino）且 nlink ≥ 2
        rel = "photos/unchanged/big.bin"
        st1 = os.stat(_lp(repo / "snapshots" / s1 / rel))
        st2 = os.stat(_lp(repo / "snapshots" / s2 / rel))
        assert st1.st_ino == st2.st_ino, "[E] unchanged 文件未硬链接复用"
        assert st2.st_nlink >= 2, "[E] nlink < 2"
        rel_m = "bin/to_modify.bin"
        assert (
            os.stat(_lp(repo / "snapshots" / s2 / rel_m)).st_ino
            != os.stat(_lp(repo / "snapshots" / s1 / rel_m)).st_ino
        ), "[E] 修改文件应真实复制"
        # 新快照内容与源字节级一致
        for e in m2["entries"]:
            if e.get("type") != "dir":
                assert _sha256_file(src / e["path"]) == _sha256_file(
                    repo / "snapshots" / s2 / e["path"]
                ), f"[E] 新快照内容不一致: {e['path']}"
        proc = cli("verify", "--all")
        assert proc.returncode == 0, "[E verify] " + proc.stdout + proc.stderr
        rep = json.loads(
            list((repo / "logs").glob(f"backup-{s2}*.json"))[0].read_text(encoding="utf-8")
        )
        assert rel in rep["linked"], "[E] 报告 linked 缺 unchanged 文件"
        assert rep["bytes_written"] < 1_000_000, "[E] 写入量异常（应仅新增/修改项）"

        # ---- F. list：普通 / --verbose / --json ----
        proc = cli("list")
        assert proc.returncode == 0
        assert all(i in proc.stdout for i in ids) and "complete" in proc.stdout
        proc = cli("list", "--verbose")
        assert proc.returncode == 0
        assert "文件" in proc.stdout and "目录" in proc.stdout and "共" in proc.stdout
        proc = cli("list", "--json")
        assert proc.returncode == 0
        payload = json.loads(proc.stdout)  # stdout 是单一 JSON 文档
        assert isinstance(payload, list) and len(payload) == 2
        for item in payload:
            assert {"snapshot_id", "status", "created_at", "stats"} <= set(item)
            assert {"files", "dirs", "total_bytes"} <= set(item["stats"])


# ---------------------------------------------------------------------------
# G restore 验收（每用例独立仓库，互不污染）
# ---------------------------------------------------------------------------


class TestRestore:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path) -> None:
        self.src = tmp_path / "src"
        self.cfg_root = tmp_path / "cfg"
        self.target = tmp_path / "target"
        self.dataset = _make_dataset(self.src)
        r = run_cli(
            "--config",
            str(self.cfg_root),
            "init",
            "--source",
            str(self.src),
            "--target",
            str(self.target),
            "--yes",
        )
        assert r.returncode == 0, r.stdout + r.stderr
        r = run_cli("--config", str(self.cfg_root), "backup", "--yes")
        assert r.returncode == 0, r.stdout + r.stderr
        self.repo = self.target / "MirrorlyRepo"
        self.sid = _manifest_ids(self.repo)[0]
        self.fp_before = _tree_fingerprint(self.repo / "snapshots")
        self.dest_root = tmp_path / "dest"

    def test_full_restore_to_empty_dir(self) -> None:
        dest = self.dest_root / "full"
        proc = run_cli(
            "--config",
            str(self.cfg_root),
            "restore",
            "--snapshot",
            self.sid,
            "--to",
            str(dest),
            "--yes",
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        # 字节级一致（含 Unicode/emoji、深层目录、空目录、长路径）
        assert _compare_trees(self.src, dest) == []

    def test_path_file_and_subtree(self) -> None:
        dest = self.dest_root / "file"
        proc = run_cli(
            "--config",
            str(self.cfg_root),
            "restore",
            "--snapshot",
            self.sid,
            "--to",
            str(dest),
            "--path",
            "docs/notes/计划.md",
            "--yes",
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert (dest / "docs" / "notes" / "计划.md").is_file()
        # 只恢复选定文件：不出现计划外的顶层条目
        assert {p.name for p in dest.iterdir()} == {"docs"}

        dest2 = self.dest_root / "subtree"
        proc = run_cli(
            "--config",
            str(self.cfg_root),
            "restore",
            "--snapshot",
            self.sid,
            "--to",
            str(dest2),
            "--path",
            "photos",
            "--yes",
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert (dest2 / "photos" / "unchanged" / "big.bin").is_file()
        assert (dest2 / "photos" / "空目录").is_dir()
        assert not (dest2 / "docs").exists()

    def test_overwrite_never_and_always(self) -> None:
        dest = self.dest_root / "ow"
        (dest / "docs").mkdir(parents=True)
        (dest / "docs" / "readme.txt").write_bytes(b"USER DATA - must survive")
        # never：已有用户文件不覆盖（skip → partial 3），用户字节不动
        proc = run_cli(
            "--config",
            str(self.cfg_root),
            "restore",
            "--snapshot",
            self.sid,
            "--to",
            str(dest),
            "--yes",
        )
        assert proc.returncode == 3
        assert (dest / "docs" / "readme.txt").read_bytes() == b"USER DATA - must survive"
        # always：覆盖为快照内容（已存在目录计入 skip → exit 3 属冻结
        # partial 语义；文件全部按计划覆盖）
        proc = run_cli(
            "--config",
            str(self.cfg_root),
            "restore",
            "--snapshot",
            self.sid,
            "--to",
            str(dest),
            "--overwrite",
            "always",
            "--yes",
        )
        assert proc.returncode in (0, 3), proc.stdout + proc.stderr
        assert (dest / "docs" / "readme.txt").read_bytes() == self.dataset["docs/readme.txt"]

    def test_source_snapshot_unmodified_after_restores(self) -> None:
        assert _tree_fingerprint(self.repo / "snapshots") == self.fp_before


# ---------------------------------------------------------------------------
# H verify corruption detection（专用副本，不污染其他仓库）
# ---------------------------------------------------------------------------


class TestVerifyCorruption:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path) -> None:
        src = tmp_path / "src"
        self.cfg_root = tmp_path / "cfg"
        target = tmp_path / "target"
        _make_dataset(src)
        r = run_cli(
            "--config",
            str(self.cfg_root),
            "init",
            "--source",
            str(src),
            "--target",
            str(target),
            "--yes",
        )
        assert r.returncode == 0, r.stdout + r.stderr
        r = run_cli("--config", str(self.cfg_root), "backup", "--yes")
        assert r.returncode == 0, r.stdout + r.stderr
        self.repo = target / "MirrorlyRepo"
        self.sid = _manifest_ids(self.repo)[0]
        self.victim = self.repo / "snapshots" / self.sid / "bin" / "blob.bin"

    def test_full_verify_detects_corruption_exit_4(self) -> None:
        data = bytearray(self.victim.read_bytes())
        data[len(data) // 2] ^= 0xFF  # 翻转中间一个字节（大小不变）
        self.victim.write_bytes(bytes(data))
        proc = run_cli("--config", str(self.cfg_root), "verify")
        assert proc.returncode == 4
        assert "bin/blob.bin" in (proc.stdout + proc.stderr)  # 报告准确指出问题
        # 落盘报告包含该 issue
        reports = sorted((self.repo / "logs").glob("verify-*.json"))
        assert reports
        rep = json.loads(reports[-1].read_text(encoding="utf-8"))
        assert rep["ok"] is False
        issue_paths = {i["path"] for s in rep["snapshots"] for i in s["issues"]}
        assert "bin/blob.bin" in issue_paths

    def test_quick_verify_same_size_corruption_passes_by_design(self) -> None:
        """quick 模式不重算哈希（CLI_SPEC §3），同尺寸损坏不触发——冻结语义。"""
        data = bytearray(self.victim.read_bytes())
        data[10] ^= 0xFF
        self.victim.write_bytes(bytes(data))
        proc = run_cli("--config", str(self.cfg_root), "verify", "--quick")
        assert proc.returncode == 0


# ---------------------------------------------------------------------------
# 3 TR-5 中断 / incomplete / resume（REAL E2E：真实子进程 kill）
# ---------------------------------------------------------------------------


class TestInterruptedBackupResume:
    INTERRUPT_BYTES = 60 * 8 * 1024 * 1024  # 60 × 8 MiB ≈ 480 MiB

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path) -> None:
        self.src = tmp_path / "src"
        self.cfg_root = tmp_path / "cfg"
        self.target = tmp_path / "target"
        self.src.mkdir(parents=True)
        # 每文件 8 MiB × 60（首次全量复制+哈希需要数秒，保证 kill 窗口）
        block = os.urandom(8 * 1024 * 1024)
        for i in range(60):
            (self.src / f"chunk-{i:03d}.bin").write_bytes(block)
        r = run_cli(
            "--config",
            str(self.cfg_root),
            "init",
            "--source",
            str(self.src),
            "--target",
            str(self.target),
            "--yes",
        )
        assert r.returncode == 0, r.stdout + r.stderr
        self.repo = self.target / "MirrorlyRepo"

    def test_kill_mid_backup_then_resume(self) -> None:
        # python -m mirrorly（kill 需直接命中业务进程；console launcher 会派生子进程）
        argv = [
            sys.executable,
            "-m",
            "mirrorly",
            "--config",
            str(self.cfg_root),
            "backup",
            "--yes",
        ]
        proc = subprocess.Popen(
            argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_ENV
        )
        try:
            deadline = time.monotonic() + 120
            killed_at_files = -1
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    pytest.fail("备份在 kill 前已完成，需增大数据集")
                manifests = list((self.repo / "manifests").glob("*.json"))
                if manifests:
                    snap_dir = self.repo / "snapshots" / manifests[0].stem
                    n = sum(1 for _ in snap_dir.rglob("*")) if snap_dir.exists() else 0
                    if n >= 5:
                        killed_at_files = n
                        proc.kill()  # TerminateProcess：TR-5 的「进程被杀」场景
                        break
                time.sleep(0.05)
            assert killed_at_files >= 5, "未能在物化阶段观察到进度"
        finally:
            proc.wait()

        # 中断后：incomplete 而非虚假 complete
        ids = _manifest_ids(self.repo)
        assert len(ids) == 1
        inc = json.loads((self.repo / "manifests" / f"{ids[0]}.json").read_text(encoding="utf-8"))
        assert inc["status"] == "incomplete"
        # 锁状态与冻结语义一致：崩溃残留 task/repo 锁 → 下一实例 exit 6，
        # 需人工确认后同时删除（均不做 PID 猜测或自动 stale cleanup）。
        r = run_cli("--config", str(self.cfg_root), "backup", "--yes")
        assert r.returncode == 6
        task_lock = self.repo / "locks" / "default.lock"
        repo_lock_dir = self.repo / "locks" / "repo-writer"
        repo_lock = repo_lock_dir / "active.lock"
        assert task_lock.is_file()
        assert repo_lock.is_file()
        task_lock.unlink()
        repo_lock.unlink()
        assert repo_lock_dir.is_dir()
        assert not repo_lock.exists()

        # --yes 续传：以 incomplete 为基线，产出新 snapshot id
        r = run_cli("--config", str(self.cfg_root), "backup", "--yes")
        assert r.returncode == 0, r.stdout + r.stderr
        ids_after = _manifest_ids(self.repo)
        assert len(ids_after) == 1  # 旧 incomplete 已被善后删除
        new_id = ids_after[0]
        assert new_id != ids[0]
        m = json.loads((self.repo / "manifests" / f"{new_id}.json").read_text(encoding="utf-8"))
        assert m["status"] == "complete"
        # B1-2：verify_on_write=True（默认配置）下 resume 发布的 complete 快照
        # 不得因 PRE_FILE 复用而静默失去内容完整性覆盖——所有文件 entry 必须有哈希
        assert all(e["sha"] for e in m["entries"] if e["type"] == "file")
        rep = json.loads(
            list((self.repo / "logs").glob(f"backup-{new_id}*.json"))[0].read_text(encoding="utf-8")
        )
        assert rep["resumed_from"] == ids[0]
        # 已复制数据确实被复用：续传只补写缺失部分，硬链接复用非空
        assert 0 < rep["bytes_written"] < self.INTERRUPT_BYTES
        assert rep["linked"]
        # 无 tmp 残留；旧 incomplete 目录已清理
        assert list(self.repo.rglob("*.mrtmp")) == []
        assert not (self.repo / "snapshots" / ids[0]).exists()
        # 最终 complete 可 verify；且必须直接断言完整性覆盖（B1-2 教训：
        # rc==0 不代表内容被哈希校验——sha=None 条目会被静默跳过）
        r = run_cli("--config", str(self.cfg_root), "verify", "--json")
        assert r.returncode == 0, r.stdout + r.stderr
        vrep = json.loads(r.stdout)["snapshots"][0]
        assert vrep["unhashed_entries"] == 0
        assert vrep["hashed_files"] == vrep["checked_files"] == 60


# ---------------------------------------------------------------------------
# 4 M10 卷身份 / 盘符漂移语义（每用例独立仓库）
# ---------------------------------------------------------------------------


class TestM10VolumeIdentity:
    def _make_repo(self, tmp_path: Path) -> tuple[Path, Path]:
        src = tmp_path / "src"
        cfg_root = tmp_path / "cfg"
        target = tmp_path / "target"
        _make_dataset(src)
        r = run_cli(
            "--config",
            str(cfg_root),
            "init",
            "--source",
            str(src),
            "--target",
            str(target),
            "--yes",
        )
        assert r.returncode == 0, r.stdout + r.stderr
        r = run_cli("--config", str(cfg_root), "backup", "--yes")
        assert r.returncode == 0, r.stdout + r.stderr
        return cfg_root, target

    def test_wrong_volume_serial_exit_5_zero_writes(self, tmp_path) -> None:
        """插错盘（卷标识不符）→ exit 5、零写入。

        真实 CLI 子进程；「错误卷」通过篡改 repo.json 记录的 serial 构造——
        与「同一路径出现另一个卷」在身份校验边界完全等价。
        """
        cfg_root, target = self._make_repo(tmp_path)
        repo = target / "MirrorlyRepo"
        info_path = repo / "repo.json"
        info = json.loads(info_path.read_text(encoding="utf-8"))
        real_serial = info["volume"]["serial"]
        wrong = "0000DEAD" if real_serial != "0000DEAD" else "0000BEEF"
        info["volume"]["serial"] = wrong
        info_path.write_text(
            json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        before = _tree_fingerprint(repo)

        r = run_cli("--config", str(cfg_root), "backup", "--yes")
        assert r.returncode == 5, r.stdout + r.stderr
        assert "卷标识不匹配" in (r.stdout + r.stderr)
        # 不扫描源 / 不创建 lock / 不建 snapshot / 不改 manifest / 不写 report：
        # 整个仓库树（含 mtime）逐字节不变
        assert _tree_fingerprint(repo) == before
        assert not list((repo / "locks").glob("*.lock"))
        assert len(_manifest_ids(repo)) == 1

    def test_drive_letter_relocation_real_guid_chain(self, tmp_path) -> None:
        """盘符漂移自动重定位（REAL API）：配置 path 失联，GUID 锚定位原卷原仓库。

        M10 冻结语义 A：用户不修改 TaskConfig 任何字段的情况下，盘符 D:→E:
        后 backup 自动定位同一目标卷并继续同一仓库（T-10 #2）。
        真实 Windows Volume GUID API 链路（GetVolumePathNamesForVolumeNameW）；
        「盘符变化」以配置 path 指向已消失的旧位置仿真（真实盘符漂移的效果
        正是 path 失效而卷与仓库仍由锚定位）。不改动真实盘符/挂载点。
        """
        from dataclasses import replace as dc_replace

        from mirrorly.config import load_task_config, write_task_config

        cfg_root, target = self._make_repo(tmp_path)
        repo = target / "MirrorlyRepo"
        ids_before = _manifest_ids(repo)

        # 仿真盘符漂移：仅把配置中的 path 换成旧盘符的失联地址（锚三字段原样保留）
        cfg_file = cfg_root / "config.d" / "default.toml"
        cfg = load_task_config(cfg_file)
        assert cfg.volume_guid, "init 未登记卷锚"
        assert cfg.repo_id and cfg.repo_dir
        stale = target.parent / "old-drive-letter-gone"
        assert not stale.exists()
        write_task_config(dc_replace(cfg, target_path=str(stale)), cfg_root)

        (Path(cfg.source) / "after-relocation.txt").write_bytes(b"after relocation\n")

        r = run_cli("--config", str(cfg_root), "backup", "--yes")
        assert r.returncode == 0, r.stdout + r.stderr
        # 重定位提示走 stderr（stdout 纯度不受影响）
        assert "重定位" in r.stderr, r.stderr

        # 自动定位回原仓库继续累积，未在失联位置新建仓库
        ids_after = _manifest_ids(repo)
        assert len(ids_after) == len(ids_before) + 1
        assert not (stale / "MirrorlyRepo").exists()

        # 配置未被自动改写（运行时 resolution，不落盘）
        cfg2 = load_task_config(cfg_file)
        assert str(cfg2.target_path) == str(stale)
        assert cfg2.volume_guid == cfg.volume_guid
        assert cfg2.repo_id == cfg.repo_id

        # 原仓库整体可 verify
        r = run_cli("--config", str(cfg_root), "verify", "--all")
        assert r.returncode == 0, r.stdout + r.stderr

    def test_unmounted_guid_exit_5_zero_writes_real_api(self, tmp_path) -> None:
        """expected GUID 未挂载（REAL API）→ exit 5、零写入、无 serial 降级认领。

        真实 API：随机 GUID 无当前挂载点；而系统上就存在 repo_id/repo_dir/
        serial 完全匹配的真实仓库（本卷）——若存在任何 serial fallback，
        本用例必失败。
        """
        from dataclasses import replace as dc_replace

        from mirrorly.config import load_task_config, write_task_config

        cfg_root, target = self._make_repo(tmp_path)
        repo = target / "MirrorlyRepo"
        cfg_file = cfg_root / "config.d" / "default.toml"
        cfg = load_task_config(cfg_file)
        # 形式合法但（几乎必然）不存在的 GUID
        fake_guid = "\\\\?\\Volume{00000000-0000-0000-0000-00c0ffee0002}\\"
        assert fake_guid != cfg.volume_guid
        write_task_config(
            dc_replace(cfg, target_path=str(target.parent / "stale"), volume_guid=fake_guid),
            cfg_root,
        )
        before = _tree_fingerprint(repo)

        r = run_cli("--config", str(cfg_root), "backup", "--yes")
        assert r.returncode == 5, r.stdout + r.stderr
        assert "未连接或卷锚已失效" in (r.stdout + r.stderr)
        assert _tree_fingerprint(repo) == before
        assert not list((repo / "locks").glob("*.lock"))

    def test_relocated_repo_missing_exit_1_no_auto_init(self, tmp_path) -> None:
        """expected volume 在（真实锚），但仓库目录已消失 → exit 1、不自动 init。"""
        from dataclasses import replace as dc_replace

        from mirrorly.config import load_task_config, write_task_config

        cfg_root, target = self._make_repo(tmp_path)
        # 仓库搬迁走 rename（避免删除配额）：配置 path 失效 + 原位置无仓库
        moved = target.parent / "moved-away"
        os.rename(target, moved)
        cfg_file = cfg_root / "config.d" / "default.toml"
        cfg = load_task_config(cfg_file)
        write_task_config(dc_replace(cfg, target_path=str(target.parent / "stale")), cfg_root)

        r = run_cli("--config", str(cfg_root), "backup", "--yes")
        assert r.returncode == 1, r.stdout + r.stderr
        assert "预期仓库路径缺失" in (r.stdout + r.stderr)
        # 原位置不被自动重建
        assert not (target / "MirrorlyRepo").exists()
        assert (moved / "MirrorlyRepo" / "repo.json").is_file()  # 搬走者原样


# ---------------------------------------------------------------------------
# 6 retention 端到端（冻结 T-10 验收标准 #1 的「保留清理」）
# ---------------------------------------------------------------------------


class TestRetentionE2E:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path) -> None:
        from dataclasses import replace as dc_replace

        from mirrorly.config import load_task_config, write_task_config

        self.src = tmp_path / "src"
        self.cfg_root = tmp_path / "cfg"
        self.target = tmp_path / "target"
        self.src.mkdir(parents=True)
        (self.src / "shared.bin").write_bytes(os.urandom(2 * 1024 * 1024))
        r = run_cli(
            "--config",
            str(self.cfg_root),
            "init",
            "--source",
            str(self.src),
            "--target",
            str(self.target),
            "--yes",
        )
        assert r.returncode == 0, r.stdout + r.stderr
        # keep_last=2 / keep_monthly=1：第 3 次备份起触发清理（模拟用户改 TOML；
        # 锚三字段原样保留——重定位能力与 retention 同时在场）
        cfg = load_task_config(self.cfg_root / "config.d" / "default.toml")
        assert cfg.volume_guid  # anchored 配置
        write_task_config(dc_replace(cfg, keep_last=2, keep_monthly=1), self.cfg_root)
        self.repo = self.target / "MirrorlyRepo"

    def test_retention_keeps_two_and_data_alive(self) -> None:
        ids_seen: list[str] = []
        for i in range(4):
            (self.src / f"v{i}.txt").write_bytes(f"version {i}\n".encode())
            r = run_cli("--config", str(self.cfg_root), "backup", "--yes")
            assert r.returncode == 0, r.stdout + r.stderr
            ids_seen += _manifest_ids(self.repo)
        final_ids = _manifest_ids(self.repo)
        assert len(final_ids) == 2  # keep_last=2
        # 被删快照的 manifest 与目录均不存在
        removed = set(ids_seen) - set(final_ids)
        assert len(removed) == 2
        for sid in removed:
            assert not (self.repo / "manifests" / f"{sid}.json").exists()
            assert not (self.repo / "snapshots" / sid).exists()
        # 保留快照可 verify（含最早就存在、被两个保留快照共享的 shared.bin）
        r = run_cli("--config", str(self.cfg_root), "verify", "--all")
        assert r.returncode == 0, r.stdout + r.stderr
        for sid in final_ids:
            shared = self.repo / "snapshots" / sid / "shared.bin"
            assert shared.is_file()
            assert shared.stat().st_nlink >= 2  # 硬链接共享的数据仍被引用
        # 不出现「manifest complete 但 data 已丢失」：verify --all 已断言
