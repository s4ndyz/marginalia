"""
library.py — 书库管理数据层

数据库: ~/.marginalia/library.db
封面缓存: ~/.marginalia/covers/<md5>.{jpg,png,...}

epub 文件本身不移动，只记录绝对路径。
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ebooklib import epub
import ebooklib


_LIBRARY_DIR = Path.home() / ".marginalia"
_DB_PATH     = _LIBRARY_DIR / "library.db"
_COVERS_DIR  = _LIBRARY_DIR / "covers"


@dataclass
class BookRecord:
    id: int
    epub_path: str
    title: str
    author: str
    cover_path: str   # 本地封面缓存路径，可能为空字符串
    added_at: str     # ISO 8601


# ------------------------------------------------------------------
# 内部工具
# ------------------------------------------------------------------

def _ensure_dirs() -> None:
    _LIBRARY_DIR.mkdir(exist_ok=True)
    _COVERS_DIR.mkdir(exist_ok=True)


def _book_key(epub_path: str) -> str:
    return hashlib.md5(epub_path.encode()).hexdigest()


def _extract_cover(epub_path: str) -> str:
    """提取封面图，存到 covers 目录，返回路径；失败返回空字符串。"""
    key = _book_key(epub_path)
    for ext in (".jpg", ".png", ".gif", ".webp"):
        cached = _COVERS_DIR / (key + ext)
        if cached.exists():
            return str(cached)

    try:
        raw = epub.read_epub(epub_path, options={"ignore_ncx": True})

        # 方法1：OPF <meta name="cover">
        cover_item = None
        cover_id = None
        for _name, val in raw.get_metadata("OPF", "cover"):
            cover_id = val.get("content") if isinstance(val, dict) else None
            break
        # 方法2：properties="cover-image"
        if not cover_id:
            for item in raw.get_items():
                if "cover-image" in (item.properties or []):
                    cover_item = item
                    break
        if cover_id and not cover_item:
            cover_item = raw.get_item_with_id(cover_id)
        # 方法3：名字含 cover 的图片
        if not cover_item:
            for item in raw.get_items_of_type(ebooklib.ITEM_IMAGE):
                if "cover" in item.file_name.lower():
                    cover_item = item
                    break
        # 方法4：第一张足够大的图
        if not cover_item:
            for item in raw.get_items_of_type(ebooklib.ITEM_IMAGE):
                if len(item.get_content()) > 1000:
                    cover_item = item
                    break

        if not cover_item:
            return ""

        mime = cover_item.media_type or ""
        ext = (".png" if "png" in mime else
               ".gif" if "gif" in mime else
               ".webp" if "webp" in mime else ".jpg")
        out = _COVERS_DIR / (key + ext)
        out.write_bytes(cover_item.get_content())
        return str(out)

    except Exception:
        return ""


def _read_metadata(epub_path: str) -> tuple[str, str]:
    try:
        raw = epub.read_epub(epub_path, options={"ignore_ncx": True})
        title_meta   = raw.get_metadata("DC", "title")
        creator_meta = raw.get_metadata("DC", "creator")
        title  = title_meta[0][0]   if title_meta   else Path(epub_path).stem
        author = creator_meta[0][0] if creator_meta else ""
        return title, author
    except Exception:
        return Path(epub_path).stem, ""


def _row_to_record(row: sqlite3.Row) -> BookRecord:
    return BookRecord(
        id=row["id"],
        epub_path=row["epub_path"],
        title=row["title"],
        author=row["author"],
        cover_path=row["cover_path"],
        added_at=row["added_at"],
    )


def _get_conn() -> sqlite3.Connection:
    _ensure_dirs()
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS books (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            epub_path  TEXT NOT NULL UNIQUE,
            title      TEXT NOT NULL DEFAULT '',
            author     TEXT NOT NULL DEFAULT '',
            cover_path TEXT NOT NULL DEFAULT '',
            added_at   TEXT NOT NULL
                       DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
        );
    """)
    conn.commit()
    return conn


# ------------------------------------------------------------------
# 公开接口
# ------------------------------------------------------------------

def import_book(epub_path: str) -> BookRecord | None:
    """
    导入一本书。路径已存在时直接返回已有记录，不重复导入。
    文件不存在或解析失败返回 None。
    """
    epub_path = str(Path(epub_path).resolve())
    if not Path(epub_path).exists():
        return None

    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM books WHERE epub_path = ?", (epub_path,)
        ).fetchone()
        if row:
            return _row_to_record(row)

        title, author = _read_metadata(epub_path)
        cover_path    = _extract_cover(epub_path)

        cur = conn.execute(
            "INSERT INTO books (epub_path, title, author, cover_path) VALUES (?,?,?,?)",
            (epub_path, title, author, cover_path),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM books WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return _row_to_record(row)
    except Exception:
        return None
    finally:
        conn.close()


def get_all_books() -> list[BookRecord]:
    """返回所有书，按导入时间倒序。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM books ORDER BY added_at DESC"
        ).fetchall()
        return [_row_to_record(r) for r in rows]
    finally:
        conn.close()


def remove_book(book_id: int) -> None:
    """从书库移除记录（不删 epub 文件，顺手删封面缓存）。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT cover_path FROM books WHERE id = ?", (book_id,)
        ).fetchone()
        if row and row["cover_path"]:
            Path(row["cover_path"]).unlink(missing_ok=True)
        conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        conn.commit()
    finally:
        conn.close()
