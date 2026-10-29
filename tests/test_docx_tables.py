"""docx 表格结构解析 + 单元格跨页定位测试。

移植自 Rust 0.5.0 ``crates/core/src/core/docx_tables.rs`` 的单元测试模块，
并补充 ``docx_io::parse_body_stream`` 的结构解析用例。

造 docx 的手段：python-docx 建空文档 → 把最小 ``document.xml`` body 片段
（只用标准 WordprocessingML 标签）挂到 body（sectPr 之前）→ 保存 → 走真实的
``load_body_stream``。这样既覆盖 python-docx 的 lxml 树遍历路径，又不必手写
完整 docx 包（[Content_Types].xml / rels 等）。
"""

from __future__ import annotations

import pytest
from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn

from handwritesim.core.doc_render import ExtractedChar
from handwritesim.core.docx_io import (
    BodyParagraph,
    CellVMerge,
    ParsedTable,
    is_gray_highlight,
    load_body_stream,
)
from handwritesim.core.docx_tables import (
    RectPx,
    build_table_views,
    infer_cell_rects,
    locate_tables_on_page,
)

# ---------------------------------------------------------------------------
# 造数据辅助
# ---------------------------------------------------------------------------


def ch(c: str, x: float, y: float) -> ExtractedChar:
    """造一个 10×10 像素的页面字符（与 Rust 测试的 ch() 一致）。"""
    return ExtractedChar(
        ch=c, min_x=x, min_y=y, max_x=x + 10.0, max_y=y + 10.0, font_size_pt=10.5, glyph_h_pt=10.0
    )


def stream_from_body_xml(body_xml: str, tmp_path, name: str = "table.docx"):
    """把 body 片段写入新 docx 并解析为 body 元素流。"""
    doc = Document()
    body = doc.element.body
    frag = parse_xml(f'<w:wrap {nsdecls("w")}>{body_xml}</w:wrap>')
    sect_pr = body.find(qn("w:sectPr"))
    for el in list(frag):
        if sect_pr is not None:
            sect_pr.addprevious(el)
        else:
            body.append(el)
    path = tmp_path / name
    doc.save(str(path))
    return load_body_stream(path)


def tc(text: str) -> str:
    """一个单段落单元格（text 直接作为 w:t 内容）。"""
    return f"<w:tc><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:tc>"


def grid(n: int) -> str:
    return "<w:gridCol/>" * n


def row_chars(texts: list[str], y0: float) -> list[ExtractedChar]:
    """按单元格文本列表生成一行页面字符（k 格横排，格内逐字排列）。"""
    out: list[ExtractedChar] = []
    for k, text in enumerate(texts):
        x = 10.0 + k * 100.0
        for j, c in enumerate(text):
            out.append(ch(c, x + j * 12.0, y0))
    return out


def flat_matches(page_match):
    return [m for row in page_match.row_matches for m in row]


# ---------------------------------------------------------------------------
# 定位：阅读顺序 / 正文不抢格 / 单字格 / 长文档后页
# ---------------------------------------------------------------------------


def test_locates_cells_in_reading_order(tmp_path):
    stream = stream_from_body_xml(
        "<w:p><w:r><w:t>前面的正文</w:t></w:r></w:p>"
        f"<w:tbl><w:tblGrid>{grid(2)}</w:tblGrid><w:tr>{tc('甲格')}{tc('乙格')}</w:tr></w:tbl>",
        tmp_path,
    )
    views = build_table_views(stream)
    chars = [
        ch("前", 10.0, 10.0), ch("面", 22.0, 10.0), ch("的", 34.0, 10.0),
        ch("正", 46.0, 10.0), ch("文", 58.0, 10.0),
        ch("甲", 10.0, 50.0), ch("格", 22.0, 50.0),
        ch("乙", 110.0, 50.0), ch("格", 122.0, 50.0),
    ]
    matches, _ = locate_tables_on_page(stream, views, chars, 0)
    assert len(matches) == 1
    m = matches[0]
    assert m.table_index == 1
    assert m.row_matches[0][0].char_idxs == [5, 6]
    assert m.row_matches[0][1].char_idxs == [7, 8]


def test_body_text_with_same_content_does_not_steal_cell_match(tmp_path):
    # 正文里出现与单元格相同的文字且在字符流中先出现：
    # 段落元素先行消耗自己的字符，单元格匹配不会误中正文位置
    stream = stream_from_body_xml(
        "<w:p><w:r><w:t>甲格</w:t></w:r></w:p>"
        f"<w:tbl><w:tblGrid>{grid(1)}</w:tblGrid><w:tr>{tc('甲格')}</w:tr></w:tbl>",
        tmp_path,
    )
    views = build_table_views(stream)
    chars = [
        ch("甲", 10.0, 10.0), ch("格", 22.0, 10.0),      # 正文（页面上部）
        ch("甲", 10.0, 100.0), ch("格", 22.0, 100.0),    # 单元格（页面下部）
    ]
    matches, _ = locate_tables_on_page(stream, views, chars, 0)
    assert matches[0].row_matches[0][0].char_idxs == [2, 3]


def test_single_char_cell_does_not_false_match_across_pages(tmp_path):
    # 跨页短格回归：1 字格会在 CELL_WINDOW 内撞上任意同名无关字符
    #（单字命中即 100%，轻松越过 60% 阈值）造成幻影定位 → 同一文字被
    # 定位+推断各渲染一次。规则：归一化长度 < 2 的格不参与匹配。
    stream = stream_from_body_xml(
        "<w:p><w:r><w:t>无关正文内容</w:t></w:r></w:p>"
        f"<w:tbl><w:tblGrid>{grid(2)}</w:tblGrid>"
        f"<w:tr>{tc('甲格甲格')}{tc('乙')}</w:tr></w:tbl>",
        tmp_path,
    )
    views = build_table_views(stream)
    chars = [
        ch("无", 10.0, 10.0), ch("关", 22.0, 10.0), ch("正", 34.0, 10.0),
        ch("文", 46.0, 10.0), ch("内", 58.0, 10.0), ch("容", 70.0, 10.0),
        ch("甲", 10.0, 100.0), ch("格", 22.0, 100.0),
        ch("甲", 34.0, 100.0), ch("格", 46.0, 100.0),
        ch("乙", 200.0, 200.0),
    ]
    matches, _ = locate_tables_on_page(stream, views, chars, 0)
    m = matches[0]
    assert m.row_matches[0][0].char_idxs == [6, 7, 8, 9]
    assert m.row_matches[0][1] is None, "1 字格不得误匹配"


def test_long_doc_table_on_later_page_locates(tmp_path):
    # 长正文 + 表格在第 2 页：旧逻辑每页都从元素 0 走整个文档流，第 1 页
    # 段落对第 2 页字符流“尽力消耗”把游标推过流尾 → 表格 0 区域。
    p1_text = "这是一段非常长的正文内容，用来填充整个第一页版面的占位文字，合计超过三十个字符"
    stream = stream_from_body_xml(
        f"<w:p><w:r><w:t>{p1_text}</w:t></w:r></w:p>"
        f"<w:tbl><w:tblGrid>{grid(2)}</w:tblGrid>"
        f"<w:tr>{tc('甲格')}{tc('乙格')}</w:tr>"
        f"<w:tr>{tc('丙格')}{tc('丁格')}</w:tr></w:tbl>",
        tmp_path,
    )
    views = build_table_views(stream)
    page1 = [
        ch(c, 10.0 + (i % 20) * 12.0, 10.0 + (i // 20) * 14.0)
        for i, c in enumerate(p1_text)
    ]
    m1, next1 = locate_tables_on_page(stream, views, page1, 0)
    assert len(m1) == 1
    assert not m1[0].located_on_page, "表格不在第 1 页：单元格字符不在第 1 页流中"
    assert m1[0].located_cell_count == 0
    assert m1[0].locatable_cell_count == 4
    assert next1 == 1, "未定位表格不得被跳过"

    page2 = [
        ch("甲", 10.0, 500.0), ch("格", 22.0, 500.0), ch("乙", 110.0, 500.0), ch("格", 122.0, 500.0),
        ch("丙", 10.0, 560.0), ch("格", 22.0, 560.0), ch("丁", 110.0, 560.0), ch("格", 122.0, 560.0),
    ]
    m2, next2 = locate_tables_on_page(stream, views, page2, next1)
    assert m2[0].located_on_page, "表格在第 2 页应被定位"
    assert m2[0].located_cell_count == 4
    assert m2[0].locatable_cell_count == 4
    assert all(m is not None for m in flat_matches(m2[0]))
    assert next2 == 2, "整表定位完成（4/4 ≥ 0.5）→ 消费，后续页不再重走"

    # 游标中毒回归锁：即使从元素 0 起走（含第 1 页段落），段落匹配失败也不得
    # 推进游标（旧逻辑 consume_on_fail=true 会把游标推到流尾）
    m3, _ = locate_tables_on_page(stream, views, page2, 0)
    assert m3[0].located_on_page, "start=0 对第 2 页字符流不得中毒"
    assert m3[0].located_cell_count == 4


# ---------------------------------------------------------------------------
# 跨页拆分 / 恰好一半 / 假段落钳制
# ---------------------------------------------------------------------------


def test_split_table_continues_on_next_page(tmp_path):
    cells = ["甲格", "乙格", "丙格", "丁格", "戊格", "己格", "庚格", "辛格"]
    body = (
        f"<w:tbl><w:tblGrid>{grid(4)}</w:tblGrid>"
        f"<w:tr>{''.join(tc(t) for t in cells[:4])}</w:tr>"
        f"<w:tr>{''.join(tc(t) for t in cells[4:])}</w:tr></w:tbl>"
    )
    stream = stream_from_body_xml(body, tmp_path)
    views = build_table_views(stream)
    page1 = row_chars(cells[:3], 100.0)
    page2 = row_chars(cells[3:], 200.0)

    m1, next1 = locate_tables_on_page(stream, views, page1, 0)
    assert m1[0].located_on_page, "3 >= 2：拆分表已开始"
    assert m1[0].located_cell_count == 3
    assert m1[0].locatable_cell_count == 8
    assert next1 == 0, "3/8 = 0.375 < 0.5 未完成：下一页仍指向表格元素"

    m2, next2 = locate_tables_on_page(stream, views, page2, next1)
    assert m2[0].located_on_page
    flat = flat_matches(m2[0])
    assert all(m is None for m in flat[:3]), "第 1 页已定位的格在第 2 页不得再定位"
    assert all(m is not None for m in flat[3:])
    assert m2[0].located_cell_count == 5
    assert m2[0].locatable_cell_count == 8
    assert next2 == 1, "5/8 = 0.625 ≥ 0.5：整表完成 → 消费"
    # 不重复：两页合计恰好 8，且任何一页都未输出全部
    assert m1[0].located_cell_count + m2[0].located_cell_count == 8
    assert m1[0].located_cell_count < 8 and m2[0].located_cell_count < 8


def test_even_split_table_continues_on_next_page(tmp_path):
    # 恰好 50%：消费判定必须是严格大于——0.5 不消费，下一页重走本表只定位
    # 余下 2 格（用 >= 判定会让后半永不出现在任何后续页的起步元素流中）。
    cells = ["甲格", "乙格", "丙格", "丁格"]
    body = (
        f"<w:tbl><w:tblGrid>{grid(4)}</w:tblGrid>"
        f"<w:tr>{''.join(tc(t) for t in cells)}</w:tr></w:tbl>"
    )
    stream = stream_from_body_xml(body, tmp_path)
    views = build_table_views(stream)
    page1 = row_chars(cells[:2], 100.0)
    page2 = row_chars(cells[2:], 200.0)

    m1, next1 = locate_tables_on_page(stream, views, page1, 0)
    assert m1[0].located_on_page, "2 >= 2：拆分表已开始输出区域"
    assert m1[0].located_cell_count == 2
    assert m1[0].locatable_cell_count == 4
    assert next1 == 0, "2/4 = 0.5 恰好一半：不得消费"

    m2, _ = locate_tables_on_page(stream, views, page2, next1)
    assert m2[0].located_on_page, "第 2 页重走表格，后 2 格定位"
    flat = flat_matches(m2[0])
    assert all(m is None for m in flat[:2])
    assert all(m is not None for m in flat[2:])
    assert m2[0].located_cell_count == 2
    assert m1[0].located_cell_count + m2[0].located_cell_count == 4


def test_false_paragraph_match_cannot_skip_splitting_table(tmp_path):
    # 表（4 格，第 1 页出前 2 格，分割中）后跟短段落「甲方」：按文档序它渲染在
    # 表尾之后、物理上应在更晚的页，但字符混入第 1 页流（2 字段落廉价误中）。
    # 分割中的表必须钳制 matched_upto，否则下一页从表后元素起步 → 表被永久跳过。
    cells = ["甲格", "乙格", "丙格", "丁格"]
    body = (
        f"<w:tbl><w:tblGrid>{grid(4)}</w:tblGrid>"
        f"<w:tr>{''.join(tc(t) for t in cells)}</w:tr></w:tbl>"
        "<w:p><w:r><w:t>甲方</w:t></w:r></w:p>"
    )
    stream = stream_from_body_xml(body, tmp_path)
    views = build_table_views(stream)
    page1 = row_chars(cells[:2], 100.0)
    page1.extend(row_chars(["甲方"], 500.0))
    m1, next1 = locate_tables_on_page(stream, views, page1, 0)
    assert m1[0].located_cell_count == 2, "表格前半定位（分割中）"
    assert next1 == 0, "假段落匹配不得把 matched_upto 推过分割中的表"

    page2 = row_chars(cells[2:], 200.0)
    m2, next2 = locate_tables_on_page(stream, views, page2, next1)
    flat = flat_matches(m2[0])
    assert all(m is None for m in flat[:2])
    assert all(m is not None for m in flat[2:]), "余下 2 格必须在第 2 页定位"
    assert m2[0].located_on_page
    assert next2 == 0, "2/4 = 0.5 仍在分割：流起点保持指向表格"


# ---------------------------------------------------------------------------
# 视图构造：对齐 / 印刷体 / 灰格 / 彩色字映射
# ---------------------------------------------------------------------------


def test_builds_views_from_stream(tmp_path):
    body = (
        "<w:p><w:r><w:t>正文</w:t></w:r></w:p>"
        f"<w:tbl><w:tblGrid>{grid(2)}</w:tblGrid><w:tr>"
        '<w:tc><w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
        "<w:r><w:t>标题</w:t></w:r></w:p></w:tc>"
        '<w:tc><w:p><w:r><w:rPr><w:highlight w:val="yellow"/></w:rPr>'
        "<w:t>{{手写:答案}}</w:t></w:r></w:p></w:tc>"
        "</w:tr></w:tbl>"
    )
    stream = stream_from_body_xml(body, tmp_path)
    assert [type(e) for e in stream] == [BodyParagraph, ParsedTable]
    views = build_table_views(stream)
    assert len(views) == 1
    assert views[0].index == 1
    c0 = views[0].rows[0].cells[0]
    assert c0.text == "标题"
    assert c0.align == 1
    assert not c0.printed
    c1 = views[0].rows[0].cells[1]
    assert c1.text == "答案", "{{手写:...}} 标签清理为内容"
    assert c1.highlight == "yellow"


def test_printed_and_gray_cells_flagged(tmp_path):
    body = (
        f"<w:tbl><w:tblGrid>{grid(2)}</w:tblGrid><w:tr>"
        f"{tc('{{打印:2026-01-01}}')}"
        '<w:tc><w:p><w:r><w:rPr><w:highlight w:val="lightGray"/></w:rPr>'
        "<w:t>灰色格</w:t></w:r></w:p></w:tc>"
        "</w:tr></w:tbl>"
    )
    stream = stream_from_body_xml(body, tmp_path)
    cells = build_table_views(stream)[0].rows[0].cells
    assert cells[0].text == "2026-01-01"
    assert cells[0].printed, "{{打印:}} → 印刷体区域"
    assert not cells[0].skip_printed
    assert cells[1].skip_printed, "灰高亮 → 整格保留印刷底图"
    assert not cells[1].printed


def test_gray_and_white_font_color_not_mapped_to_highlight(tmp_path):
    # 无 w:highlight 时字色回退要求饱和度（通道差 ≥ 30，与 is_highlight_pixel
    # 同口径）。灰/白是正文配色，旧逻辑取首个非黑 run 直落 classify 兜底的
    # “yellow”，误配黄色角色。
    def color_tc(color: str, text: str) -> str:
        return (
            f'<w:tc><w:p><w:r><w:rPr><w:color w:val="{color}"/></w:rPr>'
            f"<w:t>{text}</w:t></w:r></w:p></w:tc>"
        )

    body = (
        f"<w:tbl><w:tblGrid>{grid(2)}</w:tblGrid>"
        f"<w:tr>{color_tc('7F7F7F', '灰字格')}{color_tc('FFFFFF', '白字格')}</w:tr>"
        f"<w:tr>{color_tc('A6A6A6', '浅灰格')}{color_tc('D9D9D9', '更浅灰格')}</w:tr>"
        "</w:tbl>"
    )
    stream = stream_from_body_xml(body, tmp_path)
    views = build_table_views(stream)
    assert views[0].rows[0].cells[0].highlight is None, "7F7F7F 灰字不映射"
    assert views[0].rows[0].cells[1].highlight is None, "FFFFFF 白字不映射"
    assert views[0].rows[1].cells[0].highlight is None, "A6A6A6 灰字不映射"
    assert views[0].rows[1].cells[1].highlight is None, "D9D9D9 灰字不映射"


def test_colored_run_maps_to_cell_highlight(tmp_path):
    # 用户用 w:color（字体颜色）而非 w:highlight 标记表格格：非黑彩色 run
    # 回退映射为标准高亮色名；黑色是正文默认色，不构成标记。
    def color_tc(color: str, text: str) -> str:
        return (
            f'<w:tc><w:p><w:r><w:rPr><w:color w:val="{color}"/></w:rPr>'
            f"<w:t>{text}</w:t></w:r></w:p></w:tc>"
        )

    body = (
        f"<w:tbl><w:tblGrid>{grid(3)}</w:tblGrid><w:tr>"
        f"{color_tc('92D050', '你好')}{color_tc('0000FF', '我知道了')}{color_tc('000000', '普通黑字')}"
        "</w:tr></w:tbl>"
    )
    stream = stream_from_body_xml(body, tmp_path)
    cells = build_table_views(stream)[0].rows[0].cells
    assert cells[0].highlight == "green", "92D050 绿字 → green"
    assert cells[1].highlight == "blue", "0000FF 蓝字 → blue"
    assert cells[2].highlight is None, "黑色 w:color 不映射"


def test_is_gray_highlight_matches_rust_list():
    assert is_gray_highlight("lightGray")
    assert is_gray_highlight(" GRAY-25 ")
    assert is_gray_highlight("grey50")
    assert not is_gray_highlight("yellow")
    assert not is_gray_highlight("")


# ---------------------------------------------------------------------------
# 游标：失配不推进 / 部分命中 / 阈值 / 续行格
# ---------------------------------------------------------------------------


def test_absent_paragraph_does_not_advance_cursor(tmp_path):
    stream = stream_from_body_xml(
        "<w:p><w:r><w:t>完全不出现在页面上的无关段落正文内容</w:t></w:r></w:p>"
        f"<w:tbl><w:tblGrid>{grid(1)}</w:tblGrid><w:tr>{tc('甲格')}</w:tr></w:tbl>",
        tmp_path,
    )
    views = build_table_views(stream)
    chars = [ch("甲", 10.0, 100.0), ch("格", 22.0, 100.0)]
    matches, next_idx = locate_tables_on_page(stream, views, chars, 0)
    # 段落 0 失配 → 游标留在 0，单元格仍从起点命中 0、1
    assert matches[0].row_matches[0][0].char_idxs == [0, 1], "段落失配不得推进游标"
    assert matches[0].located_cell_count == 1
    assert matches[0].locatable_cell_count == 1
    assert next_idx == 2, "1/1 = 1.0 ≥ 0.5：表格消费（段落本身不推进 matched_upto）"


def test_unmatched_cell_below_threshold_yields_none(tmp_path):
    stream = stream_from_body_xml(
        f"<w:tbl><w:tblGrid>{grid(1)}</w:tblGrid><w:tr>{tc('五行文字')}</w:tr></w:tbl>",
        tmp_path,
    )
    views = build_table_views(stream)
    chars = [
        ch("五", 10.0, 10.0), ch("x", 30.0, 10.0), ch("无", 50.0, 10.0), ch("关", 70.0, 10.0),
    ]
    matches, _ = locate_tables_on_page(stream, views, chars, 0)
    assert matches[0].row_matches[0][0] is None, "1/4 = 25% < 60% 不定位"


def test_vmerge_continue_cell_skips_without_matching(tmp_path):
    # 续行格即使残留文字也直接返回 None，不参与匹配、不消耗字符流；
    # 同行后续单元格仍在正确位置命中
    body = (
        f"<w:tbl><w:tblGrid>{grid(2)}</w:tblGrid>"
        '<w:tr><w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr>'
        f"<w:p><w:r><w:t>甲格</w:t></w:r></w:p></w:tc>{tc('乙格')}</w:tr>"
        '<w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr>'
        f"<w:p><w:r><w:t>丙格</w:t></w:r></w:p></w:tc>{tc('丁格')}</w:tr>"
        "</w:tbl>"
    )
    stream = stream_from_body_xml(body, tmp_path)
    views = build_table_views(stream)
    chars = [
        ch("甲", 10.0, 10.0), ch("格", 22.0, 10.0),
        ch("乙", 110.0, 10.0), ch("格", 122.0, 10.0),
        ch("丙", 10.0, 50.0), ch("格", 22.0, 50.0),
        ch("丁", 110.0, 50.0), ch("格", 122.0, 50.0),
    ]
    matches, _ = locate_tables_on_page(stream, views, chars, 0)
    m = matches[0]
    assert m.row_matches[0][0].char_idxs == [0, 1]
    assert m.row_matches[0][1].char_idxs == [2, 3]
    assert m.row_matches[1][0] is None, "续行格跳过（否则会误中 4、5）"
    assert m.row_matches[1][1].char_idxs == [6, 7]


def test_partially_matched_cell_advances_cursor_preventing_overlap(tmp_path):
    # A 格 75% 命中（「总」不在页面任何位置）：若 cursor 不随部分命中前进，
    # B 格「汇率」的贪心扫描会误中 A 已占用的「汇」（下标 2），命中下标重叠
    stream = stream_from_body_xml(
        f"<w:tbl><w:tblGrid>{grid(2)}</w:tblGrid><w:tr>{tc('金额汇总')}{tc('汇率')}</w:tr></w:tbl>",
        tmp_path,
    )
    views = build_table_views(stream)
    chars = [
        ch("金", 10.0, 10.0), ch("额", 22.0, 10.0), ch("汇", 34.0, 10.0),  # A：3/4 = 75%
        ch("汇", 110.0, 50.0), ch("率", 122.0, 50.0),                      # B
    ]
    matches, _ = locate_tables_on_page(stream, views, chars, 0)
    m = matches[0]
    a = m.row_matches[0][0]
    b = m.row_matches[0][1]
    assert a.char_idxs == [0, 1, 2], "部分命中也算定位"
    assert b.char_idxs == [3, 4], "不复用 A 的下标 2"
    assert not set(a.char_idxs) & set(b.char_idxs), "相邻格命中下标不得重叠"


# ---------------------------------------------------------------------------
# 矩形推断
# ---------------------------------------------------------------------------


def test_infers_missing_cell_rect_from_bands(tmp_path):
    # 2×2 表：三个格有文字可定位，右下格为空 → 由行列带推算
    body = (
        f"<w:tbl><w:tblGrid>{grid(2)}</w:tblGrid>"
        f"<w:tr>{tc('甲格')}{tc('乙格')}</w:tr>"
        f"<w:tr>{tc('丙格')}{tc(' ')}</w:tr></w:tbl>"
    )
    stream = stream_from_body_xml(body, tmp_path)
    views = build_table_views(stream)
    chars = [
        ch("甲", 100.0, 100.0), ch("格", 112.0, 100.0), ch("乙", 300.0, 100.0), ch("格", 312.0, 100.0),
        ch("丙", 100.0, 200.0), ch("格", 112.0, 200.0),
    ]
    matches, _ = locate_tables_on_page(stream, views, chars, 0)
    rects = infer_cell_rects(views[0], matches[0], chars)
    assert len(rects) == 4
    # 甲格：命中字符 bbox（+2px pad；两字横排宽 22px → w=26）→ x≈98, y≈98
    assert rects[0] == RectPx(98, 98, 26, 14)
    assert rects[1].x == 298
    assert rects[2].y == 198
    # 右下空格：行带 y∈[198,212] × 列带 x∈[298,324] 交集（内缩 3px）
    r3 = rects[3]
    assert 296 <= r3.x <= 302, f"r3.x={r3.x}"
    assert 196 <= r3.y <= 202, f"r3.y={r3.y}"
    assert r3.w > 0 and r3.h > 0


def test_no_bands_no_rect(tmp_path):
    # 第二行无命中 → 该行空格无法推算（第一行用 2 字格保证可定位出列带）
    body = (
        f"<w:tbl><w:tblGrid>{grid(2)}</w:tblGrid>"
        f"<w:tr>{tc('甲格')}{tc('乙格')}</w:tr>"
        f"<w:tr>{tc(' ')}{tc(' ')}</w:tr></w:tbl>"
    )
    stream = stream_from_body_xml(body, tmp_path)
    views = build_table_views(stream)
    chars = [
        ch("甲", 100.0, 100.0), ch("格", 112.0, 100.0),
        ch("乙", 300.0, 100.0), ch("格", 312.0, 100.0),
    ]
    matches, _ = locate_tables_on_page(stream, views, chars, 0)
    rects = infer_cell_rects(views[0], matches[0], chars)
    assert rects[2] is None
    assert rects[3] is None


def test_vmerge_continue_cell_rect_is_none(tmp_path):
    body = (
        f"<w:tbl><w:tblGrid>{grid(1)}</w:tblGrid>"
        '<w:tr><w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr>'
        f"<w:p><w:r><w:t>甲格</w:t></w:r></w:p></w:tc></w:tr>"
        '<w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr></w:tc></w:tr>'
        "</w:tbl>"
    )
    stream = stream_from_body_xml(body, tmp_path)
    views = build_table_views(stream)
    chars = [ch("甲", 100.0, 100.0), ch("格", 112.0, 100.0)]
    matches, _ = locate_tables_on_page(stream, views, chars, 0)
    rects = infer_cell_rects(views[0], matches[0], chars)
    assert rects[0] == RectPx(98, 98, 26, 14)
    assert rects[1] is None, "续行格不生成矩形（整块由起始格表达）"


# ---------------------------------------------------------------------------
# docx_io 结构解析：嵌套表格 / 文本框 / 自闭合 gridCol / gridSpan / 多段落
# ---------------------------------------------------------------------------


def test_nested_table_content_and_props_do_not_leak(tmp_path):
    body = (
        f"<w:tbl><w:tblGrid>{grid(2)}</w:tblGrid><w:tr><w:tc>"
        "<w:p><w:r><w:t>外层</w:t></w:r></w:p>"
        # 嵌套表格：内容与属性（jc / highlight / gridSpan）都不得泄漏进外层格
        "<w:tbl><w:tblGrid><w:gridCol/></w:tblGrid><w:tr>"
        '<w:tc><w:tcPr><w:gridSpan w:val="3"/></w:tcPr>'
        '<w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
        '<w:r><w:rPr><w:highlight w:val="yellow"/></w:rPr><w:t>内层</w:t></w:r></w:p></w:tc>'
        "</w:tr></w:tbl>"
        "<w:p><w:r><w:t>外二</w:t></w:r></w:p>"
        f"</w:tc>{tc('第二格')}</w:tr></w:tbl>"
    )
    stream = stream_from_body_xml(body, tmp_path)
    table = stream[0]
    assert isinstance(table, ParsedTable)
    assert table.grid_cols == 2, "嵌套 tblGrid 不得计入外层列数"
    assert len(table.rows) == 1, "嵌套表格的行不得计入外层"
    assert len(table.rows[0].cells) == 2
    cell = table.rows[0].cells[0]
    text = "".join(r.text for r in cell.runs)
    assert text == "外层\n外二", f"嵌套表格文本泄漏：{text!r}"
    assert all(r.highlight is None for r in cell.runs), "内层高亮属性不得泄漏"
    assert cell.grid_span == 1, "内层 gridSpan 不得泄漏"
    assert cell.jc is None, "内层段落 jc 不得泄漏"


def test_textbox_content_ignored(tmp_path):
    body = (
        "<w:p><w:r><w:t>可见</w:t></w:r>"
        "<w:r><w:txbxContent><w:p><w:r><w:t>框内</w:t></w:r></w:p></w:txbxContent></w:r></w:p>"
        f"<w:tbl><w:tblGrid>{grid(1)}</w:tblGrid><w:tr>"
        '<w:tc><w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
        "<w:r><w:t>格</w:t></w:r>"
        "<w:r><w:txbxContent><w:p><w:pPr><w:jc w:val=\"right\"/></w:pPr>"
        "<w:r><w:t>框内2</w:t></w:r></w:p></w:txbxContent></w:r>"
        "</w:p></w:tc></w:tr></w:tbl>"
    )
    stream = stream_from_body_xml(body, tmp_path)
    assert stream[0].text == "可见", "文本框文字不得混入 body 段落"
    cell = stream[1].rows[0].cells[0]
    assert "".join(r.text for r in cell.runs) == "格", "文本框文字不得混入单元格"
    assert cell.jc == "center", "文本框段落的 jc 不得覆盖本格首段"


def test_selfclosing_grid_col_counted(tmp_path):
    # 真实文档的 w:gridCol 多为自闭合（无子元素），必须照样计数：
    # 三种写法（自闭合 / 带属性自闭合 / 显式开闭）都应计 1 列
    body = (
        "<w:tbl><w:tblGrid>"
        '<w:gridCol/><w:gridCol w:w="3000"/><w:gridCol></w:gridCol>'
        "</w:tblGrid>"
        f"<w:tr>{tc('甲乙')}{tc('丙丁')}{tc('戊己')}</w:tr></w:tbl>"
    )
    stream = stream_from_body_xml(body, tmp_path)
    assert stream[0].grid_cols == 3


def test_gridspan_colstart_and_vmerge_span_inherit(tmp_path):
    body = (
        f"<w:tbl><w:tblGrid>{grid(3)}</w:tblGrid>"
        # 第 1 行：跨 2 列 + 1 列 → 列号 0、2
        '<w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr>'
        f"<w:p><w:r><w:t>横向</w:t></w:r></w:p></w:tc>{tc('右一')}</w:tr>"
        # 第 2 行：纵向合并起始格（同样跨 2 列）
        '<w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr>'
        f"<w:p><w:r><w:t>纵合</w:t></w:r></w:p></w:tc>{tc('右二')}</w:tr>"
        # 第 3 行：续行格未声明 gridSpan → 继承上方 2 列
        '<w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr></w:tc>'
        f"{tc('右三')}</w:tr>"
        "</w:tbl>"
    )
    stream = stream_from_body_xml(body, tmp_path)
    rows = stream[0].rows
    assert (rows[0].cells[0].col_start, rows[0].cells[0].grid_span) == (0, 2)
    assert rows[0].cells[1].col_start == 2
    assert rows[1].cells[0].v_merge is CellVMerge.RESTART
    assert rows[1].cells[1].col_start == 2
    bottom = rows[2].cells[0]
    assert bottom.v_merge is CellVMerge.CONTINUE
    assert bottom.grid_span == 2, "续行格继承上方同列跨列数"
    assert bottom.col_start == 0
    assert rows[2].cells[1].col_start == 2


def test_cell_multi_paragraph_joined_with_newline(tmp_path):
    body = (
        f"<w:tbl><w:tblGrid>{grid(1)}</w:tblGrid><w:tr><w:tc>"
        '<w:p><w:pPr><w:jc w:val="right"/></w:pPr>'
        '<w:r><w:rPr><w:sz w:val="21"/></w:rPr><w:t>第一段</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
        '<w:r><w:rPr><w:sz w:val="44"/></w:rPr><w:t>第二段</w:t></w:r></w:p>'
        "</w:tc></w:tr></w:tbl>"
    )
    stream = stream_from_body_xml(body, tmp_path)
    cell = stream[0].rows[0].cells[0]
    assert "".join(r.text for r in cell.runs) == "第一段\n第二段"
    assert [r.text for r in cell.runs] == ["第一段", "\n", "第二段"]
    assert cell.jc == "right", "jc 取首段"
    assert cell.run_sz_half_pt == 21, "字号取首段首个 run"


def test_load_body_stream_order_and_paragraph_text(tmp_path):
    body = (
        "<w:p><w:r><w:t>甲</w:t></w:r><w:r><w:t>乙</w:t></w:r></w:p>"
        f"<w:tbl><w:tblGrid>{grid(1)}</w:tblGrid><w:tr>{tc('丙丁')}</w:tr></w:tbl>"
        "<w:p><w:r><w:t>戊</w:t></w:r></w:p>"
    )
    stream = stream_from_body_xml(body, tmp_path)
    assert [type(e) for e in stream] == [BodyParagraph, ParsedTable, BodyParagraph]
    assert stream[0].text == "甲乙"
    assert stream[2].text == "戊"


def test_load_body_stream_invalid_docx_raises_value_error(tmp_path):
    path = tmp_path / "broken.docx"
    path.write_bytes(b"not a docx at all")
    with pytest.raises(ValueError):
        load_body_stream(path)
