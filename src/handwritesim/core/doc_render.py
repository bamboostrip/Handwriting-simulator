"""把 PDF / DOCX 渲染成「打印预览」图片，用作手写底图。

PDF 用 pypdfium2 直接栅格化（Apache-2.0 授权，纯 wheel 无系统依赖）；
DOCX 的忠实排版需要本机排版引擎：优先借助 Microsoft Word（COM 自动化，
仅 Windows），其次 LibreOffice（soffice --headless），转成 PDF 后
再走同一条栅格化链路。都没有时给出明确的安装提示。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def pdf_to_images(
    pdf_path: str | Path, out_dir: str | Path, dpi: int = 200, prefix: str = "page"
) -> list[Path]:
    """把 PDF 逐页栅格化为 PNG，返回按页序排列的文件路径列表。"""
    import pypdfium2 as pdfium

    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        paths: list[Path] = []
        scale = dpi / 72.0
        for index, page in enumerate(doc):
            image = page.render(scale=scale).to_pil().convert("RGB")
            path = out_dir / f"{prefix}_{index}.png"
            image.save(path)
            paths.append(path)
    finally:
        doc.close()
    if not paths:
        raise RuntimeError(f"PDF 没有可渲染的页面：{pdf_path}")
    return paths


def docx_to_pdf(docx_path: str | Path, out_dir: str | Path) -> Path:
    """把 DOCX 转成 PDF（Word COM 优先，LibreOffice 兜底）。"""
    docx_path = Path(docx_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / (docx_path.stem + ".pdf")

    if sys.platform == "win32":
        script = _word_com_script(docx_path, pdf_path)
        try:
            run = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if run.returncode == 0 and pdf_path.exists():
                return pdf_path
        except (OSError, subprocess.TimeoutExpired):
            pass  # Word 未安装或转换失败，继续尝试 LibreOffice

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        try:
            subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf",
                 "--outdir", str(out_dir), str(docx_path)],
                capture_output=True, text=True, timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        if pdf_path.exists():
            return pdf_path

    raise RuntimeError(
        "无法把 DOCX 转成打印预览：需要本机安装 Microsoft Word 或 LibreOffice。\n"
        "也可以先在 Word 里把文档另存为 PDF，再直接导入 PDF。"
    )


def _word_com_script(docx_path: Path, pdf_path: Path) -> str:
    """生成调用 Word COM 另存为 PDF 的 PowerShell 脚本（17 = wdFormatPDF）。"""
    src = str(docx_path).replace("'", "''")
    dst = str(pdf_path).replace("'", "''")
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "$word = New-Object -ComObject Word.Application\n"
        "$word.Visible = $false\n"
        "try {\n"
        f"  $doc = $word.Documents.Open('{src}', $false, $true)\n"
        f"  $doc.SaveAs([ref]'{dst}', [ref]17)\n"
        "  $doc.Close($false)\n"
        "} finally {\n"
        "  $word.Quit()\n"
        "}\n"
    )


def document_to_page_images(
    path: str | Path, out_dir: str | Path, dpi: int = 200
) -> list[Path]:
    """入口：PDF 直接渲染；DOCX 先转 PDF。返回逐页 PNG 路径（页序即列表序）。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return pdf_to_images(path, out_dir, dpi=dpi, prefix=path.stem)
    if suffix == ".docx":
        pdf_path = docx_to_pdf(path, out_dir)
        return pdf_to_images(pdf_path, out_dir, dpi=dpi, prefix=path.stem)
    raise ValueError(f"不支持的文档类型：{path.suffix}（支持 .pdf / .docx）")
