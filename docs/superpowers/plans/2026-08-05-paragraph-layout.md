# 段落化排版（标题居中/首段缩进）实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为手写模拟器增加段落化排版能力：标题居中、正文首行缩进；支持 GUI 富文本编辑、docx 导入、CLI `--docx`。

**架构：** 新增 `Paragraph` 数据模型与 `paragraphs` 参数；新增 `_layout_paragraph` 段落独立渲染函数（居中/缩进），逐页流式拼接；新增 `docx_io` 解析；GUI 富文本工具按钮 + docx 导入；CLI 加 `--docx`。

**技术栈：** Python 3.14 + numpy/scipy + PIL + PyQt6 + python-docx（新增）

设计文档：`docs/superpowers/specs/2026-08-05-paragraph-layout-design.md`
运行命令：`uv run pytest`；启动 GUI：`uv run main.py`

---

## 文件结构

- 修改 `src/handwritesim/core/models.py` — 新增 `Paragraph` 与 `paragraphs` 字段
- 修改 `src/handwritesim/core/engine_fast.py` — 段落渲染与分页
- 创建 `src/handwritesim/core/docx_io.py` — docx 解析
- 修改 `src/handwritesim/cli.py` — `--docx` 参数
- 修改 `src/handwritesim/gui/ui.py` — 富文本工具按钮
- 修改 `src/handwritesim/gui/main_window.py` — 富文本交互 + docx 导入
- 修改 `pyproject.toml` — 新增 `python-docx`（uv add）
- 创建 `tests/test_docx_io.py`、修改 `tests/test_engine_fast.py`、`tests/test_engine.py`

---

## 任务 1：数据模型（Paragraph + paragraphs 字段）

**文件：** 修改 `src/handwritesim/core/models.py`；测试 `tests/test_engine.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_engine.py` 末尾追加：

```python
from handwritesim.core.models import Paragraph


def test_paragraphs_roundtrip_dict():
    p = Paragraph("标题", align="center", first_line_indent=60)
    params = HandwritingParams(paragraphs=[p])
    restored = HandwritingParams.from_dict(params.to_dict())
    assert restored.paragraphs == [p]


def test_paragraphs_default_none():
    assert HandwritingParams().paragraphs is None
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_engine.py::test_paragraphs_roundtrip_dict -v`
预期：FAIL（`Paragraph` 未定义）

- [ ] **步骤 3：实现数据模型**

在 `models.py` 顶部 `from dataclasses import dataclass, asdict, fields` 保持不变，新增 `Paragraph` 类（放在 `HandwritingParams` 之前）：

```python
@dataclass
class Paragraph:
    """单个段落的排版信息。"""

    text: str = ""
    align: str = "left"          # "left" | "center"
    first_line_indent: int = 0   # 首行缩进（像素）
```

在 `HandwritingParams` 的 `text` 字段之后新增：

```python
    paragraphs: list[Paragraph] | None = None  # 非空时启用段落渲染
```

重写 `from_dict`（当前是 `return cls(**{k: v ...})`），使其能把 `paragraphs` 的 dict 列表还原为 `Paragraph`：

```python
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
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_engine.py -v`
预期：PASS（含新增两项）

- [ ] **步骤 5：Commit**

```bash
git add src/handwritesim/core/models.py tests/test_engine.py
git commit -m "feat: 新增 Paragraph 数据模型与 paragraphs 参数"
```

---

## 任务 2：段落渲染引擎（_layout_paragraph + 分页）

**文件：** 修改 `src/handwritesim/core/engine_fast.py`；测试 `tests/test_engine_fast.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_engine_fast.py` 末尾追加：

```python
from handwritesim.core.models import Paragraph


def _para_params(tmp_path, paragraphs):
    params = _params(tmp_path, "占位")
    params.paragraphs = paragraphs
    return params


def test_paragraph_center_and_indent(tmp_path):
    params = _para_params(tmp_path, [
        Paragraph("标题", align="center"),
        Paragraph("正文第一段", first_line_indent=60),
    ])
    image = HandwritingEngine(backend="fast").render_preview(params)
    assert image.size == (400, 300)
    gray = np.asarray(image.convert("L"))
    assert gray.min() < 128  # 有前景


def test_paragraph_multi_page(tmp_path):
    params = _para_params(tmp_path, [
        Paragraph("标题", align="center"),
        Paragraph("很长的一段正文。" * 80),
    ])
    pages = list(HandwritingEngine(backend="fast").generate(params))
    assert len(pages) >= 2
    for page in pages:
        assert page.size == (400, 300)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_engine_fast.py::test_paragraph_center_and_indent -v`
预期：FAIL（段落路径未实现，输出无前景或抛错）

- [ ] **步骤 3：实现段落渲染**

在 `engine_fast.py` 顶部导入 `Paragraph`：

```python
from .models import HandwritingParams, Paragraph
```

在 `_layout_page` 之后新增两条辅助：

```python
def _split_text_rows(rows: np.ndarray) -> list[tuple[int, int]]:
    """把行聚合的 bool 数组按连续段分组，返回 [start, end) 列表。"""
    groups: list[tuple[int, int]] = []
    start: int | None = None
    for idx, v in enumerate(rows):
        if v and start is None:
            start = idx
        elif not v and start is not None:
            groups.append((start, idx))
            start = None
    if start is not None:
        groups.append((start, len(rows)))
    return groups


def _center_text_lines(mask: np.ndarray) -> np.ndarray:
    """按文本行测量非零 x 范围，逐行水平居中。"""
    height, width = mask.shape
    rows = np.any(mask, axis=1)
    if not rows.any():
        return mask
    result = np.zeros_like(mask)
    for y0, y1 in _split_text_rows(rows):
        band = mask[y0:y1]
        ys, xs = np.nonzero(band)
        line_w = int(xs.max()) - int(xs.min()) + 1
        if line_w >= width:
            nx_orig = xs
            nys = ys
        else:
            shift = (width - line_w) // 2 - int(xs.min())
            nx_orig = xs + shift
            nys = ys
        valid = (nx_orig >= 0) & (nx_orig < width)
        result[y0 + nys[valid], nx_orig[valid]] = True
    return result
```

新增段落渲染函数（放在 `_center_text_lines` 之后）：

```python
def _layout_paragraph(
    params: HandwritingParams,
    rand: random.Random,
    paragraph: Paragraph,
    width: int,
    height: int,
) -> tuple[np.ndarray, int]:
    """渲染单个段落，返回裁剪后的内容 mask（bool）与占用高度。"""
    page = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(page)
    base_font = ImageFont.truetype(params.font_path, size=int(params.font_size))
    font_cache: dict[int, ImageFont.FreeTypeFont] = {}
    font_size = params.font_size
    line_spacing = float(params.line_spacing) + float(params.font_size)
    end_chars = params.end_chars
    start_chars = params.start_chars
    left = params.left_margin
    right = params.right_margin
    text = paragraph.text
    text_len = len(text)

    def resolve_font(size: int) -> ImageFont.FreeTypeFont:
        if size not in font_cache:
            font_cache[size] = (
                base_font if size == font_size else base_font.font_variant(size=size)
            )
        return font_cache[size]

    i = 0
    y = line_spacing - font_size
    while True:
        if i >= text_len:
            break
        x = left + (paragraph.first_line_indent if i == 0 else 0)
        while i < text_len:
            ch = text[i]
            if ch == "\n":
                i += 1
                break
            if x > width - right - 2 * font_size and ch in start_chars:
                break
            if x > width - right - font_size and ch not in end_chars:
                break
            xy = (round(x), round(rand.gauss(y, params.line_spacing_sigma)))
            font = base_font
            if params.font_size_sigma:
                size = max(round(rand.gauss(font_size, params.font_size_sigma)), 0)
                if size != font_size:
                    font = resolve_font(size)
            draw.text(xy, ch, fill=1, font=font)
            offset = font.getbbox(ch)[2] - font.getbbox(ch)[0]
            x += rand.gauss(params.word_spacing + offset, params.word_spacing_sigma)
            i += 1
        else:
            # 整行自然结束（未换行且文本耗尽）
            if i >= text_len:
                break
        y += line_spacing

    mask = np.asarray(page, dtype=bool)
    if paragraph.align == "center":
        mask = _center_text_lines(mask)
    rows = np.any(mask, axis=1)
    if not rows.any():
        return np.zeros((0, width), dtype=bool), 0
    first = int(np.argmax(rows))
    last = int(len(rows) - 1 - np.argmax(rows[::-1]))
    return mask[first : last + 1], last - first + 1
```

在 `FastEngine` 类内新增（放在 `generate_pages` 之前）：

```python
    def _paragraph_pages(self, params: HandwritingParams):
        """按段落逐页渲染（段落为跨页最小单位）。"""
        background = np.asarray(Image.open(params.background_path).convert("RGB"))
        rand = self._new_rand()
        height, width = background.shape[:2]
        line_spacing = float(params.line_spacing) + float(params.font_size)
        top, bottom = params.top_margin, params.bottom_margin

        para_masks: list[tuple[np.ndarray, int]] = []
        for para in params.paragraphs or []:
            if not para.text:
                continue
            content, ph = _layout_paragraph(params, rand, para, width, height)
            para_masks.append((content, ph))

        page_canvas = np.zeros((height, width), dtype=bool)
        used = top
        for content, ph in para_masks:
            if ph <= 0:
                continue
            if used + ph > height - bottom:
                yield self._finalize(params, page_canvas, background)
                page_canvas = np.zeros((height, width), dtype=bool)
                used = top
            ys, xs = np.nonzero(content)
            page_canvas[used + ys, xs] = True
            used += ph + line_spacing
        if page_canvas.any():
            yield self._finalize(params, page_canvas, background)

    # _finalize 使用 self._rng，与 generate_pages 共用同一随机源
    def _finalize(self, params, mask, background):
        return Image.fromarray(_perturb_mask(mask, params, self._rng, background), mode="RGB")
```

修改 `render_preview` 增加段落分支：

```python
    def render_preview(self, params: HandwritingParams) -> Image.Image:
        params.validate()
        if params.paragraphs:
            return next(self._paragraph_pages(params))
        rand = self._new_rand()
        mask, _ = _layout_page(params, rand, params.text, 0)
        background = np.asarray(Image.open(params.background_path).convert("RGB"))
        canvas = _perturb_mask(mask, params, self._rng, background)
        return Image.fromarray(canvas, mode="RGB")
```

修改 `generate_pages` 增加段落分支：

```python
    def generate_pages(self, params: HandwritingParams) -> Iterator[Image.Image]:
        params.validate()
        if params.paragraphs:
            yield from self._paragraph_pages(params)
            return
        background = np.asarray(Image.open(params.background_path).convert("RGB"))
        rand = self._new_rand()
        start = 0
        while True:
            mask, start = _layout_page(params, rand, params.text, start)
            canvas = _perturb_mask(mask, params, self._rng, background)
            yield Image.fromarray(canvas, mode="RGB")
            if start >= len(params.text):
                break
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_engine_fast.py -v`
预期：PASS（含新增两项，原有回归通过）

- [ ] **步骤 5：Commit**

```bash
git add src/handwritesim/core/engine_fast.py tests/test_engine_fast.py
git commit -m "feat: 段落化渲染引擎（居中/首行缩进，段落分页）"
```

---

## 任务 3：docx 解析（docx_io.py + 依赖）

**文件：** 创建 `src/handwritesim/core/docx_io.py`；创建 `tests/test_docx_io.py`；修改 `pyproject.toml`

- [ ] **步骤 1：添加依赖**

运行：`uv add python-docx`
预期：`pyproject.toml` 新增 `python-docx`，`uv.lock` 更新

- [ ] **步骤 2：编写失败的测试**

创建 `tests/test_docx_io.py`：

```python
"""docx 解析测试。"""

from __future__ import annotations

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

from handwritesim.core.docx_io import load_paragraphs
from handwritesim.core.models import Paragraph


def _make_docx(path) -> None:
    doc = Document()
    hp = doc.add_paragraph("会议通知")
    hp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    body = doc.add_paragraph()
    body.paragraph_format.first_line_indent = Pt(24)  # 2 字符 @12pt
    body.add_run("现将有关事项通知如下。")
    doc.save(path)


def test_load_paragraphs(tmp_path):
    docx_path = tmp_path / "test.docx"
    _make_docx(docx_path)
    paras = load_paragraphs(docx_path)
    assert paras[0].align == "center"
    assert paras[0].first_line_indent == 0
    assert paras[1].align == "left"
    assert paras[1].first_line_indent > 0
    assert isinstance(paras[1], Paragraph)
```

- [ ] **步骤 3：运行测试验证失败**

运行：`uv run pytest tests/test_docx_io.py -v`
预期：FAIL（`handwritesim.core.docx_io` 不存在）

- [ ] **步骤 4：实现 docx_io**

创建 `src/handwritesim/core/docx_io.py`：

```python
"""docx 文档解析：提取段落对齐与首行缩进。"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

from .models import Paragraph

# docx 使用 EMU（1 英寸 = 914400 EMU），按 96dpi 换算像素
_EMU_PER_INCH = 914400
_DPI = 96


def _emu_to_px(emu: float | None) -> int:
    if not emu:
        return 0
    return int(round(emu / _EMU_PER_INCH * _DPI))


def load_paragraphs(path: str | Path) -> list[Paragraph]:
    """读取 docx 中每个段落，返回 [Paragraph]（忽略空段落）。"""
    doc = Document(str(path))
    result: list[Paragraph] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        align = "center" if para.alignment == WD_ALIGN_PARAGRAPH.CENTER else "left"
        indent = _emu_to_px(para.paragraph_format.first_line_indent)
        result.append(Paragraph(text=text, align=align, first_line_indent=indent))
    return result
```

- [ ] **步骤 5：运行测试验证通过**

运行：`uv run pytest tests/test_docx_io.py -v`
预期：PASS

- [ ] **步骤 6：Commit**

```bash
git add src/handwritesim/core/docx_io.py tests/test_docx_io.py pyproject.toml uv.lock
git commit -m "feat: 新增 docx 解析（对齐与首行缩进）"
```

---

## 任务 4：CLI --docx 参数

**文件：** 修改 `src/handwritesim/cli.py`

- [ ] **步骤 1：实现 --docx**

在 `cli.py` 导入处新增：

```python
from .core import presets
from .core.docx_io import load_paragraphs
```

在 `_build_parser()` 的文本参数附近新增：

```python
    parser.add_argument("--docx", default="", help="导入 docx 文件（解析对齐与首行缩进）")
```

在 `main()` 中，`explicit` 赋值之后、`if not args.text` 检查之前新增：

```python
    # 段落化：优先 docx，其次纯文本
    if args.docx:
        params.paragraphs = load_paragraphs(args.docx)
    elif args.text:
        params.paragraphs = None
```

- [ ] **步骤 2：手动验证**

运行：
```powershell
uv run python -m handwritesim.cli --font C:/Windows/Fonts/msyh.ttc --docx "不存在.docx" --preview-only
```
预期：因文件不存在抛出异常（确认 docx 路径被使用）。再构造一个 docx 后验证不报错。

- [ ] **步骤 3：Commit**

```bash
git add src/handwritesim/cli.py
git commit -m "feat: CLI 支持 --docx 导入段落化排版"
```

---

## 任务 5：GUI 富文本编辑器

**文件：** 修改 `src/handwritesim/gui/ui.py`、`src/handwritesim/gui/main_window.py`

- [ ] **步骤 1：在 ui.py 文本输入框上方加工具按钮行**

替换 `ui.py` 中这段（`self.label_text` 与 `self.textEdit` 之间）：

```python
        # 待处理文本
        self.label_text = QtWidgets.QLabel(panel)
        v.addWidget(self.label_text)
        self.textEdit = QtWidgets.QTextEdit(panel)
```

为：

```python
        # 待处理文本
        self.label_text = QtWidgets.QLabel(panel)
        v.addWidget(self.label_text)

        # 排版工具按钮
        row_tools = QtWidgets.QHBoxLayout()
        self.btn_align_left = QtWidgets.QPushButton(panel)
        self.btn_align_left.setObjectName("btn_align_left")
        self.btn_center = QtWidgets.QPushButton(panel)
        self.btn_center.setObjectName("btn_center")
        self.btn_indent = QtWidgets.QPushButton(panel)
        self.btn_indent.setObjectName("btn_indent")
        self.btn_import_docx = QtWidgets.QPushButton(panel)
        self.btn_import_docx.setObjectName("btn_import_docx")
        row_tools.addWidget(self.btn_align_left)
        row_tools.addWidget(self.btn_center)
        row_tools.addWidget(self.btn_indent)
        row_tools.addWidget(self.btn_import_docx)
        row_tools.addStretch(1)
        v.addLayout(row_tools)

        self.textEdit = QtWidgets.QTextEdit(panel)
```

在 `retranslateUi` 中 `self.label_text.setText(...)` 之后新增：

```python
        self.btn_align_left.setText("左对齐")
        self.btn_center.setText("居中")
        self.btn_indent.setText("首行缩进")
        self.btn_import_docx.setText("导入 docx")
```

- [ ] **步骤 2：在 main_window.py 接线**

在 `_connect_signals` 中 `ui.textEdit.textChanged.connect(...)` 之前新增：

```python
        ui.btn_align_left.clicked.connect(lambda: self._set_block_align(0))
        ui.btn_center.clicked.connect(lambda: self._set_block_align(1))
        ui.btn_indent.clicked.connect(self._indent_current_block)
        ui.btn_import_docx.clicked.connect(self._import_docx)
```

在 `_connect_signals` 之后新增工具方法：

```python
    def _set_block_align(self, flag: int) -> None:
        from PyQt6.QtGui import QTextBlockFormat
        from PyQt6.QtCore import Qt
        cursor = self._ui.textEdit.textCursor()
        fmt = QTextBlockFormat()
        fmt.setAlignment(
            Qt.AlignmentFlag.AlignCenter if flag else Qt.AlignmentFlag.AlignLeft
        )
        cursor.mergeBlockFormat(fmt)

    def _indent_current_block(self) -> None:
        from PyQt6.QtGui import QTextBlockFormat
        cursor = self._ui.textEdit.textCursor()
        fmt = QTextBlockFormat()
        fmt.setTextIndent(2 * self._int_of(self._ui.lineEdit_9, 36))
        cursor.mergeBlockFormat(fmt)
```

在 `collect_params` 中，把 `p.text = ui.textEdit.toPlainText()` 改为：

```python
        p.text = ui.textEdit.toPlainText()
        p.paragraphs = self._collect_paragraphs()
```

新增方法（放在 `collect_params` 之后）：

```python
    def _collect_paragraphs(self):
        """从富文本编辑器的块格式收集段落。"""
        from PyQt6.QtCore import Qt
        from ..core.models import Paragraph
        doc = self._ui.textEdit.document()
        paras: list[Paragraph] = []
        for i in range(doc.blockCount()):
            block = doc.findBlockByNumber(i)
            text = block.text().strip()
            if not text:
                continue
            fmt = block.blockFormat()
            align = "center" if fmt.alignment() & Qt.AlignmentFlag.AlignCenter else "left"
            paras.append(
                Paragraph(text=text, align=align, first_line_indent=int(fmt.textIndent()))
            )
        return paras
```

新增 docx 导入方法：

```python
    def _import_docx(self) -> None:
        from PyQt6.QtGui import QTextBlockFormat, QTextCursor
        from PyQt6.QtCore import Qt
        from ..core.docx_io import load_paragraphs
        path, _ = QFileDialog.getOpenFileName(self, "导入 docx", "", "Word 文档 (*.docx)")
        if not path:
            return
        try:
            paras = load_paragraphs(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        editor = self._ui.textEdit
        editor.clear()
        cursor = QTextCursor(editor.document())
        for idx, para in enumerate(paras):
            if idx:
                cursor.insertBlock()
            fmt = QTextBlockFormat()
            if para.align == "center":
                fmt.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if para.first_line_indent:
                fmt.setTextIndent(para.first_line_indent)
            cursor.setBlockFormat(fmt)
            cursor.insertText(para.text)
        editor.setTextCursor(cursor)
```

- [ ] **步骤 3：启动 GUI 手动验证**

运行：`uv run main.py`
预期：文本区上方出现四个按钮；选中段落可居中对齐、首行缩进；导入 docx 后富文本回填；预览/导出正常。

- [ ] **步骤 4：Commit**

```bash
git add src/handwritesim/gui/ui.py src/handwritesim/gui/main_window.py
git commit -m "feat: GUI 富文本排版工具与 docx 导入"
```

---

## 任务 6：集成验证与回归

- [ ] **步骤 1：运行全部测试**

运行：`uv run pytest -v`
预期：全部 PASS（含新增 docx、段落、模型测试）

- [ ] **步骤 2：GUI 端到端冒烟**

运行：`uv run main.py`，导入含标题（居中）与正文（首行缩进）的 docx，点击预览与导出，确认居中与缩进效果正确。

- [ ] **步骤 3：Commit**

```bash
git add -A
git commit -m "test: 段落化排版集成验证"
```