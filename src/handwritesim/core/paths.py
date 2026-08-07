"""资产目录解析：便携模式下资源位于 exe 旁边（打包后）或项目根（开发时）。

便携版设计：exe 与 fonts/、backgrounds/、presets/ 三个目录放在同一层，
所有相对路径以「资产根目录」为锚点解析，用户把整个文件夹拷到任意位置
都能正常使用，无需修改任何路径。目录名统一用英文，避免中文路径在
部分环境下的兼容问题。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 便携版目录名（英文，避免中文路径兼容问题）
ASSET_DIRS = ("fonts", "backgrounds", "presets")


def assets_root() -> str:
    """返回资产根目录：打包后为 exe 所在目录，开发时为项目根目录。"""
    if getattr(sys, "frozen", False):  # PyInstaller 冻结环境
        return str(Path(sys.executable).resolve().parent)
    # 开发环境：src/handwritesim/core/paths.py -> 项目根
    return str(Path(__file__).resolve().parents[3])


def ensure_assets_dirs() -> dict[str, str]:
    """确保资产子目录存在并返回 {名字: 绝对路径} 映射。

    双击运行程序时调用，为用户准备好 fonts/、backgrounds/、presets/，
    用户只需把字体放入 fonts/ 即可使用。
    """
    root = Path(assets_root())
    out: dict[str, str] = {}
    for name in ASSET_DIRS:
        target = root / name
        target.mkdir(parents=True, exist_ok=True)
        out[name] = str(target)
    return out