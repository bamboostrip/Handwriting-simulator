# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir 模式，便于分发）。

UI 资源（ui/3d.ico）与输出目录一并打包，保证 exe 运行时可加载窗口图标。
界面文字与背景已全部由 Qt 控件绘制，不再依赖背景图片。
"""
from PyInstaller.utils.hooks import collect_submodules

datas = [("ui", "ui"), ("output", "output")]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("handwritesim"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HandWriteSim",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon="ui/3d.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="HandWriteSim",
)