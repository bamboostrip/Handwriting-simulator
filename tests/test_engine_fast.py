"""高性能引擎（FastEngine）专项测试。"""

from __future__ import annotations

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


def test_multi_page(tmp_path: Path) -> None:
    # 长文本应生成多页
    params = _params(tmp_path, "多页测试文本。" * 200)
    pages = list(HandwritingEngine(backend="fast").generate(params))
    assert len(pages) >= 2
    for page in pages:
        assert page.size == (400, 300)


def test_page_count_matches_handright(tmp_path: Path) -> None:
    params = _params(tmp_path, "多页测试文本。" * 50)
    fast_pages = list(HandwritingEngine(backend="fast").generate(params))
    handright_pages = list(HandwritingEngine(backend="handright").generate(params))
    assert len(fast_pages) == len(handright_pages)


def test_output_has_foreground(tmp_path: Path) -> None:
    params = _params(tmp_path, "测试")
    image = HandwritingEngine(backend="fast").render_preview(params)
    # 背景为白、字体为黑，应存在非白像素
    gray = np.asarray(image.convert("L"))
    assert gray.min() < 128


def test_save_all_multi(tmp_path: Path) -> None:
    params = _params(tmp_path, "多页测试。" * 200)
    out = tmp_path / "out"
    files = HandwritingEngine(backend="fast").save_all(params, out)
    assert len(files) >= 2
    assert all(Path(f).exists() for f in files)


def test_perturb_rotation_around_own_center() -> None:
    """旋转扰动应绕笔画自身包围盒中心，质心位移应在笔画尺寸量级。

    回归：曾将包围盒中心的 x/y 写反，导致笔画绕对角镜像点旋转，
    产生数十像素的整体错位。
    """
    from scipy import ndimage

    from handwritesim.core.engine_fast import _perturb_mask

    params = HandwritingParams(
        perturb_x_sigma=0, perturb_y_sigma=0, perturb_theta_sigma=0.05
    )
    mask = np.zeros((300, 400), dtype=bool)
    mask[240:260, 40:60] = True  # 远离对角线的两个笔画
    mask[40:60, 340:360] = True
    background = np.full((300, 400, 3), 255, dtype=np.uint8)

    canvas = _perturb_mask(mask, params, np.random.default_rng(42), background)
    fill = np.array(params.fill, dtype=np.uint8)
    out_mask = np.all(canvas == fill, axis=-1)

    labels_in, n_in = ndimage.label(mask)
    labels_out, n_out = ndimage.label(out_mask)
    assert n_out == n_in == 2

    cin = sorted(ndimage.center_of_mass(mask, labels_in, range(1, n_in + 1)))
    cout = sorted(ndimage.center_of_mass(out_mask, labels_out, range(1, n_out + 1)))
    for (iy, ix), (oy, ox) in zip(cin, cout):
        assert np.hypot(oy - iy, ox - ix) < 3


def _para_params(tmp_path: Path, paragraphs) -> HandwritingParams:
    params = _params(tmp_path, "占位")
    params.paragraphs = paragraphs
    return params


def test_paragraph_center_and_indent(tmp_path: Path) -> None:
    params = _para_params(tmp_path, [
        Paragraph("标题", align="center"),
        Paragraph("正文第一段", first_line_indent=60),
    ])
    image = HandwritingEngine(backend="fast").render_preview(params)
    assert image.size == (400, 300)
    gray = np.asarray(image.convert("L"))
    assert gray.min() < 128  # 有前景


def test_paragraph_multi_page(tmp_path: Path) -> None:
    params = _para_params(tmp_path, [
        Paragraph("标题", align="center"),
        Paragraph("很长的一段正文。" * 80),
    ])
    pages = list(HandwritingEngine(backend="fast").generate(params))
    assert len(pages) >= 2
    for page in pages:
        assert page.size == (400, 300)


def test_paragraph_center_is_centered(tmp_path: Path) -> None:
    """居中段落的每行内容应大致水平居中。"""
    params = _para_params(tmp_path, [Paragraph("标题居中", align="center")])
    image = HandwritingEngine(backend="fast").render_preview(params)
    gray = np.asarray(image.convert("L"))
    mask = gray < 128
    cols = np.where(mask.any(axis=0))[0]
    assert cols.size > 0
    center = (cols.min() + cols.max()) / 2.0
    assert abs(center - 200) < 20


def test_paragraph_float_margins_preview(tmp_path: Path) -> None:
    """预览降采样后边距为浮点，段落渲染不应报索引错误。

    回归：GUI 预览降采样把 top_margin 缩放为 float，_paragraph_pages
    用浮点 used 做 numpy 索引导致 IndexError。
    """
    params = _params(tmp_path, "占位")
    params.paragraphs = [Paragraph("标题", align="center"), Paragraph("正文内容。")]
    params.top_margin = 25.0
    params.bottom_margin = 25.0
    params.left_margin = 25.0
    params.right_margin = 25.0
    params.font_size = 30.0
    params.line_spacing = 40.0
    image = HandwritingEngine(backend="fast").render_preview(params)
    assert image.size == (400, 300)
    gray = np.asarray(image.convert("L"))
    assert gray.min() < 128