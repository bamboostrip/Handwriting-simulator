"""GUI 应用入口。"""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from .gui import MainWindow


def main() -> int:
    """启动图形界面。"""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())