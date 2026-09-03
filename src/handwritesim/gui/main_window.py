"""主窗口：组装设计器生成的界面与核心引擎。

负责界面控件与 HandwritingParams 的映射、按钮事件、后台任务调度。
业务逻辑（校验、渲染、导出）全部委托给 core 模块。
"""

from __future__ import annotations

import copy
import random
from pathlib import Path

from PIL import Image
from PIL import ImageQt
from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QMainWindow,
    QFileDialog,
    QMessageBox,
)

from .. import __version__
from ..core.models import HandwritingParams, Paragraph, TextRegion, TextRun, HandwritingRole, default_roles
from ..core import doc_render
from ..core import presets
from ..core.docx_io import load_paragraphs_with_runs, extract_roles_from_paragraphs, _is_chinese_heading, has_docx_highlights
from .role_manager import RoleManagerDialog, role_background
from ..core.paths import assets_root, ensure_assets_dirs
from ..core.updater import (
    GITHUB_REPO_URL,
    RUST_REPO_URL,
    UpdateInfo,
    get_skipped_version,
    is_auto_check_enabled,
)
from .about_dialog import AboutDialog, CheckUpdateWorker
from .region_dialog import RegionDialog
from .update_dialog import UpdateDialog
from .workers import RenderWorker
from .ui import Ui_Form, apply_theme, is_dark_mode


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
    _PRESET_PLACEHOLDER = "选择预设"

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
        # 多角色笔迹槽位（动态高亮映射）
        self._roles: list[HandwritingRole] = default_roles()
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

        # 启动时后台异步检查更新（延迟 1.5 秒，避免阻塞启动）
        self._update_check_worker: CheckUpdateWorker | None = None
        if is_auto_check_enabled():
            QTimer.singleShot(1500, self._check_update_on_startup)

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------
    def _connect_signals(self) -> None:
        ui = self._ui
        # 关于与更新
        ui.btn_about.clicked.connect(self._show_about)
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
        # 角色管理
        ui.btn_role_add.clicked.connect(self._role_add)
        ui.btn_role_edit.clicked.connect(self._role_edit)
        ui.btn_role_del.clicked.connect(self._role_del)
        ui.btn_role_manage.clicked.connect(self._role_manage)
        ui.role_list.itemDoubleClicked.connect(lambda _: self._role_edit())
        self._refresh_role_list()
        # 划选标记：选中文本一键设为打印/角色
        ui.btn_mark_print.clicked.connect(lambda: self._mark_selection(1))
        ui.btn_mark_role1.clicked.connect(lambda: self._mark_selection(2))
        ui.btn_mark_role2.clicked.connect(lambda: self._mark_selection(3))
        ui.btn_mark_clear.clicked.connect(lambda: self._mark_selection(0))
        ui.textEdit.selectionChanged.connect(self._update_mark_buttons)
        self._update_mark_buttons()
        self._connect_auto_preview()

    def _update_miswrite_rate_label(self, value: int) -> None:
        """滑块值（0~300）同步为百分比显示（0.0%~30.0%）。"""
        self._ui.label_miswrite_rate_value.setText(f"{value / 10.0:.1f}%")

    # ------------------------------------------------------------------
    # 角色管理
    # ------------------------------------------------------------------
    def _refresh_role_list(self) -> None:
        from PyQt6.QtGui import QColor
        lst = self._ui.role_list
        lst.blockSignals(True)
        lst.clear()
        for r in sorted(self._roles, key=lambda x: x.id):
            bg = role_background(r.id)
            tag = " 🔒" if r.id in (0, 1) else ""
            col = r.color or "跟随"
            font_name = Path(r.font_path).name if r.font_path else "跟随主字体"
            item = QtWidgets.QListWidgetItem(f"[{r.id}] {r.name}{' (打印)' if r.printed else ''}{tag}  {col}  {font_name}")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, r.id)
            item.setBackground(QColor(bg))
            # 根据角色背景设置 tooltip 说明动态映射
            if r.id >= 2:
                item.setToolTip(f"对应 Word 中第 {r.id-1} 个出现的背景高亮（动态映射），及编辑器中{bg}底色文字")
            lst.addItem(item)
        lst.blockSignals(False)
        # 更新标记按钮文本以反映当前角色名；默认无手写角色时按钮隐藏，导入后按需显示
        for rid, btn in [(2, self._ui.btn_mark_role1), (3, self._ui.btn_mark_role2)]:
            role = next((x for x in self._roles if x.id == rid), None)
            if role:
                btn.setText(f"{role.name[:6]}")
                btn.setToolTip(f"设为 {role.name}（{role_background(rid)}底）")
                btn.setVisible(True)
            else:
                btn.setVisible(False)

    def _role_add(self) -> None:
        nxt = max((r.id for r in self._roles), default=-1) + 1
        from .role_manager import RoleEditDialog
        dlg = RoleEditDialog(self, HandwritingRole(id=nxt, name=f"手写角色{nxt-1}"))
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self._roles.append(dlg.result_role)
            self._refresh_role_list()
            self._refresh_region_list()
            self._preview_timer.start()

    def _role_edit(self) -> None:
        row = self._ui.role_list.currentRow()
        if row < 0:
            return
        rid = self._ui.role_list.item(row).data(QtCore.Qt.ItemDataRole.UserRole)
        role = next((r for r in self._roles if r.id == rid), None)
        if not role:
            return
        from .role_manager import RoleEditDialog
        dlg = RoleEditDialog(self, role)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            nr = dlg.result_role
            nr.id = rid
            for i, r in enumerate(self._roles):
                if r.id == rid:
                    self._roles[i] = nr
                    break
            self._refresh_role_list()
            self._refresh_region_list()
            self._preview_timer.start()

    def _role_del(self) -> None:
        row = self._ui.role_list.currentRow()
        if row < 0:
            return
        rid = self._ui.role_list.item(row).data(QtCore.Qt.ItemDataRole.UserRole)
        if rid in (0, 1):
            QMessageBox.information(self, "提示", "默认手写与打印体不可删除")
            return
        self._roles = [r for r in self._roles if r.id != rid]
        self._refresh_role_list()
        self._refresh_region_list()
        self._preview_timer.start()

    def _role_manage(self) -> None:
        dlg = RoleManagerDialog(self, self._roles)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self._roles = dlg.result_roles
            self._refresh_role_list()
            self._refresh_region_list()
            self._preview_timer.start()

    # 划选标记与高亮预览
    _ROLE_BG = {
        0: None,
        1: QtGui.QColor("#e8e8e8"),
        2: QtGui.QColor("#fff8b8"),
        3: QtGui.QColor("#d1ffd1"),
        4: QtGui.QColor("#c8e8ff"),
        5: QtGui.QColor("#ffd8f0"),
        6: QtGui.QColor("#ffe0b3"),
        7: QtGui.QColor("#e0d8ff"),
    }
    def _role_bg(self, rid: int) -> QtGui.QColor | None:
        if rid in self._ROLE_BG:
            return self._ROLE_BG[rid]
        return QtGui.QColor(role_background(rid))

    def _ensure_role(self, rid: int) -> None:
        if any(r.id == rid for r in self._roles):
            return
        # 动态按需创建手写角色（不在默认里预置）
        name = f"手写角色{rid-1}" if rid >= 2 else f"角色{rid}"
        self._roles.append(HandwritingRole(id=rid, name=name, printed=False))
        self._roles = sorted(self._roles, key=lambda r: r.id)
        self._refresh_role_list()

    def _mark_selection(self, role_id: int) -> None:
        cursor = self._ui.textEdit.textCursor()
        if not cursor.hasSelection():
            # 无选区则对当前词/行不作处理，提示用户先划选
            return
        if role_id >= 2:
            self._ensure_role(role_id)
        fmt = QtGui.QTextCharFormat()
        bg = self._role_bg(role_id)
        if bg is None or role_id == 0:
            fmt.clearBackground()
        else:
            fmt.setBackground(bg)
        # 将 role_id 存入 UserProperty 以便后续收集
        fmt.setProperty(QtGui.QTextFormat.Property.UserProperty, role_id)
        cursor.mergeCharFormat(fmt)
        # 标记后自动预览
        self._preview_timer.start()

    def _update_mark_buttons(self) -> None:
        has = self._ui.textEdit.textCursor().hasSelection()
        self._ui.btn_mark_print.setEnabled(has)
        self._ui.btn_mark_role1.setEnabled(has)
        self._ui.btn_mark_role2.setEnabled(has)
        self._ui.btn_mark_clear.setEnabled(has)

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
        """将段落列表回填为富文本（含 Run 角色背景）。"""
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QTextBlockFormat, QTextCharFormat, QTextCursor

        editor = self._ui.textEdit
        editor.clear()
        cursor = QTextCursor(editor.document())
        for idx, para in enumerate(paras):
            if idx:
                cursor.insertBlock()
            bfmt = QTextBlockFormat()
            if para.align == "center":
                bfmt.setAlignment(Qt.AlignmentFlag.AlignCenter)
            elif para.align == "right":
                bfmt.setAlignment(Qt.AlignmentFlag.AlignRight)
            if para.first_line_indent:
                bfmt.setTextIndent(para.first_line_indent)
            cursor.setBlockFormat(bfmt)
            # 若含 runs，按角色逐段插入并附背景色与 UserProperty（含字体信息以便打印体沿用原文系统字体及加粗）
            if para.runs:
                for run in para.runs:
                    cfmt = QTextCharFormat()
                    bg = self._role_bg(run.role_id)
                    if bg is not None and run.role_id != 0:
                        cfmt.setBackground(bg)
                    cfmt.setProperty(QTextCharFormat.Property.UserProperty, run.role_id)
                    if run.font_family:
                        cfmt.setProperty(QTextCharFormat.Property.UserProperty + 1, run.font_family)
                    if run.font_size:
                        cfmt.setProperty(QTextCharFormat.Property.UserProperty + 2, int(run.font_size))
                    if run.font_file:
                        cfmt.setProperty(QTextCharFormat.Property.UserProperty + 3, run.font_file)
                    if getattr(run, "bold", False):
                        cfmt.setProperty(QTextCharFormat.Property.UserProperty + 4, True)
                        # 编辑器内直观加粗预览（仅打印体）
                        cfmt.setFontWeight(QtGui.QFont.Weight.Bold)
                        f = cfmt.font()
                        f.setBold(True)
                        cfmt.setFont(f)
                    cursor.setCharFormat(cfmt)
                    cursor.insertText(run.text)
            else:
                cursor.insertText(para.text)
        editor.setTextCursor(cursor)

    def _import_docx(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入 docx", "", "Word 文档 (*.docx)")
        if not path:
            return
        ignore_hl = False
        if has_docx_highlights(path):
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("检测到文字高亮标记")
            msg_box.setText("检测到文档中部分文字带有高亮或背景色。\n\n请选择排版方式：")
            msg_box.setIcon(QMessageBox.Icon.Question)
            btn_all_handwriting = msg_box.addButton(
                "全部作为手写（推荐）", QMessageBox.ButtonRole.AcceptRole
            )
            btn_mixed = msg_box.addButton("打印/手写混排", QMessageBox.ButtonRole.ActionRole)
            btn_cancel = msg_box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            msg_box.setDefaultButton(btn_all_handwriting)
            msg_box.exec()
            clicked = msg_box.clickedButton()
            if clicked == btn_cancel or clicked is None:
                return
            if clicked == btn_all_handwriting:
                ignore_hl = True
            elif clicked == btn_mixed:
                ignore_hl = False
            else:
                return

        try:
            paras = load_paragraphs_with_runs(
                path, self._int_of(self._ui.lineEdit_9, 36), ignore_highlights=ignore_hl
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        # 检查打印体原文系统字体是否缺失，缺失则让用户自选（手写体固定用用户手写字体，无需检查）
        missing: dict[str, list] = {}
        for p in paras:
            for r in p.runs or []:
                if r.role_id == 1 and r.font_family and not r.font_file:
                    missing.setdefault(r.font_family, []).append(r)
        if missing:
            msg = "检测到以下打印字体未在本机安装：\n" + "\n".join(f"· {fam}" for fam in missing) + "\n\n是否手动为它们选择字体文件？\n（取消则使用默认打印字体/角色字体）"
            ret = QMessageBox.question(self, "缺失字体", msg, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ret == QMessageBox.StandardButton.Yes:
                for fam, runs in list(missing.items()):
                    fp, _ = QFileDialog.getOpenFileName(self, f"选择字体文件对应 ‘{fam}’", "", "字体 (*.ttf *.ttc *.otf)")
                    if fp and Path(fp).is_file():
                        for rr in runs:
                            rr.font_file = fp
                    # 若用户取消该项，保持 r.font_file 为 None，渲染时回落到打印角色/主字体
        # 将打印体原文系统字体同步到打印角色（便于在角色管理里直观显示“宋体/微软雅黑”等，而非“跟随主字体”）
        # 统计打印 runs 中最常见的系统字体文件
        try:
            from collections import Counter
            printed_files = [r.font_file for p in paras for r in (p.runs or []) if r.role_id == 1 and r.font_file]
            if printed_files:
                most_common, _ = Counter(printed_files).most_common(1)[0]
                for rr in self._roles:
                    if rr.id == 1 and not rr.font_path:
                        rr.font_path = most_common
                        break
                # 若打印角色还未在列表（极少），同步到 new_roles
                for nr in [r for r in extract_roles_from_paragraphs(paras) if r.id == 1]:
                    if not nr.font_path and most_common:
                        nr.font_path = most_common
        except Exception:
            pass
        # 动态角色：按文档中出现的背景高亮自动扩展角色表
        new_roles = extract_roles_from_paragraphs(paras)
        # 合并到现有角色：保留已自定义的字体/颜色，仅补新增 id
        existing_ids = {r.id for r in self._roles}
        for nr in new_roles:
            if nr.id not in existing_ids:
                # 为新增高亮角色赋默认样式：保持默认手写字体，颜色按 run 的标记？此处颜色延用默认
                self._roles.append(nr)
        self._roles = sorted(self._roles, key=lambda r: r.id)
        self._refresh_role_list()
        self._set_paragraphs(paras)
        self._preview_timer.start()

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
            roles=self._roles,
            role_id=0,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        text = dlg.region_text
        if not text.strip():
            return
        region = TextRegion(
            x=x, y=y, w=w, h=h,
            text=text,
            role_id=dlg.region_role_id,
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

    def _region_style_label(self, region: TextRegion) -> str:
        """区域列表里的样式摘要：绑定角色时显示角色名（对齐 Rust regionLabel）。"""
        if region.role_id >= 2:
            role = next((r for r in self._roles if r.id == region.role_id), None)
            if role is not None:
                return role.name
            from ..core.doc_render import HIGHLIGHT_NAMES

            hl_name = HIGHLIGHT_NAMES.get(region.highlight or "", "")
            base = f"手写角色{region.role_id - 1}"
            return f"{base}（{hl_name}）" if hl_name else base
        return "打印" if region.printed else "手写"

    def _refresh_region_list(self) -> None:
        """刷新区域列表（红框不再常驻，仅悬浮列表项时临时高亮）。"""
        from PyQt6.QtWidgets import QListWidgetItem

        lst = self._ui.region_list
        lst.blockSignals(True)
        lst.clear()
        for i, region in enumerate(self._regions, start=1):
            style = self._region_style_label(region)
            page = f" 第{region.page}页" if region.page > 1 else ""
            tag = " [已自定义]" if region.has_overrides() else ""
            item = QListWidgetItem(
                f"{i}. {style}{page} {len(region.text)}字 "
                f"({region.x},{region.y} {region.w}×{region.h}){tag}"
            )
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
            roles=self._roles,
            role_id=region.role_id or (1 if region.printed else 0),
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
        region.role_id = dlg.region_role_id
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
        """导入 PDF/DOCX：把打印预览逐页渲染为背景（替换当前背景）。

        同时自动识别文档中标记的手写填空区域（高亮底色 / {{...}}、
        【...】占位标签），生成 TextRegion 列表并按高亮颜色关联笔迹角色。
        """
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
            pages, regions = doc_render.document_to_page_images_with_regions(
                path, out_dir, dpi=200
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        if regions:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("检测到手写填空标记")
            msg_box.setText(
                f"检测到文档包含 {len(regions)} 处高亮标记或填空区域。\n\n请选择底图处理方式："
            )
            msg_box.setIcon(QMessageBox.Icon.Question)
            btn_extract = msg_box.addButton(
                "提取填空框（推荐）", QMessageBox.ButtonRole.AcceptRole
            )
            btn_keep_raw = msg_box.addButton(
                "保留完整底图", QMessageBox.ButtonRole.ActionRole
            )
            btn_cancel = msg_box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            msg_box.setDefaultButton(btn_extract)
            msg_box.exec()
            clicked = msg_box.clickedButton()
            if clicked == btn_cancel or clicked is None:
                return
            if clicked == btn_keep_raw:
                raw_pages = [
                    Path(p).with_name(f"{Path(p).stem}_raw{Path(p).suffix}")
                    for p in pages
                ]
                final_pages = [
                    str(rp) if rp.exists() else str(p)
                    for rp, p in zip(raw_pages, pages)
                ]
                self._doc_pages = final_pages
                self._ui.lineEdit_14.setText(
                    f"{Path(path).name}（{len(final_pages)} 页，完整底图）"
                )
                self._ui.lineEdit_2.setText(final_pages[0])
                self._regions = []
                self._editing_row = None
                self._ui.label_11.end_region_edit()
                self._show_region_highlight(None)
                self._refresh_region_list()
                self._preview_timer.start()
                return
            elif clicked == btn_extract:
                self._doc_pages = [str(p) for p in pages]
                self._ui.lineEdit_14.setText(f"{Path(path).name}（{len(pages)} 页）")
                self._ui.lineEdit_2.setText(str(pages[0]))
                self._sync_detected_roles(regions)
                self._regions = regions
                self._editing_row = None
                self._ui.label_11.end_region_edit()
                self._show_region_highlight(None)
                self._refresh_region_list()
                QMessageBox.information(
                    self,
                    "导入完成",
                    f"已导入文档底图（共 {len(pages)} 页），\n"
                    f"自动识别提取了 {len(regions)} 处手写填空区域，\n"
                    "已在区域列表中生成对应条目并关联笔迹角色。",
                )
                self._preview_timer.start()
                return
            else:
                return
        else:
            self._doc_pages = [str(p) for p in pages]
            self._ui.lineEdit_14.setText(f"{Path(path).name}（{len(pages)} 页）")
            self._ui.lineEdit_2.setText(str(pages[0]))
            self._preview_timer.start()

    def _sync_detected_roles(self, regions: list[TextRegion]) -> None:
        """把底图识别出的区域同步到角色列表（对齐 Rust 版 store.importDocument）。

        同一高亮颜色在文档中映射到同一角色（首次出现 -> 角色2，次出现 -> 3 …）；
        已存在的角色补记高亮绑定，缺失的按需创建。
        """
        from ..core.doc_render import HIGHLIGHT_NAMES

        changed = False
        for rid in sorted({r.role_id for r in regions if r.role_id >= 2}):
            matching = next(
                (r for r in regions if r.role_id == rid and r.highlight), None
            ) or next((r for r in regions if r.role_id == rid), None)
            highlight = matching.highlight if matching else None
            hl_name = HIGHLIGHT_NAMES.get(highlight or "", "")
            name = f"手写角色{rid - 1}" + (f"（{hl_name}）" if hl_name else "")
            existing = next((x for x in self._roles if x.id == rid), None)
            if existing is not None:
                if not existing.highlight and highlight:
                    existing.highlight = highlight
                    changed = True
            else:
                self._roles.append(
                    HandwritingRole(id=rid, name=name, printed=False, highlight=highlight)
                )
                changed = True
        if changed:
            self._roles = sorted(self._roles, key=lambda r: r.id)
            self._refresh_role_list()

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
        p.roles = [copy.copy(r) for r in self._roles]
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
        """从富文本编辑器的块/字符格式收集段落（含 Run 角色）。

        空行保留为空段落，使渲染结果与纯文本路径的空行行为一致；
        全文为空时返回 []，交由校验提示未输入文字。
        通过 QTextFragment 的 UserProperty 或背景色还原 role_id，
        合并连续同角色片段为 TextRun，实现自然流式混排。
        """
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QTextCharFormat

        from ..core.models import Paragraph, TextRun

        # 背景色到 role_id 的反向映射（与 _role_bg、role_background 保持一致）
        bg_to_role: dict[str, int] = {
            QtGui.QColor("#e8e8e8").name(): 1,
            QtGui.QColor("#fff8b8").name(): 2,
            QtGui.QColor("#d1ffd1").name(): 3,
            QtGui.QColor("#c8e8ff").name(): 4,
            QtGui.QColor("#ffd8f0").name(): 5,
        }
        doc = self._ui.textEdit.document()
        paras: list[Paragraph] = []
        has_text = False
        for i in range(doc.blockCount()):
            block = doc.findBlockByNumber(i)
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
            # 收集该块内各 fragment 的角色（含字体信息，打印体沿用原文系统字体）
            runs: list[TextRun] = []
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    text = frag.text()
                    if text:
                        cf = frag.charFormat()
                        rid_val = cf.property(QTextCharFormat.Property.UserProperty)
                        if isinstance(rid_val, int) and rid_val >= 0:
                            rid = rid_val
                        else:
                            # 兼容：无 UserProperty 时按背景色推断
                            bg = cf.background()
                            if bg.style() != Qt.BrushStyle.NoBrush:
                                name = bg.color().name().lower()
                                rid = bg_to_role.get(name, 0)
                                # 额外容忍未列入的动态角色背景（取 role_background 推断）
                                if rid == 0:
                                    for cand in range(2, 20):
                                        if QtGui.QColor(role_background(cand)).name().lower() == name:
                                            rid = cand
                                            break
                            else:
                                rid = 0
                        # 取字体信息（仅打印体有效，手写体忽略）及加粗
                        fam_val = cf.property(QTextCharFormat.Property.UserProperty + 1)
                        size_val = cf.property(QTextCharFormat.Property.UserProperty + 2)
                        file_val = cf.property(QTextCharFormat.Property.UserProperty + 3)
                        bold_val = cf.property(QTextCharFormat.Property.UserProperty + 4)
                        fam = str(fam_val).strip() if isinstance(fam_val, str) and fam_val else None
                        try:
                            fsize = int(size_val) if isinstance(size_val, int) and size_val > 0 else None
                        except Exception:
                            fsize = None
                        ffile = str(file_val).strip() if isinstance(file_val, str) and file_val else None
                        if fam and not ffile:
                            # 若仅有 family 但无文件，尝试即时解析
                            try:
                                from ..core.system_fonts import family_to_file as _ftf
                                p = _ftf(fam)
                                if p and Path(p).is_file():
                                    ffile = str(p)
                            except Exception:
                                pass
                        # 加粗：优先 UserProperty，其次编辑器字重（用于粘贴或历史数据）
                        is_bold = False
                        if isinstance(bold_val, bool):
                            is_bold = bold_val
                        elif bold_val not in (None, ""):
                            is_bold = bool(bold_val)
                        else:
                            try:
                                is_bold = (cf.fontWeight() >= QtGui.QFont.Weight.Bold) or cf.font().bold()
                            except Exception:
                                is_bold = False
                        # 仅打印体保留加粗，手写体忽略原文加粗
                        if rid != 1:
                            is_bold = False
                        is_heading_block = _is_chinese_heading(raw, align=align, is_first_para=(i == 0))
                        if is_heading_block and rid == 1:
                            is_bold = True
                            if not fam:
                                fam = "黑体"
                            if not ffile:
                                try:
                                    from ..core.system_fonts import family_to_file as _ftf
                                    p = _ftf(fam)
                                    if p and Path(p).is_file():
                                        ffile = str(p)
                                except Exception:
                                    pass
                        # 合并连续同 role+字体+加粗 的片段
                        if runs and runs[-1].role_id == rid and runs[-1].font_family == fam and runs[-1].font_size == fsize and runs[-1].font_file == ffile and getattr(runs[-1], "bold", False) == is_bold:
                            runs[-1].text += text
                        else:
                            runs.append(TextRun(text=text, role_id=rid, font_family=fam, font_size=fsize, font_file=ffile, bold=is_bold))
                it += 1
            # 若块无有效 fragment（如空行），构造空 text
            if not runs:
                runs = [TextRun(text=raw, role_id=0)] if raw else [TextRun(text="", role_id=0)]
                # 去除纯空行的冗余 role 包装，外层会判 has_text
                if not raw.strip():
                    paras.append(Paragraph(text="", align=align, first_line_indent=int(fmt.textIndent()), runs=None))
                    continue
            # 单一默认角色且文本与 raw 一致时，退化为 text 兼容模式
            if len(runs) == 1 and runs[0].role_id == 0 and runs[0].text == raw:
                paras.append(Paragraph(text=raw, align=align, first_line_indent=int(fmt.textIndent()), runs=None))
            else:
                # 多角色或非对齐情况，保留完整 runs
                plain = "".join(r.text for r in runs)
                # 确保 plain 与 raw 基本一致（fragment 可能丢失最后换行）
                paras.append(Paragraph(text=plain or raw, align=align, first_line_indent=int(fmt.textIndent()), runs=runs))
        return paras if has_text else []

    def apply_params(self, p: HandwritingParams) -> None:
        """将 HandwritingParams 回填到界面控件。"""
        ui = self._ui
        # 角色回填
        if p.roles is not None:
            self._roles = [copy.copy(r) for r in p.roles]
            self._refresh_role_list()
        # 预设不含文本内容：仅当预设自带文本时才回填，否则保留当前输入
        if p.paragraphs:
            # 若预设含角色，段落中的 role_id 需与当前角色一致；此处先按预设角色合并
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

    @staticmethod
    def _scaled_run(r: TextRun, scale: float) -> TextRun:
        """预览降采样时重建 Run：保留字体/加粗/颜色，仅字号按比例缩放。

        字段丢失会让预览中 docx 导入的加粗与黑体/宋体/仿宋混排
        全部回落到打印角色默认字体（导出不受影响，仅预览失真）。
        """
        return TextRun(
            text=r.text,
            role_id=r.role_id,
            color=r.color,
            font_family=r.font_family,
            font_size=max(1, round(r.font_size * scale)) if r.font_size else None,
            font_file=r.font_file,
            bold=r.bold,
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
        if params.roles is not None:
            preview.roles = []
            for r in params.roles:
                nr = copy.copy(r)
                if nr.font_size:
                    nr.font_size = max(1, round(nr.font_size * scale))
                if nr.font_size_sigma is not None:
                    nr.font_size_sigma = max(0, round(nr.font_size_sigma * scale))
                if nr.perturb_x_sigma is not None:
                    nr.perturb_x_sigma = max(0, round(nr.perturb_x_sigma * scale))
                if nr.perturb_y_sigma is not None:
                    nr.perturb_y_sigma = max(0, round(nr.perturb_y_sigma * scale))
                preview.roles.append(nr)
        if params.paragraphs:
            preview.paragraphs = []
            for p in params.paragraphs:
                runs = None
                if p.runs:
                    runs = [self._scaled_run(r, scale) for r in p.runs]
                preview.paragraphs.append(
                    Paragraph(
                        text=p.text,
                        align=p.align,
                        first_line_indent=p.first_line_indent * scale,
                        runs=runs,
                    )
                )
        # 框选区域同样按比例缩放到预览坐标（深拷贝，保留全部排版、对齐、覆盖项与段落/runs）
        preview.regions = []
        for r in params.regions or []:
            scaled_paras = None
            if r.paragraphs:
                scaled_paras = []
                for p in r.paragraphs:
                    runs = None
                    if p.runs:
                        runs = [self._scaled_run(rr, scale) for rr in p.runs]
                    scaled_paras.append(Paragraph(text=p.text, align=p.align, first_line_indent=p.first_line_indent * scale, runs=runs))
            preview.regions.append(
                TextRegion(
                    x=round(r.x * scale),
                    y=round(r.y * scale),
                    w=max(1, round(r.w * scale)),
                    h=max(1, round(r.h * scale)),
                    text=r.text,
                    role_id=r.role_id,
                    highlight=r.highlight,
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
        # 预览全部页已生成，在主线程将 PIL.Image 安全转换为 QPixmap
        self._preview_pages = [
            ImageQt.toqpixmap(p) if isinstance(p, Image.Image) else p
            for p in pages
        ]
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
    _PREVIEW_BG_COLORS = ("#c8d0ca", "#565b56", "#242c27")

    def _toggle_preview_bg(self) -> None:
        """循环切换预览区底色，避免背景图与底色撞色时边界不可辨。"""
        idx = (getattr(self, "_preview_bg_idx", 0) + 1) % len(self._PREVIEW_BG_COLORS)
        self._preview_bg_idx = idx
        color = self._PREVIEW_BG_COLORS[idx]
        border_color = "#303d34" if is_dark_mode() else "#d3ded6"
        self._ui.label_11.setStyleSheet(
            f"PreviewLabel {{ background: {color};"
            f" border: 1px solid {border_color}; border-radius: 6px; }}"
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

    def _show_about(self) -> None:
        """显示关于对话框。"""
        dlg = AboutDialog(self)
        dlg.exec()

    def _check_update_manually(self) -> None:
        """手动检查更新。"""
        self._show_about()

    def _check_update_on_startup(self) -> None:
        """启动时静默检查更新。"""
        self._update_check_worker = CheckUpdateWorker(__version__)
        self._update_check_worker.finished.connect(self._on_startup_update_checked)
        self._update_check_worker.start()

    def _on_startup_update_checked(self, info: UpdateInfo | None) -> None:
        if info is None:
            return
        from ..core.updater import compare_versions

        if compare_versions(info.version, __version__) > 0:
            if info.version != get_skipped_version():
                dlg = UpdateDialog(info, __version__, self)
                dlg.show()