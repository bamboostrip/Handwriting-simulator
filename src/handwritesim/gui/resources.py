"""资源路径解析。

开发模式下资源位于项目根目录的 ui/ 下；打包后（PyInstaller）
资源位于 sys._MEIPASS 指向的目录。本模块统一解析，避免相对路径
在分发场景下失效。
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def resource_root() -> str:
    """返回资源根目录（含 ui/ 子目录）。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:  # PyInstaller 冻结环境
        return str(Path(base))
    # 开发环境：项目根目录（src/../..）
    return str(Path(__file__).resolve().parents[3])


def resource_path(*parts: str) -> str:
    """返回资源文件的绝对路径，例如 resource_path("ui", "3d.ico")。"""
    return os.path.join(resource_root(), *parts)