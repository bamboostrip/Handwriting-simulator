"""主窗口：组装设计器生成的界面与核心引擎。

负责界面控件与 HandwritingParams 的映射、按钮事件、后台任务调度。
业务逻辑（校验、渲染、导出）全部委托给 core 模块。
"""

from __future__ import annotations

import copy
import random
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QMainWindow,
    QFileDialog,
    QMessageBox,
)

from ..core.models import HandwritingParams
from ..core import presets
from .workers import RenderWorker
from .ui import Ui_Form


class MainWindow(QMainWindow):
    """手写模拟器主窗口。"""

    def __init__(self, out_dir: str | Path = "output") -> None:
        super().__init__()
        self._ui = Ui_Form()
        self._ui.setupUi(self)
        self._out_dir = Path(out_dir)
        self._worker: RenderWorker | None = None
        # 最后一次预览使用的随机种子与参数快照：导出复用两者，
        # 保证导出的内容与屏幕上最后一次预览逐像素一致
        self._preview_seed: int | None = None
        self._preview_params: HandwritingParams | None = None
        # 预览全部页与当前页索引
        self._preview_pages: list = []
        self._preview_index = 0
        # 预览分辨率上限：fast 引擎全分辨率渲染已足够快（约 0.15s/页），
        # 上限设高使常见信纸（如 2480 宽）预览与原始程序一样全分辨率渲染，
        # 避免降采样导致笔画变细碎裂、扰动后发丑；仅对超大背景兜底降采样。
        self._preview_max_width = 4096
        # 输入防抖：停止输入后自动预览，实现实时效果
        self._auto = False
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(300)
        self._preview_timer.timeout.connect(self._on_auto_preview)

        self._connect_signals()
        self._update_page_nav()

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------
    def _connect_signals(self) -> None:
        ui = self._ui
        ui.pushButton.clicked.connect(self._choose_font)
        ui.pushButton_2.clicked.connect(self._choose_background)
        ui.pushButton_3.clicked.connect(self._on_preview)
        ui.pushButton_5.clicked.connect(self._on_export)
        ui.pushButton_4.clicked.connect(self._on_save_preset)
        ui.pushButton_6.clicked.connect(self._on_load_preset)
        # 富文本排版工具
        ui.btn_align_left.clicked.connect(lambda: self._set_block_align(0))
        ui.btn_center.clicked.connect(lambda: self._set_block_align(1))
        ui.btn_align_right.clicked.connect(lambda: self._set_block_align(2))
        ui.btn_indent.clicked.connect(self._indent_current_block)
        ui.btn_import_docx.clicked.connect(self._import_docx)
        # 预览翻页
        ui.btn_prev.clicked.connect(self._prev_page)
        ui.btn_next.clicked.connect(self._next_page)
        ui.btn_preview_bg.clicked.connect(self._toggle_preview_bg)
        self._connect_auto_preview()

    def _connect_auto_preview(self) -> None:
        """文本或任意参数变化时防抖自动预览（参数不完整时静默跳过）。

        覆盖文本编辑、字体/背景路径、文字颜色、排版参数、笔画扰动、
        边距与边界提示开关，与手动「预览」按钮效果一致。
        """
        ui = self._ui
        start = lambda *_: self._preview_timer.start()
        for w in (
            ui.textEdit,       # 待处理文本
            ui.lineEdit,       # 字体路径
            ui.lineEdit_2,     # 背景路径
            ui.lineEdit_10,    # 文字颜色
            ui.lineEdit_7,     # 字水平间距
            ui.lineEdit_8,     # 字竖直间距
            ui.lineEdit_9,     # 字体大小
            ui.lineEdit_3,     # 上边距
            ui.lineEdit_4,     # 下边距
            ui.lineEdit_5,     # 左边距
            ui.lineEdit_6,     # 右边距
            ui.lineEdit_13,    # 边界提示颜色
        ):
            w.textChanged.connect(start)
        for w in (
            ui.spinBox,        # 字间距 σ
            ui.spinBox_2,      # 行距 σ
            ui.spinBox_3,      # 字号 σ
            ui.spinBox_5,      # 笔画水平位移
            ui.spinBox_4,      # 笔画竖直位移
        ):
            w.valueChanged.connect(start)
        ui.doubleSpinBox_6.valueChanged.connect(start)  # 笔画旋转
        ui.checkBox_bounds.toggled.connect(start)       # 边界提示开关

    # ------------------------------------------------------------------
    # 富文本排版工具
    # ------------------------------------------------------------------
    def _set_block_align(self, flag: int) -> None:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QTextBlockFormat

        cursor = self._ui.textEdit.textCursor()
        fmt = QTextBlockFormat()
        fmt.setAlignment((
            Qt.AlignmentFlag.AlignLeft,
            Qt.AlignmentFlag.AlignCenter,
            Qt.AlignmentFlag.AlignRight,
        )[flag])
        cursor.mergeBlockFormat(fmt)
        # 段落格式变化后自动预览
        self._preview_timer.start()

    def _indent_current_block(self) -> None:
        from PyQt6.QtGui import QTextBlockFormat

        cursor = self._ui.textEdit.textCursor()
        fmt = QTextBlockFormat()
        fmt.setTextIndent(2 * self._int_of(self._ui.lineEdit_9, 36))
        cursor.mergeBlockFormat(fmt)
        # 段落格式变化后自动预览
        self._preview_timer.start()

    def _set_paragraphs(self, paras) -> None:
        """将段落列表回填为富文本。"""
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QTextBlockFormat, QTextCursor

        editor = self._ui.textEdit
        editor.clear()
        cursor = QTextCursor(editor.document())
        for idx, para in enumerate(paras):
            if idx:
                cursor.insertBlock()
            fmt = QTextBlockFormat()
            if para.align == "center":
                fmt.setAlignment(Qt.AlignmentFlag.AlignCenter)
            elif para.align == "right":
                fmt.setAlignment(Qt.AlignmentFlag.AlignRight)
            if para.first_line_indent:
                fmt.setTextIndent(para.first_line_indent)
            cursor.setBlockFormat(fmt)
            cursor.insertText(para.text)
        editor.setTextCursor(cursor)

    def _import_docx(self) -> None:
        from ..core.docx_io import load_paragraphs

        path, _ = QFileDialog.getOpenFileName(self, "导入 docx", "", "Word 文档 (*.docx)")
        if not path:
            return
        try:
            paras = load_paragraphs(path, self._int_of(self._ui.lineEdit_9, 36))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        self._set_paragraphs(paras)

    # ------------------------------------------------------------------
    # 文件选择
    # ------------------------------------------------------------------
    def _choose_font(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择字体", "", "字体 (*.ttf *.ttc *.otf)")
        if path:
            self._ui.lineEdit.setText(path)

    def _choose_background(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择背景", "", "图片 (*.png *.jpg *.jpeg *.bmp)")
        if path:
            self._ui.lineEdit_2.setText(path)

    # ------------------------------------------------------------------
    # 参数收集（界面 -> 模型）
    # ------------------------------------------------------------------
    def collect_params(self) -> HandwritingParams:
        """读取界面控件值，映射为 HandwritingParams。"""
        ui = self._ui
        p = HandwritingParams()
        p.text = ui.textEdit.toPlainText()
        p.paragraphs = self._collect_paragraphs()
        p.font_path = ui.lineEdit.text().strip()
        p.background_path = ui.lineEdit_2.text().strip()
        p.red, p.green, p.blue = self._color_of(ui.lineEdit_10, (0, 0, 0))
        p.word_spacing = self._int_of(ui.lineEdit_7, p.word_spacing)
        p.word_spacing_sigma = self._int_of(ui.spinBox, p.word_spacing_sigma)
        p.line_spacing = self._int_of(ui.lineEdit_8, p.line_spacing)
        p.line_spacing_sigma = self._int_of(ui.spinBox_2, p.line_spacing_sigma)
        p.font_size = self._int_of(ui.lineEdit_9, p.font_size)
        p.font_size_sigma = self._int_of(ui.spinBox_3, p.font_size_sigma)
        p.perturb_x_sigma = self._int_of(ui.spinBox_5, p.perturb_x_sigma)
        p.perturb_y_sigma = self._int_of(ui.spinBox_4, p.perturb_y_sigma)
        p.perturb_theta_sigma = self._float_of(ui.doubleSpinBox_6, p.perturb_theta_sigma)
        p.top_margin = self._int_of(ui.lineEdit_3, p.top_margin)
        p.left_margin = self._int_of(ui.lineEdit_5, p.left_margin)
        p.right_margin = self._int_of(ui.lineEdit_6, p.right_margin)
        p.bottom_margin = self._int_of(ui.lineEdit_4, p.bottom_margin)
        return p

    def _collect_paragraphs(self):
        """从富文本编辑器的块格式收集段落。

        空行保留为空段落，使渲染结果与纯文本路径的空行行为一致；
        全文为空时返回 []，交由校验提示未输入文字。
        """
        from PyQt6.QtCore import Qt

        from ..core.models import Paragraph

        doc = self._ui.textEdit.document()
        paras: list[Paragraph] = []
        has_text = False
        for i in range(doc.blockCount()):
            block = doc.findBlockByNumber(i)
            # 保留原始文本（含首尾空格）：左对齐时前导空格用于手动定位，
            # 右对齐时尾部空格用于把文字从右缘顶进来
            raw = block.text()
            if raw.strip():
                has_text = True
            fmt = block.blockFormat()
            alignment = fmt.alignment()
            if alignment & Qt.AlignmentFlag.AlignCenter:
                align = "center"
            elif alignment & Qt.AlignmentFlag.AlignRight:
                align = "right"
            else:
                align = "left"
            paras.append(
                Paragraph(text=raw, align=align, first_line_indent=int(fmt.textIndent()))
            )
        return paras if has_text else []

    def apply_params(self, p: HandwritingParams) -> None:
        """将 HandwritingParams 回填到界面控件。"""
        ui = self._ui
        # 预设不含文本内容：仅当预设自带文本时才回填，否则保留当前输入
        if p.paragraphs:
            self._set_paragraphs(p.paragraphs)
        elif p.text:
            ui.textEdit.setPlainText(p.text)
        ui.lineEdit.setText(p.font_path)
        ui.lineEdit_2.setText(p.background_path)
        ui.lineEdit_10.setText(p.color)
        ui.lineEdit_7.setText(str(p.word_spacing))
        ui.spinBox.setValue(int(p.word_spacing_sigma))
        ui.lineEdit_8.setText(str(p.line_spacing))
        ui.spinBox_2.setValue(int(p.line_spacing_sigma))
        ui.lineEdit_9.setText(str(p.font_size))
        ui.spinBox_3.setValue(int(p.font_size_sigma))
        ui.spinBox_5.setValue(int(p.perturb_x_sigma))
        ui.spinBox_4.setValue(int(p.perturb_y_sigma))
        ui.doubleSpinBox_6.setValue(float(p.perturb_theta_sigma))
        ui.lineEdit_3.setText(str(p.top_margin))
        ui.lineEdit_5.setText(str(p.left_margin))
        ui.lineEdit_6.setText(str(p.right_margin))
        ui.lineEdit_4.setText(str(p.bottom_margin))

    @staticmethod
    def _int_of(widget, default: int) -> int:
        try:
            return int(widget.text().strip())
        except (ValueError, AttributeError):
            return int(default)

    @staticmethod
    def _float_of(widget, default: float) -> float:
        try:
            return float(widget.text().strip())
        except (ValueError, AttributeError):
            return float(default)

    @staticmethod
    def _color_of(widget, default: tuple[int, int, int]) -> tuple[int, int, int]:
        """解析 #RRGGBB 颜色输入；格式非法时回退到默认三元组。"""
        from ..core.models import parse_color

        try:
            return parse_color(widget.text())
        except (ValueError, AttributeError):
            return default

    # ------------------------------------------------------------------
    # 按钮事件
    # ------------------------------------------------------------------
    def _on_preview(self) -> None:
        raw = self.collect_params()
        params = self._downsample_preview(raw)
        try:
            params.validate(require_text=True)
        except HandwritingParams.ValidationError as exc:
            QMessageBox.information(self, "参数检查", str(exc))
            return
        self._auto = False
        seed = self._new_seed()
        if self._start_worker(params, "preview", seed=seed):
            self._remember_preview(seed, raw)

    def _on_auto_preview(self) -> None:
        """防抖触发的自动预览：参数不完整时静默跳过。"""
        raw = self.collect_params()
        params = self._downsample_preview(raw)
        try:
            params.validate(require_text=True)
        except HandwritingParams.ValidationError:
            return
        self._auto = True
        # 连续输入时上一次渲染可能仍在进行，静默跳过本次，避免弹窗打断输入；
        # 注意：被跳过的预览不得更新 seed/参数快照，否则屏幕内容仍是旧预览，
        # 而导出却用了新 seed+新参数，导致预览与导出对不上
        seed = self._new_seed()
        if self._start_worker(params, "preview", quiet=True, seed=seed):
            self._remember_preview(seed, raw)

    def _new_seed(self) -> int:
        """生成一个新随机种子（暂不记录，仅渲染真正启动后生效）。"""
        return random.SystemRandom().randrange(2**31)

    def _remember_preview(self, seed: int, raw: HandwritingParams) -> None:
        """记录最后一次预览的种子与参数快照（导出复用，保证与预览一致）。

        快照保存原始（未降采样）参数：导出始终全分辨率渲染；
        常见背景（≤4096px）下预览未降采样，导出与预览逐像素一致。
        """
        self._preview_seed = seed
        self._preview_params = raw

    def _on_export(self) -> None:
        # 优先复用最后一次预览的参数与种子：导出的内容与屏幕上的
        # 预览一致；从未预览过时用当前界面参数，每次导出保持随机
        params = self._preview_params if self._preview_params is not None else self.collect_params()
        try:
            params.validate(require_text=True)
        except HandwritingParams.ValidationError as exc:
            QMessageBox.information(self, "参数检查", str(exc))
            return
        self._start_worker(params, "export", seed=self._preview_seed)

    def _on_save_preset(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "保存预设", "preset.json", "预设 (*.json);;旧版文本 (*.txt *.preset)"
        )
        if not path:
            return
        try:
            presets.save(path, self.collect_params())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        QMessageBox.information(self, "完成", "预设已保存")

    def _on_load_preset(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "载入预设", "", "预设 (*.json *.txt *.preset)"
        )
        if not path:
            return
        try:
            self.apply_params(presets.load(path))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "载入失败", str(exc))

    # ------------------------------------------------------------------
    # 预览降采样
    # ------------------------------------------------------------------
    _SPATIAL_ATTRS = (
        "font_size", "line_spacing", "word_spacing",
        "left_margin", "right_margin", "top_margin", "bottom_margin",
        "word_spacing_sigma", "line_spacing_sigma", "font_size_sigma",
        "perturb_x_sigma", "perturb_y_sigma",
    )

    def _downsample_preview(self, params: HandwritingParams) -> HandwritingParams:
        """预览时若背景过大则降采样并按比例缩放参数，保证实时性。

        不影响导出（导出使用原始参数）。
        """
        bg_path = Path(params.background_path)
        if not bg_path.is_file():
            return params
        try:
            with Image.open(bg_path) as bg:
                width, height = bg.size
        except Exception:  # noqa: BLE001
            return params
        if width <= self._preview_max_width:
            return params

        scale = self._preview_max_width / width
        new_width = self._preview_max_width
        new_height = max(1, round(height * scale))
        with Image.open(bg_path) as bg:
            thumb = bg.resize((new_width, new_height), Image.Resampling.LANCZOS)

        cache_dir = self._out_dir / ".preview_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        thumb_path = cache_dir / "bg_preview.png"
        thumb.save(thumb_path)

        preview = copy.copy(params)
        preview.background_path = str(thumb_path)
        # 保留浮点缩放值（不取整）：各参数独立取整会产生每行 ≤1px 的
        # 舍入误差并随行数累积，导致预览文字与背景行线逐渐错位。
        # 导出仍使用原始整数参数，不受影响。
        for attr in self._SPATIAL_ATTRS:
            setattr(preview, attr, getattr(preview, attr) * scale)
        preview.font_size = max(1.0, preview.font_size)
        return preview

    # ------------------------------------------------------------------
    # 后台任务
    # ------------------------------------------------------------------
    def _start_worker(
        self, params: HandwritingParams, mode: str, quiet: bool = False, seed: object | None = None
    ) -> bool:
        """启动后台任务；worker 忙时跳过并返回 False（调用方据此决定是否记录状态）。"""
        if self._worker is not None and self._worker.isRunning():
            if not quiet:
                QMessageBox.information(self, "提示", "任务进行中，请稍候")
            return False
        self._set_busy(True)
        bounds = None
        ui = self._ui
        if mode == "preview" and ui.checkBox_bounds.isChecked():
            bounds = self._color_of(ui.lineEdit_13, (76, 166, 166))
        worker = RenderWorker(params, mode, self._out_dir, bounds=bounds, seed=seed)
        worker.succeeded.connect(self._on_success)
        worker.preview_ready.connect(self._on_preview_ready)
        worker.failed.connect(self._on_failure)
        worker.finished.connect(worker.deleteLater)
        # 结束后清空引用，避免下次访问已删除的 C++ 对象
        worker.finished.connect(lambda w=worker: self._forget_worker(w))
        self._worker = worker
        worker.start()
        return True

    def _forget_worker(self, worker) -> None:
        """清空已结束 worker 的引用，防止访问已销毁的 C++ 对象。"""
        if self._worker is worker:
            self._worker = None

    def _set_busy(self, busy: bool) -> None:
        self._ui.pushButton_3.setEnabled(not busy)
        self._ui.pushButton_5.setEnabled(not busy)

    def _on_preview_ready(self, pages) -> None:
        # 预览全部页已生成，重置到第一页并刷新
        self._preview_pages = list(pages)
        self._preview_index = 0
        self._show_page(0)

    def _show_page(self, index: int) -> None:
        """显示指定页并更新翻页状态。"""
        if not self._preview_pages:
            self._update_page_nav()
            return
        index = max(0, min(index, len(self._preview_pages) - 1))
        self._preview_index = index
        # PreviewLabel 内部已按比例缩放，直接设置原图即可
        self._ui.label_11.setPixmap(self._preview_pages[index])
        self._update_page_nav()

    # 预览底色候选：一浅一深差异大，背景图撞色时可切换区分
    _PREVIEW_BG_COLORS = ("#c8d0ca", "#565b56")

    def _toggle_preview_bg(self) -> None:
        """循环切换预览区底色，避免背景图与底色撞色时边界不可辨。"""
        idx = (getattr(self, "_preview_bg_idx", 0) + 1) % len(self._PREVIEW_BG_COLORS)
        self._preview_bg_idx = idx
        color = self._PREVIEW_BG_COLORS[idx]
        self._ui.label_11.setStyleSheet(
            f"PreviewLabel {{ background: {color};"
            " border: 1px solid #d3ded6; border-radius: 6px; }"
        )

    def _prev_page(self) -> None:
        self._show_page(self._preview_index - 1)

    def _next_page(self) -> None:
        self._show_page(self._preview_index + 1)

    def _update_page_nav(self) -> None:
        """更新页码标签与翻页按钮可用状态。"""
        total = len(self._preview_pages)
        if total == 0:
            self._ui.label_page.setText("第 1 / 1 页")
            self._ui.btn_prev.setEnabled(False)
            self._ui.btn_next.setEnabled(False)
            return
        self._ui.label_page.setText(f"第 {self._preview_index + 1} / {total} 页")
        self._ui.btn_prev.setEnabled(self._preview_index > 0)
        self._ui.btn_next.setEnabled(self._preview_index < total - 1)

    def _on_success(self, files: list[str]) -> None:
        self._set_busy(False)
        if files:
            QMessageBox.information(self, "完成", f"已导出 {len(files)} 张图片到 {self._out_dir} 目录")
        # 预览场景无需额外提示

    def _on_failure(self, message: str) -> None:
        self._set_busy(False)
        if not self._auto:
            QMessageBox.warning(self, "失败", message)