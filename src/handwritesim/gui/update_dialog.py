"""更新提示与下载对话框。"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import QThread, pyqtSignal

from ..core.updater import (
    UpdateInfo,
    apply_portable_update_and_restart,
    download_file,
    set_skipped_version,
)


class DownloadWorker(QThread):
    """后台分块下载更新包线程。"""

    progress = pyqtSignal(int, int)  # received, total
    finished = pyqtSignal(bool, str)  # success, file_path_or_err

    def __init__(self, download_url: str, dest_path: Path) -> None:
        super().__init__()
        self._url = download_url
        self._dest_path = dest_path
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        def _cb(recv: int, total: int) -> None:
            self.progress.emit(recv, total)

        ok = download_file(
            self._url,
            self._dest_path,
            progress_callback=_cb,
            cancel_event=self._cancel_event,
        )
        if ok and self._dest_path.exists():
            self.finished.emit(True, str(self._dest_path))
        else:
            self.finished.emit(False, "下载未完成或已被取消")


class UpdateDialog(QtWidgets.QDialog):
    """发现新版本提示对话框。"""

    def __init__(
        self,
        update_info: UpdateInfo,
        current_version: str,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._info = update_info
        self._current_version = current_version
        self._worker: DownloadWorker | None = None

        self.setWindowTitle("发现新版本")
        self.resize(520, 420)
        self.setMinimumSize(460, 360)

        self._init_ui()

    def _init_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # 头部标题
        lbl_title = QtWidgets.QLabel(f"🎉 发现新版本：v{self._info.version}", self)
        font = lbl_title.font()
        font.setPointSize(12)
        font.setBold(True)
        lbl_title.setFont(font)
        layout.addWidget(lbl_title)

        # 版本对比提示
        lbl_ver = QtWidgets.QLabel(
            f"当前安装版本：v{self._current_version}   ➔   最新版本：v{self._info.version}",
            self,
        )
        lbl_ver.setProperty("hint", True)
        layout.addWidget(lbl_ver)

        # 更新内容标题
        lbl_changelog_title = QtWidgets.QLabel("更新内容：", self)
        lbl_changelog_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(lbl_changelog_title)

        # 更新日志滚动展示区域
        self.text_changelog = QtWidgets.QTextBrowser(self)
        self.text_changelog.setOpenExternalLinks(True)
        # 支持 Markdown 渲染并自动换行
        self.text_changelog.setMarkdown(self._info.body)
        self.text_changelog.setMinimumHeight(140)
        layout.addWidget(self.text_changelog, 1)

        # 跳过此版本复选框
        self.check_skip = QtWidgets.QCheckBox(f"跳过此版本（不再自动提醒 v{self._info.version}）", self)
        layout.addWidget(self.check_skip)

        # 下载进度与状态显示区（初始隐藏）
        self.widget_progress = QtWidgets.QWidget(self)
        v_prog = QtWidgets.QVBoxLayout(self.widget_progress)
        v_prog.setContentsMargins(0, 0, 0, 0)
        v_prog.setSpacing(4)
        self.lbl_progress = QtWidgets.QLabel("准备下载更新包...", self.widget_progress)
        v_prog.addWidget(self.lbl_progress)
        self.progress_bar = QtWidgets.QProgressBar(self.widget_progress)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        v_prog.addWidget(self.progress_bar)
        self.widget_progress.setVisible(False)
        layout.addWidget(self.widget_progress)

        # 操作按钮栏
        row_btns = QtWidgets.QHBoxLayout()
        self.btn_browser = QtWidgets.QPushButton("🌐 浏览器下载", self)
        self.btn_browser.setToolTip("前往 GitHub Release 页面手动下载便携包")
        self.btn_browser.clicked.connect(self._open_browser)
        row_btns.addWidget(self.btn_browser)

        row_btns.addStretch(1)

        self.btn_cancel = QtWidgets.QPushButton("稍后提醒", self)
        self.btn_cancel.clicked.connect(self._on_cancel)
        row_btns.addWidget(self.btn_cancel)

        self.btn_auto_update = QtWidgets.QPushButton("🚀 立即自动更新", self)
        self.btn_auto_update.setProperty("primary", True)
        self.btn_auto_update.clicked.connect(self._start_auto_update)
        row_btns.addWidget(self.btn_auto_update)

        layout.addLayout(row_btns)

    def _open_browser(self) -> None:
        """在默认浏览器中打开发布页。"""
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(self._info.html_url))

    def _on_cancel(self) -> None:
        """取消并根据复选框状态记录跳过版本。"""
        if self.check_skip.isChecked():
            set_skipped_version(self._info.version)
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
        self.reject()

    def _start_auto_update(self) -> None:
        """开始自动下载并应用更新。"""
        if not self._info.asset_url:
            # 如果没有直接的 exe/zip asset 资产链接，退回浏览器下载
            self._open_browser()
            return

        self.btn_auto_update.setEnabled(False)
        self.btn_browser.setEnabled(False)
        self.widget_progress.setVisible(True)

        temp_dir = Path(os.environ.get("TEMP", os.getcwd()))
        target_name = self._info.asset_name or f"HandWriteSim_{self._info.version}.exe"
        dest_path = temp_dir / target_name

        self._worker = DownloadWorker(self._info.asset_url, dest_path)
        self._worker.progress.connect(self._on_download_progress)
        self._worker.finished.connect(self._on_download_finished)
        self._worker.start()

    def _on_download_progress(self, received: int, total: int) -> None:
        if total > 0:
            pct = int(received * 100 / total)
            self.progress_bar.setValue(pct)
            recv_mb = received / (1024 * 1024)
            total_mb = total / (1024 * 1024)
            self.lbl_progress.setText(f"正在下载更新包：{recv_mb:.1f} MB / {total_mb:.1f} MB ({pct}%)")
        else:
            recv_mb = received / (1024 * 1024)
            self.lbl_progress.setText(f"正在下载更新包：{recv_mb:.1f} MB")

    def _on_download_finished(self, success: bool, path_or_err: str) -> None:
        self.btn_auto_update.setEnabled(True)
        self.btn_browser.setEnabled(True)

        if success:
            self.lbl_progress.setText("✅ 下载完成，准备重启并应用新版本...")
            QtWidgets.QMessageBox.information(
                self,
                "更新下载完成",
                "新版本已准备就绪，点击「确定」后程序将自动重启并升级至新版本。",
            )
            apply_portable_update_and_restart(Path(path_or_err))
            QtWidgets.QApplication.quit()
            os._exit(0)
        else:
            self.lbl_progress.setText(f"❌ 下载失败：{path_or_err}")
            QtWidgets.QMessageBox.warning(
                self,
                "更新下载失败",
                f"未能成功下载更新包（{path_or_err}）。\n您可以点击「浏览器下载」前往 GitHub 手动下载。",
            )
