"""手写参数模型。

将原 GUI 中散落的拼音缩写参数（zspjj、ztdx 等）规范化为
带类型标注、默认值与校验的 dataclass，供 GUI 与 CLI 共用。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, fields
from pathlib import Path
from typing import Any, Iterable


@dataclass
class Paragraph:
    """单个段落的排版信息。"""

    text: str = ""
    align: str = "left"          # "left" | "center"
    first_line_indent: int = 0   # 首行缩进（像素）


@dataclass
class HandwritingParams:
    """一次手写模拟的完整参数。"""

    # ---- 输入 ----
    font_path: str = ""
    background_path: str = ""
    text: str = ""
    paragraphs: list[Paragraph] | None = None  # 非空时启用段落渲染

    # ---- 字体颜色 (RGB) ----
    red: int = 0
    green: int = 0
    blue: int = 0

    # ---- 排版 ----
    font_size: int = 36          # 字体大小
    word_spacing: int = 5        # 字间距
    line_spacing: int = 48       # 行间距（不含字高）
    left_margin: int = 30
    right_margin: int = 30
    top_margin: int = 30
    bottom_margin: int = 30

    # ---- 随机扰动 ----
    word_spacing_sigma: int = 2      # 字间距随机扰动
    line_spacing_sigma: int = 2      # 行间距随机扰动
    font_size_sigma: int = 2         # 字体大小随机扰动
    perturb_x_sigma: int = 2         # 笔画横向偏移扰动
    perturb_y_sigma: int = 2         # 笔画纵向偏移扰动
    perturb_theta_sigma: float = 0.05  # 笔画旋转偏移扰动

    # ---- 排版细节 ----
    end_chars: str = "，。"
    start_chars: str = ""

    # ------------------------------------------------------------------
    # 便捷属性
    # ------------------------------------------------------------------
    @property
    def fill(self) -> tuple[int, int, int]:
        """字体颜色三元组。"""
        return (self.red, self.green, self.blue)

    @property
    def total_line_spacing(self) -> int:
        """handright 的 line_spacing 需包含字高。"""
        return int(self.line_spacing) + int(self.font_size)

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------
    class ValidationError(ValueError):
        """参数校验失败。"""

    def validate(self, *, require_text: bool = True) -> None:
        """校验参数是否完整、合法，失败抛出 ValidationError。"""
        if require_text and not self.text.strip():
            raise self.ValidationError("未输入要处理的文字")
        if not self.font_path:
            raise self.ValidationError("未指定字体文件")
        if not Path(self.font_path).is_file():
            raise self.ValidationError(f"字体文件不存在：{self.font_path}")
        if not self.background_path:
            raise self.ValidationError("未指定背景图片")
        if not Path(self.background_path).is_file():
            raise self.ValidationError(f"背景图片不存在：{self.background_path}")
        for name in (
            "font_size", "word_spacing", "line_spacing",
            "left_margin", "right_margin", "top_margin", "bottom_margin",
            "word_spacing_sigma", "line_spacing_sigma", "font_size_sigma",
            "perturb_x_sigma", "perturb_y_sigma",
        ):
            if getattr(self, name) < 0:
                raise self.ValidationError(f"{name} 不能为负")
        if self.perturb_theta_sigma < 0:
            raise self.ValidationError("perturb_theta_sigma 不能为负")

    # ------------------------------------------------------------------
    # 序列化（保留旧版 18 行文本格式，兼容 GUI 的保存/载入预设）
    # ------------------------------------------------------------------
    _FIELD_ORDER_18: tuple[str, ...] = (
        "red", "green", "blue", "font_path", "background_path",
        "word_spacing", "word_spacing_sigma", "line_spacing",
        "line_spacing_sigma", "font_size", "font_size_sigma",
        "perturb_x_sigma", "perturb_y_sigma", "perturb_theta_sigma",
        "top_margin", "left_margin", "right_margin", "bottom_margin",
    )

    def to_lines(self) -> list[str]:
        """导出为 18 行纯文本（兼容旧预设文件格式）。"""
        lines: list[str] = []
        for name in self._FIELD_ORDER_18:
            value = getattr(self, name)
            lines.append(str(value) if isinstance(value, (int, float)) else str(value))
        return lines

    @classmethod
    def from_lines(cls, lines: Iterable[str]) -> "HandwritingParams":
        """从 18 行纯文本载入参数（兼容旧预设文件格式）。"""
        # 保留空字段（如未填写的路径），仅去除行尾换行符
        values = [ln.rstrip("\n").rstrip("\r") for ln in lines]
        values = values[: len(cls._FIELD_ORDER_18)]
        if len(values) < len(cls._FIELD_ORDER_18):
            raise cls.ValidationError("预设文件字段不足，可能已损坏")
        params = cls()
        for name, raw in zip(cls._FIELD_ORDER_18, values):
            current = getattr(params, name)
            setattr(params, name, _coerce(raw, current))
        return params

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HandwritingParams":
        known = {f.name for f in fields(cls)}
        clean: dict[str, Any] = {k: v for k, v in data.items() if k in known}
        if isinstance(clean.get("paragraphs"), list):
            clean["paragraphs"] = [
                p if isinstance(p, Paragraph) else Paragraph(**p)
                for p in clean["paragraphs"]
            ]
        return cls(**clean)

    def __str__(self) -> str:  # 便于日志
        return ", ".join(f"{f.name}={getattr(self, f.name)}" for f in fields(self))


def _coerce(raw: str, current: Any) -> Any:
    """按目标字段当前类型转换字符串；失败时抛校验错误。"""
    try:
        if isinstance(current, bool):
            return raw.lower() in ("1", "true", "yes")
        if isinstance(current, int):
            return int(raw)
        if isinstance(current, float):
            return float(raw)
        return raw
    except ValueError as exc:
        raise HandwritingParams.ValidationError(
            f"字段值无法解析为 {type(current).__name__}：{raw!r}"
        ) from exc