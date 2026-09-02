"""测试预览降采样不得丢失打印体 Run 的字体/加粗信息。

背景宽度超过 _preview_max_width 时预览会走 _downsample_preview 重建段落，
此前实现重建 TextRun 时只保留 text/role_id/color，导致 docx 导入的
加粗与黑体/宋体/仿宋多字体在预览中全部丢失（导出正常，仅预览异常）。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
from pathlib import Path

import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication

from handwritesim.core.models import (
    HandwritingParams,
    HandwritingRole,
    Paragraph,
    TextRun,
)
from handwritesim.gui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _make_params(bg_path: str, font_size: int = 36) -> HandwritingParams:
    font_path = "C:/Windows/Fonts/simhei.ttf"
    return HandwritingParams(
        font_path=font_path,
        background_path=bg_path,
        font_size=font_size,
        roles=[
            HandwritingRole(id=0, name="默认手写", printed=False),
            HandwritingRole(id=1, name="打印体", printed=True, font_path="C:/Windows/Fonts/simfang.ttf"),
            HandwritingRole(id=2, name="手写角色1", printed=False),
        ],
        paragraphs=[
            Paragraph(
                text="思想汇报",
                align="center",
                runs=[
                    TextRun(
                        text="思想汇报",
                        role_id=1,
                        font_family="黑体",
                        font_file=font_path,
                        font_size=30,
                        bold=True,
                    )
                ],
            ),
            Paragraph(
                text="敬爱的党组织：",
                runs=[
                    TextRun(
                        text="敬爱的党组织：",
                        role_id=1,
                        font_family="仿宋",
                        font_file="C:/Windows/Fonts/simfang.ttf",
                        font_size=15,
                        bold=False,
                    )
                ],
            ),
        ],
    )


def test_downsample_preserves_run_bold_and_fonts(app) -> None:
    win = MainWindow()
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        bg_path = tdp / "wide_bg.png"
        Image.new("RGB", (5000, 7000), (255, 255, 255)).save(bg_path)

        params = _make_params(str(bg_path))
        preview = win._downsample_preview(params)

        # 确认确实触发了降采样
        assert win._preview_scale < 1.0
        assert preview is not params

        scale = win._preview_scale
        assert preview.paragraphs is not None
        title_run = preview.paragraphs[0].runs[0]
        assert title_run.bold is True
        assert title_run.font_family == "黑体"
        assert title_run.font_file == "C:/Windows/Fonts/simhei.ttf"
        assert title_run.font_size == max(1, round(30 * scale))

        body_run = preview.paragraphs[1].runs[0]
        assert body_run.bold is False
        assert body_run.font_family == "仿宋"
        assert body_run.font_file == "C:/Windows/Fonts/simfang.ttf"
        assert body_run.font_size == max(1, round(15 * scale))
        assert body_run.role_id == 1


def test_downsample_noop_keeps_run_bold_and_fonts(app) -> None:
    """背景不超过阈值时不降采样，原参数直接返回（字段天然保留）。"""
    win = MainWindow()
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        bg_path = tdp / "small_bg.png"
        Image.new("RGB", (2000, 2800), (255, 255, 255)).save(bg_path)

        params = _make_params(str(bg_path))
        preview = win._downsample_preview(params)
        assert win._preview_scale == 1.0
        assert preview is params
