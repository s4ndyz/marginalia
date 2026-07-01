"""
highlights.py

高亮/批注的数据存取层，基于 SQLite。

每本书对应一个独立的 .db 文件，放在书文件同目录下，
命名规则是 <epub文件名>.marginalia.db。
这样好处是书和它的批注数据放在一起，方便备份和移动。

高亮的位置用"轻量版 CFI"描述：
    - container_xpath: 文本节点的父元素 XPath，例如 "/html/body/div[1]/p[3]"
    - start_offset / end_offset: 在该文本节点内的字符偏移

这比完整 CFI 规范实现简单得多，且对于"不会编辑正文内容"的纯阅读/批注场景
完全足够——位置锚点只需要在内容不变的情况下稳定还原即可。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


# 支持的高亮颜色，key 是传递给 JS 的 CSS 颜色值
HIGHLIGHT_COLORS: dict[str, str] = {
    "yellow": "#FFE066",
    "green":  "#A8E6A3",
    "blue":   "#A3C8F5",
    "pink":   "#F5A3C8",
}
DEFAULT_COLOR = "yellow"


@dataclass
class Highlight:
    id: int | None              # 数据库主键，None 表示尚未持久化
    book_path: str              # epub 文件的绝对路径，作为跨设备时的书籍标识
    chapter_index: int
    container_xpath: str        # 选区起始/结束的公共父元素 XPath
    start_offset: int           # 在 container 文本内容中的字符起始偏移
    end_offset: int             # 字符结束偏移（不含）
    selected_text: str          # 被选中的原文，用于展示和搜索
    color: str = DEFAULT_COLOR  # 高亮颜色 key，对应 HIGHLIGHT_COLORS
    note: str = ""              # 用户附加的文字批注，可为空
    created_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )


class HighlightStore:
    """
    单本书的高亮数据库操作类。
    使用时建议作为上下文管理器（with HighlightStore(...) as store:），
    也可以直接实例化后手动调用 close()。
    """

    def __init__(self, epub_path: str | Path) -> None:
        epub_path = Path(epub_path).resolve()
        db_path = epub_path.with_suffix("").with_suffix(".marginalia.db")
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS highlights (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                book_path       TEXT    NOT NULL,
                chapter_index   INTEGER NOT NULL,
                container_xpath TEXT    NOT NULL,
                start_offset    INTEGER NOT NULL,
                end_offset      INTEGER NOT NULL,
                selected_text   TEXT    NOT NULL,
                color           TEXT    NOT NULL DEFAULT 'yellow',
                note            TEXT    NOT NULL DEFAULT '',
                created_at      TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chapter
                ON highlights (book_path, chapter_index);
            """
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, h: Highlight) -> Highlight:
        """插入一条高亮记录，返回带有 id 的新对象"""
        cur = self._conn.execute(
            """
            INSERT INTO highlights
                (book_path, chapter_index, container_xpath,
                 start_offset, end_offset, selected_text,
                 color, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                h.book_path, h.chapter_index, h.container_xpath,
                h.start_offset, h.end_offset, h.selected_text,
                h.color, h.note, h.created_at,
            ),
        )
        self._conn.commit()
        return Highlight(
            id=cur.lastrowid,
            book_path=h.book_path,
            chapter_index=h.chapter_index,
            container_xpath=h.container_xpath,
            start_offset=h.start_offset,
            end_offset=h.end_offset,
            selected_text=h.selected_text,
            color=h.color,
            note=h.note,
            created_at=h.created_at,
        )

    def get_by_chapter(self, book_path: str, chapter_index: int) -> list[Highlight]:
        """取出某章节的所有高亮，按创建时间排序"""
        rows = self._conn.execute(
            """
            SELECT * FROM highlights
            WHERE book_path = ? AND chapter_index = ?
            ORDER BY created_at ASC
            """,
            (book_path, chapter_index),
        ).fetchall()
        return [self._row_to_highlight(r) for r in rows]

    def get_all(self, book_path: str) -> list[Highlight]:
        """取出这本书的全部高亮，按章节和创建时间排序"""
        rows = self._conn.execute(
            """
            SELECT * FROM highlights
            WHERE book_path = ?
            ORDER BY chapter_index ASC, created_at ASC
            """,
            (book_path,),
        ).fetchall()
        return [self._row_to_highlight(r) for r in rows]

    def update_note(self, highlight_id: int, note: str) -> None:
        self._conn.execute(
            "UPDATE highlights SET note = ? WHERE id = ?",
            (note, highlight_id),
        )
        self._conn.commit()

    def update_color(self, highlight_id: int, color: str) -> None:
        self._conn.execute(
            "UPDATE highlights SET color = ? WHERE id = ?",
            (color, highlight_id),
        )
        self._conn.commit()

    def delete(self, highlight_id: int) -> None:
        self._conn.execute("DELETE FROM highlights WHERE id = ?", (highlight_id,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # 工具方法：序列化供 JS 使用
    # ------------------------------------------------------------------

    def highlights_to_js_json(
        self, book_path: str, chapter_index: int
    ) -> str:
        """
        把某章节的高亮列表序列化成 JSON 字符串，
        直接传给 JS 的 restoreHighlights() 函数使用。
        """
        highlights = self.get_by_chapter(book_path, chapter_index)
        data = [
            {
                "id": h.id,
                "containerXpath": h.container_xpath,
                "startOffset": h.start_offset,
                "endOffset": h.end_offset,
                "color": HIGHLIGHT_COLORS.get(h.color, HIGHLIGHT_COLORS[DEFAULT_COLOR]),
                "note": h.note,
                "selectedText": h.selected_text,
            }
            for h in highlights
        ]
        return json.dumps(data, ensure_ascii=False)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_highlight(row: sqlite3.Row) -> Highlight:
        return Highlight(
            id=row["id"],
            book_path=row["book_path"],
            chapter_index=row["chapter_index"],
            container_xpath=row["container_xpath"],
            start_offset=row["start_offset"],
            end_offset=row["end_offset"],
            selected_text=row["selected_text"],
            color=row["color"],
            note=row["note"],
            created_at=row["created_at"],
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> HighlightStore:
        return self

    def __exit__(self, *_) -> None:
        self.close()
