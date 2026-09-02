"""笔迹角色管理对话框。

对齐 Rust 版 RoleManager.vue：管理 HandwritingRole 列表（默认手写/打印体/手写角色1/2…），
支持增删、重命名、挑选字体/颜色、切换打印体零扰动、调节角色独立扰动 σ。

动态高亮映射约定：文档中首次出现的背景高亮 → 角色2，次出现 → 角色3 … 
用户不需预设颜色，角色面板仅用于调整渲染风格（字体/颜色/扰动），渲染时按 role_id 取配置。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets

from ..core.models import HandwritingRole, parse_color
from ..core.system_fonts import list_system_fonts
from .ui import NoWheelSpinBox, NoWheelDoubleSpinBox, NoWheelComboBox


_ROLE_BG_COLORS = {
    0: "#ffffff",   # 默认手写 无背景
    1: "#e8e8e8",   # 打印体 灰
    2: "#fff8b8",   # 角色1 黄
    3: "#d1ffd1",   # 角色2 绿
    4: "#c8e8ff",   # 角色3 蓝
    5: "#ffd8f0",   # 角色4 粉
    6: "#ffe0b3",   # 橘
    7: "#e0d8ff",   # 紫
}

def role_background(role_id: int) -> str:
    return _ROLE_BG_COLORS.get(role_id, "#f0f0f0")

class RoleEditDialog(QtWidgets.QDialog):
    """单角色编辑。"""

    def __init__(self, parent=None, role: HandwritingRole | None = None):
        super().__init__(parent)
        self.setWindowTitle("编辑角色" if role else "新增角色")
        self.resize(480, 360)
        init = role or HandwritingRole(id=0, name="新角色")
        v = QtWidgets.QVBoxLayout(self)
        v.setSpacing(8)

        grid = QtWidgets.QGridLayout()
        grid.setColumnStretch(1, 1)

        grid.addWidget(QtWidgets.QLabel("名称", self), 0, 0)
        self.edit_name = QtWidgets.QLineEdit(init.name, self)
        self.edit_name.setPlaceholderText("如：小明的手写")
        grid.addWidget(self.edit_name, 0, 1)

        grid.addWidget(QtWidgets.QLabel("系统字体", self), 1, 0)
        self.combo_system = NoWheelComboBox(self)
        self.combo_system.setEditable(False)
        self.combo_system.setPlaceholderText("— 请选择系统已安装字体（可选）—")
        grid.addWidget(self.combo_system, 1, 1)

        grid.addWidget(QtWidgets.QLabel("字体文件", self), 2, 0)
        hfont = QtWidgets.QHBoxLayout()
        self.edit_font = QtWidgets.QLineEdit(init.font_path, self)
        self.edit_font.setPlaceholderText("留空跟随主字体；或从上方系统字体下拉选择")
        hfont.addWidget(self.edit_font, 1)
        self.btn_font = QtWidgets.QPushButton("浏览…", self)
        hfont.addWidget(self.btn_font)
        grid.addLayout(hfont, 2, 1)

        grid.addWidget(QtWidgets.QLabel("字号", self), 3, 0)
        self.spin_size = NoWheelSpinBox(self)
        self.spin_size.setRange(0, 300)
        self.spin_size.setValue(init.font_size)
        self.spin_size.setSpecialValueText("跟随主设置")
        grid.addWidget(self.spin_size, 3, 1)

        grid.addWidget(QtWidgets.QLabel("颜色", self), 4, 0)
        hcol = QtWidgets.QHBoxLayout()
        self.edit_color = QtWidgets.QLineEdit(init.color or "", self)
        self.edit_color.setPlaceholderText("跟随主颜色 / #RRGGBB")
        hcol.addWidget(self.edit_color)
        self.btn_color = QtWidgets.QPushButton("取色", self)
        hcol.addWidget(self.btn_color)
        self.btn_color_reset = QtWidgets.QPushButton("重置", self)
        hcol.addWidget(self.btn_color_reset)
        grid.addLayout(hcol, 4, 1)

        self.check_printed = QtWidgets.QCheckBox("打印体（零扰动、规整排版）", self)
        self.check_printed.setChecked(init.printed)
        grid.addWidget(self.check_printed, 5, 0, 1, 2)

        # 扰动覆盖（跟随主设置时为 0）
        grp = QtWidgets.QGroupBox("扰动覆盖（0=跟随主设置）", self)
        g = QtWidgets.QGridLayout(grp)
        g.addWidget(QtWidgets.QLabel("字号 σ"), 0, 0)
        self.spin_fs = NoWheelSpinBox(grp); self.spin_fs.setRange(0,20); self.spin_fs.setValue(init.font_size_sigma or 0)
        g.addWidget(self.spin_fs, 0, 1)
        g.addWidget(QtWidgets.QLabel("水平位移 σ"), 0, 2)
        self.spin_px = NoWheelSpinBox(grp); self.spin_px.setRange(0,20); self.spin_px.setValue(init.perturb_x_sigma or 0)
        g.addWidget(self.spin_px, 0, 3)
        g.addWidget(QtWidgets.QLabel("竖直位移 σ"), 1, 0)
        self.spin_py = NoWheelSpinBox(grp); self.spin_py.setRange(0,20); self.spin_py.setValue(init.perturb_y_sigma or 0)
        g.addWidget(self.spin_py, 1, 1)
        g.addWidget(QtWidgets.QLabel("旋转 σ"), 1, 2)
        self.spin_pt = NoWheelDoubleSpinBox(grp); self.spin_pt.setRange(0,2); self.spin_pt.setDecimals(3); self.spin_pt.setValue(init.perturb_theta_sigma or 0.0)
        g.addWidget(self.spin_pt, 1, 3)

        v.addLayout(grid)
        v.addWidget(grp)

        # 仅打印体时禁用扰动
        self.check_printed.toggled.connect(lambda on: [self.spin_fs.setEnabled(not on), self.spin_px.setEnabled(not on), self.spin_py.setEnabled(not on), self.spin_pt.setEnabled(not on)])
        self.check_printed.toggled.emit(self.check_printed.isChecked())

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel, parent=self)
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

        self.btn_font.clicked.connect(self._pick_font)
        self.btn_color.clicked.connect(self._pick_color)
        self.btn_color_reset.clicked.connect(lambda: self.edit_color.clear())

        self._role_id = init.id

        # 填充系统字体下拉（Windows 注册表 + 字体目录），选中后自动填入字体文件路径
        try:
            sys_fonts = list_system_fonts()
            self.combo_system.addItem("— 请选择系统已安装字体（可选）—", userData="")
            for disp, path in sys_fonts:
                label = f"{disp}  ({path.name})" if path.name.lower() not in disp.lower() else disp
                # 过长截断
                if len(label) > 64:
                    label = label[:61] + "…"
                self.combo_system.addItem(label, userData=str(path))
            # 若当前字体已在列表中，自动选中
            cur = Path(init.font_path).resolve() if init.font_path else None
            if cur:
                for idx in range(self.combo_system.count()):
                    data = self.combo_system.itemData(idx)
                    if data and Path(data).resolve() == cur:
                        self.combo_system.setCurrentIndex(idx)
                        break
            self.combo_system.currentIndexChanged.connect(self._on_system_font_changed)
        except Exception:
            pass

    def _pick_font(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择角色字体", "", "字体 (*.ttf *.ttc *.otf)")
        if p:
            self.edit_font.setText(p)

    def _on_system_font_changed(self, idx: int) -> None:
        data = self.combo_system.itemData(idx)
        if data:
            self.edit_font.setText(data)

    def _pick_color(self):
        init = QtGui.QColor(self.edit_color.text().strip() or "#000000")
        col = QtWidgets.QColorDialog.getColor(init, self, "选择角色颜色")
        if col.isValid():
            self.edit_color.setText(col.name())

    def _on_ok(self):
        if self.edit_color.text().strip():
            try:
                parse_color(self.edit_color.text().strip())
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, "颜色检查", str(exc))
                return
        if self.edit_font.text().strip() and not Path(self.edit_font.text().strip()).is_file():
            QtWidgets.QMessageBox.warning(self, "字体检查", f"字体文件不存在：{self.edit_font.text().strip()}")
            return
        self.accept()

    @property
    def result_role(self) -> HandwritingRole:
        col = self.edit_color.text().strip() or None
        return HandwritingRole(
            id=self._role_id,
            name=self.edit_name.text().strip() or f"角色{self._role_id}",
            font_path=self.edit_font.text().strip(),
            font_size=self.spin_size.value(),
            color=col,
            printed=self.check_printed.isChecked(),
            font_size_sigma=self.spin_fs.value() or None,
            perturb_x_sigma=self.spin_px.value() or None,
            perturb_y_sigma=self.spin_py.value() or None,
            perturb_theta_sigma=self.spin_pt.value() or None,
        )


class RoleManagerDialog(QtWidgets.QDialog):
    """角色列表管理（增删改、排序）。"""

    def __init__(self, parent=None, roles: list[HandwritingRole] | None = None):
        super().__init__(parent)
        self.setWindowTitle("笔迹角色管理")
        self.resize(620, 420)
        self._roles: list[HandwritingRole] = [HandwritingRole(**vars(r)) for r in (roles or [])]

        # 若为空，注入默认 4 角色
        if not self._roles:
            from ..core.models import default_roles
            self._roles = default_roles()

        v = QtWidgets.QVBoxLayout(self)
        v.setSpacing(8)
        hint = QtWidgets.QLabel("动态映射：Word 中首次出现的背景高亮→手写角色1，次出现→手写角色2 … 角色面板仅调整渲染风格，不限制颜色。", self)
        hint.setWordWrap(True); hint.setProperty("hint", True)
        v.addWidget(hint)

        self.list = QtWidgets.QListWidget(self)
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        v.addWidget(self.list, 1)

        h = QtWidgets.QHBoxLayout()
        self.btn_add = QtWidgets.QPushButton("新增角色", self)
        self.btn_edit = QtWidgets.QPushButton("编辑", self)
        self.btn_del = QtWidgets.QPushButton("删除", self)
        h.addWidget(self.btn_add); h.addWidget(self.btn_edit); h.addWidget(self.btn_del); h.addStretch(1)
        v.addLayout(h)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel, parent=self)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

        self.btn_add.clicked.connect(self._add)
        self.btn_edit.clicked.connect(self._edit)
        self.btn_del.clicked.connect(self._del)
        self.list.itemDoubleClicked.connect(lambda _: self._edit())

        self._refresh()

    def _refresh(self):
        self.list.clear()
        for r in sorted(self._roles, key=lambda x: x.id):
            bg = role_background(r.id)
            # id 0/1 固定，不可删
            lock = " 🔒" if r.id in (0,1) else ""
            item = QtWidgets.QListWidgetItem(f"[{r.id}] {r.name}{' (打印体)' if r.printed else ''}{lock}  颜色:{r.color or '跟随'}  字体:{Path(r.font_path).name if r.font_path else '跟随'}  字号:{r.font_size or '跟随'}")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, r.id)
            # 背景色提示
            item.setBackground(QtGui.QColor(bg))
            if r.id == 0:
                item.setForeground(QtGui.QColor("#888888"))
            self.list.addItem(item)

    def _next_id(self) -> int:
        return max((r.id for r in self._roles), default=-1) + 1

    def _add(self):
        dlg = RoleEditDialog(self, HandwritingRole(id=self._next_id(), name=f"手写角色{self._next_id()-1}"))
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self._roles.append(dlg.result_role)
            self._refresh()

    def _edit(self):
        row = self.list.currentRow()
        if row < 0: return
        rid = self.list.item(row).data(QtCore.Qt.ItemDataRole.UserRole)
        role = next((r for r in self._roles if r.id == rid), None)
        if not role: return
        dlg = RoleEditDialog(self, role)
        # 固定角色 id 不可改，但允许改名/ style
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            nr = dlg.result_role
            nr.id = rid  # 保持 id
            for i, r in enumerate(self._roles):
                if r.id == rid:
                    self._roles[i] = nr
                    break
            self._refresh()

    def _del(self):
        row = self.list.currentRow()
        if row < 0: return
        rid = self.list.item(row).data(QtCore.Qt.ItemDataRole.UserRole)
        if rid in (0,1):
            QtWidgets.QMessageBox.information(self, "提示", "默认手写与打印体不可删除")
            return
        self._roles = [r for r in self._roles if r.id != rid]
        self._refresh()

    @property
    def result_roles(self) -> list[HandwritingRole]:
        return sorted(self._roles, key=lambda r: r.id)
