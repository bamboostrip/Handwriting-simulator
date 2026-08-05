# 设计：段落化排版（标题居中 / 首段缩进）

日期：2026-08-05
分支：feature/paragraph-layout
状态：已批准

## 目标

为手写模拟器增加快捷的段落化排版能力，支持公文式排版：**标题居中、正文段落首行缩进、标题不缩进**。提供三个入口：

1. GUI 富文本编辑器（居中 / 左对齐 / 首行缩进工具按钮）
2. GUI 导入 docx（解析对齐 + 首行缩进）
3. CLI `--docx` 参数

## 核心思路（方案 A：段落独立渲染 + 拼接）

将文档拆分为"段落"列表，每段记录自己的对齐方式与首行缩进。每段**独立渲染为透明图层**（段落内才换行），居中段按行测量后水平置中，缩进段加大首行起点，最后按顺序纵向拼接到背景上。段落作为跨页最小单位。

不破坏现有 `_layout_page` 纯文本渲染路径，保持向后兼容。

## 数据模型 — `core/models.py`

新增段落结构：

```python
@dataclass
class Paragraph:
    text: str
    align: str = "left"          # "left" | "center"
    first_line_indent: int = 0   # 首行缩进（像素）

# HandwritingParams 新增字段：
paragraphs: list[Paragraph] | None = None   # 非空时优先段落渲染
```

- 当 `paragraphs` 非空时启用段落渲染；否则回退到既有 `text` 纯文本渲染。
- `text` 字段保留，向后兼容（预设、CLI 纯文本路径不受影响）。

## docx 解析 — 新增 `core/docx_io.py`

```python
def load_paragraphs(path: str | Path) -> list[Paragraph]
```

- 遍历 `doc.paragraphs`，忽略空白段落。
- 对齐：`paragraph.alignment == WD_ALIGN_PARAGRAPH.CENTER` → `"center"`，其余 → `"left"`。
- 首行缩进：`paragraph_format.first_line_indent`（EMU 单位），按 96dpi 转像素：
  `px = emu / 914400 * 96`。
- 依赖 `python-docx`。

## 富文本编辑器 — `gui/ui.py` + `gui/main_window.py`

- 在文本输入框上方加一排工具按钮：**居中 / 左对齐 / 首行缩进** 与 **导入 docx**。
- 用 `QTextBlockFormat` 设置 / 读取每段格式：
  - 对齐：`blockFormat().setAlignment(Qt.AlignCenter / Qt.AlignLeft)`。
  - 首行缩进：`blockFormat().setTextIndent(px)`。
- 收集参数（`collect_params`）：遍历 `textEdit.document()` 的 `QTextBlock`，读取
  `blockFormat().alignment()` 与 `.textIndent()`，生成 `Paragraph` 列表赋给 `params.paragraphs`。
- 导入 docx：`QFileDialog` 选 docx → `load_paragraphs` → 将段落回填为富文本（`QTextCursor`
  逐段设置块格式），实现所见即所得。
- 工具按钮仅在选中/当前段生效，遵循 QTextEdit 标准块编辑交互。

## 渲染引擎 — `core/engine_fast.py`（核心）

新增段落渲染函数，不修改现有 `_layout_page`：

```python
def _layout_paragraph(
    params, rand, paragraph, width, height,
) -> tuple[np.ndarray, int]:
    """渲染单个段落，返回该段 mask 与其占用高度。"""
```

- 逐行排版，复用现有逐字绘制 / 字距 / end_chars / start_chars 换行逻辑。
- **首行**起点：`x = left + first_line_indent`；后续行起点：`x = left`。
- **居中段**：每行渲染后，按行测量该行非零像素的 x 范围，逐行平移使其居中。
- 返回该段 mask（从 y=0 布局）和段高。

逐页组装（`FastEngine` 新增路径）：

```python
def _generate_paragraph_pages(self, params) -> Iterator[Image.Image]:
    # 段落按顺序流式放入页面；current_y 累计；
    # 若 current_y + 段高 > height - bottom，则换新页；
    # 整页合成 mask 后复用 _perturb_mask 做笔画扰动。
```

- `render_preview` / `generate_pages` 增加分支：`params.paragraphs` 非空时走段落渲染路径。

## CLI — `cli.py`

新增 `--docx` 参数：

```python
parser.add_argument("--docx", default="", help="导入 docx 文件（解析对齐与首行缩进）")
```

- 指定 `--docx` 时，调用 `load_paragraphs` 填充 `params.paragraphs`，优先段落渲染。

## 依赖

- 新增 `python-docx`，通过 `uv add python-docx` 写入 `pyproject.toml`。

## 测试

- `tests/test_docx_io.py`：构造 docx（python-docx 写入），验证对齐与首行缩进解析。
- `tests/test_engine_fast.py` 扩展：段落渲染路径（居中段、缩进段、跨页）不抛错且输出尺寸正确。
- 回归：纯文本路径（`paragraphs=None`）行为不变。

## 兼容性

- `HandwritingParams` 新增可选字段，序列化时 `to_dict`/`from_dict` 需兼容 `paragraphs=None`。
- 预设文件 18 行格式不受影响（`paragraphs` 不参与旧文本序列化）。