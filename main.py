"""手写模拟器启动入口。

用法：
    uv run python main.py          # 启动图形界面
    uv run handwrite-cli ...       # 命令行批量生成
"""

from __future__ import annotations

from handwritesim.app import main

if __name__ == "__main__":
    raise SystemExit(main())