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

import math
import os
import random
from pathlib import Path
from typing import Iterator, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

from .models import HandwritingParams, Paragraph

# 4-连通邻域结构
_CONNECTIVITY = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)

# 错字候选常用汉字表（与 Rust 版 layout.rs 一致）
_COMMON_CHINESE_CHARS = (
    "的一是在了不和有大这主中人国为以我分们行"
    "产作本经发社工己等均部样出名家理"
    "学对里后小多下心然事资力么得之"
    "都平因起只没生量建长现前性那系"
    "各进最及外治与公向情老正路解"
    "问反政化无其期高强使教定重特立"
    "体代通度意见指表命战民保机关党"
    "议写论设合名同由接收改新想打放"
    "儿加用及那此实决求美品书"
    "要法务制清"
    "楚确认真各委局厅所"
)


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


def _wrong_char(ch: str, rand: random.Random) -> str:
    """生成一个与原字符不同的错字（与 Rust 版 get_wrong_char 一致）。

    汉字从常用字表随机取；ASCII 大写/小写/数字各自随机异字；
    其他字符（标点等）原样保留（仍会画删除线）。
    """
    if "A" <= ch <= "Z":
        wrong = ch
        while wrong == ch:
            wrong = chr(ord("A") + rand.randrange(26))
        return wrong
    if "a" <= ch <= "z":
        wrong = ch
        while wrong == ch:
            wrong = chr(ord("a") + rand.randrange(26))
        return wrong
    if "0" <= ch <= "9":
        wrong = ch
        while wrong == ch:
            wrong = chr(ord("0") + rand.randrange(10))
        return wrong
    if "\u4e00" <= ch <= "\u9fa5":
        wrong = ch
        while wrong == ch:
            wrong = _COMMON_CHINESE_CHARS[rand.randrange(len(_COMMON_CHINESE_CHARS))]
        return wrong
    return ch


def _bezier_strike(draw: ImageDraw.ImageDraw, rng: random.Random, x0: float, y0: float,
                   x1: float, y1: float, thickness: int, waviness: float) -> None:
    """画一条带随机弧度的二次贝塞尔删除线段（与 Rust 版 draw_bezier_line 一致）。

    控制点取中点沿法线偏移 waviness，5 段折线一次 draw.line 绘制（C 层执行）。
    """
    mx = (x0 + x1) / 2.0
    my = (y0 + y1) / 2.0
    dx = x1 - x0
    dy = y1 - y0
    length = max(math.hypot(dx, dy), 1.0)
    nx = -dy / length
    ny = dx / length
    offset = 0.0 if waviness <= 0.0 else rng.uniform(-waviness, waviness)
    cx = mx + nx * offset
    cy = my + ny * offset
    points = [(x0, y0)]
    for step in range(1, 6):
        t = step / 5.0
        mt = 1.0 - t
        points.append((mt * mt * x0 + 2.0 * mt * t * cx + t * t * x1,
                       mt * mt * y0 + 2.0 * mt * t * cy + t * t * y1))
    draw.line(points, fill=1, width=thickness)


def _draw_strikeout(draw: ImageDraw.ImageDraw, rng: random.Random, x: float, y_top: float,
                    size: int, wrong_advance: int, angle: float, style: str, ascent: int) -> None:
    """对错字字符绘制删除线（与 Rust 版 draw_miswrite 一致）。

    y_top 为该字符的行顶坐标；angle 为删除线倾角（rad）；粗细/弧度参数
    直接移植 Rust 的调参结果：厚度 max(size*0.035, 1.5)、波动 size*0.08。
    """
    mid_x = x + wrong_advance / 2.0
    mid_y = y_top + ascent * 0.45
    ct = math.cos(angle)
    st = math.sin(angle)
    half_w = wrong_advance * 0.55
    half_h = size * 0.4
    thickness = max(round(size * 0.035), 2)
    waviness = size * 0.08
    if style in ("line", "double_line"):
        rx = half_w * ct
        ry = half_w * st
        _bezier_strike(draw, rng, mid_x - rx, mid_y - ry, mid_x + rx, mid_y + ry, thickness, waviness)
        if style == "double_line":
            offset_y = size * 0.1
            _bezier_strike(draw, rng, mid_x - rx, mid_y - ry - offset_y,
                           mid_x + rx, mid_y + ry - offset_y, thickness, waviness)
            _bezier_strike(draw, rng, mid_x - rx, mid_y - ry + offset_y,
                           mid_x + rx, mid_y + ry + offset_y, thickness, waviness)
    elif style == "slash":
        _bezier_strike(draw, rng, mid_x + half_w * 0.7, mid_y - half_h,
                       mid_x - half_w * 0.7, mid_y + half_h, thickness, waviness)
    else:  # cross
        _bezier_strike(draw, rng, mid_x - half_w * 0.7, mid_y - half_h,
                       mid_x + half_w * 0.7, mid_y + half_h, thickness, waviness)
        _bezier_strike(draw, rng, mid_x + half_w * 0.7, mid_y - half_h,
                       mid_x - half_w * 0.7, mid_y + half_h, thickness, waviness)


# 参照字“十”（横竖各一）用于测量笔画粗细；(字体路径, 字号) -> 笔画宽度
_STROKE_PROBE_CHAR = "十"
_stroke_width_cache: dict[tuple[str, int], float] = {}


def _stroke_width(font: ImageFont.FreeTypeFont) -> float:
    """测量字体在 font.size 下的笔画宽度（像素），按 (字体标识, 字号) 缓存。

    直接用已加载的字体对象渲染探针字符，避免按路径重开字体文件：
    Windows 上非 ASCII 路径的字体由 PIL 以字节加载，font_variant 生成的
    变体字体 .path 是已消费的 BytesIO，重开会读到空内容报 cannot open resource。
    用距离变换取笔画中轴像素到背景距离的两倍（90 分位数抗边缘杂点）。
    """
    size = font.size
    path = font.path
    if isinstance(path, (str, bytes, os.PathLike)):
        cache_key = (path, size)
    elif hasattr(path, "getvalue"):
        cache_key = (path.getvalue(), size)
    else:
        cache_key = (id(font), size)
    cached = _stroke_width_cache.get(cache_key)
    if cached is not None:
        return cached
    probe_size = size * 2
    img = Image.new("1", (probe_size, probe_size), 0)
    ImageDraw.Draw(img).text((size // 2, size // 2), _STROKE_PROBE_CHAR, fill=1, font=font)
    arr = np.asarray(img, dtype=bool)
    if not arr.any():
        width = max(size * 0.035, 1.5)
    else:
        dist = ndimage.distance_transform_edt(arr)
        width = 2.0 * float(np.percentile(dist[arr], 90))
    _stroke_width_cache[cache_key] = width
    return width


def _paste_thickened_char(draw: ImageDraw.ImageDraw, x: float, y: float, text: str,
                          small_font: ImageFont.FreeTypeFont,
                          full_font: ImageFont.FreeTypeFont) -> None:
    """按原字号笔画粗细绘制小字重写（模拟同一支笔）。

    小字号字形的笔画随字号等比变细，先把小字画到临时画布，
    再按（原笔画 - 小字笔画）/2 的半径膨胀补齐后贴回。
    """
    radius = (_stroke_width(full_font) - _stroke_width(small_font)) / 2.0
    if radius < 0.5:
        draw.text((round(x), round(y)), text, fill=1, font=small_font)
        return
    r = round(radius)
    pad = r + 4
    w = math.ceil(small_font.getlength(text)) + 2 * pad
    ascent, descent = small_font.getmetrics()
    h = ascent + descent + 2 * pad
    temp = Image.new("1", (w, h), 0)
    ImageDraw.Draw(temp).text((pad, pad), text, fill=1, font=small_font)
    # 菱形结构：只沿横竖方向加粗，避免方形膨胀把密集小字的近邻笔画糊成一片
    ys, xs = np.indices((2 * r + 1, 2 * r + 1)) - r
    structure = np.abs(ys) + np.abs(xs) <= r
    arr = ndimage.binary_dilation(np.asarray(temp, dtype=bool), structure=structure)
    draw.bitmap((round(x) - pad, round(y) - pad), Image.fromarray(arr), fill=1)


def _draw_miswrite(draw: ImageDraw.ImageDraw, rng: random.Random, x: float, y_top: float,
                   size: int, wrong_advance: int, angle: float, style: str,
                   font: ImageFont.FreeTypeFont, correct_ch: str, draw_small: bool,
                   resolve_font, offset_cache: dict[tuple[int, int], int]) -> None:
    """画删除线；draw_small 时在正上方略偏右补画小一号重写（Above 模式）。"""
    ascent = font.getmetrics()[0]
    _draw_strikeout(draw, rng, x, y_top, size, wrong_advance, angle, style, ascent)
    if draw_small:
        small_size = max(round(size * 0.6), 1)
        small_font = resolve_font(small_size)
        ascent_small = small_font.getmetrics()[0]
        rx = rng.uniform(-size * 0.03, size * 0.03)
        ry = rng.uniform(-size * 0.03, size * 0.03)
        small_x = x + wrong_advance * 0.45 + rx
        small_baseline = max(y_top - size * 0.45 + ascent_small + ry, ascent_small)
        _paste_thickened_char(draw, small_x, small_baseline - ascent_small,
                              correct_ch, small_font, font)


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

    # 错字参数提前解包（循环内避免属性查找）
    miswrite_rate = params.miswrite_rate
    miswrite_active = miswrite_rate > 0
    mode = params.miswrite_rewrite_mode
    strikeout_style = params.miswrite_strikeout_style

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

            yj = round(rand.gauss(y, params.line_spacing_sigma))
            font = base_font
            size = font_size_int
            if params.font_size_sigma:
                size = max(round(rand.gauss(font_size, params.font_size_sigma)), 0)
                if size != font_size:
                    font = resolve_font(size)
            word_noise = rand.gauss(0, params.word_spacing_sigma)
            # 写错字模拟：判定只影响渲染，不参与换行；rate=0 时不消耗 RNG（零回归）
            miswrite = False
            if miswrite_active and rand.random() < miswrite_rate:
                miswrite = True
                wrong_ch = _wrong_char(ch, rand)
                angle = rand.gauss(0, 0.15)
                local_seed = rand.getrandbits(64)
            drawn_ch = wrong_ch if miswrite else ch
            draw.text((round(x), yj), drawn_ch, fill=1, font=font)
            offset = _char_offset(offset_cache, size, drawn_ch, font)
            char_x = x
            x += params.word_spacing + offset + word_noise
            if miswrite:
                local = random.Random(local_seed)
                _draw_miswrite(
                    draw, local, char_x, yj, size, offset, angle,
                    strikeout_style, font, ch, mode == "above",
                    resolve_font, offset_cache,
                )
                if mode == "rewrite":
                    draw.text((round(x), yj), ch, fill=1, font=font)
                    x += _char_offset(offset_cache, size, ch, font) + params.word_spacing

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


def _layout_paragraph(
    params: HandwritingParams,
    rand: random.Random,
    paragraph: Paragraph,
    width: int,
) -> list[tuple[np.ndarray | None, float]]:
    """渲染单个段落，返回逐行列表 [(该行裁剪墨迹 mask, 墨迹相对该行绘制基线的偏移)]。

    空行对应 (None, 0.0)，仅占用行节奏。画布按段落自身高度创建
    （不受页高裁剪），以便拼接时像纯文本路径一样逐行流式跨页。
    """
    base_font = ImageFont.truetype(params.font_path, size=int(params.font_size))
    font_cache: dict[int, ImageFont.FreeTypeFont] = {}
    font_size = params.font_size
    font_size_int = int(font_size)
    line_spacing = float(params.line_spacing) + float(params.font_size)
    end_chars = params.end_chars
    start_chars = params.start_chars
    left = params.left_margin
    right = params.right_margin
    text = paragraph.text
    text_len = len(text)

    # 按字号缓存字符宽度，避免重复 getbbox
    offset_cache: dict[tuple[int, int], int] = {}

    # 错字参数提前解包（循环内避免属性查找）
    miswrite_rate = params.miswrite_rate
    miswrite_active = miswrite_rate > 0
    mode = params.miswrite_rewrite_mode
    strikeout_style = params.miswrite_strikeout_style

    def resolve_font(size: int) -> ImageFont.FreeTypeFont:
        if size not in font_cache:
            font_cache[size] = (
                base_font if size == font_size else base_font.font_variant(size=size)
            )
        return font_cache[size]

    # 阶段一：纯排版（不绘制），随机数消耗顺序与纯文本路径完全一致，
    # 记录每字的 (错字, 原字, x, y, 字号, 行号, 错字标记, 倾角, 局部种子, 重写x)
    # 与每行结束 x（含空格推进量）。错字判定插在每字扰动之后，rate=0 短路。
    chars: list[tuple[str, str, float, int, int, int, bool, float, int, float]] = []
    line_x_ends: list[float] = []
    i = 0
    y = line_spacing - font_size
    line_ys: list[float] = []
    while i < text_len:
        line_ys.append(y)
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
            yj = round(rand.gauss(y, params.line_spacing_sigma))
            size = font_size_int
            if params.font_size_sigma:
                size = max(round(rand.gauss(font_size, params.font_size_sigma)), 0)
                if size != font_size:
                    resolve_font(size)
            word_noise = rand.gauss(0, params.word_spacing_sigma)
            miswrite = False
            wrong_ch = ch
            angle = 0.0
            local_seed = 0
            if miswrite_active and rand.random() < miswrite_rate:
                miswrite = True
                wrong_ch = _wrong_char(ch, rand)
                angle = rand.gauss(0, 0.15)
                local_seed = rand.getrandbits(64)
            font = resolve_font(size) if size != font_size_int else base_font
            offset = _char_offset(offset_cache, size, wrong_ch, font)
            x_next = x + params.word_spacing + offset + word_noise
            rewrite_x = x_next if (miswrite and mode == "rewrite") else 0.0
            chars.append((wrong_ch, ch, x, yj, size, len(line_ys) - 1,
                          miswrite, angle, local_seed, rewrite_x))
            x = x_next
            if miswrite and mode == "rewrite":
                x += _char_offset(offset_cache, size, ch, font) + params.word_spacing
            i += 1
        line_x_ends.append(x)
        y += line_spacing

    if not line_ys:
        return []

    # 右对齐：按每行逻辑宽度（含尾部空格）平移到右边距，
    # 这样尾部空格能把文字从右缘“顶”进来，与 Word 行为一致
    shifts: list[float] | None = None
    if paragraph.align == "right":
        right_x = float(width) - float(right)
        shifts = [right_x - xe for xe in line_x_ends]

    # 居中：按锚定字符（含 Rewrite 重写墨迹范围）计算每行平移，
    # 使小字带/重写与锚定字符同移，避免行带被独立居中导致漂移
    center_shifts: list[float] | None = None
    if paragraph.align == "center":
        min_x = [float("inf")] * len(line_ys)
        max_x = [float("-inf")] * len(line_ys)
        for wrong_ch, correct_ch, cx, cy, size, li, miswrite, angle, local_seed, rewrite_x in chars:
            f = resolve_font(size) if size != font_size_int else base_font
            w = _char_offset(offset_cache, size, wrong_ch, f)
            if cx < min_x[li]:
                min_x[li] = cx
            right_w = cx + w
            if miswrite and mode == "rewrite":
                right_w = max(right_w, rewrite_x + _char_offset(offset_cache, size, correct_ch, f))
            if right_w > max_x[li]:
                max_x[li] = right_w
        center_shifts = [
            0.0 if min_x[li] > max_x[li]
            else (float(width) - (max_x[li] - min_x[li])) / 2.0 - min_x[li]
            for li in range(len(line_ys))
        ]

    # 阶段二：按段落实际高度创建画布并绘制（不被页高裁剪）
    canvas_h = max(int(y + float(params.font_size) + 4 * float(params.line_spacing_sigma) + 4), 1)
    page = Image.new("1", (width, canvas_h), 0)
    draw = ImageDraw.Draw(page)
    for wrong_ch, correct_ch, cx, cy, size, li, miswrite, angle, local_seed, rewrite_x in chars:
        font = base_font if size == font_size_int else resolve_font(size)
        shift = 0.0
        if shifts is not None:
            shift = shifts[li]
        elif center_shifts is not None:
            shift = center_shifts[li]
        dx = cx + shift
        draw.text((round(dx), cy), wrong_ch, fill=1, font=font)
        if miswrite:
            local = random.Random(local_seed)
            wrong_advance = _char_offset(offset_cache, size, wrong_ch, font)
            _draw_miswrite(
                draw, local, dx, cy, size, wrong_advance, angle,
                strikeout_style, font, correct_ch, mode == "above",
                resolve_font, offset_cache,
            )
            if mode == "rewrite":
                draw.text((round(rewrite_x + shift), cy), correct_ch, fill=1, font=font)

    mask = np.asarray(page, dtype=bool)

    # 按行提取墨迹：先用连通行带分出每行墨迹组（行间距大于墨迹高度时
    # 每行自成一带），再按顺序归属到各非空行，避免固定中点切分裁掉墨迹。
    rows = np.any(mask, axis=1)
    bands = _split_text_rows(rows)
    lines: list[tuple[np.ndarray | None, float]] = []
    bi = 0
    # 墨迹相对绘制基线的合理窗口；下限放宽容纳 Above 小字悬浮带，
    # 使所有行保持画布绝对位置；超出说明归属错位，钳制避免
    # 某行被甩到远离其行槽的位置（如页顶残留）
    off_min = -0.85 * float(params.font_size) - 0.25 * line_spacing
    off_max = 0.8 * line_spacing
    for yk in line_ys:
        if bi < len(bands) and bands[bi][0] < yk + line_spacing / 2:
            s = bands[bi][0]
            e = bands[bi][1]
            bi += 1
            # 一行可能包含多个行带（Above 模式的小字重写悬浮在行顶上方），
            # 全部归入该行合并提取，避免多余行带被丢弃
            while bi < len(bands) and bands[bi][0] < yk + line_spacing / 2:
                e = bands[bi][1]
                bi += 1
            off = min(max(float(s) - yk, off_min), off_max)
            lines.append((mask[s:e], off))
        else:
            lines.append((None, 0.0))  # 空行：仅占用行节奏
    return lines


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
        """按段落逐页渲染，逐行流式分页（与纯文本路径填满页面一致）。

        行节奏与纯文本路径对齐：首行绘制基线位于 top + line_spacing（不含字高），
        每行（含段内换行、段落边界、空行）均推进一个完整行距；
        某行的绘制基线越过页底限制时才换页，保证首页不留空白。
        """
        background = np.asarray(Image.open(params.background_path).convert("RGB"))
        rand = self._new_rand()
        height, width = background.shape[:2]
        line_spacing = float(params.line_spacing) + float(params.font_size)
        lead = line_spacing - float(params.font_size)
        # 预览降采样会使边距为浮点，这里取整以保证 numpy 索引为整数
        top, bottom = int(params.top_margin), int(params.bottom_margin)
        # 与纯文本路径 _layout_page 的换页条件一致
        limit = height - bottom - float(params.font_size)

        all_lines: list[tuple[np.ndarray | None, float]] = []
        for para in params.paragraphs or []:
            lines = _layout_paragraph(params, rand, para, width)
            if not lines:
                lines = [(None, 0.0)]  # 空段保留一行空行
            all_lines.extend(lines)

        page_canvas = np.zeros((height, width), dtype=bool)
        yielded = False
        draw_y = float(top) + lead
        for band, off in all_lines:
            # 与纯文本路径一致：每行（含空行）开始前检查是否越页底
            if draw_y > limit and page_canvas.any():
                yield self._finalize(params, page_canvas, background)
                yielded = True
                page_canvas = np.zeros((height, width), dtype=bool)
                draw_y = float(top) + lead
            if band is not None:
                row0 = int(round(draw_y + off))
                ys, xs = np.nonzero(band)
                rows = row0 + ys
                # 裁掉越界行（负索引会回绕到页底产生鬼影）
                valid = (rows >= 0) & (rows < height)
                page_canvas[rows[valid], xs[valid]] = True
            draw_y += line_spacing
        if page_canvas.any() or not yielded:
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