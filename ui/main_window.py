"""
main_window.py

Marginalia 的主窗口。

UI 风格参照 iBooks/Apple 的极简风格：
    - 顶部一条细工具栏：左右翻页箭头 + 居中书名 + 目录/搜索切换按钮
    - 左侧可收起的侧边栏：目录树 或 搜索结果列表（二选一展示）
    - 中间是铺满的阅读区域（QWebEngineView）
    - 没有多余的边框、按钮、装饰
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView

from core.epub_loader import EpubBook, TocEntry, load_epub
from core.highlights import Highlight, HighlightStore
from core.search import ChapterText, SearchResult, build_search_index, search
from core.export import export_to_file
from core.paths import resource_path
from ui.theme import DARK, Theme, ThemeManager

SIDEBAR_TOC    = 0
SIDEBAR_SEARCH = 1
SIDEBAR_NOTES  = 2

# JS → Python 消息前缀（高亮操作）
_HL_PREFIX = "MARGINALIA_HL::"

# highlighter.js / footnotes.js 的路径
# 用 resource_path() 而不是直接拼 __file__，是因为打包成 .app 之后
# __file__ 反推出来的相对路径会失效（见 core/paths.py 里的说明）
_HIGHLIGHTER_JS_PATH = resource_path("assets", "web", "highlighter.js")
_FOOTNOTES_JS_PATH = resource_path("assets", "web", "footnotes.js")

# JS 端通过 console.log 这个固定前缀的消息向 Python 上报"该翻页了"，
# Python 侧用自定义 QWebEnginePage 拦截 javaScriptConsoleMessage 来接收。
#
# 为什么不用 QWebChannel：
#   QWebChannel 需要加载 qwebchannel.js 这个 Qt 自带的桥接脚本，
#   但实测发现当前 PySide6 wheel 并没有把这个文件打进 Qt 资源系统里
#   （:/qtwebchannel/qwebchannel.js 不存在），意味着要自己额外维护这份
#   第三方 JS 文件。对于这种单向、低频的简单信号，用 console.log + 拦截
#   console message 是 Qt 官方文档认可的轻量做法，不引入额外文件依赖。
_BOTTOM_SIGNAL = "MARGINALIA_REACHED_BOTTOM"

# 章末自动翻页：模仿"下拉刷新"的手势——只有当页面已经滚到底、
# 用户还在持续往下滚/划（一次有分量的下拉动作），才翻页。
#
# 这是从"停在底部等一段时间就翻页"的旧方案改过来的。旧方案两个问题：
#   1. 只要停留够时间就翻页，跟用户是不是"读完了想翻页"没关系，
#      正常阅读时的停顿也会被当成翻页信号，体验上显得很突兀。
#   2. 一屏就能放下的短章节，天然"已经在底部"，完全不需要用户做任何
#      动作，每一章都会自动触发，遇上连续几个短章节就变成停不下来的
#      连续跳转。
#
# 新方案把判断依据从"时间+位置"换成"手势"：只有用户在到底之后还
# 主动做了一个持续的下拉动作，才算数——短章节如果用户什么都不做，
# 永远不会自动翻页；正常阅读的停顿也不会被误判。
#
# 后来又发现一种情况：快速翻阅一整章（一路很快地连续滚动）时，
# 冲到章末的那个动作本身还带着"惯性尾巴"——最后几个滚动事件
# 其实是同一个快速滑动动作的延续，而不是用户到底之后重新发起的
# 下拉。这些事件恰好在滚动位置越过底部的那一刻还在继续，会被误
# 当成"下拉手势"直接累计过阈值，导致感觉像是"瞬间跳走了"。
# 用 LANDING_DELAY_MS 处理：刚抵达底部的这一小段时间内，
# 不响应任何下拉累计，先让画面在底部"停一下"；用户如果真的还想
# 继续，缓冲期过后再拉一次就行。
_PULL_TO_NEXT_JS_TEMPLATE = """
(function() {
    if (window.__marginaliaPullWatcherInstalled) { return; }
    window.__marginaliaPullWatcherInstalled = true;

    const BOTTOM_THRESHOLD_PX = 4;      // 判断"是否已经到底"的容错范围
    const PULL_THRESHOLD_PX   = 1140;   // 下拉累计够这么多"像素"才触发翻页
    const IDLE_RESET_MS       = 950;    // 停手超过这么久，之前拉的量作废重新算
    const LANDING_DELAY_MS    = 450;    // 刚抵达底部的这段时间内不响应下拉，
                                         // 用来过滤"快速翻阅冲到章末"时残留
                                         // 的滚动惯性，让画面先在底部停一下
    const COOLDOWN_MS         = 1000;   // 每次翻页后，新的一章有这么久的"冷静期"
                                         // 防止上一次下拉的惯性延续到新页面里，
                                         // 造成连续几章被一口气跳过去

    const installedAt = Date.now();
    let atBottom = false;
    let bottomArrivedAt = 0;
    let pullDistance = 0;
    let lastPullTime = 0;
    let triggered = false;

    function computeAtBottom() {
        const scrollTop = window.scrollY;
        const viewportHeight = window.innerHeight;
        const fullHeight = document.documentElement.scrollHeight;
        return scrollTop + viewportHeight >= fullHeight - BOTTOM_THRESHOLD_PX;
    }

    // 用 scroll 事件而不是 wheel 事件来判断"什么时候抵达底部"，是因为
    // 快速滑动时，滚动位置的变化（含惯性动画）比 wheel 事件本身更连续、
    // 更贴近真实画面——这样才能准确捕捉到"刚好越过底部"的那一刻，
    // 而不是依赖某次 wheel 事件恰好在那时触发。
    function onScroll() {
        const nowAtBottom = computeAtBottom();
        if (nowAtBottom && !atBottom) {
            // 刚刚抵达底部：记下时间，从这一刻起才允许开始累计下拉手势
            bottomArrivedAt = Date.now();
            pullDistance = 0;
        }
        if (!nowAtBottom) {
            pullDistance = 0;
        }
        atBottom = nowAtBottom;
    }

    // 不同输入设备上报的 deltaY 单位不一样：触控板通常是像素，
    // 鼠标滚轮常见是"行"，极少数是"页"。统一换算成大致的像素量，
    // 这样阈值才能对两种设备都合理。
    function normalizedDelta(e) {
        let delta = e.deltaY;
        if (e.deltaMode === 1) {        // DOM_DELTA_LINE
            delta *= 16;
        } else if (e.deltaMode === 2) { // DOM_DELTA_PAGE
            delta *= window.innerHeight;
        }
        // 单次事件的贡献设个上限，避免某次异常大的 delta 一下子就跨过阈值，
        // 让"下拉"失去应有的"持续动作"的意味
        return Math.max(0, Math.min(delta, 80));
    }

    function onWheel(e) {
        if (triggered) { return; }

        // 刚翻页过来的这一小段时间，不响应任何下拉——把上一次手势
        // 可能带来的惯性滤掉，避免连续翻好几章
        if (Date.now() - installedAt < COOLDOWN_MS) { return; }

        // wheel 事件发生时，先用最新的滚动位置刷新一遍"是否到底"的状态，
        // 不等下一次 scroll 事件——保证这里用到的 atBottom 足够新鲜
        onScroll();

        if (!atBottom || e.deltaY <= 0) {
            // 没到底，或者是在往回滚：当前这次"下拉尝试"作废
            pullDistance = 0;
            return;
        }

        if (Date.now() - bottomArrivedAt < LANDING_DELAY_MS) {
            // 刚落地，还在缓冲期内——大概率是快速翻阅冲过来的惯性尾巴，
            // 不算数，直接忽略，让画面先停一下
            return;
        }

        const now = Date.now();
        if (now - lastPullTime > IDLE_RESET_MS) {
            // 中间停顿太久，之前拉的不算数，重新开始累计
            pullDistance = 0;
        }
        lastPullTime = now;

        pullDistance += normalizedDelta(e);

        if (pullDistance >= PULL_THRESHOLD_PX) {
            triggered = true;
            console.log("__BOTTOM_SIGNAL__");
        }
    }

    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('wheel', onWheel, { passive: true });

    // 立刻同步跑一次：如果整章内容一屏就能放下（一屏内容不会触发任何
    // scroll 事件），atBottom/bottomArrivedAt 得在这里就确定下来，
    // 不能指望第一次 wheel 事件里才顺带发现"原来一开始就在底部"——
    // 那样会把 bottomArrivedAt 错误地记成"用户手势发生的那一刻"，
    // 平白让用户第一次真实的下拉动作被落地缓冲期挡掉。
    onScroll();
})();
"""
# 用简单字符串替换而不是 f-string/str.format，
# 因为 JS 代码本身全是花括号，跟 f-string/format 的转义语法冲突，
# 用 .replace() 这种最朴素的方式反而最不容易出错
_PULL_TO_NEXT_JS = _PULL_TO_NEXT_JS_TEMPLATE.replace(
    "__BOTTOM_SIGNAL__", _BOTTOM_SIGNAL
)

# 注入到阅读器每个章节的排版 CSS。
# 策略：只动布局层（宽度、行高、内边距），不碰字体/颜色，保留原书风格。
# 用 id="marginalia-reader-style" 做幂等保护，防止重复注入。
_READER_CSS_JS = """
(function() {
    if (document.getElementById('marginalia-reader-style')) { return; }
    const style = document.createElement('style');
    style.id = 'marginalia-reader-style';
    style.textContent = `
        /* 限制正文宽度，左右居中，保留书本阅读感 */
        body {
            max-width: 720px !important;
            margin-left:  auto !important;
            margin-right: auto !important;
            padding-left:  32px !important;
            padding-right: 32px !important;
            padding-top:   40px !important;
            padding-bottom: 60px !important;
            box-sizing: border-box !important;
        }
        /* 正文段落：舒适行高，段间距 */
        p, div, li, td {
            line-height: 1.85 !important;
        }
        /* 标题上方留更多空间 */
        h1, h2, h3, h4, h5, h6 {
            margin-top: 1.6em !important;
            line-height: 1.3 !important;
        }
        /* 图片自适应宽度，不溢出 */
        img {
            max-width: 100% !important;
            height: auto !important;
        }
    `;
    document.head.appendChild(style);
})();
"""

# 阅读区域的"夜间模式"：只强制背景色和默认文字色，不去逐个元素纠正
# （代码块、特殊高亮文字这类原书自带样式可能会显得不协调，但这是几乎
# 所有 epub 阅读器"一键夜间模式"的通用做法，取舍上足够好用）。
# 用固定 id 的 <style> 标签存放规则，切换主题时只需要改它的 textContent，
# 不需要整页重新加载。
_READER_DARK_CSS_JS = f"""
(function() {{
    let style = document.getElementById('marginalia-dark-mode');
    if (!style) {{
        style = document.createElement('style');
        style.id = 'marginalia-dark-mode';
        document.head.appendChild(style);
    }}
    style.textContent = `
        html, body {{
            background: {DARK.reader_bg} !important;
            color: {DARK.text} !important;
        }}
        a, a:visited {{ color: #6cb2ff !important; }}
    `;
}})();
"""

# 切回浅色模式时，直接把注入的夜间样式表摘掉，还原书本原始配色
_READER_LIGHT_CSS_JS = """
(function() {
    const style = document.getElementById('marginalia-dark-mode');
    if (style) { style.remove(); }
})();
"""


class ReaderPage(QWebEnginePage):
    """
    自定义 QWebEnginePage，拦截 JS 里的 console.log 消息：
      - MARGINALIA_REACHED_BOTTOM  → 触发自动翻页
      - MARGINALIA_HL::{json}      → 高亮操作（创建/更新/删除）
    """

    def __init__(self, on_reach_bottom, on_highlight_msg, parent=None) -> None:
        super().__init__(parent)
        self._on_reach_bottom = on_reach_bottom
        self._on_highlight_msg = on_highlight_msg

    def javaScriptConsoleMessage(self, level, message, line_number, source_id) -> None:
        if message == _BOTTOM_SIGNAL:
            self._on_reach_bottom()
        elif message.startswith(_HL_PREFIX):
            payload_str = message[len(_HL_PREFIX):]
            try:
                payload = json.loads(payload_str)
                self._on_highlight_msg(payload)
            except json.JSONDecodeError:
                pass


class MainWindow(QMainWindow):
    def __init__(self, on_back_to_library=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Marginalia")
        self.resize(1100, 1100)

        self._on_back_to_library = on_back_to_library
        self.book: EpubBook | None = None
        self.epub_path: str = ""
        self.current_chapter_idx: int = 0
        self.search_index: list[ChapterText] = []
        self._last_search_results: list[SearchResult] = []
        self.highlight_store: HighlightStore | None = None
        self._current_note_id: int | None = None

        self._theme_mgr = ThemeManager.instance()

        self._build_ui()
        self._build_shortcuts()
        self.sidebar_container.setVisible(False)

        self._apply_theme(self._theme_mgr.current)
        self._theme_mgr.changed.connect(self._on_theme_changed)

    # ------------------------------------------------------------------
    # UI 搭建
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.toolbar = self._build_toolbar()
        root_layout.addWidget(self.toolbar)

        # --- 主体：侧边栏 + 阅读区域，用 Splitter 让宽度可拖拽 ---
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter = self.main_splitter
        splitter.setHandleWidth(1)

        self.sidebar_container = self._build_sidebar()
        self.web_view = QWebEngineView()
        self.reader_page = ReaderPage(
            on_reach_bottom=self._on_chapter_scrolled_to_bottom,
            on_highlight_msg=self._on_highlight_message,
            parent=self.web_view,
        )
        self.web_view.setPage(self.reader_page)
        self.web_view.loadFinished.connect(self._on_page_loaded)

        splitter.addWidget(self.sidebar_container)
        splitter.addWidget(self.web_view)
        splitter.addWidget(self._build_note_drawer())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([260, 840, 0])

        root_layout.addWidget(splitter, stretch=1)

    def _build_toolbar(self) -> QWidget:
        toolbar = QWidget()
        toolbar.setFixedHeight(44)
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(8, 0, 8, 0)

        self.btn_back = QPushButton("书库")
        self.btn_back.setVisible(self._on_back_to_library is not None)
        self.btn_back.clicked.connect(self._go_back_to_library)

        self.btn_open = QPushButton("打开")
        self.btn_open.clicked.connect(self.open_file_dialog)

        # 目录按钮
        self.btn_toc = QPushButton("☰")
        self.btn_toc.setCheckable(True)
        self.btn_toc.setToolTip("目录")
        self.btn_toc.clicked.connect(self._toggle_toc_sidebar)

        # 搜索按钮
        self.btn_search = QPushButton("🔍")
        self.btn_search.setCheckable(True)
        self.btn_search.setToolTip("搜索")
        self.btn_search.clicked.connect(self._toggle_search_sidebar)

        # 笔记列表按钮
        self.btn_notes_list = QPushButton("📝")
        self.btn_notes_list.setCheckable(True)
        self.btn_notes_list.setToolTip("笔记列表")
        self.btn_notes_list.clicked.connect(self._toggle_notes_sidebar)

        self.btn_prev = QPushButton("‹")
        self.btn_prev.clicked.connect(self.prev_chapter)
        self.btn_prev.setEnabled(False)

        self.title_label = QLabel("未打开任何书籍")
        self.title_label.setObjectName("title")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_next = QPushButton("›")
        self.btn_next.clicked.connect(self.next_chapter)
        self.btn_next.setEnabled(False)

        self.btn_info = QPushButton("ⓘ")
        self.btn_info.setToolTip("编辑书籍信息")
        self.btn_info.setEnabled(False)
        self.btn_info.clicked.connect(self._open_meta_editor)

        self.btn_edit = QPushButton("✍️")
        self.btn_edit.setToolTip("编辑内容")
        self.btn_edit.setEnabled(False)
        self.btn_edit.clicked.connect(self._open_epub_editor)

        # 暗黑模式切换：图标随当前主题变化（暗色下显示"点了会变亮"的太阳，
        # 浅色下显示"点了会变暗"的月亮），文字在 _apply_theme 里同步更新
        self.btn_theme = QPushButton()
        self.btn_theme.setToolTip("切换深色/浅色模式")
        self.btn_theme.clicked.connect(self._theme_mgr.toggle)

        layout.addWidget(self.btn_back)
        layout.addWidget(self.btn_open)
        layout.addWidget(self.btn_toc)
        layout.addWidget(self.btn_search)
        layout.addWidget(self.btn_notes_list)
        layout.addWidget(self.btn_prev)
        layout.addWidget(self.title_label, stretch=1)
        layout.addWidget(self.btn_next)
        layout.addWidget(self.btn_info)
        layout.addWidget(self.btn_edit)
        layout.addWidget(self.btn_theme)

        return toolbar

    def _build_sidebar(self) -> QWidget:
        """
        侧边栏容器：内部用 QStackedWidget 在"目录树"和"搜索面板"之间切换，
        外层包一层是为了方便整体设置宽度和隐藏/显示。
        """
        container = QWidget()
        container.setMinimumWidth(200)
        container.setMaximumWidth(420)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar_stack = QStackedWidget()
        self.sidebar_stack.addWidget(self._build_toc_panel())     # index 0
        self.sidebar_stack.addWidget(self._build_search_panel())  # index 1
        self.sidebar_stack.addWidget(self._build_notes_list_panel())  # index 2
        layout.addWidget(self.sidebar_stack)

        return container

    def _build_toc_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 8, 0, 0)

        self.toc_tree = QTreeWidget()
        self.toc_tree.setHeaderHidden(True)
        self.toc_tree.itemClicked.connect(self._on_toc_item_clicked)
        layout.addWidget(self.toc_tree)
        return panel

    def _build_search_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 0)
        layout.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索全书…")
        # 输入即搜，不需要额外按回车，体验更顺手
        self.search_input.textChanged.connect(self._on_search_text_changed)

        self.search_results_list = QListWidget()
        self.search_results_list.setWordWrap(True)
        self.search_results_list.itemClicked.connect(self._on_search_result_clicked)

        self.search_status_label = QLabel("")

        layout.addWidget(self.search_input)
        layout.addWidget(self.search_status_label)
        layout.addWidget(self.search_results_list, stretch=1)
        return panel

    def _build_notes_list_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(0)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(14, 0, 10, 6)

        self.notes_header_label = QLabel("笔记")
        header_row.addWidget(self.notes_header_label)
        header_row.addStretch()

        export_btn = QPushButton("导出")
        export_btn.setToolTip("导出全部笔记为 Markdown")
        export_btn.clicked.connect(self._export_notes)
        self.btn_export_notes = export_btn
        header_row.addWidget(export_btn)

        header_widget = QWidget()
        header_widget.setLayout(header_row)
        layout.addWidget(header_widget)

        self.notes_list = QListWidget()
        self.notes_list.setWordWrap(True)
        self.notes_list.itemClicked.connect(self._on_notes_list_item_clicked)
        layout.addWidget(self.notes_list, stretch=1)
        return panel

    def _build_note_drawer(self) -> QWidget:
        """右侧笔记编辑抽屉"""
        self.note_drawer = QWidget()
        self.note_drawer.setFixedWidth(300)
        self.note_drawer.setVisible(False)
        layout = QVBoxLayout(self.note_drawer)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        header_row = QHBoxLayout()
        self.note_drawer_title = QLabel("笔记")
        self.btn_close_note = QPushButton("✕")
        self.btn_close_note.clicked.connect(self._close_note_drawer)
        header_row.addWidget(self.note_drawer_title)
        header_row.addStretch()
        header_row.addWidget(self.btn_close_note)
        layout.addLayout(header_row)

        self.note_quote_label = QLabel("")
        self.note_quote_label.setWordWrap(True)
        layout.addWidget(self.note_quote_label)

        self.note_edit = QTextEdit()
        self.note_edit.setPlaceholderText("写下你的想法…")
        layout.addWidget(self.note_edit, stretch=1)

        self.btn_save_note = QPushButton("保存笔记")
        self.btn_save_note.clicked.connect(self._save_note)
        layout.addWidget(self.btn_save_note)
        return self.note_drawer

    def _build_shortcuts(self) -> None:
        act_next = QAction(self)
        act_next.setShortcut(QKeySequence(Qt.Key.Key_Right))
        act_next.triggered.connect(self.next_chapter)
        self.addAction(act_next)

        act_prev = QAction(self)
        act_prev.setShortcut(QKeySequence(Qt.Key.Key_Left))
        act_prev.triggered.connect(self.prev_chapter)
        self.addAction(act_prev)

        act_open = QAction(self)
        act_open.setShortcut(QKeySequence.StandardKey.Open)  # Cmd+O on mac
        act_open.triggered.connect(self.open_file_dialog)
        self.addAction(act_open)

        # Cmd+F 呼出搜索，跟系统习惯一致
        act_find = QAction(self)
        act_find.setShortcut(QKeySequence.StandardKey.Find)
        act_find.triggered.connect(self._toggle_search_sidebar)
        self.addAction(act_find)

    # ------------------------------------------------------------------
    # 主题（深色/浅色）
    # ------------------------------------------------------------------

    def _on_theme_changed(self, theme: Theme) -> None:
        self._apply_theme(theme)
        self._apply_reader_dark_mode()

    def _apply_theme(self, t: Theme) -> None:
        """把当前主题的颜色 token 灌进所有手动 setStyleSheet 的控件里。
        初次构建界面时调用一次，切换深色/浅色时再整体调用一次。"""

        self.main_splitter.setStyleSheet(
            f"QSplitter::handle {{ background-color: {t.border}; }}"
        )
        self.web_view.setStyleSheet(f"background-color: {t.reader_bg};")

        self.toolbar.setStyleSheet(f"""
            QWidget {{ background-color: {t.bg_toolbar}; border-bottom: 1px solid {t.border}; }}
            QPushButton {{
                border: none; background: transparent;
                font-size: 16px; color: {t.text_secondary}; padding: 0 14px;
            }}
            QPushButton:hover {{ color: {t.text}; }}
            QPushButton:disabled {{ color: {t.text_muted}; }}
            QPushButton:checked {{ color: {t.text}; font-weight: bold; }}
            QLabel#title {{ font-size: 13px; color: {t.text_secondary}; font-weight: 500; }}
        """)
        self.btn_theme.setText("☀️" if t.is_dark else "🌙")

        self.sidebar_container.setStyleSheet(
            f"background-color: {t.bg_sidebar}; border-right: 1px solid {t.border};"
        )

        self.toc_tree.setStyleSheet(f"""
            QTreeWidget {{ border: none; background-color: transparent; font-size: 13px; color: {t.text}; }}
            QTreeWidget::item {{ padding: 5px 4px; color: {t.text}; }}
            QTreeWidget::item:selected {{ background-color: {t.bg_selected}; color: {t.text}; }}
        """)

        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid {t.border_input}; border-radius: 6px;
                padding: 6px 10px; font-size: 13px;
                background: {t.bg_input}; color: {t.text};
            }}
        """)
        self.search_results_list.setStyleSheet(f"""
            QListWidget {{ border: none; background-color: transparent; font-size: 12px; color: {t.text}; }}
            QListWidget::item {{ padding: 8px 4px; border-bottom: 1px solid {t.border}; color: {t.text}; }}
            QListWidget::item:selected {{ background-color: {t.bg_selected}; color: {t.text}; }}
        """)
        self.search_status_label.setStyleSheet(f"color: {t.text_muted}; font-size: 11px;")

        self.notes_header_label.setStyleSheet(
            f"font-size: 12px; color: {t.text_muted}; font-weight: 500;"
        )
        self.btn_export_notes.setStyleSheet(f"""
            QPushButton {{
                border: 1px solid {t.border_input}; border-radius: 5px;
                padding: 2px 10px; font-size: 11px; color: {t.text_secondary};
                background: {t.bg_input};
            }}
            QPushButton:hover {{ background: {t.bg_hover}; }}
            QPushButton:disabled {{ color: {t.text_muted}; border-color: {t.border}; }}
        """)
        self.notes_list.setStyleSheet(f"""
            QListWidget {{ border: none; background-color: transparent; font-size: 12px; color: {t.text}; }}
            QListWidget::item {{ padding: 8px 12px; border-bottom: 1px solid {t.border}; color: {t.text}; }}
            QListWidget::item:selected {{ background-color: {t.bg_selected}; color: {t.text}; }}
        """)

        self.note_drawer.setStyleSheet(
            f"background: {t.bg_sidebar}; border-left: 1px solid {t.border};"
        )
        self.note_drawer_title.setStyleSheet(
            f"font-size: 14px; font-weight: 600; color: {t.text};"
        )
        self.btn_close_note.setStyleSheet(
            f"border: none; background: transparent; color: {t.text_muted}; font-size: 14px;"
        )
        self.note_quote_label.setStyleSheet(
            f"font-size: 12px; color: {t.text_secondary}; font-style: italic; "
            f"background: {t.bg_hover}; border-radius: 4px; padding: 8px;"
        )
        self.note_edit.setStyleSheet(f"""
            QTextEdit {{
                border: 1px solid {t.border_input}; border-radius: 6px;
                padding: 8px; font-size: 13px; background: {t.bg_input}; color: {t.text};
            }}
        """)
        self.btn_save_note.setStyleSheet(f"""
            QPushButton {{
                background: {t.button_primary_bg}; color: {t.button_primary_text}; border: none;
                border-radius: 6px; padding: 8px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {t.button_primary_hover}; }}
        """)

    def _apply_reader_dark_mode(self) -> None:
        """给当前已加载的章节页面注入/移除夜间模式样式。
        不用重新加载整个页面（会丢失滚动位置），只是往页面里插一个
        <style> 标签（见 _READER_DARK_CSS_JS / _READER_LIGHT_CSS_JS）。"""
        if self.book is None:
            return
        js = _READER_DARK_CSS_JS if self._theme_mgr.current.is_dark else _READER_LIGHT_CSS_JS
        self.web_view.page().runJavaScript(js)

    # ------------------------------------------------------------------
    # 侧边栏显示/隐藏逻辑
    # ------------------------------------------------------------------

    def _toggle_toc_sidebar(self) -> None:
        # 目录和搜索互斥：点目录时把搜索按钮状态复位，反之亦然
        if self.btn_toc.isChecked():
            self.btn_search.setChecked(False)
            self.sidebar_stack.setCurrentIndex(SIDEBAR_TOC)
            self.sidebar_container.setVisible(True)
        else:
            self.sidebar_container.setVisible(False)

    def _toggle_search_sidebar(self) -> None:
        if self.btn_search.isChecked():
            self.btn_toc.setChecked(False)
            self.sidebar_stack.setCurrentIndex(SIDEBAR_SEARCH)
            self.sidebar_container.setVisible(True)
            self.search_input.setFocus()
        else:
            self.sidebar_container.setVisible(False)

    # ------------------------------------------------------------------
    # 文件打开 / 章节导航
    # ------------------------------------------------------------------

    def open_file_dialog(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "打开 epub 文件", str(Path.home()), "EPUB 文件 (*.epub)"
        )
        if file_path:
            self.open_book(file_path)

    def open_book(self, epub_path: str) -> None:
        try:
            self.book = load_epub(epub_path)
        except Exception as e:
            self.title_label.setText(f"打开失败: {e}")
            return

        if self.book.chapter_count() == 0:
            self.title_label.setText("这本书没有可读取的章节")
            return

        # 关闭上一本书的 store，打开新的
        if self.highlight_store is not None:
            self.highlight_store.close()
        self.highlight_store = HighlightStore(epub_path)
        self.epub_path = str(Path(epub_path).resolve())

        self.current_chapter_idx = 0
        self.btn_prev.setEnabled(False)
        self.btn_next.setEnabled(self.book.chapter_count() > 1)

        self._populate_toc()
        self.search_index = build_search_index(self.book)
        self.search_results_list.clear()
        self.search_input.clear()
        self.search_status_label.setText("")

        self.btn_info.setEnabled(True)
        self.btn_edit.setEnabled(True)
        self._refresh_notes_list()
        self._render_current_chapter()

    def _render_current_chapter(self) -> None:
        if self.book is None:
            return
        chapter = self.book.chapters[self.current_chapter_idx]
        self.title_label.setText(f"{self.book.title} · {chapter.title}")
        self.web_view.load(QUrl.fromLocalFile(str(chapter.file_path)))

        self.btn_prev.setEnabled(self.current_chapter_idx > 0)
        self.btn_next.setEnabled(
            self.current_chapter_idx < self.book.chapter_count() - 1
        )
        self._highlight_current_toc_item()

    def next_chapter(self) -> None:
        if self.book is None:
            return
        if self.current_chapter_idx < self.book.chapter_count() - 1:
            self.current_chapter_idx += 1
            self._render_current_chapter()

    def prev_chapter(self) -> None:
        if self.book is None:
            return
        if self.current_chapter_idx > 0:
            self.current_chapter_idx -= 1
            self._render_current_chapter()

    def go_to_chapter(self, chapter_index: int) -> None:
        if self.book is None:
            return
        if 0 <= chapter_index < self.book.chapter_count():
            self.current_chapter_idx = chapter_index
            self._render_current_chapter()

    def _on_page_loaded(self, ok: bool) -> None:
        """
        每次章节页面加载完成后的统一入口：
          1. 注入排版 CSS
          2. 注入 footnotes.js（脚注挪到章末 + 跳转/返回），
             必须在滚动监听之前跑，否则"到底"的判断会用到脚注
             搬家前的旧页面高度
          3. 注入滚动到底部监听（自动翻页）
          4. 注入 highlighter.js（选中文字 → 气泡菜单）
          5. 还原该章节已保存的高亮
        """
        if not ok:
            return
        page = self.web_view.page()
        page.runJavaScript(_READER_CSS_JS)

        if _FOOTNOTES_JS_PATH.exists():
            fn_js = _FOOTNOTES_JS_PATH.read_text(encoding="utf-8")
            page.runJavaScript(fn_js)

        page.runJavaScript(_PULL_TO_NEXT_JS)

        if _HIGHLIGHTER_JS_PATH.exists():
            hl_js = _HIGHLIGHTER_JS_PATH.read_text(encoding="utf-8")
            page.runJavaScript(hl_js)

        self._apply_reader_dark_mode()
        self._restore_highlights()

    def _restore_highlights(self) -> None:
        """把当前章节的已保存高亮数据传给 JS 还原"""
        if self.highlight_store is None or self.book is None:
            return
        highlights_json = self.highlight_store.highlights_to_js_json(
            book_path=self.epub_path,
            chapter_index=self.current_chapter_idx,
        )
        self.web_view.page().runJavaScript(
            f"restoreHighlights({highlights_json});"
        )

    def _on_highlight_message(self, payload: dict) -> None:
        """
        处理 JS 上报的高亮操作消息：
          创建: {action:"create", containerXpath, startOffset, endOffset,
                 selectedText, color, tempId}
          删除: {action:"delete", id}   id 可能是数字或字符串形式的数字
        """
        if self.highlight_store is None:
            return

        action = payload.get("action")

        if action == "create":
            h = Highlight(
                id=None,
                book_path=self.epub_path,
                chapter_index=self.current_chapter_idx,
                container_xpath=payload["containerXpath"],
                start_offset=payload["startOffset"],
                end_offset=payload["endOffset"],
                selected_text=payload["selectedText"],
                color=payload.get("color", "yellow"),
            )
            saved = self.highlight_store.add(h)
            temp_id = payload.get("tempId", "")
            # 把 DOM 里的 tempId 换成真实数据库 id，删除时才能正确定位
            self.web_view.page().runJavaScript(
                f"updateHighlightId('{temp_id}', {saved.id});"
            )
            self._refresh_notes_list()
            # 用户点的是「✎ 笔记」而不是普通颜色圆点：
            # 高亮已经写库拿到真实 id，直接打开笔记面板，不用等 JS 再报一次 open_note
            if payload.get("openNoteAfter"):
                self._open_note_drawer(saved.id)

        elif action == "update_color":
            raw_id = payload.get("id")
            try:
                db_id = int(float(str(raw_id)))
                self.highlight_store.update_color(db_id, payload.get("color", "yellow"))
            except (ValueError, TypeError):
                pass

        elif action == "open_note":
            raw_id = payload.get("id")
            try:
                db_id = int(float(str(raw_id)))
                self._open_note_drawer(db_id)
            except (ValueError, TypeError):
                pass

        elif action == "delete":
            raw_id = payload.get("id")
            try:
                db_id = int(float(str(raw_id)))
                self.highlight_store.delete(db_id)
                self._refresh_notes_list()
            except (ValueError, TypeError):
                pass

    def _on_chapter_scrolled_to_bottom(self) -> None:
        if self.book is None:
            return
        if self.current_chapter_idx < self.book.chapter_count() - 1:
            self.next_chapter()

    # ------------------------------------------------------------------
    # 目录侧边栏
    # ------------------------------------------------------------------

    def _populate_toc(self) -> None:
        """把 book.toc（嵌套结构）渲染成 QTreeWidget 的树"""
        self.toc_tree.clear()
        if self.book is None:
            return

        def add_entries(parent_item, entries: list[TocEntry]):
            for entry in entries:
                tree_item = QTreeWidgetItem([entry.title])
                # 用 Qt.UserRole 把 chapter_index 存进树节点，点击时直接取出来用
                tree_item.setData(0, Qt.ItemDataRole.UserRole, entry.chapter_index)
                if parent_item is None:
                    self.toc_tree.addTopLevelItem(tree_item)
                else:
                    parent_item.addChild(tree_item)
                add_entries(tree_item, entry.children)

        add_entries(None, self.book.toc)
        self.toc_tree.expandAll()

    def _on_toc_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        chapter_index = item.data(0, Qt.ItemDataRole.UserRole)
        if chapter_index is not None:
            self.go_to_chapter(chapter_index)

    def _highlight_current_toc_item(self) -> None:
        """翻章节后，让目录树里对应的节点高亮，方便用户知道自己读到哪了"""

        def find_and_select(items_iter) -> bool:
            for item in items_iter:
                if item.data(0, Qt.ItemDataRole.UserRole) == self.current_chapter_idx:
                    self.toc_tree.setCurrentItem(item)
                    return True
                child_count = item.childCount()
                if child_count and find_and_select(
                    item.child(i) for i in range(child_count)
                ):
                    return True
            return False

        top_items = [
            self.toc_tree.topLevelItem(i)
            for i in range(self.toc_tree.topLevelItemCount())
        ]
        find_and_select(top_items)

    # ------------------------------------------------------------------
    # 搜索侧边栏
    # ------------------------------------------------------------------

    def _on_search_text_changed(self, text: str) -> None:
        if self.book is None:
            return

        results = search(self.search_index, text, self.book)
        self._last_search_results = results

        self.search_results_list.clear()
        if not text.strip():
            self.search_status_label.setText("")
            return

        self.search_status_label.setText(f"{len(results)} 处结果")
        for r in results:
            label = f"{r.chapter_title}\n{r.snippet}"
            list_item = QListWidgetItem(label)
            list_item.setData(Qt.ItemDataRole.UserRole, r)
            self.search_results_list.addItem(list_item)

    def _on_search_result_clicked(self, item: QListWidgetItem) -> None:
        result: SearchResult = item.data(Qt.ItemDataRole.UserRole)
        if result is None:
            return
        self.go_to_chapter(result.chapter_index)

    # ------------------------------------------------------------------
    # 侧边栏切换
    # ------------------------------------------------------------------

    def _toggle_toc_sidebar(self) -> None:
        if self.btn_toc.isChecked():
            self.btn_search.setChecked(False)
            self.btn_notes_list.setChecked(False)
            self.sidebar_stack.setCurrentIndex(SIDEBAR_TOC)
            self.sidebar_container.setVisible(True)
        else:
            self.sidebar_container.setVisible(False)

    def _toggle_search_sidebar(self) -> None:
        if self.btn_search.isChecked():
            self.btn_toc.setChecked(False)
            self.btn_notes_list.setChecked(False)
            self.sidebar_stack.setCurrentIndex(SIDEBAR_SEARCH)
            self.sidebar_container.setVisible(True)
            self.search_input.setFocus()
        else:
            self.sidebar_container.setVisible(False)

    def _toggle_notes_sidebar(self) -> None:
        if self.btn_notes_list.isChecked():
            self.btn_toc.setChecked(False)
            self.btn_search.setChecked(False)
            self.sidebar_stack.setCurrentIndex(SIDEBAR_NOTES)
            self.sidebar_container.setVisible(True)
            self._refresh_notes_list()
        else:
            self.sidebar_container.setVisible(False)

    # ------------------------------------------------------------------
    # 导航
    # ------------------------------------------------------------------

    def _go_back_to_library(self) -> None:
        if self._on_back_to_library:
            self._on_back_to_library()

    # ------------------------------------------------------------------
    # 笔记抽屉（右侧）
    # ------------------------------------------------------------------

    def _open_note_drawer(self, highlight_id: int) -> None:
        if self.highlight_store is None:
            return
        highlights = self.highlight_store.get_all(self.epub_path)
        h = next((x for x in highlights if x.id == highlight_id), None)
        if h is None:
            return
        self._current_note_id = highlight_id
        self.note_quote_label.setText(f"\u201c{h.selected_text[:120]}\u201d")
        self.note_edit.setPlainText(h.note or "")
        self.note_drawer.setVisible(True)
        # 仅 setVisible 不够：splitter 记录的这一格宽度从初始化起就是 0，
        # 光"可见"但宽度为零等于看不见，必须显式重新分配宽度
        sizes = self.main_splitter.sizes()
        sizes[2] = 300
        self.main_splitter.setSizes(sizes)
        self.note_edit.setFocus()

    def _close_note_drawer(self) -> None:
        self.note_drawer.setVisible(False)
        sizes = self.main_splitter.sizes()
        sizes[2] = 0
        self.main_splitter.setSizes(sizes)
        self._current_note_id = None

    def _save_note(self) -> None:
        if self.highlight_store is None or self._current_note_id is None:
            return
        self.highlight_store.update_note(
            self._current_note_id,
            self.note_edit.toPlainText().strip(),
        )
        self._refresh_notes_list()
        self._close_note_drawer()

    # ------------------------------------------------------------------
    # 笔记列表
    # ------------------------------------------------------------------

    def _refresh_notes_list(self) -> None:
        self.notes_list.clear()
        if self.highlight_store is None:
            return
        highlights = self.highlight_store.get_all(self.epub_path)
        if not highlights:
            placeholder = QListWidgetItem("暂无笔记\n选中文字并高亮后可添加")
            placeholder.setFlags(placeholder.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            placeholder.setForeground(Qt.GlobalColor.gray)
            self.notes_list.addItem(placeholder)
            return
        for h in highlights:
            chapter_title = ""
            if self.book and 0 <= h.chapter_index < self.book.chapter_count():
                chapter_title = self.book.chapters[h.chapter_index].title
            quote = h.selected_text[:60] + ("…" if len(h.selected_text) > 60 else "")
            text = f"{chapter_title}\n\u201c{quote}\u201d"
            if h.note:
                note_preview = h.note[:80] + ("…" if len(h.note) > 80 else "")
                text += f"\n{note_preview}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, h.id)
            self.notes_list.addItem(item)

    def _on_notes_list_item_clicked(self, item: QListWidgetItem) -> None:
        highlight_id = item.data(Qt.ItemDataRole.UserRole)
        if highlight_id is not None:
            self._open_note_drawer(highlight_id)

    # ------------------------------------------------------------------
    # 元数据 / 编辑器入口
    # ------------------------------------------------------------------

    def _open_meta_editor(self) -> None:
        if not self.epub_path:
            return
        from ui.meta_editor import MetaEditorDialog
        dlg = MetaEditorDialog(self.epub_path, parent=self)
        if dlg.exec() and dlg.saved_meta:
            # 同步内存里的 book 对象，笔记列表/导出等地方用到 book.title 时才不会显示旧标题
            self.book.title = dlg.saved_meta.title
            chapter = self.book.chapters[self.current_chapter_idx]
            self.title_label.setText(f"{dlg.saved_meta.title} · {chapter.title}")
            try:
                from core.library import refresh_book_metadata
                refresh_book_metadata(self.epub_path)
            except Exception:
                pass

    def _open_epub_editor(self) -> None:
        if not self.epub_path or self.book is None:
            return
        from ui.epub_editor import EpubEditorWindow
        editor = EpubEditorWindow(self.epub_path, self.book, parent=self)
        editor.show()

    def _export_notes(self) -> None:
        if self.highlight_store is None or self.book is None or not self.epub_path:
            return

        highlights = self.highlight_store.get_all(self.epub_path)
        if not highlights:
            QMessageBox.information(self, "导出笔记", "这本书还没有任何笔记或高亮。")
            return

        default_name = f"{self.book.title}-笔记.md"
        save_path, _ = QFileDialog.getSaveFileName(
            self, "导出笔记", str(Path.home() / default_name),
            "Markdown 文件 (*.md)"
        )
        if not save_path:
            return

        try:
            export_to_file(self.book, self.highlight_store, self.epub_path, save_path)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"写入文件时出错：\n{e}")
            return

        QMessageBox.information(self, "导出成功", f"笔记已导出到：\n{save_path}")

