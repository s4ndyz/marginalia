"""
epub_loader.py

负责把一个 .epub 文件加载成"可供 QWebEngineView 直接渲染"的章节文件列表。

核心思路：
    epub 本质是一个 zip 包，里面是若干 HTML/XHTML 文件 + 图片 + CSS，
    通过 OPF (Open Packaging Format) 文件描述阅读顺序 (spine)。

    QWebEngineView 加载本地资源时，必须给它一个真实存在的文件路径
    （而不是一段 HTML 字符串），这样章节内部用相对路径引用的图片、
    CSS 才能被浏览器引擎正常解析加载。

    所以这里的做法是：把整本书解压到一个临时目录，保留原始的
    目录结构，然后返回每一章节 html 文件的"本地文件路径"列表，
    供上层 UI 用 QUrl.fromLocalFile() 加载。
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import ebooklib
from ebooklib import epub


@dataclass
class Chapter:
    """单个章节的信息"""
    index: int          # 在 spine 中的顺序，从 0 开始
    title: str          # 章节标题（尽量从目录里取，取不到就用文件名）
    file_path: Path      # 解压后的本地文件路径，可直接喂给 QWebEngineView


@dataclass
class TocEntry:
    """目录树的一个节点，保留嵌套层级（章节下还可能有小节）"""
    title: str
    chapter_index: int | None   # 对应 chapters 列表里的 index；找不到匹配章节时为 None
    children: list["TocEntry"] = field(default_factory=list)


@dataclass
class EpubBook:
    """加载完成后的一本书"""
    title: str
    author: str
    chapters: list[Chapter] = field(default_factory=list)
    toc: list[TocEntry] = field(default_factory=list)
    temp_dir: tempfile.TemporaryDirectory | None = None  # 持有引用防止被垃圾回收时删除

    def chapter_count(self) -> int:
        return len(self.chapters)


def _find_opf_relative_dir(extracted_root: str) -> Path:
    """
    根据 epub 规范，META-INF/container.xml 里指定了 OPF 文件的真实路径。
    例如 container.xml 里写着 full-path="EPUB/content.opf"，
    那么所有 item.file_name（相对 OPF 的路径）都要在前面拼上 "EPUB/" 才是真实路径。

    这是标准做法，比"猜测目录名（OEBPS/EPUB等）"更可靠，
    因为不同制作工具用的子目录名并不统一。
    """
    container_path = Path(extracted_root) / "META-INF" / "container.xml"
    if not container_path.exists():
        return Path(".")  # 极少数畸形 epub，退化为根目录

    from lxml import etree

    tree = etree.parse(str(container_path))
    ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
    rootfile = tree.find(".//c:rootfile", ns)
    if rootfile is None:
        return Path(".")

    full_path = rootfile.get("full-path")  # 例如 "EPUB/content.opf"
    return Path(full_path).parent


def _build_toc_tree(
    toc_items, filename_to_index: dict[str, int]
) -> list[TocEntry]:
    """
    把 ebooklib 的 book.toc（可能是嵌套 tuple 结构）转换成 TocEntry 树。

    filename_to_index: 文件名 -> 章节在 chapters 列表中的 index 的映射，
    用于让每个目录节点知道点击后应该跳到哪一章。
    """
    entries: list[TocEntry] = []

    for item in toc_items:
        if isinstance(item, tuple):
            link, children = item
            fname = link.href.split("#")[0] if hasattr(link, "href") else None
            chapter_idx = filename_to_index.get(fname) if fname else None
            entry = TocEntry(
                title=link.title if hasattr(link, "title") else "未命名",
                chapter_index=chapter_idx,
                children=_build_toc_tree(children, filename_to_index),
            )
            entries.append(entry)
        elif hasattr(item, "href"):
            fname = item.href.split("#")[0]
            chapter_idx = filename_to_index.get(fname)
            entries.append(
                TocEntry(title=item.title, chapter_index=chapter_idx, children=[])
            )

    return entries


def _extract_title_map(book: epub.EpubBook) -> dict[str, str]:
    """
    尝试从目录 (Table of Contents) 提取 "文件名 -> 章节标题" 的映射。
    epub 的目录可能是嵌套结构（章节下还有小节），这里做扁平化处理。
    """
    title_map: dict[str, str] = {}

    def walk(toc_items):
        for item in toc_items:
            # epub.Link 是叶子节点；tuple 表示 (父节点, 子节点列表) 的嵌套结构
            if isinstance(item, tuple):
                link, children = item
                if hasattr(link, "href"):
                    # href 可能带 #fragment，要去掉以匹配文件名
                    fname = link.href.split("#")[0]
                    title_map[fname] = link.title
                walk(children)
            elif hasattr(item, "href"):
                fname = item.href.split("#")[0]
                title_map[fname] = item.title

    walk(book.toc)
    return title_map


def load_epub(epub_path: str | Path) -> EpubBook:
    """
    加载一个 epub 文件，解压到临时目录，返回 EpubBook 对象。

    参数:
        epub_path: epub 文件的路径

    返回:
        EpubBook，包含元数据和按阅读顺序排列的章节列表
    """
    epub_path = Path(epub_path)
    if not epub_path.exists():
        raise FileNotFoundError(f"epub 文件不存在: {epub_path}")

    raw_book = epub.read_epub(str(epub_path), options={"ignore_ncx": False})

    # --- 提取元数据 ---
    title_meta = raw_book.get_metadata("DC", "title")
    creator_meta = raw_book.get_metadata("DC", "creator")
    title = title_meta[0][0] if title_meta else epub_path.stem
    author = creator_meta[0][0] if creator_meta else "未知作者"

    # --- 解压整本书到临时目录，保留目录结构 ---
    # 用 ZipFile 直接解压最省事，比逐个 item.get_content() 写文件更不容易出错
    import zipfile

    temp_dir = tempfile.TemporaryDirectory(prefix="marginalia_")
    with zipfile.ZipFile(epub_path, "r") as zf:
        zf.extractall(temp_dir.name)

    # --- 关键点：item.file_name 是相对于 OPF 文件所在目录的路径，
    # 不是相对于 zip 包根目录！例如 OPF 在 "EPUB/content.opf"，
    # 章节 item.file_name 是 "chap_01.xhtml"，
    # 实际解压出来的真实路径是 "EPUB/chap_01.xhtml"。
    # 必须找到 OPF 的目录前缀，拼回去才能定位到真实文件。
    opf_dir = _find_opf_relative_dir(temp_dir.name)

    # --- 按 spine 顺序过滤出正文文档（排除 nav 目录页等非正文项）---
    title_map = _extract_title_map(raw_book)

    spine_docs = []
    for item_id, _linear in raw_book.spine:
        item = raw_book.get_item_with_id(item_id)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        if item.get_id() == "nav" or "nav" in (item.properties or []):
            continue
        spine_docs.append(item)

    chapters: list[Chapter] = []
    for idx, item in enumerate(spine_docs):
        file_path = Path(temp_dir.name) / opf_dir / item.file_name
        chapter_title = title_map.get(item.file_name, f"第 {idx + 1} 节")
        chapters.append(Chapter(index=idx, title=chapter_title, file_path=file_path))

    # --- 构建带层级的目录树，用于侧边栏展示 ---
    # 文件名 -> chapters 列表下标的映射，让每个目录节点知道点击后该跳去哪一章
    filename_to_index = {item.file_name: idx for idx, item in enumerate(spine_docs)}
    toc_tree = _build_toc_tree(raw_book.toc, filename_to_index)

    return EpubBook(
        title=title,
        author=author,
        chapters=chapters,
        toc=toc_tree,
        temp_dir=temp_dir,
    )
