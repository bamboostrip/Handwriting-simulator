"""docx 文档解析：提取段落对齐与首行缩进。"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

from .models import Paragraph

# docx 使用 EMU（1 英寸 = 914400 EMU），按 96dpi 换算像素
_EMU_PER_INCH = 914400
_DPI = 96


def _emu_to_px(emu: float | None) -> int:
    if not emu:
        return 0
    return int(round(emu / _EMU_PER_INCH * _DPI))


def load_paragraphs(path: str | Path) -> list[Paragraph]:
    """读取 docx 中每个段落，返回 [Paragraph]（忽略空段落）。"""
    doc = Document(str(path))
    result: list[Paragraph] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        align = "center" if para.alignment == WD_ALIGN_PARAGRAPH.CENTER else "left"
        indent = _emu_to_px(para.paragraph_format.first_line_indent)
        result.append(Paragraph(text=text, align=align, first_line_indent=indent))
    return result