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

from .models import HandwritingParams, Paragraph

# 4-连通邻域结构
_CONNECTIVITY = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)


# ---------------------------------------------------------------------------
# 排版
# ---------------------------------------------------------------------------
def _char_offset(
    offset_cache: dict[tuple[int, int], int],
    size: int,
    ch: str,
    font: ImageFont.FreeTypeFont,
) -> int:
    """取字符横向宽度，按 (字号, 字符) 缓存免去重复 getbbox。"""
    key = (size, ch)
    cached = offset_cache.get(key)
    if cached is not None:
        return cached
    offset = font.getbbox(ch)[2] - font.getbbox(ch)[0]
    offset_cache[key] = offset
    return offset


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
    # 按字号缓存字体与字符宽度，避免每次扰动都重建字体/重新测量
    font_cache: dict[int, ImageFont.FreeTypeFont] = {}
    offset_cache: dict[tuple[int, int], int] = {}

    font_size = params.font_size
    # 行距含字高；用浮点加法而非 total_line_spacing 属性（其内部 int()
    # 截断会破坏预览降采样传入的浮点参数，导致行线错位累积）
    line_spacing = float(params.line_spacing) + float(params.font_size)
    end_chars = params.end_chars
    start_chars = params.start_chars
    top, bottom = params.top_margin, params.bottom_margin
    left, right = params.left_margin, params.right_margin
    text_len = len(text)

    font_size_int = int(font_size)

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
            size = font_size_int
            if params.font_size_sigma:
                size = max(round(rand.gauss(font_size, params.font_size_sigma)), 0)
                if size != font_size:
                    font = resolve_font(size)
            draw.text(xy, ch, fill=1, font=font)
            offset = _char_offset(offset_cache, size, ch, font)
            x += rand.gauss(params.word_spacing + offset, params.word_spacing_sigma)

            i += 1
            if i >= text_len:
                return np.asarray(page, dtype=bool), i
        y += line_spacing
    return np.asarray(page, dtype=bool), i


def _split_text_rows(rows: np.ndarray) -> list[tuple[int, int]]:
    """把行聚合的 bool 数组按连续段分组，返回 [start, end) 列表。"""
    groups: list[tuple[int, int]] = []
    start: int | None = None
    for idx, v in enumerate(rows):
        if v and start is None:
            start = idx
        elif not v and start is not None:
            groups.append((start, idx))
            start = None
    if start is not None:
        groups.append((start, len(rows)))
    return groups


def _center_text_lines(mask: np.ndarray) -> np.ndarray:
    """按文本行测量非零 x 范围，逐行水平居中。"""
    height, width = mask.shape
    rows = np.any(mask, axis=1)
    if not rows.any():
        return mask
    result = np.zeros_like(mask)
    for y0, y1 in _split_text_rows(rows):
        band = mask[y0:y1]
        ys, xs = np.nonzero(band)
        line_w = int(xs.max()) - int(xs.min()) + 1
        if line_w >= width:
            nx_orig = xs
            nys = ys
        else:
            shift = (width - line_w) // 2 - int(xs.min())
            nx_orig = xs + shift
            nys = ys
        valid = (nx_orig >= 0) & (nx_orig < width)
        result[y0 + nys[valid], nx_orig[valid]] = True
    return result


def _layout_paragraph(
    params: HandwritingParams,
    rand: random.Random,
    paragraph: Paragraph,
    width: int,
    height: int,
) -> tuple[np.ndarray, int]:
    """渲染单个段落，返回裁剪后的内容 mask（bool）与占用高度。"""
    page = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(page)
    base_font = ImageFont.truetype(params.font_path, size=int(params.font_size))
    font_cache: dict[int, ImageFont.FreeTypeFont] = {}
    font_size = params.font_size
    line_spacing = float(params.line_spacing) + float(params.font_size)
    end_chars = params.end_chars
    start_chars = params.start_chars
    left = params.left_margin
    right = params.right_margin
    text = paragraph.text
    text_len = len(text)

    # 按字号缓存字符宽度，避免重复 getbbox
    offset_cache: dict[tuple[int, int], int] = {}

    def resolve_font(size: int) -> ImageFont.FreeTypeFont:
        if size not in font_cache:
            font_cache[size] = (
                base_font if size == font_size else base_font.font_variant(size=size)
            )
        return font_cache[size]

    i = 0
    y = line_spacing - font_size
    while i < text_len:
        x = left + (paragraph.first_line_indent if i == 0 else 0)
        while i < text_len:
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
            size = int(font_size)
            if params.font_size_sigma:
                size = max(round(rand.gauss(font_size, params.font_size_sigma)), 0)
                if size != font_size:
                    font = resolve_font(size)
            draw.text(xy, ch, fill=1, font=font)
            offset = _char_offset(offset_cache, size, ch, font)
            x += rand.gauss(params.word_spacing + offset, params.word_spacing_sigma)
            i += 1
        y += line_spacing

    mask = np.asarray(page, dtype=bool)
    if paragraph.align == "center":
        mask = _center_text_lines(mask)
    rows = np.any(mask, axis=1)
    if not rows.any():
        return np.zeros((0, width), dtype=bool), 0
    first = int(np.argmax(rows))
    last = int(len(rows) - 1 - np.argmax(rows[::-1]))
    return mask[first : last + 1], last - first + 1


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
        if params.paragraphs:
            return next(self._paragraph_pages(params))
        rand = self._new_rand()
        mask, _ = _layout_page(params, rand, params.text, 0)
        background = np.asarray(Image.open(params.background_path).convert("RGB"))
        canvas = _perturb_mask(mask, params, self._rng, background)
        return Image.fromarray(canvas, mode="RGB")

    def generate(self, params: HandwritingParams) -> Iterator[Image.Image]:
        """生成手写图序列（惰性迭代），与引擎接口一致。"""
        return self.generate_pages(params)

    def _paragraph_pages(self, params: HandwritingParams) -> Iterator[Image.Image]:
        """按段落逐页渲染（段落为跨页最小单位）。"""
        background = np.asarray(Image.open(params.background_path).convert("RGB"))
        rand = self._new_rand()
        height, width = background.shape[:2]
        line_spacing = float(params.line_spacing) + float(params.font_size)
        # 预览降采样会使边距为浮点，这里取整以保证 numpy 索引为整数
        top, bottom = int(params.top_margin), int(params.bottom_margin)

        para_masks: list[tuple[np.ndarray, int]] = []
        for para in params.paragraphs or []:
            if not para.text:
                continue
            content, ph = _layout_paragraph(params, rand, para, width, height)
            para_masks.append((content, ph))

        page_canvas = np.zeros((height, width), dtype=bool)
        used = top
        for content, ph in para_masks:
            if ph <= 0:
                continue
            if used + ph > height - bottom:
                yield self._finalize(params, page_canvas, background)
                page_canvas = np.zeros((height, width), dtype=bool)
                used = top
            ys, xs = np.nonzero(content)
            page_canvas[used + ys, xs] = True
            used += ph + int(round(line_spacing))
        if page_canvas.any():
            yield self._finalize(params, page_canvas, background)

    # _finalize 使用 self._rng，与 generate_pages 共用同一随机源
    def _finalize(self, params, mask, background):
        return Image.fromarray(_perturb_mask(mask, params, self._rng, background), mode="RGB")

    def generate_pages(self, params: HandwritingParams) -> Iterator[Image.Image]:
        """逐页生成手写图（惰性迭代）。"""
        params.validate()
        if params.paragraphs:
            yield from self._paragraph_pages(params)
            return
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