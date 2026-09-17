"""Small durable-publication primitives for repository control metadata.

The lifecycle sequence state is an irreversible high-water mark: returning a
reservation before both the new bytes and their namespace publication are
durable could allow the same sequence to be issued again after a crash.

On Windows, ``os.fsync`` uses the Microsoft C runtime ``_commit`` operation for
the staging file and ``MoveFileExW`` with ``MOVEFILE_WRITE_THROUGH`` provides
the write-through namespace move.  The temporary file is always beside the
destination, so the move stays on the same volume and remains atomic.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
from pathlib import Path

_MOVEFILE_REPLACE_EXISTING = 0x1
_MOVEFILE_WRITE_THROUGH = 0x8


def _windows_long_path(path: Path) -> str:
    value = str(path.resolve())
    if value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def _move_file_ex_write_through(source: Path, destination: Path) -> None:
    """Atomically replace *destination* and wait for the move to reach disk."""

    from ctypes import wintypes

    move_file_ex = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    move_file_ex.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
    move_file_ex.restype = wintypes.BOOL
    flags = _MOVEFILE_REPLACE_EXISTING | _MOVEFILE_WRITE_THROUGH
    if not move_file_ex(
        _windows_long_path(source),
        _windows_long_path(destination),
        flags,
    ):
        raise ctypes.WinError(ctypes.get_last_error())


def _replace_file_durable(source: Path, destination: Path) -> None:
    if sys.platform.startswith("win"):
        _move_file_ex_write_through(source, destination)
    else:  # Mirrorly v1 is Windows-first; this path keeps unit tests portable.
        os.replace(source, destination)


def write_json_durable(path: Path, data: dict) -> None:
    """Durably publish JSON through an owned sibling temp file.

    The caller must serialize writers (the repository writer lock does this for
    lifecycle mutations).  A failure is always propagated.  If the final move
    succeeded but a later observation reports failure, the caller still aborts;
    the next run reloads the durable high-water mark, leaving a safe gap.
    """

    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(temp, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        _replace_file_durable(temp, path)
    except OSError:
        try:
            if temp.exists():
                temp.unlink()
        finally:
            raise
