"""文档底图（PDF/DOCX 打印预览）与逐页背景测试。"""

from __future__ import annotations

import os

import numpy as np
import pytest
from PIL import Image

from handwritesim.core.doc_render import document_to_page_images
from handwritesim.core.engine import HandwritingEngine
from handwritesim.core.models import HandwritingParams, TextRegion


def _flat_image(color: tuple[int, int, int], size=(400, 300)) -> Image.Image:
    return Image.new("RGB", size, color)


def _make_pdf(tmp_path, colors) -> str:
    """用 img2pdf 把纯色页拼成多页 PDF，返回路径。"""
    import img2pdf

    images = []
    paths = []
    for i, color in enumerate(colors):
        p = tmp_path / f"src_{i}.png"
        _flat_image(color).save(p)
        paths.append(str(p))
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(img2pdf.convert(paths))
    return str(pdf)


def _near_black_mask(image: Image.Image, limit: int = 60) -> np.ndarray:
    """近黑色墨迹掩码（彩色背景不会误判为墨迹）。"""
    arr = np.asarray(image.convert("RGB")).astype(np.int16)
    return np.all(arr < limit, axis=-1)


def test_pdf_to_page_images(tmp_path) -> None:
    """PDF 应按页序栅格化为多张 PNG。"""
    pdf = _make_pdf(tmp_path, [(255, 0, 0), (0, 0, 255)])
    pages = document_to_page_images(pdf, tmp_path / "out", dpi=72)
    assert len(pages) == 2
    first = np.asarray(Image.open(pages[0]).convert("RGB")).astype(np.int16)
    second = np.asarray(Image.open(pages[1]).convert("RGB")).astype(np.int16)
    # 第一页偏红、第二页偏蓝（允许栅格化带来的少量偏差）
    assert first[..., 0].mean() > 200 and first[..., 2].mean() < 80
    assert second[..., 2].mean() > 200 and second[..., 0].mean() < 80


def test_unsupported_document_type(tmp_path) -> None:
    with pytest.raises(ValueError):
        document_to_page_images(tmp_path / "a.txt", tmp_path)


# ---------------------------------------------------------------------------
# 引擎逐页背景
# ---------------------------------------------------------------------------
def _bg_params(tmp_path, colors) -> HandwritingParams:
    paths = []
    for i, color in enumerate(colors):
        p = tmp_path / f"bg_{i}.png"
        _flat_image(color).save(p)
        paths.append(str(p))
    params = HandwritingParams(
        font_path=_system_font(),
        background_path=paths[0],
        background_pages=list(paths),
        font_size=30,
        line_spacing=40,
    )
    return params


def _system_font() -> str:
    for candidate in (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ):
        if os.path.exists(candidate):
            return candidate
    pytest.skip("未找到系统 CJK 字体")


def test_engine_uses_per_page_backgrounds(tmp_path) -> None:
    """第 2 页的区域应画在第 2 页的蓝色底图上，第 1 页保持红色底图。"""
    params = _bg_params(tmp_path, [(255, 0, 0), (0, 0, 255)])
    box = (150, 120, 180, 90)
    params.regions = [
        TextRegion(x=box[0], y=box[1], w=box[2], h=box[3],
                   text="手写填写", printed=True, page=2)
    ]
    pages = list(HandwritingEngine(backend="fast", seed=5).generate(params))
    assert len(pages) == 2

    arr0 = np.asarray(pages[0].convert("RGB")).astype(np.int16)
    arr1 = np.asarray(pages[1].convert("RGB")).astype(np.int16)
    # 页面主体颜色：第 1 页红、第 2 页蓝
    assert arr0[..., 0].mean() > 180 and arr0[..., 2].mean() < 100
    assert arr1[..., 2].mean() > 180 and arr1[..., 0].mean() < 100

    # 区域墨迹只出现在第二页的框内
    ink0 = _near_black_mask(pages[0])
    ink1 = _near_black_mask(pages[1])
    inner0 = ink0[box[1]:box[1] + box[3], box[0]:box[0] + box[2]]
    inner1 = ink1[box[1]:box[1] + box[3], box[0]:box[0] + box[2]]
    assert not inner0.any()
    assert inner1.any()


def test_engine_single_background_fallback_for_extra_pages(tmp_path) -> None:
    """区域所在页超出文档页数时复用最后一页背景，不崩溃。"""
    params = _bg_params(tmp_path, [(255, 0, 0), (0, 0, 255)])
    params.regions = [
        TextRegion(x=40, y=40, w=120, h=60, text="第三页区域内容", page=3)
    ]
    pages = list(HandwritingEngine(backend="fast").generate(params))
    assert len(pages) >= 3
    for page in pages:
        assert page.size == (400, 300)


def test_missing_page_background_fails_validation(tmp_path) -> None:
    params = _bg_params(tmp_path, [(255, 0, 0), (0, 0, 255)])
    params.background_pages[1] = str(tmp_path / "nope.png")
    params.regions = [TextRegion(x=10, y=10, w=50, h=40, text="字")]
    with pytest.raises(HandwritingParams.ValidationError):
        params.validate(require_text=True)


# ---------------------------------------------------------------------------
# 纯背景预览（无文字、无区域、无字体）
# ---------------------------------------------------------------------------
def test_validate_allows_background_only_without_font(tmp_path) -> None:
    """无任何文字时不需要字体即可通过结构校验；require_text=True 仍拒绝。"""
    bg = tmp_path / "bg.png"
    _flat_image((255, 255, 255)).save(bg)
    params = HandwritingParams(background_path=str(bg))
    params.validate(require_text=False)  # 不抛异常即通过
    with pytest.raises(HandwritingParams.ValidationError):
        params.validate(require_text=True)


def test_engine_renders_single_blank_background(tmp_path) -> None:
    """只有单张背景时预览输出一页空白背景。"""
    bg = tmp_path / "bg.png"
    _flat_image((250, 250, 250)).save(bg)
    params = HandwritingParams(background_path=str(bg))
    pages = list(HandwritingEngine(backend="fast").generate(params))
    assert len(pages) == 1
    assert pages[0].size == (400, 300)
    assert not _near_black_mask(pages[0]).any()


def test_engine_renders_all_document_pages_without_text(tmp_path) -> None:
    """导入的多页文档背景在无文字时也应整本输出，便于翻页框选。"""
    params = _bg_params(tmp_path, [(255, 0, 0), (0, 0, 255), (0, 128, 0)])
    pages = list(HandwritingEngine(backend="fast").generate(params))
    assert len(pages) == 3
    arr0 = np.asarray(pages[0].convert("RGB")).astype(np.int16)
    arr2 = np.asarray(pages[2].convert("RGB")).astype(np.int16)
    assert arr0[..., 0].mean() > 180  # 第一页红
    assert arr2[..., 1].mean() > 80   # 第三页绿
