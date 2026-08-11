"""PDF 导出（img2pdf 位图层方案）测试。

与 Rust 版 printpdf 导出对齐：页物理尺寸 = 像素 @ 300 DPI。
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from handwritesim.core.engine import HandwritingEngine
from handwritesim.core.models import HandwritingParams, Paragraph

_FONTS = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
)


def _font() -> str:
    for font in _FONTS:
        if os.path.exists(font):
            return font
    pytest.skip("未找到系统 CJK 字体")


def _params(tmp_path: Path, text: str) -> HandwritingParams:
    bg = tmp_path / "bg.png"
    Image.new("RGB", (400, 300), "white").save(bg)
    return HandwritingParams(
        text=text,
        font_path=_font(),
        background_path=str(bg),
        font_size=40,
        line_spacing=55,
        word_spacing=6,
        perturb_theta_sigma=0.05,
    )


def _open_pdf(path: Path):
    """用 pikepdf 读回 PDF（img2pdf 的传递依赖），返回 (页数, 每页 (图, 尺寸))。"""
    import pikepdf

    pages = []
    with pikepdf.open(path) as pdf:
        for page in pdf.pages:
            images = [pikepdf.PdfImage(obj).as_pil_image() for obj in page.get_images().values()]
            pages.append((images, page.MediaBox))
    return pages


def test_save_pdf_writes_valid_pdf(tmp_path: Path) -> None:
    params = _params(tmp_path, "PDF 导出测试文本。" * 20)
    out = tmp_path / "out.pdf"
    engine = HandwritingEngine(seed=7)
    engine.save_pdf(params, out)
    assert out.exists()
    data = out.read_bytes()
    assert data.startswith(b"%PDF-"), "应以 %PDF- 开头"
    assert len(data) > 1000, "PDF 应包含图像数据"


def test_save_pdf_page_count_and_pixels(tmp_path: Path) -> None:
    """PDF 页数与 generate 一致，且同 seed 下 PDF 内页图逐像素等于导出的 PNG。"""
    params = _params(tmp_path, "PDF 逐像素一致性。" * 60)
    # 与 GUI 相同约定：预览与导出用相同 seed 的独立引擎
    pages = list(HandwritingEngine(seed=42).generate(params))
    assert len(pages) >= 2

    out = tmp_path / "out.pdf"
    HandwritingEngine(seed=42).save_pdf(params, out)
    pdf_pages = _open_pdf(out)
    assert len(pdf_pages) == len(pages), "PDF 页数应与渲染页数一致"

    for (images, _), page in zip(pdf_pages, pages):
        assert len(images) == 1
        assert np.array_equal(
            np.asarray(images[0].convert("RGB")), np.asarray(page.convert("RGB"))
        ), "PDF 内页图应与渲染结果逐像素一致"


def test_save_pdf_page_size_at_300dpi(tmp_path: Path) -> None:
    """页物理尺寸应等于 像素 @ 300 DPI（400×300 → 96×72 pt）。"""
    params = _params(tmp_path, "页尺寸测试")
    out = tmp_path / "out.pdf"
    HandwritingEngine(seed=1).save_pdf(params, out)
    (images, media_box), = _open_pdf(out)
    assert images[0].size == (400, 300)
    w, h = float(media_box[2]), float(media_box[3])
    assert w == pytest.approx(400 * 72.0 / 300.0)
    assert h == pytest.approx(300 * 72.0 / 300.0)


def test_save_pdf_paragraphs_multipage(tmp_path: Path) -> None:
    """段落路径的多页文本也能导出 PDF。"""
    params = _params(tmp_path, "占位")
    params.paragraphs = [Paragraph("标题", align="center"), Paragraph("正文段落内容。" * 80)]
    pages = list(HandwritingEngine(seed=5).generate(params))
    assert len(pages) >= 2

    out = tmp_path / "out.pdf"
    HandwritingEngine(seed=5).save_pdf(params, out)
    assert len(_open_pdf(out)) == len(pages)


def test_save_pdf_returns_path(tmp_path: Path) -> None:
    params = _params(tmp_path, "返回值测试")
    out = tmp_path / "out.pdf"
    result = HandwritingEngine(seed=1).save_pdf(params, out)
    assert result == out
