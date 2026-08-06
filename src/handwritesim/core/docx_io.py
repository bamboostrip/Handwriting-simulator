"""docx 文档解析：提取段落对齐与首行缩进。"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

from .models import Paragraph

# docx 使用 EMU（1 英寸 = 914400 EMU），按 96dpi 换算像素
_EMU_PER_INCH = 914400
_DPI = 96


def _emu_to_px(emu: float | None) -> int:
    if not emu:
        return 0
    return int(round(emu / _EMU_PER_INCH * _DPI))


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


def _effective_first_line_emu(para):
    """首行缩进 EMU：直接格式优先，再沿样式链继承。"""
    value = para.paragraph_format.first_line_indent
    if value:
        return value
    style = para.style
    while style is not None:
        value = style.paragraph_format.first_line_indent
        if value:
            return value
        style = style.base_style
    return None


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
        chars = _first_line_chars(para)
        if chars:
            indent = int(round(chars * (font_size or 36)))
        else:
            indent = _emu_to_px(_effective_first_line_emu(para))
        result.append(Paragraph(text=text, align=_resolve_align(para), first_line_indent=indent))
    return result
