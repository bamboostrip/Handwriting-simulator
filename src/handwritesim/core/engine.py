"""手写生成引擎。

默认使用高性能的 numpy/scipy 引擎（FastEngine）以满足实时预览需求；
保留基于 handright 的经典引擎作为可选后端（backend="handright"）。
GUI 与 CLI 均复用本模块。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Iterator

import img2pdf
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

    def save_pdf(self, params: HandwritingParams, out_path: str | Path, dpi: float = 300.0) -> Path:
        """将全部手写页导出为 PDF（位图层方案）。

        与 Rust 版 printpdf 导出一致：页物理尺寸 = 像素 @ dpi（默认 300 DPI）；
        每页 PNG 以 pHYs DPI 元数据嵌入，img2pdf 无损直嵌（Flate），
        视觉与 PNG 导出逐像素一致。返回输出文件路径。
        """
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            png_paths: list[Path] = []
            for index, image in enumerate(self.generate(params)):
                png = Path(tmp) / f"{index}.png"
                image.save(png, dpi=(dpi, dpi))
                png_paths.append(png)
            if not png_paths:
                raise RuntimeError("未生成任何页面")
            out_path.write_bytes(img2pdf.convert(png_paths))
        return out_path
