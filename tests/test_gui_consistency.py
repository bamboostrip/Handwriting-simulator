"""GUI 预览/导出一致性回归测试（依赖 Qt 会话）。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication

from handwritesim.gui.main_window import MainWindow

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


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture()
def window(qapp, tmp_path: Path) -> MainWindow:
    win = MainWindow(out_dir=tmp_path / "out")
    win._ui.lineEdit.setText(_font())
    bg = tmp_path / "bg.png"
    Image.new("RGB", (600, 400), "white").save(bg)
    win._ui.lineEdit_2.setText(str(bg))
    win._ui.textEdit.setPlainText("预览导出一致性测试。")
    return win


class _BusyWorker:
    """模拟正在运行的 RenderWorker。"""

    def isRunning(self) -> bool:
        return True


def test_export_uses_last_preview_snapshot(window, monkeypatch) -> None:
    """导出应复用最后一次预览的参数与种子，而非重新收集界面参数。

    回归：竞态下（预览渲染期间用户又输入），自动预览被跳过时屏幕仍是
    旧内容，但旧实现更新了 seed 且导出重新收集参数，导致导出与预览
    不一致（长文本时第二页差一个字符）。
    """
    old_params = window.collect_params()
    window._preview_seed = 777
    window._preview_params = old_params

    # 用户又输入了内容（预览被跳过，屏幕仍显示旧内容）
    window._ui.textEdit.setPlainText("预览导出一致性测试。补充文字。")

    captured = {}
    monkeypatch.setattr(
        window,
        "_start_worker",
        lambda params, mode, quiet=False, seed=None: captured.update(
            params=params, seed=seed
        )
        or True,
    )
    window._on_export()

    assert captured["seed"] == 777
    assert captured["params"] is old_params  # 预览时的参数快照


def test_skipped_auto_preview_keeps_snapshot(window, monkeypatch) -> None:
    """自动预览因 worker 忙被跳过时，不得更新 seed/参数快照。"""
    old_params = window.collect_params()
    window._preview_seed = 555
    window._preview_params = old_params
    window._ui.textEdit.setPlainText("被跳过的预览文本。")

    monkeypatch.setattr(window, "_worker", _BusyWorker())
    monkeypatch.setattr(window, "_set_busy", lambda busy: None)
    window._on_auto_preview()

    assert window._preview_seed == 555
    assert window._preview_params is old_params


def test_preview_success_updates_snapshot(window, monkeypatch) -> None:
    """预览真正启动后应更新种子与参数快照。"""
    monkeypatch.setattr(window, "_worker", None)
    monkeypatch.setattr(window, "_set_busy", lambda busy: None)

    def fake_start_worker(params, mode, quiet=False, seed=None):
        window._worker = object()
        return True

    monkeypatch.setattr(window, "_start_worker", fake_start_worker)
    window._on_auto_preview()

    assert window._preview_seed is not None
    assert window._preview_params is not None
    assert window._preview_params.text == "预览导出一致性测试。"


def test_export_without_preview_uses_current_params(window, monkeypatch) -> None:
    """从未预览过时，导出应使用当前界面参数且 seed 为 None（保持随机）。"""
    assert window._preview_params is None
    captured = {}
    monkeypatch.setattr(
        window,
        "_start_worker",
        lambda params, mode, quiet=False, seed=None: captured.update(
            params=params, seed=seed
        )
        or True,
    )
    window._on_export()

    assert captured["seed"] is None
    assert captured["params"].text == "预览导出一致性测试。"


def test_preset_combo_wheel_does_not_switch(qapp, tmp_path: Path) -> None:
    """预设下拉框必须点击切换：滚轮滚动不得触发切换（避免误触）。

    预设下拉框位于可滚动面板内，若滚轮直接切换预设，用户滚动参数面板时
    会误触发配置变更，故需忽略滚轮事件（与 NoWheelSpinBox 同策略）。
    """
    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QWheelEvent

    from handwritesim.gui.ui import NoWheelComboBox

    win = MainWindow(out_dir=tmp_path / "out")
    combo = win._ui.combo_preset
    assert isinstance(combo, NoWheelComboBox)

    # 构造向下滚动事件（angleDelta.y > 0）：默认行为会切换选项
    event = QWheelEvent(
        QPointF(0, 0),
        QPointF(0, 0),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    combo.setCurrentIndex(0)
    combo.wheelEvent(event)
    assert combo.currentIndex() == 0  # 滚轮不得改变当前项


def test_region_dialog_features(qapp) -> None:
    """测试 RegionDialog 的参数回填、段落工具栏与高级覆盖面板。"""
    from handwritesim.core.models import Paragraph
    from handwritesim.gui.region_dialog import RegionDialog

    dlg = RegionDialog(
        title="编辑文字区域",
        text="段落一\n段落二",
        paragraphs=[
            Paragraph(text="段落一", align="center", first_line_indent=40),
            Paragraph(text="段落二", align="right", first_line_indent=0),
        ],
        printed=True,
        font_path="dummy.ttf",
        font_size=28,
        page=2,
        word_spacing=10,
        line_spacing=50,
        perturb_theta_sigma=0.08,
        miswrite_rate=0.15,
        miswrite_strikeout_style="slash",
        color="#ff0000",
        margin_top=12,
        margin_left=15,
    )

    assert dlg.region_printed is True
    assert dlg.region_font_size == 28
    assert dlg.region_page == 2
    assert dlg.region_word_spacing == 10
    assert dlg.region_line_spacing == 50
    assert dlg.region_perturb_theta_sigma == 0.08
    assert dlg.region_miswrite_rate == 0.15
    assert dlg.region_miswrite_strikeout_style == "slash"
    assert dlg.region_color == "#ff0000"
    assert dlg.region_margin_top == 12
    assert dlg.region_margin_left == 15

    paras = dlg.region_paragraphs
    assert len(paras) == 2
    assert paras[0].text == "段落一"
    assert paras[0].align == "center"
    assert paras[1].text == "段落二"
    assert paras[1].align == "right"

