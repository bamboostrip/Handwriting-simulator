# 框选区域功能对齐 Rust 版设计文档 (Spec)

## 1. 目标与背景

Rust 项目（`handwrite-sim`）在原 Python 版的基础上，对**框选文字区域（TextRegion）**进行了深度演进与完善，包含：
1. 区域内支持多段落独立排版（各段独立对齐方式、首行缩进）；
2. 区域内支持 4 向内边距（`margin_top`, `margin_bottom`, `margin_left`, `margin_right`）；
3. 区域支持逐区域排版与扰动参数覆盖（字间距、行间距、字号、随机扰动 σ、写错字率、涂改方式、文字颜色 `#RRGGBB`），未设置时跟随主参数；打印体强制规整化（零扰动、零错字）；
4. 区域文字在所属单页内排版与渲染，超出框选范围的内容自然截断（不再跨页延伸）；
5. 区域属性对话框（`RegionDialog`）提供富文本行编辑、段落对齐/缩进工具栏、行状态提示、一键导入 docx 文档段落、以及折叠式排版与扰动覆盖面板。

本设计将 Python 版（`Handwriting-simulator`）完全对齐上述功能与数据模型规范。

---

## 2. 数据模型变更 (`models.py`)

### 2.1 `TextRegion` 扩展字段
```python
@dataclass
class TextRegion:
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    text: str = ""
    font_path: str = ""
    printed: bool = False
    font_size: int = 0
    page: int = 1
    align: str = "left"                       # "left" | "center" | "right" (默认左对齐)
    indent_em: float = 0.0                    # 首行缩进（字符数 em，默认 0.0）
    paragraphs: list[Paragraph] | None = None # 区域内各段落排版信息（各段独立设置对齐与缩进）

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
    color: str | None = None                     # "#RRGGBB" 颜色覆盖

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
```

### 2.2 序列化与校验扩展
- `HandwritingParams.validate()` 增加对 `region.align`, `region.miswrite_strikeout_style`, `region.color`, `margin_*` 等字段的合法性校验。
- `to_dict` / `from_dict` 兼容新字段与 `Paragraph` 反序列化。

---

## 3. 核心排版与渲染对齐 (`engine_fast.py`)

### 3.1 `_region_params` 局部参数构造
1. **基础参数覆盖**：
   - 文本与段落：若 `region.paragraphs` 存在且非空，直接应用；否则根据 `region.align`、`region.indent_em` 或多行换行自动转为 `Paragraph` 列表。
   - 边距：`left_margin = region.margin_left or 0`，`right_margin = region.margin_right or 0`，`top_margin = region.margin_top or 0`，`bottom_margin = region.margin_bottom or 0`。
   - 字体与字号：`font_path`，`font_size`（若大于 0）。
2. **逐区域覆盖项生效**：
   - `word_spacing`, `line_spacing`, 各项扰动 σ、`miswrite_rate`、`miswrite_strikeout_style`、`color`（解析为 `red, green, blue`）。
3. **打印体规整化**：
   - 若 `region.printed = True`，清零全部扰动 σ（`= 0`）与错字率（`= 0.0`），确保规整排版优先级最高。

### 3.2 区域排版与合成 (`_pages_with_regions`)
- 区域文字只在其指定的 `target_page = max(1, region.page) - 1` 页面内排版并绘制，单页超出框选矩形范围的内容自然截断（不再跨页延伸）。
- 区域排版支持调用 `_paragraph_page_masks` 或单段 `_layout_paragraph` 生成 `(rw, rh)` 局部单页掩码。
- 随机源消费顺序固定：`random.Random(f"{self._seed}|region{index}")` 保证可重复性。

---

## 4. 对话框与图形界面重构 (`region_dialog.py` & `main_window.py`)

### 4.1 `RegionDialog`
1. **工具栏**：
   - 「左对齐」「居中」「右对齐」按钮：设置当前光标所在行的对齐。
   - 「首行缩进 / 取消缩进」按钮：切换当前光标所在行的 2 字符缩进。
   - 「导入 docx」按钮：调用 `load_paragraphs` 读取 docx 并自动将段落文本、对齐与缩进加载进编辑器。
2. **段落行状态行**：
   - 实时显示当前光标所在行的字数、对齐方式、缩进状态（如 `第 1 行（15 字）：左对齐，首行缩进 2 字`）。
3. **富文本段落编辑器**：
   - 基于 `QTextEdit`，光标变动时同步更新工具栏选中态与状态行，回车分段保持或重置格式。
4. **基础设置网格**：
   - 样式单选（手写体 / 打印体）、所在页输入框（带单页截断提示）、打印字体文件选择器、字号输入框（0 表示跟随主设置）。
5. **折叠面板「排版与扰动覆盖」**：
   - 标题带「跟随主设置」/「已自定义」状态提示。
   - 排版参数卡片：字间距、行间距、字号（及各自随机扰动）。
   - 笔画扰动卡片：水平位移 σ、竖直位移 σ、笔画旋转 σ。
   - 写错字卡片：错字率（0~30%）、涂改方式（跟随主设置 / 单横线 / 双横线 / 斜线 / 叉号）。
   - 文字颜色卡片：`QColorDialog` 选择颜色、`#RRGGBB` 十六进制输入框、「重置跟随」按钮。
   - 边距卡片：上、下、左、右 4 向内边距输入框。

### 4.2 主窗口联动
- 新建 / 双击编辑文字区域时，完整传参并在确定后更新 `TextRegion`。
- 区域列表摘要显示：若有自定义覆盖项可给予标识或在 tooltip 中展示。

---

## 5. 测试与验证策略

1. **单元测试**：
   - `test_models.py` / `test_regions.py`：测试 `TextRegion` 新字段校验、`has_overrides`、段落与边距解析。
   - 区域独立对齐（左/中/右）与缩进渲染测试。
   - 区域内边距生效测试（墨迹落在边距内侧）。
   - 区域参数覆盖生效测试（独立颜色、独立字距/行距/扰动/错字率）。
   - 区域单页截断测试（超长文本在单页截断且不延伸至下一页）。
   - 区域 docx 导入多段落测试。
2. **GUI 一致性验证**：
   - 运行全部既有测试，确保 105+ 测试项全部通过，零回归。
