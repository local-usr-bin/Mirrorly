"""Mirrorly 命令行入口（``python -m mirrorly`` 与 console script 共用）。

业务编排在 ``mirrorly.cli``，本模块仅作入口委托。
"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
