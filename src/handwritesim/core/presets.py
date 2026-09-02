"""参数预设的读写。

提供 JSON 作为规范格式，同时兼容旧版 18 行纯文本格式，
便于用户迁移历史预设文件。

便携模式：预设内字体/背景路径若位于资产根目录（exe 旁）内，保存时写成
相对路径，载入时按资产根目录解析回绝对路径——用户把整个文件夹拷到任意
位置，预设仍然指向旁边的 fonts/ 与 backgrounds/，实现「下载即用」。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .models import HandwritingParams
from .paths import assets_root


def to_portable_path(path: str) -> str:
    """绝对路径位于资产根目录内时转为相对路径，便于预设跨机器携带。

    资产根目录之外的路径保持绝对路径（用户本机的自定义位置）。
    """
    if not path:
        return path
    try:
        rel = Path(path).resolve().relative_to(Path(assets_root()).resolve())
    except (ValueError, OSError):
        # 不在资产根内（含跨盘符）或路径非法：原样保留
        return path
    return rel.as_posix()


def from_portable_path(path: str) -> str:
    """预设中的相对路径按资产根目录解析为绝对路径。

    绝对路径原样返回；相对路径对应文件不存在时回退原字符串，
    交由参数校验提示用户手动指定。
    """
    if not path or Path(path).is_absolute():
        return path
    candidate = Path(assets_root()) / path
    return str(candidate) if candidate.is_file() else path


def save_json(params: HandwritingParams, path: str | Path) -> None:
    """将参数保存为结构化 JSON 预设文件。

    仅保存排版参数（不含文本内容），颜色以 #RRGGBB 十六进制保存。
    version 3 新增 roles。
    """
    data: dict[str, Any] = {
        "version": 3 if params.roles is not None else 2,
        "params": params.to_preset_dict(),
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
    """自动识别预设文件格式并加载（JSON 或旧版纯文本）。

    预设中的相对路径（便携模式）按资产根目录解析为绝对路径。
    """
    path = Path(path)
    if path.suffix.lower() == ".json":
        params = load_json(path)
    else:
        params = load_legacy(path)
    params.font_path = from_portable_path(params.font_path)
    params.background_path = from_portable_path(params.background_path)
    if params.roles:
        for role in params.roles:
            role.font_path = from_portable_path(role.font_path)
    return params


def save(path: str | Path, params: HandwritingParams) -> None:
    """根据文件扩展名选择保存格式（默认 JSON）。

    资产根目录内的字体/背景路径保存为相对路径（便携模式），
    不修改调用方传入的参数对象。
    """
    path = Path(path)
    portable = copy.copy(params)
    portable.font_path = to_portable_path(portable.font_path)
    portable.background_path = to_portable_path(portable.background_path)
    if portable.roles is not None:
        # 角色字体也做便携化，深拷贝角色列表
        portable.roles = [copy.copy(r) for r in portable.roles]
        for r in portable.roles:
            r.font_path = to_portable_path(r.font_path)
    if path.suffix.lower() in (".txt", ".preset"):
        data = portable.to_lines()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(data) + "\n")
    else:
        save_json(portable, path)