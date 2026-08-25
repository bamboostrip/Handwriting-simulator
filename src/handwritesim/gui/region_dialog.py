"""框选文字区域的编辑对话框。

支持多段落独立排版（左/中/右对齐、首行缩进、导入 docx）、基础属性（手写/打印、
所在页、打印字体、字号）、以及折叠面板「排版与扰动覆盖」（字距/行距/字号及各自
随机扰动、笔画扰动、写错字、文字颜色、4向内边距）。
对齐 Rust 版 RegionDialog.vue 与 RegionTextEditor.vue。
"""

from __future__ import annotations

from pathlib import Path
from PyQt6 import QtCore, QtGui, QtWidgets

from ..core.models import Paragraph, parse_color
from .ui import NoWheelComboBox, NoWheelDoubleSpinBox, NoWheelSpinBox


class RegionDialog(QtWidgets.QDialog):
    """添加 / 编辑一个框选文字区域。"""

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        *,
        title: str = "添加文字区域",
        text: str = "",
        printed: bool = False,
        font_path: str = "",
        font_size: int = 0,
        main_font_size: int = 36,
        page: int = 1,
        align: str = "left",
        indent_em: float = 0.0,
        paragraphs: list[Paragraph] | None = None,
        word_spacing: int | None = None,
        line_spacing: int | None = None,
        font_size_sigma: int | None = None,
        word_spacing_sigma: int | None = None,
        line_spacing_sigma: int | None = None,
        perturb_x_sigma: int | None = None,
        perturb_y_sigma: int | None = None,
        perturb_theta_sigma: float | None = None,
        miswrite_rate: float | None = None,
        miswrite_strikeout_style: str | None = None,
        color: str | None = None,
        margin_top: int | None = None,
        margin_bottom: int | None = None,
        margin_left: int | None = None,
        margin_right: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(500)
        self._main_font_size = main_font_size

        v = QtWidgets.QVBoxLayout(self)
        v.setSpacing(8)

        # --------------------------------------------------------------
        # 工具行：对齐 / 缩进 / 导入 docx（作用于当前光标所在行）
        # --------------------------------------------------------------
        row_tools = QtWidgets.QHBoxLayout()
        row_tools.setSpacing(6)

        self.btn_align_left = QtWidgets.QPushButton("左对齐", self)
        self.btn_align_left.setCheckable(True)
        self.btn_align_center = QtWidgets.QPushButton("居中", self)
        self.btn_align_center.setCheckable(True)
        self.btn_align_right = QtWidgets.QPushButton("右对齐", self)
        self.btn_align_right.setCheckable(True)
        self.btn_indent = QtWidgets.QPushButton("首行缩进", self)
        self.btn_indent.setCheckable(True)
        self.btn_import_docx = QtWidgets.QPushButton("导入 docx", self)

        row_tools.addWidget(self.btn_align_left)
        row_tools.addWidget(self.btn_align_center)
        row_tools.addWidget(self.btn_align_right)
        row_tools.addWidget(self.btn_indent)
        row_tools.addWidget(self.btn_import_docx)
        row_tools.addStretch(1)
        v.addLayout(row_tools)

        # 当前行状态提示
        self.label_row_status = QtWidgets.QLabel(self)
        self.label_row_status.setStyleSheet("color: #6b7a70; font-size: 11px;")
        v.addWidget(self.label_row_status)

        # 富文本段落编辑器
        self.text_edit = QtWidgets.QTextEdit(self)
        self.text_edit.setMinimumHeight(90)
        self.text_edit.setMaximumHeight(160)
        self.text_edit.setPlaceholderText(
            "输入该区域内要生成的文字，支持多行；回车分段，上方按钮设置当前行对齐/缩进；留空则放弃该区域"
        )
        v.addWidget(self.text_edit)

        # 填充初始文本与段落
        if paragraphs:
            self._set_paragraphs(paragraphs)
        elif text:
            if align != "left" or indent_em > 0:
                fs = font_size if font_size > 0 else main_font_size
                indent_px = int(round(indent_em * fs))
                self._set_paragraphs([Paragraph(text=text, align=align, first_line_indent=indent_px)])
            else:
                self.text_edit.setPlainText(text)

        # --------------------------------------------------------------
        # 基础参数网格
        # --------------------------------------------------------------
        grid_basic = QtWidgets.QGridLayout()
        grid_basic.setHorizontalSpacing(10)
        grid_basic.setVerticalSpacing(6)

        # 样式
        grid_basic.addWidget(QtWidgets.QLabel("样式", self), 0, 0)
        row_style = QtWidgets.QHBoxLayout()
        self.combo_style = NoWheelComboBox(self)
        self.combo_style.addItems(["手写体", "打印体"])
        self.combo_style.setCurrentIndex(1 if printed else 0)
        row_style.addWidget(self.combo_style)
        row_style.addStretch(1)
        grid_basic.addLayout(row_style, 0, 1)

        # 所在页
        grid_basic.addWidget(QtWidgets.QLabel("所在页", self), 1, 0)
        row_page = QtWidgets.QHBoxLayout()
        self.spin_page = NoWheelSpinBox(self)
        self.spin_page.setRange(1, 999)
        self.spin_page.setValue(max(1, int(page)))
        self.spin_page.setToolTip("该文字区域在第几页渲染（超出框选范围的内容将自然截断）")
        row_page.addWidget(self.spin_page)
        lbl_page_hint = QtWidgets.QLabel("仅在指定页渲染，超出框选范围的内容自然截断", self)
        lbl_page_hint.setStyleSheet("color: #6b7a70; font-size: 11px;")
        row_page.addWidget(lbl_page_hint)
        row_page.addStretch(1)
        grid_basic.addLayout(row_page, 1, 1)

        # 打印字体
        self.label_font = QtWidgets.QLabel("打印字体", self)
        grid_basic.addWidget(self.label_font, 2, 0)
        row_font = QtWidgets.QHBoxLayout()
        self.edit_font = QtWidgets.QLineEdit(self)
        self.edit_font.setPlaceholderText("留空使用主字体")
        self.edit_font.setText(font_path)
        row_font.addWidget(self.edit_font, 1)
        self.btn_font = QtWidgets.QPushButton("选择", self)
        row_font.addWidget(self.btn_font)
        grid_basic.addLayout(row_font, 2, 1)

        # 字号
        grid_basic.addWidget(QtWidgets.QLabel("字号", self), 3, 0)
        row_size = QtWidgets.QHBoxLayout()
        self.spin_size = NoWheelSpinBox(self)
        self.spin_size.setRange(0, 300)
        self.spin_size.setValue(int(font_size))
        self.spin_size.setSpecialValueText("跟随主设置")
        self.spin_size.setToolTip("0 表示使用主界面的字体大小")
        row_size.addWidget(self.spin_size)
        lbl_size_hint = QtWidgets.QLabel(f"主字号当前为 {main_font_size}，0 表示跟随", self)
        lbl_size_hint.setStyleSheet("color: #6b7a70; font-size: 11px;")
        row_size.addWidget(lbl_size_hint)
        row_size.addStretch(1)
        grid_basic.addLayout(row_size, 3, 1)

        v.addLayout(grid_basic)

        # --------------------------------------------------------------
        # 折叠面板：排版与扰动覆盖
        # --------------------------------------------------------------
        self.btn_toggle_adv = QtWidgets.QPushButton(self)
        self.btn_toggle_adv.setStyleSheet(
            "text-align: left; font-weight: bold; background: #eef5f0; border: 1px solid #d3ded6;"
        )
        v.addWidget(self.btn_toggle_adv)

        self.widget_adv = QtWidgets.QWidget(self)
        v_adv = QtWidgets.QVBoxLayout(self.widget_adv)
        v_adv.setContentsMargins(0, 4, 0, 0)
        v_adv.setSpacing(8)

        lbl_adv_tip = QtWidgets.QLabel("留空即跟随全局设置；打印体下扰动 / 错字类覆盖不生效。", self.widget_adv)
        lbl_adv_tip.setStyleSheet("color: #6b7a70; font-size: 11px;")
        v_adv.addWidget(lbl_adv_tip)

        # 1. 排版参数 Group
        grp_layout = QtWidgets.QGroupBox("排版参数", self.widget_adv)
        g_layout = QtWidgets.QGridLayout(grp_layout)
        g_layout.setHorizontalSpacing(10)
        g_layout.setVerticalSpacing(6)

        g_layout.addWidget(QtWidgets.QLabel("数值", grp_layout), 0, 1)
        g_layout.addWidget(QtWidgets.QLabel("随机扰动", grp_layout), 0, 2)

        g_layout.addWidget(QtWidgets.QLabel("字水平间距", grp_layout), 1, 0)
        self.spin_ws = NoWheelSpinBox(grp_layout)
        self.spin_ws.setRange(0, 100)
        self.spin_ws.setSpecialValueText("跟随主设置")
        if word_spacing is not None:
            self.spin_ws.setValue(word_spacing)
        else:
            self.spin_ws.setValue(0)
        self._set_spinbox_optional(self.spin_ws, word_spacing)
        g_layout.addWidget(self.spin_ws, 1, 1)

        self.spin_ws_sigma = NoWheelSpinBox(grp_layout)
        self.spin_ws_sigma.setRange(0, 20)
        self.spin_ws_sigma.setSpecialValueText("跟随主设置")
        self._set_spinbox_optional(self.spin_ws_sigma, word_spacing_sigma)
        g_layout.addWidget(self.spin_ws_sigma, 1, 2)

        g_layout.addWidget(QtWidgets.QLabel("字竖直间距", grp_layout), 2, 0)
        self.spin_ls = NoWheelSpinBox(grp_layout)
        self.spin_ls.setRange(0, 200)
        self.spin_ls.setSpecialValueText("跟随主设置")
        self._set_spinbox_optional(self.spin_ls, line_spacing)
        g_layout.addWidget(self.spin_ls, 2, 1)

        self.spin_ls_sigma = NoWheelSpinBox(grp_layout)
        self.spin_ls_sigma.setRange(0, 20)
        self.spin_ls_sigma.setSpecialValueText("跟随主设置")
        self._set_spinbox_optional(self.spin_ls_sigma, line_spacing_sigma)
        g_layout.addWidget(self.spin_ls_sigma, 2, 2)

        g_layout.addWidget(QtWidgets.QLabel("字号扰动", grp_layout), 3, 0)
        self.spin_fs_sigma = NoWheelSpinBox(grp_layout)
        self.spin_fs_sigma.setRange(0, 20)
        self.spin_fs_sigma.setSpecialValueText("跟随主设置")
        self._set_spinbox_optional(self.spin_fs_sigma, font_size_sigma)
        g_layout.addWidget(self.spin_fs_sigma, 3, 2)

        v_adv.addWidget(grp_layout)

        # 2. 笔画扰动 Group
        grp_perturb = QtWidgets.QGroupBox("笔画扰动", self.widget_adv)
        g_perturb = QtWidgets.QGridLayout(grp_perturb)
        g_perturb.setHorizontalSpacing(10)
        g_perturb.setVerticalSpacing(6)

        g_perturb.addWidget(QtWidgets.QLabel("水平位移 σ", grp_perturb), 0, 0)
        self.spin_px_sigma = NoWheelSpinBox(grp_perturb)
        self.spin_px_sigma.setRange(0, 20)
        self.spin_px_sigma.setSpecialValueText("跟随主设置")
        self._set_spinbox_optional(self.spin_px_sigma, perturb_x_sigma)
        g_perturb.addWidget(self.spin_px_sigma, 0, 1)

        g_perturb.addWidget(QtWidgets.QLabel("竖直位移 σ", grp_perturb), 0, 2)
        self.spin_py_sigma = NoWheelSpinBox(grp_perturb)
        self.spin_py_sigma.setRange(0, 20)
        self.spin_py_sigma.setSpecialValueText("跟随主设置")
        self._set_spinbox_optional(self.spin_py_sigma, perturb_y_sigma)
        g_perturb.addWidget(self.spin_py_sigma, 0, 3)

        g_perturb.addWidget(QtWidgets.QLabel("笔画旋转 σ", grp_perturb), 1, 0)
        self.spin_pt_sigma = NoWheelDoubleSpinBox(grp_perturb)
        self.spin_pt_sigma.setRange(0.0, 2.0)
        self.spin_pt_sigma.setSingleStep(0.01)
        self.spin_pt_sigma.setDecimals(3)
        self.spin_pt_sigma.setSpecialValueText("跟随主设置")
        if perturb_theta_sigma is not None:
            self.spin_pt_sigma.setValue(perturb_theta_sigma)
        else:
            self.spin_pt_sigma.setValue(0.0)
        g_perturb.addWidget(self.spin_pt_sigma, 1, 1)

        v_adv.addWidget(grp_perturb)

        # 3. 写错字 Group
        grp_miswrite = QtWidgets.QGroupBox("写错字", self.widget_adv)
        g_miswrite = QtWidgets.QGridLayout(grp_miswrite)
        g_miswrite.setHorizontalSpacing(10)
        g_miswrite.setVerticalSpacing(6)

        g_miswrite.addWidget(QtWidgets.QLabel("错字率", grp_miswrite), 0, 0)
        self.spin_miswrite_rate = NoWheelDoubleSpinBox(grp_miswrite)
        self.spin_miswrite_rate.setRange(0.0, 30.0)
        self.spin_miswrite_rate.setSingleStep(0.5)
        self.spin_miswrite_rate.setSuffix("%")
        self.spin_miswrite_rate.setSpecialValueText("跟随主设置")
        if miswrite_rate is not None:
            self.spin_miswrite_rate.setValue(miswrite_rate * 100.0)
        else:
            self.spin_miswrite_rate.setValue(0.0)
        g_miswrite.addWidget(self.spin_miswrite_rate, 0, 1)

        g_miswrite.addWidget(QtWidgets.QLabel("涂改方式", grp_miswrite), 0, 2)
        self.combo_miswrite_style = NoWheelComboBox(grp_miswrite)
        self.combo_miswrite_style.addItems(["跟随主设置", "单横线", "双横线", "斜线", "叉号"])
        style_map = {"line": 1, "double_line": 2, "slash": 3, "cross": 4}
        self.combo_miswrite_style.setCurrentIndex(style_map.get(miswrite_strikeout_style or "", 0))
        g_miswrite.addWidget(self.combo_miswrite_style, 0, 3)

        v_adv.addWidget(grp_miswrite)

        # 4. 文字颜色 Group
        grp_color = QtWidgets.QGroupBox("文字颜色", self.widget_adv)
        row_color = QtWidgets.QHBoxLayout(grp_color)
        row_color.addWidget(QtWidgets.QLabel("颜色", grp_color))
        self.edit_color = QtWidgets.QLineEdit(grp_color)
        self.edit_color.setPlaceholderText("跟随主设置")
        self.edit_color.setText(color or "")
        self.edit_color.setMaximumWidth(120)
        row_color.addWidget(self.edit_color)

        self.btn_color_pick = QtWidgets.QPushButton("取色", grp_color)
        row_color.addWidget(self.btn_color_pick)

        self.btn_color_reset = QtWidgets.QPushButton("重置跟随", grp_color)
        row_color.addWidget(self.btn_color_reset)
        row_color.addStretch(1)

        v_adv.addWidget(grp_color)

        # 5. 边距 Group
        grp_margins = QtWidgets.QGroupBox("边距（像素）", self.widget_adv)
        g_margins = QtWidgets.QGridLayout(grp_margins)
        g_margins.setHorizontalSpacing(10)
        g_margins.setVerticalSpacing(6)

        g_margins.addWidget(QtWidgets.QLabel("上边距", grp_margins), 0, 0)
        self.spin_m_top = NoWheelSpinBox(grp_margins)
        self.spin_m_top.setRange(0, 1000)
        self.spin_m_top.setValue(margin_top or 0)
        g_margins.addWidget(self.spin_m_top, 0, 1)

        g_margins.addWidget(QtWidgets.QLabel("下边距", grp_margins), 0, 2)
        self.spin_m_bottom = NoWheelSpinBox(grp_margins)
        self.spin_m_bottom.setRange(0, 1000)
        self.spin_m_bottom.setValue(margin_bottom or 0)
        g_margins.addWidget(self.spin_m_bottom, 0, 3)

        g_margins.addWidget(QtWidgets.QLabel("左边距", grp_margins), 1, 0)
        self.spin_m_left = NoWheelSpinBox(grp_margins)
        self.spin_m_left.setRange(0, 1000)
        self.spin_m_left.setValue(margin_left or 0)
        g_margins.addWidget(self.spin_m_left, 1, 1)

        g_margins.addWidget(QtWidgets.QLabel("右边距", grp_margins), 1, 2)
        self.spin_m_right = NoWheelSpinBox(grp_margins)
        self.spin_m_right.setRange(0, 1000)
        self.spin_m_right.setValue(margin_right or 0)
        g_margins.addWidget(self.spin_m_right, 1, 3)

        v_adv.addWidget(grp_margins)
        v.addWidget(self.widget_adv)

        # 折叠初始态：若有自定义设置则展开，否则收起
        self._adv_expanded = self._has_any_overrides(
            word_spacing, line_spacing, font_size_sigma,
            word_spacing_sigma, line_spacing_sigma, perturb_x_sigma,
            perturb_y_sigma, perturb_theta_sigma, miswrite_rate,
            miswrite_strikeout_style, color, margin_top, margin_bottom,
            margin_left, margin_right,
        )
        self._update_adv_toggle_ui()

        # --------------------------------------------------------------
        # 对话框底部按钮
        # --------------------------------------------------------------
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

        # --------------------------------------------------------------
        # 信号绑定
        # --------------------------------------------------------------
        self.btn_font.clicked.connect(self._choose_font)
        self.combo_style.currentIndexChanged.connect(self._update_font_enabled)
        self.btn_align_left.clicked.connect(lambda: self._set_align("left"))
        self.btn_align_center.clicked.connect(lambda: self._set_align("center"))
        self.btn_align_right.clicked.connect(lambda: self._set_align("right"))
        self.btn_indent.clicked.connect(self._toggle_indent)
        self.btn_import_docx.clicked.connect(self._import_docx)
        self.text_edit.cursorPositionChanged.connect(self._update_row_status_and_toolbar)
        self.btn_toggle_adv.clicked.connect(self._toggle_adv)
        self.btn_color_pick.clicked.connect(self._pick_color)
        self.btn_color_reset.clicked.connect(lambda: self.edit_color.clear())

        self._update_font_enabled()
        self._update_row_status_and_toolbar()

    # ------------------------------------------------------------------
    # 辅助工具
    # ------------------------------------------------------------------
    def _effective_font_size(self) -> int:
        sz = self.spin_size.value()
        return sz if sz > 0 else self._main_font_size

    def _set_spinbox_optional(self, spin: QtWidgets.QSpinBox, val: int | None) -> None:
        if val is not None and val > 0:
            spin.setValue(val)
        else:
            spin.setValue(0)

    def _has_any_overrides(
        self,
        word_spacing, line_spacing, font_size_sigma,
        word_spacing_sigma, line_spacing_sigma, perturb_x_sigma,
        perturb_y_sigma, perturb_theta_sigma, miswrite_rate,
        miswrite_strikeout_style, color, margin_top, margin_bottom,
        margin_left, margin_right,
    ) -> bool:
        return bool(
            word_spacing or line_spacing or font_size_sigma
            or word_spacing_sigma or line_spacing_sigma
            or perturb_x_sigma or perturb_y_sigma
            or perturb_theta_sigma or miswrite_rate
            or miswrite_strikeout_style or (color and color.strip())
            or margin_top or margin_bottom or margin_left or margin_right
        )

    def _toggle_adv(self) -> None:
        self._adv_expanded = not self._adv_expanded
        self._update_adv_toggle_ui()
        self.adjustSize()

    def _update_adv_toggle_ui(self) -> None:
        has_custom = bool(
            self.region_word_spacing is not None
            or self.region_line_spacing is not None
            or self.region_font_size_sigma is not None
            or self.region_word_spacing_sigma is not None
            or self.region_line_spacing_sigma is not None
            or self.region_perturb_x_sigma is not None
            or self.region_perturb_y_sigma is not None
            or self.region_perturb_theta_sigma is not None
            or self.region_miswrite_rate is not None
            or self.region_miswrite_strikeout_style is not None
            or self.region_color is not None
            or self.region_margin_top
            or self.region_margin_bottom
            or self.region_margin_left
            or self.region_margin_right
        )
        tag = "已自定义" if has_custom else "跟随主设置"
        arrow = "▼" if self._adv_expanded else "▶"
        self.btn_toggle_adv.setText(f"{arrow} 排版与扰动覆盖 ({tag})")
        self.widget_adv.setVisible(self._adv_expanded)

    def _update_font_enabled(self) -> None:
        printed = self.combo_style.currentIndex() == 1
        self.label_font.setEnabled(printed)
        self.edit_font.setEnabled(printed)
        self.btn_font.setEnabled(printed)

    def _choose_font(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择打印字体", "", "字体 (*.ttf *.ttc *.otf)"
        )
        if path:
            self.edit_font.setText(path)

    def _pick_color(self) -> None:
        init = QtGui.QColor(self.edit_color.text().strip() or "#000000")
        color = QtWidgets.QColorDialog.getColor(init, self, "选择区域文字颜色")
        if color.isValid():
            self.edit_color.setText(color.name())

    # ------------------------------------------------------------------
    # 段落编辑与工具栏联动
    # ------------------------------------------------------------------
    def _set_paragraphs(self, paras: list[Paragraph]) -> None:
        self.text_edit.blockSignals(True)
        self.text_edit.clear()
        cursor = QtGui.QTextCursor(self.text_edit.document())
        for idx, para in enumerate(paras):
            if idx:
                cursor.insertBlock()
            fmt = QtGui.QTextBlockFormat()
            if para.align == "center":
                fmt.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            elif para.align == "right":
                fmt.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
            else:
                fmt.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
            if para.first_line_indent:
                fmt.setTextIndent(para.first_line_indent)
            cursor.setBlockFormat(fmt)
            cursor.insertText(para.text)
        self.text_edit.setTextCursor(cursor)
        self.text_edit.blockSignals(False)
        self._update_row_status_and_toolbar()

    def _collect_paragraphs(self) -> list[Paragraph]:
        doc = self.text_edit.document()
        paras: list[Paragraph] = []
        for i in range(doc.blockCount()):
            block = doc.findBlockByNumber(i)
            raw = block.text()
            fmt = block.blockFormat()
            alignment = fmt.alignment()
            if alignment & QtCore.Qt.AlignmentFlag.AlignCenter:
                align = "center"
            elif alignment & QtCore.Qt.AlignmentFlag.AlignRight:
                align = "right"
            else:
                align = "left"
            indent_px = int(round(fmt.textIndent()))
            paras.append(Paragraph(text=raw, align=align, first_line_indent=indent_px))
        return paras

    def _update_row_status_and_toolbar(self) -> None:
        cursor = self.text_edit.textCursor()
        block = cursor.block()
        row_idx = block.blockNumber() + 1
        text = block.text().replace("\n", "")
        char_count = len(text)

        fmt = block.blockFormat()
        alignment = fmt.alignment()
        if alignment & QtCore.Qt.AlignmentFlag.AlignCenter:
            align_str = "居中"
            self.btn_align_center.setChecked(True)
            self.btn_align_left.setChecked(False)
            self.btn_align_right.setChecked(False)
        elif alignment & QtCore.Qt.AlignmentFlag.AlignRight:
            align_str = "右对齐"
            self.btn_align_right.setChecked(True)
            self.btn_align_left.setChecked(False)
            self.btn_align_center.setChecked(False)
        else:
            align_str = "左对齐"
            self.btn_align_left.setChecked(True)
            self.btn_align_center.setChecked(False)
            self.btn_align_right.setChecked(False)

        has_indent = fmt.textIndent() > 0
        self.btn_indent.setChecked(has_indent)
        indent_str = "，首行缩进 2 字" if has_indent else ""
        seg_str = "（空行）" if not text.strip() else ""

        self.label_row_status.setText(
            f"第 {row_idx} 行（{char_count} 字）：{align_str}{indent_str}{seg_str}"
        )

    def _set_align(self, align: str) -> None:
        cursor = self.text_edit.textCursor()
        fmt = QtGui.QTextBlockFormat()
        if align == "center":
            fmt.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        elif align == "right":
            fmt.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        else:
            fmt.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        cursor.mergeBlockFormat(fmt)
        self._update_row_status_and_toolbar()

    def _toggle_indent(self) -> None:
        cursor = self.text_edit.textCursor()
        current = cursor.blockFormat().textIndent()
        fmt = QtGui.QTextBlockFormat()
        if current > 0:
            fmt.setTextIndent(0)
        else:
            fmt.setTextIndent(2 * self._effective_font_size())
        cursor.mergeBlockFormat(fmt)
        self._update_row_status_and_toolbar()

    def _import_docx(self) -> None:
        from ..core.docx_io import load_paragraphs

        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "导入 docx 到区域", "", "Word 文档 (*.docx)"
        )
        if not path:
            return
        try:
            paras = load_paragraphs(path, self._effective_font_size())
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "导入失败", str(exc))
            return
        if not paras:
            QtWidgets.QMessageBox.warning(self, "导入提示", "文档中未包含有效段落")
            return
        self._set_paragraphs(paras)

    def _on_accept(self) -> None:
        font_path = self.edit_font.text().strip()
        if self.combo_style.currentIndex() == 1 and font_path:
            if not Path(font_path).is_file():
                QtWidgets.QMessageBox.warning(self, "字体检查", f"文字区域字体文件不存在：{font_path}")
                return
        color = self.region_color
        if color:
            try:
                parse_color(color)
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, "颜色检查", str(exc))
                return
        self.accept()

    # ------------------------------------------------------------------
    # 属性 Getters
    # ------------------------------------------------------------------
    @property
    def region_text(self) -> str:
        return self.text_edit.toPlainText()

    @property
    def region_printed(self) -> bool:
        return self.combo_style.currentIndex() == 1

    @property
    def region_font_path(self) -> str:
        return self.edit_font.text().strip()

    @property
    def region_font_size(self) -> int:
        return self.spin_size.value()

    @property
    def region_page(self) -> int:
        return self.spin_page.value()

    @property
    def region_paragraphs(self) -> list[Paragraph]:
        return self._collect_paragraphs()

    @property
    def region_align(self) -> str:
        paras = self.region_paragraphs
        return paras[0].align if paras else "left"

    @property
    def region_indent_em(self) -> float:
        paras = self.region_paragraphs
        if paras and paras[0].first_line_indent > 0:
            fs = self._effective_font_size()
            return round(paras[0].first_line_indent / fs, 2) if fs > 0 else 0.0
        return 0.0

    @property
    def region_word_spacing(self) -> int | None:
        v = self.spin_ws.value()
        return v if v > 0 else None

    @property
    def region_line_spacing(self) -> int | None:
        v = self.spin_ls.value()
        return v if v > 0 else None

    @property
    def region_font_size_sigma(self) -> int | None:
        v = self.spin_fs_sigma.value()
        return v if v > 0 else None

    @property
    def region_word_spacing_sigma(self) -> int | None:
        v = self.spin_ws_sigma.value()
        return v if v > 0 else None

    @property
    def region_line_spacing_sigma(self) -> int | None:
        v = self.spin_ls_sigma.value()
        return v if v > 0 else None

    @property
    def region_perturb_x_sigma(self) -> int | None:
        v = self.spin_px_sigma.value()
        return v if v > 0 else None

    @property
    def region_perturb_y_sigma(self) -> int | None:
        v = self.spin_py_sigma.value()
        return v if v > 0 else None

    @property
    def region_perturb_theta_sigma(self) -> float | None:
        v = self.spin_pt_sigma.value()
        return v if v > 0.0 else None

    @property
    def region_miswrite_rate(self) -> float | None:
        v = self.spin_miswrite_rate.value()
        return (v / 100.0) if v > 0.0 else None

    @property
    def region_miswrite_strikeout_style(self) -> str | None:
        idx = self.combo_miswrite_style.currentIndex()
        mapping = {1: "line", 2: "double_line", 3: "slash", 4: "cross"}
        return mapping.get(idx)

    @property
    def region_color(self) -> str | None:
        c = self.edit_color.text().strip()
        return c if c else None

    @property
    def region_margin_top(self) -> int | None:
        v = self.spin_m_top.value()
        return v if v > 0 else None

    @property
    def region_margin_bottom(self) -> int | None:
        v = self.spin_m_bottom.value()
        return v if v > 0 else None

    @property
    def region_margin_left(self) -> int | None:
        v = self.spin_m_left.value()
        return v if v > 0 else None

    @property
    def region_margin_right(self) -> int | None:
        v = self.spin_m_right.value()
        return v if v > 0 else None
