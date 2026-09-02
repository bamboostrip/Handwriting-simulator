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

import copy
import math
import os
import random
from pathlib import Path
from typing import Iterator, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

from .models import HandwritingParams, Paragraph, TextRegion, TextRun, HandwritingRole, parse_color

try:
    from .system_fonts import family_to_file as _family_to_file
except Exception:  # 导入失败时降级为空实现
    def _family_to_file(family: str):  # type: ignore
        return None

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


def _layout_text(
    params: HandwritingParams,
    rand: random.Random,
    text: str,
    start: int,
    width: int,
    height: int,
    force_first_line: bool = False,
) -> Tuple[np.ndarray, int]:
    """在 width×height 画布内排版文字，返回前景掩码与消费的字符数。

    复刻 handright 的 _draw_page + _flow_layout 逻辑：逐字绘制、
    行/字/字号高斯扰动、end_chars/start_chars 换行规则。
    页面路径传背景尺寸，区域路径传框选矩形的宽高；
    区域路径传 force_first_line=True，矩形再矮也至少排一行。
    """
    page = Image.new("1", (width, height), 0)
    # 纯背景预览（无文字）时不加载字体，允许 font_path 为空
    if start >= len(text):
        return np.asarray(page, dtype=bool), start
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
    first_line = True
    while y <= height - bottom - font_size or (force_first_line and first_line):
        first_line = False
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
                size = max(round(rand.gauss(font_size, params.font_size_sigma)), 1)
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


# ---------------------------------------------------------------------------
# 多角色 Run 混排辅助
# ---------------------------------------------------------------------------

def _color_hex_for_run(params: HandwritingParams, run: TextRun, role: HandwritingRole | None) -> str:
    if run.color:
        return run.color
    if role and role.color:
        return role.color
    return params.color

def _effective_font_for_run(
    params: HandwritingParams, role: HandwritingRole | None, size: int,
    font_cache_keyed: dict[tuple[str, int], ImageFont.FreeTypeFont]
) -> ImageFont.FreeTypeFont:
    fp = role.font_path if (role and role.font_path) else params.font_path
    # 缓存 key (路径, 字号)
    key = (fp, size)
    cached = font_cache_keyed.get(key)
    if cached is not None:
        return cached
    try:
        font = ImageFont.truetype(fp, size=size)
    except Exception:
        # 回退主字体
        font = ImageFont.truetype(params.font_path, size=size)
    font_cache_keyed[key] = font
    return font

def _role_perturb_overrides(params: HandwritingParams, role: HandwritingRole | None) -> tuple[int, int, float]:
    """返回该角色实际的 (perturb_x, perturb_y, perturb_theta)；打印体强制 0。"""
    if role and role.printed:
        return 0, 0, 0.0
    px = role.perturb_x_sigma if (role and role.perturb_x_sigma is not None) else params.perturb_x_sigma
    py = role.perturb_y_sigma if (role and role.perturb_y_sigma is not None) else params.perturb_y_sigma
    pt = role.perturb_theta_sigma if (role and role.perturb_theta_sigma is not None) else params.perturb_theta_sigma
    return int(px), int(py), float(pt)

def _layout_paragraph_plain(
    params: HandwritingParams,
    rand: random.Random,
    paragraph: Paragraph,
    width: int,
) -> list[tuple[np.ndarray | None, float]]:
    """原 _layout_paragraph 的纯文本实现（兼容保持不变）。"""
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
                size = max(round(rand.gauss(font_size, params.font_size_sigma)), 1)
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


def _layout_paragraph_mixed(
    params: HandwritingParams,
    rand: random.Random,
    paragraph: Paragraph,
    width: int,
) -> list[tuple[dict[str, np.ndarray] | None, float]]:
    """多 Run 混排段落渲染，返回逐行列表 [(该行各颜色的mask字典, 偏移)]。

    每个字典 key 为 #RRGGBB 颜色，value 为该行该颜色的裁剪掩码（H行×W列）。
    空行对应 (None, 0.0)。画布高度按段落自身高度创建。
    支持不同 Run 不同字体/字号/颜色同行混排，自然换行。
    """
    # 角色映射
    role_map: dict[int, HandwritingRole] = {r.id: r for r in params.effective_roles()}
    runs = paragraph.runs or []
    if not runs:
        return [(None, 0.0)]
    # 为每个 Run 解析有效字体/颜色
    # 缓存 (font_path, size) -> font
    font_cache_keyed: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
    offset_cache: dict[tuple[str, int, str], int] = {}  # (font_path, size, ch) -> advance
    # 为便于后续测量，预构建 run_infos
    # 每个 run 的基础字号（未扰动）
    # 每个 Run 的合并键：role_id + 颜色 + 字体区分（用于分层扰动，避免打印/手写同色被合并）
    run_bases: list[tuple[str, str, int, str, bool, HandwritingRole | None, bool, int]] = []  # (text, font_path, base_size, color_hex, printed, role, bold, role_id)
    distinct_keys: set[tuple[int, str]] = set()  # (role_id, color)
    for run in runs:
        role = role_map.get(run.role_id)
        is_printed = (role.printed if role else False)
        is_bold = bool(getattr(run, "bold", False)) and is_printed
        if is_printed and (run.font_family or run.font_file or run.font_size or is_bold):
            if run.font_file and Path(run.font_file).is_file():
                fp = run.font_file
            elif run.font_family:
                try:
                    p = _family_to_file(run.font_family)
                    if p and Path(p).is_file():
                        fp = str(p)
                    else:
                        fp = role.font_path if (role and role.font_path) else params.font_path
                except Exception:
                    fp = role.font_path if (role and role.font_path) else params.font_path
            else:
                fp = role.font_path if (role and role.font_path) else params.font_path
            if run.font_size and run.font_size > 0:
                base_sz = int(run.font_size)
            else:
                base_sz = (role.font_size if (role and role.font_size > 0) else int(params.font_size))
        else:
            fp = role.font_path if (role and role.font_path) else params.font_path
            base_sz = (role.font_size if (role and role.font_size > 0) else int(params.font_size))
        col = _color_hex_for_run(params, run, role)
        printed = is_printed
        distinct_keys.add((run.role_id, col.lower()))
        run_bases.append((run.text, fp, base_sz, col.lower(), printed, role, is_bold, run.role_id))

    line_spacing = float(params.line_spacing) + float(params.font_size)
    end_chars = params.end_chars
    start_chars = params.start_chars
    left = params.left_margin
    right = params.right_margin

    # 扁平字符流：记录每个字符归属的 run_idx，便于按 run 取字体/颜色
    # 结构: (ch, run_idx)
    flat: list[tuple[str, int]] = []
    for idx, (text, _, _, _, _, _, _, _) in enumerate(run_bases):
        for ch in text:
            flat.append((ch, idx))
    n = len(flat)
    miswrite_rate = params.miswrite_rate
    miswrite_active = miswrite_rate > 0
    mode = params.miswrite_rewrite_mode
    strikeout_style = params.miswrite_strikeout_style

    def char_font_and_size(run_idx: int, size: int) -> ImageFont.FreeTypeFont:
        _, fp, _, _, _, _, _, _ = run_bases[run_idx]
        # 直接使用 run_bases 解析好的 fp（含打印体原文系统字体），避免再次回落到角色字体
        key = (fp, size)
        cached = font_cache_keyed.get(key)
        if cached is not None:
            return cached
        try:
            font = ImageFont.truetype(fp, size=size)
        except Exception:
            # 回落手写主字体
            try:
                font = ImageFont.truetype(params.font_path, size=size)
            except Exception:
                font = ImageFont.load_default()
        font_cache_keyed[key] = font
        return font

    def char_offset_for(run_idx: int, size: int, ch: str) -> int:
        _, fp, _, _, _, _, _, _ = run_bases[run_idx]
        key = (fp, size, ch)
        cached = offset_cache.get(key)
        if cached is not None:
            return cached
        font = char_font_and_size(run_idx, size)
        off = font.getbbox(ch)[2] - font.getbbox(ch)[0]
        offset_cache[key] = off
        return off

    # 阶段一：排版规划，消耗随机数与纯文本路径一致的顺序（但 per-run 字体影响宽度）
    # 记录每字 (wrong_ch, correct_ch, x, yj, size, li, miswrite, angle, local_seed, rewrite_x, run_idx, color, bold)
    chars: list[tuple[str, str, float, int, int, int, bool, float, int, float, int, str, bool]] = []
    line_x_ends: list[float] = []
    line_ys: list[float] = []
    i = 0
    y = line_spacing - float(params.font_size)
    # run pointer通过 flat 索引 i 来定位
    # 为便于换行判断，我们需要在内层通过 flat[i] 获取当前 run_idx 与字符
    line_start_i = 0
    # 简化：直接按 flat 顺序推进，但段首缩进仅第一行生效
    # 为与 plain 一致，段首缩进加入首行 x 起点
    # 这里复刻 plain 的两层循环结构，但字符源改为 flat
    # 外层：行
    pos = 0
    while pos < n:
        line_ys.append(y)
        # 首行缩进仅对段内首行生效（pos==0 时）
        if pos == 0:
            x = left + paragraph.first_line_indent
        else:
            x = left
        # 内层：行内字符
        while pos < n:
            ch, run_idx = flat[pos]
            _, fp, base_sz, col, _, _, is_bold, _ = run_bases[run_idx]
            if ch == "\n":
                pos += 1
                break
            # 换行判断：使用该 run 的 base_sz 作为阈值参考（类似 plain 的 font_size）
            # 为保证不同字号混排时行尾判断仍稳定，取当前字符对应 base_sz
            cur_font_size = base_sz
            if x > width - right - 2 * cur_font_size and ch in start_chars:
                break
            if x > width - right - cur_font_size and ch not in end_chars:
                break
            yj = round(rand.gauss(y, params.line_spacing_sigma))
            size = base_sz
            if params.font_size_sigma:
                size = max(round(rand.gauss(base_sz, params.font_size_sigma)), 1)
            word_noise = rand.gauss(0, params.word_spacing_sigma)
            miswrite = False
            wrong_ch = ch
            angle = 0.0
            local_seed = 0
            if miswrite_active and rand.random() < miswrite_rate:
                # 对于打印体角色，跳过错字（角色级 printed 强制不写错）
                role = role_map.get(runs[run_idx].role_id)
                if not (role and role.printed):
                    miswrite = True
                    wrong_ch = _wrong_char(ch, rand)
                    angle = rand.gauss(0, 0.15)
                    local_seed = rand.getrandbits(64)
            # 测量 wrong_ch 在扰动后字号下的宽度（加粗时额外 +1px 模拟描边宽度）
            font_for_measure = char_font_and_size(run_idx, size)
            offset = char_offset_for(run_idx, size, wrong_ch)
            if is_bold:
                offset += 1
            x_next = x + params.word_spacing + offset + word_noise
            rewrite_x = x_next if (miswrite and mode == "rewrite") else 0.0
            chars.append((wrong_ch, ch, x, yj, size, len(line_ys)-1, miswrite, angle, local_seed, rewrite_x, run_idx, col, is_bold))
            x = x_next
            if miswrite and mode == "rewrite":
                # 重写字的宽度额外推进（加粗同样 +1）
                extra = 1 if is_bold else 0
                x += char_offset_for(run_idx, size, ch) + extra + params.word_spacing
            pos += 1
            # 若刚填满一行且下一个字符会导致超宽，外层循环会换行；这里继续直到 pos 满行条件在下一迭代触发
        line_x_ends.append(x)
        y += line_spacing
        # 若当前 pos 指向的字符是 \n 已在内层break消费，无需额外处理
        # 否则若因宽度触发 break，则保留 pos 不动，外层开启新行

    if not line_ys:
        return []

    # 对齐位移
    shifts: list[float] | None = None
    if paragraph.align == "right":
        right_x = float(width) - float(right)
        shifts = [right_x - xe for xe in line_x_ends]
    center_shifts: list[float] | None = None
    if paragraph.align == "center":
        min_x = [float("inf")] * len(line_ys)
        max_x = [float("-inf")] * len(line_ys)
        for wrong_ch, correct_ch, cx, cy, size, li, miswrite, angle, local_seed, rewrite_x, run_idx, col, is_bold in chars:
            font = char_font_and_size(run_idx, size)
            w = char_offset_for(run_idx, size, wrong_ch) + (1 if is_bold else 0)
            if cx < min_x[li]:
                min_x[li] = cx
            right_w = cx + w
            if miswrite and mode == "rewrite":
                # 重写字也在同一颜色层，仍按其宽度计算右边界（重写字跟随原 Run 是否加粗）
                rw = char_offset_for(run_idx, size, correct_ch) + (1 if is_bold else 0)
                right_w = max(right_w, rewrite_x + rw)
            if right_w > max_x[li]:
                max_x[li] = right_w
        center_shifts = [
            0.0 if min_x[li] > max_x[li] else (float(width) - (max_x[li] - min_x[li])) / 2.0 - min_x[li]
            for li in range(len(line_ys))
        ]

    # 阶段二：按 (角色,颜色) 分层绘制（避免打印/手写同色被合并导致扰动错误）
    canvas_h = max(int(y + float(params.font_size) + 4 * float(params.line_spacing_sigma) + 4), 1)
    # (role_id, color) -> PIL 1-bit 画布
    key_to_image: dict[tuple[int, str], Image.Image] = {}
    key_to_draw: dict[tuple[int, str], ImageDraw.ImageDraw] = {}
    for rid, col in distinct_keys:
        key = (rid, col)
        img = Image.new("1", (width, canvas_h), 0)
        key_to_image[key] = img
        key_to_draw[key] = ImageDraw.Draw(img)
    # 额外颜色/角色可能在绘制时动态出现（如标签新色），兜底
    for wrong_ch, correct_ch, cx, cy, size, li, miswrite, angle, local_seed, rewrite_x, run_idx, col, is_bold in chars:
        rid = run_bases[run_idx][7]
        key = (rid, col)
        if key not in key_to_image:
            img = Image.new("1", (width, canvas_h), 0)
            key_to_image[key] = img
            key_to_draw[key] = ImageDraw.Draw(img)
        font = char_font_and_size(run_idx, size)
        shift = 0.0
        if shifts is not None:
            shift = shifts[li]
        elif center_shifts is not None:
            shift = center_shifts[li]
        dx = cx + shift
        draw = key_to_draw[key]
        # 加粗：打印体 bold 用描边模拟（兼容 1-bit 模式）
        if is_bold:
            try:
                draw.text((round(dx), cy), wrong_ch, fill=1, font=font, stroke_width=1, stroke_fill=1)
            except TypeError:
                draw.text((round(dx), cy), wrong_ch, fill=1, font=font)
                draw.text((round(dx)+1, cy), wrong_ch, fill=1, font=font)
        else:
            draw.text((round(dx), cy), wrong_ch, fill=1, font=font)
        if miswrite:
            local = random.Random(local_seed)
            wrong_advance = char_offset_for(run_idx, size, wrong_ch) + (1 if is_bold else 0)
            def resolve_for_mis(s: int):
                return char_font_and_size(run_idx, s)
            _draw_miswrite(
                draw, local, dx, cy, size, wrong_advance, angle,
                strikeout_style, font, correct_ch, mode == "above",
                resolve_for_mis, {},
            )
            if mode == "rewrite":
                if is_bold:
                    try:
                        draw.text((round(rewrite_x + shift), cy), correct_ch, fill=1, font=font, stroke_width=1, stroke_fill=1)
                    except TypeError:
                        draw.text((round(rewrite_x + shift), cy), correct_ch, fill=1, font=font)
                        draw.text((round(rewrite_x + shift)+1, cy), correct_ch, fill=1, font=font)
                else:
                    draw.text((round(rewrite_x + shift), cy), correct_ch, fill=1, font=font)

    # 转为 numpy 并求并集用于行带切分
    key_to_mask: dict[tuple[int, str], np.ndarray] = {k: np.asarray(img, dtype=bool) for k, img in key_to_image.items()}
    # union
    if key_to_mask:
        union = np.zeros((canvas_h, width), dtype=bool)
        for m in key_to_mask.values():
            union |= m
    else:
        union = np.zeros((canvas_h, width), dtype=bool)
    rows = np.any(union, axis=1)
    bands = _split_text_rows(rows)
    lines: list[tuple[dict[tuple[int, str], np.ndarray] | None, float]] = []
    bi = 0
    off_min = -0.85 * float(params.font_size) - 0.25 * line_spacing
    off_max = 0.8 * line_spacing
    for yk in line_ys:
        if bi < len(bands) and bands[bi][0] < yk + line_spacing / 2:
            s = bands[bi][0]
            e = bands[bi][1]
            bi += 1
            while bi < len(bands) and bands[bi][0] < yk + line_spacing / 2:
                e = bands[bi][1]
                bi += 1
            off = min(max(float(s) - yk, off_min), off_max)
            # 为该行提取各 (角色,颜色) 切片
            band_dict: dict[tuple[int, str], np.ndarray] = {}
            for key, full in key_to_mask.items():
                sl = full[s:e]
                if sl.any():
                    band_dict[key] = sl.copy()
            lines.append((band_dict if band_dict else None, off))
        else:
            lines.append((None, 0.0))
    return lines


def _layout_paragraph(
    params: HandwritingParams,
    rand: random.Random,
    paragraph: Paragraph,
    width: int,
) -> list[tuple[np.ndarray | None, float]] | list[tuple[dict[str, np.ndarray] | None, float]]:
    """分发：含 runs 的段落走多角色混排，否则走纯文本路径。

    返回类型为 Union：纯文本返回 (mask|None, off)，混排返回 (dict|None, off)。
    调用方需通过 isinstance 检查区分。
    """
    if paragraph.runs is not None and any(r.text for r in paragraph.runs):
        # 若仅单 Run 且 role 0，可回落 plain 以保持完全一致的随机数与像素级回归
        if len(paragraph.runs) == 1 and paragraph.runs[0].role_id == 0 and not paragraph.runs[0].color:
            # 退化为单 run 纯文本，等价于 paragraph.text 情况
            # 但为保持排版输入一致，直接走 plain 用 paragraph.text 或 run.text
            plain_para = Paragraph(text=paragraph.runs[0].text, align=paragraph.align, first_line_indent=paragraph.first_line_indent)
            return _layout_paragraph_plain(params, rand, plain_para, width)
        return _layout_paragraph_mixed(params, rand, paragraph, width)
    return _layout_paragraph_plain(params, rand, paragraph, width)


# ---------------------------------------------------------------------------
# 笔画扰动（全向量化）
# ---------------------------------------------------------------------------
def _perturbed_positions(
    mask: np.ndarray,
    params: HandwritingParams,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """对前景掩码按笔画做独立随机扰动，返回扰动后的墨迹像素坐标 (ys, xs)。

    全向量化：一次为所有笔画生成扰动参数，再用 label 索引批量变换
    所有前景像素，避免逐笔画 Python 循环。坐标已裁剪到掩码尺寸内。
    """
    height, width = mask.shape
    if not mask.any():
        empty = np.zeros(0, dtype=np.int64)
        return empty, empty.copy()

    labels, n_strokes = ndimage.label(mask, structure=_CONNECTIVITY)

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
    return ny[valid], nx[valid]


def _perturbed_positions_with_sigmas(
    mask: np.ndarray,
    perturb_x_sigma: int | float,
    perturb_y_sigma: int | float,
    perturb_theta_sigma: float,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """按显式 sigma 对掩码做扰动（用于多角色分色）。"""
    height, width = mask.shape
    if not mask.any():
        empty = np.zeros(0, dtype=np.int64)
        return empty, empty.copy()
    labels, n_strokes = ndimage.label(mask, structure=_CONNECTIVITY)
    dxs = rng.normal(0, perturb_x_sigma, n_strokes)
    dys = rng.normal(0, perturb_y_sigma, n_strokes)
    thetas = rng.normal(0, perturb_theta_sigma, n_strokes)
    centers = np.zeros((n_strokes, 2), dtype=np.float64)
    slices = ndimage.find_objects(labels)
    for i, sl in enumerate(slices):
        if sl is not None:
            centers[i, 0] = (sl[1].start + sl[1].stop) / 2.0
            centers[i, 1] = (sl[0].start + sl[0].stop) / 2.0
    ys, xs = np.nonzero(labels > 0)
    lbl = labels[ys, xs] - 1
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
    return ny[valid], nx[valid]


def _perturb_mask(
    mask: np.ndarray,
    params: HandwritingParams,
    rng: np.random.Generator,
    background: np.ndarray,
) -> np.ndarray:
    """对前景掩码按笔画做独立随机扰动并写回画布。返回 RGB 画布（H, W, 3）。"""
    canvas = background.copy()
    ny, nx = _perturbed_positions(mask, params, rng)
    canvas[ny, nx] = np.array(params.fill, dtype=np.uint8)
    return canvas


def _perturb_mask_colored(
    colored_masks: dict[str, np.ndarray] | dict[tuple[int, str], np.ndarray],
    params: HandwritingParams,
    rng: np.random.Generator,
    background: np.ndarray,
) -> np.ndarray:
    """多色版本：colored_masks key=#RRGGBB 或 (role_id, #RRGGBB)，按角色 sigma 分别扰动后着色。

    键为 (role_id, color) 时可精确区分打印/手写同色（如同为 #000000 但打印零扰动）。
    键为旧版 str 时回退按颜色匹配角色。
    """
    canvas = background.copy()
    # 兼容旧版 str 键与新版 tuple 键
    role_by_color: dict[str, HandwritingRole | None] = {}
    for role in params.effective_roles():
        if role.color:
            role_by_color[role.color.lower()] = role
    role_by_id: dict[int, HandwritingRole] = {r.id: r for r in params.effective_roles()}
    for key, mask in colored_masks.items():
        if not mask.any():
            continue
        if isinstance(key, tuple):
            rid, col_hex = key
            role = role_by_id.get(rid)
            # 颜色以 key 中的 color 为准（run 的实际颜色）
        else:
            col_hex = key  # type: ignore
            role = role_by_color.get(col_hex.lower())
        if role and role.printed:
            px, py, pt = 0, 0, 0.0
        elif role:
            px = role.perturb_x_sigma if role.perturb_x_sigma is not None else params.perturb_x_sigma
            py = role.perturb_y_sigma if role.perturb_y_sigma is not None else params.perturb_y_sigma
            pt = role.perturb_theta_sigma if role.perturb_theta_sigma is not None else params.perturb_theta_sigma
        else:
            px, py, pt = params.perturb_x_sigma, params.perturb_y_sigma, params.perturb_theta_sigma
        # 若该颜色未绑定任何角色且为默认黑，可尊重打印体？但默认手写仍走全局扰动
        try:
            fill = parse_color(col_hex)
        except ValueError:
            fill = params.fill
        ny, nx = _perturbed_positions_with_sigmas(mask, px, py, pt, rng)
        canvas[ny, nx] = np.array(fill, dtype=np.uint8)
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

    # ------------------------------------------------------------------
    # 背景（支持多页文档底图：每页一张，尺寸统一到首页）
    # ------------------------------------------------------------------
    def _background_count(self, params: HandwritingParams) -> int:
        return len(params.background_pages) if params.background_pages else 0

    def _first_background(self, params: HandwritingParams) -> np.ndarray:
        """首页背景数组（决定画布尺寸与排版边界）。"""
        if params.background_pages:
            path = params.background_pages[0]
        else:
            path = params.background_path
        return np.asarray(Image.open(path).convert("RGB"))

    def _page_background(
        self, params: HandwritingParams, index: int, size: tuple[int, int]
    ) -> np.ndarray:
        """第 index 页（0 基）的背景。

        多页文档超出自身页数时复用最后一页；某页尺寸与首页不同时
        统一缩放到首页尺寸（保证排版坐标一致）。
        """
        pages = params.background_pages
        if pages:
            path = pages[min(index, len(pages) - 1)]
        else:
            path = params.background_path
        image = Image.open(path).convert("RGB")
        if tuple(image.size) != tuple(size):
            image = image.resize(size, Image.Resampling.LANCZOS)
        return np.asarray(image)

    def render_preview(self, params: HandwritingParams) -> Image.Image:
        """仅渲染第一页，用于预览。"""
        params.validate(require_text=False)
        return next(self.generate_pages(params))

    def generate(self, params: HandwritingParams) -> Iterator[Image.Image]:
        """生成手写图序列（惰性迭代），与引擎接口一致。"""
        return self.generate_pages(params)

    def _region_params(
        self, params: HandwritingParams, region: TextRegion
    ) -> HandwritingParams:
        """构造区域局部的渲染参数：独立字体/字号；排版/扰动/错字/颜色覆盖项；打印体关闭全部扰动。"""
        rp = copy.copy(params)
        rp.text = region.text
        rp.paragraphs = None
        rp.regions = None
        # 区域内边距（以矩形自身为界）
        rp.left_margin = region.margin_left or 0
        rp.right_margin = region.margin_right or 0
        rp.top_margin = region.margin_top or 0
        rp.bottom_margin = region.margin_bottom or 0
        if region.font_path:
            rp.font_path = region.font_path
        if region.font_size > 0:
            rp.font_size = region.font_size

        # 段落 / 对齐 / 首行缩进：
        if region.paragraphs:
            rp.paragraphs = [copy.copy(p) for p in region.paragraphs]
            rp.text = ""
        elif region.align != "left" or region.indent_em > 0:
            rp.paragraphs = [
                Paragraph(
                    text=region.text,
                    align=region.align,
                    first_line_indent=int(round(region.indent_em * rp.font_size)),
                )
            ]
            rp.text = ""
        elif "\n" in region.text:
            rp.paragraphs = [
                Paragraph(text=t, align="left", first_line_indent=0)
                for t in region.text.split("\n")
            ]
            rp.text = ""
        else:
            rp.text = region.text

        # 逐区域覆盖项（None = 跟随主设置）
        if region.word_spacing is not None:
            rp.word_spacing = region.word_spacing
        if region.line_spacing is not None:
            rp.line_spacing = region.line_spacing
        if region.word_spacing_sigma is not None:
            rp.word_spacing_sigma = region.word_spacing_sigma
        if region.line_spacing_sigma is not None:
            rp.line_spacing_sigma = region.line_spacing_sigma
        if region.font_size_sigma is not None:
            rp.font_size_sigma = region.font_size_sigma
        if region.perturb_x_sigma is not None:
            rp.perturb_x_sigma = region.perturb_x_sigma
        if region.perturb_y_sigma is not None:
            rp.perturb_y_sigma = region.perturb_y_sigma
        if region.perturb_theta_sigma is not None:
            rp.perturb_theta_sigma = region.perturb_theta_sigma
        if region.miswrite_rate is not None:
            rp.miswrite_rate = region.miswrite_rate
        if region.miswrite_strikeout_style is not None:
            rp.miswrite_strikeout_style = region.miswrite_strikeout_style
        if region.color is not None:
            rp.color = region.color

        # 打印体：零扰动、零错字（优先于任何覆盖项）
        if region.printed:
            rp.word_spacing_sigma = 0
            rp.line_spacing_sigma = 0
            rp.font_size_sigma = 0
            rp.perturb_x_sigma = 0
            rp.perturb_y_sigma = 0
            rp.perturb_theta_sigma = 0.0
            rp.miswrite_rate = 0.0
        return rp

    def _is_mixed(self, params: HandwritingParams) -> bool:
        """判断主段落是否含多角色混排（任一段含 runs 且颜色/角色多样）。"""
        if not params.paragraphs:
            return False
        for p in params.paragraphs:
            if p.runs is not None and len(p.runs) > 0:
                # 单一 run 且 role 0 且无颜色 视为非混排（走 plain 高速路径）
                if len(p.runs) == 1 and p.runs[0].role_id == 0 and not p.runs[0].color:
                    continue
                return True
        return False

    def _main_page_masks(
        self, params: HandwritingParams, width: int, height: int
    ) -> list[np.ndarray | dict[str, np.ndarray]]:
        """主文字（text 或 paragraphs）的逐页墨迹掩码；无文字返回 []。

        混排时返回 list[dict[color->mask]]，纯文本返回 list[bool mask]。
        """
        if params.paragraphs:
            if self._is_mixed(params):
                return list(self._paragraph_page_masks_mixed(params, width, height))
            return list(self._paragraph_page_masks(params, width, height))
        if not params.text.strip():
            return []
        rand = self._new_rand()
        masks: list[np.ndarray] = []
        start = 0
        while True:
            mask, start = _layout_text(params, rand, params.text, start, width, height)
            masks.append(mask)
            if start >= len(params.text):
                break
        return masks

    def _pages_with_regions(
        self, params: HandwritingParams
    ) -> Iterator[Image.Image]:
        """框选区域模式：每个区域独立排版，与主文字合成后逐页输出。

        每个区域仅在所属页面（target_page = region.page - 1）渲染，
        超出框选范围的内容单页自然截断（不跨页延伸）。
        总页数取主文字、各区域所在页的最大值（至少 1 页）。
        区域先合成、主文字后合成（重叠时主文字在上）；
        随机源消费顺序固定，相同 seed 下预览与导出逐像素一致。
        """
        background = self._first_background(params)
        height, width = background.shape[:2]
        is_mixed_main = self._is_mixed(params)
        main_masks = self._main_page_masks(params, width, height)

        # entries: (局部参数, ox, oy, rw, rh, 单页mask, 目标页索引0基)
        entries: list[tuple[HandwritingParams, int, int, int, int, np.ndarray, int]] = []
        for index, region in enumerate(params.regions or []):
            has_text = bool(region.text.strip()) or (
                bool(region.paragraphs) and any(p.plain_text().strip() for p in region.paragraphs)
            )
            if not has_text:
                continue
            ox = max(0, min(int(region.x), width - 1))
            oy = max(0, min(int(region.y), height - 1))
            rw = max(1, min(int(region.w), width - ox))
            rh = max(1, min(int(region.h), height - oy))
            rp = self._region_params(params, region)
            # 每区域独立排版随机源：由主 seed 派生的确定性字符串种子，
            # 保证相同 seed 下预览与导出的逐字扰动完全一致
            rand = random.Random(f"{self._seed}|region{index}")

            # 区域排版：仅排版在所属单页内（超出直接截断）
            # 区域目前保持 plain 渲染（区域内如需混排，可通过 region.paragraphs 的 runs 走 mixed 分页）
            if rp.paragraphs:
                # 检测区域段落是否混排
                mixed = any(p.runs is not None and len(p.runs) > 1 for p in rp.paragraphs or [])
                if mixed:
                    masks_mixed = list(self._paragraph_page_masks_mixed(rp, rw, rh, rand=rand))
                    # 区域仅取首页的 colored dict，转换为 union mask for region entry? 但需分色渲染，改为特殊处理：单页 dict
                    # 为兼容 entries 的单 mask 结构，区域混排时直接取 union
                    mm = masks_mixed[0] if masks_mixed else {}
                    if isinstance(mm, dict):
                        # 合并为单 bool union（仅用于区域模式的简单贴合，颜色信息暂丢失）
                        # 若需保留分色，需重构 entries 为 dict，但此为罕见路径（区域内再混排），先 union
                        union = np.zeros((rh, rw), dtype=bool)
                        for v in mm.values():
                            union |= v
                        mask = union
                    else:
                        mask = mm
                else:
                    masks = list(self._paragraph_page_masks(rp, rw, rh, rand=rand))
                    mask = masks[0] if masks else np.zeros((rh, rw), dtype=bool)
            else:
                mask, _ = _layout_text(
                    rp, rand, region.text, 0, rw, rh, force_first_line=True
                )
            target_page = max(1, int(region.page)) - 1
            entries.append((rp, ox, oy, rw, rh, mask, target_page))

        n_pages = max(
            [len(main_masks), self._background_count(params)]
            + [e[6] + 1 for e in entries]
            + [1]
        )
        for page_index in range(n_pages):
            canvas = self._page_background(
                params, page_index, (width, height)
            ).copy()
            for rp, ox, oy, rw, rh, mask, target_page in entries:
                if page_index != target_page or not mask.any():
                    continue
                ys, xs = _perturbed_positions(mask, rp, self._rng)
                canvas[oy + ys, ox + xs] = np.array(rp.fill, dtype=np.uint8)
            if page_index < len(main_masks):
                mm = main_masks[page_index]
                if isinstance(mm, dict):
                    # 混排主文字：分色扰动
                    if any(v.any() for v in mm.values()):
                        canvas = _perturb_mask_colored(mm, params, self._rng, canvas)
                elif isinstance(mm, np.ndarray) and mm.any():
                    canvas = _perturb_mask(mm, params, self._rng, canvas)
            yield Image.fromarray(canvas, mode="RGB")

    def _paragraph_pages(self, params: HandwritingParams) -> Iterator[Image.Image]:
        """按段落逐页渲染（逐行流式分页），在掩码基础上做笔画扰动。支持混排分色。"""
        background = self._first_background(params)
        height, width = background.shape[:2]
        page_index = 0
        if self._is_mixed(params):
            for page_dict in self._paragraph_page_masks_mixed(params, width, height):
                bg = self._page_background(params, page_index, (width, height))
                # 空页也可能含零掩码
                if any(v.any() for v in page_dict.values()):
                    img = _perturb_mask_colored(page_dict, params, self._rng, bg)
                else:
                    img = bg.copy()
                yield Image.fromarray(img, mode="RGB")
                page_index += 1
        else:
            for page_canvas in self._paragraph_page_masks(params, width, height):
                yield self._finalize(
                    params, page_canvas,
                    self._page_background(params, page_index, (width, height)),
                )
                page_index += 1
        # 文档底图剩余的空白页也输出，便于用户翻页浏览后再框选
        while page_index < self._background_count(params):
            canvas = self._page_background(params, page_index, (width, height))
            yield Image.fromarray(canvas, mode="RGB")
            page_index += 1

    def _paragraph_page_masks(
        self, params: HandwritingParams, width: int, height: int, rand: random.Random | None = None
    ) -> Iterator[np.ndarray]:
        """按段落逐页排版，yield 每页前景掩码（不做笔画扰动）。

        行节奏与纯文本路径对齐：首行绘制基线位于 top + line_spacing（不含字高），
        每行（含段内换行、段落边界、空行）均推进一个完整行距；
        某行的绘制基线越过页底限制时才换页，保证首页不留空白。
        """
        if rand is None:
            rand = self._new_rand()
        line_spacing = float(params.line_spacing) + float(params.font_size)
        lead = line_spacing - float(params.font_size)
        # 预览降采样会使边距为浮点，这里取整以保证 numpy 索引为整数
        top, bottom = int(params.top_margin), int(params.bottom_margin)
        # 与纯文本路径 _layout_page 的换页条件一致
        limit = height - bottom - float(params.font_size)

        all_lines: list[tuple[np.ndarray | None, float]] = []
        for para in params.paragraphs or []:
            lines = _layout_paragraph_plain(params, rand, para, width)
            # _layout_paragraph 可能返回混合类型，但此方法仅处理 plain，混排走 _mixed 分支
            # 为兼容，过滤 dict 类型
            filtered: list[tuple[np.ndarray | None, float]] = []
            for band, off in lines:  # type: ignore
                if isinstance(band, dict):
                    # 混排行中取 union 作为降级（不应进入此分支）
                    if band:
                        union = np.zeros(next(iter(band.values())).shape, dtype=bool)
                        for v in band.values():
                            # band 形状已是行裁剪后，无法直接 union 需重构，此路径仅兜底
                            pass
                        filtered.append((None, off))
                    else:
                        filtered.append((None, off))
                else:
                    filtered.append((band, off))
            if not filtered:
                filtered = [(None, 0.0)]  # 空段保留一行空行
            all_lines.extend(filtered)

        page_canvas = np.zeros((height, width), dtype=bool)
        yielded = False
        draw_y = float(top) + lead
        for band, off in all_lines:
            # 与纯文本路径一致：每行（含空行）开始前检查是否越页底
            if draw_y > limit and page_canvas.any():
                yield page_canvas
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
            yield page_canvas

    def _paragraph_page_masks_mixed(
        self, params: HandwritingParams, width: int, height: int, rand: random.Random | None = None
    ) -> Iterator[dict[tuple[int, str], np.ndarray]]:
        """混排版逐页掩码：yield 每页 dict[(role_id, color_hex) -> bool mask]。"""
        if rand is None:
            rand = self._new_rand()
        line_spacing = float(params.line_spacing) + float(params.font_size)
        lead = line_spacing - float(params.font_size)
        top, bottom = int(params.top_margin), int(params.bottom_margin)
        limit = height - bottom - float(params.font_size)

        all_lines: list[tuple[dict[tuple[int, str], np.ndarray] | None, float]] = []
        for para in params.paragraphs or []:
            lines = _layout_paragraph(params, rand, para, width)  # type: ignore
            # 统一为 mixed 形式：plain 行转 dict[(role_id,color)]
            converted: list[tuple[dict[tuple[int, str], np.ndarray] | None, float]] = []
            for band, off in lines:  # type: ignore
                if band is None:
                    converted.append((None, off))
                elif isinstance(band, dict):
                    # 兼容旧版 str 键与新版 tuple 键
                    if band and isinstance(next(iter(band.keys())), str):
                        # 旧版 str -> 转为 (0, color) 的默认角色
                        converted.append(({ (0, k): v for k, v in band.items()}, off))  # type: ignore
                    else:
                        converted.append((band, off))  # type: ignore
                else:
                    # bool mask -> dict以默认角色+颜色
                    if band.any():
                        converted.append(({(0, params.color.lower()): band}, off))
                    else:
                        converted.append((None, off))
            if not converted:
                converted = [(None, 0.0)]
            all_lines.extend(converted)

        # 初始化空页字典（按需创建 (role,color) 键）
        all_keys: set[tuple[int, str]] = set()
        for d, _ in all_lines:
            if d:
                all_keys.update(d.keys())
        if not all_keys:
            all_keys.add((0, params.color.lower()))

        page_dict: dict[tuple[int, str], np.ndarray] = {k: np.zeros((height, width), dtype=bool) for k in all_keys}
        yielded = False
        draw_y = float(top) + lead
        for band_dict, off in all_lines:
            if draw_y > limit and any(v.any() for v in page_dict.values()):
                yield page_dict
                yielded = True
                page_dict = {k: np.zeros((height, width), dtype=bool) for k in all_keys}
                draw_y = float(top) + lead
            if band_dict is not None:
                for key in list(band_dict.keys()):
                    if key not in page_dict:
                        page_dict[key] = np.zeros((height, width), dtype=bool)
                        all_keys.add(key)
                row0 = int(round(draw_y + off))
                for key, band in band_dict.items():
                    ys, xs = np.nonzero(band)
                    rows = row0 + ys
                    valid = (rows >= 0) & (rows < height)
                    page_dict[key][rows[valid], xs[valid]] = True
            draw_y += line_spacing
        if any(v.any() for v in page_dict.values()) or not yielded:
            yield page_dict

    # _finalize 使用 self._rng，与 generate_pages 共用同一随机源
    def _finalize(self, params, mask, background):
        if isinstance(mask, dict):
            return Image.fromarray(_perturb_mask_colored(mask, params, self._rng, background), mode="RGB")
        return Image.fromarray(_perturb_mask(mask, params, self._rng, background), mode="RGB")

    def generate_pages(self, params: HandwritingParams) -> Iterator[Image.Image]:
        """逐页生成手写图（惰性迭代）。

        引擎只做结构校验（require_text=False）：文字是否必填由
        GUI/CLI 各自决定，纯背景预览在这里合法。
        """
        params.validate(require_text=False)
        if params.regions:
            yield from self._pages_with_regions(params)
            return
        if params.paragraphs:
            yield from self._paragraph_pages(params)
            return
        background = self._first_background(params)
        height, width = background.shape[:2]
        rand = self._new_rand()
        start = 0
        page_index = 0
        while True:
            mask, start = _layout_text(params, rand, params.text, start, width, height)
            canvas = _perturb_mask(
                mask, params, self._rng,
                self._page_background(params, page_index, (width, height)),
            )
            yield Image.fromarray(canvas, mode="RGB")
            page_index += 1
            if start >= len(params.text):
                break
        # 文档底图剩余的空白页也输出，便于用户翻页浏览后再框选
        while page_index < self._background_count(params):
            canvas = self._page_background(params, page_index, (width, height))
            yield Image.fromarray(canvas, mode="RGB")
            page_index += 1

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