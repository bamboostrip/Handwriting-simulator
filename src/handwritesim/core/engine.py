"""手写生成引擎。

默认使用高性能的 numpy/scipy 引擎（FastEngine）以满足实时预览需求；
保留基于 handright 的经典引擎作为可选后端（backend="handright"）。
GUI 与 CLI 均复用本模块。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from PIL import Image

from .engine_fast import FastEngine
from .models import HandwritingParams


class HandwritingEngine:
    """手写生成引擎（默认高性能后端）。"""

    def __init__(self, backend: str = "fast", seed: object | None = None) -> None:
        if backend == "fast":
            self._impl: object = FastEngine(seed)
        elif backend == "handright":
            from .engine_handright import HandrightEngine

            self._impl = HandrightEngine(seed)
        else:
            raise ValueError(f"未知后端：{backend!r}，可选 'fast' / 'handright'")

    def render_preview(self, params: HandwritingParams) -> Image.Image:
        """仅渲染第一张，用于 GUI 预览。"""
        return self._impl.render_preview(params)

    def generate(self, params: HandwritingParams) -> Iterator[Image.Image]:
        """生成手写图序列（惰性迭代）。"""
        return self._impl.generate(params)

    def save_all(self, params: HandwritingParams, out_dir: str | Path) -> list[Path]:
        """将全部手写图导出到 out_dir，返回生成的文件路径列表。"""
        return self._impl.save_all(params, out_dir)