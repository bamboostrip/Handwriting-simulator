"""docx 文档解析：提取段落对齐与首行缩进。"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

from .models import Paragraph

# docx 使用 EMU（1 英寸 = 914400 EMU）；1pt = 12700 EMU（1/72 英寸）
_EMU_PER_PT = 12700


def _first_line_chars(para) -> float | None:
    """读取 w:firstLineChars（1/100 字符），直接格式优先，再沿样式链继承。

    中文 Word 文档的“首行缩进 2 字符”通常写 firstLineChars 而非 EMU，
    python-docx 的 paragraph_format.first_line_indent 读不到它。
    """
    pPr = para._p.pPr
    if pPr is not None:
        ind = pPr.find(qn("w:ind"))
        if ind is not None:
            value = ind.get(qn("w:firstLineChars"))
            if value:
                return int(value) / 100.0
    style = para.style
    while style is not None:
        spPr = style.element.find(qn("w:pPr"))
        if spPr is not None:
            ind = spPr.find(qn("w:ind"))
            if ind is not None:
                value = ind.get(qn("w:firstLineChars"))
                if value:
                    return int(value) / 100.0
        style = style.base_style
    return None


def _font_size_pt(para, doc) -> float:
    """取段落实际字号（pt）：run 直接格式 > 段落样式链 > Normal > docDefaults，兜底 12。"""
    for run in para.runs:
        size = run.font.size
        if size:
            return size.pt
    style = para.style
    while style is not None:
        size = style.font.size
        if size:
            return size.pt
        style = style.base_style
    try:
        size = doc.styles["Normal"].font.size
        if size:
            return size.pt
    except KeyError:
        pass
    el = doc.styles.element.find(qn("w:docDefaults"))
    if el is not None:
        el = el.find(qn("w:rPrDefault"))
        if el is not None:
            el = el.find(qn("w:rPr"))
            if el is not None:
                sz = el.find(qn("w:sz"))
                if sz is not None:
                    val = sz.get(qn("w:val"))
                    if val:
                        return int(val) / 2.0  # 半磅 -> pt
    return 12.0


def _first_line_emu_chars(para, doc) -> float | None:
    """把 firstLine（twips→EMU）按文档字号还原为字符数。

    Word/WPS 某些版本把“首行缩进 2 字符”存成固定值 firstLine
    （如 480twips = 12pt 字号的 2 字符）而不写 firstLineChars；
    先按文档字号换算回字符数，再随渲染字号等比缩放，
    否则缩进固定为 96dpi 像素，渲染字号大时明显偏小。
    """
    value = para.paragraph_format.first_line_indent
    if not value:
        style = para.style
        while style is not None:
            value = style.paragraph_format.first_line_indent
            if value:
                break
            style = style.base_style
    if not value:
        return None
    pt = value / _EMU_PER_PT  # EMU -> pt
    return pt / _font_size_pt(para, doc)


def _resolve_align(para) -> str:
    if para.alignment == WD_ALIGN_PARAGRAPH.CENTER:
        return "center"
    if para.alignment == WD_ALIGN_PARAGRAPH.RIGHT:
        return "right"
    return "left"


def load_paragraphs(path: str | Path, font_size: int | None = None) -> list[Paragraph]:
    """读取 docx 中每个段落，返回 [Paragraph]（忽略空段落）。

    首行缩进优先按字符数（firstLineChars）× font_size 换算，
    与 GUI“首行缩进”按钮（2×字体大小）的语义一致；
    无字符数时回退 EMU（直接/样式继承）按 96dpi 换算。
    """
    doc = Document(str(path))
    result: list[Paragraph] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        # 优先字符单位（firstLineChars），否则 firstLine 固定值按文档字号还原字符数
        chars = _first_line_chars(para) or _first_line_emu_chars(para, doc)
        indent = int(round(chars * (font_size or 36))) if chars else 0
        result.append(Paragraph(text=text, align=_resolve_align(para), first_line_indent=indent))
    return result
