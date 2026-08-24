"""手写参数模型。

将原 GUI 中散落的拼音缩写参数（zspjj、ztdx 等）规范化为
带类型标注、默认值与校验的 dataclass，供 GUI 与 CLI 共用。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, fields
from pathlib import Path
from typing import Any, Iterable


def parse_color(value: str) -> tuple[int, int, int]:
    """解析 #RRGGBB 或 RRGGBB 颜色值为 RGB 三元组，失败抛 ValueError。"""
    text = str(value).strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"颜色值应为 #RRGGBB 格式：{value!r}")
    try:
        return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError as exc:
        raise ValueError(f"颜色值应为 #RRGGBB 格式：{value!r}") from exc


@dataclass
class Paragraph:
    """单个段落的排版信息。"""

    text: str = ""
    align: str = "left"          # "left" | "center"
    first_line_indent: int = 0   # 首行缩进（像素）


@dataclass
class TextRegion:
    """页面上一个框选文字区域（实验特性：手写/打印混排）。

    坐标为背景图原始像素坐标（与预览降采样无关，GUI 负责换算）。
    文字在矩形内自行换行，放不下时流式延续到下一页的同一矩形。
    page 为区域起始页（1 基）：区域文字从该页开始渲染，超出矩形
    时继续向后续页面延伸；默认 1 = 第一页。
    """

    x: int = 0                   # 区域左上角横坐标
    y: int = 0                   # 区域左上角纵坐标
    w: int = 0                   # 区域宽
    h: int = 0                   # 区域高
    text: str = ""               # 区域内文字
    font_path: str = ""          # 区域独立字体；空 = 使用主字体
    printed: bool = False        # True = 打印体（零扰动、规整排版）
    font_size: int = 0           # 区域字号；0 = 跟随主设置
    page: int = 1                # 起始页（1 基）；1 = 第一页

    def label(self, index: int) -> str:
        """区域列表里的一行摘要。"""
        style = "打印" if self.printed else "手写"
        page = f" 第{self.page}页" if self.page > 1 else ""
        return f"{index}. {style}{page} {len(self.text)}字 ({self.x},{self.y} {self.w}×{self.h})"


@dataclass
class HandwritingParams:
    """一次手写模拟的完整参数。"""

    # ---- 输入 ----
    font_path: str = ""
    background_path: str = ""
    # 多页文档背景（如导入的 PDF/DOCX 打印预览，每页一张）；
    # 为空时所有页使用 background_path 单张背景
    background_pages: list[str] | None = None
    text: str = ""
    paragraphs: list[Paragraph] | None = None  # 非空时启用段落渲染
    regions: list[TextRegion] | None = None    # 非空时在框选矩形内渲染（可与主文字并存）

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

    # ---- 写错字模拟 ----
    miswrite_rate: float = 0.0        # 每字符被判定为错字的概率（0~1，UI 中为 0~30%）
    miswrite_rewrite_mode: str = "above"   # "above"（右上方小字重写）| "rewrite"（后文正常位置重写）
    miswrite_strikeout_style: str = "line"  # "line" | "double_line" | "slash" | "cross"

    # ------------------------------------------------------------------
    # 便捷属性
    # ------------------------------------------------------------------
    @property
    def fill(self) -> tuple[int, int, int]:
        """字体颜色三元组。"""
        return (self.red, self.green, self.blue)

    @property
    def color(self) -> str:
        """字体颜色十六进制表示（#RRGGBB）。"""
        return f"#{self.red:02x}{self.green:02x}{self.blue:02x}"

    @color.setter
    def color(self, value: str) -> None:
        """按 #RRGGBB 十六进制设置字体颜色。"""
        self.red, self.green, self.blue = parse_color(value)

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
        """校验参数是否完整、合法，失败抛出 ValidationError。

        require_text=False 时允许"纯背景预览"：没有文字/区域时不要求
        字体（一个字都不画），但仍要求背景文件有效。
        """
        has_content = (
            bool(self.text.strip())
            or bool(self.paragraphs)
            or any(r.text.strip() for r in self.regions or [])
        )
        if require_text and not has_content:
            raise self.ValidationError("未输入要处理的文字")
        if has_content and not self.font_path:
            raise self.ValidationError("未指定字体文件")
        if has_content and not Path(self.font_path).is_file():
            raise self.ValidationError(f"字体文件不存在：{self.font_path}")
        if not self.background_path:
            raise self.ValidationError("未指定背景图片")
        if not Path(self.background_path).is_file():
            raise self.ValidationError(f"背景图片不存在：{self.background_path}")
        for i, page_bg in enumerate(self.background_pages or [], start=1):
            if not Path(page_bg).is_file():
                raise self.ValidationError(f"第 {i} 页背景文件不存在：{page_bg}")
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
        if not 0.0 <= self.miswrite_rate <= 1.0:
            raise self.ValidationError("miswrite_rate 必须在 0~1 之间")
        for i, region in enumerate(self.regions or [], start=1):
            if region.w <= 0 or region.h <= 0:
                raise self.ValidationError(f"文字区域 {i} 的宽高必须为正")
            if region.x < 0 or region.y < 0:
                raise self.ValidationError(f"文字区域 {i} 的坐标不能为负")
            if region.page < 1:
                raise self.ValidationError(f"文字区域 {i} 的页码必须从 1 开始")
            if region.font_path and not Path(region.font_path).is_file():
                raise self.ValidationError(f"文字区域 {i} 的字体文件不存在：{region.font_path}")
            if region.font_size < 0:
                raise self.ValidationError(f"文字区域 {i} 的字号不能为负")
        if self.miswrite_rewrite_mode not in ("above", "rewrite"):
            raise self.ValidationError(
                f"未知重写方式：{self.miswrite_rewrite_mode!r}，可选 above/rewrite"
            )
        if self.miswrite_strikeout_style not in ("line", "double_line", "slash", "cross"):
            raise self.ValidationError(
                f"未知涂改方式：{self.miswrite_strikeout_style!r}，可选 line/double_line/slash/cross"
            )
        for name in ("red", "green", "blue"):
            value = getattr(self, name)
            if not isinstance(value, int) or not 0 <= value <= 255:
                raise self.ValidationError(f"{name} 必须在 0-255 之间")

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

    # 预设仅保存排版参数，不含文本内容（text/paragraphs/regions 不属于预设范围）
    _PRESET_FIELDS: tuple[str, ...] = (
        "font_path", "background_path",
        "font_size", "word_spacing", "line_spacing",
        "left_margin", "right_margin", "top_margin", "bottom_margin",
        "word_spacing_sigma", "line_spacing_sigma", "font_size_sigma",
        "perturb_x_sigma", "perturb_y_sigma", "perturb_theta_sigma",
        "end_chars", "start_chars",
        "miswrite_rate", "miswrite_rewrite_mode", "miswrite_strikeout_style",
    )

    def to_dict(self) -> dict[str, Any]:
        """完整序列化（含文本内容），供测试/内存往返使用。"""
        return asdict(self)

    def to_preset_dict(self) -> dict[str, Any]:
        """导出预设字段：不含 text/paragraphs，颜色以 #RRGGBB 十六进制保存。"""
        data = {name: getattr(self, name) for name in self._PRESET_FIELDS}
        data["color"] = self.color
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HandwritingParams":
        data = dict(data)
        # 兼容新版 #RRGGBB 颜色值与旧版 red/green/blue 三个数字
        if "color" in data:
            try:
                data["red"], data["green"], data["blue"] = parse_color(data["color"])
            except ValueError as exc:
                raise cls.ValidationError(str(exc)) from exc
        data.pop("color", None)
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