"""GUI 后台任务。

将耗时的渲染/导出工作放入 QThread，通过信号回传结果，
避免阻塞 UI 线程或在子线程中直接操作控件。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from PyQt6.QtCore import QThread, pyqtSignal

from PIL import Image, ImageQt

from ..core.engine import HandwritingEngine
from ..core.models import HandwritingParams

Mode = Literal["preview", "export"]


class RenderWorker(QThread):
    """在后台线程执行一次渲染或导出。"""

    preview_ready = pyqtSignal(object)   # QPixmap
    succeeded = pyqtSignal(list)         # list[Path]（导出）或空（预览）
    failed = pyqtSignal(str)             # 错误信息

    def __init__(
        self,
        engine: HandwritingEngine,
        params: HandwritingParams,
        mode: Mode,
        out_dir: str | Path = "output",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._params = params
        self._mode = mode
        self._out_dir = out_dir

    def run(self) -> None:  # noqa: D102
        try:
            if self._mode == "preview":
                image: Image.Image = self._engine.render_preview(self._params)
                pixmap = ImageQt.toqpixmap(image.convert("RGBA"))
                self.preview_ready.emit(pixmap)
                self.succeeded.emit([])
            else:
                files = self._engine.save_all(self._params, self._out_dir)
                self.succeeded.emit([str(f) for f in files])
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))