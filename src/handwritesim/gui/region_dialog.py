"""框选文字区域的编辑对话框。

输入区域内文字，选择手写体 / 打印体；打印体可指定独立字体与字号
（留空 / 0 表示跟随主设置）。
"""

from __future__ import annotations

from PyQt6 import QtWidgets

from .ui import NoWheelSpinBox


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
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        v = QtWidgets.QVBoxLayout(self)

        v.addWidget(QtWidgets.QLabel("区域文字", self))
        self.text_edit = QtWidgets.QPlainTextEdit(self)
        self.text_edit.setPlainText(text)
        self.text_edit.setMinimumHeight(80)
        self.text_edit.setPlaceholderText("输入该区域内要生成的文字，支持多行")
        v.addWidget(self.text_edit)

        row_style = QtWidgets.QHBoxLayout()
        row_style.addWidget(QtWidgets.QLabel("样式", self))
        self.combo_style = QtWidgets.QComboBox(self)
        self.combo_style.addItems(["手写体", "打印体"])
        self.combo_style.setCurrentIndex(1 if printed else 0)
        row_style.addWidget(self.combo_style, 1)
        v.addLayout(row_style)

        row_font = QtWidgets.QHBoxLayout()
        self.label_font = QtWidgets.QLabel("打印字体", self)
        row_font.addWidget(self.label_font)
        self.edit_font = QtWidgets.QLineEdit(self)
        self.edit_font.setPlaceholderText("留空使用主字体")
        self.edit_font.setText(font_path)
        row_font.addWidget(self.edit_font, 1)
        self.btn_font = QtWidgets.QPushButton("选择", self)
        row_font.addWidget(self.btn_font)
        v.addLayout(row_font)

        row_size = QtWidgets.QHBoxLayout()
        row_size.addWidget(QtWidgets.QLabel("字号", self))
        self.spin_size = NoWheelSpinBox(self)
        self.spin_size.setRange(0, 300)
        self.spin_size.setValue(int(font_size))
        self.spin_size.setSpecialValueText("跟随主设置")
        self.spin_size.setToolTip("0 表示使用主界面的字体大小")
        row_size.addWidget(self.spin_size)
        row_size.addStretch(1)
        v.addLayout(row_size)

        tip = QtWidgets.QLabel(
            f"提示：打印体不做笔画扰动、排版规整；主字号当前为 {main_font_size}。",
            self,
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #6b7a70;")
        v.addWidget(tip)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

        self.btn_font.clicked.connect(self._choose_font)
        self.combo_style.currentIndexChanged.connect(self._update_font_enabled)
        self._update_font_enabled()

    # ------------------------------------------------------------------
    def _update_font_enabled(self) -> None:
        """仅打印体需要独立字体。"""
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
