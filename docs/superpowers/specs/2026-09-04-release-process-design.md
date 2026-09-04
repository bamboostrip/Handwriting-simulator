# 发布流程规范（Python 版）

为避免以后推送版本出问题（漏改版本号、Release 缺更新介绍、资产不全、
自动更新失效），发版一律按本规范执行。机制与 Rust 版对齐
（`CHANGELOG.md` + `packaging/release-body.md` + 单文件资产）。

## 版本号位置（共 3 处，发版时同步改）

| 文件 | 字段 |
| --- | --- |
| `src/handwritesim/__init__.py` | `__version__`（软件内显示与更新比对的唯一来源） |
| `pyproject.toml` | `version` |
| `uv.lock` | 项目自身的 `version`（改完前两处后跑 `uv lock` 自动同步，勿手改依赖项） |

版本号遵循 SemVer：新功能加 minor（如 `0.3.1` → `0.4.0`），纯修复加 patch。

## CHANGELOG（更新介绍的唯一来源）

- `CHANGELOG.md` 按 Keep a Changelog 维护：开发期间记到 `## [Unreleased]`，
  发布时移入 `## [x.y.z] - 日期` 小节（必须与 tag 版本一致，不带 `v`）。
- Release 页面的 `## 更新内容` 即该小节原文；软件内更新弹窗展示同一节
  （`trim_release_notes_markdown` 自动截掉下载说明样板）。
- 若某次打 tag 时 CHANGELOG 缺对应小节，工作流自动回退到 git 提交列表，
  不会发空 Release——但属于事故，发布检查清单里要确认。

## 打 tag 即发布

```powershell
git push origin main
git tag v0.4.0
git push origin v0.4.0
```

- tag 必须打在已推送的 `main` 提交上（先推分支再打 tag）。
- Action（`.github/workflows/build.yml`）行为：三平台构建 →
  每平台产出便携 zip + 单文件升级包 → 拼 Release 说明
  （CHANGELOG 小节 + `packaging/release-body.md`，`{TAG}` 自动替换）
  → 发布 6 个资产。`files: artifacts/**/*` 缺一不可。
- Windows 单文件（`HandWriteSim-windows-x86_64.exe`）是应用内自动更新
  的唯一下载目标（`updater.py` 只匹配 `.exe`，无单文件时回退浏览器下载）。
  若某次 Release 缺 `.exe` 资产，自动更新即退化——发版后必须验证。

## 发布检查清单

1. `pytest` 全绿（含 `test_updater.py`）。
2. 三处版本号一致，且 `CHANGELOG.md` 有对应版本小节。
3. 推分支、打 tag、推 tag。
4. Actions 三平台构建全绿（约十几分钟）。
5. 打开 Release 页确认：更新内容为 CHANGELOG 原文、6 个资产齐全、
   下载直链可点（`{TAG}` 已替换）。
6. 用旧版本点「检查更新」，确认弹窗只显示更新介绍、能下载单文件。

## 回滚

发版搞砸不要删 tag 重打（用户可能已收到更新提示）：修好后加 patch
版本重新走一遍本流程。
