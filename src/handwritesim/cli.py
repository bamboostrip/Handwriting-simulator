"""命令行入口：批量手写生成。

示例：
    handwrite-cli "你好世界" --font C:/Windows/Fonts/msyh.ttc --out output
    handwrite-cli "多页文本" --font f.ttf --background bg.png --font-size 40
    handwrite-cli --preset preset.json "文本" --out output
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

from .core.engine import HandwritingEngine
from .core.models import HandwritingParams
from .core import presets
from .core.docx_io import load_paragraphs


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="handwrite-cli",
        description="基于 handright 的手写体生成命令行工具",
    )
    parser.add_argument("text", nargs="?", default="", help="要处理的手写文本")
    parser.add_argument("--docx", default="", help="导入 docx 文件（解析对齐与首行缩进）")
    parser.add_argument("--font", required=True, help="字体文件路径 (.ttf/.ttc)")
    parser.add_argument("--background", default="", help="背景图片路径（默认纯白）")
    parser.add_argument("--width", type=int, default=800, help="背景为纯白时宽度")
    parser.add_argument("--height", type=int, default=1200, help="背景为纯白时高度")
    parser.add_argument("--out", default="output", help="输出目录")
    parser.add_argument("--preset", default="", help="载入预设文件 (.json/.txt)")
    parser.add_argument("--save-preset", default="", help="生成后保存当前参数为预设 (.json)")
    parser.add_argument("--preview-only", action="store_true", help="仅预览第一张并保存")

    # 排版
    parser.add_argument("--font-size", type=int, default=36)
    parser.add_argument("--word-spacing", type=int, default=5)
    parser.add_argument("--line-spacing", type=int, default=48)
    parser.add_argument("--left-margin", type=int, default=30)
    parser.add_argument("--right-margin", type=int, default=30)
    parser.add_argument("--top-margin", type=int, default=30)
    parser.add_argument("--bottom-margin", type=int, default=30)
    # 扰动
    parser.add_argument("--word-spacing-sigma", type=int, default=2)
    parser.add_argument("--line-spacing-sigma", type=int, default=2)
    parser.add_argument("--font-size-sigma", type=int, default=2)
    parser.add_argument("--perturb-x-sigma", type=int, default=2)
    parser.add_argument("--perturb-y-sigma", type=int, default=2)
    parser.add_argument("--perturb-theta-sigma", type=float, default=0.05)
    # 颜色
    parser.add_argument("--red", type=int, default=0)
    parser.add_argument("--green", type=int, default=0)
    parser.add_argument("--blue", type=int, default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # 载入预设作为基础参数
    if args.preset:
        params = presets.load(args.preset)
    else:
        params = HandwritingParams()

    # 命令行显式覆盖
    explicit = {
        "font_path": args.font,
        "text": args.text,
        "font_size": args.font_size,
        "word_spacing": args.word_spacing,
        "line_spacing": args.line_spacing,
        "left_margin": args.left_margin,
        "right_margin": args.right_margin,
        "top_margin": args.top_margin,
        "bottom_margin": args.bottom_margin,
        "word_spacing_sigma": args.word_spacing_sigma,
        "line_spacing_sigma": args.line_spacing_sigma,
        "font_size_sigma": args.font_size_sigma,
        "perturb_x_sigma": args.perturb_x_sigma,
        "perturb_y_sigma": args.perturb_y_sigma,
        "perturb_theta_sigma": args.perturb_theta_sigma,
        "red": args.red,
        "green": args.green,
        "blue": args.blue,
    }
    for key, value in explicit.items():
        setattr(params, key, value)

    # 段落化：优先 docx，其次纯文本
    if args.docx:
        params.paragraphs = load_paragraphs(args.docx)
    elif args.text:
        params.paragraphs = None

    # 背景：未指定时生成纯白背景
    if args.background:
        params.background_path = args.background
    else:
        bg = Image.new("RGB", (args.width, args.height), "white")
        bg_path = Path(args.out) / "__background__.png"
        bg_path.parent.mkdir(parents=True, exist_ok=True)
        bg.save(bg_path)
        params.background_path = str(bg_path)

    if args.save_preset:
        presets.save(args.save_preset, params)

    if not args.docx and not args.text:
        print("错误：未提供文本或 docx（可用位置参数、--docx 或 --preset 中的 text）", file=sys.stderr)
        return 2

    engine = HandwritingEngine()
    try:
        if args.preview_only:
            image = engine.render_preview(params)
            out = Path(args.out) / "preview.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            image.convert("RGB").save(out)
            print(f"预览已保存：{out}")
        else:
            files = engine.save_all(params, args.out)
            print(f"已导出 {len(files)} 张图片到：{Path(args.out).resolve()}")
            for f in files:
                print(f"  {f}")
    except HandwritingParams.ValidationError as exc:
        print(f"参数错误：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"生成失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())