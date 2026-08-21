"""PreviewLabel 坐标换算回归测试（离屏运行，兼容系统 DPI 缩放）。

回归背景：坐标换算曾按「源位图原始像素 / 控件逻辑尺寸」计算，
在系统显示缩放（devicePixelRatio ≠ 1）下会把框选区域放大错位；
现以实际显示位图的逻辑几何为基准，任意 DPR 下换算一致。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QMouseEvent, QPixmap
from PyQt6.QtWidgets import QApplication

from handwritesim.gui.ui import PreviewLabel


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _make_label(
    app,
    src: tuple[int, int] = (400, 300),
    label_size: tuple[int, int] = (200, 150),
    dpr: float = 1.0,
) -> PreviewLabel:
    label = PreviewLabel()
    label.resize(*label_size)
    pm = QPixmap(src[0], src[1])
    pm.fill(Qt.GlobalColor.white)
    if dpr != 1.0:
        pm.setDevicePixelRatio(dpr)
    label.setPixmap(pm)
    return label


def _mouse_event(event_type: QEvent.Type, pos: QPointF) -> QMouseEvent:
    return QMouseEvent(
        event_type,
        pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _xy(point) -> tuple[int, int]:
    return point.x(), point.y()


def _drag(label: PreviewLabel, p0: tuple[int, int], p1: tuple[int, int]) -> list[QRect]:
    received: list[QRect] = []
    label.region_selected.connect(received.append)
    # 必须先显示：隐藏状态下子控件 isVisible() 恒为 False，
    # mouseReleaseEvent 会因橡皮带不可见而跳过
    label.show()
    label.set_region_mode(True)
    label.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, QPointF(*p0)))
    label.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, QPointF(*p1)))
    label.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, QPointF(*p1)))
    label.set_region_mode(False)
    return received


def test_map_no_letterbox(app) -> None:
    """控件与图像同比例（4:3）时无留白，四角与中心换算正确。"""
    label = _make_label(app, src=(400, 300), label_size=(200, 150))
    assert _xy(label._map_to_source(QPoint(0, 0))) == (0, 0)
    assert _xy(label._map_to_source(QPoint(200, 150))) == (399, 299)  # 钳制到图内
    assert _xy(label._map_to_source(QPoint(100, 75))) == (200, 150)


def test_map_letterbox(app) -> None:
    """控件更高时左右铺满、上下居中留白，换算应扣除偏移。"""
    label = _make_label(app, src=(400, 300), label_size=(200, 200))
    # 显示区为 200×150，垂直偏移 25；x=100 始终映射到源图 x=200
    assert _xy(label._map_to_source(QPoint(100, 25))) == (200, 0)
    assert _xy(label._map_to_source(QPoint(100, 175))) == (200, 299)
    assert _xy(label._map_to_source(QPoint(100, 100))) == (200, 150)


def test_map_matches_display_geometry(app) -> None:
    """显示区四角必须映射到源图四角、显示区中心映射到源图中心。

    以 _display_geometry 给出的实际显示几何为准验证换算，
    不依赖 Qt 对带 devicePixelRatio 位图的缩放实现细节；
    角点取整允许 ±3 源像素容差（半像素圆整放大后 ≤3px）。
    """
    for dpr in (1.0, 2.0):
        label = _make_label(app, dpr=dpr)
        geo = label._display_geometry()
        assert geo is not None
        dw, dh, ox, oy = geo

        def close(p, expected):
            x, y = _xy(p)
            return abs(x - expected[0]) <= 3 and abs(y - expected[1]) <= 3

        assert close(label._map_to_source(QPoint(round(ox), round(oy))), (0, 0))
        assert close(
            label._map_to_source(QPoint(round(ox + dw), round(oy + dh))), (399, 299)
        )
        center = QPoint(round(ox + dw / 2), round(oy + dh / 2))
        assert close(label._map_to_source(center), (200, 150))


def test_drag_emits_expected_rect(app) -> None:
    """模拟拖拽框选：发出的矩形应为预览图坐标（含钳制）。

    QRect 按两角点构造时宽高含端点，故为 201×151 而非 200×150。
    """
    label = _make_label(app, src=(400, 300), label_size=(200, 150))
    received = _drag(label, (20, 30), (120, 105))
    assert len(received) == 1
    rect = received[0]
    assert (rect.x(), rect.y(), rect.width(), rect.height()) == (40, 60, 201, 151)


def test_drag_tiny_selection_filtered(app) -> None:
    """误触的极小选区不应发出信号。"""
    label = _make_label(app)
    assert _drag(label, (10, 10), (12, 12)) == []


def test_highlight_does_not_mutate_source(app) -> None:
    """叠加高亮只影响显示，不改动源位图。"""
    label = _make_label(app)
    source = label.pixmap()
    label.set_region_rects([QRect(10, 10, 100, 60)])
    assert label.pixmap() is source
    label.set_region_rects([])
    assert label.pixmap() is source
