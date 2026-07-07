"""
main_window.py — Marginalia 主窗口

布局：
    顶部工具栏
    ├── 左侧边栏（目录 / 搜索 / 笔记列表，三选一）
    ├── 中间阅读区（QWebEngineView）
    └── 右侧笔记抽屉（默认隐藏，点击高亮上的✎打开）
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

# 左侧侧边栏的三个 tab
SIDEBAR_TOC    = 0
SIDEBAR_SEARCH = 1
SIDEBAR_NOTES  = 2

_HL_PREFIX           = "MARGINALIA_HL::"
_HIGHLIGHTER_JS_PATH = Path(__file__).parent.parent / "assets" / "web" / "highlighter.js"
_BOTTOM_SIGNAL       = "MARGINALIA_REACHED_BOTTOM"

_SCROLL_WATCHER_JS_TEMPLATE = """
(function() {
    if (window.__marginaliaScrollWatcherInstalled) { return; }
    window.__marginaliaScrollWatcherInstalled = true;
    let notifiedBottom = false;
    function checkBottom() {
        const atBottom = window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 20;
        if (atBottom && !notifiedBottom) { notifiedBottom = true; console.log("__BOTTOM_SIGNAL__"); }
        else if (!atBottom) { notifiedBottom = false; }
    }
    window.addEventListener('scroll', checkBottom);
    setTimeout(checkBottom, 600);
})();
"""
_SCROLL_WATCHER_JS = _SCROLL_WATCHER_JS_TEMPLATE.replace("__BOTTOM_SIGNAL__", _BOTTOM_SIGNAL)


class ReaderPage(QWebEnginePage):
    def __init__(self, on_reach_bottom, on_highlight_msg, parent=None) -> None:
        super().__init__(parent)
        self._on_reach_bottom  = on_reach_bottom
        self._on_highlight_msg = on_highlight_msg

    def javaScriptConsoleMessage(self, level, message, line_number, source_id) -> None:
        if message == _BOTTOM_SIGNAL:
            self._on_reach_bottom()
        elif message.startswith(_HL_PREFIX):
            try:
                self._on_highlight_msg(json.loads(message[len(_HL_PREFIX):]))
            except json.JSONDecodeError:
                pass


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Marginalia")
        self.resize(1200, 1100)

        self.book: EpubBook | None        = None
        self.epub_path: str               = ""
        self.current_chapter_idx: int     = 0
        self.search_index: list[ChapterText]      = []
        self._last_search_results: list[SearchResult] = []
        self.highlight_store: HighlightStore | None   = None
        self._active_note_id: int | None  = None   # 当前抽屉正在编辑的高亮 id

        self._build_ui()
        self._build_shortcuts()
        self.sidebar_container.setVisible(False)
        self.note_drawer.setVisible(False)

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

        # 主体：左侧边栏 | 阅读区 | 右侧笔记抽屉
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setHandleWidth(1)
        self.main_splitter.setStyleSheet("QSplitter::handle { background-color: #e5e5e5; }")

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

        self.note_drawer = self._build_note_drawer()

        self.main_splitter.addWidget(self.sidebar_container)
        self.main_splitter.addWidget(self.web_view)
        self.main_splitter.addWidget(self.note_drawer)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setSizes([260, 700, 280])

        root_layout.addWidget(self.main_splitter, stretch=1)

    def _build_toolbar(self) -> QWidget:
        toolbar = QWidget()
        toolbar.setFixedHeight(44)
        toolbar.setStyleSheet("""
            QWidget { background-color: #fafafa; border-bottom: 1px solid #e5e5e5; }
            QPushButton {
                border: none; background: transparent;
                font-size: 16px; color: #333; padding: 0 14px;
            }
            QPushButton:hover { color: #000; }
            QPushButton:disabled { color: #ccc; }
            QPushButton:checked { color: #000; font-weight: bold; }
            QLabel#title { font-size: 13px; color: #444; font-weight: 500; }
        """)
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(8, 0, 8, 0)

        self.btn_open = QPushButton("打开")
        self.btn_open.clicked.connect(self.open_file_dialog)

        self.btn_toc = QPushButton("☰")
        self.btn_toc.setCheckable(True)
        self.btn_toc.setToolTip("目录")
        self.btn_toc.clicked.connect(self._toggle_toc_sidebar)

        self.btn_search = QPushButton("⌕")
        self.btn_search.setCheckable(True)
        self.btn_search.setToolTip("搜索")
        self.btn_search.clicked.connect(self._toggle_search_sidebar)

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

        layout.addWidget(self.btn_open)
        layout.addWidget(self.btn_toc)
        layout.addWidget(self.btn_search)
        layout.addWidget(self.btn_notes_list)
        layout.addWidget(self.btn_prev)
        layout.addWidget(self.title_label, stretch=1)
        layout.addWidget(self.btn_next)
        return toolbar

    def _build_sidebar(self) -> QWidget:
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
        self.sidebar_stack.addWidget(self._build_toc_panel())     # 0
        self.sidebar_stack.addWidget(self._build_search_panel())  # 1
        self.sidebar_stack.addWidget(self._build_notes_panel())   # 2
        layout.addWidget(self.sidebar_stack)
        return container

    def _build_toc_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 8, 0, 0)

        self.toc_tree = QTreeWidget()
        self.toc_tree.setHeaderHidden(True)
        self.toc_tree.setStyleSheet("""
            QTreeWidget { border: none; background-color: transparent; font-size: 13px; color: #333; }
            QTreeWidget::item { padding: 5px 4px; color: #333; }
            QTreeWidget::item:selected { background-color: #e8e6df; color: #000; }
        """)
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
        self.search_input.setStyleSheet("""
            QLineEdit {
                border: 1px solid #d8d6cf; border-radius: 6px;
                padding: 6px 10px; font-size: 13px; background: white;
            }
        """)
        self.search_input.textChanged.connect(self._on_search_text_changed)

        self.search_results_list = QListWidget()
        self.search_results_list.setStyleSheet("""
            QListWidget { border: none; background-color: transparent; font-size: 12px; color: #333; }
            QListWidget::item { padding: 8px 4px; border-bottom: 1px solid #ebe9e3; color: #333; }
            QListWidget::item:selected { background-color: #e8e6df; color: #000; }
        """)
        self.search_results_list.setWordWrap(True)
        self.search_results_list.itemClicked.connect(self._on_search_result_clicked)

        self.search_status_label = QLabel("")
        self.search_status_label.setStyleSheet("color: #888; font-size: 11px;")

        layout.addWidget(self.search_input)
        layout.addWidget(self.search_status_label)
        layout.addWidget(self.search_results_list, stretch=1)
        return panel

    def _build_notes_panel(self) -> QWidget:
        """左侧笔记列表面板：显示这本书所有高亮+批注，按章节分组"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(0)

        header = QLabel("全书笔记")
        header.setStyleSheet(
            "font-size: 12px; color: #888; padding: 0 12px 8px 12px; font-weight: 500;"
        )
        layout.addWidget(header)

        self.notes_list = QListWidget()
        self.notes_list.setStyleSheet("""
            QListWidget {
                border: none; background-color: transparent;
                font-size: 12px; color: #333;
            }
            QListWidget::item {
                padding: 10px 12px;
                border-bottom: 1px solid #ebe9e3;
                color: #333;
            }
            QListWidget::item:selected { background-color: #e8e6df; color: #000; }
        """)
        self.notes_list.setWordWrap(True)
        self.notes_list.itemClicked.connect(self._on_notes_list_item_clicked)
        layout.addWidget(self.notes_list, stretch=1)
        return panel

    def _build_note_drawer(self) -> QWidget:
        """右侧笔记编辑抽屉：显示原文摘录 + 多行文本输入框 + 保存按钮"""
        drawer = QWidget()
        drawer.setMinimumWidth(220)
        drawer.setMaximumWidth(380)
        drawer.setStyleSheet(
            "background-color: #faf9f6; border-left: 1px solid #e5e5e5;"
        )
        layout = QVBoxLayout(drawer)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # 抽屉标题栏：「笔记」+ 关闭按钮
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        drawer_title = QLabel("笔记")
        drawer_title.setStyleSheet("font-size: 14px; font-weight: 600; color: #222;")
        close_btn = QPushButton("✕")
        close_btn.setStyleSheet(
            "border: none; background: transparent; color: #999; font-size: 14px; padding: 0;"
        )
        close_btn.clicked.connect(self._close_note_drawer)
        title_row.addWidget(drawer_title)
        title_row.addStretch()
        title_row.addWidget(close_btn)
        layout.addLayout(title_row)

        # 原文摘录（只读，显示被高亮的那段文字，给用户上下文感）
        self.note_quote_label = QLabel("")
        self.note_quote_label.setWordWrap(True)
        self.note_quote_label.setStyleSheet("""
            font-size: 12px; color: #666; font-style: italic;
            background: #f0ede6; border-radius: 6px;
            padding: 8px 10px; line-height: 1.5;
            border-left: 3px solid #FFE066;
        """)
        self.note_quote_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.note_quote_label)

        # 笔记输入框
        self.note_edit = QTextEdit()
        self.note_edit.setPlaceholderText("在这里写下你的想法…")
        self.note_edit.setStyleSheet("""
            QTextEdit {
                border: 1px solid #d8d6cf; border-radius: 8px;
                padding: 8px; font-size: 13px;
                background: white; color: #333;
            }
        """)
        layout.addWidget(self.note_edit, stretch=1)

        # 保存按钮
        save_btn = QPushButton("保存笔记")
        save_btn.setStyleSheet("""
            QPushButton {
                background: #333; color: white; border: none;
                border-radius: 6px; padding: 8px 0; font-size: 13px;
            }
            QPushButton:hover { background: #111; }
        """)
        save_btn.clicked.connect(self._save_note)
        layout.addWidget(save_btn)

        return drawer

    def _build_shortcuts(self) -> None:
        for key, fn in [
            (Qt.Key.Key_Right,          self.next_chapter),
            (Qt.Key.Key_Left,           self.prev_chapter),
            (QKeySequence.StandardKey.Open, self.open_file_dialog),
            (QKeySequence.StandardKey.Find, self._toggle_search_sidebar),
        ]:
            act = QAction(self)
            act.setShortcut(QKeySequence(key) if isinstance(key, Qt.Key) else key)
            act.triggered.connect(fn)
            self.addAction(act)

    # ------------------------------------------------------------------
    # 侧边栏切换
    # ------------------------------------------------------------------

    def _deactivate_all_sidebar_btns(self) -> None:
        for btn in (self.btn_toc, self.btn_search, self.btn_notes_list):
            btn.setChecked(False)

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
    # 笔记抽屉（右侧）
    # ------------------------------------------------------------------

    def open_note_drawer(self, highlight_id: int) -> None:
        """打开右侧笔记抽屉，加载指定高亮的摘录和已有笔记"""
        if self.highlight_store is None:
            return
        # 从数据库查出这条高亮（为了拿原文摘录和已有笔记内容）
        all_hl = self.highlight_store.get_all(self.epub_path)
        target = next((h for h in all_hl if h.id == highlight_id), None)
        if target is None:
            return

        self._active_note_id = highlight_id

        # 原文最多显示 120 个字符，太长会撑坏抽屉布局
        quote = target.selected_text
        if len(quote) > 120:
            quote = quote[:120] + "…"
        self.note_quote_label.setText("\u201c" + quote + "\u201d")

        self.note_edit.setPlainText(target.note)
        self.note_edit.setFocus()
        self.note_drawer.setVisible(True)

    def _close_note_drawer(self) -> None:
        self._active_note_id = None
        self.note_drawer.setVisible(False)

    def _save_note(self) -> None:
        if self.highlight_store is None or self._active_note_id is None:
            return
        note_text = self.note_edit.toPlainText().strip()
        self.highlight_store.update_note(self._active_note_id, note_text)
        # 如果笔记列表面板是打开的，刷新一下
        if self.btn_notes_list.isChecked():
            self._refresh_notes_list()
        self._close_note_drawer()

    # ------------------------------------------------------------------
    # 笔记列表（左侧）
    # ------------------------------------------------------------------

    def _refresh_notes_list(self) -> None:
        """重新从数据库加载全书所有高亮，填充左侧笔记列表"""
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
        if highlight_id is None:
            return
        # 找到对应高亮，跳转到所在章节，然后打开笔记抽屉
        if self.highlight_store is None:
            return
        all_hl = self.highlight_store.get_all(self.epub_path)
        target = next((h for h in all_hl if h.id == highlight_id), None)
        if target is None:
            return
        self.go_to_chapter(target.chapter_index)
        self.open_note_drawer(highlight_id)

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
        self.notes_list.clear()

        self._close_note_drawer()
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
        if self.book and self.current_chapter_idx < self.book.chapter_count() - 1:
            self.current_chapter_idx += 1
            self._render_current_chapter()

    def prev_chapter(self) -> None:
        if self.book and self.current_chapter_idx > 0:
            self.current_chapter_idx -= 1
            self._render_current_chapter()

    def go_to_chapter(self, chapter_index: int) -> None:
        if self.book and 0 <= chapter_index < self.book.chapter_count():
            self.current_chapter_idx = chapter_index
            self._render_current_chapter()

    def _on_page_loaded(self, ok: bool) -> None:
        if not ok:
            return
        page = self.web_view.page()
        page.runJavaScript(_SCROLL_WATCHER_JS)
        if _HIGHLIGHTER_JS_PATH.exists():
            page.runJavaScript(_HIGHLIGHTER_JS_PATH.read_text(encoding="utf-8"))
        self._restore_highlights()

    def _restore_highlights(self) -> None:
        if self.highlight_store is None or self.book is None:
            return
        highlights_json = self.highlight_store.highlights_to_js_json(
            book_path=self.epub_path,
            chapter_index=self.current_chapter_idx,
        )
        self.web_view.page().runJavaScript(f"restoreHighlights({highlights_json});")

    def _on_highlight_message(self, payload: dict) -> None:
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
            self.web_view.page().runJavaScript(
                f"updateHighlightId('{payload.get('tempId', '')}', {saved.id});"
            )

        elif action == "update_color":
            try:
                self.highlight_store.update_color(
                    int(float(str(payload["id"]))), payload.get("color", "yellow")
                )
            except (ValueError, TypeError, KeyError):
                pass

        elif action == "delete":
            try:
                self.highlight_store.delete(int(float(str(payload["id"]))))
            except (ValueError, TypeError, KeyError):
                pass

        elif action == "open_note":
            try:
                self.open_note_drawer(int(float(str(payload["id"]))))
            except (ValueError, TypeError, KeyError):
                pass

    def _on_chapter_scrolled_to_bottom(self) -> None:
        if self.book and self.current_chapter_idx < self.book.chapter_count() - 1:
            self.next_chapter()

    # ------------------------------------------------------------------
    # 目录侧边栏
    # ------------------------------------------------------------------

    def _populate_toc(self) -> None:
        self.toc_tree.clear()
        if self.book is None:
            return

        def add_entries(parent_item, entries: list[TocEntry]):
            for entry in entries:
                tree_item = QTreeWidgetItem([entry.title])
                tree_item.setData(0, Qt.ItemDataRole.UserRole, entry.chapter_index)
                if parent_item is None:
                    self.toc_tree.addTopLevelItem(tree_item)
                else:
                    parent_item.addChild(tree_item)
                add_entries(tree_item, entry.children)

        add_entries(None, self.book.toc)
        self.toc_tree.expandAll()

    def _on_toc_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        idx = item.data(0, Qt.ItemDataRole.UserRole)
        if idx is not None:
            self.go_to_chapter(idx)

    def _highlight_current_toc_item(self) -> None:
        def find(items_iter) -> bool:
            for item in items_iter:
                if item.data(0, Qt.ItemDataRole.UserRole) == self.current_chapter_idx:
                    self.toc_tree.setCurrentItem(item)
                    return True
                if item.childCount() and find(item.child(i) for i in range(item.childCount())):
                    return True
            return False

        find(self.toc_tree.topLevelItem(i) for i in range(self.toc_tree.topLevelItemCount()))

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
            item = QListWidgetItem(f"{r.chapter_title}\n{r.snippet}")
            item.setData(Qt.ItemDataRole.UserRole, r)
            self.search_results_list.addItem(item)

    def _on_search_result_clicked(self, item: QListWidgetItem) -> None:
        result: SearchResult = item.data(Qt.ItemDataRole.UserRole)
        if result:
            self.go_to_chapter(result.chapter_index)
