"""
export.py — 把全书高亮和批注导出成 Markdown

排版规则：
    # 书名
    ## 章节标题
    > 原文摘录
    批注文字（如果有）
    *创建于 2024-01-01 12:00*
    ---

每条笔记独立成一个区块（日记条目风格），按章节分组，
组内按创建时间排序。没写批注、只有高亮的条目也导出，
原文摘录本身就算一种笔记。
"""

from __future__ import annotations

from core.epub_loader import EpubBook
from core.highlights import Highlight, HighlightStore


def build_markdown(book: EpubBook, highlights: list[Highlight]) -> str:
    """
    把高亮列表按章节分组，渲染成 Markdown 字符串。
    highlights 应该已经是某本书的全部高亮（HighlightStore.get_all 的返回值）。
    """
    if not highlights:
        return f"# {book.title}\n\n暂无笔记。\n"

    # 按章节分组，保留章节在书中的原始顺序
    by_chapter: dict[int, list[Highlight]] = {}
    for h in highlights:
        by_chapter.setdefault(h.chapter_index, []).append(h)

    lines: list[str] = [f"# {book.title}", ""]
    if book.author:
        lines.append(f"*{book.author}*")
        lines.append("")

    chapter_title_map = {ch.index: ch.title for ch in book.chapters}

    for chapter_idx in sorted(by_chapter.keys()):
        chapter_title = chapter_title_map.get(chapter_idx, f"章节 {chapter_idx + 1}")
        lines.append(f"## {chapter_title}")
        lines.append("")

        chapter_highlights = sorted(
            by_chapter[chapter_idx], key=lambda h: h.created_at
        )

        for h in chapter_highlights:
            lines.extend(_render_note_block(h))

    return "\n".join(lines).rstrip() + "\n"


def _render_note_block(h: Highlight) -> list[str]:
    """渲染单条笔记为一个独立区块（日记条目风格）"""
    block: list[str] = []

    # 原文摘录，用 blockquote；多行原文每行都要加 >
    quote_lines = h.selected_text.strip().splitlines() or [""]
    for line in quote_lines:
        block.append(f"> {line}")

    # 批注（如果有）
    if h.note.strip():
        block.append("")
        block.append(h.note.strip())

    # 创建时间
    block.append("")
    block.append(f"*创建于 {_format_timestamp(h.created_at)}*")
    block.append("")
    block.append("---")
    block.append("")

    return block


def _format_timestamp(iso_ts: str) -> str:
    """把 ISO 格式时间戳转成更易读的展示形式，转换失败原样返回"""
    try:
        date_part, time_part = iso_ts.split("T")
        return f"{date_part} {time_part}"
    except ValueError:
        return iso_ts


def export_to_file(
    book: EpubBook,
    highlight_store: HighlightStore,
    epub_path: str,
    output_path: str,
) -> None:
    """
    从数据库读出某本书的全部高亮，渲染成 Markdown，写入 output_path。
    """
    highlights = highlight_store.get_all(epub_path)
    markdown = build_markdown(book, highlights)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(markdown)
