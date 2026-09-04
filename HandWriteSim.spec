# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onefile 单文件模式，便于分发）。

打包产物为单个 HandWriteSim 可执行文件，运行时自解压到临时目录，
无需携带 _internal 等附加文件夹，单文件即可拷贝分发。
UI 资源（ui/3d.ico）一并打入，窗口图标直接嵌入可执行文件。

跨平台：通过 sys.platform 分支适配三平台差异——
- imageformats 插件文件名随平台不同（qjpeg.dll / libqjpeg.so / libqjpeg.dylib）
- 未使用 Qt DLL 的剔除清单仅 Windows 实测可安全移除，其余平台保留
- 图标仅 Windows 支持 .ico，Linux/macOS 不指定（用默认图标）

体积优化：
- excludes 排除 PyInstaller hook 全量收集的未使用模块（scipy 仅用 ndimage
  及其依赖链 special/linalg；PyQt6 仅用 QtCore/QtGui/QtWidgets）
- Qt 平台插件按需收集，imageformats 只保留 png/jpeg/ico
- Windows 下剔除未使用的 Qt6Pdf/Qt6Svg DLL 与 opengl32sw 软件渲染器
  （QWidgets 光栅渲染不需要 OpenGL）
"""
import os
import sys

import PyQt6
from PyInstaller.building.datastruct import TOC
from PyInstaller.utils.hooks import collect_submodules

_IS_WIN = sys.platform.startswith("win")
_IS_MAC = sys.platform == "darwin"

# Qt6 根目录（spec 由 PyInstaller 在项目 venv 中执行，PyQt6 已安装）
_qt_root = os.path.join(os.path.dirname(PyQt6.__file__), "Qt6")

# UI 资源 + Qt 运行时插件（排除未用 Qt 模块后 hook 不再自动收集插件，
# 必须显式加入，否则 GUI 无法启动）
# 只打包窗口图标 3d.ico；png 图标源文件仅留存仓库，不进 exe（避免多余体积）
datas = [("ui/3d.ico", "ui"), ("ui/arrow_down.svg", "ui"), ("ui/arrow_down_dark.svg", "ui")]
for _sub in (
    "plugins/platforms",   # 窗口平台插件（qwindows/libqxcb/libqcocoa）
    "plugins/styles",      # 界面样式
    "plugins/generic",     # 触摸支持
    "plugins/tls",         # 网络 TLS 后端
):
    _src = os.path.join(_qt_root, _sub)
    if os.path.isdir(_src):
        datas.append((_src, os.path.join("PyQt6", "Qt6", _sub)))

# imageformats 按需收集，文件名随平台不同：
# Windows: qjpeg.dll / qico.dll；Linux: libqjpeg.so；macOS: libqjpeg.dylib。
# PNG 为 Qt 内置格式无需插件；qico 仅 Windows 需要（ico 窗口图标）；
# qwebp 用于 webp 背景（内置背景素材已统一为 webp 格式）。
_keep_images = {"qjpeg", "qwebp"}
if _IS_WIN:
    _keep_images.add("qico")


def _img_stem(name: str) -> str:
    """qjpeg.dll / libqjpeg.so / libqjpeg.dylib -> qjpeg。"""
    base = name.split(".")[0]
    return base[3:] if base.startswith("lib") else base


_img_dir = os.path.join(_qt_root, "plugins", "imageformats")
if os.path.isdir(_img_dir):
    for _name in os.listdir(_img_dir):
        if _img_stem(_name) in _keep_images:
            datas.append(
                (
                    os.path.join(_img_dir, _name),
                    os.path.join("PyQt6", "Qt6", "plugins", "imageformats"),
                )
            )

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
    "PIL._avif",  # AVIF 格式支持，项目不需要
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

# 从收集结果中剔除未使用的 Qt DLL（Qt6Gui/Qt6Widgets 不依赖它们）：
# - Qt6Pdf（PDF 模块）、Qt6Svg（SVG 模块）
# - opengl32sw（Qt 软件 OpenGL 渲染器，QWidgets 光栅渲染不需要）
# - Qt6Network（Qt 网络模块；本应用网络走 Python 标库 urllib，不走 QtNetwork）
# 注意：仅 Windows 实测可安全移除（文件名固定为 *.dll）；Linux/macOS 的
# 动态链接关系未经实测，保留原文件避免启动失败。
# 警告：绝不能剔除 libssl-3-x64.dll / libcrypto-3-x64.dll——它们是 CPython
# _ssl.pyd 的依赖，检查更新/下载走 https（urllib）必须有它们。v0.4.0/v0.4.1
# 曾误删导致打包版“无法连接至 GitHub Releases API”，见 tests/test_build_spec.py。
if _IS_WIN:
    a.binaries = TOC(
        (name, path, typecode)
        for name, path, typecode in a.binaries
        if os.path.basename(name)
        not in ("Qt6Pdf.dll", "Qt6Svg.dll", "opengl32sw.dll", "Qt6Network.dll")
    )

# hook 默认全量收集 Qt 插件（imageformats 十余个、iconengines 等），
# 按需过滤：iconengines 全删（依赖已移除的 Qt6Svg），
# imageformats 只留 jpeg（Windows 另加 ico；PNG 为 Qt 内置格式无需插件）
def _keep_qt_plugins(name: str) -> bool:
    """按需保留 Qt 插件，返回 False 表示剔除。"""
    if "plugins" not in name:
        return True
    if "iconengines" in name:
        return False
    if "imageformats" in name:
        return _img_stem(os.path.basename(name)) in _keep_images
    return True


a.datas = TOC(
    (name, path, typecode)
    for name, path, typecode in a.datas
    if _keep_qt_plugins(name)
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
    # UPX 压缩会破坏 macOS 签名，非 macOS 平台可用（未安装 upx 时自动跳过）
    upx=not _IS_MAC,
    # UPX 5.x 压不了新工具链的 CFG 保护 DLL（如 CPython 3.14 的 python3.dll，
    # 报 NotCompressibleException），显式排除以免构建日志刷 traceback；
    # 被排除的文件仅以原样打入，体积影响约数 MB
    upx_exclude=["python3*.dll", "api-ms-win-*.dll"],
    console=False,
    disable_windowed_traceback=False,
    # 图标仅 Windows 支持 .ico；Linux/macOS 不指定（PyInstaller 默认图标）
    icon="ui/3d.ico" if _IS_WIN else None,
)