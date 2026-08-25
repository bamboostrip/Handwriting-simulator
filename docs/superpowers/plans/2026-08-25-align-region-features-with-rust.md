# 框选区域功能对齐 Rust 版实施计划 (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Python 版框选文字区域（TextRegion）的排版、参数模型、高级覆盖面板、多段对齐与缩进、docx 导入以及单页截断渲染功能完全对齐 Rust 版实现。

**Architecture:**
1. 数据层：在 `models.py` 的 `TextRegion` 中增加 `align`, `indent_em`, `paragraphs`, `margin_*` 与逐区域排版/扰动/错字/颜色覆盖字段，并扩展校验与序列化方法。
2. 引擎层：在 `engine_fast.py` 中增强 `_region_params` 与 `_pages_with_regions`，支持区域多段排版、边距生效、参数覆盖、打印体规整化以及单页截断机制。
3. 交互层：重构 `region_dialog.py`，支持段落工具栏（左/中/右对齐、首行缩进切换、导入 docx）、光标行状态显示、富文本段落编辑器、以及折叠式排版与扰动覆盖面板。
4. 联动层：在 `main_window.py` 中更新区域增改逻辑与参数快照传递。

**Tech Stack:** Python 3.10+, PyQt6, Pillow, NumPy, python-docx, pytest.

## Global Constraints
- 所有新增字段需有合理的默认值（None / 0 / "left"），确保历史预设和调用向后兼容。
- 相同随机 seed 下预览与导出逐像素一致。
- 打印体区域在引擎中强制规整化（零扰动、零错字）。

---

### Task 1: 扩展 `TextRegion` 数据模型与校验

**Files:**
- Modify: `src/handwritesim/core/models.py`
- Test: `tests/test_regions.py`

**Interfaces:**
- `TextRegion` 增加字段：`align`, `indent_em`, `paragraphs: list[Paragraph] | None`, `margin_top/bottom/left/right`, `word_spacing`, `line_spacing`, `font_size_sigma`, `word_spacing_sigma`, `line_spacing_sigma`, `perturb_x_sigma`, `perturb_y_sigma`, `perturb_theta_sigma`, `miswrite_rate`, `miswrite_strikeout_style`, `color`
- `TextRegion.has_overrides(self) -> bool`
- `HandwritingParams.validate()` 验证 region 各项新参数合法性

- [ ] **Step 1: 编写测试用例**
在 `tests/test_regions.py` 中增加对 `TextRegion` 字段默认值、`has_overrides()`、`align` / `margin` / 覆盖参数校验的测试。

- [ ] **Step 2: 运行测试验证失败**
运行 `uv run pytest tests/test_regions.py -k test_region_model`，验证失败。

- [ ] **Step 3: 实现 `models.py` 字段扩展与校验**
更新 `src/handwritesim/core/models.py` 中的 `TextRegion` 与 `HandwritingParams.validate()` 及序列化。

- [ ] **Step 4: 运行测试验证通过**
运行 `uv run pytest tests/test_regions.py`，确保全部测试通过。

---

### Task 2: 核心排版引擎 `engine_fast.py` 对齐

**Files:**
- Modify: `src/handwritesim/core/engine_fast.py`
- Test: `tests/test_regions.py`

**Interfaces:**
- `_region_params(self, params: HandwritingParams, region: TextRegion) -> HandwritingParams`：应用内边距、字体字号、段落/对齐/缩进转换、全部覆盖项、打印体规整化。
- `_pages_with_regions(self, params: HandwritingParams) -> Iterator[Image.Image]`：区域单页截断排版与所属目标页合成。

- [ ] **Step 1: 编写引擎渲染测试**
在 `tests/test_regions.py` 中增加：
1. 区域多段落对齐（左/中/右）与缩进渲染测试
2. 区域内边距测试（margin 留白）
3. 区域覆盖项生效测试（独立颜色 fill、独立字距/行距/扰动）
4. 区域单页截断测试（超长文字在单页截断，不延伸到下一页）

- [ ] **Step 2: 运行测试验证失败**
运行 `uv run pytest tests/test_regions.py -k "margin or override or align or truncate"`。

- [ ] **Step 3: 更新 `engine_fast.py` 实现**
修改 `_region_params` 和 `_pages_with_regions`，实现多段落排版、边距、参数覆盖与单页截断渲染。

- [ ] **Step 4: 运行测试验证通过**
运行 `uv run pytest tests/test_regions.py` 验证通过。

---

### Task 3: 重构 `RegionDialog` 对话框与工具栏/覆盖面板

**Files:**
- Modify: `src/handwritesim/gui/region_dialog.py`
- Test: `tests/test_gui_consistency.py`

**Interfaces:**
- `RegionDialog` 构造函数接收 `TextRegion` 或完整属性（含 paragraphs、align、indent_em、margins、overrides）。
- 属性提供 `to_region_kwargs()` 或各独立 getter 返回最新编辑值。
- 工具栏按钮：左对齐、居中、右对齐、首行缩进、导入 docx。
- 折叠面板：排版参数、笔画扰动、写错字、文字颜色、边距。

- [ ] **Step 1: 编写对 `RegionDialog` 的功能测试**
在 `tests/test_gui_consistency.py` 中测试 `RegionDialog` 的数据读取、回填、段落对齐及 docx 导入交互。

- [ ] **Step 2: 运行测试验证失败**
运行 `uv run pytest tests/test_gui_consistency.py`。

- [ ] **Step 3: 重构 `region_dialog.py`**
实现基于富文本的段落编辑与状态提示、工具栏对齐/缩进/docx 导入、以及折叠式排版与扰动覆盖组件。

- [ ] **Step 4: 运行测试验证通过**
运行 `uv run pytest tests/test_gui_consistency.py`。

---

### Task 4: 主界面 `main_window.py` 联动与全量回归验证

**Files:**
- Modify: `src/handwritesim/gui/main_window.py`
- Test: `tests/` 全量测试

**Interfaces:**
- `_on_region_selected`：创建 `RegionDialog` 时传入完整上下文，保存生成包含所有新特性的 `TextRegion`。
- `_edit_region`：双击编辑时传入现有 `TextRegion` 的全部参数（paragraphs, align, indent_em, margins, overrides），确定后写回。
- 列表摘要与 Tooltip 显示优化。

- [ ] **Step 1: 更新 `main_window.py` 区域增改联动代码**
- [ ] **Step 2: 执行全量单元测试与回归测试**
运行 `uv run pytest`，确保全套 105+ 测试项全部 PASS。
