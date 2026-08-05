"""docx 解析测试。"""

from __future__ import annotations

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
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