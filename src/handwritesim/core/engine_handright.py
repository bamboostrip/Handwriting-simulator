"""基于 handright 的经典渲染引擎（可选后端）。

接口与 FastEngine 一致，供需要对照或依赖 handright 行为时使用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from PIL import Image, ImageFont

from handright import Template, handwrite

from .models import HandwritingParams


class HandrightEngine:
    """封装 handright 的手写生成引擎。"""

    def __init__(self, seed: object | None = None) -> None:
        self._seed = seed

    def _build_template(self, params: HandwritingParams) -> Template:
        params.validate()
        return Template(
            background=Image.open(params.background_path),
            font=ImageFont.truetype(params.font_path, size=int(params.font_size)),
            line_spacing=params.total_line_spacing,
            fill=params.fill,
            left_margin=int(params.left_margin),
            top_margin=int(params.top_margin),
            right_margin=int(params.right_margin) - int(params.word_spacing) * 2,
            bottom_margin=int(params.bottom_margin),
            word_spacing=int(params.word_spacing),
            line_spacing_sigma=int(params.line_spacing_sigma),
            font_size_sigma=int(params.font_size_sigma),
            word_spacing_sigma=int(params.word_spacing_sigma),
            end_chars=params.end_chars,
            start_chars=params.start_chars,
            perturb_x_sigma=int(params.perturb_x_sigma),
            perturb_y_sigma=int(params.perturb_y_sigma),
            perturb_theta_sigma=float(params.perturb_theta_sigma),
        )

    def generate(self, params: HandwritingParams) -> Iterator[Image.Image]:
        """生成手写图序列（惰性迭代）。"""
        template = self._build_template(params)
        return (im.convert("RGB") for im in handwrite(params.text, template, seed=self._seed))

    def render_preview(self, params: HandwritingParams) -> Image.Image:
        """仅渲染第一张，用于 GUI 预览。"""
        params.validate()
        for image in self.generate(params):
            return image.convert("RGBA")
        raise RuntimeError("未生成任何图片")

    def save_all(self, params: HandwritingParams, out_dir: str | Path) -> list[Path]:
        """将全部手写图导出到 out_dir，返回文件路径列表。"""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        files: list[Path] = []
        for index, image in enumerate(self.generate(params)):
            path = out_dir / f"{index}.png"
            image.save(path)
            files.append(path)
        if not files:
            raise RuntimeError("未生成任何图片")
        return files