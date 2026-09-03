# 文档与正文导入高亮意图交互确认设计规范

## 1. 背景与目标

在手写模拟器中，DOCX 与 PDF 文档可能包含高亮颜色或背景底色标记。此前系统会自动将带有底色/高亮的区域识别为手写填空，并将底图中的对应区域抹白，正文中未高亮的部分自动转为打印体。
但在实际使用中存在两种常见场景：
1. **纯彩色底图需求**：用户希望使用带有底色、色块或表格样式的完整 PDF 底图，不希望颜色被擦除抹白，也不需要切分成手写填空区域。
2. **免改模板全篇手写需求**：用户直接导入现成的 Word 模板，模板本身含有高亮或单元格底色，但用户不想逐一修改 Word 格式，希望全文所有内容（无论有无高亮）一律作为手写文本排版。

为了兼顾“自动智能识别”与“用户原样保留”，系统在检测到文档包含高亮/底色时，按需弹出选择对话框，交由用户决定处理策略。

---

## 2. 交互与功能设计

### 2.1 底图导入（PDF / Word 背景导入）
- **触发入口**：主界面“导入文档底图”按钮（`_import_document`）。
- **渲染处理**：
  - 调用渲染流程时，在对高亮框与标签进行擦除前，保留原始未擦除的页面图像文件（例如命名为 `{prefix}_{idx}_raw.png`），擦除后的保存为 `{prefix}_{idx}.png`。
  - 若检测到有效区域（`len(regions) > 0`）：
    - 弹出模态选择对话框：
      - **标题**：`检测到手写填空标记`
      - **信息文本**：`检测到文档包含 X 处高亮标记或填空区域。\n请选择底图处理方式：`
      - **按钮 1（推荐）**：`提取填空框`（擦除高亮底色用于打印，自动生成文字区域与笔迹角色）
      - **按钮 2**：`保留完整底图`（保留原图色彩与高亮，不擦除底色，不生成填空区域）
      - **按钮 3**：`取消`（中止导入）
    - 用户选择“提取填空框”：采用擦除后的图片和 `regions` 列表，同步角色。
    - 用户选择“保留完整底图”：切换使用未擦除的原始图片，`regions` 置空，不生成填空框。
  - 若未检测到高亮（`len(regions) == 0`）：
    - 直接使用原始底图，不弹任何多余对话框，无缝导入。

### 2.2 正文导入（Word 导入到正文编辑器）
- **触发入口**：主界面“导入 docx”按钮（`_import_docx`）。
- **解析处理**：
  - `load_paragraphs_with_runs` 增加参数 `ignore_highlights: bool = False`。
  - 首先检测文档中是否包含高亮/底色（`has_docx_highlights(path)`）：
    - 若 `has_docx_highlights` 为 False：直接按默认全手写排版导入，不弹对话框。
    - 若 `has_docx_highlights` 为 True：
      - 弹出模态选择对话框：
        - **标题**：`检测到文字高亮标记`
        - **信息文本**：`文档中部分文字带有高亮/背景色。\n请选择排版方式：`
        - **按钮 1（推荐）**：`全部作为手写`（忽略高亮/背景色，整篇文档全部使用手写字体）
        - **按钮 2**：`打印/手写混排`（高亮文字为手写，未高亮文字保留为打印体）
        - **按钮 3**：`取消`（中止导入）
      - 若选择“全部作为手写”：传入 `ignore_highlights=True`，所有段落的所有 runs 统一赋予默认手写角色（`role_id=0`），不创建多余角色。
      - 若选择“打印/手写混排”：保持原有的智能分流逻辑（未高亮为打印体 role 1，高亮为手写角色 role 2+）。

---

## 3. 模块变更与技术实现

### 3.1 核心解析层 (`src/handwritesim/core/docx_io.py`)
- 新增 `has_docx_highlights(path: str | Path) -> bool`：快速检查 docx 中是否存在任何高亮/背景色 run。
- 改造 `load_paragraphs_with_runs(path, font_size, ignore_highlights: bool = False)`：
  - 当 `ignore_highlights=True` 时：强制 `has_any_highlight = False`，所有 run 均不提取 highlight 属性，`role = 0`，不进行多角色与打印角色分配。

### 3.2 渲染层 (`src/handwritesim/core/doc_render.py`)
- `pdf_to_images_with_regions(..., keep_raw: bool = True)` / `document_to_page_images_with_regions`:
  - 渲染页面时，在 `erase_highlight_boxes` 前先将原图保存为 `_raw_{index}.png`。
  - 返回值提供 `raw_paths`：`(paths, regions, raw_paths)`。

### 3.3 GUI 层 (`src/handwritesim/gui/main_window.py`)
- 改造 `_import_document`：在 `len(regions) > 0` 时弹出三按钮提示框（提取填空 / 保留底图 / 取消）。
- 改造 `_import_docx`：在文档含有高亮时弹出三按钮提示框（全部手写 / 打印混排 / 取消）。

---

## 4. 测试与验证策略
1. 单元测试：`tests/test_doc_background.py` & `tests/test_docx_io.py`
   - 测试 `ignore_highlights=True` 时生成的段落 runs 全部为手写 role_id 0。
   - 测试 `doc_render` 原图保留与路径切换。
2. 集成测试：
   - 模拟带高亮的 docx 分别选择“全部手写”与“混排”后的角色列表与段落。
   - 模拟带高亮的 PDF/DOCX 底图分别选择“提取填空”与“保留底图”后的页面与区域列表。
