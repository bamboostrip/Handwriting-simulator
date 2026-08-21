"""框选文字区域（手写/打印混排）引擎测试。"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from handwritesim.core.engine import HandwritingEngine
from handwritesim.core.models import HandwritingParams, TextRegion

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


def _params(tmp_path: Path, text: str = "") -> HandwritingParams:
    bg = tmp_path / "bg.png"
    Image.new("RGB", (400, 300), "white").save(bg)
    return HandwritingParams(
        text=text,
        font_path=_font(),
        background_path=str(bg),
        font_size=30,
        line_spacing=40,
        word_spacing=5,
    )


def _ink_mask(image: Image.Image) -> np.ndarray:
    """白底黑字的墨迹掩码。"""
    return np.asarray(image.convert("L")) < 128


def test_printed_region_ink_near_box(tmp_path: Path) -> None:
    """打印体区域（零扰动）的墨迹应落在框选矩形附近（允许标点悬挂等溢出）。"""
    params = _params(tmp_path)
    box = (60, 50, 200, 120)  # x, y, w, h
    params.regions = [
        TextRegion(x=box[0], y=box[1], w=box[2], h=box[3],
                   text="打印体测试文字", printed=True)
    ]
    image = HandwritingEngine(backend="fast", seed=7).render_preview(params)
    ink = _ink_mask(image)
    assert ink.any()
    ys, xs = np.nonzero(ink)
    slack = params.font_size * 2
    assert xs.min() >= box[0] - slack
    assert ys.min() >= box[1] - slack
    assert xs.max() <= box[0] + box[2] + slack
    assert ys.max() <= box[1] + box[3] + slack
    # 墨迹应与矩形相交（而不是整体飘到区域外）
    inner = ink[box[1]:box[1] + box[3], box[0]:box[0] + box[2]]
    assert inner.any()


def test_handwritten_region_renders(tmp_path: Path) -> None:
    """手写体区域应正常渲染出前景。"""
    params = _params(tmp_path)
    params.regions = [
        TextRegion(x=40, y=40, w=300, h=200, text="手写体区域内容")
    ]
    image = HandwritingEngine(backend="fast").render_preview(params)
    assert _ink_mask(image).any()


def test_region_and_main_text_coexist(tmp_path: Path) -> None:
    """主文字与框选区域应同时渲染：区域内、区域外都有墨迹。"""
    params = _params(tmp_path, "这是主文字，铺满页面边距区域。" * 10)
    box = (150, 100, 160, 90)
    params.regions = [
        TextRegion(x=box[0], y=box[1], w=box[2], h=box[3],
                   text="区域文字", printed=True)
    ]
    image = HandwritingEngine(backend="fast").render_preview(params)
    ink = _ink_mask(image)
    assert ink.any()
    inner = ink[box[1]:box[1] + box[3], box[0]:box[0] + box[2]]
    assert inner.any()
    # 区域外左上角应有主文字墨迹（首行基线在 top_margin + line_spacing ≈ 70）
    assert ink[71:100, 31:140].any()


def test_region_multi_page(tmp_path: Path) -> None:
    """区域文字超出矩形时应流式延续到下一页的同一矩形。"""
    params = _params(tmp_path)
    box = (50, 40, 180, 80)
    params.regions = [
        TextRegion(x=box[0], y=box[1], w=box[2], h=box[3],
                   text="很长的一段区域文字。" * 30)
    ]
    pages = list(HandwritingEngine(backend="fast").generate(params))
    assert len(pages) >= 2
    for page in pages:
        ink = _ink_mask(page)
        assert ink.any()
        inner = ink[box[1]:box[1] + box[3], box[0]:box[0] + box[2]]
        assert inner.any()


def test_region_same_seed_preview_matches_export(tmp_path: Path) -> None:
    """相同 seed 下，区域渲染的预览与导出应逐像素一致。"""
    params = _params(tmp_path, "主文字内容。" * 20)
    params.regions = [
        TextRegion(x=30, y=30, w=200, h=100, text="区域一"),
        TextRegion(x=240, y=150, w=130, h=110, text="区域二",
                   printed=True, font_size=24),
    ]
    preview_pages = list(HandwritingEngine(backend="fast", seed=99).generate(params))
    out = tmp_path / "export"
    files = HandwritingEngine(backend="fast", seed=99).save_all(params, out)
    export_pages = [Image.open(f) for f in files]
    assert len(export_pages) == len(preview_pages)
    for a, b in zip(preview_pages, export_pages):
        assert np.array_equal(np.asarray(a.convert("L")), np.asarray(b.convert("L")))


def test_region_only_passes_validation(tmp_path: Path) -> None:
    """只有框选区域、没有主文字时也应通过校验。"""
    params = _params(tmp_path)
    params.regions = [TextRegion(x=10, y=10, w=100, h=60, text="仅区域")]
    params.validate(require_text=True)  # 不抛异常即通过


def test_region_missing_font_fails_validation(tmp_path: Path) -> None:
    """区域独立字体文件不存在时应报校验错误。"""
    params = _params(tmp_path)
    params.regions = [
        TextRegion(x=10, y=10, w=100, h=60, text="字",
                   font_path=str(tmp_path / "nope.ttf"))
    ]
    with pytest.raises(HandwritingParams.ValidationError):
        params.validate(require_text=True)


def test_region_bad_rect_fails_validation(tmp_path: Path) -> None:
    """宽高非正的区域应报校验错误。"""
    params = _params(tmp_path)
    params.regions = [TextRegion(x=10, y=10, w=0, h=60, text="字")]
    with pytest.raises(HandwritingParams.ValidationError):
        params.validate(require_text=True)


def test_region_empty_text_skipped(tmp_path: Path) -> None:
    """空白文字的区域应被跳过，不参与渲染与页数计算。"""
    params = _params(tmp_path)
    params.regions = [
        TextRegion(x=10, y=10, w=100, h=60, text="   "),
        TextRegion(x=10, y=100, w=100, h=100, text="有效区域"),
    ]
    image = HandwritingEngine(backend="fast").render_preview(params)
    assert _ink_mask(image).any()


def test_region_clamped_to_page(tmp_path: Path) -> None:
    """超出背景边界的区域应被钳制，不崩溃也不产生越界索引。"""
    params = _params(tmp_path)
    params.regions = [
        TextRegion(x=350, y=250, w=200, h=150, text="越界区域"),
        TextRegion(x=0, y=0, w=5000, h=5000, text="超大区域"),
    ]
    image = HandwritingEngine(backend="fast").render_preview(params)
    assert image.size == (400, 300)
