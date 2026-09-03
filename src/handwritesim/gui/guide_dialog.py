"""使用指南对话框：分章节向用户简明介绍各功能用法。

入口位于「关于」对话框的「📖 使用指南」按钮。内容为模块级常量，
章节结构与文案可按版本功能演进直接增改；测试依赖 _SECTIONS 的标题关键词。
"""

from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from ..core.updater import GITHUB_REPO_URL

# 高亮色块示意（自带底色，深浅主题下均可读）
_HL_YELLOW = 'style="background-color:#fff3a3;color:#3a3422;padding:0 3px"'
_HL_GREEN = 'style="background-color:#d6f5d6;color:#223a26;padding:0 3px"'
_HL_GRAY = 'style="background-color:#e6e6e6;color:#333333;padding:0 3px"'

_SECTIONS: list[tuple[str, str]] = [
    (
        "🚀 快速上手",
        """
<h3>三步变手写</h3>
<ol>
<li>✍️ 在「待处理文本」里输入或粘贴文字（支持多段、空行）</li>
<li>🎨 选择一款<b>手写字体</b>和一张<b>背景</b>（信纸 / 格子纸 / 自备图片）</li>
<li>📤 点「导出」得到整页 PNG，或「导出 PDF」得到 300 DPI 高清 PDF</li>
</ol>
<p>💡 停止输入约 0.3 秒后右侧会<b>自动预览</b>，改任何参数都能实时看到效果；
文字多了自动分页，用「◀ 上一页 / 下一页 ▶」翻页查看。</p>
<p>字体去哪找？软件不附带字体（版权原因），把 <b>.ttf / .ttc / .otf</b>
放进软件旁的 <b>fonts/</b> 文件夹即可，推荐开源的「霞鹜文楷」。</p>
""",
    ),
    (
        "✏️ 基础排版",
        """
<h3>让版面像真人写的</h3>
<ul>
<li><b>段落工具</b>：选中段落后一键 左对齐 / 居中 / 右对齐 / 首行缩进——标题居中、
落款靠右，一眼不再假。</li>
<li><b>排版参数</b>：字距、行距、字号都带 <b>σ 随机扰动</b>，每个字的大小位置
轻微抖动，告别机器人般的整齐。</li>
<li><b>笔画扰动</b>：水平 / 竖直位移 + 笔画旋转，模拟手腕不稳的笔迹。</li>
<li><b>边距</b>：上 / 下 / 左 / 右独立设置，输入框的位置即含义。</li>
<li><b>文字颜色</b>：填 <b>#RRGGBB</b> 十六进制值，换蓝黑墨水、红笔都行。</li>
<li><b>预设</b>：调好一组参数可「保存预设」，下次「载入预设」一键还原风格；
presets/ 文件夹内的预设会出现在下拉列表里。</li>
</ul>
<p>💡 拿不准文字写到了哪？勾选<b>「边界提示(仅预览)」</b>，非渲染区域会着色
并画出边距框线，只影响预览、不影响导出。</p>
""",
    ),
    (
        "📥 进阶 · 导入 docx 混排",
        f"""
<h3>Word 里高亮一下，手写打印自动分流</h3>
<p>点「导入 docx」可以把整篇 Word 导入正文（对齐、首行缩进原样保留）。
如果文档里有<b>高亮 / 背景色</b>的文字，会弹窗让你选：</p>
<ul>
<li><b>全部作为手写（推荐）</b>：无视高亮，整篇都用手写体。</li>
<li><b>打印 / 手写混排</b>：<span {_HL_GRAY}>未高亮 → 打印体</span>，
<span {_HL_YELLOW}>高亮 → 手写</span>。适合"打印稿 + 局部手写"的作业。</li>
</ul>
<p>也可以反向操作：文字导入后，在正文编辑器里<b>划选</b>一段文字，点
「设为打印」「角色1 黄」「角色2 绿」直接改身份，背景色实时预览，
「清除标记」恢复默认。</p>
<h3>笔迹角色</h3>
<p>「笔迹角色管理 → 管理…」里每个角色都能单独配<b>字体 / 颜色 / 扰动强度</b>。
Word 中第一种背景高亮自动映射为<span {_HL_YELLOW}>手写角色1</span>、
第二种映射为<span {_HL_GREEN}>手写角色2</span>……多人合写一份稿子就这么玩。
打印体角色固定零扰动、零错字，规规矩矩。</p>
""",
    ),
    (
        "📄 进阶 · 文档底图与填空",
        """
<h3>把试卷 / 表格变成手写底稿</h3>
<p>「文档底图 → 导入」支持任意 <b>PDF / Word</b>：每一页都会渲染成背景，
你直接在"打印稿"上写手写内容。适合试卷、申请表、实验报告。</p>
<h3>高亮 = 填空框（自动识别）</h3>
<p>在 Word / PDF 里把要手写填的位置涂上<b>高亮色块</b>（或写
<b>{{...}}</b>、<b>【...】</b> 占位标签），导入时程序会检测到并弹窗：</p>
<ul>
<li><b>提取填空框（推荐）</b>：高亮底色被擦白，自动生成手写填空区域，
不同高亮颜色还会关联到不同笔迹角色。</li>
<li><b>保留完整底图</b>：原样保留所有颜色，不生成填空框——
适合本身带色块 / 表格样式的完整模板。</li>
</ul>
<h3>框选文字，想让字在哪就在哪</h3>
<p>勾选「框选文字」后在预览图上<b>拖出一个矩形</b>：框内文字独立排版，
可选手写体或打印体，字体、字号、颜色、对齐、边距、错字率都能逐框单独设置
（不设就跟随全局）。单击列表项可拖动 / 八向缩放微调，双击重新编辑，
还能指定这个框出现在第几页。</p>
<p>💡 不输正文也能先看纯背景——先摆好版式，再逐页框选。</p>
""",
    ),
    (
        "🧩 进阶 · 错字模拟",
        """
<h3>假装写错了再改</h3>
<p>「写错字」面板让稿子更有"人味"：</p>
<ul>
<li><b>错字率</b>滑杆：0～30%，按比例随机挑字"写错"。</li>
<li><b>重写方式</b>：
<ul>
<li><b>右上方重写</b>——错字划掉后在右上方补一个小一号的正确字（更像真人）；</li>
<li><b>后文重写</b>——划掉后在后面重写一遍。</li>
</ul></li>
<li><b>涂改方式</b>：单横线 / 双横线 / 斜线 / 叉号，四种划掉姿势。</li>
</ul>
<p>框选区域还能<b>逐框单独设错字率</b>：正文规规矩矩，某一段"赶工潦草"，
效果更自然。打印体区域永远零错字。</p>
""",
    ),
    (
        "💡 小技巧与常见问题",
        f"""
<h3>小技巧</h3>
<ul>
<li><b>预览底色</b>：背景图与界面撞色时，点「预览底色」切换浅灰绿 / 深灰底，
看清图片边界。</li>
<li><b>便携模式</b>：fonts/、backgrounds/、presets/ 三个文件夹都在软件旁边，
整个文件夹拷到哪台电脑都能用。</li>
<li><b>找不到字体？</b>：软件不附带版权字体，推荐开源可商用的
「霞鹜文楷」「沐瑶随心手写体」等，放入 fonts/ 即可。</li>
<li><b>导出</b>：PNG 按页输出 0.png、1.png……到 output/；PDF 为 300 DPI
位图层，打印清晰。</li>
</ul>
<h3>常见问题</h3>
<ul>
<li><b>预览没反应？</b> 字体 / 背景没选时会静默跳过，先检查这两项。</li>
<li><b>Word 底图导入失败？</b> PDF 底图开箱即用；DOCX 底图需要本机装有
Microsoft Word 或 LibreOffice 其一。</li>
<li><b>载入预设后文字没了？</b> 预设只保存排版参数，不包含文本内容。</li>
</ul>
<p>更多细节见项目 README：
<a href="{GITHUB_REPO_URL}">{GITHUB_REPO_URL}</a></p>
""",
    ),
]


class GuideDialog(QtWidgets.QDialog):
    """功能使用指南：左侧章节导航 + 右侧富文本说明页。"""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("使用指南")
        self.resize(780, 560)
        self.setMinimumSize(640, 440)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(10)

        self.list_sections = QtWidgets.QListWidget(self)
        self.list_sections.setFixedWidth(208)
        self.list_sections.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        for title, _html in _SECTIONS:
            self.list_sections.addItem(title)
        self.list_sections.currentRowChanged.connect(self._show_section)

        self.text_page = QtWidgets.QTextBrowser(self)
        self.text_page.setOpenExternalLinks(True)

        body.addWidget(self.list_sections)
        body.addWidget(self.text_page, 1)
        layout.addLayout(body, 1)

        row_bottom = QtWidgets.QHBoxLayout()
        btn_close = QtWidgets.QPushButton("关闭", self)
        btn_close.setProperty("primary", True)
        btn_close.setMinimumWidth(80)
        btn_close.clicked.connect(self.accept)
        row_bottom.addStretch(1)
        row_bottom.addWidget(btn_close)
        layout.addLayout(row_bottom)

        self.list_sections.setCurrentRow(0)

    def _show_section(self, row: int) -> None:
        """切换章节：显示对应富文本页。"""
        if 0 <= row < len(_SECTIONS):
            self.text_page.setHtml(_SECTIONS[row][1])
