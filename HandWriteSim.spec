# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onefile 单文件模式，便于分发）。

打包产物为单个 HandWriteSim.exe，运行时自解压到临时目录，
无需携带 _internal 等附加文件夹，单文件即可拷贝分发。
UI 资源（ui/3d.ico）一并打入，窗口图标直接嵌入 exe。

体积优化：excludes 排除 PyInstaller hook 默认全量收集的未使用模块
（scipy 仅用 ndimage、PyQt6 仅用 QtCore/QtGui/QtWidgets）；
Qt 插件与 opengl32sw 软件渲染器显式收集，保证平台插件不丢失。
"""
import os

import PyQt6
from PyInstaller.utils.hooks import collect_submodules

# Qt6 根目录（spec 由 PyInstaller 在项目 venv 中执行，PyQt6 已安装）
_qt_root = os.path.join(os.path.dirname(PyQt6.__file__), "Qt6")

# UI 资源 + Qt 运行时插件（platforms/qwindows 等，排除未用 Qt 模块后
# hook 不再自动收集插件，必须显式加入，否则 GUI 无法启动）
datas = [("ui", "ui")]
for _sub in (
    "plugins/platforms",
    "plugins/styles",
    "plugins/imageformats",
    "plugins/iconengines",
    "plugins/generic",
    "plugins/tls",
):
    _src = os.path.join(_qt_root, _sub)
    if os.path.isdir(_src):
        datas.append((_src, os.path.join("PyQt6", "Qt6", _sub)))

# Qt 软件渲染器：无 GPU 环境时 Qt6Gui 依赖它做 OpenGL 回退
_sw_renderer = os.path.join(_qt_root, "bin", "opengl32sw.dll")
if os.path.isfile(_sw_renderer):
    datas.append((_sw_renderer, os.path.join("PyQt6", "Qt6", "bin")))

# 未使用模块排除清单：
# - scipy 仅使用 ndimage，排除其余子模块
#   （注意：scipy.special 是 ndimage._interpolation 的依赖，scipy.linalg
#   又是 special._ellip_harm_2 的依赖，二者必须保留）
# - PyQt6 仅使用 QtCore/QtGui/QtWidgets，排除其余 Qt 模块
#   （其 DLL 多为 CFG 保护无法 UPX 压缩，排除收益最大）
# 注意：QtNetwork/QtSvg/QtPdf 是 QtGui 依赖链的一部分，不能排除
_excludes = [
    "scipy.optimize", "scipy.stats",
    "scipy.spatial", "scipy.fft", "scipy.integrate", "scipy.interpolate",
    "scipy.signal", "scipy.sparse", "scipy.cluster", "scipy.constants",
    "scipy.io", "scipy.misc", "scipy.odr", "scipy.datasets",
    "PyQt6.QtOpenGL", "PyQt6.QtOpenGLWidgets", "PyQt6.QtPrintSupport",
    "PyQt6.QtMultimedia", "PyQt6.QtMultimediaWidgets", "PyQt6.QtSql",
    "PyQt6.QtTest", "PyQt6.QtXml", "PyQt6.QtQml", "PyQt6.QtQuick",
    "PyQt6.QtQuickWidgets", "PyQt6.QtQuick3D", "PyQt6.QtQuickControls2",
    "PyQt6.QtQuickDialogs2", "PyQt6.QtShaderTools", "PyQt6.QtDesigner",
    "PyQt6.QtCharts", "PyQt6.QtDBus", "PyQt6.QtHelp",
    "PyQt6.QtWebChannel", "PyQt6.QtWebSockets", "PyQt6.QtSensors",
    "PyQt6.QtSerialPort", "PyQt6.QtStateMachine", "PyQt6.QtTextToSpeech",
    "PyQt6.QtBluetooth", "PyQt6.QtNfc", "PyQt6.QtPositioning",
    "PyQt6.QtLocation", "PyQt6.QtRemoteObjects", "PyQt6.QtConcurrent",
    "PyQt6.QtCore5Compat",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("handwritesim"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_excludes,
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