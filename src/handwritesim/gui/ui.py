"""主界面：纯 Qt 控件 + 自动布局（不再依赖背景图片）。

所有界面文字均为真实 QLabel，窗口可自由缩放，控件随布局自适应，
任何尺寸下都保持可读。控件属性名与旧版保持一致，main_window 可无缝复用。
"""

from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets

from .resources import resource_path

_LIGHT_QSS = """
QMainWindow { background: #f4f7f4; }
QWidget { color: #2b3430; font: 10pt "楷体"; }
QWidget#central { background: #f4f7f4; }
QScrollArea { background: transparent; border: none; }
QTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox {
    background: #ffffff;
    border: 1px solid #c9d6cd;
    border-radius: 4px;
    padding: 2px 6px;
    selection-background-color: #9ddc80;
}
QTextEdit:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #79c267;
}
/* 仅扩大数字框上下按钮的点击热区（宽度 20px），其余外观保持系统默认 */
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 20px;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 20px;
}
QPushButton {
    background: #dcf7e6;
    border: 1px solid #b7e4c9;
    border-radius: 4px;
    padding: 4px 12px;
}
QPushButton:hover { background: #c9f0d8; }
QPushButton:pressed { background: #b2e5c4; }
QPushButton[primary="true"] {
    background: #9ddc80;
    border: 1px solid #7fc465;
    font-weight: bold;
}
QPushButton[primary="true"]:hover { background: #8ed271; }
QGroupBox {
    border: 1px solid #d3ded6;
    border-radius: 6px;
    margin-top: 12px;
    padding: 14px 8px 8px 8px;
    font-weight: bold;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
PreviewLabel {
    background: #c8d0ca;
    border: 1px solid #d3ded6;
    border-radius: 6px;
}
QScrollBar:vertical { width: 10px; background: transparent; }
QScrollBar::handle:vertical { background: #c3cec6; border-radius: 5px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


class NoWheelSpinBox(QtWidgets.QSpinBox):
    """禁用鼠标滚轮改值的整数输入框，避免与滚动面板冲突。"""

    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    """禁用鼠标滚轮改值的浮点输入框，避免与滚动面板冲突。"""

    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelComboBox(QtWidgets.QComboBox):
    """禁用鼠标滚轮切换的下拉框，避免滚动面板时误切换选项。"""

    def wheelEvent(self, event) -> None:
        event.ignore()


class PreviewLabel(QtWidgets.QLabel):
    """预览区：保持宽高比缩放，随窗口尺寸自适应；支持框选文字区域。

    框选模式下按住鼠标拖出矩形，松开后发出 region_selected 信号；
    set_region_rects 用于临时高亮（如悬浮区域列表项时），
    坐标均为预览图像素坐标（调用方负责与原始背景坐标互转）。
    坐标换算以实际显示位图的逻辑尺寸为基准，兼容系统 DPI 缩放。
    """

    region_selected = QtCore.pyqtSignal(object)  # QtCore.QRect（预览图坐标）
    region_geometry_changed = QtCore.pyqtSignal(object)  # 调整后的新矩形（预览图坐标）
    region_edit_cancelled = QtCore.pyqtSignal()  # 编辑态被取消（Esc/点击框外）

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._source: QtGui.QPixmap | None = None
        self._disp: QtGui.QPixmap | None = None  # 缩放后实际显示的位图
        self._rects: list[QtCore.QRect] = []     # 临时高亮的矩形（预览图坐标）
        self._region_mode = False
        self._rubber: QtWidgets.QRubberBand | None = None
        self._origin: QtCore.QPoint | None = None      # 新建拖拽起点（控件坐标）
        self._edit_rect: QtCore.QRect | None = None    # 调整中区域（预览图坐标）
        self._adjust: str | None = None                # move / tl / tr / bl / br / l / r / t / b
        self._press_pos: QtCore.QPoint | None = None   # 按下位置（控件坐标）
        self._press_rect: QtCore.QRect | None = None   # 按下时编辑框（控件坐标）
        # 无按键移动也接收 move 事件，用于边缘/四角的光标提示
        self.setMouseTracking(True)
        # 允许点击聚焦以接收 Esc 退出调整
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

    # ---- 区域框选 ----
    def set_region_mode(self, on: bool) -> None:
        """开关框选模式；开启时光标变为十字，并结束进行中的区域调整。"""
        if on and self._edit_rect is not None:
            self._finish_edit(emit=True)
        self._region_mode = on
        if on:
            self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        else:
            self.unsetCursor()
            if self._rubber is not None:
                self._rubber.hide()

    def set_region_rects(self, rects: list[QtCore.QRect]) -> None:
        """设置临时高亮的区域矩形（预览图坐标），立即重绘；空列表清除。"""
        self._rects = list(rects)
        self._rescale()

    # ---- 区域调整（点击列表项进入）----
    def _ensure_rubber(self) -> None:
        if self._rubber is None:
            self._rubber = QtWidgets.QRubberBand(
                QtWidgets.QRubberBand.Shape.Rectangle, self
            )

    def _display_area(self) -> QtCore.QRect:
        """显示位图在控件内的矩形区域（用于把调整限制在图纸范围内）。"""
        geo = self._display_geometry()
        if geo is None:
            return self.rect()
        dw, dh, ox, oy = geo
        return QtCore.QRect(
            round(ox), round(oy), max(1, round(dw)), max(1, round(dh))
        )

    def _rect_to_widget(self, source_rect: QtCore.QRect) -> QtCore.QRect:
        """预览图坐标矩形 -> 控件坐标矩形。"""
        geo = self._display_geometry()
        if geo is None or self._source is None or self._source.isNull():
            return QtCore.QRect(source_rect)
        k = geo[0] / max(1, self._source.width())
        return QtCore.QRect(
            round(geo[2] + source_rect.x() * k),
            round(geo[3] + source_rect.y() * k),
            max(1, round(source_rect.width() * k)),
            max(1, round(source_rect.height() * k)),
        )

    def begin_region_edit(self, source_rect: QtCore.QRect) -> None:
        """进入调整模式：按预览图坐标显示可拖动/缩放的编辑框。"""
        if self._source is None or self._source.isNull():
            return
        self._ensure_rubber()
        self._edit_rect = QtCore.QRect(source_rect)
        self._rubber.setGeometry(self._rect_to_widget(self._edit_rect))
        self._rubber.show()

    def end_region_edit(self) -> None:
        """静默退出调整模式（窗口主动调用，不发取消信号）。"""
        self._finish_edit(emit=False)

    def is_editing(self) -> bool:
        return self._edit_rect is not None

    def _finish_edit(self, emit: bool) -> None:
        had = self._edit_rect is not None
        self._edit_rect = None
        self._adjust = None
        self._press_pos = None
        self._press_rect = None
        # 新建拖拽与编辑共用橡皮带：仅当没有进行中的新建拖拽时才隐藏
        if self._origin is None and self._rubber is not None:
            self._rubber.hide()
        self.unsetCursor()
        if emit and had:
            self.region_edit_cancelled.emit()

    def _hit_zone(self, pos: QtCore.QPoint) -> str:
        """判断控件坐标命中编辑框的部位：角/边/内部/外部。"""
        if self._rubber is None or not self._rubber.isVisible():
            return "outside"
        r = self._rubber.geometry()
        m = 8  # 边缘命中容差（逻辑像素）
        near_l = abs(pos.x() - r.left()) <= m
        near_r = abs(pos.x() - r.right()) <= m
        near_t = abs(pos.y() - r.top()) <= m
        near_b = abs(pos.y() - r.bottom()) <= m
        in_v = r.top() <= pos.y() <= r.bottom()
        in_h = r.left() <= pos.x() <= r.right()
        if near_l and near_t:
            return "tl"
        if near_r and near_t:
            return "tr"
        if near_l and near_b:
            return "bl"
        if near_r and near_b:
            return "br"
        if near_l and in_v:
            return "l"
        if near_r and in_v:
            return "r"
        if near_t and in_h:
            return "t"
        if near_b and in_h:
            return "b"
        if r.contains(pos):
            return "move"
        return "outside"

    _ZONE_CURSORS = {
        "tl": QtCore.Qt.CursorShape.SizeFDiagCursor,
        "br": QtCore.Qt.CursorShape.SizeFDiagCursor,
        "tr": QtCore.Qt.CursorShape.SizeBDiagCursor,
        "bl": QtCore.Qt.CursorShape.SizeBDiagCursor,
        "l": QtCore.Qt.CursorShape.SizeHorCursor,
        "r": QtCore.Qt.CursorShape.SizeHorCursor,
        "t": QtCore.Qt.CursorShape.SizeVerCursor,
        "b": QtCore.Qt.CursorShape.SizeVerCursor,
        "move": QtCore.Qt.CursorShape.SizeAllCursor,
    }

    def _update_cursor(self, zone: str) -> None:
        if zone == "outside":
            if self._region_mode:
                self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
            else:
                self.unsetCursor()
        else:
            self.setCursor(self._ZONE_CURSORS[zone])

    def _begin_adjust(self, zone: str, pos: QtCore.QPoint) -> None:
        self._adjust = zone
        self._press_pos = QtCore.QPoint(pos)
        self._press_rect = QtCore.QRect(self._rubber.geometry())
        self.setCursor(self._ZONE_CURSORS[zone])

    def _apply_adjust(self, pos: QtCore.QPoint) -> None:
        """按当前调整部位更新编辑框几何（限制在显示区内）。"""
        if self._rubber is None or self._press_rect is None or self._press_pos is None:
            return
        area = self._display_area()
        p = QtCore.QPoint(
            max(area.left(), min(pos.x(), area.right())),
            max(area.top(), min(pos.y(), area.bottom())),
        )
        if self._adjust == "move":
            dx = p.x() - self._press_pos.x()
            dy = p.y() - self._press_pos.y()
            r = QtCore.QRect(self._press_rect)
            r.translate(dx, dy)
            r.moveLeft(max(area.left(), min(r.left(), area.right() - r.width() + 1)))
            r.moveTop(max(area.top(), min(r.top(), area.bottom() - r.height() + 1)))
            self._rubber.setGeometry(r)
            return
        l = self._press_rect.left()
        t = self._press_rect.top()
        rr = self._press_rect.right()
        b = self._press_rect.bottom()
        if "l" in self._adjust:
            l = p.x()
        if "r" in self._adjust:
            rr = p.x()
        if "t" in self._adjust:
            t = p.y()
        if "b" in self._adjust:
            b = p.y()
        new = QtCore.QRect(
            QtCore.QPoint(min(l, rr), min(t, b)),
            QtCore.QPoint(max(l, rr), max(t, b)),
        )
        # 过小的增量直接忽略，保持上一几何
        if new.width() >= 4 and new.height() >= 4:
            self._rubber.setGeometry(new)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key.Key_Escape and self._edit_rect is not None:
            self._finish_edit(emit=True)
            event.accept()
            return
        super().keyPressEvent(event)

    def _display_geometry(self) -> tuple[float, float, float, float] | None:
        """显示位图的逻辑几何 (宽, 高, x偏移, y偏移)；尚未就绪返回 None。"""
        if self._disp is None or self._disp.isNull():
            return None
        dpr = self._disp.devicePixelRatio()
        if dpr <= 0:
            dpr = 1.0
        dw = self._disp.width() / dpr
        dh = self._disp.height() / dpr
        return dw, dh, (self.width() - dw) / 2.0, (self.height() - dh) / 2.0

    def _map_to_source(self, pos: QtCore.QPoint) -> QtCore.QPoint:
        """控件坐标 -> 预览图像素坐标（钳制在图内）。

        按实际显示位图的逻辑尺寸线性换算：显示位图与源位图的
        devicePixelRatio 相同，直接用原始像素宽高相除即可，
        系统缩放（DPR ≠ 1）不会引入偏差。
        """
        if self._source is None or self._source.isNull():
            return QtCore.QPoint(0, 0)
        geo = self._display_geometry()
        if geo is None:
            # 尚未完成首次缩放：退化为整控件映射
            lw = max(1, self.width())
            lh = max(1, self.height())
            x = round(pos.x() * self._source.width() / lw)
            y = round(pos.y() * self._source.height() / lh)
        else:
            dw, dh, ox, oy = geo
            x = round((pos.x() - ox) * self._source.width() / dw)
            y = round((pos.y() - oy) * self._source.height() / dh)
        x = max(0, min(x, self._source.width() - 1))
        y = max(0, min(y, self._source.height() - 1))
        return QtCore.QPoint(x, y)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._source is None or self._source.isNull():
            super().mousePressEvent(event)
            return
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        pos = event.position().toPoint()
        # 已有编辑框优先命中：抓取移动/缩放（即使处于新建模式）
        if self._edit_rect is not None:
            zone = self._hit_zone(pos)
            if zone != "outside":
                self._begin_adjust(zone, pos)
                event.accept()
                return
        if self._region_mode:
            # 新建框选拖拽；若处于编辑态先退出
            if self._edit_rect is not None:
                self._finish_edit(emit=True)
            self._origin = QtCore.QPoint(pos)
            self._ensure_rubber()
            self._rubber.setGeometry(QtCore.QRect(self._origin, QtCore.QSize()))
            self._rubber.show()
            event.accept()
            return
        if self._edit_rect is not None:
            # 非新建模式下点击框外：结束调整
            self._finish_edit(emit=True)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        pos = event.position().toPoint()
        if self._adjust is not None:
            self._apply_adjust(pos)
            event.accept()
            return
        if (
            self._region_mode
            and self._origin is not None
            and self._rubber is not None
            and self._rubber.isVisible()
        ):
            self._rubber.setGeometry(
                QtCore.QRect(self._origin, pos).normalized()
            )
            event.accept()
            return
        if self._edit_rect is not None:
            self._update_cursor(self._hit_zone(pos))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._adjust is not None:
            rect = self._rubber.geometry() if self._rubber is not None else None
            press_rect = self._press_rect
            self._adjust = None
            self._press_pos = None
            self._press_rect = None
            self._update_cursor(self._hit_zone(event.position().toPoint()))
            if rect is not None and press_rect is not None and rect != press_rect:
                tl = self._map_to_source(rect.topLeft())
                br = self._map_to_source(rect.bottomRight())
                self._edit_rect = QtCore.QRect(tl, br).normalized()
                self.region_geometry_changed.emit(QtCore.QRect(self._edit_rect))
            event.accept()
            return
        if (
            self._region_mode
            and self._origin is not None
            and self._rubber is not None
            and self._rubber.isVisible()
        ):
            self._rubber.hide()
            rect = self._rubber.geometry()
            self._origin = None
            # 过滤误触：按控件坐标判断拖动距离（与用户感知一致，
            # 源坐标会随背景分辨率放大，不适合作为阈值）。
            # QRect 宽高含端点：移动 2px 的橡皮带宽为 3，阈值取 4
            if rect.width() < 4 or rect.height() < 4:
                return
            tl = self._map_to_source(rect.topLeft())
            br = self._map_to_source(rect.bottomRight())
            source_rect = QtCore.QRect(tl, br).normalized()
            if source_rect.width() >= 4 and source_rect.height() >= 4:
                self.region_selected.emit(source_rect)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def setPixmap(self, pixmap: QtGui.QPixmap | None) -> None:  # type: ignore[override]
        self._source = pixmap
        self._rescale()

    def pixmap(self) -> QtGui.QPixmap | None:  # type: ignore[override]
        return self._source

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self._source is None or self._source.isNull():
            return
        self._disp = self._source.scaled(
            self.size(),
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        pm = self._disp
        if self._rects:
            pm = self._draw_rect_overlays(pm)
        super().setPixmap(pm)
        # 编辑中的调整框跟随新的显示几何重新定位（预览重渲染/窗口缩放后）
        if (
            self._edit_rect is not None
            and self._origin is None
            and self._adjust is None
            and self._rubber is not None
        ):
            self._rubber.setGeometry(self._rect_to_widget(self._edit_rect))
            self._rubber.show()

    def _draw_rect_overlays(self, pm: QtGui.QPixmap) -> QtGui.QPixmap:
        """把高亮矩形画到缩放后的位图上（虚线框 + 浅色填充）。

        位图内部没有留白偏移，源坐标按原始像素宽高等比缩放即可；
        先对 painter 做 scale，再直接用源坐标绘制，兼容 DPR ≠ 1。
        """
        if self._source is None or self._source.isNull():
            return pm
        k = pm.width() / max(1, self._source.width())
        painter = QtGui.QPainter(pm)
        pen = QtGui.QPen(
            QtGui.QColor("#e5484d"), max(1, round(2 / k)), QtCore.Qt.PenStyle.DashLine
        )
        painter.setPen(pen)
        painter.setBrush(QtGui.QColor(229, 72, 77, 28))
        painter.scale(k, k)
        for r in self._rects:
            painter.drawRect(r)
        painter.end()
        return pm


class Ui_Form(object):
    def setupUi(self, Form):
        Form.setObjectName("Form")
        Form.resize(1040, 680)
        Form.setMinimumSize(QtCore.QSize(960, 640))
        icon = QtGui.QIcon()
        icon.addPixmap(QtGui.QPixmap(resource_path("ui", "3d.ico")), QtGui.QIcon.Mode.Normal, QtGui.QIcon.State.Off)
        Form.setWindowIcon(icon)
        Form.setWindowOpacity(0.98)

        central = QtWidgets.QWidget(Form)
        central.setObjectName("central")
        Form.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        # ---- 左侧：预览区 + 翻页导航 ----
        left = QtWidgets.QWidget(central)
        left_col = QtWidgets.QVBoxLayout(left)
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(6)
        self.label_11 = PreviewLabel(left)
        self.label_11.setObjectName("label_11")
        self.label_11.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Ignored
        )
        left_col.addWidget(self.label_11, 1)
        row_nav = QtWidgets.QHBoxLayout()
        self.btn_prev = QtWidgets.QPushButton(left)
        self.btn_prev.setObjectName("btn_prev")
        row_nav.addWidget(self.btn_prev)
        self.label_page = QtWidgets.QLabel(left)
        self.label_page.setObjectName("label_page")
        self.label_page.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        row_nav.addWidget(self.label_page, 1)
        self.btn_next = QtWidgets.QPushButton(left)
        self.btn_next.setObjectName("btn_next")
        row_nav.addWidget(self.btn_next)
        self.btn_preview_bg = QtWidgets.QPushButton(left)
        self.btn_preview_bg.setObjectName("btn_preview_bg")
        row_nav.addWidget(self.btn_preview_bg)
        self.btn_select_region = QtWidgets.QPushButton(left)
        self.btn_select_region.setObjectName("btn_select_region")
        self.btn_select_region.setCheckable(True)
        row_nav.addWidget(self.btn_select_region)
        left_col.addLayout(row_nav)
        root.addWidget(left, 1)

        # ---- 右侧：参数面板（可滚动） ----
        scroll = QtWidgets.QScrollArea(central)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right = QtWidgets.QWidget(central)
        right.setFixedWidth(400)
        right_col = QtWidgets.QVBoxLayout(right)
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(8)
        right_col.addWidget(scroll, 1)
        root.addWidget(right)
        panel = QtWidgets.QWidget(scroll)
        scroll.setWidget(panel)
        v = QtWidgets.QVBoxLayout(panel)
        v.setContentsMargins(2, 2, 6, 2)
        v.setSpacing(8)

        # 待处理文本
        self.label_text = QtWidgets.QLabel(panel)
        v.addWidget(self.label_text)

        # 排版工具按钮
        row_tools = QtWidgets.QHBoxLayout()
        self.btn_align_left = QtWidgets.QPushButton(panel)
        self.btn_align_left.setObjectName("btn_align_left")
        self.btn_center = QtWidgets.QPushButton(panel)
        self.btn_center.setObjectName("btn_center")
        self.btn_align_right = QtWidgets.QPushButton(panel)
        self.btn_align_right.setObjectName("btn_align_right")
        self.btn_indent = QtWidgets.QPushButton(panel)
        self.btn_indent.setObjectName("btn_indent")
        self.btn_import_docx = QtWidgets.QPushButton(panel)
        self.btn_import_docx.setObjectName("btn_import_docx")
        row_tools.addWidget(self.btn_align_left)
        row_tools.addWidget(self.btn_center)
        row_tools.addWidget(self.btn_align_right)
        row_tools.addWidget(self.btn_indent)
        row_tools.addWidget(self.btn_import_docx)
        row_tools.addStretch(1)
        v.addLayout(row_tools)

        self.textEdit = QtWidgets.QTextEdit(panel)
        self.textEdit.setObjectName("textEdit")
        # 文本输入区适当加大，便于一次录入大量文字
        self.textEdit.setMinimumHeight(120)
        self.textEdit.setMaximumHeight(200)
        v.addWidget(self.textEdit)

        # 框选文字区域（实验特性：手写 / 打印混排）
        self.group_regions = QtWidgets.QGroupBox(panel)
        self.group_regions.setObjectName("group_regions")
        gr = QtWidgets.QVBoxLayout(self.group_regions)
        self.label_regions_hint = QtWidgets.QLabel(self.group_regions)
        self.label_regions_hint.setWordWrap(True)
        self.label_regions_hint.setStyleSheet("color: #6b7a70; font-weight: normal;")
        gr.addWidget(self.label_regions_hint)
        self.region_list = QtWidgets.QListWidget(self.group_regions)
        self.region_list.setObjectName("region_list")
        self.region_list.setMinimumHeight(64)
        self.region_list.setMaximumHeight(120)
        gr.addWidget(self.region_list)
        row_region_btns = QtWidgets.QHBoxLayout()
        self.btn_region_delete = QtWidgets.QPushButton(self.group_regions)
        self.btn_region_delete.setObjectName("btn_region_delete")
        row_region_btns.addWidget(self.btn_region_delete)
        self.btn_region_clear = QtWidgets.QPushButton(self.group_regions)
        self.btn_region_clear.setObjectName("btn_region_clear")
        row_region_btns.addWidget(self.btn_region_clear)
        row_region_btns.addStretch(1)
        gr.addLayout(row_region_btns)
        v.addWidget(self.group_regions)

        # 字体 / 背景 文件选择
        grid_file = QtWidgets.QGridLayout()
        grid_file.setColumnStretch(1, 1)
        self.label_font = QtWidgets.QLabel(panel)
        grid_file.addWidget(self.label_font, 0, 0)
        self.lineEdit = QtWidgets.QLineEdit(panel)
        self.lineEdit.setObjectName("lineEdit")
        grid_file.addWidget(self.lineEdit, 0, 1)
        self.pushButton = QtWidgets.QPushButton(panel)
        self.pushButton.setObjectName("pushButton")
        grid_file.addWidget(self.pushButton, 0, 2)
        self.label_bg = QtWidgets.QLabel(panel)
        grid_file.addWidget(self.label_bg, 1, 0)
        self.lineEdit_2 = QtWidgets.QLineEdit(panel)
        self.lineEdit_2.setObjectName("lineEdit_2")
        grid_file.addWidget(self.lineEdit_2, 1, 1)
        self.pushButton_2 = QtWidgets.QPushButton(panel)
        self.pushButton_2.setObjectName("pushButton_2")
        grid_file.addWidget(self.pushButton_2, 1, 2)
        # 文档底图：PDF / Word 打印预览作为多页背景
        self.label_doc = QtWidgets.QLabel(panel)
        grid_file.addWidget(self.label_doc, 2, 0)
        self.lineEdit_14 = QtWidgets.QLineEdit(panel)
        self.lineEdit_14.setObjectName("lineEdit_14")
        self.lineEdit_14.setReadOnly(True)
        self.lineEdit_14.setPlaceholderText("可选：导入 PDF / Word 作为打印底图")
        grid_file.addWidget(self.lineEdit_14, 2, 1)
        self.pushButton_8 = QtWidgets.QPushButton(panel)
        self.pushButton_8.setObjectName("pushButton_8")
        grid_file.addWidget(self.pushButton_8, 2, 2)
        v.addLayout(grid_file)

        # 文字颜色（#RRGGBB 十六进制）
        row_color = QtWidgets.QHBoxLayout()
        self.label_color = QtWidgets.QLabel(panel)
        row_color.addWidget(self.label_color)
        self.lineEdit_10 = QtWidgets.QLineEdit(panel)
        self.lineEdit_10.setObjectName("lineEdit_10")
        self.lineEdit_10.setMaximumWidth(96)
        self.lineEdit_10.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        row_color.addWidget(self.lineEdit_10)
        row_color.addStretch(1)
        v.addLayout(row_color)

        # 预设：下拉框快捷切换（预设文件夹内）+ 载入/保存按钮（任意位置）
        row_preset = QtWidgets.QHBoxLayout()
        self.label_preset = QtWidgets.QLabel(panel)
        row_preset.addWidget(self.label_preset)
        self.combo_preset = NoWheelComboBox(panel)
        self.combo_preset.setObjectName("combo_preset")
        self.combo_preset.setMinimumWidth(90)
        row_preset.addWidget(self.combo_preset, 1)
        self.pushButton_6 = QtWidgets.QPushButton(panel)
        self.pushButton_6.setObjectName("pushButton_6")
        row_preset.addWidget(self.pushButton_6)
        self.pushButton_4 = QtWidgets.QPushButton(panel)
        self.pushButton_4.setObjectName("pushButton_4")
        row_preset.addWidget(self.pushButton_4)
        v.addLayout(row_preset)

        # 排版参数
        self.group_layout = QtWidgets.QGroupBox(panel)
        gl = QtWidgets.QGridLayout(self.group_layout)
        self.label_head_val = QtWidgets.QLabel(self.group_layout)
        self.label_head_val.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        gl.addWidget(self.label_head_val, 0, 1)
        self.label_head_sigma = QtWidgets.QLabel(self.group_layout)
        self.label_head_sigma.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        gl.addWidget(self.label_head_sigma, 0, 3)
        self.label_word_spacing = QtWidgets.QLabel(self.group_layout)
        gl.addWidget(self.label_word_spacing, 1, 0)
        self.lineEdit_7 = QtWidgets.QLineEdit(self.group_layout)
        self.lineEdit_7.setObjectName("lineEdit_7")
        self.lineEdit_7.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        gl.addWidget(self.lineEdit_7, 1, 1)
        self.label_sigma_1 = QtWidgets.QLabel(self.group_layout)
        gl.addWidget(self.label_sigma_1, 1, 2)
        self.spinBox = NoWheelSpinBox(self.group_layout)
        self.spinBox.setObjectName("spinBox")
        gl.addWidget(self.spinBox, 1, 3)
        self.label_line_spacing = QtWidgets.QLabel(self.group_layout)
        gl.addWidget(self.label_line_spacing, 2, 0)
        self.lineEdit_8 = QtWidgets.QLineEdit(self.group_layout)
        self.lineEdit_8.setObjectName("lineEdit_8")
        self.lineEdit_8.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        gl.addWidget(self.lineEdit_8, 2, 1)
        self.label_sigma_2 = QtWidgets.QLabel(self.group_layout)
        gl.addWidget(self.label_sigma_2, 2, 2)
        self.spinBox_2 = NoWheelSpinBox(self.group_layout)
        self.spinBox_2.setObjectName("spinBox_2")
        gl.addWidget(self.spinBox_2, 2, 3)
        self.label_font_size = QtWidgets.QLabel(self.group_layout)
        gl.addWidget(self.label_font_size, 3, 0)
        self.lineEdit_9 = QtWidgets.QLineEdit(self.group_layout)
        self.lineEdit_9.setObjectName("lineEdit_9")
        self.lineEdit_9.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        gl.addWidget(self.lineEdit_9, 3, 1)
        self.label_sigma_3 = QtWidgets.QLabel(self.group_layout)
        gl.addWidget(self.label_sigma_3, 3, 2)
        self.spinBox_3 = NoWheelSpinBox(self.group_layout)
        self.spinBox_3.setObjectName("spinBox_3")
        gl.addWidget(self.spinBox_3, 3, 3)
        gl.setColumnStretch(1, 1)
        v.addWidget(self.group_layout)

        # 笔画扰动
        self.group_perturb = QtWidgets.QGroupBox(panel)
        gp = QtWidgets.QGridLayout(self.group_perturb)
        self.label_perturb_x = QtWidgets.QLabel(self.group_perturb)
        gp.addWidget(self.label_perturb_x, 0, 0)
        self.spinBox_5 = NoWheelSpinBox(self.group_perturb)
        self.spinBox_5.setObjectName("spinBox_5")
        self.spinBox_5.setProperty("value", 4)
        gp.addWidget(self.spinBox_5, 0, 1)
        self.label_perturb_y = QtWidgets.QLabel(self.group_perturb)
        gp.addWidget(self.label_perturb_y, 1, 0)
        self.spinBox_4 = NoWheelSpinBox(self.group_perturb)
        self.spinBox_4.setObjectName("spinBox_4")
        self.spinBox_4.setProperty("value", 4)
        gp.addWidget(self.spinBox_4, 1, 1)
        self.label_perturb_theta = QtWidgets.QLabel(self.group_perturb)
        gp.addWidget(self.label_perturb_theta, 2, 0)
        self.doubleSpinBox_6 = NoWheelDoubleSpinBox(self.group_perturb)
        self.doubleSpinBox_6.setObjectName("doubleSpinBox_6")
        self.doubleSpinBox_6.setSingleStep(0.01)
        self.doubleSpinBox_6.setProperty("value", 0.05)
        gp.addWidget(self.doubleSpinBox_6, 2, 1)
        gp.setColumnStretch(1, 1)
        v.addWidget(self.group_perturb)

        # 边距：位置式布局——上/下/左/右输入框环绕中心“边距”，位置即含义
        self.group_margin = QtWidgets.QGroupBox(panel)
        gm = QtWidgets.QGridLayout(self.group_margin)
        gm.setSpacing(6)
        self.lineEdit_3 = QtWidgets.QLineEdit(self.group_margin)  # 上
        self.lineEdit_3.setObjectName("lineEdit_3")
        self.lineEdit_3.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.lineEdit_4 = QtWidgets.QLineEdit(self.group_margin)  # 下
        self.lineEdit_4.setObjectName("lineEdit_4")
        self.lineEdit_4.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.lineEdit_5 = QtWidgets.QLineEdit(self.group_margin)  # 左
        self.lineEdit_5.setObjectName("lineEdit_5")
        self.lineEdit_5.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.lineEdit_6 = QtWidgets.QLineEdit(self.group_margin)  # 右
        self.lineEdit_6.setObjectName("lineEdit_6")
        self.lineEdit_6.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.label_margin_center = QtWidgets.QLabel(self.group_margin)
        self.label_margin_center.setObjectName("label_margin_center")
        self.label_margin_center.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.label_margin_center.setStyleSheet(
            "color: #4ca6a6; font-weight: bold; border: none; background: transparent;"
        )
        gm.addWidget(self.lineEdit_3, 0, 1)
        gm.addWidget(self.lineEdit_5, 1, 0)
        gm.addWidget(self.label_margin_center, 1, 1)
        gm.addWidget(self.lineEdit_6, 1, 2)
        gm.addWidget(self.lineEdit_4, 2, 1)
        gm.setColumnStretch(0, 1)
        gm.setColumnStretch(2, 1)
        # 边界提示（仅预览）：开关 + 提示颜色 #RRGGBB
        row_bounds = QtWidgets.QHBoxLayout()
        self.checkBox_bounds = QtWidgets.QCheckBox(self.group_margin)
        self.checkBox_bounds.setObjectName("checkBox_bounds")
        self.checkBox_bounds.setChecked(False)
        row_bounds.addWidget(self.checkBox_bounds)
        self.lineEdit_13 = QtWidgets.QLineEdit(self.group_margin)
        self.lineEdit_13.setObjectName("lineEdit_13")
        self.lineEdit_13.setMaximumWidth(96)
        self.lineEdit_13.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        row_bounds.addWidget(self.lineEdit_13)
        row_bounds.addStretch(1)
        gm.addLayout(row_bounds, 3, 0, 1, 3)
        v.addWidget(self.group_margin)

        # 写错字：错字率（0~30%）+ 重写方式 + 涂改方式
        self.group_miswrite = QtWidgets.QGroupBox(panel)
        gmw = QtWidgets.QGridLayout(self.group_miswrite)
        gmw.setSpacing(6)
        self.label_miswrite_rate = QtWidgets.QLabel(self.group_miswrite)
        gmw.addWidget(self.label_miswrite_rate, 0, 0)
        self.miswrite_rate_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal, self.group_miswrite)
        self.miswrite_rate_slider.setObjectName("miswrite_rate_slider")
        self.miswrite_rate_slider.setRange(0, 300)  # 0~30%，步进 0.1%
        self.miswrite_rate_slider.setValue(0)
        gmw.addWidget(self.miswrite_rate_slider, 0, 1)
        self.label_miswrite_rate_value = QtWidgets.QLabel(self.group_miswrite)
        self.label_miswrite_rate_value.setObjectName("label_miswrite_rate_value")
        self.label_miswrite_rate_value.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.label_miswrite_rate_value.setMinimumWidth(40)
        gmw.addWidget(self.label_miswrite_rate_value, 0, 2)
        self.label_miswrite_mode = QtWidgets.QLabel(self.group_miswrite)
        gmw.addWidget(self.label_miswrite_mode, 1, 0)
        self.miswrite_mode_combo = QtWidgets.QComboBox(self.group_miswrite)
        self.miswrite_mode_combo.setObjectName("miswrite_mode_combo")
        gmw.addWidget(self.miswrite_mode_combo, 1, 1, 1, 2)
        self.label_miswrite_style = QtWidgets.QLabel(self.group_miswrite)
        gmw.addWidget(self.label_miswrite_style, 2, 0)
        self.miswrite_style_combo = QtWidgets.QComboBox(self.group_miswrite)
        self.miswrite_style_combo.setObjectName("miswrite_style_combo")
        gmw.addWidget(self.miswrite_style_combo, 2, 1, 1, 2)
        gmw.setColumnStretch(1, 1)
        v.addWidget(self.group_miswrite)

        # 预览 / 导出
        row_btn = QtWidgets.QHBoxLayout()
        self.pushButton_3 = QtWidgets.QPushButton(panel)
        self.pushButton_3.setObjectName("pushButton_3")
        self.pushButton_3.setProperty("primary", True)
        self.pushButton_3.setMinimumHeight(34)
        row_btn.addWidget(self.pushButton_3, 1)
        self.pushButton_5 = QtWidgets.QPushButton(panel)
        self.pushButton_5.setObjectName("pushButton_5")
        self.pushButton_5.setProperty("primary", True)
        self.pushButton_5.setMinimumHeight(34)
        row_btn.addWidget(self.pushButton_5, 1)
        self.pushButton_7 = QtWidgets.QPushButton(panel)
        self.pushButton_7.setObjectName("pushButton_7")
        self.pushButton_7.setProperty("primary", True)
        self.pushButton_7.setMinimumHeight(34)
        row_btn.addWidget(self.pushButton_7, 1)
        right_col.addLayout(row_btn)

        Form.setStyleSheet(_LIGHT_QSS)
        self.retranslateUi(Form)
        QtCore.QMetaObject.connectSlotsByName(Form)

    def retranslateUi(self, Form):
        _translate = QtCore.QCoreApplication.translate
        Form.setWindowTitle(_translate("Form", "手写模拟"))
        self.btn_prev.setText("◀ 上一页")
        self.btn_next.setText("下一页 ▶")
        self.btn_preview_bg.setText("预览底色")
        self.btn_select_region.setText("框选文字")
        self.btn_select_region.setToolTip(
            "勾选后在预览图上按住鼠标拖出矩形，松开后输入该区域的文字\n"
            "（可独立选择手写体 / 打印体，实现混排）"
        )
        self.group_regions.setTitle("文字区域（手写 / 打印混排）")
        self.label_regions_hint.setText(
            "勾选「框选文字」后在左侧预览图拖出矩形生成文字；单击右侧"
            "列表项可在预览中拖动 / 缩放该区域边框（Esc 或点击空白退出），"
            "双击列表项可编辑文字。"
        )
        self.btn_region_delete.setText("删除选中")
        self.btn_region_clear.setText("清空")
        self.label_page.setText("第 1 / 1 页")
        self.label_text.setText(_translate("Form", "待处理文本"))
        self.btn_align_left.setText("左对齐")
        self.btn_center.setText("居中")
        self.btn_align_right.setText("右对齐")
        self.btn_indent.setText("首行缩进")
        self.btn_import_docx.setText("导入 docx")
        self.textEdit.setPlaceholderText(_translate(
            "Form",
            "请输入文本内容，支持多行。\n",
        ))
        self.label_font.setText(_translate("Form", "字体"))
        self.pushButton.setText(_translate("Form", "选择"))
        self.label_bg.setText(_translate("Form", "背景"))
        self.pushButton_2.setText(_translate("Form", "选择"))
        self.label_doc.setText(_translate("Form", "文档底图"))
        self.pushButton_8.setText(_translate("Form", "导入"))
        self.pushButton_8.setToolTip(
            "把 PDF / Word 文档的打印预览逐页作为背景（替换当前背景图片），"
            "然后在预览上框选需要手写填写的位置"
        )
        self.label_color.setText(_translate("Form", "文字颜色"))
        self.lineEdit_10.setText("#000000")
        self.lineEdit_10.setPlaceholderText("#000000")
        self.label_preset.setText(_translate("Form", "预设"))
        self.pushButton_6.setText(_translate("Form", "载入预设"))
        self.pushButton_4.setText(_translate("Form", "保存预设"))
        self.group_layout.setTitle(_translate("Form", "排版参数"))
        self.label_head_val.setText(_translate("Form", "数值"))
        self.label_head_sigma.setText(_translate("Form", "扰动 σ"))
        self.label_word_spacing.setText(_translate("Form", "字水平间距"))
        self.lineEdit_7.setPlaceholderText("5")
        self.label_sigma_1.setText("σ")
        self.label_line_spacing.setText(_translate("Form", "字竖直间距"))
        self.lineEdit_8.setPlaceholderText("48")
        self.label_sigma_2.setText("σ")
        self.label_font_size.setText(_translate("Form", "字体大小"))
        self.lineEdit_9.setPlaceholderText("36")
        self.label_sigma_3.setText("σ")
        self.group_perturb.setTitle(_translate("Form", "笔画扰动"))
        self.label_perturb_x.setText(_translate("Form", "水平笔画位移"))
        self.label_perturb_y.setText(_translate("Form", "竖直笔画位移"))
        self.label_perturb_theta.setText(_translate("Form", "笔画旋转"))
        self.group_margin.setTitle(_translate("Form", "边距"))
        self.label_margin_center.setText(_translate("Form", "边距"))
        self.checkBox_bounds.setText(_translate("Form", "边界提示(仅预览)"))
        self.lineEdit_13.setText("#4ca6a6")
        self.lineEdit_13.setPlaceholderText("#4ca6a6")
        self.lineEdit_3.setPlaceholderText("30")
        self.lineEdit_4.setPlaceholderText("30")
        self.lineEdit_5.setPlaceholderText("30")
        self.lineEdit_6.setPlaceholderText("30")
        self.pushButton_3.setText(_translate("Form", "预览"))
        self.pushButton_5.setText(_translate("Form", "导出"))
        self.pushButton_7.setText(_translate("Form", "导出 PDF"))
        self.group_miswrite.setTitle(_translate("Form", "写错字"))
        self.label_miswrite_rate.setText(_translate("Form", "错字率"))
        self.miswrite_rate_slider.setToolTip(_translate("Form", "0~30%"))
        self.label_miswrite_mode.setText(_translate("Form", "重写方式"))
        self.miswrite_mode_combo.addItems(["右上方重写", "后文重写"])
        self.label_miswrite_style.setText(_translate("Form", "涂改方式"))
        self.miswrite_style_combo.addItems(["单横线", "双横线", "斜线", "叉号"])
