"""关于对话框与更新设置。"""

from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import QThread, pyqtSignal

from .. import __version__
from ..core.updater import (
    GITHUB_REPO_URL,
    RUST_REPO_URL,
    UpdateInfo,
    check_for_updates,
    is_auto_check_enabled,
    set_auto_check_enabled,
)
from .resources import resource_path
from .update_dialog import UpdateDialog


class CheckUpdateWorker(QThread):
    """后台异步检查更新线程。"""

    finished = pyqtSignal(object)  # UpdateInfo or None

    def __init__(self, current_version: str) -> None:
        super().__init__()
        self._ver = current_version

    def run(self) -> None:
        info = check_for_updates(self._ver, timeout=6.0, check_all=True)
        self.finished.emit(info)


class AboutDialog(QtWidgets.QDialog):
    """关于软件与版本信息对话框。"""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("关于手写模拟器")
        self.resize(500, 480)
        self.setMinimumSize(460, 420)
        self._worker: CheckUpdateWorker | None = None

        self._init_ui()

    def _init_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # 头部：图标 + 名称 + 版本
        row_head = QtWidgets.QHBoxLayout()
        lbl_icon = QtWidgets.QLabel(self)
        icon_pm = QtGui.QPixmap(resource_path("ui", "3d.ico"))
        if not icon_pm.isNull():
            lbl_icon.setPixmap(
                icon_pm.scaled(
                    54,
                    54,
                    QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                    QtCore.Qt.TransformationMode.SmoothTransformation,
                )
            )
        row_head.addWidget(lbl_icon)

        v_head = QtWidgets.QVBoxLayout()
        lbl_name = QtWidgets.QLabel("手写模拟器 (Handwriting Simulator)", self)
        font = lbl_name.font()
        font.setPointSize(12)
        font.setBold(True)
        lbl_name.setFont(font)
        v_head.addWidget(lbl_name)

        lbl_version = QtWidgets.QLabel(f"当前版本：v{__version__}", self)
        lbl_version.setProperty("hint", True)
        v_head.addWidget(lbl_version)
        row_head.addLayout(v_head)
        row_head.addStretch(1)
        layout.addLayout(row_head)

        # 介绍信息
        lbl_desc = QtWidgets.QLabel(
            "基于 handright 核心的国风手写字迹模拟与排版生成工具。\n"
            "支持背景底图模板、笔画高斯扰动、错字涂改、图文混排与多页 PDF 导出。",
            self,
        )
        lbl_desc.setWordWrap(True)
        layout.addWidget(lbl_desc)

        # 开源与项目链接 Group
        grp_links = QtWidgets.QGroupBox("开源项目与重构版", self)
        v_links = QtWidgets.QVBoxLayout(grp_links)
        v_links.setSpacing(8)

        lbl_py = QtWidgets.QLabel(
            f'🌟 <b>Python 版开源主仓库：</b><br>'
            f'<a href="{GITHUB_REPO_URL}">{GITHUB_REPO_URL}</a>',
            grp_links,
        )
        lbl_py.setOpenExternalLinks(True)
        v_links.addWidget(lbl_py)

        lbl_rust = QtWidgets.QLabel(
            f'🚀 <b>Rust 极速重构版（超快性能与低内存）：</b><br>'
            f'<a href="{RUST_REPO_URL}">{RUST_REPO_URL}</a>',
            grp_links,
        )
        lbl_rust.setOpenExternalLinks(True)
        v_links.addWidget(lbl_rust)

        layout.addWidget(grp_links)

        # 自动更新配置与手动检查
        grp_update = QtWidgets.QGroupBox("版本更新", self)
        v_update = QtWidgets.QVBoxLayout(grp_update)
        v_update.setSpacing(8)

        self.check_auto_update = QtWidgets.QCheckBox("启动软件时自动检查更新", grp_update)
        self.check_auto_update.setChecked(is_auto_check_enabled())
        self.check_auto_update.toggled.connect(set_auto_check_enabled)
        v_update.addWidget(self.check_auto_update)

        row_check = QtWidgets.QHBoxLayout()
        self.lbl_update_status = QtWidgets.QLabel("点击右侧按钮可主动联网检测最新版本", grp_update)
        self.lbl_update_status.setProperty("hint", True)
        row_check.addWidget(self.lbl_update_status, 1)

        self.btn_check_update = QtWidgets.QPushButton("🔄 检查更新", grp_update)
        self.btn_check_update.clicked.connect(self._manual_check_update)
        row_check.addWidget(self.btn_check_update)
        v_update.addLayout(row_check)

        layout.addWidget(grp_update)

        layout.addStretch(1)

        # 底部确定按钮
        row_bottom = QtWidgets.QHBoxLayout()
        row_bottom.addStretch(1)
        btn_close = QtWidgets.QPushButton("确定", self)
        btn_close.setProperty("primary", True)
        btn_close.setMinimumWidth(80)
        btn_close.clicked.connect(self.accept)
        row_bottom.addWidget(btn_close)
        layout.addLayout(row_bottom)

    def _manual_check_update(self) -> None:
        """手动触发检查更新。"""
        self.btn_check_update.setEnabled(False)
        self.lbl_update_status.setText("正在连接 GitHub 查询最新版本...")

        self._worker = CheckUpdateWorker(__version__)
        self._worker.finished.connect(self._on_check_finished)
        self._worker.start()

    def _on_check_finished(self, info: UpdateInfo | None) -> None:
        self.btn_check_update.setEnabled(True)
        if info is None:
            self.lbl_update_status.setText("❌ 查询失败，请检查网络连接")
            QtWidgets.QMessageBox.warning(
                self,
                "检查更新失败",
                "无法连接至 GitHub Releases API，请检查网络或稍后重试。",
            )
            return

        from ..core.updater import compare_versions

        if compare_versions(info.version, __version__) > 0:
            self.lbl_update_status.setText(f"🎉 发现新版本：v{info.version}")
            dlg = UpdateDialog(info, __version__, self)
            dlg.exec()
        else:
            self.lbl_update_status.setText(f"✅ 当前已是最新版本 (v{__version__})")
            QtWidgets.QMessageBox.information(
                self,
                "检查更新",
                f"当前已是最新版本 (v{__version__})，无需更新。",
            )
