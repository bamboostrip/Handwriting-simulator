"""docx 表格识别导入·端到端手工验证（对齐 Rust 版 ``tests/docx_table_e2e.rs``）。

链路：真实 docx → Word COM（或 LibreOffice）转 PDF → pypdfium2 栅格化
→ 表格定位 / 字符级擦字 → 单元格 TextRegion 生成 → 表格线保留校验。

依赖本机环境（Microsoft Word 或 LibreOffice），默认**跳过**；需要时显式开启：

```powershell
$env:HANDWRITE_E2E_DOCX_TABLE = "1"
.\\.venv\\Scripts\\python.exe -m pytest -q tests/test_table_e2e.py -s
```

输出目录固定为系统临时目录 ``handwrite_sim_e2e_table``，便于人工查看底图 PNG。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
from PIL import Image

from handwritesim.core import doc_render

# 3×3 表格：表头居中、纵向合并、黄色高亮、{{打印:备注}} 印刷格、一个空格。
# 期望：9 格 − 续行格 1 − 空格 1 = 7 个表格区域；表外无独立标签区域。
_DOCUMENT_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body>"
    "<w:p><w:r><w:t>表格前正文</w:t></w:r></w:p>"
    "<w:tbl>"
    '<w:tblPr><w:tblW w:w="0" w:type="auto"/>'
    "<w:tblBorders>"
    '<w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
    '<w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
    '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
    '<w:right w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
    '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
    '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
    "</w:tblBorders>"
    '<w:tblLayout w:type="fixed"/>'
    # 单元格左右边距 108 twips（Word 表格默认）：裸表文字贴边框渲染时，
    # 字符擦除的 2px 外扩会误伤表格线
    '<w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/>'
    '<w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar>'
    "</w:tblPr>"
    '<w:tblGrid><w:gridCol w:w="2768"/><w:gridCol w:w="2768"/><w:gridCol w:w="2768"/></w:tblGrid>'
    # 第一行：表头居中；第三格 {{打印:备注}}
    "<w:tr>"
    '<w:tc><w:tcPr><w:tcW w:w="2768" w:type="dxa"/></w:tcPr>'
    '<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>项目</w:t></w:r></w:p></w:tc>'
    '<w:tc><w:tcPr><w:tcW w:w="2768" w:type="dxa"/></w:tcPr>'
    '<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>数值</w:t></w:r></w:p></w:tc>'
    '<w:tc><w:tcPr><w:tcW w:w="2768" w:type="dxa"/></w:tcPr>'
    '<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>{{打印:备注}}</w:t></w:r></w:p></w:tc>'
    "</w:tr>"
    # 第二行：纵向合并起始格 + 黄色高亮格 + 空格
    "<w:tr>"
    '<w:tc><w:tcPr><w:tcW w:w="2768" w:type="dxa"/><w:vMerge w:val="restart"/></w:tcPr>'
    '<w:p><w:r><w:t>合并说明</w:t></w:r></w:p></w:tc>'
    '<w:tc><w:tcPr><w:tcW w:w="2768" w:type="dxa"/></w:tcPr>'
    '<w:p><w:r><w:rPr><w:highlight w:val="yellow"/></w:rPr><w:t>手写答案</w:t></w:r></w:p></w:tc>'
    '<w:tc><w:tcPr><w:tcW w:w="2768" w:type="dxa"/></w:tcPr><w:p/></w:tc>'
    "</w:tr>"
    # 第三行：续行格（无内容）+ 多字格 + 单字格（"乙项" 定位行带，"丙" 走带推断）
    "<w:tr>"
    '<w:tc><w:tcPr><w:tcW w:w="2768" w:type="dxa"/><w:vMerge/></w:tcPr><w:p/></w:tc>'
    '<w:tc><w:tcPr><w:tcW w:w="2768" w:type="dxa"/></w:tcPr>'
    '<w:p><w:r><w:t>乙项</w:t></w:r></w:p></w:tc>'
    '<w:tc><w:tcPr><w:tcW w:w="2768" w:type="dxa"/></w:tcPr>'
    '<w:p><w:r><w:t>丙</w:t></w:r></w:p></w:tc>'
    "</w:tr>"
    "</w:tbl>"
    "<w:p/>"
    '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
    '<w:pgMar w:top="1440" w:right="1800" w:bottom="1440" w:left="1800" '
    'w:header="851" w:footer="992" w:gutter="0"/></w:sectPr>'
    "</w:body></w:document>"
)

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)

_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>'
)

_DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
)


def _write_test_docx(path: Path) -> None:
    """写出最小可用 docx（各 part 与 Rust E2E 一致）。"""
    import zipfile

    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _ROOT_RELS)
        zf.writestr("word/document.xml", _DOCUMENT_XML)
        zf.writestr("word/_rels/document.xml.rels", _DOC_RELS)


def _word_available() -> bool:
    """Windows 上是否注册了 Word COM（不启动 Word）。"""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Word.Application"):
            return True
    except OSError:
        return False


def _converter_available() -> bool:
    return _word_available() or bool(shutil.which("soffice") or shutil.which("libreoffice"))


_E2E_ENABLED = os.environ.get("HANDWRITE_E2E_DOCX_TABLE", "").strip() in {"1", "true", "yes"}


def test_fixture_docx_parses_into_table_views(tmp_path: Path) -> None:
    """E2E 固件本身的离线校验：整份 docx 的结构解析结果符合预期。

    Word 渲染段（docx→PDF）由上面的端到端测试覆盖；本用例保证即使没有
    Word/LibreOffice，固件与 body 流解析、表格视图构造也始终被验证。
    """
    from handwritesim.core.docx_io import BodyParagraph, CellVMerge, load_body_stream
    from handwritesim.core.docx_tables import build_table_views

    docx_path = tmp_path / "表格测试.docx"
    _write_test_docx(docx_path)

    stream = load_body_stream(docx_path)
    assert len(stream) == 3, "body 流应为 [正文段落, 表格, 空段落]"
    assert isinstance(stream[0], BodyParagraph) and stream[0].text == "表格前正文"

    views = build_table_views(stream)
    assert len(views) == 1
    view = views[0]
    assert view.index == 1 and len(view.rows) == 3

    first = view.rows[0].cells
    assert [c.text for c in first] == ["项目", "数值", "备注"]
    assert first[0].align == 1, "居中格 align=1"
    assert first[2].printed, "{{打印:备注}} → 印刷体区域"

    second = view.rows[1].cells
    assert second[0].v_merge is CellVMerge.RESTART
    assert second[1].highlight == "yellow"
    assert second[2].text == ""

    third = view.rows[2].cells
    assert third[0].v_merge is CellVMerge.CONTINUE, "续行格占位但无内容"
    assert [c.text for c in third[1:]] == ["乙项", "丙"]


def test_docx_table_regions_without_word(tmp_path: Path) -> None:
    """离线半程 E2E：docx 结构 + 伪造 PDF 字符流 → 单元格区域与擦字。

    真实链路里字符流由 Word 渲染出的 PDF 提供；这里按同样的阅读顺序伪造
    字符网格（含 ``{{打印:备注}}`` 的模板字符），验证「表格定位 → 逐字擦白
    → 单元格 TextRegion」整段逻辑，不依赖本机排版引擎。
    """
    import numpy as np

    from handwritesim.core.doc_render import ExtractedChar, process_tables_on_page
    from handwritesim.core.docx_io import load_body_stream
    from handwritesim.core.docx_tables import build_table_views

    docx_path = tmp_path / "表格测试.docx"
    _write_test_docx(docx_path)
    stream = load_body_stream(docx_path)
    views = build_table_views(stream)

    # 与 3×3 表一致的字符网格：列 x=100/400/700，行 y=100/200/300；
    # 第一行第三格保留模板字符（真实 PDF 里 {{打印:备注}} 是逐字渲染的）
    rows = [
        [("项目", 100), ("数值", 400), ("{{打印:备注}}", 700)],
        [("合并说明", 100), ("手写答案", 400), (None, 700)],
        [(None, 100), ("乙项", 400), ("丙", 700)],
    ]
    chars: list[ExtractedChar] = []
    for r, row in enumerate(rows):
        for text, x0 in row:
            if not text:
                continue
            for k, ch in enumerate(text):
                x = x0 + k * 26.0
                y = 100.0 + r * 100.0
                chars.append(ExtractedChar(ch, x, y, x + 24.0, y + 36.0, 26.0, 26.0))

    # 页面图像：整页白底，用于验证字符级擦除（表格线由另一些像素模拟）
    arr = np.full((600, 1000, 3), 255, dtype=np.uint8)
    arr[:, 20] = 0  # 左表格线
    arr[40, :] = 0  # 上表格线

    color_map: dict[str, int] = {}
    next_role_id = [2]
    result = process_tables_on_page(
        stream, views, chars, 0, 1, 200, arr, color_map, next_role_id
    )

    texts = [r.text for r in result.regions]
    assert len(result.regions) == 7, f"9 格 − 续行 1 − 空文本 1 = 7，实际 {texts}"
    assert "备注" in texts, "印刷格文本应剥离 {{打印:}} 模板字符"
    for expect in ("项目", "数值", "合并说明", "手写答案", "乙项"):
        assert expect in texts, f"缺少单元格区域 {expect!r}：{texts}"

    for region in result.regions:
        assert region.table_index == 1 and region.page == 1
        assert region.margin_left == 4 and region.margin_top == 2

    yellow = next(r for r in result.regions if r.highlight == "yellow")
    assert yellow.text == "手写答案"
    assert yellow.role_id == 2, "首个高亮色占用角色 2"
    assert color_map == {"yellow": 2}
    assert next_role_id == [3]

    printed = [r for r in result.regions if r.printed]
    assert [r.text for r in printed] == ["备注"]

    # 命中格的字形像素被擦白，而模拟的表格线（第 20 列 / 第 40 行）保留
    assert arr[100, 100:130].min() == 255, "单元格文字应被字符级擦除"
    assert arr[:, 20].max() == 0, "表格竖线不得被擦除"
    assert arr[40, :].max() == 0, "表格横线不得被擦除"


@pytest.mark.skipif(
    not _E2E_ENABLED or not _converter_available(),
    reason=(
        "端到端导入需要本机 Word/LibreOffice；"
        "请设置 HANDWRITE_E2E_DOCX_TABLE=1 后手动运行"
    ),
)
def test_docx_table_import_end_to_end(tmp_path: Path) -> None:
    """真实 docx → PDF → 底图：单元格区域、角色映射、印刷格与表格线保留。"""
    out_dir = Path(os.environ.get("TEMP", str(tmp_path))) / "handwrite_sim_e2e_table"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    docx_path = out_dir / "表格测试.docx"
    _write_test_docx(docx_path)

    pages, regions = doc_render.document_to_page_images_with_regions(
        docx_path, out_dir, dpi=200
    )
    assert len(pages) == 1, "应为单页文档"

    table_regions = [r for r in regions if r.table_index == 1]
    texts = [r.text for r in table_regions]
    print(f"表格区域 {len(table_regions)} 个：{texts}")
    assert len(table_regions) >= 6, f"3×3 表应生成 6~7 个单元格区域，实际 {texts}"
    for expect in ("项目", "数值", "备注", "合并说明", "手写答案", "乙项"):
        assert expect in texts, f"缺少单元格区域 {expect!r}，实际 {texts}"

    # {{打印:}} 标签被单元格区域接管：不得再生成独立表外标签区域
    assert not [r for r in regions if r.table_index is None], (
        f"表格文档不应有表外区域：{[r.text for r in regions if r.table_index is None]}"
    )

    # 高亮格 → 角色 2（首个高亮色）；{{打印:}} 格 → printed
    yellow = next((r for r in table_regions if r.highlight == "yellow"), None)
    assert yellow is not None and yellow.text == "手写答案"
    assert yellow.role_id == 2, f"首个高亮色应映射角色 2，实际 {yellow.role_id}"
    printed = [r for r in table_regions if r.printed]
    assert len(printed) == 1 and printed[0].text == "备注", "仅 {{打印:备注}} 一格为印刷体"

    # 表格线保留：底图表格外框内存在通栏暗色横线/竖线
    with Image.open(pages[0]) as img:
        rgb = img.convert("RGB")

    def lum(px: tuple[int, int, int]) -> int:
        return (px[0] + px[1] + px[2]) // 3

    xs = [r.x for r in table_regions] + [r.x + r.w for r in table_regions]
    ys = [r.y for r in table_regions] + [r.y + r.h for r in table_regions]
    margin = 40
    x0, x1 = max(min(xs) - margin, 0), min(max(xs) + margin, rgb.width)
    y0, y1 = max(min(ys) - margin, 0), min(max(ys) + margin, rgb.height)
    need = int((x1 - x0) * 0.5)
    h_lines = 0
    for y in range(y0, y1):
        run = best = 0
        for x in range(x0, x1):
            run = run + 1 if lum(rgb.getpixel((x, y))) < 160 else 0
            best = max(best, run)
        if best >= need:
            h_lines += 1
    assert h_lines >= 1, "表格水平边框线应至少保留 1 行（通栏暗线）"
    print(f"表格线检测：水平暗线行 {h_lines}；底图留存于 {out_dir}")
