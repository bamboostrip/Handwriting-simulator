"""测试 QComboBox 下拉箭头 QSS 与 NoWheelComboBox 的 QListView 设置。"""

from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QListView

from handwritesim.gui.ui import NoWheelComboBox, get_theme_qss


def test_nowheel_combobox_uses_qlistview():
    """测试 NoWheelComboBox 实例默认使用 QListView 保证弹窗规整渲染。"""
    app = QApplication.instance() or QApplication([])
    combo = NoWheelComboBox()
    assert isinstance(combo.view(), QListView)


def test_qss_contains_down_arrow():
    """测试浅色与深色 QSS 均正确配置了 QComboBox::down-arrow。"""
    light_qss = get_theme_qss(dark=False)
    dark_qss = get_theme_qss(dark=True)
    assert "QComboBox::down-arrow" in light_qss
    assert "arrow_down.svg" in light_qss
    assert "QComboBox::down-arrow" in dark_qss
    assert "arrow_down_dark.svg" in dark_qss

