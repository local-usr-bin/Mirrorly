"""volume.py 真实 Windows API smoke 测试（T-10 M10 卷锚链路）。

全部只读，不修改盘符 / 不调用 mountvol / 不改系统状态。
若沙箱虚拟化屏蔽这些 kernel32 查询，用例显式 fail（不得静默跳过——
M10 重定位依赖该链路，必须先证实环境可用）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from mirrorly import volume


def test_get_volume_mount_root_of_current_dir(tmp_path: Path) -> None:
    # GetVolumePathNameW：真实路径 → 挂载点根（盘符根，含尾反斜杠）
    marker = tmp_path / "m.txt"
    marker.write_text("x", encoding="utf-8")
    root = volume.get_volume_mount_root(str(marker))
    assert root.endswith("\\")
    assert len(root) >= 3  # 至少 "C:\"
    # 挂载点根必须真实存在
    assert Path(root).exists()


def test_volume_guid_roundtrip_contains_current_mount_root(tmp_path: Path) -> None:
    # G：GetVolumePathNameW → GetVolumeNameForVolumeMountPointW
    #    → GetVolumePathNamesForVolumeNameW → 返回集合含当前挂载根
    marker = tmp_path / "m.txt"
    marker.write_text("x", encoding="utf-8")
    root = volume.get_volume_mount_root(str(marker))
    guid = volume.get_volume_guid_for_path(str(marker))
    # canonical 形式
    assert volume.VOLUME_GUID_RE.match(guid), guid
    roots = volume.get_mount_roots(guid)
    assert roots, "GUID 无当前挂载点（不应发生：路径本身就在该卷上）"
    norm = {str(Path(r)).rstrip("\\").casefold() for r in roots}
    assert str(Path(root)).rstrip("\\").casefold() in norm
    # 同一路径再次查询 GUID 应一致（挂载管理器缓存稳定）
    assert volume.get_volume_guid_for_path(str(marker)) == guid


def test_get_mount_roots_rejects_non_guid() -> None:
    with pytest.raises(volume.VolumeError):
        volume.get_mount_roots(r"\\?\Device\HarddiskVolume3")
    with pytest.raises(volume.VolumeError):
        volume.get_mount_roots(r"\\?\Volume{not-a-guid}")
    with pytest.raises(volume.VolumeError):
        volume.get_mount_roots("C:\\")


def test_get_mount_roots_unknown_guid_returns_empty() -> None:
    # 形式合法但（几乎必然）不存在的 GUID → 空（未挂载语义）
    guid = "\\\\?\\Volume{00000000-0000-0000-0000-00c0ffee0001}\\"
    roots = volume.get_mount_roots(guid)
    assert roots == []


def test_list_mounted_volumes_diagnostic(tmp_path: Path) -> None:
    # 诊断枚举：非空且全部为 canonical Volume GUID path
    vols = volume.list_mounted_volumes()
    assert vols
    for v in vols:
        assert volume.VOLUME_GUID_RE.match(v), v
    # 至少包含当前路径所在卷
    guid = volume.get_volume_guid_for_path(str(tmp_path))
    assert guid in vols


def test_get_volume_mount_root_missing_path_fails() -> None:
    with pytest.raises(volume.VolumeError):
        volume.get_volume_mount_root(r"Z:\definitely\not\exist\path\x.txt")


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="仅 Windows")
def test_sandbox_api_availability() -> None:
    # 显式冒烟：沙箱内三个 API 必须可用（不可用则 M10 实现需沙箱外跑）
    root = volume.get_volume_mount_root(str(Path.cwd()))
    assert root.endswith("\\")
