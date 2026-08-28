"""
epub_editor.py — 所见即所得 epub 内容编辑器

用 QWebEngineView + contenteditable 实现。
打开章节后页面进入可编辑状态，用户直接在渲染好的排版上修改文字。
保存时把 DOM 序列化回 HTML，通过 epub_writer 写回 epub 文件。

窗口布局：
    顶部工具栏：保存、粗体、斜体、下划线、撤销、重做
    左侧：章节列表（点击切换）
    右侧：contenteditable 编辑区
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt, QTimer
from PySide6.QtGui import QKeySequence, QAction, QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView

from core.epub_loader import EpubBook, Chapter
from core.epub_writer import (
    read_chapter_html,
    temp_path_to_zip_name,
    write_chapters,
)
from ui.theme import DARK, Theme, ThemeManager

# 注入到编辑页面的 JS：开启 contenteditable，注入最小样式
_EDIT_INIT_JS = """
(function() {
    // 让 body 可编辑
    document.body.contentEditable = 'true';
    document.body.spellcheck = false;

    // 注入编辑态样式：光标、选区颜色、去掉默认 outline
    // 加 id 是为了保存前能精确找到并移除，不污染存回 epub 的内容
    const style = document.createElement('style');
    style.id = 'marginalia-editor-style';
    style.textContent = `
        body {
            outline: none;
            cursor: text;
            caret-color: #333;
        }
        ::selection {
            background: #b4d0f5;
        }
        /* 让行宽舒适，不铺满全宽 */
        body > * {
            max-width: 680px;
            margin-left: auto;
            margin-right: auto;
        }
    `;
    document.head.appendChild(style);

    // 禁用链接跳转（编辑状态下点链接只是移动光标，不应该导航）
    document.addEventListener('click', function(e) {
        if (e.target.tagName === 'A') e.preventDefault();
    }, true);

    // 内容变化时通知 Python
    document.addEventListener('input', function() {
        console.log('EDITOR_DIRTY');
    });
})();
"""

# 编辑区域的夜间模式：跟阅读器的 _READER_DARK_CSS_JS 是同一套思路，
# 用固定 id 的 <style> 标签，切主题时只改 textContent，不用重新加载页面
# （重新加载会打断正在编辑的内容和撤销历史）。
_EDITOR_DARK_CSS_JS = f"""
(function() {{
    let style = document.getElementById('marginalia-editor-dark');
    if (!style) {{
        style = document.createElement('style');
        style.id = 'marginalia-editor-dark';
        document.head.appendChild(style);
    }}
    style.textContent = `
        html, body {{
            background: {DARK.reader_bg} !important;
            color: {DARK.text} !important;
        }}
    `;
}})();
"""

_EDITOR_LIGHT_CSS_JS = """
(function() {
    const style = document.getElementById('marginalia-editor-dark');
    if (style) { style.remove(); }
})();
"""


# 序列化当前 DOM 为 HTML 字符串，供保存时写回 epub 使用。
#
# 不能直接用 document.documentElement.outerHTML！
# 那是 HTML 序列化算法，会把 <br/> <img/> 这类自闭合标签
# 序列化成不闭合的 <br> <img>，而 epub 的 XHTML 内容要求严格
# 良构 XML —— 未闭合标签会导致这一章下次打开时 XML 解析失败。
#
# 改用 XMLSerializer，它按 XML 规则序列化，自闭合标签保持自闭合。
# 保存前还要清理掉编辑器自己注入的痕迹（contenteditable 属性、
# 编辑态样式表），否则这些东西会永久写进正文内容里。
_GET_HTML_JS = """
(function() {
    const clone = document.cloneNode(true);

    const styleTag = clone.getElementById('marginalia-editor-style');
    if (styleTag) { styleTag.remove(); }

    const darkStyleTag = clone.getElementById('marginalia-editor-dark');
    if (darkStyleTag) { darkStyleTag.remove(); }

    if (clone.body) {
        clone.body.removeAttribute('contenteditable');
        clone.body.removeAttribute('spellcheck');
    }

    // 原文件里的 <?xml ...?> 声明有时会被解析成一个残留的注释节点
    // （排在 doctype 之前），清掉它，避免和我们下面重新加的声明重复
    for (const node of Array.from(clone.childNodes)) {
        if (node.nodeType === Node.COMMENT_NODE && node.data.trim().startsWith('?xml')) {
            clone.removeChild(node);
        }
    }

    const serialized = new XMLSerializer().serializeToString(clone);
    return '<?xml version="1.0" encoding="utf-8"?>\\n' + serialized;
})();
"""



class EditorPage(QWebEnginePage):
    """只拦截 EDITOR_DIRTY，其他 console 消息忽略"""

    def __init__(self, on_dirty, parent=None):
        super().__init__(parent)
        self._on_dirty = on_dirty

    def javaScriptConsoleMessage(self, level, message, line, source):
        if message == "EDITOR_DIRTY":
            self._on_dirty()


class EpubEditorWindow(QMainWindow):
    """
    独立编辑器窗口。
    由阅读器主窗口通过菜单/按钮打开，传入已加载的 EpubBook 和 epub 路径。
    """

    def __init__(
        self,
        epub_path: str,
        book: EpubBook,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.epub_path   = epub_path
        self.book        = book
        self._current_idx: int | None = None
        self._dirty      = False   # 当前章节是否有未保存的改动

        self.setWindowTitle(f"编辑 — {book.title}")
        self.resize(1100, 820)

        self._theme_mgr = ThemeManager.instance()

        self._build_ui()
        self._build_shortcuts()

        self._apply_theme(self._theme_mgr.current)
        self._theme_mgr.changed.connect(self._on_theme_changed)

        # 默认打开第一章
        if book.chapter_count() > 0:
            self._load_chapter(0)

    # ------------------------------------------------------------------
    # 构建 UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter = splitter
        splitter.setHandleWidth(1)

        splitter.addWidget(self._build_chapter_list())
        splitter.addWidget(self._build_editor_area())
        splitter.setSizes([220, 880])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter, stretch=1)

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        self._toolbar = bar
        bar.setFixedHeight(44)
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 0, 10, 0)
        row.setSpacing(2)

        # 保存
        self.btn_save = QPushButton("保存")
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self._save_current)
        row.addWidget(self.btn_save)

        row.addSpacing(12)

        # 格式按钮
        fmt_btns = [
            ("𝐁",  "粗体",     "bold"),
            ("𝑰",  "斜体",     "italic"),
            ("U̲",  "下划线",   "underline"),
        ]
        for label, tip, cmd in fmt_btns:
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _, c=cmd: self._exec_command(c))
            row.addWidget(btn)

        row.addSpacing(8)

        # 撤销/重做
        self.btn_undo = QPushButton("↩")
        self.btn_undo.setToolTip("撤销")
        self.btn_undo.clicked.connect(lambda: self._exec_command("undo"))
        self.btn_redo = QPushButton("↪")
        self.btn_redo.setToolTip("重做")
        self.btn_redo.clicked.connect(lambda: self._exec_command("redo"))
        row.addWidget(self.btn_undo)
        row.addWidget(self.btn_redo)

        row.addStretch()

        self.status_label = QLabel("选择左侧章节开始编辑")
        row.addWidget(self.status_label)

        # 暗黑模式切换（跟阅读器主窗口共用同一个 ThemeManager，
        # 这里切一下，阅读器那边也会跟着变）
        self.btn_theme = QPushButton()
        self.btn_theme.setToolTip("切换深色/浅色模式")
        self.btn_theme.clicked.connect(self._theme_mgr.toggle)
        row.addWidget(self.btn_theme)

        return bar

    def _build_chapter_list(self) -> QWidget:
        panel = QWidget()
        self._chapter_panel = panel
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(0, 8, 0, 0)
        vbox.setSpacing(0)

        self._chapter_header = QLabel("章节")
        vbox.addWidget(self._chapter_header)

        self.chapter_list = QListWidget()

        for ch in self.book.chapters:
            item = QListWidgetItem(ch.title or f"章节 {ch.index + 1}")
            item.setData(Qt.ItemDataRole.UserRole, ch.index)
            self.chapter_list.addItem(item)

        self.chapter_list.currentRowChanged.connect(self._on_chapter_selected)
        vbox.addWidget(self.chapter_list, stretch=1)
        return panel

    def _build_editor_area(self) -> QWidget:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        self.web_view = QWebEngineView()
        page = EditorPage(
            on_dirty=self._on_editor_dirty,
            parent=self.web_view,
        )
        self.web_view.setPage(page)
        self.web_view.loadFinished.connect(self._on_editor_ready)
        vbox.addWidget(self.web_view)
        return container

    def _build_shortcuts(self) -> None:
        save_act = QAction(self)
        save_act.setShortcut(QKeySequence.StandardKey.Save)
        save_act.triggered.connect(self._save_current)
        self.addAction(save_act)

    # ------------------------------------------------------------------
    # 主题（深色/浅色）
    # ------------------------------------------------------------------

    def _on_theme_changed(self, theme: Theme) -> None:
        self._apply_theme(theme)
        self._apply_editor_dark_mode()

    def _apply_theme(self, t: Theme) -> None:
        self._splitter.setStyleSheet(
            f"QSplitter::handle {{ background: {t.border}; }}"
        )
        self._toolbar.setStyleSheet(f"""
            QWidget   {{ background: {t.bg_toolbar}; border-bottom: 1px solid {t.border}; }}
            QPushButton {{
                border: none; background: transparent;
                font-size: 14px; color: {t.text_secondary}; padding: 0 12px; min-width: 32px;
            }}
            QPushButton:hover   {{ color: {t.text}; background: {t.bg_hover}; border-radius: 4px; }}
            QPushButton:pressed {{ background: {t.bg_selected}; }}
            QPushButton:disabled {{ color: {t.text_muted}; }}
            QLabel {{ font-size: 13px; color: {t.text_secondary}; }}
        """)
        self.btn_save.setStyleSheet(f"""
            QPushButton {{
                background: {t.button_primary_bg}; color: {t.button_primary_text}; border-radius: 6px;
                padding: 0 16px; font-size: 13px; min-width: 56px;
            }}
            QPushButton:hover   {{ background: {t.button_primary_hover}; }}
            QPushButton:disabled {{ background: {t.text_muted}; }}
        """)
        self.btn_theme.setText("☀️" if t.is_dark else "🌙")

        self._chapter_panel.setStyleSheet(f"background: {t.bg_sidebar};")
        self._chapter_header.setStyleSheet(
            f"font-size: 11px; color: {t.text_muted}; font-weight: 600;"
            "padding: 0 14px 6px; letter-spacing: 0.5px;"
        )
        self.chapter_list.setStyleSheet(f"""
            QListWidget {{
                border: none; background: transparent; font-size: 13px; color: {t.text};
            }}
            QListWidget::item {{
                padding: 8px 14px; color: {t.text}; border-radius: 0;
            }}
            QListWidget::item:selected {{
                background: {t.bg_selected}; color: {t.text};
            }}
            QListWidget::item:hover:!selected {{
                background: {t.bg_hover};
            }}
        """)

    def _apply_editor_dark_mode(self) -> None:
        """给当前正在编辑的章节内容注入/移除夜间模式样式（不重新加载页面，
        避免打断正在编辑的内容和撤销历史）。"""
        if self._current_idx is None:
            return
        js = _EDITOR_DARK_CSS_JS if self._theme_mgr.current.is_dark else _EDITOR_LIGHT_CSS_JS
        self.web_view.page().runJavaScript(js)

    # ------------------------------------------------------------------
    # 章节加载
    # ------------------------------------------------------------------

    def _on_chapter_selected(self, row: int) -> None:
        if row < 0:
            return
        idx = self.chapter_list.item(row).data(Qt.ItemDataRole.UserRole)
        if idx == self._current_idx:
            return
        # 有未保存改动时询问
        if self._dirty:
            reply = QMessageBox.question(
                self, "未保存的改动",
                "当前章节有未保存的改动，切换章节将丢失这些改动。\n是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                # 恢复列表选中状态
                self.chapter_list.blockSignals(True)
                self.chapter_list.setCurrentRow(self._current_idx)
                self.chapter_list.blockSignals(False)
                return
        self._load_chapter(idx)

    def _load_chapter(self, chapter_idx: int) -> None:
        chapter = self.book.chapters[chapter_idx]
        self._current_idx = chapter_idx
        self._dirty = False
        self.btn_save.setEnabled(False)
        self.status_label.setText(f"正在加载 {chapter.title}…")

        # 从原始 epub zip 读 HTML（不从临时目录读，避免拿到注入了高亮JS的版本）
        zip_name = temp_path_to_zip_name(
            chapter.file_path,
            self.book.temp_dir.name,
        )
        try:
            html = read_chapter_html(self.epub_path, zip_name)
        except Exception as e:
            self.status_label.setText(f"加载失败：{e}")
            return

        self._current_zip_name = zip_name

        # 用 file:// 路径作为 base URL，让章节内相对路径的图片/CSS 正常加载
        base_url = QUrl.fromLocalFile(str(chapter.file_path))
        self.web_view.setHtml(html, base_url)

        # 同步列表高亮
        self.chapter_list.blockSignals(True)
        self.chapter_list.setCurrentRow(chapter_idx)
        self.chapter_list.blockSignals(False)

    def _on_editor_ready(self, ok: bool) -> None:
        """loadFinished 触发，注入编辑初始化 JS"""
        if not ok or self._current_idx is None:
            return
        self.web_view.page().runJavaScript(_EDIT_INIT_JS)
        chapter = self.book.chapters[self._current_idx]
        self.status_label.setText(chapter.title or f"章节 {self._current_idx + 1}")
        self._apply_editor_dark_mode()

    def _on_editor_dirty(self) -> None:
        """JS 检测到内容变化，标记未保存状态"""
        if not self._dirty:
            self._dirty = True
            self.btn_save.setEnabled(True)
        chapter = self.book.chapters[self._current_idx]
        title = chapter.title or f"章节 {self._current_idx + 1}"
        self.status_label.setText(title + "  ●")

    # ------------------------------------------------------------------
    # 格式命令
    # ------------------------------------------------------------------

    def _exec_command(self, command: str) -> None:
        self.web_view.page().runJavaScript(
            f"document.execCommand('{command}', false, null);"
        )

    # ------------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------------

    def _save_current(self) -> None:
        if self._current_idx is None or not self._dirty:
            return
        self.status_label.setText("保存中…")
        self.btn_save.setEnabled(False)

        # 异步拿到当前 DOM 的 HTML 序列化
        self.web_view.page().runJavaScript(
            _GET_HTML_JS, self._on_got_html
        )

    def _on_got_html(self, html: str) -> None:
        if not html:
            self.status_label.setText("获取内容失败")
            self.btn_save.setEnabled(True)
            return
        try:
            write_chapters(
                self.epub_path,
                {self._current_zip_name: html.encode("utf-8")},
            )
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"写回 epub 时出错：\n{e}")
            self.btn_save.setEnabled(True)
            self.status_label.setText("保存失败")
            return

        self._dirty = False
        chapter = self.book.chapters[self._current_idx]
        self.status_label.setText(f"{chapter.title}  已保存")
        # 短暂显示「已保存」后恢复正常标题
        QTimer.singleShot(
            2000,
            lambda: self.status_label.setText(chapter.title or "")
            if not self._dirty else None,
        )

    # ------------------------------------------------------------------
    # 关闭窗口时提示未保存
    # ------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._dirty:
            reply = QMessageBox.question(
                self, "未保存的改动",
                "当前章节有未保存的改动，关闭将丢失这些改动。\n是否关闭？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return
        try:
            self._theme_mgr.changed.disconnect(self._on_theme_changed)
        except (RuntimeError, TypeError):
            pass
        event.accept()
