"""docx 文档解析：提取段落对齐、首行缩进与多角色 Run 流。

增强：
- 读取 <w:rPr><w:highlight w:val="..."/> / <w:shd w:fill="..."/> 背景色，动态映射到角色：
  文档中首次出现的背景色 → 角色2（手写角色1），次出现 → 角色3 … 不限制黄/绿
- 读取 <w:color w:val="..."/> 作为 Run 级颜色覆盖
- 读取 <w:rFonts w:eastAsia/w:ascii> 与 <w:sz> 字体字号，打印体（无背景）沿用原文系统字体，
  手写体则固定用用户选择的手写字体（背景标记段忽略原文纸质字体）
- 支持文本标签拆分 {{角色名:文本}} / {{手写:文本}} / {{打印:文本}}
"""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

from .models import HandwritingRole, Paragraph, TextRun

try:
    from .system_fonts import family_to_file
except Exception:  # 导入失败时回退为空实现
    def family_to_file(family: str):  # type: ignore
        return None

# docx 使用 EMU（1 英寸 = 914400 EMU）；1pt = 12700 EMU（1/72 英寸）
_EMU_PER_PT = 12700

# 标签正则：{{前缀:内容}} 前缀可为 手写/打印/角色1/张三 等任意非冒号非括号字符，内容为任意非 } 字符（非贪婪）
_TAG_RE = re.compile(r"\{\{\s*([^:{}]+?)\s*:\s*(.*?)\s*\}\}", re.DOTALL)


def _first_line_chars(para) -> float | None:
    """读取 w:firstLineChars（1/100 字符），直接格式优先，再沿样式链继承。

    中文 Word 文档的“首行缩进 2 字符”通常写 firstLineChars 而非 EMU，
    python-docx 的 paragraph_format.first_line_indent 读不到它。
    """
    pPr = para._p.pPr
    if pPr is not None:
        ind = pPr.find(qn("w:ind"))
        if ind is not None:
            value = ind.get(qn("w:firstLineChars"))
            if value:
                return int(value) / 100.0
    style = para.style
    while style is not None:
        spPr = style.element.find(qn("w:pPr"))
        if spPr is not None:
            ind = spPr.find(qn("w:ind"))
            if ind is not None:
                value = ind.get(qn("w:firstLineChars"))
                if value:
                    return int(value) / 100.0
        style = style.base_style
    return None


def _font_size_pt(para, doc) -> float:
    """取段落实际字号（pt）：run 直接格式 > 段落样式链 > Normal > docDefaults，兜底 12。"""
    for run in para.runs:
        size = run.font.size
        if size:
            return size.pt
    style = para.style
    while style is not None:
        size = style.font.size
        if size:
            return size.pt
        style = style.base_style
    try:
        size = doc.styles["Normal"].font.size
        if size:
            return size.pt
    except KeyError:
        pass
    el = doc.styles.element.find(qn("w:docDefaults"))
    if el is not None:
        el = el.find(qn("w:rPrDefault"))
        if el is not None:
            el = el.find(qn("w:rPr"))
            if el is not None:
                sz = el.find(qn("w:sz"))
                if sz is not None:
                    val = sz.get(qn("w:val"))
                    if val:
                        return int(val) / 2.0  # 半磅 -> pt
    return 12.0


def _first_line_emu_chars(para, doc) -> float | None:
    """把 firstLine（twips→EMU）按文档字号还原为字符数。

    Word/WPS 某些版本把“首行缩进 2 字符”存成固定值 firstLine
    （如 480twips = 12pt 字号的 2 字符）而不写 firstLineChars；
    先按文档字号换算回字符数，再随渲染字号等比缩放，
    否则缩进固定为 96dpi 像素，渲染字号大时明显偏小。
    """
    value = para.paragraph_format.first_line_indent
    if not value:
        style = para.style
        while style is not None:
            value = style.paragraph_format.first_line_indent
            if value:
                break
            style = style.base_style
    if not value:
        return None
    pt = value / _EMU_PER_PT  # EMU -> pt
    return pt / _font_size_pt(para, doc)


def _resolve_align(para) -> str:
    if para.alignment == WD_ALIGN_PARAGRAPH.CENTER:
        return "center"
    if para.alignment == WD_ALIGN_PARAGRAPH.RIGHT:
        return "right"
    return "left"


def _run_highlight(run) -> str | None:
    """读取 run 的高亮背景色 w:highlight @w:val（yellow/green/cyan...），或 w:shd @w:fill。

    优先 highlight，其次 shd（Word 底纹）。返回小写色名或 hex（shd），无则 None。
    """
    rPr = run._element.rPr
    if rPr is None:
        return None
    hl = rPr.find(qn("w:highlight"))
    if hl is not None:
        val = hl.get(qn("w:val"))
        if val and val != "none":
            return val.strip().lower()
    shd = rPr.find(qn("w:shd"))
    if shd is not None:
        fill = shd.get(qn("w:fill"))
        if fill and fill.lower() not in ("auto", "ffffff", "fffffff"):
            # shd fill 为 RRGGBB hex，直接返回 hex 以便动态映射
            return fill.strip().lower()
    return None


def _run_color(run) -> str | None:
    """读取 run 的文本颜色 w:color @w:val，返回 #RRGGBB 或 None（auto）。"""
    rPr = run._element.rPr
    if rPr is None:
        return None
    el = rPr.find(qn("w:color"))
    if el is None:
        return None
    val = el.get(qn("w:val"))
    if not val or val.lower() == "auto":
        return None
    v = val.strip().lstrip("#")
    if len(v) == 6 and all(c in "0123456789abcdefABCDEF" for c in v):
        return f"#{v.lower()}"
    return None


def _run_font_family(run, para=None) -> str | None:
    """读取 run 的字体 w:rFonts（优先 eastAsia > ascii > hAnsi），返回家族名或 None。

    若 run 无直接指定，沿段落样式链回退；兼容同一 rPr 内多个 w:rFonts（Word 主题 + 自定义）。
    """
    def _from_rpr(rPr) -> str | None:
        if rPr is None:
            return None
        # 可能存在多个 w:rFonts（主题 + 覆盖），取最后一个有效
        found = None
        for el in rPr.findall(qn("w:rFonts")):
            for attr in ("w:eastAsia", "w:ascii", "w:hAnsi", "w:cs"):
                val = el.get(qn(attr))
                if val and val.strip() and val.strip().lower() not in ("theme",):
                    # 过滤主题占位，优先 eastAsia
                    if attr == "w:eastAsia":
                        return val.strip()
                    if found is None:
                        found = val.strip()
        return found

    rPr = run._element.rPr
    fam = _from_rpr(rPr)
    if fam:
        return fam
    if para is not None:
        style = para.style
        while style is not None:
            sp = style.element.find(qn("w:rPr"))
            fam = _from_rpr(sp)
            if fam:
                return fam
            # 段落样式的 rPr 可能在 w:pPr/w:rPr 层？已覆盖 w:style/w:rPr
            style = style.base_style
    return None


def _run_bold(run, para=None, doc=None) -> bool:
    """读取 run 的加粗 w:b / w:bCs（考虑 val=false 显式关闭），沿样式链回退。"""
    def _check_b(rPr) -> bool | None:
        if rPr is None:
            return None
        for tag in ("w:b", "w:bCs"):
            el = rPr.find(qn(tag))
            if el is not None:
                val = el.get(qn("w:val"))
                if val is None:
                    return True  # <w:b/> 无 val 视为 true
                v = val.strip().lower()
                if v in ("false", "0", "off"):
                    return False
                if v in ("true", "1", "on"):
                    return True
                return True
        return None
    # run 直接
    rPr = run._element.rPr
    b = _check_b(rPr)
    if b is not None:
        return b
    # 段落样式链
    if para is not None:
        style = para.style
        while style is not None:
            sp = style.element.find(qn("w:rPr"))
            b = _check_b(sp)
            if b is not None:
                return b
            style = style.base_style
        # run.font.bold 兜底（python-docx 已解析）
        try:
            if run.bold is True:
                return True
            if run.bold is False:
                # 显式 false 也算，但可能被样式覆盖，已在上游处理
                pass
        except Exception:
            pass
    # docDefaults 一般不含加粗，默认不加粗
    return False


def _run_font_size_pt(run, para=None, doc=None) -> float | None:
    """读取 run 的字号 w:sz / w:szCs @w:val（half-pt），返回 pt 或 None。

    优先 run 直接格式，其次段落样式链，再次 docDefaults。
    """
    rPr = run._element.rPr
    if rPr is not None:
        for tag in ("w:sz", "w:szCs"):
            last = None
            for el in rPr.findall(qn(tag)):
                val = el.get(qn("w:val"))
                if val and val.strip().isdigit():
                    try:
                        last = int(val.strip()) / 2.0
                    except ValueError:
                        continue
            if last is not None:
                return last
    if para is not None:
        style = para.style
        while style is not None:
            sp = style.element.find(qn("w:rPr"))
            if sp is not None:
                for tag in ("w:sz", "w:szCs"):
                    last = None
                    for el in sp.findall(qn(tag)):
                        val = el.get(qn("w:val"))
                        if val and val.strip().isdigit():
                            try:
                                last = int(val.strip()) / 2.0
                            except ValueError:
                                continue
                    if last is not None:
                        return last
            style = style.base_style
        # 尝试 Normal 样式字体大小
        try:
            sz = style.font.size if style else None  # type: ignore
        except Exception:
            pass
    if doc is not None:
        try:
            # docDefaults
            el = doc.styles.element.find(qn("w:docDefaults"))
            if el is not None:
                el = el.find(qn("w:rPrDefault"))
                if el is not None:
                    el = el.find(qn("w:rPr"))
                    if el is not None:
                        for tag in ("w:sz", "w:szCs"):
                            sz = el.find(qn(tag))
                            if sz is not None:
                                val = sz.get(qn("w:val"))
                                if val and val.strip().isdigit():
                                    return int(val.strip()) / 2.0
        except Exception:
            pass
    return None


def _pt_to_px(pt: float) -> int:
    """Word pt（1/72英寸）转像素（96 DPI），与 Pillow 逻辑一致。"""
    return max(1, int(round(pt * 96.0 / 72.0)))


def _split_by_tags(text: str) -> list[tuple[str, str | None]]:
    """按 {{前缀:内容}} 切分文本，返回 [(片段, 标签前缀或None)]。

    标签前缀白名单外也保留，以便动态分配角色名。
    无标签时返回 [(text, None)]。
    """
    if not text or "{{" not in text:
        return [(text, None)] if text else []
    res: list[tuple[str, str | None]] = []
    last = 0
    for m in _TAG_RE.finditer(text):
        start, end = m.span()
        if start > last:
            res.append((text[last:start], None))
        key = m.group(1).strip()
        content = m.group(2)
        if content:
            res.append((content, key))
        last = end
    if last < len(text):
        res.append((text[last:], None))
    # 过滤空片段
    return [(t, k) for t, k in res if t]


# 固定标签映射（不占用动态 id 池）
_FIXED_TAG_TO_ROLE: dict[str, int] = {
    "手写": 0, "默认手写": 0, "默认": 0,
    "打印": 1, "打印体": 1,
}


def _normalize_tag_key(key: str) -> str:
    return key.strip()


def load_paragraphs(path: str | Path, font_size: int | None = None) -> list[Paragraph]:
    """读取 docx 中每个段落，返回 [Paragraph]（忽略空段落）。

    兼容旧接口：返回纯文本段落（text），不含 runs。
    首行缩进优先按字符数（firstLineChars）× font_size 换算，
    与 GUI“首行缩进”按钮（2×字体大小）的语义一致；
    无字符数时回退 EMU（直接/样式继承）。
    """
    # 复用新实现后压缩为 plain
    paras = load_paragraphs_with_runs(path, font_size=font_size)
    # 向后兼容：保留 text，供旧调用方比较 plain_text
    return paras


def load_paragraphs_with_runs(
    path: str | Path, font_size: int | None = None
) -> list[Paragraph]:
    """读取 docx 返回带 TextRun 的段落列表（支持高亮动态映射与标签）。

    动态映射规则（与用户预期一致）：
      - 文档**整体无背景高亮** → 全文视为手写（无标记段落 role 0）
      - 文档**存在任意背景高亮** → 有高亮片断为手写（按首现顺序分配 2,3…），
        其余无标记文本视为打印体（role 1），实现“背景色之处手写、其余打印”的自然排版
      - {{打印:...}} / {{打印体:...}} → role 1（打印体，最高优先级）
      - 高亮颜色首次出现 → 分配 id=2，第二次出现 → id=3 … 颜色值不限定
      - {{任意名:...}} 中任意名首次出现 → 分配下一可用 id（与高亮共享 id 池，文档顺序决定）
    同一文档内相同高亮/相同标签名前缀始终映射到同一角色。
    """
    doc = Document(str(path))
    # 预扫描：文档级是否有任意高亮（决定无标记文本的默认去向）
    has_any_highlight = False
    for para in doc.paragraphs:
        for run in para.runs:
            if run.text and _run_highlight(run) is not None:
                has_any_highlight = True
                break
        if has_any_highlight:
            break
    result: list[Paragraph] = []
    # 动态 id 池：2 开始；已分配的 highlight/tag 映射
    highlight_to_role: dict[str, int] = {}
    tag_to_role: dict[str, int] = {}
    next_role_id = 2

    def alloc_for_highlight(hl: str) -> int:
        nonlocal next_role_id
        key = hl.lower()
        if key not in highlight_to_role:
            highlight_to_role[key] = next_role_id
            next_role_id += 1
        return highlight_to_role[key]

    def alloc_for_tag(tag_key: str) -> int:
        nonlocal next_role_id
        nk = _normalize_tag_key(tag_key)
        # 固定映射优先
        if nk in _FIXED_TAG_TO_ROLE:
            return _FIXED_TAG_TO_ROLE[nk]
        # 已分配的自定义标签
        if nk in tag_to_role:
            return tag_to_role[nk]
        # “角色1/角色2” 解析：角色N -> id N+1 ？为了与动态对齐，显式 角色1→2 角色2→3
        # 兼容：纯数字标签 "1" / "2"
        low = nk.lower()
        if low.startswith("角色"):
            suffix = low[2:].strip()
            try:
                n = int(suffix)
                # 角色1 -> 2, 角色2 -> 3
                rid = n + 1
                # 确保 next_role_id 推进到超过该 rid，避免与后续动态冲突
                if rid >= next_role_id:
                    next_role_id = rid + 1
                tag_to_role[nk] = rid
                return rid
            except ValueError:
                pass
        # 任意新名字动态分配
        tag_to_role[nk] = next_role_id
        next_role_id += 1
        return tag_to_role[nk]

    for para in doc.paragraphs:
        # 跳过完全空白段落（无文本且无高亮运行）
        if not para.text.strip() and not any(r.text for r in para.runs):
            continue
        chars = _first_line_chars(para) or _first_line_emu_chars(para, doc)
        indent = int(round(chars * (font_size or 36))) if chars else 0
        align = _resolve_align(para)
        # 若段落无 runs（极少），退化为单 run，并遵循文档级策略
        if not para.runs:
            plain = para.text.strip()
            if not plain:
                continue
            fallback_role = 1 if has_any_highlight else 0
            result.append(Paragraph(text=plain, align=align, first_line_indent=indent, runs=[TextRun(text=plain, role_id=fallback_role)]))
            continue

        runs: list[TextRun] = []
        for run in para.runs:
            raw = run.text
            if not raw:
                continue
            hl = _run_highlight(run)
            col = _run_color(run)
            # 预取该 run 的字体信息（仅打印体时沿用）；回退到段落/文档样式
            fam = _run_font_family(run, para)
            pt = _run_font_size_pt(run, para, doc)
            fpx = _pt_to_px(pt) if pt is not None else None
            ffile = None
            if fam:
                try:
                    p = family_to_file(fam)
                    if p and Path(p).is_file():
                        ffile = str(p)
                except Exception:
                    ffile = None
            is_bold = _run_bold(run, para, doc)
            # 标签切分：run 文本内可能含多个 {{k:v}}
            segments = _split_by_tags(raw)
            if not segments:
                continue
            def _font_kwargs_for(role_id: int) -> dict:
                # 仅打印体（role 1 或 printed）沿用原文系统字体与加粗；手写体固定用用户手写字体，忽略原文
                if role_id == 1:
                    return dict(font_family=fam, font_size=fpx, font_file=ffile, bold=is_bold)
                return dict(font_family=None, font_size=None, font_file=None, bold=False)

            # 若无标签，直接按高亮/全局策略映射
            if len(segments) == 1 and segments[0][1] is None:
                text, _ = segments[0]
                if hl is None:
                    # 全局无高亮 → 默认手写；有高亮 → 无标记视为打印
                    role = 1 if has_any_highlight else 0
                else:
                    role = alloc_for_highlight(hl)
                runs.append(TextRun(text=text, role_id=role, color=col, **_font_kwargs_for(role)))
            else:
                # 含标签：每段按标签前缀分配，高亮仅对非标签片段生效
                for seg_text, seg_key in segments:
                    if seg_key is None:
                        # 非标签片段跟随 run 的高亮，并结合文档级策略
                        if hl is None:
                            role = 1 if has_any_highlight else 0
                        else:
                            role = alloc_for_highlight(hl)
                        runs.append(TextRun(text=seg_text, role_id=role, color=col, **_font_kwargs_for(role)))
                    else:
                        role = alloc_for_tag(seg_key)
                        # 标签内容的颜色仍跟随 run 的 w:color（若有）；字体仅在打印标签时沿用
                        runs.append(TextRun(text=seg_text, role_id=role, color=col, **_font_kwargs_for(role)))
        if not runs:
            continue
        # 合并相邻同 role+color+font+bold 的 Run，压缩列表
        merged: list[TextRun] = []
        for r in runs:
            if merged and merged[-1].role_id == r.role_id and merged[-1].color == r.color \
               and merged[-1].font_family == r.font_family and merged[-1].font_size == r.font_size \
               and merged[-1].font_file == r.font_file and merged[-1].bold == r.bold:
                merged[-1].text += r.text
            else:
                merged.append(r)
        # 纯文本回落（兼容旧段落 text 字段）
        plain = "".join(r.text for r in merged)
        if not plain.strip():
            continue
        result.append(Paragraph(text=plain, align=align, first_line_indent=indent, runs=merged))
    return result


def extract_roles_from_paragraphs(paragraphs: list[Paragraph]) -> list[HandwritingRole]:
    """从已解析的段落 runs 中推断角色表（供 GUI 初始化角色面板）。

    返回动态分配的角色列表（包含 0/1 固定角色）。
    """
    seen: dict[int, str] = {0: "默认手写", 1: "打印体"}
    for p in paragraphs:
        for r in p.runs or []:
            if r.role_id not in seen:
                if r.role_id == 0:
                    seen[r.role_id] = "默认手写"
                elif r.role_id == 1:
                    seen[r.role_id] = "打印体"
                else:
                    seen[r.role_id] = f"手写角色{r.role_id-1}"
    # 按 id 排序返回
    roles = []
    for rid in sorted(seen):
        roles.append(HandwritingRole(id=rid, name=seen[rid], printed=(rid == 1)))
    return roles
