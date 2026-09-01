"""手写模拟器 - 基于 handright 的手写体生成工具。

提供可复用的核心引擎（core）、图形界面（gui）与命令行（cli）。
"""

__version__ = "0.3.1"

from .core.engine import HandwritingEngine
from .core.models import HandwritingParams

__all__ = ["HandwritingEngine", "HandwritingParams", "__version__"]