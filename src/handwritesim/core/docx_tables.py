"""docx 表格视图：从 body 元素流构造供匹配与区域生成使用的表格结构。

移植自 Rust 0.5.0 ``crates/core/src/core/docx_tables.rs``（逐条对齐其常量与
规则，注释保留原因便于后续维护）。每个单元格得到：清理后的可见文本
（``{{...}}`` / ``【...】`` 标签已剥离）、列起始/跨列、对齐、字号回退、
印刷体判定与高亮色——``w:highlight`` 优先；无高亮时，具饱和度的彩色
``w:color`` 字色回退映射为标准色名（黑/灰/白是正文配色，不映射）。
嵌套表格与文本框内容已在 docx_io 解析阶段丢弃（外层格文本不含内层内容）。

跨页定位的核心是“整篇 body 元素流 × 单页字符流”的两指针子序列匹配，
配以跨页流游标（``start_element``）与消费判定，保证拆分表两页各输出自己
的部分、不重复也不丢失。
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field
from typing import Sequence

from .doc_render import ExtractedChar, classify_highlight_color, strip_tag_syntax
from .docx_io import (
    BodyElement,
    BodyParagraph,
    CellRun,
    CellVMerge,
    ParsedCell,
    ParsedTable,
    is_gray_highlight,
)

# 正文段落匹配的向前搜索窗口（字符数）。
PARAGRAPH_WINDOW = 500

# 单元格匹配的向前搜索窗口（字符数）。
CELL_WINDOW = 3000

# 单元格判定定位成功的最低命中比例。
CELL_MATCH_RATIO = 0.6

# 表格在一页上判定“已开始输出区域”的最低定位格数
#（防止正文误匹配产生幻影区域）。
TABLE_LOCATED_MIN_CELLS = 2

# 表格判定“整表完成”（后续页不再重试）的定位比例。消费为严格大于：
# 恰好一半（如 4 格表 2+2 均分）仍视为分割中，后半必须留给下一页补走
#（在 50% 处消费会让后半永不出现在任何后续页的起步元素流中）。
TABLE_CONSUMED_RATIO = 0.5

# 段落判定匹配成功（消费字符）的最低命中比例；失败时游标回退不再推进。
PARAGRAPH_MATCH_RATIO = 0.6

# 字色回退映射的最低通道差（max−min，与 doc_render::is_highlight_pixel 的
# 灰度差阈值同口径）：黑白灰字（7F7F7F/A6A6A6/D9D9D9/FFFFFF 等）通道差
# 为 0，是正文配色而非标记，不得落入 classify_highlight_color 的兜底色。
COLOR_FILL_MIN_SPREAD = 30


# ---------------------------------------------------------------------------
# 视图
# ---------------------------------------------------------------------------


@dataclass
class TableCellView:
    """单元格视图（匹配与区域生成的输入）。"""

    text: str
    """清理标签后的可见文本（多段落以 "\\n" 分隔；可为空）。"""

    col_start: int
    col_span: int
    v_merge: CellVMerge
    align: int
    """0 左 / 1 中 / 2 右（取首段 w:jc）。"""

    sz_half_pt: int | None
    """首段首个 run 字号（半磅；字号回退用）。"""

    printed: bool
    """原格含 {{打印 / 【打印 标签 → 生成印刷体区域（擦除原字重排）。"""

    skip_printed: bool
    """灰高亮 → 整格保留印刷底图（不生成区域、不擦除）。"""

    highlight: str | None
    """高亮色名（角色映射用）：首个非灰 w:highlight run；无 w:highlight 时
    回退取首个具饱和度（通道差 ≥ COLOR_FILL_MIN_SPREAD）的彩色 w:color run
    经 classify_highlight_color 映射的标准色名；黑/灰/白字不映射。"""


@dataclass
class TableRowView:
    cells: list[TableCellView] = field(default_factory=list)


@dataclass
class TableView:
    """一张表的视图（index 为文档内 1 基序号）。"""

    index: int
    rows: list[TableRowView] = field(default_factory=list)


def _cell_text(cell: ParsedCell) -> str:
    """单元格原始文本（各 run 顺序拼接，含多段落 "\\n"）。"""
    return "".join(run.text for run in cell.runs)


def _cell_printed_marker(raw_text: str) -> bool:
    """是否含印刷体标签（对齐 Rust：{{打印 与【打印 两种写法）。"""
    return "{{打印" in raw_text or "【打印" in raw_text


def _cell_highlight(cell: ParsedCell) -> str | None:
    """单元格高亮色名：w:highlight 优先，其次具饱和度的 w:color 回退。"""
    for run in cell.runs:
        if run.highlight is not None and not is_gray_highlight(run.highlight):
            return run.highlight
    # 无 w:highlight 时回退取首个具饱和度的彩色 w:color run（用户用字体颜色
    # 而非高亮标记单元格），映射为标准色名以复用高亮角色配色管线。
    # 黑色是正文默认色、灰/白是正文配色，通道差 < COLOR_FILL_MIN_SPREAD
    # 一律不构成标记（classify 兜底分支会返回 "yellow"，必须拦在门外）。
    # 灰色判定（skip_printed）仍只看 w:highlight，此处不改变印刷体语义。
    for run in cell.runs:
        rgb = run.color
        if rgb is None:
            continue
        r, g, b = rgb
        if max(r, g, b) - min(r, g, b) >= COLOR_FILL_MIN_SPREAD:
            return classify_highlight_color(r, g, b)
    return None


def _build_cell_view(cell: ParsedCell) -> TableCellView:
    """把解析期单元格转成视图（标签清理、印刷体/灰格判定、颜色映射）。"""
    raw_text = _cell_text(cell)
    text = strip_tag_syntax(raw_text)
    has_gray = any(
        run.highlight is not None and is_gray_highlight(run.highlight) for run in cell.runs
    )
    if cell.jc == "center":
        align = 1
    elif cell.jc == "right":
        align = 2
    else:
        align = 0
    return TableCellView(
        text=text,
        col_start=cell.col_start,
        col_span=cell.grid_span,
        v_merge=cell.v_merge,
        align=align,
        sz_half_pt=cell.run_sz_half_pt,
        printed=_cell_printed_marker(raw_text) and bool(text),
        skip_printed=has_gray,
        highlight=_cell_highlight(cell),
    )


def build_table_views(stream: Sequence[BodyElement]) -> list[TableView]:
    """从 body 元素流构造表格视图列表（跳过段落元素，保持文档顺序）。"""
    views: list[TableView] = []
    for element in stream:
        if not isinstance(element, ParsedTable):
            continue
        rows = [
            TableRowView(cells=[_build_cell_view(cell) for cell in row.cells])
            for row in element.rows
        ]
        views.append(TableView(index=len(views) + 1, rows=rows))
    return views


# ---------------------------------------------------------------------------
# 页面字符流匹配器（两指针子序列匹配）
# ---------------------------------------------------------------------------


def _is_control(ch: str) -> bool:
    """是否为 Unicode 控制字符（对齐 Rust char::is_control，即 Cc 类）。"""
    return unicodedata.category(ch) == "Cc"


def _norm_chars(text: str) -> list[str]:
    """归一化文本：丢弃空白与控制字符（docx 文本与 PDF 字符层口径一致）。"""
    return [ch for ch in text if not ch.isspace() and not _is_control(ch)]


@dataclass
class CellMatch:
    """单个单元格在页面字符流中的命中结果。"""

    char_idxs: list[int] = field(default_factory=list)
    """命中的 chars 下标（阅读顺序）。"""


@dataclass
class TablePageMatch:
    """一张表在一页上的匹配结果（与 TableView.rows 行列对齐）。"""

    table_index: int
    row_matches: list[list[CellMatch | None]] = field(default_factory=list)
    located_cell_count: int = 0
    """本页定位成功的可定位格数。"""

    locatable_cell_count: int = 0
    """本页参与匹配判定的格数（非续行、归一化文本 ≥ 2 字）。"""

    located_on_page: bool = False
    """表格是否在本页开始输出区域（定位格数 ≥ 阈值，或定位比例 ≥ 消费比例）。"""


class PageStreamMatcher:
    """页面字符流匹配器（两指针子序列匹配）。

    ``seq`` 为页面字符流经归一化后的 ``(chars 下标, 字符)`` 序列；``cursor``
    是本页已消费到的位置。放大匹配窗口（CELL_WINDOW=3000）是为了容忍表格
    单元格在 PDF 字符层与 docx 文本之间的轻微顺序偏差，但窗口内的字符一旦
    被某格命中即被占用，后续格不会再复用。
    """

    def __init__(self, chars: Sequence[ExtractedChar]) -> None:
        self.seq: list[tuple[int, str]] = [
            (i, c.ch)
            for i, c in enumerate(chars)
            if not c.ch.isspace() and not _is_control(c.ch)
        ]
        self.cursor = 0

    def try_match(self, target: Sequence[str], window: int, consume_on_fail: bool) -> list[int]:
        """从 cursor 起在 window 内逐字符匹配 target。

        返回命中下标；``consume_on_fail=True`` 时失败也保留已消费进度
        （正文段落尽力消耗），否则回滚到匹配起点。
        """
        start = self.cursor
        limit = min(start + window, len(self.seq))
        hit: list[int] = []
        probe = self.cursor
        for want in target:
            found = -1
            while probe < limit:
                if self.seq[probe][1] == want:
                    found = probe
                    break
                probe += 1
            if found < 0:
                self.cursor = probe if consume_on_fail else start
                return hit
            hit.append(self.seq[found][0])
            probe = found + 1
        self.cursor = probe
        return hit

    def match_cell(self, target: Sequence[str]) -> CellMatch | None:
        """匹配单元格文本：命中比例 ≥ CELL_MATCH_RATIO 判定定位成功。

        定位成功（含部分命中）时推进 cursor 到最后命中字符之后——命中的页面
        字符已被本格占用，相邻格不得重叠匹配。

        归一化长度 < 2 的格不参与匹配：跨页拆分表会被后续页重走（未消费），
        1 字格会在 CELL_WINDOW 内撞上任意同名无关字符（单字命中即 100%，
        轻松越过阈值）造成幻影定位——真持有格随后失配回退带推断，同一文字
        被重复渲染。短格的兜底是带推断，且前提是同行存在可定位的多字格提供
        行带；整行均为单字格（如序号行 一/二/三、口算表数字格）时无行带可用，
        该行保持印刷底图（不生成区域）。
        """
        if len(target) < 2:
            return None
        hit = self.try_match(target, CELL_WINDOW, consume_on_fail=False)
        if hit and len(hit) / len(target) >= CELL_MATCH_RATIO:
            self.advance_to_after(hit[-1])
            return CellMatch(char_idxs=hit)
        return None

    def match_paragraph(self, target: Sequence[str]) -> bool:
        """匹配正文段落：命中比例 ≥ PARAGRAPH_MATCH_RATIO 判定段落在本页。

        成功时 cursor 推进到最后命中字符之后（段落字符已为本段占用）。
        失败时 try_match（consume_on_fail=False）已把 cursor 回退到匹配起点——
        不得“尽力消耗”：跨页重走时，前页段落会把后页字符流的 cursor 一路
        推过流尾（每段最多 PARAGRAPH_WINDOW=500 字），轮到表格元素时已无字符
        可配（游标中毒，真实用户 2 页合同 0 区域的根因）。
        """
        hit = self.try_match(target, PARAGRAPH_WINDOW, consume_on_fail=False)
        if hit and len(hit) / len(target) >= PARAGRAPH_MATCH_RATIO:
            self.advance_to_after(hit[-1])
            return True
        return False

    def advance_to_after(self, char_idx: int) -> None:
        """把 cursor 推进到原始下标 char_idx 的命中字符之后。

        部分命中时 try_match 已回滚 cursor 到匹配起点，命中位置必在其前方；
        整段命中时 cursor 已越过该字符（原始下标唯一），此处为空操作。
        """
        for pos in range(self.cursor, len(self.seq)):
            if self.seq[pos][0] == char_idx:
                self.cursor = pos + 1
                return


def locate_tables_on_page(
    stream: Sequence[BodyElement],
    views: Sequence[TableView],
    chars: Sequence[ExtractedChar],
    start_element: int,
) -> tuple[list[TablePageMatch], int]:
    """在单页字符流上按文档顺序定位表格单元格，从 ``start_element`` 起走。

    其之前的元素属于更早的页，本页不触碰——否则前页段落会消耗本页字符，
    游标中毒。返回 ``(各表匹配结果（含 located_on_page 标记）, matched_upto)``：
    ``matched_upto`` 为“最后一个已消费元素之后”的下标，供下一页作为
    ``start_element``。

    - 段落匹配成功（比例达标）→ 消费；
    - 表格定位比例 > TABLE_CONSUMED_RATIO → 整表消费（后续页不再重走；
      恰好一半不算）；
    - 拆分表（已开始但未过半）与本页未定位的表不推进 matched_upto——下一页
      重走该表时，已定位格的字符不在下一页流中，只会定位余下部分，不会重复；
    - 分割中的表（本页已定位但未消费）还钳制 matched_upto：按文档序其后的
      段落渲染于表尾之后，只可能出现在更晚的页，本页任何段落“匹配”均为
      假匹配（短段落如 甲方/乙方 2 字即可廉价误中），不得借其把 matched_upto
      推过该表（否则表被永久跳过，余下半表 0 区域）。从未定位过的表
      （located == 0）不钳制：其后的首个合法段落匹配仍可推进 matched_upto
      （自愈路径）。
    - 未命中任何文字的表也返回（row_matches 全 None、located_on_page=false，
      由调用方按 located_on_page 门禁过滤）。
    """
    matcher = PageStreamMatcher(chars)
    result: list[TablePageMatch] = []
    # 视图游标对齐文档表序：start_element 之前的表格已由更早的页处理，
    # 本页走到的第 k 张表对应 views 中第 k 张（views 按文档顺序构建）
    view_cursor = sum(
        1 for el in stream[: max(0, min(start_element, len(stream)))]
        if isinstance(el, ParsedTable)
    )
    matched_upto = start_element
    # 最后一个“正在分割中”的表元素下标（本页已定位未消费）：
    # 走完后用于把 matched_upto 钳回该表（假段落匹配防护）。
    splitting_table_element: int | None = None

    for i, element in enumerate(stream):
        if i < start_element:
            continue
        if isinstance(element, BodyParagraph):
            norm = _norm_chars(element.text)
            if norm and matcher.match_paragraph(norm):
                matched_upto = i + 1
            continue

        # ---- 表格 ----
        if view_cursor >= len(views):
            continue
        view = views[view_cursor]
        view_cursor += 1
        located = 0
        locatable = 0
        row_matches: list[list[CellMatch | None]] = []
        for row in view.rows:
            row_result: list[CellMatch | None] = []
            for cell in row.cells:
                if cell.v_merge is CellVMerge.CONTINUE:
                    row_result.append(None)  # 续行格无内容
                    continue
                norm = _norm_chars(cell.text)
                if len(norm) < 2:
                    row_result.append(None)  # 短格不参与匹配（与 match_cell 规则一致）
                    continue
                locatable += 1
                match = matcher.match_cell(norm)
                if match is not None:
                    located += 1
                row_result.append(match)
            row_matches.append(row_result)

        ratio = located / locatable if locatable > 0 else 0.0
        page_match = TablePageMatch(
            table_index=view.index,
            row_matches=row_matches,
            located_cell_count=located,
            locatable_cell_count=locatable,
            located_on_page=located >= TABLE_LOCATED_MIN_CELLS
            or (locatable > 0 and ratio >= TABLE_CONSUMED_RATIO),
        )
        if locatable > 0 and ratio > TABLE_CONSUMED_RATIO:
            matched_upto = i + 1  # 整表完成：后续页不再重走（严格大于：恰好一半仍在分割）
        elif located > 0:
            # 分割中（已定位未消费）：记为钳制点，走完后不得让后续段落的
            # 假匹配把 matched_upto 推过本表。
            splitting_table_element = i
        result.append(page_match)

    # 假段落匹配钳制：分割中的表之后的段落匹配在本页必为假命中，钳回。
    if splitting_table_element is not None and matched_upto > splitting_table_element:
        matched_upto = splitting_table_element
    return result, matched_upto


# ---------------------------------------------------------------------------
# 矩形推断
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RectPx:
    """像素矩形（背景图原始像素坐标）。"""

    x: int
    y: int
    w: int
    h: int


def _union_char_bbox(chars: Sequence[ExtractedChar], idxs: Sequence[int], pad: int) -> RectPx:
    """命中字符包围盒（每边外扩 pad 像素，左/上钳到 0）。"""
    if not idxs:  # 防御：空命中不该走到这里（调用方仅对已定位格调用）
        return RectPx(x=0, y=0, w=0, h=0)
    min_x = math.inf
    min_y = math.inf
    max_x = -math.inf
    max_y = -math.inf
    for i in idxs:
        c = chars[i]
        min_x = min(min_x, c.min_x)
        min_y = min(min_y, c.min_y)
        max_x = max(max_x, c.max_x)
        max_y = max(max_y, c.max_y)
    x0 = math.floor(min_x)
    y0 = math.floor(min_y)
    return RectPx(
        x=max(x0 - pad, 0),
        y=max(y0 - pad, 0),
        w=int(math.ceil(max_x) - x0) + pad * 2,
        h=int(math.ceil(max_y) - y0) + pad * 2,
    )


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _view_max_cols(view: TableView) -> int:
    """表格最大列数（各行列终点的最大值）。"""
    best = 0
    for row in view.rows:
        for cell in row.cells:
            best = max(best, cell.col_start + cell.col_span)
    return best


def infer_cell_rects(
    view: TableView,
    m: TablePageMatch,
    chars: Sequence[ExtractedChar],
) -> list[RectPx | None]:
    """推算整表所有单元格矩形（行主序扁平输出，与 view.rows 迭代顺序对齐）。

    已定位格 = 命中字符 bbox（pad=2）；未命中格 = 行/列带交集内缩 3px；
    vMerge 续行格与无法推算的格为 None。行列带只由“已定位格”统计，因此
    整行/整列都没定位到时该行/列无法推算（保持印刷底图，不生成区域）。
    """
    # 每格 bbox（未定位为 None）
    bboxes: list[list[RectPx | None]] = []
    for r_idx, row in enumerate(view.rows):
        row_matches = m.row_matches[r_idx] if r_idx < len(m.row_matches) else []
        row_boxes: list[RectPx | None] = []
        for c_idx, _cell in enumerate(row.cells):
            cell_match = (
                row_matches[c_idx]
                if c_idx < len(row_matches)
                else None
            )
            row_boxes.append(
                _union_char_bbox(chars, cell_match.char_idxs, 2) if cell_match is not None else None
            )
        bboxes.append(row_boxes)

    # 列带：由已定位的单列格（col_span==1 且非续行）收集左右边缘
    col_count = _view_max_cols(view)
    col_left: list[list[float]] = [[] for _ in range(col_count)]
    col_right: list[list[float]] = [[] for _ in range(col_count)]
    for r_idx, row in enumerate(view.rows):
        for c_idx, cell in enumerate(row.cells):
            rect = bboxes[r_idx][c_idx]
            if rect is None:
                continue
            if cell.col_span == 1 and cell.v_merge is not CellVMerge.CONTINUE:
                col_left[cell.col_start].append(float(rect.x))
                col_right[cell.col_start].append(float(rect.x + rect.w))

    # 行带：由该行任意已定位格收集上下边缘
    row_top: list[list[float]] = [[] for _ in view.rows]
    row_bottom: list[list[float]] = [[] for _ in view.rows]
    for r_idx, row in enumerate(view.rows):
        for c_idx, _cell in enumerate(row.cells):
            rect = bboxes[r_idx][c_idx]
            if rect is None:
                continue
            row_top[r_idx].append(float(rect.y))
            row_bottom[r_idx].append(float(rect.y + rect.h))

    out: list[RectPx | None] = []
    for r_idx, row in enumerate(view.rows):
        for c_idx, cell in enumerate(row.cells):
            if cell.v_merge is CellVMerge.CONTINUE:
                out.append(None)
                continue
            rect = bboxes[r_idx][c_idx]
            if rect is not None:
                out.append(rect)
                continue
            # 未定位格：行带 × 列带交集（跨列取首末列带），内缩 3px
            x0 = _mean(col_left[cell.col_start])
            last_col = min(cell.col_start + cell.col_span - 1, len(col_right) - 1)
            x1 = _mean(col_right[last_col])
            y0 = _mean(row_top[r_idx])
            y1 = _mean(row_bottom[r_idx])
            if x0 is None or x1 is None or y0 is None or y1 is None or not (x1 > x0 and y1 > y0):
                out.append(None)
                continue
            out.append(
                RectPx(
                    x=int(x0 + 3.0),
                    y=int(y0 + 3.0),
                    w=int(x1 - x0 - 6.0),
                    h=int(y1 - y0 - 6.0),
                )
            )
    return out
