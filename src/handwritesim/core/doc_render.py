"""把 PDF / DOCX 渲染成「打印预览」图片，用作手写底图，并自动识别标记的手写区域。

PDF 用 pypdfium2 直接栅格化（Apache-2.0 授权，纯 wheel 无系统依赖）；
DOCX 的忠实排版需要本机排版引擎：优先借助 Microsoft Word（COM 自动化，
仅 Windows），其次 LibreOffice（soffice --headless），转成 PDF 后
再走同一条栅格化链路。都没有时给出明确的安装提示。

自动区域检测（对齐 Rust 版 doc_render.rs）：
1. docx 表格单元格（0.5.0 新增）：按 body 元素流定位表格 → 逐字符擦白印刷字
   （保留表格线）→ 为每个单元格生成 TextRegion（``table_index`` 标注归属）；
2. 图像高亮底色检测（Word 标准黄色/绿色/青色/粉色等高亮矩形区域）；
3. 文本占位符标签检测（``{{...}}`` 与 ``【...】``；表格内的标签只擦除不重复成区）；
4. 自动擦除原图上的高亮色块与占位文字为纯白底色，
   从 PDF 文本层提取高亮框内的文字、字号、行距与首行缩进，
   返回带 role_id/highlight 绑定的 TextRegion 列表。
"""

from __future__ import annotations

import functools
import math
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

import numpy as np
from PIL import Image

from .models import TextRegion

if TYPE_CHECKING:  # 仅类型检查期导入：避免与 docx_tables 形成运行期循环依赖
    from .docx_io import BodyElement
    from .docx_tables import RectPx, TableView


# ---------------------------------------------------------------------------
# 高亮颜色分类与像素判定
# ---------------------------------------------------------------------------

# 高亮颜色名 -> 中文名（角色命名与提示用）
HIGHLIGHT_NAMES: dict[str, str] = {
    "yellow": "黄色",
    "green": "绿色",
    "cyan": "青色",
    "magenta": "品红",
    "pink": "粉红",
    "red": "红色",
    "blue": "蓝色",
}


def classify_highlight_color(r: int, g: int, b: int) -> str:
    """将 RGB 高亮颜色分类为标准颜色名称（yellow/green/cyan/magenta/pink/red/blue）。"""
    rf, gf, bf = float(r), float(g), float(b)
    if rf > 160.0 and gf > 160.0 and bf < 140.0:
        return "yellow"
    if gf > 150.0 and bf > 150.0 and rf < 150.0:
        return "cyan"
    if rf > 180.0 and bf > 140.0 and gf < 170.0:
        if gf < 100.0 and bf > 180.0:
            return "magenta"
        return "pink"
    if gf > rf and gf > bf:
        return "green"
    if bf > rf and bf > gf:
        if gf > 140.0:
            return "cyan"
        return "blue"
    if rf > gf and rf > bf:
        if bf > 100.0:
            return "pink"
        return "red"
    return "yellow"


def is_highlight_pixel(r: int, g: int, b: int) -> bool:
    """判断像素是否属于高亮底色（浅色/高饱和度，排除黑白灰与深色文字）。"""
    mx = max(r, g, b)
    mn = min(r, g, b)
    diff = mx - mn
    if diff < 30:  # 灰度差不足，排除黑白灰背景与文字抗锯齿
        return False
    if mx < 90:  # 亮度不足，排除深色文字
        return False
    if diff / mx < 0.20:  # 饱和度不足
        return False
    return True


def _highlight_mask(arr: np.ndarray) -> np.ndarray:
    """整图高亮像素掩码（向量化版 is_highlight_pixel）。"""
    mx = arr.max(axis=-1).astype(np.int16)
    mn = arr.min(axis=-1).astype(np.int16)
    diff = mx - mn
    with np.errstate(divide="ignore", invalid="ignore"):
        sat = np.where(mx > 0, diff / np.maximum(mx, 1), 0.0)
    return (diff >= 30) & (mx >= 90) & (sat >= 0.20)


# ---------------------------------------------------------------------------
# 包围盒
# ---------------------------------------------------------------------------

@dataclass
class BoundingBox:
    """像素包围盒。"""

    min_x: int
    min_y: int
    max_x: int
    max_y: int
    highlight: str | None = None

    def width(self) -> int:
        return self.max_x - self.min_x + 1

    def height(self) -> int:
        return self.max_y - self.min_y + 1

    def union(self, other: "BoundingBox") -> "BoundingBox":
        return BoundingBox(
            min(self.min_x, other.min_x),
            min(self.min_y, other.min_y),
            max(self.max_x, other.max_x),
            max(self.max_y, other.max_y),
            self.highlight or other.highlight,
        )

    def intersects(self, other: "BoundingBox") -> bool:
        return (
            self.min_x <= other.max_x
            and self.max_x >= other.min_x
            and self.min_y <= other.max_y
            and self.max_y >= other.min_y
        )


def detect_highlight_boxes(img: Image.Image | np.ndarray) -> list[BoundingBox]:
    """检测图像中的高亮区域包围盒（8 邻域连通 + 噪声过滤 + 相邻合并）。"""
    arr = np.asarray(img.convert("RGB") if isinstance(img, Image.Image) else img)
    height, width = arr.shape[:2]
    if width == 0 or height == 0:
        return []

    mask = _highlight_mask(arr)

    # scipy 8 邻域连通域标记
    from scipy import ndimage

    structure = np.ones((3, 3), dtype=np.int32)
    labeled, n_labels = ndimage.label(mask, structure=structure)
    if n_labels == 0:
        return []

    slices = ndimage.find_objects(labeled)
    flat_labels = labeled.ravel()
    counts = np.bincount(flat_labels, minlength=n_labels + 1)
    # 逐连通域的平均颜色（加权 bincount）
    chan_sums = np.stack([
        np.bincount(flat_labels, weights=arr[..., c].ravel().astype(np.float64),
                    minlength=n_labels + 1)
        for c in range(3)
    ], axis=-1)

    raw_boxes: list[BoundingBox] = []
    for label_idx in range(1, n_labels + 1):
        count = int(counts[label_idx])
        sl = slices[label_idx - 1]
        if sl is None:
            continue
        min_y, max_y = sl[0].start, sl[0].stop - 1
        min_x, max_x = sl[1].start, sl[1].stop - 1
        bw = max_x - min_x + 1
        bh = max_y - min_y + 1
        # 过滤噪声：最小宽度 >= 15，最小高度 >= 8，像素数 >= 30
        if bw >= 15 and bh >= 8 and count >= 30:
            avg = chan_sums[label_idx] / max(count, 1)
            color_name = classify_highlight_color(int(avg[0]), int(avg[1]), int(avg[2]))
            raw_boxes.append(BoundingBox(min_x, min_y, max_x, max_y, color_name))

    return merge_close_boxes(raw_boxes)


def _should_merge_boxes(a: BoundingBox, b: BoundingBox) -> bool:
    # 0. 高亮颜色不同不合并
    if a.highlight != b.highlight:
        return False
    # 1. 直接相交或包含
    if a.intersects(b):
        return True
    # 2. 同行相邻且水平间隙较小（gap <= 20 像素，垂直重叠超过较小高度的 40%）
    overlap_y = min(a.max_y, b.max_y) - max(a.min_y, b.min_y) + 1
    min_h = min(a.height(), b.height())
    if overlap_y > 0 and overlap_y >= min_h * 2 // 5:
        if a.max_x < b.min_x:
            gap_x = b.min_x - a.max_x
        elif b.max_x < a.min_x:
            gap_x = a.min_x - b.max_x
        else:
            gap_x = 0
        if gap_x <= 20:
            return True
    # 3. 多行段落垂直连续段（同色，水平重叠 >= 40% 较小宽度，
    #    垂直间隙 gap_y <= 较小高度的 1.5 倍）。
    #    注意用两框中较小的高度（行框高度）做基准：合并是迭代进行的，current
    #    已是增长后的累计大框，若按 max_h 计算阈值，串起来的框会吞并下方
    #    任意远的内容（包括两段高亮之间未高亮的标题行）。
    overlap_x = min(a.max_x, b.max_x) - max(a.min_x, b.min_x) + 1
    min_w = min(a.width(), b.width())
    if overlap_x > 0 and overlap_x >= min_w * 2 // 5:
        if a.max_y < b.min_y:
            gap_y = b.min_y - a.max_y
        elif b.max_y < a.min_y:
            gap_y = a.min_y - b.max_y
        else:
            gap_y = 0
        if gap_y <= min(a.height(), b.height()) * 3 // 2:
            return True
    return False


def merge_close_boxes(boxes: list[BoundingBox]) -> list[BoundingBox]:
    """合并相邻或重叠的同色高亮矩形框（迭代至收敛）。"""
    if len(boxes) <= 1:
        return list(boxes)
    changed = True
    while changed:
        changed = False
        next_boxes: list[BoundingBox] = []
        merged = [False] * len(boxes)
        for i in range(len(boxes)):
            if merged[i]:
                continue
            current = boxes[i]
            for j in range(i + 1, len(boxes)):
                if merged[j]:
                    continue
                if _should_merge_boxes(current, boxes[j]):
                    current = current.union(boxes[j])
                    merged[j] = True
                    changed = True
            next_boxes.append(current)
        boxes = next_boxes
    return boxes


def merge_wrap_line_boxes(
    boxes: list[BoundingBox], chars: list[ExtractedChar]
) -> list[BoundingBox]:
    """换行签名合并：同一句高亮跨行折断时，行尾条纹与下一行行首条纹水平投影零重叠，
    规则 3 的 40% 重叠要求判不合并 → 一句话被上下切成两个区域。此函数按换行签名
    「上行条纹是行尾（同行右侧无字符）& 下行条纹是行首（同行左侧无字符）& 同色 &
    垂直间隙 ≤ 1.5×较小框高」把这类条纹对合并回来；在 merge_close_boxes 之后调用，
    只处理其漏网的零重叠跨行对。签名用行内字符占位而非页面版心边：对离群字符
    坐标（如 loose bounds 越界）免疫。

    只对「原始条纹对」做单趟配对，合并结果不再参与后续配对：多行句子（3 条纹
    以上）的水平重叠行已被规则 3 提前串起，到达这里的基本是 2 条纹短句；若允许
    联合框继续配对，其高度会让间隙阈值（1.5×较小框高）失真，把上方无关段落
    整块吞进来（实测复现过），故显式禁止链式合并。
    """
    # 无字符流时行尾/行首判定空真，会退化成「同色+垂直邻近即合并」，直接不合并
    if len(boxes) <= 1 or not chars:
        return list(boxes)

    result: list[BoundingBox] = []
    consumed = [False] * len(boxes)
    for i in range(len(boxes)):
        if consumed[i]:
            continue
        # 判定始终用原始框 i 与原始框 j：union 后的 current 只作输出；
        # 发射或被吸收的框都标记 consumed，防止后续框再与它配对造成重复合并
        current = replace(boxes[i])
        for j in range(i + 1, len(boxes)):
            if consumed[j]:
                continue
            if _is_wrap_pair(boxes[i], boxes[j], chars):
                current = current.union(boxes[j])
                consumed[j] = True
        consumed[i] = True
        result.append(current)
    return result


def _is_wrap_pair(a: BoundingBox, b: BoundingBox, chars: list[ExtractedChar]) -> bool:
    """判断 b 是否是 a 的跨行延续（换行签名：上行条纹为行尾、下行条纹为行首）。

    上下关系按 min_y 归一，输入顺序无关；垂直阈值沿用规则 3 的行距容差
    （间隙 ≤ 1.5×较小框高），行尾/行首容差为半个较小框高（可吸收行尾空格）。
    """
    if a.highlight != b.highlight:
        return False
    upper, lower = (a, b) if a.min_y <= b.min_y else (b, a)
    # 必须严格换行到下一行（同一行内的左右分块不归换行签名管）
    if lower.min_y <= upper.min_y:
        return False
    min_h = min(upper.height(), lower.height())
    gap_y = lower.min_y - upper.max_y
    if gap_y > min_h * 3 // 2:
        return False
    tol = min_h / 2.0

    def in_band(c: ExtractedChar, box: BoundingBox) -> bool:
        # 行带判定要求重叠 ≥ 字高一半：相邻行条纹常在边界相触（1px 重叠），
        # 不收紧会把下一行的字符误算进上一行
        overlap = min(c.max_y, box.max_y) - max(c.min_y, box.min_y)
        return overlap > 0.0 and overlap >= (c.max_y - c.min_y) * 0.5

    # 行尾：与上行条纹同行的字符中，没有谁整个落在条纹右侧（超出容差）
    upper_right = upper.max_x + tol
    if any(in_band(c, upper) and c.min_x > upper_right for c in chars):
        return False

    # 行首：与下行条纹同行的字符中，没有谁整个落在条纹左侧（超出容差）
    lower_left = lower.min_x - tol
    return not any(in_band(c, lower) and c.max_x < lower_left for c in chars)


def erase_highlight_boxes(img: Image.Image | np.ndarray, boxes: list[BoundingBox]) -> None:
    """在图像上将高亮矩形区域及残留高亮像素抹白为纯白 #FFFFFF（就地修改）。

    接受 PIL Image（就地 putpixel 等价修改）或 numpy 数组（就地切片赋值）。
    """
    if isinstance(img, Image.Image):
        arr = np.asarray(img.convert("RGB")).copy()
        erase_highlight_boxes(arr, boxes)
        img.paste(Image.fromarray(arr), (0, 0))
        return

    arr = img
    height, width = arr.shape[:2]
    for b in boxes:
        x0 = min(b.min_x, width - 1)
        x1 = min(b.max_x, width - 1)
        y0 = min(b.min_y, height - 1)
        y1 = min(b.max_y, height - 1)
        arr[y0:y1 + 1, x0:x1 + 1] = 255
    # 整页残留高亮像素也抹白（消除边缘溢色）
    residual = _highlight_mask(arr)
    arr[residual] = 255


def erase_char_boxes(
    arr: np.ndarray,
    chars: list[ExtractedChar],
    idxs: Sequence[int],
    pad: int = 2,
) -> None:
    """按字符包围盒擦白（glyph 级擦除：只清字形矩形，保留穿过区域的表格线）。

    arr 为 numpy uint8 HxWx3（就地切片赋值 255）。整框擦白会把表格线一起抹掉，
    故此路径只在单元格内逐字符擦除；pad 用于吃掉文字抗锯齿边缘。
    """
    height, width = arr.shape[:2]
    for i in idxs:
        if not 0 <= i < len(chars):
            continue  # 字符下标越界（调用方数据不一致）：跳过，不影响其余字
        c = chars[i]
        x0 = max(math.floor(c.min_x) - pad, 0)
        x1 = min(math.ceil(c.max_x) + pad, width - 1)
        y0 = max(math.floor(c.min_y) - pad, 0)
        y1 = min(math.ceil(c.max_y) + pad, height - 1)
        if x1 < x0 or y1 < y0:
            continue
        arr[y0:y1 + 1, x0:x1 + 1] = 255


def _rect_contains_box(r: RectPx, b: BoundingBox) -> bool:
    """BoundingBox 是否完整落在 RectPx 内（2px 容差，容纳包围盒贴合误差）。"""
    return (
        b.min_x >= r.x - 2
        and b.max_x <= r.x + r.w + 2
        and b.min_y >= r.y - 2
        and b.max_y <= r.y + r.h + 2
    )


def _partition_highlight_boxes(
    boxes: list[BoundingBox], outer_rects: Sequence[RectPx]
) -> tuple[list[BoundingBox], list[BoundingBox]]:
    """按表格外包围矩形把高亮框分流为 (表内, 表外)。

    表内框不整框擦白（保表格线）、不生成区域；其残留高亮像素由
    erase_highlight_boxes 内的整页像素过滤步骤一并清除。
    判定用「框完整落在表内」（2px 容差）而非相交：表格线就在高亮框边界上，
    相交判定会把紧贴表格的高亮整框擦白，连带抹掉那一条表格线。
    """
    in_table: list[BoundingBox] = []
    normal: list[BoundingBox] = []
    for b in boxes:
        if any(_rect_contains_box(r, b) for r in outer_rects):
            in_table.append(b)
        else:
            normal.append(b)
    return in_table, normal


# ---------------------------------------------------------------------------
# 文本标签（{{...}} / 【...】占位符）
# ---------------------------------------------------------------------------

@dataclass
class TagMatch:
    """匹配到的文本标签结果。"""

    start_char_idx: int
    end_char_idx: int
    inner_text: str


def scan_text_tags(chars: list[str]) -> list[TagMatch]:
    """在字符流中扫描 ``{{...}}`` 或 ``【...】`` 占位标签。"""
    matches: list[TagMatch] = []
    length = len(chars)
    i = 0
    while i < length:
        # 匹配 {{...}}
        if i + 1 < length and chars[i] == "{" and chars[i + 1] == "{":
            j = i + 2
            found = False
            while j + 1 < length:
                if chars[j] == "}" and chars[j + 1] == "}":
                    inner = "".join(chars[i + 2:j])
                    matches.append(TagMatch(i, j + 1, inner.strip()))
                    i = j + 2
                    found = True
                    break
                if (chars[j] == "{" and chars[j + 1] == "{") or chars[j] in ("\n", "\r"):
                    i = j
                    found = True
                    break
                j += 1
            if not found:
                i += 1
        # 匹配 【...】
        elif chars[i] == "【":
            j = i + 1
            found = False
            while j < length:
                if chars[j] == "】":
                    inner = "".join(chars[i + 1:j])
                    matches.append(TagMatch(i, j, inner.strip()))
                    i = j + 1
                    found = True
                    break
                if chars[j] == "【" or chars[j] in ("\n", "\r"):
                    i = j
                    found = True
                    break
                j += 1
            if not found:
                i += 1
        else:
            i += 1
    return matches


def _tag_body(inner: str) -> str:
    """提取标签内部冒号后的正文（如 ``手写:张三`` -> ``张三``）。"""
    for sep in (":", "："):
        pos = inner.find(sep)
        if pos >= 0:
            return inner[pos + len(sep):].strip()
    return inner


def strip_tag_syntax(text: str) -> str:
    """清理提取文本中的模板标签语法（如 ``{{...}}``、``【...】``、``{{手写:...}}``）。"""
    trimmed = text.strip()
    if not trimmed:
        return ""

    # 1. 整体被 {{ ... }} 包裹
    if trimmed.startswith("{{") and trimmed.endswith("}}") and len(trimmed) >= 4:
        return _tag_body(trimmed[2:-2].strip())
    # 2. 整体被 【 ... 】 包裹
    if trimmed.startswith("【") and trimmed.endswith("】") and len(trimmed) >= 2:
        return _tag_body(trimmed[1:-1].strip())

    # 3. 扫描并替换内部可能存在的内联标签
    chars = list(text)
    length = len(chars)
    result: list[str] = []
    replaced = False
    i = 0
    while i < length:
        if i + 1 < length and chars[i] == "{" and chars[i + 1] == "{":
            j = i + 2
            found = False
            while j + 1 < length:
                if chars[j] == "}" and chars[j + 1] == "}":
                    result.append(_tag_body("".join(chars[i + 2:j]).strip()))
                    i = j + 2
                    found = True
                    replaced = True
                    break
                j += 1
            if not found:
                result.append(chars[i])
                i += 1
        elif chars[i] == "【":
            j = i + 1
            found = False
            while j < length:
                if chars[j] == "】":
                    result.append(_tag_body("".join(chars[i + 1:j]).strip()))
                    i = j + 1
                    found = True
                    replaced = True
                    break
                j += 1
            if not found:
                result.append(chars[i])
                i += 1
        else:
            result.append(chars[i])
            i += 1

    if replaced:
        return "".join(result).strip()
    return trimmed


# ---------------------------------------------------------------------------
# PDF 文本层字符提取
# ---------------------------------------------------------------------------

@dataclass
class ExtractedChar:
    """PDF 提取的单个字符及其在页面像素空间的位置与字号。

    glyph_h_pt 为紧包围盒（tight charbox）的字符高度（点），用于在
    raw 字号失真（嵌入字体矩阵放大等）时回退估计真实字号。
    """

    ch: str
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    font_size_pt: float
    glyph_h_pt: float = 0.0


def _is_full_width_char(ch: str) -> bool:
    """是否为全角（CJK 表意文字等）字符：其紧包围盒高度约等于字号。"""
    import unicodedata

    return (
        not ch.isspace()
        and unicodedata.east_asian_width(ch) in ("W", "F")
        and unicodedata.category(ch) not in ("Pc", "Ps", "Pe", "Pi", "Pf")
    )


def _resolve_font_size_px(matched: list[ExtractedChar], scale: float) -> int:
    """从匹配字符解析字号（像素）。

    pdfium 的字号接口对嵌入非标准 FontMatrix 的字体会产生偏差：
    - pypdfium2 的 FPDFText_GetFontSize 对 Word 中文 PDF 返回放大数十倍的值；
    - pdfium-render 的 scaled_font_size 则偏大约 5%~10%。
    这里统一用字符包围盒做校验/校准：
    - raw 字号对 loose 包围盒高度明显失真（超出 [0.35, 1.5] 区间）时，
      回退到全角字符紧包围盒高度（≈字号）估计；
    - 存在全角字符且 raw 可信时，仍取 min(raw, 全角紧包围盒最大高)——
      宁可略小也不放不下（与 Rust 版 resolve_font_size_px 对齐）；
    - 无全角字符时按 loose 高度 / 1.15 估计。
    """
    sizes = [c.font_size_pt for c in matched if c.font_size_pt > 0.0]
    loose_heights = [abs(c.max_y - c.min_y) for c in matched if abs(c.max_y - c.min_y) > 0.0]
    avg_raw = sum(sizes) / len(sizes) if sizes else 0.0
    avg_loose = sum(loose_heights) / len(loose_heights) if loose_heights else 0.0

    plausible = avg_raw > 0.0 and avg_loose > 0.0 and 0.35 * avg_loose <= avg_raw <= 1.5 * avg_loose
    fw_glyphs = [c.glyph_h_pt for c in matched if _is_full_width_char(c.ch) and c.glyph_h_pt > 0.0]
    if fw_glyphs:
        # 全角字符（汉字等）紧包围盒高度 ≈ 字号；取最大值避免标点/小字形拉低
        glyph_px = int(round(max(fw_glyphs) * scale))
        if plausible:
            return min(int(round(avg_raw * scale)), glyph_px)
        return glyph_px
    if plausible:
        return int(round(avg_raw * scale))
    if avg_loose > 0.0:
        # 西文字体 loose 包围盒 ≈ 字号 × 1.1~1.2
        return int(round(avg_loose / 1.15 * scale))
    if avg_raw > 0.0:
        return int(round(avg_raw * scale))
    return 0


def extract_pdf_page_chars(page, dpi: int) -> list[ExtractedChar]:
    """从 PDF 页面提取所有文字字符及其包围盒（像素坐标）与字号。

    pypdfium2 的 charbox 返回 (left, bottom, right, top)（PDF 点坐标，
    原点左下）；转换为图像像素坐标（原点左上）。零尺寸字符（行尾 \\r\\n
    等控制符）无几何信息，直接跳过。
    """
    import pypdfium2.raw as pdfium_raw

    try:
        text_page = page.get_textpage()
    except Exception:  # noqa: BLE001
        return []
    try:
        count = text_page.count_chars()
        if count <= 0:
            return []
        scale = dpi / 72.0
        _, page_h_pt = page.get_size()
        chars: list[ExtractedChar] = []
        for idx in range(count):
            try:
                ch = text_page.get_text_range(idx, 1)
            except Exception:  # noqa: BLE001
                continue
            if not ch or ch == "\x00":
                continue
            try:
                left, bottom, right, top = text_page.get_charbox(idx, loose=True)
                _, t_bottom, _, t_top = text_page.get_charbox(idx, loose=False)
            except Exception:  # noqa: BLE001
                continue
            if right <= left or top <= bottom:
                continue  # 零尺寸字符（\r\n 等）无几何意义
            try:
                font_size_pt = float(pdfium_raw.FPDFText_GetFontSize(text_page, idx))
            except Exception:  # noqa: BLE001
                font_size_pt = 0.0
            if not (font_size_pt > 0.0):  # NaN / 0 回落到包围盒高度
                font_size_pt = abs(top - bottom)

            min_x, max_x = sorted((left * scale, right * scale))
            py1 = (page_h_pt - top) * scale
            py2 = (page_h_pt - bottom) * scale
            min_y, max_y = sorted((py1, py2))
            chars.append(ExtractedChar(
                ch, min_x, min_y, max_x, max_y,
                font_size_pt, abs(t_top - t_bottom),
            ))
        return chars
    finally:
        text_page.close()


def extract_text_and_font_size_for_box(
    chars: list[ExtractedChar], b: BoundingBox, scale: float
) -> tuple[str, int, float, float]:
    """提取落在高亮包围盒内的文字与字号、行距、缩进。

    - 自动过滤不在包围盒内的字符（中心点命中或面积重叠 >= 30%，带 4 像素容差）；
    - 按阅读顺序分行排序（行内从左到右，行间从上到下）；
    - 清理占位标签语法；
    - 返回 (文本, 字号px, 行间距px, 首行缩进em)。
    """
    if not chars:
        return ("", 0, 0.0, 0.0)

    pad = 4.0
    box_min_x = max(b.min_x - pad, 0.0)
    box_max_x = b.max_x + pad
    box_min_y = max(b.min_y - pad, 0.0)
    box_max_y = b.max_y + pad

    matched: list[ExtractedChar] = []
    for c in chars:
        if not c.ch.isprintable() and c.ch not in ("\n", "\t"):
            continue
        cx = (c.min_x + c.max_x) / 2.0
        cy = (c.min_y + c.max_y) / 2.0
        center_inside = box_min_x <= cx <= box_max_x and box_min_y <= cy <= box_max_y
        overlap_x = max(min(c.max_x, box_max_x) - max(c.min_x, box_min_x), 0.0)
        overlap_y = max(min(c.max_y, box_max_y) - max(c.min_y, box_min_y), 0.0)
        char_w = max(abs(c.max_x - c.min_x), 1.0)
        char_h = max(abs(c.max_y - c.min_y), 1.0)
        overlap_inside = overlap_x * overlap_y >= 0.3 * char_w * char_h
        if center_inside or overlap_inside:
            matched.append(c)

    if not matched:
        return ("", 0, 0.0, 0.0)

    # 字号解析（raw 字号对包围盒做一致性校验，失真时回退紧包围盒估计）
    font_size_px = _resolve_font_size_px(matched, scale)
    heights = [abs(c.max_y - c.min_y) for c in matched if abs(c.max_y - c.min_y) > 0.0]
    avg_char_h = sum(heights) / len(matched) if heights else 0.0
    if font_size_px <= 0:
        if avg_char_h > 2.0:
            font_size_px = int(round(avg_char_h))
        else:
            font_size_px = max(int(round(b.height() * 0.8)), 1)

    # 按阅读顺序排序：先按垂直中心粗排
    matched.sort(key=lambda c: ((c.min_y + c.max_y) / 2.0, c.min_x))

    # 动态分行
    if font_size_px > 0:
        line_threshold = max(font_size_px * 0.5, 4.0)
    elif avg_char_h > 0.0:
        line_threshold = max(avg_char_h * 0.5, 4.0)
    else:
        line_threshold = max(b.height() * 0.5, 4.0)

    lines: list[list[ExtractedChar]] = []
    for c in matched:
        cy = (c.min_y + c.max_y) / 2.0
        if lines:
            last = lines[-1]
            line_avg_cy = sum((x.min_y + x.max_y) / 2.0 for x in last) / len(last)
            if abs(cy - line_avg_cy) <= line_threshold:
                last.append(c)
                continue
        lines.append([c])

    # 各行内按 x 升序
    for line in lines:
        line.sort(key=lambda c: (c.min_x, c.min_y))

    # 行距与首行缩进检测前先按行收集（行内去首尾空白：PDF 文本层的行尾空格
    # 会与排版换行叠加产生空行槽，把后续行整体推低一行）
    trimmed_lines: list[list[ExtractedChar]] = []
    for line in lines:
        l = list(line)
        while l and l[-1].ch == " ":
            l.pop()
        while l and l[0].ch == " ":
            l.pop(0)
        if l:
            trimmed_lines.append(l)

    # 行距检测
    detected_line_spacing = 0.0
    if len(lines) >= 2:
        centers = [
            sum((x.min_y + x.max_y) / 2.0 for x in line) / len(line) for line in lines
        ]
        total_pitch = sum(max(centers[k + 1] - centers[k], 0.0) for k in range(len(centers) - 1))
        avg_pitch = total_pitch / (len(centers) - 1)
        detected_line_spacing = max(avg_pitch - font_size_px, 0.0)

    # 首行缩进检测
    detected_indent_em = 0.0
    if len(lines) >= 2 and font_size_px > 0:
        line1_min_x = min(c.min_x for c in lines[0])
        min_other_x = min(c.min_x for line in lines[1:] for c in line)
        if line1_min_x > min_other_x + font_size_px * 0.8:
            detected_indent_em = round(max((line1_min_x - min_other_x) / font_size_px, 0.0))

    raw_text = "\n".join("".join(c.ch for c in line) for line in trimmed_lines)
    clean_text = strip_tag_syntax(raw_text)
    return (clean_text, font_size_px, detected_line_spacing, detected_indent_em)


def extract_pdf_page_tags(
    page_chars: list[ExtractedChar],
    page_index: int,
    dpi: int,
    img_width: int,
    img_height: int,
    img: np.ndarray,
    exclude_rects: Sequence[RectPx] = (),
) -> list[TextRegion]:
    """从页面字符流提取 ``{{...}}`` / ``【...】`` 标签区域，并在图像上就地抹白标签文字。

    exclude_rects 为表格单元格矩形：与任一矩形**相交**的标签不生成独立区域
    （由单元格区域接管，避免重复），但**仍然擦除**标签框——单元格路径只擦除
    命中的正文汉字，``{{``/``}}``/前缀等模板字符会残留在底图上。
    判定用重叠而非中心包含：标签框比单元格正文包围盒更宽（含前缀字符），
    中心可能落在单元格矩形之外。擦除必须发生在判定之前，顺序不能变。
    """
    chars = [c.ch for c in page_chars]
    matches = scan_text_tags(chars)
    scale = dpi / 72.0

    regions: list[TextRegion] = []
    for m in matches:
        min_x = float("inf")
        max_x = float("-inf")
        min_y = float("inf")
        max_y = float("-inf")
        tag_chars: list[ExtractedChar] = []
        has_valid_bounds = False

        for k in range(m.start_char_idx, m.end_char_idx + 1):
            if k < len(page_chars):
                e = page_chars[k]
                if e.max_x > e.min_x and e.max_y > e.min_y:
                    min_x = min(min_x, e.min_x)
                    max_x = max(max_x, e.max_x)
                    min_y = min(min_y, e.min_y)
                    max_y = max(max_y, e.max_y)
                    tag_chars.append(e)
                    has_valid_bounds = True

        if not has_valid_bounds:
            continue

        x_px = int(round(min_x))
        y_px = int(round(min_y))
        w_px = max(int(round(max_x - min_x)), 1)
        h_px = max(int(round(max_y - min_y)), 1)

        x = max(0, min(x_px, img_width - 1))
        y = max(0, min(y_px, img_height - 1))
        w = max(1, min(w_px, max(img_width - x, 1)))
        h = max(1, min(h_px, max(img_height - y, 1)))

        font_size = _resolve_font_size_px(tag_chars, scale) if tag_chars else 0

        # 表格单元格内的标签由单元格区域接管：仍擦除标签框（见上），但不生成
        # 独立区域，避免与单元格区域重复。
        overlaps_cell = any(
            x < r.x + r.w and x + w > r.x and y < r.y + r.h and y + h > r.y
            for r in exclude_rects
        )

        # 清理原图上的标签区域为纯白色（向外扩展 2 像素消除文字抗锯齿）
        pad = 2
        ex0 = max(x - pad, 0)
        ey0 = max(y - pad, 0)
        ex1 = min(x + w + pad, img_width)
        ey1 = min(y + h + pad, img_height)
        img[ey0:ey1, ex0:ex1] = 255

        if overlaps_cell:
            continue

        regions.append(TextRegion(
            x=x, y=y, w=w, h=h,
            text=strip_tag_syntax(m.inner_text),
            page=page_index + 1,
            font_size=font_size,
        ))
    return regions


# ---------------------------------------------------------------------------
# 高亮框 + 标签区域合并
# ---------------------------------------------------------------------------

def combine_page_regions_with_role_map(
    highlight_boxes: list[BoundingBox],
    tag_regions: list[TextRegion],
    page_chars: list[ExtractedChar],
    page_num: int,
    scale: float,
    color_map: dict[str, int],
    next_role_id: list[int],
) -> list[TextRegion]:
    """合并高亮区域与文本标签区域，并从 PDF 字符层提取高亮框内部文字与字号。

    color_map / next_role_id 为跨页共享的可变状态：同一高亮颜色在整篇文档中
    映射到同一个角色 ID（首次出现 -> 2，次出现 -> 3 …）。
    """
    final_regions: list[TextRegion] = []
    matched_tags = [False] * len(tag_regions)

    for b in highlight_boxes:
        bx, by = b.min_x, b.min_y
        bw, bh = b.width(), b.height()

        if b.highlight is not None:
            if b.highlight not in color_map:
                color_map[b.highlight] = next_role_id[0]
                next_role_id[0] += 1
            role_id = color_map[b.highlight]
            highlight = b.highlight
        else:
            role_id, highlight = 0, None

        extracted_text, detected_font_size, detected_line_spacing, detected_indent_em = (
            extract_text_and_font_size_for_box(page_chars, b, scale)
        )

        # 查找是否包含或重叠某个 tag_region
        tag_text = ""
        tag_font_size = 0
        for t_idx, tag in enumerate(tag_regions):
            if matched_tags[t_idx]:
                continue
            overlap_x = min(bx + bw, tag.x + tag.w) - max(bx, tag.x)
            overlap_y = min(by + bh, tag.y + tag.h) - max(by, tag.y)
            if overlap_x > 0 and overlap_y > 0:
                matched_tags[t_idx] = True
                if tag.text:
                    tag_text = tag.text
                    tag_font_size = tag.font_size

        if extracted_text:
            text = extracted_text
            font_size = detected_font_size
            line_spacing = (
                round(detected_line_spacing) if detected_line_spacing > 0 else None
            )
            indent_em = detected_indent_em
        elif tag_text:
            text, font_size, line_spacing, indent_em = tag_text, tag_font_size, None, 0.0
        else:
            text, font_size, line_spacing, indent_em = "", detected_font_size, None, 0.0

        final_regions.append(TextRegion(
            x=bx, y=by, w=bw, h=bh,
            text=text,
            role_id=role_id,
            highlight=highlight,
            page=page_num,
            font_size=font_size,
            line_spacing=line_spacing,
            indent_em=indent_em,
        ))

    # 剩余未与高亮框重叠的 tag 区域直接作为独立的 TextRegion
    for t_idx, tag in enumerate(tag_regions):
        if not matched_tags[t_idx]:
            final_regions.append(tag)

    # 按照从上到下、从左到右排序（y 相差 <= 10 视为同一行，按 x 排）
    def _region_order(a: TextRegion, b: TextRegion) -> int:
        if abs(a.y - b.y) <= 10:
            return -1 if a.x < b.x else (1 if a.x > b.x else 0)
        return -1 if a.y < b.y else (1 if a.y > b.y else 0)

    final_regions.sort(key=functools.cmp_to_key(_region_order))
    return final_regions


def combine_page_regions(
    highlight_boxes: list[BoundingBox],
    tag_regions: list[TextRegion],
    page_chars: list[ExtractedChar],
    page_num: int,
    scale: float,
) -> list[TextRegion]:
    """单页便捷包装（内部角色映射从 2 开始重新分配）。"""
    return combine_page_regions_with_role_map(
        highlight_boxes, tag_regions, page_chars, page_num, scale, {}, [2]
    )


# ---------------------------------------------------------------------------
# docx 表格单元格区域（对齐 Rust doc_render.rs::process_tables_on_page）
# ---------------------------------------------------------------------------

# docx 的 w:jc 编号（0 左 / 1 中 / 2 右）→ TextRegion.align 字符串。
# Rust 版 TextRegion.align 是 i32，Python 版是 "left"/"center"/"right"
#（engine_fast / GUI / 参数校验都按字符串消费），故在区域生成处一次性换算。
_JC_TO_ALIGN: dict[int, str] = {0: "left", 1: "center", 2: "right"}


@dataclass
class TablePageResult:
    """一页的表格处理结果。"""

    regions: list[TextRegion] = field(default_factory=list)
    # 已生成区域的单元格矩形（供标签区域排重）
    cell_rects: list[RectPx] = field(default_factory=list)
    # 每张（有区域的）表的外包围矩形（供高亮框分流）
    outer_rects: list[RectPx] = field(default_factory=list)
    # 下一页的流起点（本页已消费元素之后的下标；未定位/未完成的表格
    # 保持指向自身，下一页重走）
    next_start_element: int = 0


def process_tables_on_page(
    stream: Sequence[BodyElement],
    views: Sequence[TableView],
    chars: list[ExtractedChar],
    start_element: int,
    page_num: int,
    dpi: int,
    arr: np.ndarray,
    color_map: dict[str, int],
    next_role_id: list[int],
) -> TablePageResult:
    """处理一页上的所有表格：定位 → 擦字 → 生成单元格 TextRegion。

    start_element 为本页在 body 流中的起点（上一页的 next_start_element）。
    located_on_page == False 的表整表跳过（不生成区域、不擦除、不推断矩形），
    但流位置（next_start_element）由 locate 结果决定，与是否输出区域无关。
    无表格或整表未命中时返回空结果（表格保留印刷底图）。

    color_map / next_role_id 为跨页共享的高亮色 → 角色 ID 映射，与高亮框路径
    同池（同一颜色在整篇文档中只有一个角色）。
    """
    # 延迟导入：docx_tables 反向依赖本模块的 strip_tag_syntax/classify_highlight_color，
    # 顶层 import 会形成循环；同时也让表格功能的可用性不阻塞基础渲染
    from .docx_io import CellVMerge
    from .docx_tables import RectPx, infer_cell_rects, locate_tables_on_page

    scale = dpi / 72.0
    matches, next_start_element = locate_tables_on_page(
        stream, views, chars, start_element
    )
    result = TablePageResult(next_start_element=next_start_element)
    img_h, img_w = arr.shape[:2]

    for view in views:
        match = next((m for m in matches if m.table_index == view.index), None)
        if match is None:
            continue
        # 本页未定位的表（整表在更早/更晚页）：不生成区域、不擦除、不推断矩形
        if not match.located_on_page:
            continue
        # 行主序扁平展开：flat_cells / flat_matches / rects 三者下标一一对应
        #（均按 view.rows 迭代顺序展开，含续行格占位）
        flat_cells = [c for row in view.rows for c in row.cells]
        flat_matches = [cm for row_m in match.row_matches for cm in row_m]
        rects = infer_cell_rects(view, match, chars)
        if all(r is None for r in rects):
            continue  # 整表未命中：保留印刷底图

        # 参与生成区域的单元格：非续行、非灰高亮、非空文本且矩形可得
        #（区域与排重矩形同源）
        cells_on_page: list[tuple[int, RectPx]] = []
        for i, (cell, rect) in enumerate(zip(flat_cells, rects)):
            if cell.v_merge == CellVMerge.CONTINUE:
                continue
            if cell.skip_printed:
                continue
            if not cell.text.strip():
                continue
            if rect is None:
                continue
            cells_on_page.append((i, rect))

        table_regions: list[TextRegion] = []
        for i, rect in cells_on_page:
            cell = flat_cells[i]
            located = flat_matches[i] if i < len(flat_matches) else None
            # 命中字符：擦除原印刷字（保留表格线）；字号取命中字符校准值
            font_size = 0
            if located is not None:
                erase_char_boxes(arr, chars, located.char_idxs, 2)
                refs = [chars[k] for k in located.char_idxs if 0 <= k < len(chars)]
                font_size = _resolve_font_size_px(refs, scale) if refs else 0
            if font_size <= 0 and cell.sz_half_pt:
                # 未命中或校准失败：回退 w:sz（半磅）→ 像素
                font_size = int(round((cell.sz_half_pt / 2.0) * scale))
            # 区域矩形：命中 bbox 外扩（水平 6 / 竖直 4）；推断矩形已内缩
            if located is not None:
                region_rect = RectPx(rect.x - 6, rect.y - 4, rect.w + 12, rect.h + 8)
            else:
                region_rect = rect
            # 钳制到图像内（框可能越出页边）
            rx = max(region_rect.x, 0)
            ry = max(region_rect.y, 0)
            rw = max(min(region_rect.w, img_w - rx), 1)
            rh = max(min(region_rect.h, img_h - ry), 1)
            # 角色映射：高亮色走共享 color_map（与高亮框路径同池）
            if cell.highlight is not None:
                if cell.highlight not in color_map:
                    color_map[cell.highlight] = next_role_id[0]
                    next_role_id[0] += 1
                role_id = color_map[cell.highlight]
                highlight = cell.highlight
            else:
                role_id, highlight = 0, None
            table_regions.append(TextRegion(
                x=rx, y=ry, w=rw, h=rh,
                text=cell.text,
                role_id=role_id,
                highlight=highlight,
                printed=cell.printed,
                page=page_num,
                font_size=font_size,
                align=_JC_TO_ALIGN.get(cell.align, "left"),
                margin_left=4,
                margin_right=4,
                margin_top=2,
                margin_bottom=2,
                table_index=view.index,
            ))

        if not table_regions:
            continue
        # 外包围 = 单元格矩形并集（外扩 4px）；供高亮框「框完整落在表内」分流
        first_rect = cells_on_page[0][1]
        x0, y0 = first_rect.x, first_rect.y
        x1 = first_rect.x + first_rect.w
        y1 = first_rect.y + first_rect.h
        for _, rect in cells_on_page[1:]:
            x0 = min(x0, rect.x)
            y0 = min(y0, rect.y)
            x1 = max(x1, rect.x + rect.w)
            y1 = max(y1, rect.y + rect.h)
        result.outer_rects.append(RectPx(x0 - 4, y0 - 4, x1 - x0 + 8, y1 - y0 + 8))
        result.cell_rects.extend(rect for _, rect in cells_on_page)
        result.regions.extend(table_regions)

    return result


# ---------------------------------------------------------------------------
# 入口：PDF / DOCX -> 逐页 PNG + 自动识别的 TextRegion
# ---------------------------------------------------------------------------

def pdf_to_images_with_regions(
    pdf_path: str | Path,
    out_dir: str | Path,
    dpi: int = 200,
    prefix: str | None = None,
    body_stream: Sequence[BodyElement] | None = None,
) -> tuple[list[Path], list[TextRegion]]:
    """把 PDF 逐页栅格化为 PNG，并自动检测标记区域。

    返回 (文件路径列表, 识别出的 TextRegion 列表)；页面上被标记的高亮色块
    与占位标签文字会被擦除为纯白，使底图可直接用于打印。

    body_stream 为 docx body 元素流（``docx_io.load_body_stream`` 的产物）：
    非空时启用表格单元格识别；PDF 直接导入时为空，行为与旧版一致。
    """
    import pypdfium2 as pdfium

    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if prefix is None:
        prefix = pdf_path.stem
    _clear_stale_pages(out_dir, prefix)

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        scale = dpi / 72.0
        paths: list[Path] = []
        all_regions: list[TextRegion] = []
        color_map: dict[str, int] = {}
        next_role_id = [2]
        # body 流的表格视图只依赖文档结构，构建一次供所有页复用
        stream: list[BodyElement] = list(body_stream or [])
        views: list[TableView] = []
        if stream:
            # 延迟导入：见 process_tables_on_page 的说明（避免循环依赖）
            from .docx_tables import build_table_views

            views = build_table_views(stream)
        # 跨页流游标：本页已消费元素之后的下标，供下一页作为流起点
        #（前页元素不再消耗后页字符，防游标中毒）
        start_element = 0

        for index, page in enumerate(doc):
            image = page.render(scale=scale).to_pil().convert("RGB")
            raw_path = out_dir / f"{prefix}_{index}_raw.png"
            image.save(raw_path)
            arr = np.array(image)  # 可写拷贝（后续就地抹白高亮/标签）

            # 1. 提取页面所有字符对象及其包围盒与字号
            page_chars = extract_pdf_page_chars(page, dpi)

            # 2. 表格：定位 + 擦字 + 生成单元格区域（先于标签/高亮，
            #    标签排除与高亮分流依赖其矩形）
            if not views:
                # 无表格：流位置保持不变（显式传递，避免默认值 0 回退）
                table_result = TablePageResult(next_start_element=start_element)
            else:
                table_result = process_tables_on_page(
                    stream, views, page_chars, start_element, index + 1, dpi,
                    arr, color_map, next_role_id,
                )
            start_element = table_result.next_start_element

            # 3. {{...}}/【...】标签（表格内标签已被单元格区域接管，不重复生成）
            tag_regions = extract_pdf_page_tags(
                page_chars, index, dpi, arr.shape[1], arr.shape[0], arr,
                exclude_rects=table_result.cell_rects,
            )

            # 4. 高亮色块分区：表内框不整框擦白（保表格线）、不生成区域，
            #    其残留高亮像素由 erase_highlight_boxes 内的整页像素过滤步骤一并清除。
            #    先按表格分流再换行合并：表内对角相邻的单元格高亮天然满足
            #    行尾/行首签名，若先合并会逃逸表格分流、整框擦白时抹掉表格线；
            #    而两个都不被表格包含的框其并集必不被包含，故对纯表外合并而言
            #    两种顺序等价。
            highlight_boxes = detect_highlight_boxes(arr)
            _in_table, normal_boxes = _partition_highlight_boxes(
                highlight_boxes, table_result.outer_rects
            )
            # 换行签名合并：规则 3 要求水平重叠 ≥40%，「句子从行中开始、下一行
            # 行首结束」的两行条纹零重叠会被切成上下两块，这里按行尾/行首签名补合
            normal_boxes = merge_wrap_line_boxes(normal_boxes, page_chars)
            erase_highlight_boxes(arr, normal_boxes)

            # 5. 表外高亮框 → 区域（原逻辑不变），表格区域按行主序排在前面
            page_regions = combine_page_regions_with_role_map(
                normal_boxes, tag_regions, page_chars,
                index + 1, scale, color_map, next_role_id,
            )
            all_regions.extend(table_result.regions)
            all_regions.extend(page_regions)

            path = out_dir / f"{prefix}_{index}.png"
            Image.fromarray(arr).save(path)
            paths.append(path)
    finally:
        doc.close()
    if not paths:
        raise RuntimeError(f"PDF 没有可渲染的页面：{pdf_path}")
    return (paths, all_regions)


def _clear_stale_pages(out_dir: Path, prefix: str) -> None:
    """清理同前缀的旧页文件，避免旧文档页数混入新导入结果。"""
    for old in out_dir.glob(f"{prefix}*.png"):
        try:
            old.unlink()
        except OSError:
            pass


def pdf_to_images(
    pdf_path: str | Path, out_dir: str | Path, dpi: int = 200, prefix: str = "page"
) -> list[Path]:
    """把 PDF 逐页栅格化为 PNG，返回按页序排列的文件路径列表。"""
    paths, _ = pdf_to_images_with_regions(pdf_path, out_dir, dpi=dpi, prefix=prefix)
    return paths


def _soffice_candidates() -> list[Path]:
    """LibreOffice 可执行文件的候选路径（按平台）。

    Windows 版 LibreOffice 安装时**不会**把自己加入 PATH，只查 PATH 会把
    「已安装」误判成「未安装」（用户实测：已装 LibreOffice 仍提示需要安装）。
    故除 PATH 外还要探测安装目录与注册表（见 ``find_soffice``）。
    """
    if sys.platform == "win32":
        return [
            Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
            Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
        ]
    if sys.platform == "darwin":
        return [Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")]
    # Linux / 其他类 Unix：发行版包管理器与 snap / flatpak 的常见落点
    return [
        Path("/usr/bin/soffice"),
        Path("/usr/local/bin/soffice"),
        Path("/snap/bin/libreoffice"),
        Path("/opt/libreoffice/program/soffice"),
    ]


def _soffice_from_registry() -> Path | None:
    """从 Windows 注册表取 LibreOffice 安装目录（默认值即 program 目录）。

    ``HKLM\\SOFTWARE\\LibreOffice\\UNO\\InstallPath`` 的默认值是
    ``...\\LibreOffice\\program``，据此拼出 ``soffice.exe``；读注册表失败
    （非 Windows / 无权限 / 未安装）一律返回 None。
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:  # pragma: no cover - 仅非 Windows 会走到
        return None
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for sub in (r"SOFTWARE\LibreOffice\UNO\InstallPath", r"SOFTWARE\WOW6432Node\LibreOffice\UNO\InstallPath"):
            try:
                with winreg.OpenKey(root, sub) as key:
                    install, _ = winreg.QueryValueEx(key, "")
            except OSError:
                continue
            exe = Path(str(install)) / "soffice.exe"
            if exe.is_file():
                return exe
    return None


def find_soffice() -> Path | None:
    """查找 LibreOffice 可执行文件：PATH → 平台常见安装目录 → Windows 注册表。

    对齐 Rust 版 ``find_soffice``（PATH + 两个 Windows 安装目录），并额外补
    注册表与 macOS/Linux 常见路径：只查 PATH 是「装了却提示没装」的根因。
    """
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return Path(found)
    for candidate in _soffice_candidates():
        if candidate.is_file():
            return candidate
    return _soffice_from_registry()


def _run_soffice(soffice: Path, docx_path: Path, out_dir: Path) -> None:
    """调用 LibreOffice 无头模式把 docx 转 PDF（失败不抛，由调用方按文件判定）。

    使用独立的临时用户配置目录（``-env:UserInstallation``）：用户开着
    LibreOffice 界面时，默认配置目录被锁，headless 转换会挂起或转交给正在
    运行的 GUI 实例（可能弹出窗口）。隔离配置后可稳定并发转换。
    """
    profile_dir = Path(tempfile.mkdtemp(prefix="handwrite_lo_profile_"))
    try:
        subprocess.run(
            [
                str(soffice),
                f"-env:UserInstallation={profile_dir.as_uri()}",
                "--headless",
                "--norestore",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(docx_path),
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=300,
        )
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)


def docx_to_pdf(docx_path: str | Path, out_dir: str | Path) -> Path:
    """把 DOCX 转成 PDF（Word COM 优先，LibreOffice 兜底）。

    LibreOffice 查找覆盖 PATH 与各平台默认安装目录（Windows 版不写 PATH），
    因此「已安装却提示未安装」不会再发生；仍找不到时错误信息给出排查办法。
    """
    docx_path = Path(docx_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / (docx_path.stem + ".pdf")

    if sys.platform == "win32":
        script = _word_com_script(docx_path, pdf_path)
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                errors="replace",  # PowerShell 中文系统输出 GBK，避免解码崩溃
                timeout=300,
            )
            # Word 退出阶段（$word.Quit）偶发 RPC 已断开的 COM 异常使退出码非 0，
            # 但此时 PDF 已生成；以文件存在为准判断转换成功
            if pdf_path.exists():
                return pdf_path
        except (OSError, subprocess.TimeoutExpired):
            pass  # Word 未安装或转换失败，继续尝试 LibreOffice

    soffice = find_soffice()
    if soffice is not None:
        try:
            _run_soffice(soffice, docx_path, out_dir)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if pdf_path.exists():
            return pdf_path

    checked = ["PATH 中的 soffice / libreoffice", *(str(p) for p in _soffice_candidates())]
    if sys.platform == "win32":
        checked.append("Windows 注册表 LibreOffice\\UNO\\InstallPath")
    raise RuntimeError(
        "无法把 DOCX 转成打印预览：未找到可用的 Microsoft Word 或 LibreOffice。\n"
        f"已检查：{'、'.join(checked)}。\n"
        "解决办法（任选其一）：\n"
        "1. 安装免费的 LibreOffice 后重试（本程序会自动探测默认安装目录）；\n"
        "2. 在 Word / WPS 中「另存为 PDF」，再导入 PDF——版式最稳，推荐；\n"
        "3. 已安装但装在非默认目录时，把其 program 目录（含 soffice.exe）加入 PATH 后重启本程序。"
    )


def _word_com_script(docx_path: Path, pdf_path: Path) -> str:
    """生成调用 Word COM 另存为 PDF 的 PowerShell 脚本（17 = wdFormatPDF）。

    退出码显式化：COM 拉起的 Word 在最后一个文档关闭后可能自行退出，此时
    Quit() 抛 RPC 0x800706BE；且 powershell -Command 下即使异常被 try/catch
    捕获，退出码仍可能被置 1，覆盖成功的转换。故收尾以 PDF 文件是否存在
    显式决定退出码（调用方同样校验退出码 + 文件存在，见 docx_to_pdf）。
    """
    src = str(docx_path).replace("'", "''")
    dst = str(pdf_path).replace("'", "''")
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "$word = New-Object -ComObject Word.Application\n"
        "$word.Visible = $false\n"
        "try {\n"
        f"  $doc = $word.Documents.Open('{src}', $false, $true)\n"
        f"  $doc.SaveAs([ref]'{dst}', [ref]17)\n"
        "  $doc.Close($false)\n"
        "} finally {\n"
        "  try { $word.Quit() } catch { }\n"
        "}\n"
        f"if (Test-Path '{dst}') {{ exit 0 }} else {{ exit 1 }}\n"
    )


def document_to_page_images_with_regions(
    path: str | Path, out_dir: str | Path, dpi: int = 200
) -> tuple[list[Path], list[TextRegion]]:
    """入口：PDF 直接渲染；DOCX 先转 PDF。返回逐页 PNG 路径与自动识别的 TextRegion 列表。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return pdf_to_images_with_regions(path, out_dir, dpi=dpi)
    if suffix == ".docx":
        # 表格结构解析失败不阻塞导入：退化为纯底图（表格保持印刷）
        from .docx_io import load_body_stream

        try:
            body_stream = load_body_stream(path)
        except Exception:  # noqa: BLE001
            body_stream = []
        pdf_path = docx_to_pdf(path, out_dir)
        return pdf_to_images_with_regions(
            pdf_path, out_dir, dpi=dpi, prefix=path.stem, body_stream=body_stream
        )
    raise ValueError(f"不支持的文档类型：{path.suffix}（支持 .pdf / .docx）")


def document_to_page_images(
    path: str | Path, out_dir: str | Path, dpi: int = 200
) -> list[Path]:
    """入口：PDF 直接渲染；DOCX 先转 PDF。返回逐页 PNG 路径（页序即列表序）。"""
    paths, _ = document_to_page_images_with_regions(path, out_dir, dpi=dpi)
    return paths
