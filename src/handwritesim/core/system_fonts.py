r"""系统字体枚举（跨平台）。

Windows 优先读取注册表 HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts
以获得“显示名 -> 文件名”映射，其余平台扫描约定字体目录并通过 QFontDatabase 补充。
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import List, Tuple

def _windows_fonts() -> List[Tuple[str, Path]]:
    result: List[Tuple[str, Path]] = []
    fonts_dir = Path(r"C:\Windows\Fonts")
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
        i = 0
        while True:
            try:
                name, filename, _ = winreg.EnumValue(key, i)
                i += 1
                # filename 可能是 "arial.ttf" 或 "C:\...\custom.ttf"
                p = Path(filename)
                if not p.is_absolute():
                    p = fonts_dir / filename
                # 仅保留存在的字体文件，且后缀为常见字体
                if p.suffix.lower() not in (".ttf", ".ttc", ".otf", ".fon"):
                    continue
                if p.is_file():
                    result.append((name, p))
                else:
                    # 对 .ttc 有的条目包含 "&" 合并显示，仍保留
                    if p.exists():
                        result.append((name, p))
            except OSError:
                break
    except Exception:
        # 回退：直接扫描目录
        if fonts_dir.is_dir():
            for p in fonts_dir.glob("*.*"):
                if p.suffix.lower() in (".ttf", ".ttc", ".otf"):
                    result.append((p.stem, p))
    # 去重：同一文件可能对应多个显示名，保留首个
    seen: dict[Path, str] = {}
    dedup: List[Tuple[str, Path]] = []
    for name, p in result:
        if p not in seen:
            seen[p] = name
            dedup.append((name, p))
    # 补充中文别名（便于用户搜“宋体”）：将常见英文字体显示改为中文+英文
    cn_for_file = {
        "simsun.ttc": "宋体",
        "simsunb.ttf": "宋体-ExtB",
        "simhei.ttf": "黑体",
        "simkai.ttf": "楷体",
        "simfang.ttf": "仿宋",
        "msyh.ttc": "微软雅黑",
        "msyhbd.ttc": "微软雅黑 Bold",
        "msyhl.ttc": "微软雅黑 Light",
        "deng.ttf": "等线",
        "dengb.ttf": "等线 Bold",
        "dengl.ttf": "等线 Light",
    }
    enriched: List[Tuple[str, Path]] = []
    for name, p in dedup:
        cn = cn_for_file.get(p.name.lower())
        if cn and cn not in name:
            name = f"{cn} ({name})"
        enriched.append((name, p))
    def _sort_key(item: Tuple[str, Path]):
        name = item[0]
        is_cn = any("\u4e00" <= c <= "\u9fff" for c in name)
        return (0 if is_cn else 1, name.lower())
    enriched.sort(key=_sort_key)
    return enriched

def _unix_fonts() -> List[Tuple[str, Path]]:
    candidates: List[Path] = []
    extra_dirs = []
    sys_name = platform.system()
    if sys_name == "Darwin":
        extra_dirs = [Path("/Library/Fonts"), Path("/System/Library/Fonts"), Path.home() / "Library/Fonts"]
    else: # Linux
        extra_dirs = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts"]
        # 尝试 fc-list
        try:
            import subprocess
            out = subprocess.run(["fc-list", "--format", "%{file}\n"], capture_output=True, text=True, timeout=3)
            if out.returncode == 0:
                for line in out.stdout.splitlines():
                    p = Path(line.strip())
                    if p.is_file() and p.suffix.lower() in (".ttf", ".ttc", ".otf"):
                        candidates.append(p)
        except Exception:
            pass
    for d in extra_dirs:
        if d.is_dir():
            for p in d.rglob("*.*"):
                if p.suffix.lower() in (".ttf", ".ttc", ".otf") and p.is_file():
                    candidates.append(p)
    # 去重
    uniq: dict[Path, str] = {}
    out: List[Tuple[str, Path]] = []
    for p in candidates:
        if p not in uniq:
            uniq[p] = p.stem
            out.append((p.stem, p))
    out.sort(key=lambda x: x[0].lower())
    return out

def _qt_families() -> List[str]:
    try:
        # 需 QApplication 存在；若无则不抛异常
        from PyQt6.QtGui import QFontDatabase
        # QFontDatabase.families() 在 Qt6 为静态
        try:
            fams = QFontDatabase.families()
        except TypeError:
            db = QFontDatabase()
            fams = db.families()
        return sorted(fams)
    except Exception:
        return []

def list_system_fonts() -> List[Tuple[str, Path]]:
    """返回系统字体列表 [(显示名, 绝对路径)]，已按显示名排序。"""
    sys_name = platform.system()
    if sys_name == "Windows":
        fonts = _windows_fonts()
        if fonts:
            return fonts
    # 非 Windows 或注册表失败，回退扫描
    fonts = _unix_fonts()
    if fonts:
        return fonts
    # 最后兜底：至少返回 QFontDatabase 家族（无路径）
    fams = _qt_families()
    # 无路径时 Path 为空，回退用族名
    return [(f, Path("")) for f in fams]

def family_to_file(family: str) -> Path | None:
    """按家族名查找最匹配的字体文件（Windows 用注册表模糊匹配）。"""
    family_low = family.lower().strip()
    for name, p in list_system_fonts():
        if family_low in name.lower() or name.lower() in family_low:
            if p and p.is_file():
                return p
    # 尝试直接扫描 families 结合文件
    return None

def get_font_dirs() -> List[Path]:
    sys_name = platform.system()
    if sys_name == "Windows":
        return [Path(r"C:\Windows\Fonts")]
    if sys_name == "Darwin":
        return [Path("/Library/Fonts"), Path("/System/Library/Fonts"), Path.home() / "Library/Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts")]

# 便于 UI 下拉展示的精简样本（常用中文+英文）
COMMON_FONTS_HINT = ["宋体", "黑体", "楷体", "仿宋", "微软雅黑", "等线", "Arial", "Times New Roman", "Calibri"]
