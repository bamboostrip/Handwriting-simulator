# Changelog

本文件记录各版本的更新内容。发布新版本时：

1. 在 `## [Unreleased]` 下累积改动，发布时移入新的 `## [x.y.z] - 日期` 小节
2. 打标签 `vx.y.z` 触发 GitHub Actions 构建，发布说明自动从本文件提取
   当前版本小节（缺失时回退到 git 提交列表）并拼入 Release 页面
3. 软件内「检查更新」弹窗只展示 `## 更新内容` 一节（自动截掉下载说明等样板）

格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.4.2] - 2026-09-04

### Fixed

- **修复打包版检查更新永远失败**（`无法连接至 GitHub Releases API`）：
  `HandWriteSim.spec` 在 Windows 下误删了 `libssl-3-x64.dll` /
  `libcrypto-3-x64.dll`（CPython `_ssl.pyd` 的依赖），导致打包版所有
  `https`（检查更新 / 下载）必然失败。现仅剔除无用的 Qt DLL，保留
  OpenSSL，并新增 `tests/test_build_spec.py` 回归测试锁定。

## [0.4.1] - 2026-09-04

### Fixed

- **修复自动更新完成后弹出 ping 终端窗口**（与 Rust 版同根因）：
  更新批处理的 `ping 127.0.0.1 -n 2` 延时在 Win11 默认终端下会弹新终端窗口
  （CREATE_NO_WINDOW 不继承给孙进程）；改走 `wscript //B //Nologo` +
  `WScript.Sleep` 无窗口延时，新增回归测试锁定。Python 版无覆盖重试，
  原本只闪现一次，修后彻底无感。

## [0.4.0] - 2026-09-04

### Added

- **混排笔迹 / 多角色手写**：TextRun/Role 动态映射、docx 字体字号加粗提取、
  引擎多字体多色流式混排、打印体系统字体下拉、角色面板与划选标记
  （正文划选一键设打印 / 角色 / 清除，Word 高亮自动映射角色）。
- **PDF / DOCX 底图高亮区域自动识别并关联笔迹角色**：高亮色块连通域检测 /
  同色合并 / 整页擦白，`{{...}}`、`【...】` 占位标签扫描，PDF 文本层提取
  框内文字字号行距与缩进，跨页同色映射同一角色。
- **高亮导入确认对话框**：docx 导入可选「全部作为手写 / 打印手写混排」；
  文档底图可选「提取填空框 / 保留完整底图」；「关于」→「使用指南」对话框。
- **单文件升级包**：Release 每平台同步提供便携 zip + 单文件
  （`HandWriteSim-windows-x86_64.exe` 等），应用内自动更新只走单文件、
  下载后覆盖替换重启，无需解压整包。

### Fixed

- 预设空白 / 标题加粗识别 / 打印多字体混排 / 打印扰动。
- 预览降采样丢失加粗与多字体（重建 TextRun 保留样式），docx 字号改按
  文档内比例映射，打印体零扰动零错字。
- 字号校准对齐 Rust 版（全角紧包围盒上限，宁小勿溢）。
- 导入无标记文档时清理残留区域；全手写模式下 `{{tag}}` 不再抢角色。
- UPX 排除压不了的 DLL（CPython 3.14 的 `python3.dll`），消除构建 traceback。

### Docs

- 双版 README 对齐：Python 补高亮填空教程，Rust 补占位标签 / 划选操作 /
  Word 直导依赖说明，统一错字与导出措辞；Release 说明改用直接下载链接模板。

## [0.3.1] - 2026-08-24

- 新增关于对话框、Rust 重构版推荐与便携版自动更新功能。
- 修复渲染崩溃、CLI 预设覆盖与深色模式深度适配。
