"""Windows 卷定位 API（T-10 M10：卷锚与挂载点解析）。

只封装 Windows 官方 volume-identity 链路（全部只读，零依赖）：

- ``GetVolumePathNameW``: 路径 → 其所属卷的挂载点根（盘符根或挂载文件夹根）
- ``GetVolumeNameForVolumeMountPointW``: 挂载点 → Volume GUID path
- ``GetVolumePathNamesForVolumeNameW``: Volume GUID → 该卷当前全部挂载点
- ``FindFirstVolumeW``/``FindNextVolumeW``: 枚举全机卷 GUID（仅诊断用途，
  绝不作为 anchored config 的身份降级路径）

注意（Windows 官方语义，不得写出过强假设）：一个 volume 可能存在多个
Volume GUID path，``GetVolumeNameForVolumeMountPointW`` 返回挂载管理器
缓存中的一个。MVP 把 init 时登记到的 GUID path 作为 Windows 安装内的
本地卷锚；若该锚后来不可解析，一律 fail closed，不做别名猜测。
"""

from __future__ import annotations

import ctypes
import re
import sys
from ctypes import wintypes

#: canonical Volume GUID path：\\?\Volume{GUID}\（尾反斜杠必需，大小写不敏感）
VOLUME_GUID_RE = re.compile(
    r"^\\\\\?\\Volume\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}"
    r"-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}\\$"
)


class VolumeError(Exception):
    """卷定位 API 失败。"""


def _kernel32() -> ctypes.WinDLL:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetVolumePathNameW.restype = wintypes.BOOL
    k32.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    k32.GetVolumeNameForVolumeMountPointW.restype = wintypes.BOOL
    k32.GetVolumeNameForVolumeMountPointW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    k32.GetVolumePathNamesForVolumeNameW.restype = wintypes.BOOL
    k32.GetVolumePathNamesForVolumeNameW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    k32.FindFirstVolumeW.restype = wintypes.HANDLE
    k32.FindFirstVolumeW.argtypes = [wintypes.LPWSTR, wintypes.DWORD]
    k32.FindNextVolumeW.restype = wintypes.BOOL
    k32.FindNextVolumeW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD]
    k32.FindVolumeClose.restype = wintypes.BOOL
    k32.FindVolumeClose.argtypes = [wintypes.HANDLE]
    return k32


_K32: ctypes.WinDLL | None = None


def _k32() -> ctypes.WinDLL:
    global _K32
    if _K32 is None:
        _K32 = _kernel32()
    return _K32


def is_windows() -> bool:
    return sys.platform.startswith("win")


def get_volume_mount_root(path: str | object) -> str:
    """路径 → 其所属卷的挂载点根（如 ``C:\\`` 或挂载文件夹根，含尾反斜杠）。"""
    if not is_windows():
        raise VolumeError(f"非 Windows 平台无卷挂载点: {path}")
    k32 = _k32()
    buf = ctypes.create_unicode_buffer(1024)
    if not k32.GetVolumePathNameW(str(path), buf, len(buf)):
        raise VolumeError(f"GetVolumePathNameW 失败（路径不可达或超长）: {path}")
    return buf.value


def get_volume_guid_for_path(path: str | object) -> str:
    """路径 → Volume GUID path（经 mount root → GetVolumeNameForVolumeMountPointW）。"""
    if not is_windows():
        raise VolumeError(f"非 Windows 平台无卷 GUID: {path}")
    root = get_volume_mount_root(path)
    if not root.endswith("\\"):
        root += "\\"
    k32 = _k32()
    buf = ctypes.create_unicode_buffer(100)
    if not k32.GetVolumeNameForVolumeMountPointW(root, buf, len(buf)):
        raise VolumeError(f"GetVolumeNameForVolumeMountPointW 失败（挂载点无卷 GUID）: {root}")
    return buf.value


def get_mount_roots(volume_guid: str) -> list[str]:
    """Volume GUID path → 该卷当前全部挂载点（盘符根 + 挂载文件夹根，含尾反斜杠）。

    卷未挂载 / GUID 不可解析 → 返回空列表（调用方 fail closed，绝不猜测）。
    """
    if not is_windows():
        raise VolumeError(f"非 Windows 平台无挂载点枚举: {volume_guid}")
    if not VOLUME_GUID_RE.match(volume_guid):
        raise VolumeError(f"非法 Volume GUID path: {volume_guid}")
    k32 = _k32()
    buf_len = 1024
    while True:
        buf = ctypes.create_unicode_buffer(buf_len)
        needed = wintypes.DWORD(0)
        ok = k32.GetVolumePathNamesForVolumeNameW(volume_guid, buf, buf_len, ctypes.byref(needed))
        if ok:
            break
        err = ctypes.get_last_error()
        if err == 2:  # ERROR_FILE_NOT_FOUND：该卷 GUID 当前不可解析（未挂载）
            return []
        if err == 234 and needed.value > 0:  # ERROR_MORE_DATA
            buf_len = needed.value + 2
            continue
        raise VolumeError(f"GetVolumePathNamesForVolumeNameW 失败: {volume_guid} (WinError {err})")
    # create_unicode_buffer 零初始化；c_wchar 数组切片返回含内嵌 NUL 的完整串
    chars = buf[:]
    return [p for p in chars.split("\x00") if p]


def list_mounted_volumes() -> list[str]:
    """枚举本机全部 Volume GUID path（仅诊断/测试用途，非身份判定路径）。"""
    if not is_windows():
        raise VolumeError("非 Windows 平台无卷枚举")
    k32 = _k32()
    out: list[str] = []
    buf = ctypes.create_unicode_buffer(100)
    handle = k32.FindFirstVolumeW(buf, len(buf))
    if handle == wintypes.HANDLE(-1).value or not handle:
        raise VolumeError(f"FindFirstVolumeW 失败 (WinError {ctypes.get_last_error()})")
    try:
        while True:
            out.append(buf.value)
            if not k32.FindNextVolumeW(handle, buf, len(buf)):
                break
    finally:
        k32.FindVolumeClose(handle)
    return out
