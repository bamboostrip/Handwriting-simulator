"""GUI 后台任务。

将耗时的渲染/导出工作放入 QThread，通过信号回传结果，
避免阻塞 UI 线程或在子线程中直接操作控件。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from PyQt6.QtCore import QThread, pyqtSignal

from PIL import Image
from PIL import ImageDraw
from PIL import ImageQt

from ..core.engine import HandwritingEngine
from ..core.models import HandwritingParams

Mode = Literal["preview", "export"]


def _bounds_overlay(
    image: Image.Image, params: HandwritingParams, color: tuple[int, int, int]
) -> Image.Image:
    """预览专用：非渲染区域半透明着色 + 边距框线，让用户看清渲染边界。"""
    w, h = image.size
    left, top = int(params.left_margin), int(params.top_margin)
    right, bottom = int(params.right_margin), int(params.bottom_margin)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([0, 0, w - 1, h - 1], fill=(*color, 40))
    draw.rectangle([left, top, w - right - 1, h - bottom - 1], fill=(0, 0, 0, 0))
    draw.rectangle(
        [left, top, w - right - 1, h - bottom - 1],
        outline=(*color, 230),
        width=max(2, w // 900),
    )
    return Image.alpha_composite(image.convert("RGBA"), overlay)


class RenderWorker(QThread):
    """在后台线程执行一次渲染或导出。"""

    preview_ready = pyqtSignal(object)   # list[QPixmap]（预览全部页）
    succeeded = pyqtSignal(list)         # list[Path]（导出）或空（预览）
    failed = pyqtSignal(str)             # 错误信息

    def __init__(
        self,
        params: HandwritingParams,
        mode: Mode,
        out_dir: str | Path = "output",
        parent=None,
        bounds: tuple[int, int, int] | None = None,
        seed: object | None = None,
    ) -> None:
        super().__init__(parent)
        self._params = params
        self._mode = mode
        self._out_dir = out_dir
        self._bounds = bounds
        self._seed = seed

    def run(self) -> None:  # noqa: D102
        # 每次渲染都新建引擎并注入 seed：预览与导出只要 seed 相同，
        # 随机序列就从同一状态开始，笔画扰动完全一致；
        # 若共享一个引擎实例，预览会消耗随机数导致导出对不上。
        engine = HandwritingEngine(seed=self._seed)
        try:
            if self._mode == "preview":
                pixmaps = []
                for im in engine.generate(self._params):
                    if self._bounds is not None:
                        im = _bounds_overlay(im, self._params, self._bounds)
                    else:
                        im = im.convert("RGBA")
                    pixmaps.append(ImageQt.toqpixmap(im))
                self.preview_ready.emit(pixmaps)
                self.succeeded.emit([])
            else:
                files = engine.save_all(self._params, self._out_dir)
                self.succeeded.emit([str(f) for f in files])
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))