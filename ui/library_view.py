"""
library_view.py — 书库界面

用自定义 BookCard widget 构成网格，比 QListWidget IconMode
能更精细地控制每张卡片的布局和样式。

交互：
    - 单击打开书
    - 右键菜单：从书库移除
    - 封面不存在时显示带首字的彩色占位块
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap, QCursor
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.library import BookRecord, get_all_books, import_book, remove_book

COVER_W   = 148
COVER_H   = 210
CARD_W    = COVER_W + 16
GRID_COLS = 5
GRID_GAP  = 24

_PALETTE = [
    "#C17C74", "#6E8B8B", "#7A9E7E", "#8B7355",
    "#7B6B8B", "#8B8B6B", "#6B7B8B", "#8B7B6B",
]


def _placeholder_pixmap(title: str) -> QPixmap:
    color = _PALETTE[ord(title[0]) % len(_PALETTE)] if title else "#999"
    px = QPixmap(COVER_W, COVER_H)
    px.fill(QColor(color))
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    f = QFont(); f.setPointSize(56); f.setBold(True)
    p.setFont(f)
    p.setPen(QColor(255, 255, 255, 160))
    p.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, (title or "?")[0])
    p.end()
    return px


def _cover_pixmap(cover_path: str) -> QPixmap:
    px = QPixmap(cover_path)
    if px.isNull():
        return QPixmap()
    return px.scaled(
        COVER_W, COVER_H,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    ).copy(0, 0, COVER_W, COVER_H)


class BookCard(QFrame):
    """一张书卡：封面 + 书名 + 作者，单击打开，右键移除"""

    def __init__(self, rec: BookRecord, on_open, on_remove, parent=None):
        super().__init__(parent)
        self.rec = rec
        self._on_open   = on_open
        self._on_remove = on_remove
        self._build()

    def _build(self):
        self.setFixedWidth(CARD_W)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setStyleSheet("""
            BookCard { background: transparent; border: none; border-radius: 6px; }
        """)

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(8, 8, 8, 10)
        vbox.setSpacing(7)

        # ── 封面 ──────────────────────────────────
        cover_lbl = QLabel()
        cover_lbl.setFixedSize(COVER_W, COVER_H)
        cover_lbl.setScaledContents(False)
        cover_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_lbl.setStyleSheet("""
            border-radius: 5px;
            background: #e8e5de;
        """)

        if self.rec.cover_path and Path(self.rec.cover_path).exists():
            px = _cover_pixmap(self.rec.cover_path)
        else:
            px = _placeholder_pixmap(self.rec.title)

        # 给封面加细圆角（用 mask）
        rounded = QPixmap(COVER_W, COVER_H)
        rounded.fill(Qt.GlobalColor.transparent)
        rp = QPainter(rounded)
        rp.setRenderHint(QPainter.RenderHint.Antialiasing)
        rp.setBrush(Qt.GlobalColor.white)
        rp.setPen(Qt.PenStyle.NoPen)
        rp.drawRoundedRect(0, 0, COVER_W, COVER_H, 5, 5)
        rp.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceIn
        )
        rp.drawPixmap(0, 0, px)
        rp.end()

        cover_lbl.setPixmap(rounded)
        vbox.addWidget(cover_lbl)

        # ── 书名 ──────────────────────────────────
        title_lbl = QLabel(self.rec.title or "（无标题）")
        title_lbl.setWordWrap(True)
        title_lbl.setFixedWidth(COVER_W)
        title_lbl.setMaximumHeight(36)
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        title_lbl.setStyleSheet(
            "font-size: 12px; font-weight: 600; color: #1a1a1a; background: transparent;"
        )
        vbox.addWidget(title_lbl)

        # ── 作者 ──────────────────────────────────
        author_lbl = QLabel(self.rec.author or "")
        author_lbl.setFixedWidth(COVER_W)
        author_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)
        author_lbl.setStyleSheet(
            "font-size: 11px; color: #888; background: transparent;"
        )
        vbox.addWidget(author_lbl)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            if Path(self.rec.epub_path).exists():
                self._on_open(self.rec.epub_path)
        super().mousePressEvent(ev)

    def contextMenuEvent(self, ev):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: white; border: 1px solid #ddd;
                border-radius: 8px; padding: 4px;
            }
            QMenu::item {
                padding: 7px 18px; font-size: 13px;
                color: #333; border-radius: 4px;
            }
            QMenu::item:selected { background: #f0ede6; }
        """)
        act = menu.addAction("从书库移除（不删除文件）")
        if menu.exec(ev.globalPos()) == act:
            self._on_remove(self.rec.id)


class LibraryView(QWidget):

    def __init__(self, on_open_book, parent=None):
        super().__init__(parent)
        self._on_open_book  = on_open_book
        self._all_records: list[BookRecord] = []
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.setStyleSheet(
            "QScrollArea { border: none; background: #f7f6f3; }"
        )

        self._grid_host = QWidget()
        self._grid_host.setStyleSheet("background: #f7f6f3;")
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(36, 36, 36, 36)
        self._grid.setHorizontalSpacing(GRID_GAP)
        self._grid.setVerticalSpacing(GRID_GAP + 4)

        self._scroll.setWidget(self._grid_host)
        root.addWidget(self._scroll, stretch=1)

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(56)
        bar.setStyleSheet(
            "background: #fafafa; border-bottom: 1px solid #e5e5e5;"
        )
        row = QHBoxLayout(bar)
        row.setContentsMargins(24, 0, 24, 0)
        row.setSpacing(12)

        title = QLabel("书库")
        title.setStyleSheet(
            "font-size: 16px; font-weight: 600; color: #1a1a1a;"
        )
        row.addWidget(title)
        row.addStretch()

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("搜索书名或作者…")
        self._search_box.setFixedWidth(210)
        self._search_box.setStyleSheet("""
            QLineEdit {
                border: 1px solid #d8d6cf; border-radius: 7px;
                padding: 6px 12px; font-size: 13px;
                background: white; color: #333;
            }
            QLineEdit:focus { border-color: #aaa; }
        """)
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self._apply_filter)
        self._search_box.textChanged.connect(lambda: self._timer.start(200))
        row.addWidget(self._search_box)

        btn = QPushButton("＋ 导入")
        btn.setFixedHeight(34)
        btn.setStyleSheet("""
            QPushButton {
                background: #2c2c2c; color: white; border: none;
                border-radius: 7px; padding: 0 18px; font-size: 13px;
            }
            QPushButton:hover  { background: #111; }
            QPushButton:pressed{ background: #000; }
        """)
        btn.clicked.connect(self._import_dialog)
        row.addWidget(btn)
        return bar

    # ------------------------------------------------------------------
    # 数据 / 渲染
    # ------------------------------------------------------------------

    def refresh(self):
        self._all_records = get_all_books()
        self._render(self._all_records)

    def _apply_filter(self):
        q = self._search_box.text().strip().lower()
        filtered = (
            self._all_records if not q else
            [r for r in self._all_records
             if q in r.title.lower() or q in r.author.lower()]
        )
        self._render(filtered)

    def _render(self, records: list[BookRecord]):
        # 清空旧卡片
        while self._grid.count():
            w = self._grid.takeAt(0).widget()
            if w:
                w.deleteLater()

        if not records:
            lbl = QLabel("书库是空的\n点击右上角「＋ 导入」添加第一本书")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("font-size: 14px; color: #bbb; line-height: 2;")
            self._grid.addWidget(lbl, 0, 0, 1, GRID_COLS,
                                 Qt.AlignmentFlag.AlignCenter)
            return

        for i, rec in enumerate(records):
            card = BookCard(
                rec,
                on_open=self._on_open_book,
                on_remove=self._on_remove,
            )
            self._grid.addWidget(card, i // GRID_COLS, i % GRID_COLS)

        # 最后一行不满时，右侧补弹性空白，让卡片左对齐
        remainder = len(records) % GRID_COLS
        if remainder:
            spacer = QWidget()
            spacer.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
            )
            last_row = (len(records) - 1) // GRID_COLS
            self._grid.addWidget(
                spacer, last_row, remainder, 1, GRID_COLS - remainder
            )

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------

    def _import_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "导入 epub 文件", str(Path.home()),
            "EPUB 文件 (*.epub);;所有文件 (*)"
        )
        for p in paths:
            import_book(p)
        if paths:
            self.refresh()

    def _on_remove(self, book_id: int):
        remove_book(book_id)
        self.refresh()
