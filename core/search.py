"""
search.py

全书文本搜索。

设计取舍：
    QWebEngineView 自带 findText()，但它只能搜"当前已加载的那一章"，
    搜不到其他章节。要做真正的"全书搜索"，必须自己把每一章的纯文本
    提取出来，在内存里统一做字符串匹配。

    epub 章节本身是 HTML，所以这里用 BeautifulSoup 把标签去掉，
    只留纯文本用于搜索；命中后展示"关键词前后一小段上下文"，
    方便用户判断要不要跳过去，这跟 Ctrl+F 体验类似但是跨章节的。

性能考量：
    一本几十万字的书，提取纯文本一次大概是几十毫秒级别，
    可以接受在每次打开书时做一次预处理（缓存在内存里），
    而不必每次搜索都重新解析 HTML。
"""

from __future__ import annotations

from dataclasses import dataclass

from bs4 import BeautifulSoup

from core.epub_loader import EpubBook


@dataclass
class SearchResult:
    chapter_index: int
    chapter_title: str
    snippet: str          # 关键词前后一小段上下文，用于展示
    match_start: int       # 关键词在该章节纯文本中的字符偏移，供后续定位高亮使用


@dataclass
class ChapterText:
    """缓存某一章节解析出的纯文本，避免重复解析 HTML"""
    chapter_index: int
    plain_text: str


def build_search_index(book: EpubBook) -> list[ChapterText]:
    """
    把全书每一章节的 HTML 转换成纯文本，构建搜索用的内存索引。
    建议在打开一本书之后调用一次，结果传给 search() 复用。
    """
    index: list[ChapterText] = []
    for chapter in book.chapters:
        try:
            html = chapter.file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            # 个别章节文件读取失败不应该影响整本书可用，跳过即可
            continue

        soup = BeautifulSoup(html, "lxml")
        # separator=" " 避免标签间文字被意外粘连成一个词
        text = soup.get_text(separator=" ")
        index.append(ChapterText(chapter_index=chapter.index, plain_text=text))

    return index


def search(
    index: list[ChapterText],
    query: str,
    book: EpubBook,
    context_chars: int = 40,
    max_results: int = 200,
) -> list[SearchResult]:
    """
    在已构建好的搜索索引里查找关键词，大小写不敏感。

    参数:
        index: build_search_index() 的返回值
        query: 搜索关键词
        book: 用于查回章节标题
        context_chars: 命中位置前后各取多少字符作为上下文片段
        max_results: 结果数量上限，防止超长书 + 超常见词导致结果列表爆炸

    返回:
        按章节顺序排列的搜索结果列表
    """
    query = query.strip()
    if not query:
        return []

    query_lower = query.lower()
    results: list[SearchResult] = []

    chapter_title_map = {ch.index: ch.title for ch in book.chapters}

    for chapter_text in index:
        text = chapter_text.plain_text
        text_lower = text.lower()

        start = 0
        while True:
            pos = text_lower.find(query_lower, start)
            if pos == -1:
                break

            snippet_start = max(0, pos - context_chars)
            snippet_end = min(len(text), pos + len(query) + context_chars)
            snippet = text[snippet_start:snippet_end].strip()
            # 给省略号提示这只是片段，不是完整句子
            if snippet_start > 0:
                snippet = "…" + snippet
            if snippet_end < len(text):
                snippet = snippet + "…"

            results.append(
                SearchResult(
                    chapter_index=chapter_text.chapter_index,
                    chapter_title=chapter_title_map.get(
                        chapter_text.chapter_index, ""
                    ),
                    snippet=snippet,
                    match_start=pos,
                )
            )

            if len(results) >= max_results:
                return results

            start = pos + len(query)  # 避免重叠匹配导致死循环或结果重复

    return results
