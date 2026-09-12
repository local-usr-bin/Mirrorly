"""Mirrorly 命令行入口（占位）。

产品功能实现后将在此提供 CLI 主入口。
"""

import sys


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口占位。"""
    print("Mirrorly - 尚未实现产品功能，当前为初始化阶段。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
