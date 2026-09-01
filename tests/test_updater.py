"""更新模块与关于对话框测试。"""

from __future__ import annotations

import io
import json
import os
import urllib.request
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication

from handwritesim.core.updater import (
    UpdateInfo,
    clean_version,
    compare_versions,
    get_skipped_version,
    is_auto_check_enabled,
    parse_version_tuple,
    set_auto_check_enabled,
    set_skipped_version,
    check_for_updates,
)
from handwritesim.gui.about_dialog import AboutDialog
from handwritesim.gui.update_dialog import UpdateDialog

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_clean_version() -> None:
    assert clean_version("v0.3.1") == "0.3.1"
    assert clean_version("V1.0.0 ") == "1.0.0"
    assert clean_version("0.3.0") == "0.3.0"


def test_parse_version_tuple() -> None:
    assert parse_version_tuple("0.3.1") == (0, 3, 1)
    assert parse_version_tuple("v1.2.3.4") == (1, 2, 3, 4)
    assert parse_version_tuple("invalid") == (0,)


def test_compare_versions() -> None:
    assert compare_versions("0.3.1", "0.3.2") == -1
    assert compare_versions("0.3.2", "0.3.1") == 1
    assert compare_versions("v0.3.1", "0.3.1") == 0
    assert compare_versions("1.0.0", "0.9.9") == 1
    assert compare_versions("0.3.1.1", "0.3.1") == 1
    assert compare_versions("0.3.1", "0.3.1.0") == 0


def test_settings_auto_check_and_skip(app) -> None:
    set_auto_check_enabled(False)
    assert is_auto_check_enabled() is False
    set_auto_check_enabled(True)
    assert is_auto_check_enabled() is True

    set_skipped_version("0.3.2")
    assert get_skipped_version() == "0.3.2"
    set_skipped_version("")
    assert get_skipped_version() == ""


def test_check_for_updates_mocked(monkeypatch) -> None:
    mock_payload = {
        "tag_name": "v0.3.2",
        "name": "v0.3.2 优化更新",
        "body": "## 更新日志\n- 修复了若干问题\n- 增加了便携版更新",
        "html_url": "https://github.com/bamboostrip/Handwriting-simulator/releases/tag/v0.3.2",
        "assets": [
            {
                "name": "HandWriteSim.exe",
                "browser_download_url": "https://github.com/example/HandWriteSim.exe",
                "size": 12345678,
            }
        ],
    }

    class MockResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps(mock_payload).encode("utf-8")

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=5.0: MockResponse())

    # 当前版本 0.3.1，查询到 0.3.2 应返回更新
    info = check_for_updates("0.3.1")
    assert info is not None
    assert info.version == "0.3.2"
    assert info.asset_name == "HandWriteSim.exe"
    assert info.asset_size == 12345678

    # 当前版本 0.3.2，没有新版本应返回 None
    info_same = check_for_updates("0.3.2")
    assert info_same is None

    # check_all=True 即使同版本也返回 info
    info_all = check_for_updates("0.3.2", check_all=True)
    assert info_all is not None


def test_about_dialog_render(app) -> None:
    dlg = AboutDialog()
    dlg.show()
    pix = dlg.grab()
    assert not pix.isNull()
    assert pix.width() > 0
    dlg.close()


def test_update_dialog_render(app) -> None:
    info = UpdateInfo(
        version="0.3.2",
        tag_name="v0.3.2",
        title="v0.3.2 更新说明",
        body="### 更新内容\n- 完善深色模式\n- 增加自动更新",
        html_url="https://github.com/bamboostrip/Handwriting-simulator/releases/tag/v0.3.2",
        asset_name="HandWriteSim.exe",
        asset_url="https://example.com/HandWriteSim.exe",
        asset_size=1024000,
    )
    dlg = UpdateDialog(info, "0.3.1")
    dlg.show()
    pix = dlg.grab()
    assert not pix.isNull()
    assert pix.width() > 0
    dlg.close()
