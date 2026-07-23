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

SIDEBAR_TOC    = 0
SIDEBAR_SEARCH = 1
SIDEBAR_NOTES  = 2

# JS → Python 消息前缀（高亮操作）
_HL_PREFIX = "MARGINALIA_HL::"

# highlighter.js 的路径（相对于项目根目录）
_HIGHLIGHTER_JS_PATH = Path(__file__).parent.parent / "assets" / "web" / "highlighter.js"
_FOOTNOTES_JS_PATH = Path(__file__).parent.parent / "assets" / "web" / "footnotes.js"

# JS 端通过 console.log 这个固定前缀的消息向 Python 上报"滚动到底部"，
# Python 侧用自定义 QWebEnginePage 拦截 javaScriptConsoleMessage 来接收。
#
# 为什么不用 QWebChannel：
#   QWebChannel 需要加载 qwebchannel.js 这个 Qt 自带的桥接脚本，
#   但实测发现当前 PySide6 wheel 并没有把这个文件打进 Qt 资源系统里
#   （:/qtwebchannel/qwebchannel.js 不存在），意味着要自己额外维护这份
#   第三方 JS 文件。对于"滚动到底部"这种单向、低频的简单信号，
#   用 console.log + 拦截 console message 是 Qt 官方文档认可的轻量做法，
#   不引入任何额外文件依赖。
_BOTTOM_SIGNAL = "MARGINALIA_REACHED_BOTTOM"

# 注入到每个章节页面的 JS：监听滚动事件，到底部时打印约定好的信号字符串。
# 用阈值(20px)容错，避免因为浮点像素误差导致永远卡在差一点点到底的状态。
#
# 关键点：碰到底部阈值不能立刻触发翻页——用户往下滚动的过程中，
# 视口底边瞬间越过阈值的那一刻，最后一行往往还没来得及看完就被跳走了。
# 所以加一个"停留时间"（DWELL_MS）：碰到底部后先排个定时器，
# 如果这段时间内用户又往回滚了（不再处于底部），就取消定时器；
# 只有真的在底部停留够时间，才真正上报"到底了"。
_SCROLL_WATCHER_JS_TEMPLATE = """
(function() {
    if (window.__marginaliaScrollWatcherInstalled) { return; }
    window.__marginaliaScrollWatcherInstalled = true;

    const DWELL_MS = 1100;   // 停留多久才判定为"真的读完了，可以翻页"
    let notifiedBottom = false;
    let bottomTimer = null;

    function checkBottom() {
        const scrollTop = window.scrollY;
        const viewportHeight = window.innerHeight;
        const fullHeight = document.documentElement.scrollHeight;
        const threshold = 20;
        const atBottom = scrollTop + viewportHeight >= fullHeight - threshold;

        if (atBottom) {
            if (bottomTimer === null && !notifiedBottom) {
                bottomTimer = setTimeout(() => {
                    bottomTimer = null;
                    notifiedBottom = true;
                    console.log("__BOTTOM_SIGNAL__");
                }, DWELL_MS);
            }
        } else {
            if (bottomTimer !== null) {
                clearTimeout(bottomTimer);
                bottomTimer = null;
            }
            notifiedBottom = false;
        }
    }

    window.addEventListener('scroll', checkBottom);

    // 如果整页内容比视口还短（一页就能放下，不会触发滚动事件），
    // 同样要走一遍停留计时，不能因为它是"初始检查"就绕过 DWELL_MS。
    setTimeout(checkBottom, 600);
})();
"""
# 用简单字符串替换而不是 f-string/str.format，
# 因为 JS 代码本身全是花括号，跟 f-string/format 的转义语法冲突，
# 用 .replace() 这种最朴素的方式反而最不容易出错
_SCROLL_WATCHER_JS = _SCROLL_WATCHER_JS_TEMPLATE.replace(
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

        self._build_ui()
        self._build_shortcuts()
        self.sidebar_container.setVisible(False)

    # ------------------------------------------------------------------
    # UI 搭建
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_toolbar())

        # --- 主体：侧边栏 + 阅读区域，用 Splitter 让宽度可拖拽 ---
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter = self.main_splitter
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background-color: #e5e5e5; }")

        self.sidebar_container = self._build_sidebar()
        self.web_view = QWebEngineView()
        self.web_view.setStyleSheet("background-color: #fdfdfb;")
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
        toolbar.setStyleSheet(
            """
            QWidget { background-color: #fafafa; border-bottom: 1px solid #e5e5e5; }
            QPushButton {
                border: none; background: transparent;
                font-size: 16px; color: #333; padding: 0 14px;
            }
            QPushButton:hover { color: #000; }
            QPushButton:disabled { color: #ccc; }
            QPushButton:checked { color: #000; font-weight: bold; }
            QLabel#title { font-size: 13px; color: #444; font-weight: 500; }
            """
        )
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(8, 0, 8, 0)

        self.btn_back = QPushButton("‹ 书库")
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
        self.btn_search = QPushButton("⌕")
        self.btn_search.setCheckable(True)
        self.btn_search.setToolTip("搜索")
        self.btn_search.clicked.connect(self._toggle_search_sidebar)

        # 笔记列表按钮
        self.btn_notes_list = QPushButton("𝄏")
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

        self.btn_edit = QPushButton("✏")
        self.btn_edit.setToolTip("编辑内容")
        self.btn_edit.setEnabled(False)
        self.btn_edit.clicked.connect(self._open_epub_editor)

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

        return toolbar

    def _build_sidebar(self) -> QWidget:
        """
        侧边栏容器：内部用 QStackedWidget 在"目录树"和"搜索面板"之间切换，
        外层包一层是为了方便整体设置宽度和隐藏/显示。
        """
        container = QWidget()
        container.setMinimumWidth(200)
        container.setMaximumWidth(420)
        container.setStyleSheet(
            "background-color: #f5f5f3; border-right: 1px solid #e5e5e5;"
        )
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
        self.toc_tree.setStyleSheet(
            """
            QTreeWidget { border: none; background-color: transparent; font-size: 13px; color: #333; }
            QTreeWidget::item { padding: 5px 4px; color: #333; }
            QTreeWidget::item:selected { background-color: #e8e6df; color: #000; }
            """
        )
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
        self.search_input.setStyleSheet(
            """
            QLineEdit {
                border: 1px solid #d8d6cf; border-radius: 6px;
                padding: 6px 10px; font-size: 13px; background: white;
            }
            """
        )
        # 输入即搜，不需要额外按回车，体验更顺手
        self.search_input.textChanged.connect(self._on_search_text_changed)

        self.search_results_list = QListWidget()
        self.search_results_list.setStyleSheet(
            """
            QListWidget { border: none; background-color: transparent; font-size: 12px; color: #333; }
            QListWidget::item { padding: 8px 4px; border-bottom: 1px solid #ebe9e3; color: #333; }
            QListWidget::item:selected { background-color: #e8e6df; color: #000; }
            """
        )
        self.search_results_list.setWordWrap(True)
        self.search_results_list.itemClicked.connect(self._on_search_result_clicked)

        self.search_status_label = QLabel("")
        self.search_status_label.setStyleSheet("color: #888; font-size: 11px;")

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

        header = QLabel("笔记")
        header.setStyleSheet(
            "font-size: 12px; color: #888; font-weight: 500;"
        )
        header_row.addWidget(header)
        header_row.addStretch()

        export_btn = QPushButton("导出")
        export_btn.setToolTip("导出全部笔记为 Markdown")
        export_btn.setStyleSheet("""
            QPushButton {
                border: 1px solid #d8d6cf; border-radius: 5px;
                padding: 2px 10px; font-size: 11px; color: #555;
                background: white;
            }
            QPushButton:hover { background: #f0eeea; }
            QPushButton:disabled { color: #ccc; border-color: #e5e3dd; }
        """)
        export_btn.clicked.connect(self._export_notes)
        self.btn_export_notes = export_btn
        header_row.addWidget(export_btn)

        header_widget = QWidget()
        header_widget.setLayout(header_row)
        layout.addWidget(header_widget)

        self.notes_list = QListWidget()
        self.notes_list.setStyleSheet("""
            QListWidget { border: none; background-color: transparent; font-size: 12px; }
            QListWidget::item { padding: 8px 12px; border-bottom: 1px solid #ebe9e3; }
            QListWidget::item:selected { background-color: #e8e6df; color: #000; }
        """)
        self.notes_list.setWordWrap(True)
        self.notes_list.itemClicked.connect(self._on_notes_list_item_clicked)
        layout.addWidget(self.notes_list, stretch=1)
        return panel

    def _build_note_drawer(self) -> QWidget:
        """右侧笔记编辑抽屉"""
        self.note_drawer = QWidget()
        self.note_drawer.setFixedWidth(300)
        self.note_drawer.setVisible(False)
        self.note_drawer.setStyleSheet(
            "background: #faf9f7; border-left: 1px solid #e5e5e5;"
        )
        layout = QVBoxLayout(self.note_drawer)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        header_row = QHBoxLayout()
        header_lbl = QLabel("笔记")
        header_lbl.setStyleSheet("font-size: 14px; font-weight: 600; color: #1a1a1a;")
        close_btn = QPushButton("✕")
        close_btn.setStyleSheet(
            "border: none; background: transparent; color: #aaa; font-size: 14px;"
        )
        close_btn.clicked.connect(self._close_note_drawer)
        header_row.addWidget(header_lbl)
        header_row.addStretch()
        header_row.addWidget(close_btn)
        layout.addLayout(header_row)

        self.note_quote_label = QLabel("")
        self.note_quote_label.setWordWrap(True)
        self.note_quote_label.setStyleSheet(
            "font-size: 12px; color: #888; font-style: italic; "
            "background: #f0ede6; border-radius: 4px; padding: 8px;"
        )
        layout.addWidget(self.note_quote_label)

        self.note_edit = QTextEdit()
        self.note_edit.setPlaceholderText("写下你的想法…")
        self.note_edit.setStyleSheet("""
            QTextEdit {
                border: 1px solid #d8d6cf; border-radius: 6px;
                padding: 8px; font-size: 13px; background: white;
            }
        """)
        layout.addWidget(self.note_edit, stretch=1)

        save_btn = QPushButton("保存笔记")
        save_btn.setStyleSheet("""
            QPushButton {
                background: #2c2c2c; color: white; border: none;
                border-radius: 6px; padding: 8px; font-size: 13px;
            }
            QPushButton:hover { background: #111; }
        """)
        save_btn.clicked.connect(self._save_note)
        layout.addWidget(save_btn)
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

        page.runJavaScript(_SCROLL_WATCHER_JS)

        if _HIGHLIGHTER_JS_PATH.exists():
            hl_js = _HIGHLIGHTER_JS_PATH.read_text(encoding="utf-8")
            page.runJavaScript(hl_js)

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

