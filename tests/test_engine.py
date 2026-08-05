"""核心引擎测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image

from handwritesim.core.engine import HandwritingEngine
from handwritesim.core.models import HandwritingParams

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


def _params(tmp_path: Path) -> HandwritingParams:
    bg = tmp_path / "bg.png"
    Image.new("RGB", (600, 400), "white").save(bg)
    return HandwritingParams(
        text="测试手写模拟",
        font_path=_font(),
        background_path=str(bg),
        font_size=40,
        line_spacing=55,
        word_spacing=6,
    )


def test_generate(tmp_path: Path) -> None:
    params = _params(tmp_path)
    images = list(HandwritingEngine().generate(params))
    assert len(images) >= 1
    assert images[0].size == (600, 400)


def test_render_preview(tmp_path: Path) -> None:
    params = _params(tmp_path)
    image = HandwritingEngine().render_preview(params)
    assert image.mode in ("RGB", "RGBA")
    assert image.size == (600, 400)


def test_save_all(tmp_path: Path) -> None:
    params = _params(tmp_path)
    out = tmp_path / "out"
    files = HandwritingEngine().save_all(params, out)
    assert files
    assert all(Path(f).exists() for f in files)


def test_validate_requires_text(tmp_path: Path) -> None:
    params = HandwritingParams(text="", font_path=_font(), background_path=str(tmp_path / "x.png"))
    with pytest.raises(HandwritingParams.ValidationError):
        params.validate()


def test_validate_missing_font(tmp_path: Path) -> None:
    bg = tmp_path / "bg.png"
    Image.new("RGB", (10, 10), "white").save(bg)
    params = HandwritingParams(text="x", font_path=str(tmp_path / "nope.ttf"), background_path=str(bg))
    with pytest.raises(HandwritingParams.ValidationError, match="字体"):
        params.validate()