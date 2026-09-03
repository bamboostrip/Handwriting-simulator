# 导入文档/Docx高亮意图确认弹窗实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当导入底图（PDF/DOCX）或正文（DOCX）检测到高亮或背景色标记时，弹出对话框让用户自主选择“提取填空/混排”或“保留完整底图/全部手写”，避免用户不愿改模板或想保留彩色底图时被强制切分。

**Architecture:**
- `docx_io`: 增加 `has_docx_highlights` 轻量检测函数，并在 `load_paragraphs_with_runs` 中增加 `ignore_highlights` 参数。
- `doc_render`: 在栅格化渲染时保留擦除前的原始无损底图（`{prefix}_{index}_raw.png`）。
- `main_window`: 在 `_import_document` 和 `_import_docx` 中，检测到高亮/区域时弹出 3 按钮选择对话框，无高亮时不弹窗直接导入。

**Tech Stack:** Python 3.10+, PyQt6, python-docx, pypdfium2, Pillow, pytest

## Global Constraints
- 保证原有测试用例与接口向后兼容，不改变默认无需弹窗流程。
- 底图导入选择“保留完整底图”时直接使用已渲染的原图，禁止重复调用外部进程渲染。

---

### Task 1: 核心 docx_io 支持高亮检测与忽略高亮参数

**Files:**
- Modify: `src/handwritesim/core/docx_io.py`
- Test: `tests/test_docx_io.py`

**Interfaces:**
- Consumes: python-docx `Document`
- Produces:
  - `has_docx_highlights(path: str | Path) -> bool`
  - `load_paragraphs_with_runs(path: str | Path, font_size: int | None = None, ignore_highlights: bool = False) -> list[Paragraph]`

- [ ] **Step 1: Write the failing test**
在 `tests/test_docx_io.py` 中添加测试用例：
```python
def test_has_docx_highlights_and_ignore():
    from handwritesim.core.docx_io import has_docx_highlights, load_paragraphs_with_runs
    from docx import Document
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        doc_path = Path(tmpdir) / "test_hl.docx"
        doc = Document()
        p = doc.add_paragraph()
        r1 = p.add_run("Normal text")
        r2 = p.add_run("Highlighted text")
        from docx.enum.text import WD_COLOR_INDEX
        r2.font.highlight_color = WD_COLOR_INDEX.YELLOW
        doc.save(str(doc_path))

        assert has_docx_highlights(doc_path) is True

        # 测试正常混排模式（默认）
        paras_mixed = load_paragraphs_with_runs(doc_path, 36, ignore_highlights=False)
        roles_mixed = {r.role_id for p in paras_mixed for r in p.runs}
        assert 1 in roles_mixed  # 打印体
        assert any(rid >= 2 for rid in roles_mixed)  # 高亮角色

        # 测试忽略高亮模式（全部手写）
        paras_hand = load_paragraphs_with_runs(doc_path, 36, ignore_highlights=True)
        roles_hand = {r.role_id for p in paras_hand for r in p.runs}
        assert roles_hand == {0}  # 全部为默认手写
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_docx_io.py -k test_has_docx_highlights_and_ignore -v`
Expected: FAIL

- [ ] **Step 3: Implement in `src/handwritesim/core/docx_io.py`**
增加 `has_docx_highlights`，并在 `load_paragraphs_with_runs` 添加 `ignore_highlights=False` 参数处理：
```python
def has_docx_highlights(path: str | Path) -> bool:
    """快速检查 docx 是否包含任何高亮或背景色标记。"""
    try:
        doc = Document(str(path))
        for para in doc.paragraphs:
            for run in para.runs:
                if run.text and _run_highlight(run) is not None:
                    return True
    except Exception:
        pass
    return False
```
在 `load_paragraphs_with_runs` 开头与 run 循环中增加对 `ignore_highlights` 的判断：
若 `ignore_highlights` 为 True，将 `has_any_highlight` 置为 False，且循环内 `hl = None`。

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_docx_io.py -k test_has_docx_highlights_and_ignore -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/handwritesim/core/docx_io.py tests/test_docx_io.py
git commit -m "feat(docx_io): add has_docx_highlights and ignore_highlights support"
```

---

### Task 2: 核心 doc_render 保留未擦除原始底图

**Files:**
- Modify: `src/handwritesim/core/doc_render.py`
- Test: `tests/test_pdf_region_detection.py`

**Interfaces:**
- Consumes: pypdfium2, PIL
- Produces: 在 `out_dir` 同步生成 `{prefix}_{index}_raw.png`

- [ ] **Step 1: Write the failing test**
在 `tests/test_pdf_region_detection.py` 中添加测试用例，验证渲染同时生成 `_raw.png` 且当存在高亮时 `_raw.png` 保留高亮色彩而主图已抹白：
```python
def test_doc_render_preserves_raw_page_images(tmp_path):
    from handwritesim.core.doc_render import pdf_to_images_with_regions
    # 创建带高亮 PDF 页面进行测试
    ...
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_pdf_region_detection.py -k test_doc_render_preserves_raw_page_images -v`
Expected: FAIL

- [ ] **Step 3: Implement in `src/handwritesim/core/doc_render.py`**
在 `pdf_to_images_with_regions` 内部循环中：
```python
raw_path = out_dir / f"{prefix}_{index}_raw.png"
image.save(raw_path)
```
确保原始图片在 `erase_highlight_boxes` 前被落盘保存。

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_pdf_region_detection.py -k test_doc_render_preserves_raw_page_images -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/handwritesim/core/doc_render.py tests/test_pdf_region_detection.py
git commit -m "feat(doc_render): save raw un-erased page images alongside detected ones"
```

---

### Task 3: GUI 增加底图与正文导入确认弹窗

**Files:**
- Modify: `src/handwritesim/gui/main_window.py`
- Test: `tests/test_gui_consistency.py`

**Interfaces:**
- Consumes: `has_docx_highlights`, `load_paragraphs_with_runs`, `document_to_page_images_with_regions`
- Produces: 交互式 QMessageBox 选项分支

- [ ] **Step 1: Write the failing test**
在 `tests/test_gui_consistency.py` 中模拟用户选择“全部手写”与“保留完整底图”的分支行为。

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_gui_consistency.py -k test_import_dialog_options -v`
Expected: FAIL

- [ ] **Step 3: Implement in `src/handwritesim/gui/main_window.py`**
1. 改造 `_import_docx`：
   若 `has_docx_highlights(path)` 为 True，弹出选择对话框提供“全部手写（推荐）”、“打印/手写混排”与“取消”。
2. 改造 `_import_document`：
   若 `len(regions) > 0`，弹出选择对话框提供“提取填空框（推荐）”、“保留完整底图”与“取消”。若选“保留完整底图”，将底图切换为 `_raw.png`，清空 `regions`。

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_gui_consistency.py -k test_import_dialog_options -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/handwritesim/gui/main_window.py tests/test_gui_consistency.py
git commit -m "feat(gui): add interactive confirmation dialogs for highlighted doc/docx imports"
```

---

### Task 4: 全量回归测试

**Files:**
- All test files

- [ ] **Step 1: Run full test suite**
Run: `pytest -v`
Expected: All tests pass.

- [ ] **Step 2: Commit any cleanups**
```bash
git status
```
