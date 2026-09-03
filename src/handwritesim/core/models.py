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


# ---------------------------------------------------------------------------
# 多角色 / Run 片段（对齐 Rust 版 TextRun / HandwritingRole）
# ---------------------------------------------------------------------------

@dataclass
class HandwritingRole:
    """笔迹角色槽位。

    id 0 = 默认手写（跟随主字体/颜色/扰动）
    id 1 = 打印体（零扰动、规整排版）
    id >=2 = 手写角色 1..N（动态映射：文档中首次出现的背景高亮→角色2，次出现→角色3 …）
    用户不限制必须用黄/绿，任意颜色均按出现顺序分配。
    """

    id: int = 0
    name: str = ""
    highlight: str | None = None   # 绑定的高亮颜色名（如 "yellow"；文档导入时自动关联角色用）
    font_path: str = ""          # 空 = 跟随主字体
    font_size: int = 0           # 0 = 跟随主字号
    color: str | None = None     # None = 跟随主颜色；#RRGGBB
    printed: bool = False        # True = 打印体，强制零扰动、零错字
    word_spacing: int | None = None   # 字水平间距（None = 跟随全局）
    line_spacing: int | None = None   # 行间距（None = 跟随全局）
    # 可选扰动覆盖（None = 跟随全局）
    font_size_sigma: int | None = None
    word_spacing_sigma: int | None = None
    line_spacing_sigma: int | None = None
    perturb_x_sigma: int | None = None
    perturb_y_sigma: int | None = None
    perturb_theta_sigma: float | None = None


@dataclass
class TextRun:
    """段落内一串连续样式相同的文字片段。"""

    text: str = ""
    role_id: int = 0             # 指向 HandwritingParams.roles 的 id
    color: str | None = None     # 可选：直接 #RRGGBB 覆盖（来自 w:color），None=跟随角色/全局
    font_family: str | None = None  # 来自 docx w:rFonts（如 宋体/微软雅黑），打印体时有效
    font_size: int | None = None    # 字号（与全局字号同坐标系：docx 按文档内比例映射），打印体时有效；None=跟随角色/全局
    font_file: str | None = None    # 若能解析到系统字体文件，存绝对路径；否则保留 family 供后续让用户自选
    bold: bool = False              # 来自 w:b / w:bCs，打印体加粗（手写体忽略）


@dataclass
class Paragraph:
    """单个段落的排版信息。支持纯文本（text）或多 Run 流式混排（runs）。"""

    text: str = ""
    align: str = "left"          # "left" | "center" | "right"
    first_line_indent: int = 0   # 首行缩进（像素）
    runs: list[TextRun] | None = None  # 非空时启用段内多 Run 混排

    def effective_runs(self) -> list[TextRun]:
        """返回可用于排版的有效 Run 列表（兼容旧 text）。"""
        if self.runs is not None:
            return self.runs
        if self.text:
            return [TextRun(text=self.text, role_id=0)]
        return []

    def is_mixed(self) -> bool:
        """是否包含多种角色的混排。"""
        if self.runs is None or len(self.runs) <= 1:
            return False
        first = self.runs[0].role_id
        return any(r.role_id != first for r in self.runs)

    def plain_text(self) -> str:
        """拼接全部 Run/文本为纯文本（用于校验/导出）。"""
        if self.runs is not None:
            return "".join(r.text for r in self.runs)
        return self.text


@dataclass
class TextRegion:
    """页面上一个框选文字区域（实验特性：手写/打印混排）。

    坐标为背景图原始像素坐标（与预览降采样无关，GUI 负责换算）。
    文字在矩形内自行换行，仅在指定所在页渲染，超出框选范围的内容自然截断。
    page 为区域所在页（1 基）：1 = 第一页。
    """

    x: int = 0                   # 区域左上角横坐标
    y: int = 0                   # 区域左上角纵坐标
    w: int = 0                   # 区域宽
    h: int = 0                   # 区域高
    text: str = ""               # 区域内文字
    role_id: int = 0             # 绑定的角色 ID（0 默认手写 / 1 打印体 / >=2 手写角色）
    highlight: str | None = None  # 来源高亮颜色名（PDF/DOCX 底图自动识别时记录，如 "yellow"）
    font_path: str = ""          # 区域独立字体；空 = 使用主字体
    printed: bool = False        # True = 打印体（零扰动、规整排版）
    font_size: int = 0           # 区域字号；0 = 跟随主设置
    page: int = 1                # 所在页（1 基）；1 = 第一页
    align: str = "left"          # 对齐方式："left" | "center" | "right"
    indent_em: float = 0.0       # 首行缩进（字符数 em；0 = 无）
    paragraphs: list[Paragraph] | None = None  # 区域内各段落排版信息（各段独立对齐与缩进）

    # ---- 逐区域排版/扰动覆盖项（None = 跟随主设置）----
    word_spacing: int | None = None
    line_spacing: int | None = None
    font_size_sigma: int | None = None
    word_spacing_sigma: int | None = None
    line_spacing_sigma: int | None = None
    perturb_x_sigma: int | None = None
    perturb_y_sigma: int | None = None
    perturb_theta_sigma: float | None = None
    miswrite_rate: float | None = None
    miswrite_strikeout_style: str | None = None  # "line" | "double_line" | "slash" | "cross"
    color: str | None = None                     # 文字颜色覆盖（#RRGGBB 十六进制）

    # ---- 区域内边距（像素；None 或 0 = 紧贴框边界，默认 0）----
    margin_top: int | None = None
    margin_bottom: int | None = None
    margin_left: int | None = None
    margin_right: int | None = None

    def has_overrides(self) -> bool:
        """是否设置了任意一项逐区域覆盖（排版、扰动、错字、颜色、内边距）。"""
        return (
            self.word_spacing is not None
            or self.line_spacing is not None
            or self.font_size_sigma is not None
            or self.word_spacing_sigma is not None
            or self.line_spacing_sigma is not None
            or self.perturb_x_sigma is not None
            or self.perturb_y_sigma is not None
            or self.perturb_theta_sigma is not None
            or self.miswrite_rate is not None
            or self.miswrite_strikeout_style is not None
            or self.color is not None
            or (self.margin_top is not None and self.margin_top > 0)
            or (self.margin_bottom is not None and self.margin_bottom > 0)
            or (self.margin_left is not None and self.margin_left > 0)
            or (self.margin_right is not None and self.margin_right > 0)
        )

    def label(self, index: int) -> str:
        """区域列表里的一行摘要。"""
        style = "打印" if self.printed else "手写"
        page = f" 第{self.page}页" if self.page > 1 else ""
        return f"{index}. {style}{page} {len(self.text)}字 ({self.x},{self.y} {self.w}×{self.h})"


def default_roles() -> list[HandwritingRole]:
    """返回内置默认角色（仅 默认手写 + 打印体）。

    手写角色1/2 等不再预置，按 docx 实际出现的高亮/标签动态生成，
    避免空文档也显示多余角色。
    """
    return [
        HandwritingRole(id=0, name="默认手写", printed=False, color=None),
        HandwritingRole(id=1, name="打印体", printed=True, color="#333333"),
    ]


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
    roles: list[HandwritingRole] | None = None  # 多角色槽位；None=使用 default_roles()

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

    def effective_roles(self) -> list[HandwritingRole]:
        """返回生效的角色列表（None 时返回默认 2 角色）。"""
        if self.roles is None:
            return default_roles()
        return self.roles

    def role_by_id(self, rid: int) -> HandwritingRole | None:
        for r in self.effective_roles():
            if r.id == rid:
                return r
        return None

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
            or bool(self.paragraphs and any(p.plain_text().strip() or p.text.strip() for p in self.paragraphs))
            or any(r.text.strip() for r in self.regions or [])
            or bool(self.paragraphs and any(p.runs and any(rr.text.strip() for rr in p.runs) for p in self.paragraphs))
        )
        # 额外：paragraphs 含 runs 时也算有内容
        if self.paragraphs:
            for p in self.paragraphs:
                if p.runs and any(rr.text.strip() for rr in p.runs):
                    has_content = True
                    break
                if p.text.strip():
                    has_content = True
                    break
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
        # 校验 roles
        if self.roles is not None:
            seen_ids: set[int] = set()
            for role in self.roles:
                if role.id < 0:
                    raise self.ValidationError(f"角色 id 不能为负：{role.id}")
                if role.id in seen_ids:
                    raise self.ValidationError(f"角色 id 重复：{role.id}")
                seen_ids.add(role.id)
                if role.font_size < 0:
                    raise self.ValidationError(f"角色 {role.id} 的字号不能为负")
                if role.font_path and not Path(role.font_path).is_file():
                    raise self.ValidationError(f"角色 {role.id} 的字体文件不存在：{role.font_path}")
                if role.color is not None:
                    try:
                        parse_color(role.color)
                    except ValueError as exc:
                        raise self.ValidationError(f"角色 {role.id} 的颜色格式无效：{exc}") from exc
                for s_name in ("font_size_sigma", "word_spacing_sigma", "line_spacing_sigma", "perturb_x_sigma", "perturb_y_sigma"):
                    s_val = getattr(role, s_name)
                    if s_val is not None and s_val < 0:
                        raise self.ValidationError(f"角色 {role.id} 的 {s_name} 不能为负")
                if role.perturb_theta_sigma is not None and role.perturb_theta_sigma < 0:
                    raise self.ValidationError(f"角色 {role.id} 的 perturb_theta_sigma 不能为负")
        # 校验 paragraphs（含 runs）
        if self.paragraphs:
            for idx, para in enumerate(self.paragraphs, start=1):
                if para.align not in ("left", "center", "right"):
                    raise self.ValidationError(f"段落 {idx} 的未知对齐方式：{para.align!r}")
                if para.first_line_indent < 0:
                    raise self.ValidationError(f"段落 {idx} 的首行缩进不能为负")
                if para.runs is not None:
                    for ri, run in enumerate(para.runs, start=1):
                        if run.role_id < 0:
                            raise self.ValidationError(f"段落 {idx} 的第 {ri} 个 Run 的 role_id 不能为负")
                        # role 存在性不强制，渲染时回落 0
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
            if region.align not in ("left", "center", "right"):
                raise self.ValidationError(
                    f"文字区域 {i} 的未知对齐方式：{region.align!r}，可选 left/center/right"
                )
            if region.indent_em < 0:
                raise self.ValidationError(f"文字区域 {i} 的首行缩进不能为负")
            for m_name in ("margin_top", "margin_bottom", "margin_left", "margin_right"):
                m_val = getattr(region, m_name)
                if m_val is not None and m_val < 0:
                    raise self.ValidationError(f"文字区域 {i} 的 {m_name} 不能为负")
            for s_name in (
                "word_spacing", "line_spacing", "word_spacing_sigma",
                "line_spacing_sigma", "font_size_sigma", "perturb_x_sigma",
                "perturb_y_sigma",
            ):
                s_val = getattr(region, s_name)
                if s_val is not None and s_val < 0:
                    raise self.ValidationError(f"文字区域 {i} 的 {s_name} 不能为负")
            if region.perturb_theta_sigma is not None and region.perturb_theta_sigma < 0:
                raise self.ValidationError(f"文字区域 {i} 的 perturb_theta_sigma 不能为负")
            if region.miswrite_rate is not None and not 0.0 <= region.miswrite_rate <= 1.0:
                raise self.ValidationError(f"文字区域 {i} 的 miswrite_rate 必须在 0~1 之间")
            if region.miswrite_strikeout_style is not None and region.miswrite_strikeout_style not in (
                "line", "double_line", "slash", "cross"
            ):
                raise self.ValidationError(
                    f"文字区域 {i} 的未知涂改方式：{region.miswrite_strikeout_style!r}，可选 line/double_line/slash/cross"
                )
            if region.color is not None:
                try:
                    parse_color(region.color)
                except ValueError as exc:
                    raise self.ValidationError(f"文字区域 {i} 的颜色格式无效：{exc}") from exc
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
        """导出预设字段：不含 text/paragraphs，颜色以 #RRGGBB 十六进制保存；包含 roles。"""
        data = {name: getattr(self, name) for name in self._PRESET_FIELDS}
        data["color"] = self.color
        if self.roles is not None:
            data["roles"] = [asdict(r) for r in self.roles]
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
        # roles
        if isinstance(clean.get("roles"), list):
            clean["roles"] = [
                r if isinstance(r, HandwritingRole) else HandwritingRole(**r)
                for r in clean["roles"]
            ]
        if isinstance(clean.get("paragraphs"), list):
            paras = []
            for p in clean["paragraphs"]:
                if isinstance(p, Paragraph):
                    paras.append(p)
                elif isinstance(p, dict):
                    p_dict = dict(p)
                    if isinstance(p_dict.get("runs"), list):
                        p_dict["runs"] = [
                            r if isinstance(r, TextRun) else TextRun(**r)
                            for r in p_dict["runs"]
                        ]
                    paras.append(Paragraph(**p_dict))
            clean["paragraphs"] = paras
        if isinstance(clean.get("regions"), list):
            clean_regions = []
            for r in clean["regions"]:
                if isinstance(r, TextRegion):
                    clean_regions.append(r)
                elif isinstance(r, dict):
                    r_dict = dict(r)
                    if isinstance(r_dict.get("paragraphs"), list):
                        r_dict["paragraphs"] = [
                            p if isinstance(p, Paragraph) else Paragraph(**p)
                            for p in r_dict["paragraphs"]
                        ]
                    # 兼容旧 region paragraphs 内的旧格式
                    if isinstance(r_dict.get("paragraphs"), list):
                        fixed = []
                        for pp in r_dict["paragraphs"]:
                            if isinstance(pp, dict):
                                if isinstance(pp.get("runs"), list):
                                    pp = dict(pp)
                                    pp["runs"] = [rr if isinstance(rr, TextRun) else TextRun(**rr) for rr in pp["runs"]]
                                    fixed.append(Paragraph(**pp))
                                else:
                                    fixed.append(pp if isinstance(pp, Paragraph) else Paragraph(**pp))
                            else:
                                fixed.append(pp)
                        r_dict["paragraphs"] = fixed
                    clean_regions.append(TextRegion(**r_dict))
            clean["regions"] = clean_regions
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
