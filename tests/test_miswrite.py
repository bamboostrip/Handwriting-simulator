"""错字划掉重写（错字率驱动）测试。

对齐 Rust 版 layout.rs 的测试语义：rate=0 零回归、同 seed 逐像素稳定、
错字增加墨迹、Rewrite 模式更宽、Above 模式上方小字、段落路径行位置不变。
"""

from __future__ import annotations

import os
import random
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from handwritesim.core.engine import HandwritingEngine
from handwritesim.core.engine_fast import _layout_paragraph, _wrong_char
from handwritesim.core.models import HandwritingParams, Paragraph

_FONTS = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
)


def _font() -> str:
    for font in _FONTS:
        if os.path.exists(font):
            return font
    pytest.skip("未找到系统 CJK 字体")


def _params(tmp_path: Path, text: str, size: tuple[int, int] = (400, 300)) -> HandwritingParams:
    bg = tmp_path / "bg.png"
    Image.new("RGB", size, "white").save(bg)
    params = HandwritingParams(
        text=text,
        font_path=_font(),
        background_path=str(bg),
        font_size=40,
        line_spacing=55,
        word_spacing=6,
        perturb_theta_sigma=0.05,
    )
    # 与 Rust 测试一致：关闭排版扰动，聚焦错字逻辑
    params.word_spacing_sigma = 0
    params.font_size_sigma = 0
    params.line_spacing_sigma = 0
    return params


def _ink(image: Image.Image) -> int:
    return int(np.count_nonzero(np.asarray(image.convert("L")) < 128))


def test_miswrite_above_adds_ink_and_is_deterministic(tmp_path: Path) -> None:
    """错字率>0（Above）应增加墨迹；同 seed 两次渲染逐像素一致。"""
    text = "今天天气很好，我们去公园散步。"
    params = _params(tmp_path, text)
    params.miswrite_rate = 0.5
    params.miswrite_rewrite_mode = "above"
    a = HandwritingEngine(backend="fast", seed=7).render_preview(params)
    b = HandwritingEngine(backend="fast", seed=7).render_preview(params)
    assert np.array_equal(np.asarray(a), np.asarray(b)), "同 seed 应逐像素一致"

    zero = _params(tmp_path, text)
    zero.miswrite_rate = 0.0
    base = HandwritingEngine(backend="fast", seed=7).render_preview(zero)
    assert _ink(a) > _ink(base), "错字效果应增加前景像素"


def test_miswrite_rate_zero_consumes_no_rng(tmp_path: Path) -> None:
    """rate=0 时 mode/style 不影响渲染：不消耗额外 RNG（零回归）。"""
    a = _params(tmp_path, "零回归测试文本。")
    a.miswrite_rate = 0.0
    a.miswrite_rewrite_mode = "above"
    b = _params(tmp_path, "零回归测试文本。")
    b.miswrite_rate = 0.0
    b.miswrite_rewrite_mode = "rewrite"
    b.miswrite_strikeout_style = "cross"
    ia = HandwritingEngine(backend="fast", seed=3).render_preview(a)
    ib = HandwritingEngine(backend="fast", seed=3).render_preview(b)
    assert np.array_equal(np.asarray(ia), np.asarray(ib))


def test_miswrite_rewrite_mode_draws_extra_glyph(tmp_path: Path) -> None:
    """Rewrite 模式：重写字符画在错字右侧，墨迹明显更宽；不画上方小字。"""
    params = _params(tmp_path, "甲乙丙", size=(600, 400))
    params.miswrite_rate = 1.0
    params.miswrite_rewrite_mode = "rewrite"
    image = HandwritingEngine(backend="fast", seed=7).render_preview(params)
    mask = np.asarray(image.convert("L")) < 128
    rows = np.where(mask.any(axis=1))[0]
    # 首行行顶之上不应有墨迹（只有删除线+内联重写）
    first_top = int(params.top_margin) + int(params.line_spacing)
    assert rows[0] >= first_top, f"Rewrite 不应在行顶上方画小字：首个墨迹行 {rows[0]}"

    base = _params(tmp_path, "甲乙丙", size=(600, 400))
    base.miswrite_rate = 0.0
    base_image = HandwritingEngine(backend="fast", seed=7).render_preview(base)
    base_mask = np.asarray(base_image.convert("L")) < 128
    last_x = int(np.where(mask.any(axis=0))[0].max())
    last_x0 = int(np.where(base_mask.any(axis=0))[0].max())
    assert last_x > last_x0 + 30, f"Rewrite 应把最右墨迹推到更远处：{last_x} vs {last_x0}"


def test_miswrite_above_draws_small_char_above(tmp_path: Path) -> None:
    """Above 模式：错字正上方出现小一号重写墨迹。"""
    params = _params(tmp_path, "上面有小字测试", size=(600, 400))
    params.miswrite_rate = 1.0
    params.miswrite_rewrite_mode = "above"
    image = HandwritingEngine(backend="fast", seed=7).render_preview(params)
    mask = np.asarray(image.convert("L")) < 128
    rows = np.where(mask.any(axis=1))[0]
    first_top = int(params.top_margin) + int(params.line_spacing)
    assert rows[0] < first_top, "Above 模式应在行顶上方画出小字"


def test_miswrite_above_non_ascii_font_path(tmp_path: Path) -> None:
    """非 ASCII 字体路径（Windows 常见）不应报 cannot open resource。

    回归防护：PIL 对非 ASCII 路径字体以字节加载，font_variant 变体的
    .path 是已消费的 BytesIO；笔画测量不能按路径重开字体文件。
    """
    import shutil

    font_src = _font()
    ch_dir = tmp_path / "字体"
    ch_dir.mkdir()
    font_dst = ch_dir / "微软雅黑字体.bin"
    shutil.copy(font_src, font_dst)

    params = _params(tmp_path, "中好工一", size=(600, 400))
    params.font_path = str(font_dst)
    params.miswrite_rate = 1.0
    params.miswrite_rewrite_mode = "above"
    image = HandwritingEngine(backend="fast", seed=7).render_preview(params)
    assert np.count_nonzero(np.asarray(image.convert("L")) < 128) > 0


def test_above_small_char_same_pen_stroke_width(tmp_path: Path) -> None:
    """Above 模式小字按同一支笔加粗：笔画粗细与原字基本一致，不再明显偏细。

    回归防护：之前小字直接用 0.6 倍字号渲染，笔画随字形等比变细
    （同一支笔不可能写出更细的笔迹）；加粗后应逼近原字号笔画宽度。
    笔画宽度用每列竖直连续墨迹长度的中位数估计。
    """
    from PIL import Image, ImageDraw, ImageFont

    from handwritesim.core.engine_fast import _paste_thickened_char

    font = ImageFont.truetype(_font(), size=40)
    small = font.font_variant(size=24)
    canvas = Image.new("1", (300, 150), 0)
    draw = ImageDraw.Draw(canvas)
    draw.text((60, 25), "工", fill=1, font=font)
    _paste_thickened_char(draw, 60, 80, "工", small, font)
    mask = np.asarray(canvas, dtype=bool)

    # 按行间隙把主字与小字墨迹分成上下两带
    rows = np.where(mask.any(axis=1))[0]
    gap = max(range(len(rows) - 1), key=lambda i: rows[i + 1] - rows[i])
    top = mask[rows[0] : rows[gap] + 1]
    bottom = mask[rows[gap + 1] : rows[-1] + 1]

    def stroke(band: np.ndarray) -> float:
        runs = []
        for c in range(band.shape[1]):
            run = 0
            for v in band[:, c]:
                run = run + 1 if v else 0
                if not v and run:
                    runs.append(run)
                    run = 0
            if run:
                runs.append(run)
        return float(np.median(runs)) if runs else 0.0

    main_w, small_w = stroke(top), stroke(bottom)
    # 加粗后小字应显著粗于原始 0.6 倍字形（"工" 24px 原笔迹约 2px）
    assert small_w > 3.0, f"小字应加粗到与原字同笔，实测 {small_w:.1f}px"
    assert abs(small_w - main_w) < 1.5, f"小字笔画({small_w:.1f}px)应与原字({main_w:.1f}px)一致"


def test_miswrite_styles_all_add_ink(tmp_path: Path) -> None:
    """四种涂改样式（单横线/双横线/斜线/叉号）都应渲染出删除线墨迹。"""
    text = "样式测试文本"
    base = _params(tmp_path, text)
    base.miswrite_rate = 0.0
    baseline = _ink(HandwritingEngine(backend="fast", seed=9).render_preview(base))
    for style in ("line", "double_line", "slash", "cross"):
        params = _params(tmp_path, text)
        params.miswrite_rate = 1.0
        params.miswrite_rewrite_mode = "rewrite"
        params.miswrite_strikeout_style = style
        image = HandwritingEngine(backend="fast", seed=9).render_preview(params)
        assert _ink(image) > baseline, f"样式 {style} 应增加删除线墨迹"


def test_wrong_char_never_same() -> None:
    """错字应始终与原字符不同；非字母数字汉字之外的字符原样保留。"""
    rand = random.Random(42)
    for ch in ("中", "a", "A", "7"):
        assert _wrong_char(ch, rand) != ch, f"{ch!r} 的错字不应等于自身"
    assert _wrong_char("，", rand) == "，"


def test_miswrite_paragraph_adds_ink_and_deterministic(tmp_path: Path) -> None:
    """段落路径：错字率>0 增加前景；同 seed 两次渲染逐像素一致。"""
    text = "思想汇报\n敬爱的党组织：\n时光荏苒，第四季度工作与学习已近尾声。"
    params = _params(tmp_path, text)
    params.paragraphs = [Paragraph(line) for line in text.split("\n")]
    params.miswrite_rate = 0.8
    params.miswrite_rewrite_mode = "above"
    a = HandwritingEngine(backend="fast", seed=5).render_preview(params)
    b = HandwritingEngine(backend="fast", seed=5).render_preview(params)
    assert np.array_equal(np.asarray(a), np.asarray(b)), "段落路径同 seed 应逐像素一致"

    zero = _params(tmp_path, text)
    zero.paragraphs = [Paragraph(line) for line in text.split("\n")]
    zero.miswrite_rate = 0.0
    base = HandwritingEngine(backend="fast", seed=5).render_preview(zero)
    assert _ink(a) > _ink(base), "段落错字效果应增加前景像素"


def test_miswrite_paragraph_keeps_line_position(tmp_path: Path) -> None:
    """段落路径：Above 小字带悬浮于行顶上方且与主行带合并，主行不漂移、墨迹不丢。

    回归防护：小字带若未与主行带合并，会占用下一行的行槽，主行墨迹被丢弃。
    """
    text = "今天天气很好。"

    def band_info(rate: float) -> tuple[float, np.ndarray]:
        p = _params(tmp_path, text)
        p.paragraphs = [Paragraph(text)]
        p.miswrite_rate = rate
        p.miswrite_rewrite_mode = "above"
        p.perturb_x_sigma = 0
        p.perturb_y_sigma = 0
        p.perturb_theta_sigma = 0
        band, off = _layout_paragraph(p, random.Random(9), p.paragraphs[0], 400)[0]
        assert band is not None
        return off, np.where(np.any(band, axis=1))[0]

    off, rows = band_info(0.8)
    base_off, base_rows = band_info(0.0)
    # 小字带顶应在网格顶或之上（off = s0 - yk）
    assert off <= 0.0, f"小字带顶应在网格顶或之上：off={off}"
    # 主行墨迹未丢：带内墨迹高度 ≥ 字形高度（仅小字带则 < 40）
    assert rows.max() - rows.min() >= 40, "主行墨迹应与小字带合并保留"
    # 主行墨迹下缘仍在网格行槽内（容差 = 错字字形下伸差异）
    bottom_m = rows.max() + off
    bottom_z = base_rows.max() + base_off
    assert abs(bottom_m - bottom_z) < 3, f"主行底部漂移：{bottom_m} vs {bottom_z}"


def test_miswrite_paragraph_rewrite_not_covered(tmp_path: Path) -> None:
    """段落 Rewrite 模式：重写字符紧邻错字、不被下一字符覆盖，最右墨迹更远。"""
    params = _params(tmp_path, "甲乙丙", size=(600, 400))
    params.paragraphs = [Paragraph("甲乙丙")]
    params.miswrite_rate = 1.0
    params.miswrite_rewrite_mode = "rewrite"
    image = HandwritingEngine(backend="fast", seed=7).render_preview(params)
    mask = np.asarray(image.convert("L")) < 128
    last_x = int(np.where(mask.any(axis=0))[0].max())

    base = _params(tmp_path, "甲乙丙", size=(600, 400))
    base.paragraphs = [Paragraph("甲乙丙")]
    base.miswrite_rate = 0.0
    base_image = HandwritingEngine(backend="fast", seed=7).render_preview(base)
    base_mask = np.asarray(base_image.convert("L")) < 128
    last_x0 = int(np.where(base_mask.any(axis=0))[0].max())
    assert last_x > last_x0 + 30, f"段落 Rewrite 应把最右墨迹推到更远处：{last_x} vs {last_x0}"


def test_miswrite_paragraph_center_still_centered(tmp_path: Path) -> None:
    """居中段落 + 错字：重写墨迹（含小字带）后整行仍大致居中。"""
    params = _params(tmp_path, "标题")
    params.paragraphs = [Paragraph("标题", align="center")]
    params.miswrite_rate = 1.0
    params.miswrite_rewrite_mode = "above"
    image = HandwritingEngine(backend="fast", seed=5).render_preview(params)
    mask = np.asarray(image.convert("L")) < 128
    cols = np.where(mask.any(axis=0))[0]
    assert cols.size > 0
    center = (cols.min() + cols.max()) / 2.0
    assert abs(center - 200) < 60, f"居中行漂移过大：{center}"


def test_same_seed_miswrite_preview_matches_export(tmp_path: Path) -> None:
    """相同 seed 下，错字开启的预览与导出应逐像素一致（GUI 场景）。"""
    params = _params(tmp_path, "预览与导出一致性测试。" * 40)
    params.miswrite_rate = 0.5
    params.miswrite_rewrite_mode = "above"
    preview_pages = list(HandwritingEngine(backend="fast", seed=2024).generate(params))
    out = tmp_path / "export"
    files = HandwritingEngine(backend="fast", seed=2024).save_all(params, out)
    export_pages = [Image.open(f) for f in files]
    assert len(export_pages) == len(preview_pages) >= 2
    for a, b in zip(preview_pages, export_pages):
        assert np.array_equal(np.asarray(a.convert("L")), np.asarray(b.convert("L")))
