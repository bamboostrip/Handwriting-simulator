"""PDF 底图手写区域自动识别测试（对齐 Rust 版 doc_render.rs 行为）。

覆盖：高亮像素判定、高亮框检测/合并/擦除、占位标签扫描与语法清理、
PDF 文本层字符提取（文字/字号/行距/缩进）、高亮框+标签区域合并与
角色分配、端到端 PDF 导入检测，以及区域绑定角色的引擎渲染。
"""

from __future__ import annotations

import os

import numpy as np
import pytest
from PIL import Image

from handwritesim.core.doc_render import (
    BoundingBox,
    ExtractedChar,
    classify_highlight_color,
    combine_page_regions,
    combine_page_regions_with_role_map,
    detect_highlight_boxes,
    document_to_page_images_with_regions,
    erase_highlight_boxes,
    extract_pdf_page_chars,
    extract_text_and_font_size_for_box,
    is_highlight_pixel,
    merge_close_boxes,
    pdf_to_images_with_regions,
    scan_text_tags,
    strip_tag_syntax,
)
from handwritesim.core.models import HandwritingParams, HandwritingRole, TextRegion


# ---------------------------------------------------------------------------
# 高亮像素与颜色分类
# ---------------------------------------------------------------------------
def test_highlight_pixel_classifier() -> None:
    # 标准高亮色
    assert is_highlight_pixel(255, 255, 0)     # 黄
    assert is_highlight_pixel(0, 255, 0)       # 绿
    assert is_highlight_pixel(0, 255, 255)     # 青
    assert is_highlight_pixel(255, 0, 255)     # 品红
    assert is_highlight_pixel(255, 105, 180)   # 粉红
    assert is_highlight_pixel(100, 180, 255)   # 浅蓝
    assert is_highlight_pixel(255, 80, 80)     # 浅红
    # 灰度/黑白背景与文字
    assert not is_highlight_pixel(255, 255, 255)
    assert not is_highlight_pixel(0, 0, 0)
    assert not is_highlight_pixel(30, 30, 30)
    assert not is_highlight_pixel(128, 128, 128)
    assert not is_highlight_pixel(240, 240, 240)
    assert not is_highlight_pixel(200, 205, 202)  # 轻微抗锯齿灰边


def test_classify_highlight_color_names() -> None:
    assert classify_highlight_color(255, 255, 0) == "yellow"
    assert classify_highlight_color(0, 255, 0) == "green"
    assert classify_highlight_color(0, 255, 255) == "cyan"
    assert classify_highlight_color(255, 0, 255) == "magenta"
    assert classify_highlight_color(255, 105, 180) == "pink"
    assert classify_highlight_color(255, 80, 80) == "red"
    assert classify_highlight_color(80, 80, 255) == "blue"
    assert classify_highlight_color(100, 180, 255) == "cyan"  # gf>150 且 rf<150 优先判青


# ---------------------------------------------------------------------------
# 高亮框检测 / 合并 / 擦除
# ---------------------------------------------------------------------------
def test_detect_highlight_boxes_and_erase() -> None:
    img = Image.new("RGB", (400, 300), (255, 255, 255))
    arr = np.array(img)
    arr[60:90, 50:150] = (255, 255, 0)      # 黄 (50,60) 100x30
    arr[150:170, 200:280] = (0, 255, 255)   # 青 (200,150) 80x20
    arr[10:15, 10:15] = (255, 0, 0)         # 5x5 噪点（应被过滤）
    img = Image.fromarray(arr)

    boxes = detect_highlight_boxes(img)
    assert len(boxes) == 2, "应检测到 2 个有效高亮框并过滤噪点"

    b1 = next(b for b in boxes if b.min_x == 50 and b.min_y == 60)
    assert (b1.width(), b1.height()) == (100, 30)
    assert b1.highlight == "yellow"

    b2 = next(b for b in boxes if b.min_x == 200 and b.min_y == 150)
    assert (b2.width(), b2.height()) == (80, 20)
    assert b2.highlight == "cyan"

    # 擦除后高亮区域应变为纯白
    erase_highlight_boxes(img, boxes)
    out = np.asarray(img)
    assert (out[60:90, 50:150] == 255).all()
    assert (out[150:170, 200:280] == 255).all()


def test_merge_four_consecutive_line_highlight_boxes() -> None:
    """4 行间距 25px 的连续同色高亮框应合并为 1 个段落包围盒。"""
    boxes = [BoundingBox(100, 50 + i * 45, 400, 70 + i * 45, "yellow") for i in range(4)]
    merged = merge_close_boxes(boxes)
    assert len(merged) == 1
    assert (merged[0].min_x, merged[0].max_x) == (100, 400)
    assert (merged[0].min_y, merged[0].max_y) == (50, 205)
    assert merged[0].highlight == "yellow"


def test_merge_does_not_swallow_across_unhighlighted_lines() -> None:
    """回归：间隔 109px（跨一行未高亮内容）的同色高亮框不应被累计大框吞并。"""
    boxes = [BoundingBox(100, 50 + i * 45, 400, 70 + i * 45, "yellow") for i in range(4)]
    boxes.append(BoundingBox(150, 314, 350, 334, "yellow"))
    merged = merge_close_boxes(boxes)
    assert len(merged) == 2
    assert merged[0].max_y == 205
    assert merged[1].min_y == 314


# ---------------------------------------------------------------------------
# 占位标签
# ---------------------------------------------------------------------------
def test_scan_text_tags() -> None:
    m1 = scan_text_tags(list("请在此处手写：{{ 签名 }}，祝好！"))
    assert [x.inner_text for x in m1] == ["签名"]

    m2 = scan_text_tags(list("意见：【同意批准】 日期：【2026-09-02】"))
    assert [x.inner_text for x in m2] == ["同意批准", "2026-09-02"]

    m3 = scan_text_tags(list("空标签：{{}} 以及未闭合标签 {{ 未闭合"))
    assert [x.inner_text for x in m3] == [""]


def test_strip_tag_syntax() -> None:
    assert strip_tag_syntax("纯文本内容") == "纯文本内容"
    assert strip_tag_syntax("{{ 签名 }}") == "签名"
    assert strip_tag_syntax("{{手写:张三}}") == "张三"
    assert strip_tag_syntax("{{手写1: 李四}}") == "李四"
    assert strip_tag_syntax("{{打印: 2026-09-02}}") == "2026-09-02"
    assert strip_tag_syntax("【同意批准】") == "同意批准"
    assert strip_tag_syntax("【手写：王五】") == "王五"
    assert strip_tag_syntax("{{}}") == ""
    assert strip_tag_syntax("【】") == ""
    assert strip_tag_syntax("   {{  审核通过  }}   ") == "审核通过"
    assert strip_tag_syntax("前缀 {{手写:内容}} 后缀") == "前缀 内容 后缀"
    assert strip_tag_syntax("前缀 【手写：结论】 后缀") == "前缀 结论 后缀"


# ---------------------------------------------------------------------------
# 框内文字与字号提取
# ---------------------------------------------------------------------------
def _line_chars(text: str, x0: float, y0: float, size: float, step: float = 35.0):
    return [
        ExtractedChar(ch, x0 + k * step, y0, x0 + k * step + 30.0, y0 + 30.0, size)
        for k, ch in enumerate(text)
    ]


def test_extract_text_and_font_size_for_box() -> None:
    scale = 200.0 / 72.0
    chars = [
        *_line_chars("张三", 100.0, 50.0, 12.0, step=25.0),
        ExtractedChar("外", 500.0, 500.0, 520.0, 520.0, 10.0),
    ]
    text, fs, _, _ = extract_text_and_font_size_for_box(
        chars, BoundingBox(95, 45, 150, 75), scale
    )
    assert text == "张三"
    assert fs == round(12.0 * scale)

    # 空高亮框
    text2, _, _, _ = extract_text_and_font_size_for_box(
        chars, BoundingBox(300, 300, 350, 330), scale
    )
    assert text2 == ""


def test_extract_sorts_characters_reading_order() -> None:
    """乱序的多行字符应按阅读顺序还原（行间换行、行内从左到右）。"""
    chars = [
        ExtractedChar("行", 90.0, 139.0, 105.0, 155.0, 12.0),
        ExtractedChar("一", 70.0, 101.0, 85.0, 116.0, 12.0),
        ExtractedChar("第", 50.0, 100.0, 65.0, 115.0, 12.0),
        ExtractedChar("二", 70.0, 141.0, 85.0, 156.0, 12.0),
        ExtractedChar("行", 90.0, 99.0, 105.0, 114.0, 12.0),
        ExtractedChar("第", 50.0, 140.0, 65.0, 155.0, 12.0),
    ]
    text, _, _, _ = extract_text_and_font_size_for_box(
        chars, BoundingBox(45, 95, 110, 160), 1.0
    )
    assert text == "第一行\n第二行"


def test_extract_multiline_line_spacing_and_indent() -> None:
    """多行提取应检测行距（pitch - 字号）与首行缩进（em）。"""
    chars = [
        *_line_chars("第一行", 120.0, 35.0, 30.0),   # 首行缩进 60px = 2em
        *_line_chars("第二行", 60.0, 75.0, 30.0),
        *_line_chars("第三行", 60.0, 115.0, 30.0),
    ]
    text, fs, ls, indent = extract_text_and_font_size_for_box(
        chars, BoundingBox(50, 30, 250, 150), 1.0
    )
    assert text == "第一行\n第二行\n第三行"
    assert fs == 30
    assert abs(ls - 10.0) < 1e-3  # pitch 40 - 字号 30
    assert indent == 2.0


def test_resolve_font_size_falls_back_when_raw_inflated() -> None:
    """raw 字号被嵌入字体矩阵放大（如 Word 中文 PDF 返回 221 而实际 10.5pt）
    时，应回退到全角字符紧包围盒高度估计，而不是产生 20 倍字号。"""
    from handwritesim.core.doc_render import _resolve_font_size_px

    scale = 200.0 / 72.0
    # loose 高 21.12pt、tight 高 10.18pt、raw 字号 221（Word 等线字体实测值）
    chars = [
        ExtractedChar("张", 0, 0, 21.12, 21.12, 221.0, glyph_h_pt=10.18),
        ExtractedChar("三", 25, 0, 46.12, 21.12, 221.0, glyph_h_pt=10.19),
        ExtractedChar("丰", 50, 0, 71.12, 21.12, 221.0, glyph_h_pt=10.15),
    ]
    fs = _resolve_font_size_px(chars, scale)
    assert fs == round(10.19 * scale)  # ≈28px，而非 221×scale≈614

    # raw 字号与包围盒一致（常规 PDF）时保持原值
    chars_ok = [
        ExtractedChar("H", 0, 0, 18, 26.78, 24.0, glyph_h_pt=17.3),
        ExtractedChar("i", 20, 0, 30, 26.78, 24.0, glyph_h_pt=11.9),
    ]
    assert _resolve_font_size_px(chars_ok, scale) == round(24.0 * scale)

    # raw 轻度偏大（pdfium-render 场景，仍在可信区间）+ 存在全角字符：
    # 取 min(raw, 全角紧包围盒) —— 宁可略小也不放不下（与 Rust 版对齐）
    chars_slight = [
        ExtractedChar("张", 0, 0, 21.12, 21.12, 11.16, glyph_h_pt=10.18),
        ExtractedChar("三", 25, 0, 46.12, 21.12, 11.16, glyph_h_pt=10.19),
    ]
    assert _resolve_font_size_px(chars_slight, scale) == round(10.19 * scale)


# ---------------------------------------------------------------------------
# 高亮框 + 标签区域合并与角色分配
# ---------------------------------------------------------------------------
def test_combine_page_regions() -> None:
    scale = 200.0 / 72.0
    highlight_boxes = [
        BoundingBox(50, 100, 250, 140, "yellow"),
        BoundingBox(300, 500, 400, 530, "cyan"),
    ]
    tag_regions = [
        TextRegion(x=60, y=105, w=80, h=20, text="请签名", page=1, font_size=28),
        TextRegion(x=100, y=300, w=120, h=25, text="独立标签", page=1, font_size=24),
    ]
    page_chars = [
        ExtractedChar(ch, 60 + k * 25, 105, 80 + k * 25, 125, 12.0)
        for k, ch in enumerate("请签名")
    ]

    combined = combine_page_regions(highlight_boxes, tag_regions, page_chars, 1, scale)
    assert len(combined) == 3

    # 1. 与字符重叠的高亮框：使用高亮框尺寸 + 提取的文字/字号，分配角色 2
    r1 = next(r for r in combined if r.x == 50 and r.y == 100)
    assert (r1.w, r1.h) == (201, 41)
    assert r1.text == "请签名"
    assert r1.page == 1
    assert r1.font_size == round(12.0 * scale)
    assert r1.role_id == 2
    assert r1.highlight == "yellow"

    # 2. 独立标签（未与高亮框重叠）：默认角色 0
    r2 = next(r for r in combined if r.x == 100 and r.y == 300)
    assert r2.text == "独立标签"
    assert r2.font_size == 24
    assert r2.role_id == 0
    assert r2.highlight is None

    # 3. 无文字高亮框：分配角色 3
    r3 = next(r for r in combined if r.x == 300 and r.y == 500)
    assert r3.text == ""
    assert r3.role_id == 3
    assert r3.highlight == "cyan"


def test_combine_allocates_same_role_for_same_color_across_pages() -> None:
    """同一高亮颜色跨页应映射到同一角色（color_map 为共享状态）。"""
    color_map: dict[str, int] = {}
    next_role_id = [2]
    page1 = combine_page_regions_with_role_map(
        [BoundingBox(10, 10, 100, 40, "yellow")], [], [], 1, 1.0, color_map, next_role_id
    )
    page2 = combine_page_regions_with_role_map(
        [BoundingBox(10, 10, 100, 40, "yellow"), BoundingBox(10, 60, 100, 90, "green")],
        [], [], 2, 1.0, color_map, next_role_id,
    )
    assert page1[0].role_id == 2
    assert page2[0].role_id == 2   # 黄色跨页复用角色 2
    assert page2[1].role_id == 3   # 绿色新分配角色 3


# ---------------------------------------------------------------------------
# PDF 文本层提取（手工构造带文本的 PDF）
# ---------------------------------------------------------------------------
def _make_text_pdf(path) -> str:
    """构造带两行 Helvetica 文本（24pt / 12pt）的最小 PDF。"""
    content = (
        b"BT /F1 24 Tf 72 700 Td (Hello World) Tj ET\n"
        b"BT /F1 12 Tf 72 650 Td (Small) Tj ET"
    )
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs)+1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(bytes(out))
    doc.save(str(path))
    doc.close()
    return str(path)


def test_extract_pdf_page_chars_text_and_font_size(tmp_path) -> None:
    import pypdfium2 as pdfium

    pdf = _make_text_pdf(tmp_path / "text.pdf")
    doc = pdfium.PdfDocument(pdf)
    try:
        page = doc[0]
        chars = extract_pdf_page_chars(page, dpi=72)
        # 零尺寸控制符（\r\n）应被过滤，只留下可见字符
        text = "".join(c.ch for c in chars)
        assert text.replace(" ", "") == "HelloWorldSmall"
        hello = [c for c in chars if c.font_size_pt == 24.0]
        small = [c for c in chars if c.font_size_pt == 12.0]
        assert len(hello) == 11 and len(small) == 5
        # 坐标原点在左上：Hello(基线 700) 在 Small(基线 650) 上方
        assert min(c.min_y for c in hello) < min(c.min_y for c in small)
        # 72dpi 下像素坐标与 PDF 点一致
        first = hello[0]
        assert first.min_x == pytest.approx(72.0, abs=1.0)

        # 框住 Hello World 的包围盒 -> 提取文本与字号
        box = BoundingBox(60, 60, 220, 100)  # y: 792-722=70 .. 792-695=97
        text2, fs, _, _ = extract_text_and_font_size_for_box(chars, box, 1.0)
        assert text2 == "Hello World"
        assert fs == 24
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# 端到端：img2pdf 生成的高亮色块页 -> 检测 + 擦除 + 角色分配
# ---------------------------------------------------------------------------
def _system_font() -> str:
    for candidate in (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ):
        if os.path.exists(candidate):
            return candidate
    pytest.skip("未找到系统 CJK 字体")


def _make_highlight_pdf(tmp_path, blocks_per_page):
    """blocks_per_page: 每页的高亮色块列表 [(y0, y1, x0, x1, rgb), ...]。"""
    import img2pdf

    src_paths = []
    for i, blocks in enumerate(blocks_per_page):
        arr = np.full((300, 400, 3), 255, dtype=np.uint8)
        for y0, y1, x0, x1, rgb in blocks:
            arr[y0:y1, x0:x1] = rgb
        p = tmp_path / f"hl_src_{i}.png"
        Image.fromarray(arr).save(p)
        src_paths.append(str(p))
    pdf_path = tmp_path / "hl.pdf"
    pdf_path.write_bytes(img2pdf.convert(src_paths))
    return str(pdf_path)


def test_pdf_to_images_with_regions_end_to_end(tmp_path) -> None:
    pdf = _make_highlight_pdf(tmp_path, [
        [(60, 90, 50, 150, (255, 255, 0)), (150, 170, 200, 320, (180, 255, 180))],
        [(100, 140, 80, 300, (0, 255, 255))],
    ])
    paths, regions = pdf_to_images_with_regions(pdf, tmp_path / "out", dpi=72)

    assert len(paths) == 2
    assert [r.highlight for r in regions] == ["yellow", "green", "cyan"]
    assert [r.role_id for r in regions] == [2, 3, 4]
    assert [r.page for r in regions] == [1, 1, 2]
    # 区域按从上到下排序
    assert [r.y for r in regions if r.page == 1] == sorted(r.y for r in regions if r.page == 1)

    # 输出底图中高亮被擦除为纯白
    out0 = np.asarray(Image.open(paths[0]).convert("RGB"))
    assert (out0[60:90, 50:150] == 255).all()
    assert (out0[150:170, 200:320] == 255).all()
    out1 = np.asarray(Image.open(paths[1]).convert("RGB"))
    assert (out1[100:140, 80:300] == 255).all()


def test_document_import_unsupported_type(tmp_path) -> None:
    with pytest.raises(ValueError):
        document_to_page_images_with_regions(tmp_path / "a.txt", tmp_path)


def test_pdf_no_highlight_yields_no_regions(tmp_path) -> None:
    """白底纯文本 PDF（无高亮无标签）不应产生区域。"""
    pdf = _make_text_pdf(tmp_path / "plain.pdf")
    paths, regions = pdf_to_images_with_regions(pdf, tmp_path / "out", dpi=72)
    assert len(paths) == 1
    assert regions == []


# ---------------------------------------------------------------------------
# 区域绑定角色的引擎渲染
# ---------------------------------------------------------------------------
def _region_engine_params(tmp_path, roles=None):
    bg = tmp_path / "bg.png"
    Image.new("RGB", (600, 500), "white").save(bg)
    return HandwritingParams(
        font_path=_system_font(),
        background_path=str(bg),
        font_size=36,
        line_spacing=97,  # 模拟整页预设的大行距
        roles=roles,
    )


def test_region_inherits_role_attributes(tmp_path) -> None:
    """区域未显式指定的字体/颜色/字号/扰动应继承绑定的角色。"""
    from handwritesim.core.engine_fast import FastEngine

    params = _region_engine_params(tmp_path, roles=[
        HandwritingRole(id=0, name="默认手写"),
        HandwritingRole(id=1, name="打印体", printed=True),
        HandwritingRole(
            id=2, name="高亮角色", font_path=_system_font(),
            font_size=26, color="#ff0000", printed=False,
            word_spacing=5, line_spacing=30,
        ),
    ])
    region = TextRegion(x=10, y=10, w=200, h=40, text="继承测试", role_id=2)
    engine = FastEngine(seed=1)
    rp = engine._region_params(params, region)
    assert rp.font_path == params.roles[2].font_path
    assert rp.color == "#ff0000"
    assert rp.font_size == 26
    assert rp.word_spacing == 5
    # 角色行距优先于自然行距推断
    assert rp.line_spacing == 30


def test_region_role_printed_zero_perturbation(tmp_path) -> None:
    """绑定打印体角色的区域应零扰动：不同 seed 输出完全一致。"""
    from handwritesim.core.engine import HandwritingEngine

    def render(seed) -> np.ndarray:
        params = _region_engine_params(tmp_path, roles=[
            HandwritingRole(id=0, name="默认手写"),
            HandwritingRole(id=1, name="打印体", printed=True, color="#333333"),
        ])
        params.regions = [
            TextRegion(x=50, y=50, w=300, h=60, text="打印角色区域", role_id=1)
        ]
        return np.asarray(
            HandwritingEngine(backend="fast", seed=seed).render_preview(params).convert("RGB")
        )

    a = render(11)
    b = render(22)
    assert np.array_equal(a, b), "打印体角色区域零扰动，跨 seed 应逐像素一致"
    assert (a != 255).any(), "区域应有墨迹"


def test_small_region_layout_produces_visible_ink(tmp_path) -> None:
    """矮单行区域在整页大行距预设下仍应在框内出墨（首行基线修复）。"""
    from handwritesim.core.engine import HandwritingEngine

    params = _region_engine_params(tmp_path)
    params.regions = [
        TextRegion(x=10, y=10, w=200, h=44, text="思想汇报", printed=True)
    ]
    img = HandwritingEngine(backend="fast", seed=5).render_preview(params)
    ink = np.asarray(img.convert("L")) < 128
    inner = ink[10:54, 10:210]
    assert inner.any(), "矮区域 (h=44, font_size=36) 内应生成有效墨迹"


def test_multiline_region_renders_with_clean_line_spacing(tmp_path) -> None:
    """多行区域的行间应有清晰分隔（自然行距，不继承整页 97px 行距）。"""
    from handwritesim.core.engine import HandwritingEngine

    params = _region_engine_params(tmp_path)
    params.regions = [
        TextRegion(
            x=20, y=20, w=320, h=150,
            text="第一行测试内容\n第二行排版内容\n第三行验证内容",
            printed=True,
        )
    ]
    img = HandwritingEngine(backend="fast", seed=42).render_preview(params)
    ink = np.asarray(img.convert("L")) < 128
    inner = ink[20:170, 20:340]
    assert inner.any()
    rows_with_ink = inner.any(axis=1)
    # 统计连续墨迹带数量 = 3 行
    bands = 0
    prev = False
    for v in rows_with_ink:
        if v and not prev:
            bands += 1
        prev = v
    assert bands == 3, f"3 行文本应产生 3 个墨迹带，实际 {bands}"


# ---------------------------------------------------------------------------
# 区域扰动比例缩放 / 行距盒高收敛 / 末行保留（对齐 Rust 暂存修复）
# ---------------------------------------------------------------------------
def test_region_noise_sigma_scaled_by_font_ratio(tmp_path) -> None:
    """小字号区域的噪声 σ 应按 区域字号/主字号 比例缩小，避免字被"摇散"。"""
    from handwritesim.core.engine_fast import FastEngine

    params = _region_engine_params(tmp_path)
    params.font_size = 100
    params.word_spacing_sigma = 8
    params.line_spacing_sigma = 8
    params.font_size_sigma = 8
    params.perturb_x_sigma = 8
    params.perturb_y_sigma = 8

    region = TextRegion(x=10, y=10, w=300, h=40, text="小字号区域", font_size=25)
    rp = FastEngine(seed=1)._region_params(params, region)
    assert rp.font_size == 25
    assert rp.word_spacing_sigma == pytest.approx(2.0)   # 8 × 25/100
    assert rp.line_spacing_sigma == pytest.approx(2.0)
    assert rp.font_size_sigma == pytest.approx(2.0)
    assert rp.perturb_x_sigma == pytest.approx(2.0)
    assert rp.perturb_y_sigma == pytest.approx(2.0)
    # 均值类参数不缩放
    assert rp.word_spacing == params.word_spacing

    # 字号与主字号一致时不缩放
    region_same = TextRegion(x=10, y=10, w=300, h=60, text="同字号", font_size=100)
    rp2 = FastEngine(seed=1)._region_params(params, region_same)
    assert rp2.perturb_x_sigma == 8


def test_region_sigma_scaling_zero_for_printed(tmp_path) -> None:
    """打印体区域 σ 已清零，比例缩放后仍为零。"""
    from handwritesim.core.engine_fast import FastEngine

    params = _region_engine_params(tmp_path)
    params.font_size = 100
    params.perturb_x_sigma = 8
    region = TextRegion(x=0, y=0, w=300, h=40, text="打印", font_size=25, printed=True)
    rp = FastEngine(seed=1)._region_params(params, region)
    assert rp.perturb_x_sigma == 0
    assert rp.font_size_sigma == 0


def test_region_line_spacing_converges_to_fit_box(tmp_path) -> None:
    """多行区域的行距超过盒高容纳上限时应收敛（内容完整优先于行距还原）。"""
    from handwritesim.core.engine_fast import FastEngine

    params = _region_engine_params(tmp_path)
    params.font_size = 36
    region = TextRegion(
        x=20, y=20, w=320, h=110,          # 3 行 × 36 号字刚好放下：上限行距 110/3-36
        text="第一行\n第二行\n第三行",
        line_spacing=30,                    # 显式大行距放不下：3×36+2×30=168 > 110
    )
    rp = FastEngine(seed=1)._region_params(params, region)
    assert rp.line_spacing == pytest.approx(110 / 3 - 36, abs=1e-6)

    # 行距本身放得下时不收紧
    region_ok = TextRegion(
        x=20, y=20, w=320, h=200, text="第一行\n第二行\n第三行", line_spacing=10
    )
    rp2 = FastEngine(seed=1)._region_params(params, region_ok)
    assert rp2.line_spacing == 10


def test_region_last_line_not_dropped_with_bottom_margin(tmp_path) -> None:
    """下边距把行距判定推严时，末行仍应渲染（排版画布 4 倍盒高 + 裁剪回盒内）。

    盒高 140、下边距 10：第三行绘制基线 104 > limit(140-10-36)，
    旧行为（排版高度=盒高）会把末行分页丢弃，只剩 2 行。
    """
    from handwritesim.core.engine import HandwritingEngine

    params = _region_engine_params(tmp_path)
    params.regions = [
        TextRegion(
            x=20, y=20, w=320, h=140,
            text="第一行\n第二行\n第三行",
            printed=True,
            margin_bottom=10,
        )
    ]
    img = HandwritingEngine(backend="fast", seed=9).render_preview(params)
    ink = np.asarray(img.convert("L")) < 128
    inner = ink[20:160, 20:340]
    rows_with_ink = inner.any(axis=1)
    bands = 0
    prev = False
    for v in rows_with_ink:
        if v and not prev:
            bands += 1
        prev = v
    assert bands == 3, f"带下边距的 3 行区域不应丢行，实际墨迹带 {bands} 个"
    # 墨迹不得越出盒底（画布 4 倍盒高的越界墨迹被裁剪回盒内）
    assert not ink[160:, 20:340].any()


def test_preview_downsample_preserves_region_role_binding(_qapp, tmp_path) -> None:
    """预览降采样重建 TextRegion 时应保留 role_id/highlight（预览与导出角色一致）。"""
    from handwritesim.gui.main_window import MainWindow

    bg = tmp_path / "wide_bg.png"
    Image.new("RGB", (5000, 1600), "white").save(bg)  # 超过预览最大宽度触发降采样
    win = MainWindow(out_dir=tmp_path / "out")
    win._ui.lineEdit.setText(_system_font())
    win._ui.lineEdit_2.setText(str(bg))
    win._regions = [
        TextRegion(x=100, y=100, w=400, h=80, text="区域文字",
                   role_id=2, highlight="yellow", font_size=30)
    ]
    params = win.collect_params()
    preview = win._downsample_preview(params)
    assert preview is not params
    r = preview.regions[0]
    assert r.role_id == 2
    assert r.highlight == "yellow"
    assert r.font_size == pytest.approx(30 * (win._preview_max_width / 5000), abs=1)


# ---------------------------------------------------------------------------
# GUI 冒烟：导入高亮 PDF -> 区域填充 + 角色同步 + 对话框角色下拉
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def _qapp():
    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        pytest.skip("PyQt6 不可用")
    return QApplication.instance() or QApplication([])


def test_gui_import_document_populates_regions_and_roles(_qapp, tmp_path, monkeypatch) -> None:
    from PyQt6.QtWidgets import QMessageBox

    from handwritesim.gui.main_window import MainWindow

    pdf = _make_highlight_pdf(tmp_path, [
        [(60, 90, 50, 300, (255, 255, 0)), (150, 170, 200, 380, (180, 255, 180))],
    ])
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    win = MainWindow(out_dir=out_dir)
    win._ui.lineEdit.setText(_system_font())

    monkeypatch.setattr(
        "handwritesim.gui.main_window.QFileDialog.getOpenFileName",
        staticmethod(lambda *a, **k: (pdf, "")),
    )
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

    win._import_document()

    # 背景 & 多页底图状态
    assert win._doc_pages and len(win._doc_pages) == 1
    assert win._ui.lineEdit_2.text() == win._doc_pages[0]

    # 区域已填充并按高亮色绑定角色
    assert len(win._regions) == 2
    assert [r.highlight for r in win._regions] == ["yellow", "green"]
    assert [r.role_id for r in win._regions] == [2, 3]
    role2 = next(r for r in win._roles if r.id == 2)
    role3 = next(r for r in win._roles if r.id == 3)
    assert role2.highlight == "yellow" and "黄色" in role2.name
    assert role3.highlight == "green" and "绿色" in role3.name

    # 区域列表标签显示角色名
    labels = [win._ui.region_list.item(i).text() for i in range(win._ui.region_list.count())]
    assert any(role2.name in t for t in labels)

    # RegionDialog 角色下拉回填与回读
    from handwritesim.gui.region_dialog import RegionDialog

    dlg = RegionDialog(
        win, roles=win._roles, role_id=2, text="测试", page=1,
        main_font_size=36,
    )
    assert dlg.region_role_id == 2
    # 切到打印体角色 -> 样式联动为打印体
    idx = dlg.combo_role.findData(1)
    dlg.combo_role.setCurrentIndex(idx)
    assert dlg.region_role_id == 1
    assert dlg.region_printed is True

    # 渲染链路：collect_params 后区域携带 role_id，引擎可渲染
    from handwritesim.core.engine import HandwritingEngine

    win._ui.textEdit.setPlainText("")
    params = win.collect_params()
    assert [r.role_id for r in params.regions or []] == [2, 3]
    for region in params.regions or []:
        region.text = "填入文字"
    pages = list(HandwritingEngine(backend="fast", seed=3).generate(params))
    assert len(pages) == 1
    assert (np.asarray(pages[0].convert("L")) < 128).any()
