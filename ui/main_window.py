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
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from core.epub_loader import EpubBook, TocEntry, load_epub
from core.search import ChapterText, SearchResult, build_search_index, search

SIDEBAR_TOC = 0
SIDEBAR_SEARCH = 1


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Marginalia")
        self.resize(1100, 1100)  # 比之前更宽一点，给侧边栏留空间

        self.book: EpubBook | None = None
        self.current_chapter_idx: int = 0
        self.search_index: list[ChapterText] = []
        # 当前激活的搜索结果，跳转时用于拿到 match_start 滚动定位
        self._last_search_results: list[SearchResult] = []

        self._build_ui()
        self._build_shortcuts()
        self.sidebar_container.setVisible(False)  # 没打开书之前不需要侧边栏

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
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background-color: #e5e5e5; }")

        self.sidebar_container = self._build_sidebar()
        self.web_view = QWebEngineView()
        self.web_view.setStyleSheet("background-color: #fdfdfb;")

        splitter.addWidget(self.sidebar_container)
        splitter.addWidget(self.web_view)
        splitter.setStretchFactor(0, 0)  # 侧边栏不随窗口拉伸自动变宽
        splitter.setStretchFactor(1, 1)  # 阅读区占满剩余空间
        splitter.setSizes([260, 840])

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

        self.btn_open = QPushButton("打开")
        self.btn_open.clicked.connect(self.open_file_dialog)

        # 目录按钮：点击切换侧边栏显示/隐藏，内容固定为目录树
        self.btn_toc = QPushButton("☰")
        self.btn_toc.setCheckable(True)
        self.btn_toc.setToolTip("目录")
        self.btn_toc.clicked.connect(self._toggle_toc_sidebar)

        # 搜索按钮：点击切换侧边栏显示/隐藏，内容固定为搜索面板
        self.btn_search = QPushButton("⌕")
        self.btn_search.setCheckable(True)
        self.btn_search.setToolTip("搜索")
        self.btn_search.clicked.connect(self._toggle_search_sidebar)

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
        layout.addWidget(self.btn_prev)
        layout.addWidget(self.title_label, stretch=1)
        layout.addWidget(self.btn_next)

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
            QTreeWidget { border: none; background-color: transparent; font-size: 13px; }
            QTreeWidget::item { padding: 5px 4px; }
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
            QListWidget { border: none; background-color: transparent; font-size: 12px; }
            QListWidget::item { padding: 8px 4px; border-bottom: 1px solid #ebe9e3; }
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

        self.current_chapter_idx = 0
        self.btn_prev.setEnabled(False)
        self.btn_next.setEnabled(self.book.chapter_count() > 1)

        self._populate_toc()
        self.search_index = build_search_index(self.book)
        self.search_results_list.clear()
        self.search_input.clear()
        self.search_status_label.setText("")

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
        # 注：跳转到章节后定位到具体文字位置（用 JS scrollIntoView）属于更细的体验优化，
        # 当前版本先做到"跳对章节"，精确定位留到笔记高亮功能一起做，
        # 因为那时候会引入 CFI 定位机制，两者可以共用同一套滚动定位逻辑。

