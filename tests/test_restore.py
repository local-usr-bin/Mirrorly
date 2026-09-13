"""T-08 恢复（restore）测试（对应 MVP_TASKS T-08 验收标准 + 修订计划安全语义）。

覆盖：
- canonical Windows 路径校验（含保留设备名 COM¹-³/LPT¹-³ 及带扩展名形式、
  非 BMP 字符的 UTF-16 长度语义）；
- 字面 selector（无 glob 语义）；
- plan/apply 两阶段、manifest 指纹、no-upgrade / stale 对账；
- 三种覆盖策略（never / older 严格小于 / always）与 file/dir 类型冲突；
- 目标边界（仓库内拒绝、in_place 仅同一实际位置、普通子目录允许）；
- reparse 双侧防护、独占临时文件与清理纪律、partial restore。
"""

from __future__ import annotations

import os
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from mirrorly.hashing import hash_file
from mirrorly.manifest import (
    STATUS_INCOMPLETE,
    ManifestError,
    create_manifest,
    load_manifest,
    mark_complete,
    write_manifest,
)
from mirrorly.repo import VolumeInfo, init_repo
from mirrorly.restore import (
    ACTION_CREATE,
    ACTION_OVERWRITE,
    ACTION_SKIP,
    RestoreEntry,
    RestoreError,
    apply_restore,
    normalize_selector,
    plan_restore,
    validate_canonical_rel_path,
)
from mirrorly.scan import scan_source, to_long_path

_NTFS = VolumeInfo(label="BackupDisk", serial="A1B2C3D4", filesystem="NTFS")


def _ntfs_provider(_path):
    return _NTFS


def _init_repo(path: Path):
    return init_repo(path, volume_info_provider=_ntfs_provider)


def _write_file(path: Path, data: bytes) -> None:
    """写文件（自动建父目录；走 long-path 前缀以支持长文件名用例）。"""
    Path(to_long_path(path.parent)).mkdir(parents=True, exist_ok=True)
    with open(to_long_path(path), "wb") as f:
        f.write(data)


def _make_snapshot(
    repo,
    snap_id: str,
    files: dict[str, bytes],
    *,
    dirs: tuple[str, ...] = (),
    source_root: str = "C:/source",
    complete: bool = True,
):
    """物化一个快照目录并登记 manifest（元数据取自真实文件）。"""
    snap_dir = repo.path / "snapshots" / snap_id
    Path(to_long_path(snap_dir)).mkdir(parents=True)
    for d in dirs:
        Path(to_long_path(snap_dir / Path(d))).mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        _write_file(snap_dir / Path(rel), data)
    scanned = scan_source(snap_dir, [])
    hashes = {
        rel: hash_file(to_long_path(snap_dir / Path(rel)), repo.hash_algorithm)
        for rel, e in scanned.entries.items()
        if not e.is_dir
    }
    m = create_manifest(snap_id, source_root, repo.hash_algorithm, scanned.entries, hashes)
    if complete:
        m = mark_complete(m)
    write_manifest(repo, m)
    return snap_dir


def _actions(plan) -> dict[str, str]:
    return {e.rel_path: e.action for e in plan.entries}


def _make_symlink(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
    try:
        os.symlink(target, link, target_is_directory=target_is_directory)
    except OSError as e:
        pytest.skip(f"当前环境无法创建符号链接: {e}")
    if not os.path.lexists(str(link)):
        pytest.skip("当前环境创建符号链接后不可见（沙箱虚拟化 reparse point），跳过")
    st = os.lstat(str(link))
    masked = not (
        stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400  # REPARSE_POINT
    )
    if masked:
        pytest.skip("当前环境对符号链接屏蔽 reparse 属性（沙箱限制），跳过")


# ---------------------------------------------------------------------------
# canonical 路径校验
# ---------------------------------------------------------------------------


class TestCanonicalValidator:
    @pytest.mark.parametrize(
        "rel",
        [
            "a.txt",
            "dir/sub/a.txt",
            "文件/名 字.txt",
            "COM0",
            "COM10",
            "LPT0",
            "CONSOLE",
            "CONX",
            "a" * 255,  # BMP：恰好 255 个 UTF-16 code unit
            "😀" * 127,  # 非 BMP：127 × 2 = 254 个 code unit
        ],
    )
    def test_valid(self, rel: str) -> None:
        assert str(validate_canonical_rel_path(rel)) == rel

    @pytest.mark.parametrize(
        "rel",
        [
            "",
            "/abs/path",
            "C:/abs",
            "a//b",
            "a/./b",
            "a/../b",
            "..",
            "a /b",  # 尾随空格
            "a./b",  # 尾随点
            "a\x01b",  # 控制字符
            "a<b",
            "a>b",
            "a:b",
            'a"b',
            "a|b",
            "a?b",
            "a*b",
            "a" * 256,  # BMP 超限
            "😀" * 128,  # 非 BMP：256 个 code unit（len() 仅 128，不可误判合法）
            "😀" * 200,  # 非 BMP：400 个 code unit（len() 仅 200，必须拒绝）
        ],
    )
    def test_invalid(self, rel: str) -> None:
        with pytest.raises(RestoreError):
            validate_canonical_rel_path(rel)

    @pytest.mark.parametrize(
        "name",
        [
            "CON",
            "PRN",
            "AUX",
            "NUL",
            "COM1",
            "COM5",
            "COM9",
            "LPT1",
            "LPT9",
            "COM¹",
            "COM²",
            "COM³",
            "LPT¹",
            "LPT²",
            "LPT³",
            "con",
            "com1",
            "lpt9",  # 大小写不敏感
            # 带扩展名形式同样保留
            "CON.txt",
            "NUL.log",
            "COM1.txt",
            "LPT9.doc",
            "COM¹.txt",
            "COM².log",
            "COM³.bin",
            "LPT¹.txt",
            "LPT².doc",
            "LPT³.dat",
        ],
    )
    def test_reserved_device_names(self, name: str) -> None:
        with pytest.raises(RestoreError):
            validate_canonical_rel_path(name)
        with pytest.raises(RestoreError):
            validate_canonical_rel_path(f"dir/{name}")


class TestSelector:
    def test_backslash_normalized(self) -> None:
        assert normalize_selector("dir\\sub\\a.txt") == "dir/sub/a.txt"

    def test_glob_chars_rejected_not_expanded(self) -> None:
        # *.txt 不做 glob 展开：* 是 Windows 禁止字符，直接判非法
        with pytest.raises(RestoreError):
            normalize_selector("*.txt")

    def test_literal_file_and_subtree(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(
            repo,
            "s1",
            {"a.txt": b"a", "sub/b.txt": b"b", "sub/deep/c.txt": b"c", "other/d.txt": b"d"},
        )
        plan = plan_restore(repo, "s1", tmp_path / "out", paths=("sub",))
        assert set(_actions(plan)) == {"sub", "sub/b.txt", "sub/deep", "sub/deep/c.txt"}
        plan = plan_restore(repo, "s1", tmp_path / "out", paths=("a.txt",))
        assert set(_actions(plan)) == {"a.txt"}

    def test_unmatched_selector_rejected(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "s1", {"a.txt": b"a"})
        with pytest.raises(RestoreError, match="未命中"):
            plan_restore(repo, "s1", tmp_path / "out", paths=("nope.txt",))


# ---------------------------------------------------------------------------
# plan / 目标边界
# ---------------------------------------------------------------------------


class TestPlanAndDestinationBoundary:
    def test_plan_to_empty_dir_all_create(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "s1", {"a.txt": b"a", "sub/b.txt": b"b"}, dirs=("empty",))
        plan = plan_restore(repo, "s1", tmp_path / "out")
        actions = _actions(plan)
        assert actions["a.txt"] == ACTION_CREATE
        assert actions["sub/b.txt"] == ACTION_CREATE
        assert actions["empty"] == ACTION_CREATE
        # 目录排在文件之前（父目录先创建）
        kinds = [(e.is_dir, e.rel_path) for e in plan.entries]
        assert kinds == sorted(kinds, key=lambda k: (not k[0], k[1].count("/"), k[1]))

    def test_destination_inside_repo_rejected(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "s1", {"a.txt": b"a"})
        with pytest.raises(RestoreError, match="仓库目录内"):
            plan_restore(repo, "s1", repo.path / "restore-out")
        with pytest.raises(RestoreError, match="仓库目录内"):
            plan_restore(repo, "s1", repo.path / "snapshots" / "elsewhere")

    def test_in_place_required_only_for_same_location(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        source = tmp_path / "src"
        source.mkdir()
        _make_snapshot(repo, "s1", {"a.txt": b"a"}, source_root=str(source))
        # 同一实际位置：必须 in_place
        with pytest.raises(RestoreError, match="in_place"):
            plan_restore(repo, "s1", source)
        plan = plan_restore(repo, "s1", source, in_place=True)
        assert plan.entries
        # source_root 的普通子目录：允许，无需 in_place
        plan = plan_restore(repo, "s1", source / "subdir")
        assert plan.entries

    def test_incomplete_manifest_rejected(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "s1", {"a.txt": b"a"}, complete=False)
        with pytest.raises(ManifestError):
            plan_restore(repo, "s1", tmp_path / "out")

    def test_snapshot_dir_missing_rejected(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        snap_dir = _make_snapshot(repo, "s1", {"a.txt": b"a"})
        import shutil

        shutil.rmtree(to_long_path(snap_dir))
        with pytest.raises(RestoreError, match="快照目录不存在"):
            plan_restore(repo, "s1", tmp_path / "out")

    def test_invalid_snapshot_id_rejected(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        with pytest.raises(RestoreError):
            plan_restore(repo, "../evil", tmp_path / "out")

    def test_invalid_overwrite_policy_rejected(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "s1", {"a.txt": b"a"})
        with pytest.raises(RestoreError, match="覆盖策略"):
            plan_restore(repo, "s1", tmp_path / "out", overwrite="sometimes")

    def test_tampered_manifest_entry_path_rejected(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "s1", {"a.txt": b"a"})
        m = load_manifest(repo, "s1", require_complete=True)
        evil = replace(m.entries[0], path="../evil.txt")
        write_manifest(repo, replace(m, entries=(evil,)))
        with pytest.raises(RestoreError, match="manifest 条目路径"):
            plan_restore(repo, "s1", tmp_path / "out")


# ---------------------------------------------------------------------------
# apply：基本恢复正确性
# ---------------------------------------------------------------------------


class TestApplyBasic:
    def test_restore_byte_identical(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        files = {
            "a.txt": b"hello",
            "sub/b.bin": bytes(range(256)) * 100,
            "sub/deep/c.txt": "中文内容😀".encode(),
            "empty.txt": b"",
        }
        _make_snapshot(repo, "s1", files, dirs=("emptydir",))
        dest = tmp_path / "out"
        result = apply_restore(repo, plan_restore(repo, "s1", dest))
        assert not result.errors
        assert set(result.restored) == set(files)
        for rel, data in files.items():
            assert (dest / Path(rel)).read_bytes() == data
        assert (dest / "emptydir").is_dir()
        assert "emptydir" in result.dirs_created
        assert result.leftovers == ()

    def test_mtime_preserved(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "s1", {"a.txt": b"a"})
        m = load_manifest(repo, "s1", require_complete=True)
        entry = next(e for e in m.entries if e.path == "a.txt")
        dest = tmp_path / "out"
        apply_restore(repo, plan_restore(repo, "s1", dest))
        restored_mtime = os.stat(to_long_path(dest / "a.txt")).st_mtime_ns
        # Windows FILETIME 100ns 粒度截断属平台限制（同 T-03 测试容差）
        assert abs(restored_mtime - entry.mtime_ns) <= 100

    def test_no_temp_residue_and_user_mrtmp_untouched(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "s1", {"a.txt": b"a"})
        dest = tmp_path / "out"
        dest.mkdir()
        # 用户自己的 .mrtmp 文件：恢复绝不扫描后缀批量删除
        user_tmp = dest / "foo.txt.mrtmp"
        user_tmp.write_bytes(b"user data")
        result = apply_restore(repo, plan_restore(repo, "s1", dest))
        assert not result.errors
        assert user_tmp.read_bytes() == b"user data"
        residue = [p for p in dest.rglob("*") if ".mirrorly-restore-" in p.name]
        assert residue == []

    def test_long_filename_near_component_limit(self, tmp_path) -> None:
        # 合法 final 名接近 255 code unit 上限：临时文件名（短固定前缀）不得因此溢出
        repo = _init_repo(tmp_path / "target")
        long_name = "文" * 120 + "😀" * 60 + ".txt"  # 120×1 + 60×2 + 4 = 244 code units
        assert len(long_name.encode("utf-16-le")) // 2 <= 255
        _make_snapshot(repo, "s1", {long_name: b"long-name-content"})
        dest = tmp_path / "out"
        result = apply_restore(repo, plan_restore(repo, "s1", dest))
        assert not result.errors
        with open(to_long_path(dest / long_name), "rb") as f:
            assert f.read() == b"long-name-content"


# ---------------------------------------------------------------------------
# apply：覆盖策略与类型冲突
# ---------------------------------------------------------------------------


class TestOverwritePolicies:
    def _setup(self, tmp_path):
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "s1", {"a.txt": b"snapshot"})
        m = load_manifest(repo, "s1", require_complete=True)
        entry = next(e for e in m.entries if e.path == "a.txt")
        dest = tmp_path / "out"
        dest.mkdir()
        (dest / "a.txt").write_bytes(b"user data")
        return repo, dest, entry.mtime_ns

    def test_never_skips(self, tmp_path) -> None:
        repo, dest, _ = self._setup(tmp_path)
        result = apply_restore(repo, plan_restore(repo, "s1", dest, overwrite="never"))
        assert (dest / "a.txt").read_bytes() == b"user data"
        assert result.restored == ()
        assert result.skipped == (("a.txt", "目标已存在（never）"),)

    def test_always_overwrites(self, tmp_path) -> None:
        repo, dest, _ = self._setup(tmp_path)
        result = apply_restore(repo, plan_restore(repo, "s1", dest, overwrite="always"))
        assert (dest / "a.txt").read_bytes() == b"snapshot"
        assert result.restored == ("a.txt",)

    def test_older_overwrites_only_when_strictly_older(self, tmp_path) -> None:
        repo, dest, snap_mtime = self._setup(tmp_path)
        older = snap_mtime - 10_000_000_000  # 早 10 秒
        os.utime(to_long_path(dest / "a.txt"), ns=(older, older))
        result = apply_restore(repo, plan_restore(repo, "s1", dest, overwrite="older"))
        assert (dest / "a.txt").read_bytes() == b"snapshot"
        assert result.restored == ("a.txt",)

    def test_older_skips_when_equal_or_newer(self, tmp_path) -> None:
        repo, dest, snap_mtime = self._setup(tmp_path)
        # 相等：严格小于才不覆盖 → 跳过
        os.utime(to_long_path(dest / "a.txt"), ns=(snap_mtime, snap_mtime))
        result = apply_restore(repo, plan_restore(repo, "s1", dest, overwrite="older"))
        assert (dest / "a.txt").read_bytes() == b"user data"
        assert result.restored == ()
        # 更新：跳过
        newer = snap_mtime + 10_000_000_000
        os.utime(to_long_path(dest / "a.txt"), ns=(newer, newer))
        result = apply_restore(repo, plan_restore(repo, "s1", dest, overwrite="older"))
        assert (dest / "a.txt").read_bytes() == b"user data"
        assert result.restored == ()

    def test_file_dir_conflict_never_deletes_user_dir(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "s1", {"a.txt": b"snapshot"})
        dest = tmp_path / "out"
        (dest / "a.txt").mkdir(parents=True)  # 目标为同名目录
        (dest / "a.txt" / "precious.txt").write_bytes(b"user")
        result = apply_restore(repo, plan_restore(repo, "s1", dest, overwrite="always"))
        assert result.restored == ()
        assert len(result.conflicts) == 1
        assert (dest / "a.txt" / "precious.txt").read_bytes() == b"user"  # 目录原样保留

    def test_dir_file_conflict_never_deletes_user_file(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "s1", {}, dirs=("sub",))
        dest = tmp_path / "out"
        dest.mkdir()
        (dest / "sub").write_bytes(b"user file")  # 目标为同名文件
        result = apply_restore(repo, plan_restore(repo, "s1", dest, overwrite="always"))
        assert (dest / "sub").read_bytes() == b"user file"
        assert any(p == "sub" for p, _ in result.conflicts)

    def test_parent_blocked_by_file_conflict(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "s1", {"sub/b.txt": b"b"})
        dest = tmp_path / "out"
        dest.mkdir()
        (dest / "sub").write_bytes(b"blocker")  # 父路径被文件阻挡
        result = apply_restore(repo, plan_restore(repo, "s1", dest, overwrite="always"))
        assert result.restored == ()
        assert any(p == "sub/b.txt" for p, _ in result.conflicts)
        assert (dest / "sub").read_bytes() == b"blocker"


# ---------------------------------------------------------------------------
# apply：stale / no-upgrade 对账
# ---------------------------------------------------------------------------


class TestStaleAndNoUpgrade:
    def test_manifest_digest_mismatch_is_stale(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "s1", {"a.txt": b"a"})
        plan = plan_restore(repo, "s1", tmp_path / "out")
        # 计划后 manifest 被改写（内容变化 → digest 变化）
        m = load_manifest(repo, "s1", require_complete=True)
        write_manifest(repo, replace(m, created_at="2000-01-01T00:00:00+00:00"))
        with pytest.raises(RestoreError, match="stale"):
            apply_restore(repo, plan)
        assert not (tmp_path / "out" / "a.txt").exists()

    def test_incomplete_after_plan_is_stale(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "s1", {"a.txt": b"a"})
        plan = plan_restore(repo, "s1", tmp_path / "out")
        m = load_manifest(repo, "s1", require_complete=True)
        write_manifest(repo, replace(m, status=STATUS_INCOMPLETE))
        with pytest.raises(ManifestError):
            apply_restore(repo, plan)

    def test_destructive_upgrade_is_stale(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "s1", {"a.txt": b"snapshot"})
        dest = tmp_path / "out"
        plan = plan_restore(repo, "s1", dest, overwrite="always")
        assert _actions(plan)["a.txt"] == ACTION_CREATE
        # 计划后目标出现同名文件：重算从 create 升级为 overwrite → stale
        dest.mkdir()
        (dest / "a.txt").write_bytes(b"user data")
        with pytest.raises(RestoreError, match="破坏性升级"):
            apply_restore(repo, plan)
        assert (dest / "a.txt").read_bytes() == b"user data"  # 零写入

    def test_downgrade_allowed_and_executed(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "s1", {"a.txt": b"snapshot"})
        dest = tmp_path / "out"
        dest.mkdir()
        (dest / "a.txt").write_bytes(b"old")
        plan = plan_restore(repo, "s1", dest, overwrite="always")
        assert _actions(plan)["a.txt"] == ACTION_OVERWRITE
        # 计划后目标文件被删除：重算降级为 create → 允许执行
        (dest / "a.txt").unlink()
        result = apply_restore(repo, plan)
        assert (dest / "a.txt").read_bytes() == b"snapshot"
        assert result.restored == ("a.txt",)

    def test_forged_plan_cannot_escalate(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "s1", {"a.txt": b"snapshot"})
        dest = tmp_path / "out"
        dest.mkdir()
        (dest / "a.txt").write_bytes(b"user data")
        plan = plan_restore(repo, "s1", dest, overwrite="never")
        assert _actions(plan)["a.txt"] == ACTION_SKIP
        # 伪造 plan：把 skip 改成 overwrite。apply 重跑规划按 never 仍得 skip，
        # 不执行伪造的 overwrite（重算破坏性不高于批准值即按重算执行）
        forged = replace(
            plan,
            entries=tuple(
                RestoreEntry(e.rel_path, e.dest_path, e.is_dir, ACTION_OVERWRITE)
                for e in plan.entries
            ),
        )
        result = apply_restore(repo, forged)
        assert result.restored == ()
        assert (dest / "a.txt").read_bytes() == b"user data"


# ---------------------------------------------------------------------------
# apply：reparse 双侧防护
# ---------------------------------------------------------------------------


class TestReparseProtection:
    def test_snapshot_side_reparse_rejected(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        snap_dir = _make_snapshot(repo, "s1", {"a.txt": b"a", "b.txt": b"b"})
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_bytes(b"secret")
        # 快照内条目被替换为符号链接（完整性异常）→ 整体拒绝
        (snap_dir / "a.txt").unlink()
        _make_symlink(snap_dir / "a.txt", outside / "secret.txt")
        with pytest.raises(RestoreError, match="reparse"):
            plan_restore(repo, "s1", tmp_path / "out")

    def test_destination_side_reparse_is_conflict(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "s1", {"a.txt": b"snapshot", "b.txt": b"b"})
        dest = tmp_path / "out"
        dest.mkdir()
        elsewhere = tmp_path / "elsewhere.txt"
        elsewhere.write_bytes(b"elsewhere")
        _make_symlink(dest / "a.txt", elsewhere)
        result = apply_restore(repo, plan_restore(repo, "s1", dest, overwrite="always"))
        # 链接条目记 conflict，不穿越写入；其余条目正常恢复
        assert any(p == "a.txt" for p, _ in result.conflicts)
        assert "b.txt" in result.restored
        assert elsewhere.read_bytes() == b"elsewhere"

    def test_destination_root_reparse_rejected(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        _make_snapshot(repo, "s1", {"a.txt": b"a"})
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link-dest"
        _make_symlink(link, real, target_is_directory=True)
        with pytest.raises(RestoreError, match="reparse"):
            plan_restore(repo, "s1", link)


# ---------------------------------------------------------------------------
# apply：partial restore
# ---------------------------------------------------------------------------


class TestPartialRestore:
    def test_single_file_failure_does_not_block_others(self, tmp_path) -> None:
        repo = _init_repo(tmp_path / "target")
        snap_dir = _make_snapshot(repo, "s1", {"a.txt": b"a", "b.txt": b"b"})
        dest = tmp_path / "out"
        plan = plan_restore(repo, "s1", dest)
        # 计划后快照内一个文件丢失（损坏）→ 该文件记 error，其余照常恢复
        (snap_dir / "a.txt").unlink()
        result = apply_restore(repo, plan)
        assert result.restored == ("b.txt",)
        assert [p for p, _ in result.errors] == ["a.txt"]
        assert (dest / "b.txt").read_bytes() == b"b"
