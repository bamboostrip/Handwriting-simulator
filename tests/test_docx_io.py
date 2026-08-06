"""docx 解析测试。"""

from __future__ import annotations

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

from handwritesim.core.docx_io import load_paragraphs
from handwritesim.core.models import Paragraph


def _make_docx(path) -> None:
    doc = Document()
    hp = doc.add_paragraph("会议通知")
    hp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    body = doc.add_paragraph()
    body.paragraph_format.first_line_indent = Pt(24)  # 2 字符 @12pt
    body.add_run("现将有关事项通知如下。")
    doc.save(path)


def test_load_paragraphs(tmp_path):
    docx_path = tmp_path / "test.docx"
    _make_docx(docx_path)
    paras = load_paragraphs(docx_path)
    assert paras[0].align == "center"
    assert paras[0].first_line_indent == 0
    assert paras[1].align == "left"
    assert paras[1].first_line_indent > 0
    assert isinstance(paras[1], Paragraph)


def test_right_align(tmp_path):
    doc = Document()
    p = doc.add_paragraph("汇报人：张三")
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    path = tmp_path / "right.docx"
    doc.save(path)
    paras = load_paragraphs(path)
    assert paras[0].align == "right"


def test_first_line_chars_indent(tmp_path):
    """中文 Word 的“首行缩进 2 字符”写 firstLineChars，应按字号换算。"""
    doc = Document()
    p = doc.add_paragraph("现将有关事项通知如下。")
    pPr = p._p.get_or_add_pPr()
    pPr.append(pPr.makeelement(qn("w:ind"), {qn("w:firstLineChars"): "200"}))
    path = tmp_path / "chars.docx"
    doc.save(path)
    paras = load_paragraphs(path, font_size=140)
    assert paras[0].first_line_indent == 280


def test_style_inherited_indent(tmp_path):
    """直接格式缺失时应沿样式链继承首行缩进。"""
    doc = Document()
    doc.styles["Normal"].paragraph_format.first_line_indent = Pt(24)
    doc.add_paragraph("继承缩进的正文。")
    path = tmp_path / "style.docx"
    doc.save(path)
    paras = load_paragraphs(path)
    assert paras[0].first_line_indent > 0