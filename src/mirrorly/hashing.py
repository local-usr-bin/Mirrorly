"""哈希算法抽象（ADR-013）。

BLAKE3 为主用算法；第三方包 ``blake3`` 不可用时降级 SHA-256（hashlib 标准库）。
xxHash 不用于完整性校验（非密码学哈希）。算法在仓库 init 时锁定写入
repo.json，同一仓库内不混用。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

try:
    import blake3 as _blake3
except ImportError:  # pragma: no cover - 取决于环境
    _blake3 = None

ALGORITHM_BLAKE3 = "blake3"
ALGORITHM_SHA256 = "sha256"

#: 文件哈希的流式读取分块大小（TR-6：大文件不一次性载入内存）
DEFAULT_CHUNK_SIZE = 1024 * 1024


class HashError(Exception):
    """哈希算法相关错误（不支持的算法、算法不可用等）。"""


def blake3_available() -> bool:
    """blake3 包是否可用。"""
    return _blake3 is not None


def default_algorithm() -> str:
    """默认完整性哈希算法：blake3 优先，不可用则降级 sha256。"""
    return ALGORITHM_BLAKE3 if blake3_available() else ALGORITHM_SHA256


def new_hasher(algorithm: str | None = None):
    """创建哈希器。algorithm 为 None 时使用 default_algorithm()。

    返回对象具有 update(bytes) 与 hexdigest() 方法。
    """
    algo = algorithm or default_algorithm()
    if algo == ALGORITHM_BLAKE3:
        if _blake3 is None:
            raise HashError("请求使用 blake3，但 blake3 包不可用（可降级 sha256）")
        return _blake3.blake3()
    if algo == ALGORITHM_SHA256:
        return hashlib.sha256()
    raise HashError(f"不支持的哈希算法: {algo!r}（允许: blake3 / sha256）")


def hash_file(
    path: str | Path,
    algorithm: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> str:
    """流式计算文件哈希，返回十六进制摘要。"""
    h = new_hasher(algorithm)
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()
