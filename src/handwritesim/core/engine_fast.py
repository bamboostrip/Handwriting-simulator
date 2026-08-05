"""高性能手写渲染引擎。

针对 handright 逐像素纯 Python 循环的性能瓶颈，用 numpy + scipy 重写：

1. 排版：保留 PIL 的 C 层 ImageDraw.text 逐字绘制（本就快，非瓶颈）。
2. 连通区域提取：用 scipy.ndimage.label（C 实现）替代原 Python DFS，
   一次得到所有笔画的像素标签。
3. 笔画扰动：对每个笔画用 numpy 向量化坐标变换（旋转 + 平移），
   替代逐像素 Python 循环，最后一次性写回画布。

不依赖 handright，接口与 HandwritingEngine 兼容。
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Iterator, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

from .models import HandwritingParams

# 4-连通邻域结构
_CONNECTIVITY = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)


# ---------------------------------------------------------------------------
# 排版
# ---------------------------------------------------------------------------
def _layout_page(
    params: HandwritingParams,
    rand: random.Random,
    text: str,
    start: int,
) -> Tuple[np.ndarray, int]:
    """排版一页文字，返回前景掩码（bool 数组）与本页消费的字符数。

    复刻 handright 的 _draw_page + _flow_layout 逻辑：逐字绘制、
    行/字/字号高斯扰动、end_chars/start_chars 换行规则。
    """
    with Image.open(params.background_path) as bg:
        width, height = bg.size
    page = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(page)
    base_font = ImageFont.truetype(params.font_path, size=int(params.font_size))
    # 按字号缓存字体，避免每次扰动都重建字体对象
    font_cache: dict[int, ImageFont.FreeTypeFont] = {}

    font_size = params.font_size
    # 行距含字高；用浮点加法而非 total_line_spacing 属性（其内部 int()
    # 截断会破坏预览降采样传入的浮点参数，导致行线错位累积）
    line_spacing = float(params.line_spacing) + float(params.font_size)
    end_chars = params.end_chars
    start_chars = params.start_chars
    top, bottom = params.top_margin, params.bottom_margin
    left, right = params.left_margin, params.right_margin
    text_len = len(text)

    def resolve_font(size: int) -> ImageFont.FreeTypeFont:
        if size not in font_cache:
            font_cache[size] = (
                base_font if size == font_size else base_font.font_variant(size=size)
            )
        return font_cache[size]

    i = start
    y = top + line_spacing - font_size
    while y <= height - bottom - font_size:
        x = left
        while True:
            if i >= text_len:
                return np.asarray(page, dtype=bool), i
            ch = text[i]
            if ch == "\n":
                i += 1
                break
            if x > width - right - 2 * font_size and ch in start_chars:
                break
            if x > width - right - font_size and ch not in end_chars:
                break

            xy = (round(x), round(rand.gauss(y, params.line_spacing_sigma)))
            font = base_font
            if params.font_size_sigma:
                size = max(round(rand.gauss(font_size, params.font_size_sigma)), 0)
                if size != font_size:
                    font = resolve_font(size)
            draw.text(xy, ch, fill=1, font=font)
            offset = font.getbbox(ch)[2] - font.getbbox(ch)[0]
            x += rand.gauss(params.word_spacing + offset, params.word_spacing_sigma)

            i += 1
            if i >= text_len:
                return np.asarray(page, dtype=bool), i
        y += line_spacing
    return np.asarray(page, dtype=bool), i


# ---------------------------------------------------------------------------
# 笔画扰动（全向量化）
# ---------------------------------------------------------------------------
def _perturb_mask(
    mask: np.ndarray,
    params: HandwritingParams,
    rng: np.random.Generator,
    background: np.ndarray,
) -> np.ndarray:
    """对前景掩码按笔画做独立随机扰动并写回画布。

    全向量化：一次为所有笔画生成扰动参数，再用 label 索引批量变换
    所有前景像素，避免逐笔画 Python 循环。返回 RGB 画布（H, W, 3）。
    """
    height, width = mask.shape
    canvas = background.copy()
    if not mask.any():
        return canvas

    labels, n_strokes = ndimage.label(mask, structure=_CONNECTIVITY)
    fill = np.array(params.fill, dtype=np.uint8)

    # 每笔画的独立扰动参数（批量生成）
    dxs = rng.normal(0, params.perturb_x_sigma, n_strokes)
    dys = rng.normal(0, params.perturb_y_sigma, n_strokes)
    thetas = rng.normal(0, params.perturb_theta_sigma, n_strokes)

    # 每笔画包围盒中心（与 handright 的 (min+max)/2 一致）
    # 约定 centers[i, 0] = x（列），centers[i, 1] = y（行）
    centers = np.zeros((n_strokes, 2), dtype=np.float64)
    slices = ndimage.find_objects(labels)
    for i, sl in enumerate(slices):
        if sl is not None:
            centers[i, 0] = (sl[1].start + sl[1].stop) / 2.0
            centers[i, 1] = (sl[0].start + sl[0].stop) / 2.0

    ys, xs = np.nonzero(labels > 0)
    lbl = labels[ys, xs] - 1  # 0 基标签
    cx = centers[lbl, 0]
    cy = centers[lbl, 1]
    dx = dxs[lbl]
    dy = dys[lbl]
    theta = thetas[lbl]

    ct = np.cos(theta)
    st = np.sin(theta)
    fx = (xs - cx) * ct + (ys - cy) * st + cx
    fy = (ys - cy) * ct - (xs - cx) * st + cy

    nx = np.rint(fx + dx).astype(np.int64)
    ny = np.rint(fy + dy).astype(np.int64)
    valid = (nx >= 0) & (nx < width) & (ny >= 0) & (ny < height)
    canvas[ny[valid], nx[valid]] = fill
    return canvas


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------
class FastEngine:
    """基于 numpy/scipy 的高性能手写渲染引擎。"""

    def __init__(self, seed: object | None = None) -> None:
        self._seed = seed
        self._rng = np.random.default_rng(seed)

    def _new_rand(self) -> random.Random:
        """排版用的随机源（逐字扰动）。"""
        return random.Random(self._seed)

    def render_preview(self, params: HandwritingParams) -> Image.Image:
        """仅渲染第一页，用于预览。"""
        params.validate()
        rand = self._new_rand()
        mask, _ = _layout_page(params, rand, params.text, 0)
        background = np.asarray(Image.open(params.background_path).convert("RGB"))
        canvas = _perturb_mask(mask, params, self._rng, background)
        return Image.fromarray(canvas, mode="RGB")

    def generate(self, params: HandwritingParams) -> Iterator[Image.Image]:
        """生成手写图序列（惰性迭代），与引擎接口一致。"""
        return self.generate_pages(params)

    def generate_pages(self, params: HandwritingParams) -> Iterator[Image.Image]:
        """逐页生成手写图（惰性迭代）。"""
        params.validate()
        background = np.asarray(Image.open(params.background_path).convert("RGB"))
        rand = self._new_rand()
        start = 0
        while True:
            mask, start = _layout_page(params, rand, params.text, start)
            canvas = _perturb_mask(mask, params, self._rng, background)
            yield Image.fromarray(canvas, mode="RGB")
            if start >= len(params.text):
                break

    def save_all(self, params: HandwritingParams, out_dir: str | Path) -> list[Path]:
        """将全部手写页导出到 out_dir，返回文件路径列表。"""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        files: list[Path] = []
        for index, image in enumerate(self.generate_pages(params)):
            path = out_dir / f"{index}.png"
            image.save(path)
            files.append(path)
        if not files:
            raise RuntimeError("未生成任何图片")
        return files