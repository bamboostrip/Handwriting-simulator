"""命令行接口（CLI）单元测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image

from handwritesim import cli
from handwritesim.core import presets
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


def _make_dummy_preset(tmp_path: Path, font_path: str) -> Path:
    bg = tmp_path / "bg.png"
    Image.new("RGB", (400, 300), "white").save(bg)
    params = HandwritingParams(
        font_path=font_path,
        background_path=str(bg),
        font_size=42,
        line_spacing=50,
        word_spacing=8,
        left_margin=25,
    )
    preset_path = tmp_path / "test_preset.json"
    presets.save_json(params, preset_path)
    return preset_path


def test_cli_preset_without_font(tmp_path: Path) -> None:
    """已有 --preset 时无需提供 --font，文档示例 handwrite-cli --preset ... '文本' 应正常执行。"""
    font = _font()
    preset_file = _make_dummy_preset(tmp_path, font)
    out_dir = tmp_path / "out_preset"

    ret = cli.main(["--preset", str(preset_file), "测试预设文本", "--out", str(out_dir)])
    assert ret == 0
    generated = list(out_dir.glob("*.png"))
    assert len(generated) >= 1


def test_cli_preset_with_font_override(tmp_path: Path) -> None:
    """指定 --preset 的同时传入 --font，应以命令行显式指定的字体为准。"""
    font = _font()
    preset_file = _make_dummy_preset(tmp_path, font)
    out_dir = tmp_path / "out_override"

    ret = cli.main([
        "--preset", str(preset_file),
        "--font", font,
        "--font-size", "50",
        "测试覆盖字体与字号",
        "--out", str(out_dir),
    ])
    assert ret == 0
    assert len(list(out_dir.glob("*.png"))) >= 1


def test_cli_no_preset_with_font(tmp_path: Path) -> None:
    """不使用预设时，显式指定 --font 和文本即可生成。"""
    font = _font()
    out_dir = tmp_path / "out_direct"

    ret = cli.main(["--font", font, "直接生成文本", "--out", str(out_dir)])
    assert ret == 0
    assert len(list(out_dir.glob("*.png"))) >= 1


def test_cli_missing_font_fails(tmp_path: Path) -> None:
    """既无预设也未提供 --font 时应返回错误码 2。"""
    out_dir = tmp_path / "out_fail"
    ret = cli.main(["没有字体的文本", "--out", str(out_dir)])
    assert ret == 2


def test_cli_missing_text_fails(tmp_path: Path) -> None:
    """未提供文本时返回错误码 2。"""
    font = _font()
    ret = cli.main(["--font", font])
    assert ret == 2


def test_cli_preview_only(tmp_path: Path) -> None:
    """--preview-only 生成 preview.png。"""
    font = _font()
    preset_file = _make_dummy_preset(tmp_path, font)
    out_dir = tmp_path / "out_preview"

    ret = cli.main(["--preset", str(preset_file), "--preview-only", "预览测试", "--out", str(out_dir)])
    assert ret == 0
    assert (out_dir / "preview.png").is_file()


def test_cli_pdf_export(tmp_path: Path) -> None:
    """--pdf 导出 PDF 文件。"""
    font = _font()
    preset_file = _make_dummy_preset(tmp_path, font)
    pdf_out = tmp_path / "out.pdf"

    ret = cli.main(["--preset", str(preset_file), "--pdf", str(pdf_out), "PDF 导出测试"])
    assert ret == 0
    assert pdf_out.is_file()
