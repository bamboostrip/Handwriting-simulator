"""预设读写测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from handwritesim.core import presets
from handwritesim.core.models import HandwritingParams, Paragraph, parse_color


def test_json_roundtrip(tmp_path: Path) -> None:
    params = HandwritingParams(font_size=42, word_spacing=7, red=10, font_path="a.ttf")
    path = tmp_path / "p.json"
    presets.save(path, params)
    loaded = presets.load(path)
    assert loaded.font_size == 42
    assert loaded.word_spacing == 7
    assert loaded.red == 10
    assert loaded.font_path == "a.ttf"


def test_json_saves_hex_color(tmp_path: Path) -> None:
    """新格式预设保存 #RRGGBB 颜色值，不再保存 red/green/blue 数字。"""
    params = HandwritingParams(red=255, green=128, blue=0, font_path="a.ttf")
    path = tmp_path / "p.json"
    presets.save(path, params)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["params"]["color"] == "#ff8000"
    assert "red" not in raw["params"]
    assert "green" not in raw["params"]
    assert "blue" not in raw["params"]
    loaded = presets.load(path)
    assert (loaded.red, loaded.green, loaded.blue) == (255, 128, 0)


def test_preset_excludes_text_and_paragraphs(tmp_path: Path) -> None:
    """预设只保存排版参数，text/paragraphs 不属于预设范围。"""
    params = HandwritingParams(
        text="正文内容",
        paragraphs=[Paragraph(text="段落", align="center")],
        font_path="a.ttf",
    )
    path = tmp_path / "p.json"
    presets.save(path, params)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "text" not in raw["params"]
    assert "paragraphs" not in raw["params"]
    loaded = presets.load(path)
    assert loaded.text == ""
    assert loaded.paragraphs is None


def test_load_legacy_rgb_json(tmp_path: Path) -> None:
    """旧版预设（red/green/blue 数字）仍可正常载入。"""
    path = tmp_path / "old.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "params": {
                    "red": 10,
                    "green": 20,
                    "blue": 30,
                    "font_path": "a.ttf",
                },
            }
        ),
        encoding="utf-8",
    )
    loaded = presets.load(path)
    assert (loaded.red, loaded.green, loaded.blue) == (10, 20, 30)


def test_parse_color() -> None:
    assert parse_color("#ff8000") == (255, 128, 0)
    assert parse_color("FF8000") == (255, 128, 0)
    assert parse_color("  #4ca6a6  ") == (76, 166, 166)
    with pytest.raises(ValueError):
        parse_color("#ff80")
    with pytest.raises(ValueError):
        parse_color("#gg8000")
    with pytest.raises(ValueError):
        parse_color("")


def test_color_property() -> None:
    params = HandwritingParams(red=255, green=128, blue=0)
    assert params.color == "#ff8000"
    params.color = "#00ff00"
    assert (params.red, params.green, params.blue) == (0, 255, 0)


def test_legacy_roundtrip(tmp_path: Path) -> None:
    params = HandwritingParams(font_size=42, word_spacing=7, perturb_theta_sigma=0.05)
    path = tmp_path / "p.preset"
    presets.save(path, params)
    loaded = presets.load(path)
    assert loaded.font_size == 42
    assert loaded.word_spacing == 7
    assert abs(loaded.perturb_theta_sigma - 0.05) < 1e-9


def test_to_from_lines() -> None:
    params = HandwritingParams(font_size=42, perturb_theta_sigma=0.05, font_path="a.ttf")
    rebuilt = HandwritingParams.from_lines(params.to_lines())
    assert rebuilt.font_size == 42
    assert abs(rebuilt.perturb_theta_sigma - 0.05) < 1e-9
    assert rebuilt.font_path == "a.ttf"


def test_from_lines_rejects_short() -> None:
    with pytest.raises(HandwritingParams.ValidationError):
        HandwritingParams.from_lines(["1", "2"])