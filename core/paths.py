"""
paths.py — 资源文件路径解析

开发模式下（uv run main.py），资源文件（assets/ 目录）就在项目根目录旁边，
用 __file__ 反推路径即可。

但打包成 .app 之后（PyInstaller），程序不再是"项目目录里的一堆 .py 文件"，
而是被解包到一个临时目录（sys._MEIPASS）里运行，原来那种「相对 __file__ 走
两层 parent」的写法会指向一个根本不存在的路径。

resource_path() 统一处理这两种情况：
    - 冻结状态（打包后）：从 sys._MEIPASS 找
    - 开发状态（源码直接跑）：从项目根目录找（本文件所在目录的上一级）
"""

from __future__ import annotations

import sys
from pathlib import Path


def resource_path(*parts: str) -> Path:
    """返回资源文件的绝对路径，兼容源码运行和 PyInstaller 打包后运行。

    用法：resource_path("assets", "web", "highlighter.js")
    """
    if getattr(sys, "frozen", False):
        # PyInstaller 打包后，sys._MEIPASS 是解压出来的临时根目录，
        # 我们在 .spec 里把 assets/ 原样打进了这个目录下
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        # 项目根目录 = 本文件（core/paths.py）的上一级
        base = Path(__file__).parent.parent
    return base.joinpath(*parts)
