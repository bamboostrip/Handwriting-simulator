"""软件版本检查与便携版自动更新模块。

提供 GitHub Releases 最新版本查询、语义化版本比对、
分块下载、便携版进程安全替换重启以及更新配置管理（QSettings）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QSettings

GITHUB_OWNER = "bamboostrip"
GITHUB_REPO = "Handwriting-simulator"
GITHUB_API_LATEST_RELEASE = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)
GITHUB_REPO_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
RUST_REPO_URL = "https://github.com/bamboostrip/Handwriting-sim-rs"

_SETTINGS_ORGANIZATION = "HandwritingSimulator"
_SETTINGS_APPLICATION = "Updater"
_KEY_AUTO_CHECK = "auto_check_update"
_KEY_SKIPPED_VERSION = "skipped_version"


@dataclass
class UpdateInfo:
    """版本更新信息。"""

    version: str
    tag_name: str
    title: str
    body: str
    html_url: str
    asset_name: str = ""
    asset_url: str = ""
    asset_size: int = 0


def clean_version(v: str) -> str:
    """清理版本号字符串，去除前导 'v'、'V' 及首尾空白。"""
    v = v.strip()
    if v.startswith(("v", "V")):
        v = v[1:]
    return v


def parse_version_tuple(v: str) -> tuple[int, ...]:
    """将版本号解析为整数元组以便比较（如 '0.3.1' -> (0, 3, 1)）。"""
    cleaned = clean_version(v)
    parts = re.split(r"[^\d]+", cleaned)
    numbers = []
    for p in parts:
        if p.isdigit():
            numbers.append(int(p))
    return tuple(numbers) if numbers else (0,)


def compare_versions(v1: str, v2: str) -> int:
    """比较两个版本号。

    返回：
        -1: v1 < v2
         0: v1 == v2
         1: v1 > v2
    """
    t1 = parse_version_tuple(v1)
    t2 = parse_version_tuple(v2)

    # 补齐长度
    max_len = max(len(t1), len(t2))
    t1_padded = t1 + (0,) * (max_len - len(t1))
    t2_padded = t2 + (0,) * (max_len - len(t2))

    if t1_padded < t2_padded:
        return -1
    elif t1_padded > t2_padded:
        return 1
    return 0


def trim_release_notes_markdown(md: str) -> str:
    """从 Release 说明中只保留 `## 更新内容` 一节。

    对齐 Rust 版同名逻辑：软件内弹窗只展示更新介绍，截掉其后的
    下载说明 / 字体说明等每版重复的固定样板。无 `## ` 标题时原样返回。
    """
    lines = md.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == "## 更新内容"), None)
    if start is None:
        return md
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start:end]).strip() or md


def is_auto_check_enabled() -> bool:
    """检查是否启用了启动时自动检查更新（默认 True）。"""
    settings = QSettings(_SETTINGS_ORGANIZATION, _SETTINGS_APPLICATION)
    val = settings.value(_KEY_AUTO_CHECK, True)
    if isinstance(val, str):
        return val.lower() in ("true", "1")
    return bool(val)


def set_auto_check_enabled(enabled: bool) -> None:
    """设置是否启用启动时自动检查更新。"""
    settings = QSettings(_SETTINGS_ORGANIZATION, _SETTINGS_APPLICATION)
    settings.setValue(_KEY_AUTO_CHECK, enabled)


def get_skipped_version() -> str:
    """获取用户选择跳过的版本号。"""
    settings = QSettings(_SETTINGS_ORGANIZATION, _SETTINGS_APPLICATION)
    return str(settings.value(_KEY_SKIPPED_VERSION, "") or "")


def set_skipped_version(version: str) -> None:
    """设置用户跳过的版本号。"""
    settings = QSettings(_SETTINGS_ORGANIZATION, _SETTINGS_APPLICATION)
    settings.setValue(_KEY_SKIPPED_VERSION, version)


def check_for_updates(
    current_version: str,
    timeout: float = 5.0,
    check_all: bool = False,
    api_url: str = GITHUB_API_LATEST_RELEASE,
) -> UpdateInfo | None:
    """向 GitHub API 查询最新发布版本。

    Args:
        current_version: 当前软件版本号（如 "0.3.1"）
        timeout: 网络请求超时时间（秒）
        check_all: 是否忽略版本比较强制返回最新 Release 信息（用于关于界面查询）
        api_url: API 请求地址

    Returns:
        若有更新（或 check_all=True）且查询成功返回 UpdateInfo，否则返回 None。
    """
    try:
        req = urllib.request.Request(
            api_url,
            headers={
                "User-Agent": f"HandwritingSimulator/{current_version} (Windows; Python)",
                "Accept": "application/vnd.github.v3+json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        tag_name = data.get("tag_name", "")
        latest_version = clean_version(tag_name)
        title = data.get("name", "") or tag_name
        # 软件内只展示更新介绍，下载说明等样板在弹窗里没有意义（对齐 Rust 版裁剪）
        body = trim_release_notes_markdown(data.get("body", "") or "暂无更新说明。")
        html_url = data.get("html_url", GITHUB_REPO_URL)

        # 只匹配单文件升级包（.exe）：自动更新直接下载单文件覆盖替换，
        # 不再走便携 zip（zip 需解压组装，无法原地替换）。若 Release 中没有
        # 单文件资产则 asset_url 为空，调用方回退到浏览器手动下载。
        assets = data.get("assets", [])
        asset_name = ""
        asset_url = ""
        asset_size = 0

        for a in assets:
            name = a.get("name", "")
            if name.endswith(".exe"):
                asset_name = name
                asset_url = a.get("browser_download_url", "")
                asset_size = a.get("size", 0)
                break

        info = UpdateInfo(
            version=latest_version,
            tag_name=tag_name,
            title=title,
            body=body,
            html_url=html_url,
            asset_name=asset_name,
            asset_url=asset_url,
            asset_size=asset_size,
        )

        if check_all or compare_versions(latest_version, current_version) > 0:
            return info
        return None

    except Exception:
        return None


def download_file(
    url: str,
    dest_path: Path,
    progress_callback: Callable[[int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
    chunk_size: int = 65536,
    timeout: float = 30.0,
) -> bool:
    """从指定 URL 分块下载文件到本地，并报告进度。

    Args:
        url: 下载地址
        dest_path: 本地保存路径
        progress_callback: 进度回调 (received_bytes, total_bytes)
        cancel_event: 取消事件
        chunk_size: 分块大小
        timeout: 超时时间

    Returns:
        下载是否成功完成
    """
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        temp_dest = dest_path.with_suffix(dest_path.suffix + ".download")

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "HandwritingSimulator-Updater (Windows)",
            },
        )

        with urllib.request.urlopen(req, timeout=timeout) as response:
            total_size = int(response.headers.get("Content-Length", 0))
            received = 0

            with open(temp_dest, "wb") as f:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        temp_dest.unlink(missing_ok=True)
                        return False

                    chunk = response.read(chunk_size)
                    if not chunk:
                        break

                    f.write(chunk)
                    received += len(chunk)
                    if progress_callback:
                        progress_callback(received, total_size)

        if temp_dest.exists():
            if dest_path.exists():
                dest_path.unlink()
            temp_dest.rename(dest_path)
            return True
        return False

    except Exception:
        if "temp_dest" in locals() and temp_dest.exists():
            temp_dest.unlink(missing_ok=True)
        return False


def build_updater_bat_content(
    new_file_path: Path,
    target_exe_path: Path,
    downloaded_file_path: Path,
    sleep_vbs_path: Path,
) -> str:
    """生成 Windows 更新批处理脚本内容。

    流程：延时 1 秒等主进程退出释放 exe 占用 → 覆盖复制新版 exe →
    清理下载文件与延时脚本 → 重新拉起新版 → 自删 bat。

    全程无窗口约束：延时必须用 ``wscript //B //Nologo`` + ``.vbs``
    （windows 子系统，永不分配控制台）。严禁用 ``ping`` / ``timeout`` /
    ``choice`` 等控制台程序做延时：父进程的 CREATE_NO_WINDOW 不会继承给
    孙进程，Win11 默认终端会为每个此类子进程弹一个新终端窗口。
    其余命令均为 cmd 内部命令，不产生子进程。
    """
    return f"""@echo off
chcp 65001 >nul
wscript //B //Nologo "{sleep_vbs_path}" >nul 2>&1
copy /y "{new_file_path}" "{target_exe_path}" >nul
if exist "{downloaded_file_path}" del /f /q "{downloaded_file_path}" >nul
if exist "{sleep_vbs_path}" del /f /q "{sleep_vbs_path}" >nul
start "" "{target_exe_path}"
(goto) 2>nul & del "%~f0"
"""


def apply_portable_update_and_restart(new_file_path: Path, target_exe_path: Path | None = None) -> None:
    """生成并启动便携版无锁替换脚本，退出当前进程并重新拉起新版。"""
    if target_exe_path is None:
        target_exe_path = Path(sys.executable).resolve()

    temp_dir = Path(os.environ.get("TEMP", os.getcwd()))
    bat_file = temp_dir / f"handwritesim_updater_{os.getpid()}.bat"
    # 无窗口延时脚本（wscript 为 windows 子系统，不弹任何终端窗口）
    sleep_vbs = temp_dir / f"handwritesim_sleep_{os.getpid()}.vbs"
    sleep_vbs.write_text("WScript.Sleep 1000\r\n", encoding="utf-8")

    bat_content = build_updater_bat_content(
        Path(new_file_path), target_exe_path, Path(new_file_path), sleep_vbs
    )
    bat_file.write_text(bat_content, encoding="utf-8")

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)

    subprocess.Popen(
        ["cmd.exe", "/c", str(bat_file)],
        shell=False,
        creationflags=creationflags,
        close_fds=True,
    )
