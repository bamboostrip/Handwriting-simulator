"""主窗口：组装设计器生成的界面与核心引擎。

负责界面控件与 HandwritingParams 的映射、按钮事件、后台任务调度。
业务逻辑（校验、渲染、导出）全部委托给 core 模块。
"""

from __future__ import annotations

import copy
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QMainWindow,
    QFileDialog,
    QMessageBox,
)

from ..core.engine import HandwritingEngine
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
        self._engine = HandwritingEngine()
        self._worker: RenderWorker | None = None
        # 预览分辨率上限：超过则降采样，保证实时
        self._preview_max_width = 1000
        # 输入防抖：停止输入后自动预览，实现实时效果
        self._auto = False
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(300)
        self._preview_timer.timeout.connect(self._on_auto_preview)

        self._connect_signals()

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
        # 输入文字变化时防抖自动预览
        ui.textEdit.textChanged.connect(self._preview_timer.start)

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
        p.font_path = ui.lineEdit.text().strip()
        p.background_path = ui.lineEdit_2.text().strip()
        p.red = self._int_of(ui.lineEdit_10, 0)
        p.green = self._int_of(ui.lineEdit_11, 0)
        p.blue = self._int_of(ui.lineEdit_12, 0)
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

    def apply_params(self, p: HandwritingParams) -> None:
        """将 HandwritingParams 回填到界面控件。"""
        ui = self._ui
        ui.textEdit.setPlainText(p.text)
        ui.lineEdit.setText(p.font_path)
        ui.lineEdit_2.setText(p.background_path)
        ui.lineEdit_10.setText(str(p.red))
        ui.lineEdit_11.setText(str(p.green))
        ui.lineEdit_12.setText(str(p.blue))
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

    # ------------------------------------------------------------------
    # 按钮事件
    # ------------------------------------------------------------------
    def _on_preview(self) -> None:
        params = self._downsample_preview(self.collect_params())
        try:
            params.validate(require_text=True)
        except HandwritingParams.ValidationError as exc:
            QMessageBox.information(self, "参数检查", str(exc))
            return
        self._auto = False
        self._start_worker(params, "preview")

    def _on_auto_preview(self) -> None:
        """防抖触发的自动预览：参数不完整时静默跳过。"""
        params = self._downsample_preview(self.collect_params())
        try:
            params.validate(require_text=True)
        except HandwritingParams.ValidationError:
            return
        self._auto = True
        self._start_worker(params, "preview")

    def _on_export(self) -> None:
        params = self.collect_params()
        try:
            params.validate(require_text=True)
        except HandwritingParams.ValidationError as exc:
            QMessageBox.information(self, "参数检查", str(exc))
            return
        self._start_worker(params, "export")

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
    def _start_worker(self, params: HandwritingParams, mode: str) -> None:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "提示", "任务进行中，请稍候")
            return
        self._set_busy(True)
        worker = RenderWorker(self._engine, params, mode, self._out_dir)
        worker.succeeded.connect(self._on_success)
        worker.preview_ready.connect(self._on_preview_ready)
        worker.failed.connect(self._on_failure)
        worker.finished.connect(worker.deleteLater)
        # 结束后清空引用，避免下次访问已删除的 C++ 对象
        worker.finished.connect(lambda w=worker: self._forget_worker(w))
        self._worker = worker
        worker.start()

    def _forget_worker(self, worker) -> None:
        """清空已结束 worker 的引用，防止访问已销毁的 C++ 对象。"""
        if self._worker is worker:
            self._worker = None

    def _set_busy(self, busy: bool) -> None:
        self._ui.pushButton_3.setEnabled(not busy)
        self._ui.pushButton_5.setEnabled(not busy)

    def _on_preview_ready(self, pixmap) -> None:
        # PreviewLabel 内部已按比例缩放，直接设置原图即可
        self._ui.label_11.setPixmap(pixmap)

    def _on_success(self, files: list[str]) -> None:
        self._set_busy(False)
        if files:
            QMessageBox.information(self, "完成", f"已导出 {len(files)} 张图片到 {self._out_dir} 目录")
        # 预览场景无需额外提示

    def _on_failure(self, message: str) -> None:
        self._set_busy(False)
        if not self._auto:
            QMessageBox.warning(self, "失败", message)