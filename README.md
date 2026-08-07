# 手写模拟器（HandWriteSim）

把普通文本变成以假乱真的手写体图片：选择一款手写字体、一张信纸背景，程序会按真实的书写习惯排版并施加**字距、行距、字号、笔画位移、笔画旋转**等多种随机扰动，让每个字、每一页都独一无二。

提供 **图形界面（GUI）** 与 **命令行（CLI）** 两种使用方式，核心引擎基于 `numpy` + `scipy` 全向量化重写，预览渲染一页约 0.15 秒，实时交互流畅。

## 技术栈

| 类别 | 选型 |
| --- | --- |
| 语言 / 运行时 | Python 3.14（`.python-version` 锁定） |
| 依赖管理 | uv（`pyproject.toml` + `uv.lock`） |
| 界面 | PyQt6（纯 Qt 控件 + 自动布局，无背景图片依赖） |
| 渲染引擎 | numpy + scipy（FastEngine，默认）；handright 8.2.0（可选经典后端） |
| 图像处理 | Pillow |
| docx 解析 | python-docx |
| 测试 | pytest |
| 打包 | PyInstaller（onefile 单文件，跨 Windows/macOS/Linux，`HandWriteSim.spec`） |

## 功能特性

### GUI（图形界面）

- **富文本输入**：多段文本、空行，所见即所得
- **段落排版工具**：左对齐 / 居中 / 右对齐 / 首行缩进（按 2 倍字体大小），支持整段应用
- **导入 docx**：自动解析段落对齐方式与首行缩进（支持 Word 的「首行缩进 2 字符」写法，沿样式链继承）
- **字体 / 背景选择**：字体支持 `.ttf` `.ttc` `.otf`；背景支持 `.png` `.jpg` `.jpeg` `.bmp`
- **文字颜色**：直接输入 `#RRGGBB` 十六进制颜色值
- **排版参数**：字水平间距、字竖直间距（行距）、字体大小，每个都带独立的随机扰动 σ
- **笔画扰动**：水平位移、竖直位移、笔画旋转三个独立扰动强度
- **边距设置**：上 / 下 / 左 / 右 位置式布局，输入框位置即含义
- **边界提示（仅预览）**：开关 + 自定义颜色，非渲染区域半透明着色并绘制边距框线，直观看清文字实际渲染边界（默认关闭）
- **实时自动预览**：停止输入 300ms 后自动渲染，全程后台线程不卡界面；参数不完整时静默跳过
- **多页预览**：上一页 / 下一页 / 页码指示，自动分页
- **预览底色切换**：浅灰绿 / 深灰两档，背景图与底色撞色时可切换以区分边界
- **预设系统**：预设文件夹（`presets/`）内预设可直接下拉切换，也支持保存 / 载入任意位置（JSON 格式，颜色为 `#RRGGBB`），旧版预设自动兼容
- **一键导出**：全部页面导出为 `0.png`、`1.png`……到 `output/` 目录

### CLI（命令行）

- 纯文本或 docx 输入，批量生成手写图片
- 全部排版 / 扰动 / 颜色参数可用命令行指定
- 载入预设作为基础参数，命令行参数可覆盖
- `--save-preset` 生成后保存参数预设
- 未指定背景时自动生成纯白背景，可用 `--width` / `--height` 控制尺寸
- `--preview-only` 仅渲染第一页，快速查看效果

### 渲染引擎

- **高性能**：连通区域提取用 `scipy.ndimage.label`（C 实现），笔画扰动一次为全部笔画生成随机参数并做向量化坐标变换（旋转 + 平移），替代 handright 的逐像素 Python 循环
- **两种后端**：默认 FastEngine；可切换 `backend="handright"` 使用经典引擎对照
- **段落化排版**：标题居中、右对齐、首行缩进、空行占位，逐行流式跨页，与纯文本路径行为一致
- **真实书写习惯**：标点不换行（`end_chars`）、行首禁则字符（`start_chars`）等换行规则
- **随机性可控**：引擎与排版各自持有随机源，可传入 `seed` 复现结果

## 环境要求与安装

- 操作系统：Windows / macOS / Linux（GUI 与打包均支持）
- Python 3.12+（项目锁定 3.14）
- 包管理器：[uv](https://docs.astral.sh/uv/)

```powershell
# 一键安装全部依赖（自动按 .python-version 选择 Python 版本）
uv sync

# 如需开发/打包依赖（pytest、pyinstaller）
uv sync --extra dev
```

### 依赖清单

| 包 | 版本 | 用途 |
| --- | --- | --- |
| handright | 8.2.0 | 经典手写渲染后端（可选） |
| numpy | >=2.5.1 | 向量化渲染计算 |
| scipy | >=1.18.0 | 连通区域标记等 |
| pillow | >=11,<13 | 图像绘制与读写 |
| PyQt6 | >=6.6 | 图形界面 |
| python-docx | >=1.2.0 | docx 解析 |
| pytest | >=8.0（dev） | 测试 |
| pyinstaller | >=6.0（dev） | 打包 |

## 快速开始

### 启动图形界面

```powershell
uv run python main.py
# 或
uv run handwrite-gui
```

操作流程：输入文字 → 选择字体 → 选择背景 → （可选）调整排版参数与颜色 → 点「预览」或直接「导出」。

### 便携模式（推荐）

exe 首次运行会自动在自身所在目录创建 `fonts/`、`backgrounds/`、`presets/` 三个文件夹：

- `fonts/`：把字体文件（`.ttf` / `.ttc` / `.otf`）放进去，选择字体时默认打开此目录
- `backgrounds/`：放背景图片，选择背景时默认打开此目录
- `presets/`：放预设文件，界面上的预设下拉框会列出此目录内的预设，一键切换

整个文件夹（exe + 资源目录）可以随意拷贝到任意位置，**所有相对路径都以 exe 所在目录为锚点**，无需修改任何路径。

### 命令行示例

```powershell
# 最简：微软雅黑字体，导出到 output/
uv run handwrite-cli "你好，世界！" --font C:/Windows/Fonts/msyh.ttc

# 指定背景与字号，多页文本自动分页
uv run handwrite-cli "第一页正文……第二页正文……" --font f.ttf --background bg.png --font-size 40

# 载入预设 + 命令行覆盖参数
uv run handwrite-cli --preset preset.json "文本内容" --font-size 48 --out output

# 导入 docx（解析对齐与首行缩进）
uv run handwrite-cli --docx 通知.docx --font f.ttf --background 信纸.png

# 仅预览第一页
uv run handwrite-cli "你好" --font f.ttf --background bg.png --preview-only

# 生成并保存当前参数为预设
uv run handwrite-cli "你好" --font f.ttf --background bg.png --save-preset my.preset.json
```

## CLI 参数说明

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `text` | 位置参数 | `""` | 要处理的手写文本 |
| `--docx` | 路径 | `""` | 导入 docx（解析对齐与首行缩进），与 text 互斥优先 |
| `--font` | 路径 | 必填 | 字体文件（`.ttf` / `.ttc`） |
| `--background` | 路径 | `""` | 背景图片；缺省时生成纯白背景 |
| `--width` / `--height` | int | 800 / 1200 | 纯白背景尺寸 |
| `--out` | 目录 | `output` | 输出目录 |
| `--preset` | 路径 | `""` | 载入预设（`.json` / `.txt` / `.preset`） |
| `--save-preset` | 路径 | `""` | 生成后保存参数为预设 |
| `--preview-only` | flag | 关 | 仅渲染第一页保存为 `preview.png` |
| `--font-size` | int | 36 | 字体大小 |
| `--word-spacing` | int | 5 | 字水平间距 |
| `--line-spacing` | int | 48 | 字竖直间距（行距，不含字高） |
| `--left/right/top/bottom-margin` | int | 30 | 四周边距 |
| `--word-spacing-sigma` | int | 2 | 字间距随机扰动 |
| `--line-spacing-sigma` | int | 2 | 行距随机扰动 |
| `--font-size-sigma` | int | 2 | 字号随机扰动 |
| `--perturb-x-sigma` | int | 2 | 笔画水平位移扰动 |
| `--perturb-y-sigma` | int | 2 | 笔画竖直位移扰动 |
| `--perturb-theta-sigma` | float | 0.05 | 笔画旋转扰动（弧度） |
| `--red` / `--green` / `--blue` | int | 0 | 文字颜色 RGB（0-255） |

## 预设文件格式

### JSON 预设（v2，现行格式）

保存到 `.json` 时使用版本 2 结构，**只保存排版参数**（不含文本内容），颜色为 `#RRGGBB` 十六进制：

```json
{
  "version": 2,
  "params": {
    "color": "#000000",
    "font_path": "C:/Windows/Fonts/msyh.ttc",
    "background_path": "D:/信纸/横线信纸.png",
    "font_size": 36,
    "word_spacing": 5,
    "line_spacing": 48,
    "left_margin": 30,
    "right_margin": 30,
    "top_margin": 30,
    "bottom_margin": 30,
    "word_spacing_sigma": 2,
    "line_spacing_sigma": 2,
    "font_size_sigma": 2,
    "perturb_x_sigma": 2,
    "perturb_y_sigma": 2,
    "perturb_theta_sigma": 0.05,
    "end_chars": "，。",
    "start_chars": ""
  }
}
```

> 预设刻意**不包含** `text` / `paragraphs`：文本属于内容而非排版风格，载入预设时保留当前输入框内容。

### 兼容性

| 旧格式 | 载入行为 |
| --- | --- |
| JSON v1（`red` / `green` / `blue` 数字） | 自动转换载入，等价于新 hex 颜色 |
| 旧版 18 行纯文本（`.txt` / `.preset`） | 格式不变，照常载入 |

旧预设载入后若再保存，将统一以新格式（v2 + `#RRGGBB`）写出。

## 项目结构

```
Handwriting-simulator/
├── main.py                      # GUI 启动入口（uv run python main.py）
├── pyproject.toml               # 项目元数据、依赖声明、脚本入口、uv 依赖覆盖
├── uv.lock                      # 依赖锁定文件
├── .python-version              # Python 版本锁定（3.14）
├── HandWriteSim.spec            # PyInstaller 打包配置（onefile，跨平台）
├── build.ps1                    # Windows 一键打包脚本
├── backgrounds/                 # 内置背景素材（随仓库分发，可自行替换）
├── presets/                     # 内置预设示例（JSON v2，相对路径引用资源）
├── fonts/                       # 运行时自动创建，用户自备字体（版权字体不入库）
├── ui/                          # GUI 资源
│   └── 3d.ico                   # 窗口图标
├── docs/superpowers/            # 设计文档（段落化排版功能的设计与实现计划）
│   ├── plans/
│   └── specs/
├── src/handwritesim/            # 包源码（src 布局）
│   ├── app.py                   # GUI 应用入口（QApplication + MainWindow）
│   ├── cli.py                   # 命令行入口（argparse）
│   ├── core/                    # 核心逻辑层（不依赖任何 GUI 组件）
│   │   ├── models.py            # HandwritingParams / Paragraph 数据模型、校验、序列化
│   │   ├── paths.py             # 资产目录解析（exe 旁/项目根）与目录自动创建
│   │   ├── engine.py            # 引擎门面：统一接口，默认 fast 后端，可切 handright
│   │   ├── engine_fast.py       # FastEngine：numpy/scipy 高性能渲染
│   │   ├── engine_handright.py  # HandrightEngine：handright 经典后端
│   │   ├── docx_io.py           # docx 解析：段落对齐 + 首行缩进（含样式链继承）
│   │   └── presets.py           # 预设读写：JSON v2 + 旧版文本兼容 + 相对路径双向转换
│   └── gui/                     # 图形界面层
│       ├── ui.py                # Qt 界面构建（纯控件 + 自动布局）
│       ├── main_window.py       # 主窗口逻辑：参数映射、事件、预览降采样、预设下拉切换
│       ├── workers.py           # QThread 后台渲染 / 导出，信号回传结果
│       └── resources.py         # 资源路径解析（开发 / PyInstaller 双环境）
└── tests/                       # pytest 测试
    ├── test_docx_io.py          # docx 对齐与缩进解析
    ├── test_engine.py           # 引擎接口、校验、参数序列化
    ├── test_engine_fast.py      # FastEngine 渲染与段落路径
    └── test_presets.py          # 预设读写、hex 颜色、相对路径往返、新旧格式兼容
```

### 分层约定

- `core/` 不 import 任何 GUI 模块，GUI 与 CLI 共同依赖 core，保证命令行与图形界面行为一致
- 数据流：GUI/CLI → `HandwritingParams`（校验）→ `HandwritingEngine` → 图片
- 界面控件名沿用历史命名（`lineEdit_10` 等），`main_window.py` 通过对象名引用，与 `ui.py` 解耦

## 渲染引擎原理

### FastEngine（默认）

针对 handright 逐像素纯 Python 循环的性能瓶颈，用 numpy + scipy 重写：

1. **排版**：复用 PIL C 层 `ImageDraw.text` 逐字绘制（本就快，非瓶颈），逐字字号 / 行位高斯扰动，字符宽度按 `(字号, 字符)` 缓存
2. **连通区域提取**：`scipy.ndimage.label`（C 实现）一次得到所有笔画的像素标签，替代原 Python DFS
3. **笔画扰动**：对每个笔画用 numpy 向量化坐标变换（绕笔画中心旋转 + 平移），一次写回画布

### 段落渲染路径

`params.paragraphs` 非空时启用：每个段落独立排版（对齐 / 首行缩进），按行提取墨迹后逐行流式拼接跨页；行节奏与纯文本路径完全对齐，空行保留占位。居中按行测量水平置中；右对齐按行逻辑宽度（含尾部空格）平移到右边距。

### 随机性

- 排版随机源：`random.Random(seed)`，保证逐字扰动序列可复现
- 笔画扰动随机源：`numpy.random.default_rng(seed)`
- 预览与导出的随机行为一致（预览仅针对超大背景做参数等比缩放）

### 换行规则

- `end_chars`（默认 `，。`）：行尾遇到这些字符时不换行，避免标点孤悬行首
- `start_chars`：行首禁则字符

### 预览降采样策略

背景宽度超过 4096px 时，预览将背景缩放到 4096 宽，并**按同一比例缩放全部空间参数**（字号、边距、间距、扰动），保证预览与导出布局一致、行线不错位；导出始终使用原始参数全分辨率渲染。

## 开发指南

```powershell
# 运行全部测试
uv run pytest

# 运行单个测试文件
uv run pytest tests/test_presets.py -v
```

当前测试覆盖：引擎生成 / 预览 / 导出、参数校验、段落往返序列化、docx 对齐与缩进解析、预设新旧格式兼容、hex 颜色解析等。

### 代码规范要点

- 核心逻辑放 `core/`，GUI 只做控件映射与任务调度
- 渲染 / 导出在 `QThread` 中执行，禁止在子线程操作控件
- 参数校验统一在 `HandwritingParams.validate()`，GUI 与 CLI 共用
- 数字输入框禁用滚轮改值（`NoWheelSpinBox`），避免与滚动面板冲突

### 设计文档

功能开发遵循「先设计后编码」流程，相关设计文档位于 `docs/superpowers/specs/` 与 `docs/superpowers/plans/`（如段落化排版的设计与实现计划）。

## 字体与版权

**仓库不随包分发任何字体**：手写体多为商业版权字体（如汉呈、华阳、云江等字库），开源分发需授权，故请用户自备字体。推荐以下**开源 / 免费可商用**的手写体（下载后放入 `fonts/` 即可）：

| 字体 | 协议 | 说明 |
| --- | --- | --- |
| 霞鹜文楷（LXGW WenKai） | OFL 1.1 | 开源可商用，最接近手写体观感 |
| 沐瑶随心手写体 | 免费可商用 | 灵动手写风格 |
| 站酷小薇 / 站酷快乐体 | 免费可商用 | 站酷字库出品 |
| 内海字体（NeiHai） | 开源 | 手写圆体 |

仓库内置的 `backgrounds/`（信纸、格子纸等）与 `presets/`（参数示例）均为原创素材，可自由使用与分发。

## 打包发布

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File build.ps1

# macOS / Linux（等价命令）
uv run --extra dev pyinstaller --noconfirm --clean HandWriteSim.spec
```

- 产物：`dist/HandWriteSim`（**单文件**，Windows 约 49 MB，Linux/macOS 体积相近）
- 便携模式：首次运行自动创建 `fonts/`、`backgrounds/`、`presets/` 目录，把资源放进去即可，整个文件夹可随意拷贝
- 打包配置见 `HandWriteSim.spec`（PyInstaller onefile 模式），已按 `sys.platform` 跨平台适配：
  - imageformats 插件按平台文件名收集（`qjpeg.dll` / `libqjpeg.so` / `libqjpeg.dylib`）
  - 未使用 Qt DLL 的剔除清单仅 Windows 生效，其余平台保留避免启动失败
  - 图标仅 Windows 支持 `.ico`，Linux/macOS 使用默认图标
  - UPX 压缩仅非 macOS 平台启用（不破坏签名）
- 首次启动时单文件会自解压到系统临时目录，启动稍慢属正常现象
- 首次运行若被杀毒软件（如 Windows Defender）实时扫描拦截，等待扫描完成或将其加入白名单后即可正常启动（PyInstaller 单文件程序的共性现象，并非程序问题）

## 常见问题

**为什么没有随包字体？**
手写体多为商业版权字体，开源分发需授权，故仓库不提供字体。请自备字体放入 `fonts/` 目录，或从上方「字体与版权」章节的开源字体清单中下载。预设中的字体路径为占位（如 `fonts/云烟体.ttf`），对应的字体文件放入后即可使用，也可以直接改用其他字体。

**为什么预览没有边界提示？**
边界提示默认关闭，勾选「边界提示(仅预览)」后按边距参数在预览图上着色并绘制框线，可自定义提示颜色（`#RRGGBB`）。

**载入预设后文本被清空了吗？**
不会。预设只包含排版参数，载入时保留当前文本输入框内容；仅当旧预设本身携带文本时才会回填。

**自动预览什么时候不触发？**
字体 / 背景缺失、未输入文本时静默跳过，不弹窗打断输入；输入停止 300ms 后触发。
