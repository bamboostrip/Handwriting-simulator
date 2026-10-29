"""可折叠分组控件：标题按钮 + 内容容器。

右栏参数面板按使用阶段分为 6 组（文本内容 / 文字区域 / 笔迹角色 / 导入素材 /
笔迹仿真与版式 / 预设），每组一个 ``CollapsibleSection``：

- 标题按钮带 ``collapsible`` 属性，复用 QSS 中 ``QPushButton[collapsible="true"]`` 样式；
- 标题文本支持计数（如「文字区域（3）」），由调用方 ``set_count()`` 维护；
- 折叠 = ``body.setVisible(False)``，**不销毁**内容控件：QLineEdit 焦点、
  QListWidget 悬浮高亮、富文本编辑器光标与选区状态都得以保活；
- 折叠态变化通过回调（或 ``toggled`` 信号）通知调用方，便于写入 QSettings。

对齐 Rust 版 ``ParamsPanel.vue`` 的 ``NCollapse + NCollapseItem`` 分组行为，
但用 Qt 原生控件实现，不依赖前端框架。
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6 import QtCore, QtWidgets

# 折叠态图标：与 Rust 版折叠头一致（展开 ▼ / 折叠 ▶）
_ARROW_EXPANDED = "▼"
_ARROW_COLLAPSED = "▶"


class CollapsibleSection(QtWidgets.QWidget):
    """标题按钮 + 内容容器组成的折叠分组。

    参数：
        title: 标题文本（不含箭头与计数，计数由 ``set_count`` 维护）。
        parent: 父控件。
        collapsed: 初始是否折叠。
        key: 分组标识（``text`` / ``regions`` / ``roles`` / ``import`` / ``sim`` / ``preset``），
             用于 QSettings 持久化与测试定位，同时拼进 objectName。
    """

    # 折叠态变化：True = 已折叠
    toggled = QtCore.pyqtSignal(bool)

    def __init__(
        self,
        title: str,
        parent: QtWidgets.QWidget | None = None,
        *,
        collapsed: bool = False,
        key: str = "",
    ) -> None:
        super().__init__(parent)
        self.key = key
        self._title = title
        self._count: int | None = None
        self._count_suffix = ""
        self._collapsed = bool(collapsed)
        self._callback: Callable[[bool], None] | None = None

        self.setObjectName(f"section_{key}" if key else "section")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # 标题按钮：整行可点，QSS 已有 QPushButton[collapsible="true"] 样式
        self.btn_header = QtWidgets.QPushButton(self)
        self.btn_header.setObjectName(f"section_header_{key}" if key else "section_header")
        self.btn_header.setProperty("collapsible", True)
        self.btn_header.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.btn_header.clicked.connect(self.toggle)
        layout.addWidget(self.btn_header)

        # 内容容器：折叠时仅隐藏，不销毁
        self.body = QtWidgets.QWidget(self)
        self.body.setObjectName(f"section_body_{key}" if key else "section_body")
        self.content_layout = QtWidgets.QVBoxLayout(self.body)
        self.content_layout.setContentsMargins(2, 0, 2, 4)
        self.content_layout.setSpacing(6)
        layout.addWidget(self.body)

        # 初始折叠态直接应用（不触发回调，避免构造期写 QSettings）
        self.set_collapsed(self._collapsed, notify=False)

    # ------------------------------------------------------------------
    # 折叠态
    # ------------------------------------------------------------------
    def is_collapsed(self) -> bool:
        """当前是否折叠。"""
        return self._collapsed

    def set_collapsed(self, collapsed: bool, notify: bool = True) -> None:
        """设置折叠态；notify=True 时触发回调与 ``toggled`` 信号。"""
        collapsed = bool(collapsed)
        changed = collapsed != self._collapsed
        self._collapsed = collapsed
        self.body.setVisible(not collapsed)
        self._update_header()
        if changed and notify:
            if self._callback is not None:
                self._callback(collapsed)
            self.toggled.emit(collapsed)

    def toggle(self) -> None:
        """在展开 / 折叠之间切换（标题按钮点击时调用）。"""
        self.set_collapsed(not self._collapsed)

    def set_collapse_callback(self, callback: Callable[[bool], None] | None) -> None:
        """注册折叠态变化回调（参数为新的折叠态，True=折叠）。

        用显式回调而非仅信号：``Ui_Form`` 是普通 Python 对象，
        这里保存强引用可确保持久化回调不会因弱引用被回收。
        """
        self._callback = callback

    # ------------------------------------------------------------------
    # 标题
    # ------------------------------------------------------------------
    def title(self) -> str:
        """不含箭头与计数的标题文本。"""
        return self._title

    def set_title(self, title: str) -> None:
        """更新标题文本（保留计数）。"""
        self._title = title
        self._update_header()

    def count(self) -> int | None:
        """当前计数；None 表示标题不带计数。"""
        return self._count

    def set_count(self, count: int | None, suffix: str = "") -> None:
        """设置标题计数（如 3 → 「文字区域（3）」）；``None`` 取消计数。"""
        self._count = None if count is None else int(count)
        self._count_suffix = suffix
        self._update_header()

    def header_text(self) -> str:
        """标题按钮当前完整文本（箭头 + 标题 + 计数）。"""
        return self.btn_header.text()

    def _update_header(self) -> None:
        text = self._title
        if self._count is not None:
            text = f"{text}（{self._count}{self._count_suffix}）"
        arrow = _ARROW_COLLAPSED if self._collapsed else _ARROW_EXPANDED
        self.btn_header.setText(f"{arrow} {text}")
        action = "展开" if self._collapsed else "折叠"
        self.btn_header.setToolTip(f"点击{action}「{text}」")
        self.btn_header.setAccessibleName(text)


__all__ = ["CollapsibleSection"]
