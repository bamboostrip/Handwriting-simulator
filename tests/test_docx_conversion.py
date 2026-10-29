"""docx → PDF 转换链路：LibreOffice 探测、无头转换参数与失败提示。

回归背景：Windows 版 LibreOffice 安装时**不写 PATH**，早期实现只做
``shutil.which("soffice")``，导致「已装 LibreOffice 仍提示需要安装」。
本文件锁定 PATH → 常见安装目录 → 注册表 的三级探测，以及失败提示的可执行性。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from handwritesim.core import doc_render


def _fake_soffice(tmp_path: Path) -> Path:
    """造一个假的 soffice 可执行文件（只用于路径探测断言）。"""
    exe = tmp_path / ("soffice.exe" if sys.platform == "win32" else "soffice")
    exe.write_bytes(b"stub")
    return exe


def test_find_soffice_prefers_path(monkeypatch, tmp_path: Path) -> None:
    """PATH 命中时直接返回（不落到安装目录探测）。"""
    exe = _fake_soffice(tmp_path)
    monkeypatch.setattr(
        doc_render.shutil, "which", lambda name: str(exe) if name == "soffice" else None
    )
    monkeypatch.setattr(
        doc_render, "_soffice_candidates", lambda: pytest.fail("PATH 命中后不应再探测安装目录")
    )
    assert doc_render.find_soffice() == exe


def test_find_soffice_falls_back_to_install_dir(monkeypatch, tmp_path: Path) -> None:
    """PATH 无 soffice 时回退到安装目录（Windows 版 LibreOffice 的真实情况）。"""
    exe = _fake_soffice(tmp_path)
    monkeypatch.setattr(doc_render.shutil, "which", lambda name: None)
    monkeypatch.setattr(doc_render, "_soffice_candidates", lambda: [tmp_path / "nope.exe", exe])
    monkeypatch.setattr(
        doc_render, "_soffice_from_registry", lambda: pytest.fail("安装目录命中后不应再查注册表")
    )
    assert doc_render.find_soffice() == exe


def test_find_soffice_falls_back_to_registry(monkeypatch, tmp_path: Path) -> None:
    """安装目录也不存在时，最后尝试注册表记录的安装路径。"""
    exe = _fake_soffice(tmp_path)
    monkeypatch.setattr(doc_render.shutil, "which", lambda name: None)
    monkeypatch.setattr(doc_render, "_soffice_candidates", lambda: [])
    monkeypatch.setattr(doc_render, "_soffice_from_registry", lambda: exe)
    assert doc_render.find_soffice() == exe


def test_find_soffice_returns_none_when_absent(monkeypatch) -> None:
    """三处都找不到时返回 None（调用方据此给出安装提示）。"""
    monkeypatch.setattr(doc_render.shutil, "which", lambda name: None)
    monkeypatch.setattr(doc_render, "_soffice_candidates", lambda: [])
    monkeypatch.setattr(doc_render, "_soffice_from_registry", lambda: None)
    assert doc_render.find_soffice() is None


def test_candidates_include_windows_default_install_dir() -> None:
    """候选列表必须覆盖 LibreOffice 在 Windows 的默认安装目录。"""
    paths = [str(p).lower() for p in doc_render._soffice_candidates()]
    if sys.platform == "win32":
        assert any("program files\\libreoffice\\program\\soffice.exe" in p for p in paths)
    else:
        assert paths, "非 Windows 也应给出发行版常见路径"


def test_run_soffice_uses_isolated_profile_and_headless(monkeypatch, tmp_path: Path) -> None:
    """无头转换必须带独立 UserInstallation：用户开着 LibreOffice 时默认配置目录被锁，
    headless 转换会挂起或转交给 GUI 实例（可能弹窗）。"""
    exe = _fake_soffice(tmp_path)
    docx = tmp_path / "a.docx"
    docx.write_bytes(b"stub")
    captured: dict[str, list[str]] = {}

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN003 - 与 subprocess.run 同形
        captured["cmd"] = [str(c) for c in cmd]
        captured["kwargs"] = kwargs
        return None

    monkeypatch.setattr(doc_render.subprocess, "run", fake_run)
    doc_render._run_soffice(exe, docx, tmp_path)

    cmd = captured["cmd"]
    assert cmd[0] == str(exe)
    assert any(arg.startswith("-env:UserInstallation=file://") for arg in cmd)
    assert "--headless" in cmd
    assert "--convert-to" in cmd and cmd[cmd.index("--convert-to") + 1] == "pdf"
    assert "--outdir" in cmd
    assert cmd[-1] == str(docx)
    assert docx.parent == tmp_path


def test_docx_to_pdf_error_lists_checked_locations(monkeypatch, tmp_path: Path) -> None:
    """都找不到转换器时的报错要可执行：列出已检查位置 + 三条解决办法。"""
    monkeypatch.setattr(doc_render.sys, "platform", "linux")  # 跳过 Word COM 分支
    monkeypatch.setattr(doc_render.shutil, "which", lambda name: None)
    monkeypatch.setattr(doc_render, "_soffice_candidates", lambda: [Path("/usr/bin/soffice")])
    monkeypatch.setattr(doc_render, "_soffice_from_registry", lambda: None)

    docx = tmp_path / "a.docx"
    docx.write_bytes(b"stub")
    with pytest.raises(RuntimeError) as excinfo:
        doc_render.docx_to_pdf(docx, tmp_path)

    message = str(excinfo.value)
    assert "未找到可用的 Microsoft Word 或 LibreOffice" in message
    # 已检查位置要列出来（Windows 下 Path 会显示成反斜杠形式，按平台取字符串）
    assert "已检查" in message and str(Path("/usr/bin/soffice")) in message
    assert "另存为 PDF" in message
    assert "PATH" in message


def test_docx_to_pdf_falls_back_to_libreoffice(monkeypatch, tmp_path: Path) -> None:
    """Word 不可用时走 LibreOffice，并按 PDF 产物判定成功。"""
    monkeypatch.setattr(doc_render.sys, "platform", "linux")
    exe = _fake_soffice(tmp_path)
    monkeypatch.setattr(doc_render, "find_soffice", lambda: exe)

    docx = tmp_path / "报告.docx"
    docx.write_bytes(b"stub")
    pdf_expected = tmp_path / "报告.pdf"

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN003
        pdf_expected.write_bytes(b"%PDF-1.4 stub")
        return None

    monkeypatch.setattr(doc_render.subprocess, "run", fake_run)
    assert doc_render.docx_to_pdf(docx, tmp_path) == pdf_expected


def test_docx_to_pdf_reports_converter_failure_distinctly(monkeypatch, tmp_path: Path) -> None:
    """LibreOffice 找到了但没产出 PDF（崩溃/超时/被拦截）时，报错要区别于「未安装」。"""
    monkeypatch.setattr(doc_render.sys, "platform", "linux")
    exe = _fake_soffice(tmp_path)
    monkeypatch.setattr(doc_render, "find_soffice", lambda: exe)
    monkeypatch.setattr(doc_render.subprocess, "run", lambda cmd, **kw: None)

    docx = tmp_path / "a.docx"
    docx.write_bytes(b"stub")
    with pytest.raises(RuntimeError) as excinfo:
        doc_render.docx_to_pdf(docx, tmp_path)

    message = str(excinfo.value)
    assert "已找到 LibreOffice，但 DOCX 转 PDF 失败" in message
    assert str(exe) in message
    assert "未找到可用的" not in message, "已找到转换器时不得再报「未安装」"
    assert "另存为 PDF" in message
