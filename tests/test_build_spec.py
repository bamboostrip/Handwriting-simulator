"""打包配置回归测试：防止再次剔除 Python https 所需的 OpenSSL DLL。

根因（v0.4.0/v0.4.1）：HandWriteSim.spec 在 Windows 下把
libssl-3-x64.dll / libcrypto-3-x64.dll 当成“Qt 不用的网络库”一起剔除，
导致打包版 _ssl.pyd 缺依赖、urllib 访问 https://api.github.com 必然失败，
“检查更新”永远弹“无法连接至 GitHub Releases API”。
"""

from __future__ import annotations

from pathlib import Path


def _read_spec() -> str:
    spec_path = Path(__file__).resolve().parent.parent / "HandWriteSim.spec"
    return spec_path.read_text(encoding="utf-8")


def test_spec_must_not_exclude_python_ssl_dlls() -> None:
    """spec 不得剔除 libssl / libcrypto（Python _ssl.pyd 的依赖）。"""
    content = _read_spec()
    # 只检查代码行（去掉 # 注释）：注释里提 libssl 做警告是允许的
    code_lines = []
    for line in content.splitlines():
        stripped = line.split("#", 1)[0]
        code_lines.append(stripped)
    code = "\n".join(code_lines).lower()
    assert "libssl" not in code, (
        "HandWriteSim.spec 不得包含 libssl 剔除规则："
        "Python 标库 urllib 做 https 检查更新依赖它"
    )
    assert "libcrypto" not in code, (
        "HandWriteSim.spec 不得包含 libcrypto 剔除规则："
        "Python 标库 urllib 做 https 检查更新依赖它"
    )


def test_stdlib_ssl_can_do_https() -> None:
    """打包环境自身的 ssl 上下文必须可用（https 前置条件）。"""
    import ssl

    ctx = ssl.create_default_context()
    assert ctx is not None
    # OpenSSL 版本可打印即加载成功（缺 DLL 时 import/_ssl 即失败）
    assert "openssl" in ssl.OPENSSL_VERSION.lower() or "libressl" in ssl.OPENSSL_VERSION.lower()
