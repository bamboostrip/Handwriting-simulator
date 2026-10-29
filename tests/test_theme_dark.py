"""深色与浅色系统主题适配及渲染测试。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from handwritesim.gui.main_window import MainWindow
from handwritesim.gui.region_dialog import RegionDialog
from handwritesim.gui.ui import _DARK_QSS, _LIGHT_QSS, apply_theme, get_theme_qss, is_dark_mode


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_theme_qss_content() -> None:
    """验证深色与浅色 QSS 均包含关键控件样式定义与正确的配色。"""
    assert "#181c19" in _DARK_QSS
    assert "#f4f7f4" in _LIGHT_QSS
    assert 'QLabel[hint="true"]' in _DARK_QSS
    assert 'QLabel[hint="true"]' in _LIGHT_QSS
    assert 'QPushButton[collapsible="true"]' in _DARK_QSS
    assert 'QPushButton[collapsible="true"]' in _LIGHT_QSS


def test_get_theme_qss_explicit() -> None:
    """明确传参时应返回对应 QSS。"""
    assert get_theme_qss(dark=True) == _DARK_QSS
    assert get_theme_qss(dark=False) == _LIGHT_QSS


def test_apply_theme_and_render_dark(app) -> None:
    """在深色调色板下应用深色主题，主窗口与弹窗均能正常渲染。"""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#1e1e1e"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#121212"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#ffffff"))
    app.setPalette(palette)

    apply_theme(app, dark=True)
    assert app.styleSheet() == _DARK_QSS

    win = MainWindow()
    win.resize(1000, 700)
    win.show()
    pix = win.grab()
    assert not pix.isNull()
    assert pix.width() > 0 and pix.height() > 0
    win.close()

    dlg = RegionDialog()
    dlg.resize(600, 580)
    dlg.show()
    dlg_pix = dlg.grab()
    assert not dlg_pix.isNull()
    assert dlg_pix.width() > 0 and dlg_pix.height() > 0
    dlg.close()


def test_apply_theme_and_render_light(app) -> None:
    """在浅色调色板下应用浅色主题，主窗口与弹窗均能正常渲染。"""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f0f0f0"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#000000"))
    app.setPalette(palette)

    apply_theme(app, dark=False)
    assert app.styleSheet() == _LIGHT_QSS

    win = MainWindow()
    win.resize(1000, 700)
    win.show()
    pix = win.grab()
    assert not pix.isNull()
    win.close()


def test_theme_preference_overrides_system(app, tmp_path, monkeypatch) -> None:
    """主题三态偏好：持久化、循环顺序，且手动偏好优先于系统判定。"""
    from PyQt6.QtCore import QSettings

    import handwritesim.gui.ui as ui_module
    from handwritesim.gui.ui import (
        cycle_theme_preference,
        effective_dark,
        get_theme_preference,
        is_dark_mode,
        set_theme_preference,
        theme_preference_label,
    )

    # 偏好 QSettings 指向临时 INI，避免污染真实注册表
    ini = tmp_path / "theme.ini"
    monkeypatch.setattr(
        ui_module,
        "_panel_settings",
        lambda: QSettings(str(ini), QSettings.Format.IniFormat),
    )

    # 系统浅色调色板
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f0f0f0"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#000000"))
    app.setPalette(palette)

    assert get_theme_preference() == "auto"
    assert effective_dark() == is_dark_mode()  # auto = 跟随系统
    assert theme_preference_label() == "跟随系统"
    system_dark = is_dark_mode()  # 系统判定基准值（后续只改偏好、不再改调色板）

    # 三态循环：跟随系统 → 浅色 → 深色 → 跟随系统
    assert cycle_theme_preference() == "light"
    assert get_theme_preference() == "light"
    assert effective_dark() is False
    assert theme_preference_label() == "浅色"

    assert cycle_theme_preference() == "dark"
    assert effective_dark() is True  # 手动深色优先于系统浅色
    apply_theme(app)                 # 无参：按偏好套用 QSS
    assert app.styleSheet() == _DARK_QSS
    assert theme_preference_label() == "深色"

    assert cycle_theme_preference() == "auto"
    assert effective_dark() == is_dark_mode()
    assert theme_preference_label() == "跟随系统"

    # 持久化（set_theme_preference 内已 sync），非法值兜底为 auto
    stored = QSettings(str(ini), QSettings.Format.IniFormat).value("theme/preference")
    assert stored == "auto"
    assert set_theme_preference("未知偏好") == "auto"
    assert get_theme_preference() == "auto"

    # is_dark_mode() 保持系统判定语义：手动偏好不影响它
    set_theme_preference("dark")
    assert is_dark_mode() is system_dark
    assert effective_dark() is True

    # 还原：跟随系统 + 浅色 QSS，避免影响后续用例
    set_theme_preference("auto")
    apply_theme(app, dark=False)
