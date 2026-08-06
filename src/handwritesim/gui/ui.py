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
    background: #ffffff;
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


class PreviewLabel(QtWidgets.QLabel):
    """预览区：保持宽高比缩放，随窗口尺寸自适应。"""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._source: QtGui.QPixmap | None = None
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

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
        super().setPixmap(
            self._source.scaled(
                self.size(),
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
        )


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
        self.textEdit.setMinimumHeight(90)
        self.textEdit.setMaximumHeight(150)
        v.addWidget(self.textEdit)

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
        v.addLayout(grid_file)

        # 文字颜色 RGB
        row_color = QtWidgets.QHBoxLayout()
        self.label_color = QtWidgets.QLabel(panel)
        row_color.addWidget(self.label_color)
        self.label_r = QtWidgets.QLabel(panel)
        self.label_r.setStyleSheet("color: #d05a5a; font-weight: bold;")
        row_color.addWidget(self.label_r)
        self.lineEdit_10 = QtWidgets.QLineEdit(panel)
        self.lineEdit_10.setObjectName("lineEdit_10")
        self.lineEdit_10.setMaximumWidth(52)
        self.lineEdit_10.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        row_color.addWidget(self.lineEdit_10)
        self.label_g = QtWidgets.QLabel(panel)
        self.label_g.setStyleSheet("color: #4ca64c; font-weight: bold;")
        row_color.addWidget(self.label_g)
        self.lineEdit_11 = QtWidgets.QLineEdit(panel)
        self.lineEdit_11.setObjectName("lineEdit_11")
        self.lineEdit_11.setMaximumWidth(52)
        self.lineEdit_11.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        row_color.addWidget(self.lineEdit_11)
        self.label_b = QtWidgets.QLabel(panel)
        self.label_b.setStyleSheet("color: #5a7ed0; font-weight: bold;")
        row_color.addWidget(self.label_b)
        self.lineEdit_12 = QtWidgets.QLineEdit(panel)
        self.lineEdit_12.setObjectName("lineEdit_12")
        self.lineEdit_12.setMaximumWidth(52)
        self.lineEdit_12.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        row_color.addWidget(self.lineEdit_12)
        row_color.addStretch(1)
        v.addLayout(row_color)

        # 预设
        row_preset = QtWidgets.QHBoxLayout()
        self.label_preset = QtWidgets.QLabel(panel)
        row_preset.addWidget(self.label_preset)
        self.pushButton_6 = QtWidgets.QPushButton(panel)
        self.pushButton_6.setObjectName("pushButton_6")
        row_preset.addWidget(self.pushButton_6)
        self.pushButton_4 = QtWidgets.QPushButton(panel)
        self.pushButton_4.setObjectName("pushButton_4")
        row_preset.addWidget(self.pushButton_4)
        row_preset.addStretch(1)
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
        # 边界提示（仅预览）：开关 + 提示颜色 RGB
        row_bounds = QtWidgets.QHBoxLayout()
        self.checkBox_bounds = QtWidgets.QCheckBox(self.group_margin)
        self.checkBox_bounds.setObjectName("checkBox_bounds")
        self.checkBox_bounds.setChecked(True)
        row_bounds.addWidget(self.checkBox_bounds)
        self.label_bounds_r = QtWidgets.QLabel(self.group_margin)
        self.label_bounds_r.setStyleSheet("color: #d05a5a; font-weight: bold;")
        row_bounds.addWidget(self.label_bounds_r)
        self.lineEdit_13 = QtWidgets.QLineEdit(self.group_margin)
        self.lineEdit_13.setObjectName("lineEdit_13")
        self.lineEdit_13.setMaximumWidth(52)
        self.lineEdit_13.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        row_bounds.addWidget(self.lineEdit_13)
        self.label_bounds_g = QtWidgets.QLabel(self.group_margin)
        self.label_bounds_g.setStyleSheet("color: #4ca64c; font-weight: bold;")
        row_bounds.addWidget(self.label_bounds_g)
        self.lineEdit_14 = QtWidgets.QLineEdit(self.group_margin)
        self.lineEdit_14.setObjectName("lineEdit_14")
        self.lineEdit_14.setMaximumWidth(52)
        self.lineEdit_14.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        row_bounds.addWidget(self.lineEdit_14)
        self.label_bounds_b = QtWidgets.QLabel(self.group_margin)
        self.label_bounds_b.setStyleSheet("color: #5a8ed0; font-weight: bold;")
        row_bounds.addWidget(self.label_bounds_b)
        self.lineEdit_15 = QtWidgets.QLineEdit(self.group_margin)
        self.lineEdit_15.setObjectName("lineEdit_15")
        self.lineEdit_15.setMaximumWidth(52)
        self.lineEdit_15.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        row_bounds.addWidget(self.lineEdit_15)
        row_bounds.addStretch(1)
        gm.addLayout(row_bounds, 3, 0, 1, 3)
        v.addWidget(self.group_margin)

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
        right_col.addLayout(row_btn)

        Form.setStyleSheet(_LIGHT_QSS)
        self.retranslateUi(Form)
        QtCore.QMetaObject.connectSlotsByName(Form)

    def retranslateUi(self, Form):
        _translate = QtCore.QCoreApplication.translate
        Form.setWindowTitle(_translate("Form", "手写模拟"))
        self.btn_prev.setText("◀ 上一页")
        self.btn_next.setText("下一页 ▶")
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
        self.label_color.setText(_translate("Form", "文字颜色"))
        self.label_r.setText("R")
        self.label_g.setText("G")
        self.label_b.setText("B")
        self.lineEdit_10.setText("0")
        self.lineEdit_11.setText("0")
        self.lineEdit_12.setText("0")
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
        self.label_bounds_r.setText("R")
        self.label_bounds_g.setText("G")
        self.label_bounds_b.setText("B")
        self.lineEdit_13.setText("76")
        self.lineEdit_14.setText("166")
        self.lineEdit_15.setText("166")
        self.lineEdit_3.setPlaceholderText("30")
        self.lineEdit_4.setPlaceholderText("30")
        self.lineEdit_5.setPlaceholderText("30")
        self.lineEdit_6.setPlaceholderText("30")
        self.pushButton_3.setText(_translate("Form", "预览"))
        self.pushButton_5.setText(_translate("Form", "导出"))
