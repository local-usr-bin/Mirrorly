"""T-02 测试：源扫描与变更检测（ADR-006：元数据初筛 + 疑似项哈希复核）。"""

import os
import sys

import pytest

from mirrorly.scan import (
    ChangeSet,
    PreviousEntry,
    detect_changes,
    scan_source,
)


def _write(path, data: bytes = b"x", mtime_ns: int | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


def _entry(size: int, mtime_ns: int, sha: str | None = None) -> PreviousEntry:
    return PreviousEntry(size=size, mtime_ns=mtime_ns, sha=sha)


class TestScanSource:
    def test_empty_baseline_all_entries(self, tmp_path) -> None:
        _write(tmp_path / "a.txt", b"aaa")
        _write(tmp_path / "sub" / "b.txt", b"bb")
        result = scan_source(tmp_path, ())
        files = {e.path for e in result.entries.values() if not e.is_dir}
        assert files == {"a.txt", "sub/b.txt"}
        dirs = {e.path for e in result.entries.values() if e.is_dir}
        assert "sub" in dirs
        a = result.entries["a.txt"]
        assert a.size == 3 and a.mtime_ns > 0

    def test_empty_directory_recorded(self, tmp_path) -> None:
        (tmp_path / "empty_dir").mkdir()
        result = scan_source(tmp_path, ())
        assert result.entries["empty_dir"].is_dir

    def test_unicode_filename(self, tmp_path) -> None:
        _write(tmp_path / "你好_数据_ñ🙂.txt", b"u")
        result = scan_source(tmp_path, ())
        assert "你好_数据_ñ🙂.txt" in result.entries

    @pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows 长路径场景")
    def test_long_path_over_260_chars(self, tmp_path) -> None:
        deep = tmp_path
        while len(str(deep)) < 240:
            deep = deep / ("d" * 40)
        target = deep / "long_file.txt"
        prefixed = "\\\\?\\" + str(target)
        os.makedirs("\\\\?\\" + str(deep), exist_ok=True)
        with open(prefixed, "wb") as f:
            f.write(b"long")
        assert len(str(target)) > 260
        result = scan_source(tmp_path, ())
        rel = "/".join(target.relative_to(tmp_path).parts)
        assert rel in result.entries
        assert result.entries[rel].size == 4


class TestExcludeRules:
    def test_file_pattern_excludes_files_anywhere(self, tmp_path) -> None:
        _write(tmp_path / "keep.txt")
        _write(tmp_path / "drop.tmp")
        _write(tmp_path / "sub" / "drop2.tmp")
        result = scan_source(tmp_path, ("*.tmp",))
        files = {p for p, e in result.entries.items() if not e.is_dir}
        assert files == {"keep.txt"}

    def test_dir_pattern_prunes_whole_subtree(self, tmp_path) -> None:
        _write(tmp_path / "keep.txt")
        _write(tmp_path / "node_modules" / "pkg" / "index.js")
        result = scan_source(tmp_path, ("node_modules/",))
        assert set(result.entries) == {"keep.txt"}

    def test_file_and_dir_patterns_are_distinguished(self, tmp_path) -> None:
        # 无尾斜杠的模式只作用于文件，不排除同名目录
        _write(tmp_path / "cache")  # 文件 cache
        _write(tmp_path / "cache_dir" / "inner.txt")
        result = scan_source(tmp_path, ("cache",))
        assert "cache" not in result.entries  # 文件被排除
        assert "cache_dir/inner.txt" in result.entries  # 目录不受影响

        # 带尾斜杠的模式只作用于目录
        result2 = scan_source(tmp_path, ("cache_dir/",))
        assert "cache" in result2.entries  # 文件保留
        assert "cache_dir/inner.txt" not in result2.entries  # 目录被剪枝

    def test_path_pattern_with_slash(self, tmp_path) -> None:
        _write(tmp_path / "build" / "out.bin")
        _write(tmp_path / "src" / "build" / "keep.bin")
        result = scan_source(tmp_path, ("build/out.bin",))
        assert "build/out.bin" not in result.entries
        assert "src/build/keep.bin" in result.entries


class TestDetectChanges:
    def test_empty_baseline_all_added(self, tmp_path) -> None:
        _write(tmp_path / "a.txt", b"aaa")
        current = scan_source(tmp_path, ()).entries
        cs = detect_changes(tmp_path, current, None)
        assert cs.added == ["a.txt"]
        assert cs.modified == [] and cs.deleted == [] and cs.suspected_modified == []

    def test_new_file_added(self, tmp_path) -> None:
        _write(tmp_path / "a.txt", b"aaa", mtime_ns=1000)
        prev = {"a.txt": _entry(3, 1000, "sha")}
        _write(tmp_path / "b.txt", b"bb", mtime_ns=2000)
        current = scan_source(tmp_path, ()).entries
        cs = detect_changes(tmp_path, current, prev)
        assert cs.added == ["b.txt"]
        assert cs.modified == []

    def test_deleted_file(self, tmp_path) -> None:
        _write(tmp_path / "a.txt", b"aaa", mtime_ns=1000)
        prev = {"a.txt": _entry(3, 1000, "sha"), "gone.txt": _entry(5, 1000, "sha")}
        current = scan_source(tmp_path, ()).entries
        cs = detect_changes(tmp_path, current, prev)
        assert cs.deleted == ["gone.txt"]

    def test_size_change_is_modified_without_hash(self, tmp_path) -> None:
        _write(tmp_path / "a.txt", b"aaaa", mtime_ns=1000)
        prev = {"a.txt": _entry(3, 1000, "sha")}
        current = scan_source(tmp_path, ()).entries
        cs = detect_changes(tmp_path, current, prev)
        assert cs.modified == ["a.txt"]
        assert cs.hashed_files == []  # 大小变化直接判修改，无需哈希复核

    def test_content_change_with_same_mtime_detected_by_hash(self, tmp_path) -> None:
        # 内容变化但 mtime 保持（恶意/异常场景）：大小若也变则走大小分支，
        # 此处构造"大小相同、mtime 相同"以外唯一可检出路径——mtime 不同触发复核。
        # 真正的"大小+mtime 都不变但内容变"按 ADR-006 信任元数据，属已知取舍。
        f = _write(tmp_path / "a.txt", b"aaa", mtime_ns=1000)
        prev = {"a.txt": _entry(3, 1000, None)}  # 无哈希记录 → 保守判修改
        f.write_bytes(b"bbb")
        os.utime(f, ns=(2000, 2000))
        current = scan_source(tmp_path, ()).entries
        cs = detect_changes(tmp_path, current, prev)
        assert cs.modified == ["a.txt"]

    def test_mtime_only_change_cleared_by_hash(self, tmp_path) -> None:
        from mirrorly.hashing import hash_file

        f = _write(tmp_path / "a.txt", b"aaa", mtime_ns=1000)
        sha = hash_file(f)
        os.utime(f, ns=(9999, 9999))  # 仅 mtime 变化（如 touch）
        prev = {"a.txt": _entry(3, 1000, sha)}
        current = scan_source(tmp_path, ()).entries
        cs = detect_changes(tmp_path, current, prev)
        assert cs.modified == []
        assert cs.suspected_modified == ["a.txt"]  # 复核后确认未变
        assert cs.hashed_files == ["a.txt"]

    def test_mtime_change_with_different_hash_is_modified(self, tmp_path) -> None:
        from mirrorly.hashing import hash_file

        f = _write(tmp_path / "a.txt", b"aaa", mtime_ns=1000)
        old_sha = hash_file(f)
        f.write_bytes(b"xyz")  # 同大小不同内容
        os.utime(f, ns=(9999, 9999))
        prev = {"a.txt": _entry(3, 1000, old_sha)}
        current = scan_source(tmp_path, ()).entries
        cs = detect_changes(tmp_path, current, prev)
        assert cs.modified == ["a.txt"]
        assert cs.suspected_modified == []

    def test_unchanged_file_not_in_any_list(self, tmp_path) -> None:
        _write(tmp_path / "a.txt", b"aaa", mtime_ns=1000)
        prev = {"a.txt": _entry(3, 1000, "sha")}
        current = scan_source(tmp_path, ()).entries
        cs = detect_changes(tmp_path, current, prev)
        assert cs == ChangeSet()

    def test_directories_added_and_deleted(self, tmp_path) -> None:
        (tmp_path / "new_dir").mkdir()
        current = scan_source(tmp_path, ()).entries
        cs = detect_changes(tmp_path, current, None, previous_dirs=("old_dir",))
        assert cs.added_dirs == ["new_dir"]
        assert cs.deleted_dirs == ["old_dir"]

    def test_unicode_and_long_path_hash_recheck(self, tmp_path) -> None:
        from mirrorly.hashing import hash_file

        f = _write(tmp_path / "你好.txt", b"data", mtime_ns=1000)
        sha = hash_file(f)
        os.utime(f, ns=(2000, 2000))
        prev = {"你好.txt": _entry(4, 1000, sha)}
        current = scan_source(tmp_path, ()).entries
        cs = detect_changes(tmp_path, current, prev)
        assert cs.suspected_modified == ["你好.txt"]
