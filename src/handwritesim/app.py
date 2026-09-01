"""GUI 应用入口。"""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from .gui import MainWindow


def main() -> int:
    """启动图形界面。"""
    app = QApplication(sys.argv)
    from .gui.ui import apply_theme
    apply_theme(app)
    # 动态监听系统深色/浅色模式切换
    try:
        app.styleHints().colorSchemeChanged.connect(lambda _: apply_theme(app))
    except Exception:
        pass
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())