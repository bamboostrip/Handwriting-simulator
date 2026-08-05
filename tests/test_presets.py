"""预设读写测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from handwritesim.core import presets
from handwritesim.core.models import HandwritingParams


def test_json_roundtrip(tmp_path: Path) -> None:
    params = HandwritingParams(font_size=42, word_spacing=7, red=10, font_path="a.ttf")
    path = tmp_path / "p.json"
    presets.save(path, params)
    loaded = presets.load(path)
    assert loaded.font_size == 42
    assert loaded.word_spacing == 7
    assert loaded.red == 10
    assert loaded.font_path == "a.ttf"


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