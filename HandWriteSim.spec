# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onefile 单文件模式，便于分发）。

打包产物为单个 HandWriteSim.exe，运行时自解压到临时目录，
无需携带 _internal 等附加文件夹，单文件即可拷贝分发。
UI 资源（ui/3d.ico）一并打入，窗口图标直接嵌入 exe。
"""
from PyInstaller.utils.hooks import collect_submodules

datas = [("ui", "ui")]

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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="HandWriteSim",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon="ui/3d.ico",
)