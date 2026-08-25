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

from ..core.models import HandwritingParams, Paragraph, TextRegion
from ..core import doc_render
from ..core import presets
from ..core.paths import assets_root, ensure_assets_dirs
from .region_dialog import RegionDialog
from .workers import RenderWorker
from .ui import Ui_Form


def _is_under_assets(path: Path) -> bool:
    """判断路径是否位于资产根目录（exe 旁）内。"""
    try:
        path.resolve().relative_to(Path(assets_root()).resolve())
    except (ValueError, OSError):
        return False
    return True


class MainWindow(QMainWindow):
    """手写模拟器主窗口。"""

    # 预设下拉框占位项：选中时不触发加载
    _PRESET_PLACEHOLDER = "— 选择预设 —"

    def __init__(self, out_dir: str | Path = "output") -> None:
        super().__init__()
        self._ui = Ui_Form()
        self._ui.setupUi(self)
        self._out_dir = Path(out_dir)
        self._worker: RenderWorker | None = None
        # 便携模式：确保 exe 旁 fonts/backgrounds/presets 目录存在
        ensure_assets_dirs()
        # 最后一次预览使用的随机种子与参数快照：导出复用两者，
        # 保证导出的内容与屏幕上最后一次预览逐像素一致
        self._preview_seed: int | None = None
        self._preview_params: HandwritingParams | None = None
        # 预览全部页与当前页索引
        self._preview_pages: list = []
        self._preview_index = 0
        # 框选文字区域（原始背景像素坐标）与最近一次预览的降采样比例
        self._regions: list[TextRegion] = []
        self._preview_scale = 1.0
        # 当前正在预览图上拖动/缩放调整的区域行号（None = 非调整态）
        self._editing_row: int | None = None
        # 文档底图：导入的 PDF/DOCX 打印预览逐页 PNG（None = 未使用）
        self._doc_pages: list[str] | None = None
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
        self._refresh_preset_combo()
        self._update_page_nav()

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------
    def _connect_signals(self) -> None:
        ui = self._ui
        ui.pushButton.clicked.connect(self._choose_font)
        ui.pushButton_2.clicked.connect(self._choose_background)
        ui.pushButton_8.clicked.connect(self._import_document)
        # 手动改背景路径时自动失效文档底图状态
        ui.lineEdit_2.textChanged.connect(self._sync_doc_state)
        ui.pushButton_3.clicked.connect(self._on_preview)
        ui.pushButton_5.clicked.connect(self._on_export)
        ui.pushButton_7.clicked.connect(self._on_export_pdf)
        ui.pushButton_4.clicked.connect(self._on_save_preset)
        ui.pushButton_6.clicked.connect(self._on_load_preset)
        ui.combo_preset.currentIndexChanged.connect(self._on_preset_combo_changed)
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
        # 框选文字区域
        ui.btn_select_region.toggled.connect(self._on_region_mode_toggled)
        ui.label_11.region_selected.connect(self._on_region_selected)
        ui.btn_region_delete.clicked.connect(self._delete_selected_region)
        ui.btn_region_clear.clicked.connect(self._clear_regions)
        ui.region_list.itemDoubleClicked.connect(self._edit_region)
        # 悬浮区域列表项 -> 预览图临时高亮对应框选区域
        ui.region_list.setMouseTracking(True)
        ui.region_list.itemEntered.connect(self._on_region_hover)
        ui.region_list.viewport().installEventFilter(self)
        # 单击列表项 -> 预览图出现可拖动/缩放的调整框
        ui.region_list.itemClicked.connect(self._on_region_item_clicked)
        ui.label_11.region_geometry_changed.connect(self._on_region_geometry_changed)
        ui.label_11.region_edit_cancelled.connect(self._on_region_edit_cancelled)
        # 写错字：滑块数值同步到百分比标签
        ui.miswrite_rate_slider.valueChanged.connect(self._update_miswrite_rate_label)
        self._update_miswrite_rate_label(ui.miswrite_rate_slider.value())
        self._connect_auto_preview()

    def _update_miswrite_rate_label(self, value: int) -> None:
        """滑块值（0~300）同步为百分比显示（0.0%~30.0%）。"""
        self._ui.label_miswrite_rate_value.setText(f"{value / 10.0:.1f}%")

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
        ui.miswrite_rate_slider.valueChanged.connect(start)   # 错字率
        ui.miswrite_mode_combo.currentIndexChanged.connect(start)   # 重写方式
        ui.miswrite_style_combo.currentIndexChanged.connect(start)  # 涂改方式

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
    # 框选文字区域（手写 / 打印混排）
    # ------------------------------------------------------------------
    def _on_region_mode_toggled(self, checked: bool) -> None:
        """预览区进入 / 退出框选模式。"""
        self._ui.label_11.set_region_mode(checked)

    def _on_region_selected(self, rect) -> None:
        """预览图上框选完成：换算回原始背景坐标并弹出编辑对话框。"""
        from PyQt6.QtWidgets import QDialog

        # 预览图坐标 -> 原始背景坐标（scale = 原始/预览，≥1）
        scale = self._preview_scale_now()
        x = max(0, round(rect.x() * scale))
        y = max(0, round(rect.y() * scale))
        w = max(8, round(rect.width() * scale))
        h = max(8, round(rect.height() * scale))
        # 钳制到背景图范围内兜底，避免比例异常时产生越界坐标
        try:
            with Image.open(self._ui.lineEdit_2.text().strip()) as bg:
                bw, bh = bg.size
            x, y = min(x, max(0, bw - 8)), min(y, max(0, bh - 8))
            w, h = min(w, bw - x), min(h, bh - y)
        except Exception:  # noqa: BLE001
            pass
        dlg = RegionDialog(
            self,
            main_font_size=self._int_of(self._ui.lineEdit_9, 36),
            page=self._preview_index + 1,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        text = dlg.region_text
        if not text.strip():
            return
        region = TextRegion(
            x=x, y=y, w=w, h=h,
            text=text,
            font_path=dlg.region_font_path,
            printed=dlg.region_printed,
            font_size=dlg.region_font_size,
            page=dlg.region_page,
            align=dlg.region_align,
            indent_em=dlg.region_indent_em,
            paragraphs=dlg.region_paragraphs,
            word_spacing=dlg.region_word_spacing,
            line_spacing=dlg.region_line_spacing,
            font_size_sigma=dlg.region_font_size_sigma,
            word_spacing_sigma=dlg.region_word_spacing_sigma,
            line_spacing_sigma=dlg.region_line_spacing_sigma,
            perturb_x_sigma=dlg.region_perturb_x_sigma,
            perturb_y_sigma=dlg.region_perturb_y_sigma,
            perturb_theta_sigma=dlg.region_perturb_theta_sigma,
            miswrite_rate=dlg.region_miswrite_rate,
            miswrite_strikeout_style=dlg.region_miswrite_strikeout_style,
            color=dlg.region_color,
            margin_top=dlg.region_margin_top,
            margin_bottom=dlg.region_margin_bottom,
            margin_left=dlg.region_margin_left,
            margin_right=dlg.region_margin_right,
        )
        try:
            region_font_check = (
                f"文字区域字体文件不存在：{region.font_path}"
                if region.font_path
                and not Path(region.font_path).is_file()
                else ""
            )
        except OSError:
            region_font_check = ""
        if region_font_check:
            QMessageBox.warning(self, "字体检查", region_font_check)
            return
        self._regions.append(region)
        self._refresh_region_list()
        self._preview_timer.start()

    def _refresh_region_list(self) -> None:
        """刷新区域列表（红框不再常驻，仅悬浮列表项时临时高亮）。"""
        from PyQt6.QtWidgets import QListWidgetItem

        lst = self._ui.region_list
        lst.blockSignals(True)
        lst.clear()
        for i, region in enumerate(self._regions, start=1):
            tag = " [已自定义]" if region.has_overrides() else ""
            item = QListWidgetItem(f"{region.label(i)}{tag}")
            if region.has_overrides():
                item.setToolTip("包含独立自定义排版/扰动/颜色/边距覆盖项")
            lst.addItem(item)
        lst.blockSignals(False)

    def _preview_scale_now(self) -> float:
        """当前预览位图相对原始背景的缩放比，按两者实际宽度计算。

        不依赖缓存的 _preview_scale，避免切换背景后比例失准；
        背景不可读时退回最近一次预览记录的比例。
        """
        pm = self._ui.label_11.pixmap()
        bg_path = self._ui.lineEdit_2.text().strip()
        if pm is not None and not pm.isNull() and bg_path:
            try:
                with Image.open(bg_path) as bg:
                    bg_w = bg.width
                if bg_w > 0 and pm.width() > 0:
                    return bg_w / pm.width()
            except Exception:  # noqa: BLE001
                pass
        # 回退：_preview_scale 是预览/原始（<1），取倒数统一为原始/预览
        return 1.0 / self._preview_scale if self._preview_scale else 1.0

    def _show_region_highlight(self, row: int | None) -> None:
        """在预览图上临时高亮指定区域；row 为 None 时清除高亮。"""
        from PyQt6.QtCore import QRect

        if row is None or not 0 <= row < len(self._regions):
            self._ui.label_11.set_region_rects([])
            return
        r = self._regions[row]
        s = self._preview_scale_now()
        self._ui.label_11.set_region_rects([
            QRect(
                round(r.x / s), round(r.y / s),
                max(1, round(r.w / s)), max(1, round(r.h / s)),
            )
        ])

    def _on_region_hover(self, item) -> None:
        """悬浮区域列表项：在预览图上高亮对应框选区域。

        仅当区域所在页与当前预览页一致时高亮，避免误导；
        单击列表项会自动跳到区域所在页。
        """
        if self._editing_row is not None:
            return  # 调整态下不叠加悬浮高亮，避免与调整框混淆
        row = self._ui.region_list.row(item)
        if not 0 <= row < len(self._regions):
            return
        if self._regions[row].page - 1 != self._preview_index:
            return
        self._show_region_highlight(row)

    def _on_region_item_clicked(self, item) -> None:
        """单击列表项：跳到区域所在页并显示可拖动/缩放的调整框。"""
        row = self._ui.region_list.row(item)
        if not 0 <= row < len(self._regions):
            return
        from PyQt6.QtCore import QRect

        region = self._regions[row]
        self._editing_row = row
        # 区域在其他页时先翻过去，调整框才有意义
        if region.page - 1 != self._preview_index:
            self._show_page(region.page - 1)
        s = self._preview_scale_now()
        self._ui.label_11.begin_region_edit(QRect(
            round(region.x / s), round(region.y / s),
            max(1, round(region.w / s)), max(1, round(region.h / s)),
        ))

    def _on_region_geometry_changed(self, rect) -> None:
        """预览图上拖动/缩放完成：写回区域坐标并自动刷新预览。"""
        if self._editing_row is None or not 0 <= self._editing_row < len(self._regions):
            return
        scale = self._preview_scale_now()
        x = max(0, round(rect.x() * scale))
        y = max(0, round(rect.y() * scale))
        w = max(4, round(rect.width() * scale))
        h = max(4, round(rect.height() * scale))
        # 钳制到背景图范围内兜底
        try:
            with Image.open(self._ui.lineEdit_2.text().strip()) as bg:
                bw, bh = bg.size
            x, y = min(x, max(0, bw - 4)), min(y, max(0, bh - 4))
            w, h = min(w, bw - x), min(h, bh - y)
        except Exception:  # noqa: BLE001
            pass
        region = self._regions[self._editing_row]
        region.x, region.y, region.w, region.h = x, y, w, h
        self._refresh_region_list()
        self._preview_timer.start()

    def _on_region_edit_cancelled(self) -> None:
        """预览图上结束了区域调整（Esc / 点击框外）。"""
        self._editing_row = None

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        from PyQt6.QtCore import QEvent

        if obj is self._ui.region_list.viewport() and event.type() == QEvent.Type.Leave:
            self._show_region_highlight(None)
        return super().eventFilter(obj, event)

    def _delete_selected_region(self) -> None:
        row = self._ui.region_list.currentRow()
        if 0 <= row < len(self._regions):
            self._regions.pop(row)
            self._editing_row = None
            self._ui.label_11.end_region_edit()
            self._show_region_highlight(None)
            self._refresh_region_list()
            self._preview_timer.start()

    def _clear_regions(self) -> None:
        if not self._regions:
            return
        self._regions.clear()
        self._editing_row = None
        self._ui.label_11.end_region_edit()
        self._show_region_highlight(None)
        self._refresh_region_list()
        self._preview_timer.start()

    def _edit_region(self, item) -> None:
        """双击列表项重新编辑该区域。"""
        from PyQt6.QtWidgets import QDialog

        row = self._ui.region_list.row(item)
        if not 0 <= row < len(self._regions):
            return
        region = self._regions[row]
        dlg = RegionDialog(
            self,
            title=f"编辑文字区域 {row + 1}",
            text=region.text,
            printed=region.printed,
            font_path=region.font_path,
            font_size=region.font_size,
            main_font_size=self._int_of(self._ui.lineEdit_9, 36),
            page=region.page,
            align=region.align,
            indent_em=region.indent_em,
            paragraphs=region.paragraphs,
            word_spacing=region.word_spacing,
            line_spacing=region.line_spacing,
            font_size_sigma=region.font_size_sigma,
            word_spacing_sigma=region.word_spacing_sigma,
            line_spacing_sigma=region.line_spacing_sigma,
            perturb_x_sigma=region.perturb_x_sigma,
            perturb_y_sigma=region.perturb_y_sigma,
            perturb_theta_sigma=region.perturb_theta_sigma,
            miswrite_rate=region.miswrite_rate,
            miswrite_strikeout_style=region.miswrite_strikeout_style,
            color=region.color,
            margin_top=region.margin_top,
            margin_bottom=region.margin_bottom,
            margin_left=region.margin_left,
            margin_right=region.margin_right,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if not dlg.region_text.strip():
            return
        region.text = dlg.region_text
        region.printed = dlg.region_printed
        region.font_path = dlg.region_font_path
        region.font_size = dlg.region_font_size
        new_page = max(1, dlg.region_page)
        page_changed = new_page != region.page
        region.page = new_page
        region.align = dlg.region_align
        region.indent_em = dlg.region_indent_em
        region.paragraphs = dlg.region_paragraphs
        region.word_spacing = dlg.region_word_spacing
        region.line_spacing = dlg.region_line_spacing
        region.font_size_sigma = dlg.region_font_size_sigma
        region.word_spacing_sigma = dlg.region_word_spacing_sigma
        region.line_spacing_sigma = dlg.region_line_spacing_sigma
        region.perturb_x_sigma = dlg.region_perturb_x_sigma
        region.perturb_y_sigma = dlg.region_perturb_y_sigma
        region.perturb_theta_sigma = dlg.region_perturb_theta_sigma
        region.miswrite_rate = dlg.region_miswrite_rate
        region.miswrite_strikeout_style = dlg.region_miswrite_strikeout_style
        region.color = dlg.region_color
        region.margin_top = dlg.region_margin_top
        region.margin_bottom = dlg.region_margin_bottom
        region.margin_left = dlg.region_margin_left
        region.margin_right = dlg.region_margin_right
        self._refresh_region_list()
        if page_changed and region.page - 1 != self._preview_index:
            # 页码被改走时结束调整态，避免调整框留在旧页上误导
            self._editing_row = None
            self._ui.label_11.end_region_edit()
        self._preview_timer.start()

    # ------------------------------------------------------------------
    # 文件选择
    # ------------------------------------------------------------------
    def _choose_font(self) -> None:
        # 默认打开 exe 旁的 fonts/ 目录，便于用户放入字体后直接选择
        start_dir = str(Path(assets_root()) / "fonts")
        path, _ = QFileDialog.getOpenFileName(self, "选择字体", start_dir, "字体 (*.ttf *.ttc *.otf)")
        if path:
            self._ui.lineEdit.setText(path)

    def _choose_background(self) -> None:
        # 默认打开 exe 旁的 backgrounds/ 目录
        start_dir = str(Path(assets_root()) / "backgrounds")
        path, _ = QFileDialog.getOpenFileName(self, "选择背景", start_dir, "图片 (*.png *.jpg *.jpeg *.bmp *.webp)")
        if path:
            self._doc_pages = None
            self._ui.lineEdit_14.clear()
            self._ui.lineEdit_2.setText(path)

    # ------------------------------------------------------------------
    # 文档底图（PDF / Word 打印预览）
    # ------------------------------------------------------------------
    def _import_document(self) -> None:
        """导入 PDF/DOCX：把打印预览逐页渲染为背景（替换当前背景）。"""
        start_dir = str(Path(assets_root()) / "backgrounds")
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 PDF / Word 文档", start_dir, "文档 (*.pdf *.docx)"
        )
        if not path:
            return
        ret = QMessageBox.question(
            self,
            "导入文档",
            "导入的文档会按打印预览逐页生成为背景，\n"
            "替换当前选择的背景图片。是否继续？",
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        out_dir = Path(self._out_dir) / ".preview_cache" / "doc_bg"
        try:
            pages = doc_render.document_to_page_images(path, out_dir, dpi=200)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        self._doc_pages = [str(p) for p in pages]
        self._ui.lineEdit_14.setText(f"{Path(path).name}（{len(pages)} 页）")
        self._ui.lineEdit_2.setText(str(pages[0]))
        self._preview_timer.start()

    def _sync_doc_state(self) -> None:
        """背景路径不再指向文档首页时（手动改选），清除文档底图状态。"""
        if self._doc_pages and self._ui.lineEdit_2.text().strip() != self._doc_pages[0]:
            self._doc_pages = None
            self._ui.lineEdit_14.clear()

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
        # 文档多页底图：仅当背景仍指向文档首页时生效（手动改背景即失效）
        if self._doc_pages and p.background_path == self._doc_pages[0]:
            p.background_pages = list(self._doc_pages)
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
        p.miswrite_rate = ui.miswrite_rate_slider.value() / 1000.0
        p.miswrite_rewrite_mode = ("above", "rewrite")[ui.miswrite_mode_combo.currentIndex()]
        p.miswrite_strikeout_style = ("line", "double_line", "slash", "cross")[
            ui.miswrite_style_combo.currentIndex()
        ]
        # 框选区域：深拷贝快照，避免渲染线程读到后续被编辑的同一对象及其段落列表
        p.regions = [copy.deepcopy(r) for r in self._regions]
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
        ui.miswrite_rate_slider.setValue(round(p.miswrite_rate * 1000))
        ui.miswrite_mode_combo.setCurrentIndex(0 if p.miswrite_rewrite_mode == "above" else 1)
        ui.miswrite_style_combo.setCurrentIndex(
            ("line", "double_line", "slash", "cross").index(p.miswrite_strikeout_style)
        )

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
            # 允许纯背景预览：只要背景就绪即可预览，方便用户空背景框选
            params.validate(require_text=False)
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
            # 与手动预览一致：纯背景（无文字）也允许自动预览
            params.validate(require_text=False)
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

    def _on_export_pdf(self) -> None:
        # 与图片导出相同：复用最后一次预览的参数与种子，保证与预览一致
        params = self._preview_params if self._preview_params is not None else self.collect_params()
        try:
            params.validate(require_text=True)
        except HandwritingParams.ValidationError as exc:
            QMessageBox.information(self, "参数检查", str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 PDF", str(self._out_dir / "handwrite.pdf"), "PDF (*.pdf)"
        )
        if not path:
            return
        self._start_worker(params, "pdf", seed=self._preview_seed, out_pdf=path)

    def _on_save_preset(self) -> None:
        # 默认保存到 exe 旁的 presets/ 目录，文件名可自行编辑
        default_path = str(Path(assets_root()) / "presets" / "preset.json")
        path, _ = QFileDialog.getSaveFileName(
            self, "保存预设", default_path, "预设 (*.json);;旧版文本 (*.txt *.preset)"
        )
        if not path:
            return
        try:
            presets.save(path, self.collect_params())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        # 保存到预设文件夹时同步到快捷下拉框
        self._refresh_preset_combo(select=path)
        QMessageBox.information(self, "完成", "预设已保存")

    def _on_load_preset(self) -> None:
        # 默认打开 exe 旁的 presets/ 目录，也允许选择任意位置
        start_dir = str(Path(assets_root()) / "presets")
        path, _ = QFileDialog.getOpenFileName(
            self, "载入预设", start_dir, "预设 (*.json *.txt *.preset)"
        )
        if not path:
            return
        try:
            self.apply_params(presets.load(path))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "载入失败", str(exc))
            return
        # 载入的预设位于预设文件夹内时，同步高亮下拉框
        if _is_under_assets(Path(path)):
            self._refresh_preset_combo(select=path)

    def _refresh_preset_combo(self, select: str | None = None) -> None:
        """扫描预设文件夹，刷新快捷切换下拉框。

        select 为要选中的预设文件路径（位于预设文件夹内时高亮）。
        """
        combo = self._ui.combo_preset
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(self._PRESET_PLACEHOLDER)
        preset_dir = Path(assets_root()) / "presets"
        files = sorted(
            p for p in preset_dir.iterdir()
            if p.is_file() and p.suffix.lower() in (".json", ".preset", ".txt")
        )
        for p in files:
            combo.addItem(p.stem, userData=str(p))
        if select:
            target = Path(select).resolve()
            for i in range(1, combo.count()):
                if Path(combo.itemData(i)).resolve() == target:
                    combo.setCurrentIndex(i)
                    break
            else:
                combo.setCurrentIndex(0)
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _on_preset_combo_changed(self, index: int) -> None:
        """下拉框切换预设：跳过占位项，载入并应用到界面。"""
        if index <= 0:
            return
        path = self._ui.combo_preset.itemData(index)
        if not path or not Path(path).is_file():
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

        不影响导出（导出使用原始参数）。同时记录缩放比例，
        供框选区域在预览坐标与原始背景坐标之间换算。
        """
        bg_path = Path(params.background_path)
        if not bg_path.is_file():
            self._preview_scale = 1.0
            return params
        try:
            with Image.open(bg_path) as bg:
                width, height = bg.size
        except Exception:  # noqa: BLE001
            self._preview_scale = 1.0
            return params
        if width <= self._preview_max_width:
            self._preview_scale = 1.0
            return params

        scale = self._preview_max_width / width
        self._preview_scale = scale
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
        if params.paragraphs:
            preview.paragraphs = [
                Paragraph(
                    text=p.text,
                    align=p.align,
                    first_line_indent=p.first_line_indent * scale,
                )
                for p in params.paragraphs
            ]
        # 框选区域同样按比例缩放到预览坐标（深拷贝，保留全部排版、对齐、覆盖项与段落）
        preview.regions = []
        for r in params.regions or []:
            scaled_paras = None
            if r.paragraphs:
                scaled_paras = [
                    Paragraph(
                        text=p.text,
                        align=p.align,
                        first_line_indent=p.first_line_indent * scale,
                    )
                    for p in r.paragraphs
                ]
            preview.regions.append(
                TextRegion(
                    x=round(r.x * scale),
                    y=round(r.y * scale),
                    w=max(1, round(r.w * scale)),
                    h=max(1, round(r.h * scale)),
                    text=r.text,
                    font_path=r.font_path,
                    printed=r.printed,
                    font_size=round(r.font_size * scale) if r.font_size else 0,
                    page=r.page,
                    align=r.align,
                    indent_em=r.indent_em,
                    paragraphs=scaled_paras,
                    word_spacing=round(r.word_spacing * scale) if r.word_spacing is not None else None,
                    line_spacing=round(r.line_spacing * scale) if r.line_spacing is not None else None,
                    font_size_sigma=round(r.font_size_sigma * scale) if r.font_size_sigma is not None else None,
                    word_spacing_sigma=round(r.word_spacing_sigma * scale) if r.word_spacing_sigma is not None else None,
                    line_spacing_sigma=round(r.line_spacing_sigma * scale) if r.line_spacing_sigma is not None else None,
                    perturb_x_sigma=round(r.perturb_x_sigma * scale) if r.perturb_x_sigma is not None else None,
                    perturb_y_sigma=round(r.perturb_y_sigma * scale) if r.perturb_y_sigma is not None else None,
                    perturb_theta_sigma=r.perturb_theta_sigma,
                    miswrite_rate=r.miswrite_rate,
                    miswrite_strikeout_style=r.miswrite_strikeout_style,
                    color=r.color,
                    margin_top=round(r.margin_top * scale) if r.margin_top is not None else None,
                    margin_bottom=round(r.margin_bottom * scale) if r.margin_bottom is not None else None,
                    margin_left=round(r.margin_left * scale) if r.margin_left is not None else None,
                    margin_right=round(r.margin_right * scale) if r.margin_right is not None else None,
                )
            )
        return preview

    # ------------------------------------------------------------------
    # 后台任务
    # ------------------------------------------------------------------
    def _start_worker(
        self,
        params: HandwritingParams,
        mode: str,
        quiet: bool = False,
        seed: object | None = None,
        out_pdf: str | Path = "",
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
        worker = RenderWorker(
            params, mode, self._out_dir, out_pdf=out_pdf, bounds=bounds, seed=seed
        )
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
        self._ui.pushButton_7.setEnabled(not busy)

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
            if Path(files[0]).suffix.lower() == ".pdf":
                QMessageBox.information(self, "完成", f"PDF 已导出：{files[0]}")
            else:
                QMessageBox.information(self, "完成", f"已导出 {len(files)} 张图片到 {self._out_dir} 目录")
        # 预览场景无需额外提示

    def _on_failure(self, message: str) -> None:
        self._set_busy(False)
        if not self._auto:
            QMessageBox.warning(self, "失败", message)