"""使用指南对话框与「关于」入口测试（依赖 Qt 会话）。"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QPushButton

from handwritesim.gui.about_dialog import AboutDialog
from handwritesim.gui.guide_dialog import GuideDialog, _SECTIONS


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_guide_sections_cover_basics_and_advanced(qapp) -> None:
    """章节覆盖快速上手、多篇进阶与错字模拟。"""
    titles = [title for title, _html in _SECTIONS]
    assert any("快速上手" in t for t in titles)
    assert sum("进阶" in t for t in titles) >= 3
    assert any("错字" in t for t in titles)
    assert any("小技巧" in t for t in titles)


def test_guide_dialog_defaults_to_first_section(qapp) -> None:
    """打开即显示第一章，切换章节内容随之变化。"""
    dlg = GuideDialog()
    assert dlg.list_sections.count() == len(_SECTIONS)
    assert dlg.list_sections.currentRow() == 0
    first_html = dlg.text_page.toHtml()
    dlg.list_sections.setCurrentRow(1)
    assert dlg.text_page.toHtml() != first_html
    # 越界行号不崩溃
    dlg._show_section(-1)
    dlg._show_section(len(_SECTIONS) + 10)


def test_guide_content_mentions_key_features(qapp) -> None:
    """文案提及核心功能关键词（与实际 GUI 控件对齐）。"""
    joined = "".join(html for _title, html in _SECTIONS)
    for keyword in (
        "导出 PDF",
        "首行缩进",
        "全部作为手写",
        "打印 / 手写混排",
        "提取填空框",
        "保留完整底图",
        "错字率",
        "笔迹角色",
    ):
        assert keyword in joined, f"使用指南缺少关键词：{keyword}"


def test_about_dialog_has_guide_entry(qapp, monkeypatch) -> None:
    """「关于」对话框含使用指南按钮，点击可打开 GuideDialog。"""
    opened: list[GuideDialog | None] = []

    class _DummyGuide:
        def __init__(self, parent=None) -> None:
            opened.append(parent)

        def exec(self) -> int:
            return 0

    monkeypatch.setattr("handwritesim.gui.about_dialog.GuideDialog", _DummyGuide)
    dlg = AboutDialog()
    btn = dlg.findChild(QPushButton, "btn_guide")
    assert btn is not None
    btn.click()
    assert opened and opened[0] is dlg
