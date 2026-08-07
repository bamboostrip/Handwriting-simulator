"""打包时压缩背景图片（jpg quality 80），源素材保持原样。

用于本地打包 zip 体积控制：背景原始 jpg 约 40MB，压缩后约 15MB，
使含字体的便携包控制在 100MB（蓝奏云上限）以内。
png 与子目录原样拷贝。
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from PIL import Image

JPEG_QUALITY = 80


def main() -> None:
    parser = argparse.ArgumentParser(description="压缩背景图片到目标目录")
    parser.add_argument("dst", help="目标目录（如 staging/backgrounds）")
    args = parser.parse_args()

    src = Path(__file__).resolve().parents[1] / "backgrounds"
    dst = Path(args.dst)
    dst.mkdir(parents=True, exist_ok=True)

    for f in sorted(src.rglob("*")):
        rel = f.relative_to(src)
        out = dst / rel
        if f.is_dir():
            out.mkdir(parents=True, exist_ok=True)
            continue
        if f.suffix.lower() in (".jpg", ".jpeg"):
            img = Image.open(f)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            out.parent.mkdir(parents=True, exist_ok=True)
            img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, out)


if __name__ == "__main__":
    main()