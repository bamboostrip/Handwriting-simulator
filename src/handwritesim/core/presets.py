"""参数预设的读写。

提供 JSON 作为规范格式，同时兼容旧版 18 行纯文本格式，
便于用户迁移历史预设文件。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import HandwritingParams


def save_json(params: HandwritingParams, path: str | Path) -> None:
    """将参数保存为结构化 JSON 预设文件。"""
    data: dict[str, Any] = {
        "version": 1,
        "params": params.to_dict(),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def load_json(path: str | Path) -> HandwritingParams:
    """从 JSON 预设文件加载参数。"""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    params_dict = data.get("params", data) if isinstance(data, dict) else {}
    return HandwritingParams.from_dict(params_dict)


def load_legacy(path: str | Path) -> HandwritingParams:
    """从旧版 18 行纯文本预设文件加载参数。"""
    with open(path, "r", encoding="utf-8") as fh:
        return HandwritingParams.from_lines(fh.readlines())


def load(path: str | Path) -> HandwritingParams:
    """自动识别预设文件格式并加载（JSON 或旧版纯文本）。"""
    path = Path(path)
    if path.suffix.lower() == ".json":
        return load_json(path)
    return load_legacy(path)


def save(path: str | Path, params: HandwritingParams) -> None:
    """根据文件扩展名选择保存格式（默认 JSON）。"""
    path = Path(path)
    if path.suffix.lower() in (".txt", ".preset"):
        data = params.to_lines()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(data) + "\n")
    else:
        save_json(params, path)