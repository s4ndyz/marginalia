"""
epub_meta.py — epub 元数据的读取和写回

epub 的元数据存在 OPF 文件（content.opf）里，格式是 Dublin Core XML：
    <dc:title>书名</dc:title>
    <dc:creator>作者</dc:creator>
    ...

读取：直接用 ebooklib 解析。
写回：用 lxml 定位到 OPF 文件，原地修改 XML 节点，再重新打包 zip。

为什么不用 ebooklib 写回：
    ebooklib 的 epub.write_epub() 会重建整个 epub 结构，
    可能丢失原书里一些非标准字段或自定义内容。
    直接操作 zip + lxml 只改我们关心的节点，更安全。
"""

from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from lxml import etree


# Dublin Core 命名空间
_DC  = "http://purl.org/dc/elements/1.1/"
_OPF = "http://www.idpf.org/2007/opf"

_DC_FIELDS = {
    "title":       f"{{{_DC}}}title",
    "creator":     f"{{{_DC}}}creator",
    "language":    f"{{{_DC}}}language",
    "publisher":   f"{{{_DC}}}publisher",
    "date":        f"{{{_DC}}}date",
    "description": f"{{{_DC}}}description",
    "identifier":  f"{{{_DC}}}identifier",
}


@dataclass
class EpubMeta:
    title:       str = ""
    author:      str = ""   # dc:creator
    language:    str = ""
    publisher:   str = ""
    date:        str = ""   # 出版日期，通常是 YYYY 或 YYYY-MM-DD
    description: str = ""
    identifier:  str = ""   # ISBN / UUID，只读展示，不允许编辑


# ------------------------------------------------------------------
# 读取
# ------------------------------------------------------------------

def read_meta(epub_path: str | Path) -> EpubMeta:
    """从 epub 文件读取元数据，任何字段读不到就留空字符串。"""
    epub_path = Path(epub_path)

    try:
        opf_xml = _get_opf_xml(epub_path)
    except Exception:
        return EpubMeta()

    root = etree.fromstring(opf_xml)
    metadata = root.find(f"{{{_OPF}}}metadata")
    if metadata is None:
        # 兼容没有命名空间的 opf（极少见）
        metadata = root.find("metadata")
    if metadata is None:
        return EpubMeta()

    def _get(tag_key: str) -> str:
        el = metadata.find(_DC_FIELDS[tag_key])
        return (el.text or "").strip() if el is not None else ""

    return EpubMeta(
        title=       _get("title"),
        author=      _get("creator"),
        language=    _get("language"),
        publisher=   _get("publisher"),
        date=        _get("date"),
        description= _get("description"),
        identifier=  _get("identifier"),
    )


# ------------------------------------------------------------------
# 写回
# ------------------------------------------------------------------

def write_meta(epub_path: str | Path, meta: EpubMeta) -> None:
    """
    把 EpubMeta 里的字段写回 epub 文件。
    操作流程：
      1. 备份原文件（.bak），出错时可以还原
      2. 在 zip 里找到 OPF 文件，解析 XML
      3. 原地更新 <dc:*> 节点的文本内容
      4. 把修改后的 OPF 写回 zip（重建 zip，因为 zipfile 不支持原地修改）
      5. 成功后删除备份
    """
    epub_path = Path(epub_path).resolve()
    bak_path  = epub_path.with_suffix(".epub.bak")

    # 1. 备份
    shutil.copy2(epub_path, bak_path)

    try:
        _write_meta_inplace(epub_path, meta)
        bak_path.unlink(missing_ok=True)
    except Exception:
        # 出错时还原备份
        shutil.copy2(bak_path, epub_path)
        bak_path.unlink(missing_ok=True)
        raise


def _write_meta_inplace(epub_path: Path, meta: EpubMeta) -> None:
    opf_zip_name = _find_opf_name(epub_path)

    # 读出所有文件内容（除了 OPF，OPF 单独处理）
    with zipfile.ZipFile(epub_path, "r") as zin:
        all_items = {name: zin.read(name) for name in zin.namelist()}

    # 修改 OPF XML
    opf_xml  = all_items[opf_zip_name]
    new_xml  = _patch_opf(opf_xml, meta)
    all_items[opf_zip_name] = new_xml

    # 重新打包（mimetype 必须是第一个且不压缩）
    tmp_path = epub_path.with_suffix(".epub.tmp")
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
        # mimetype 不压缩，必须排第一
        if "mimetype" in all_items:
            zout.writestr(
                zipfile.ZipInfo("mimetype"),   # compression=ZIP_STORED
                all_items.pop("mimetype"),
            )
        for name, data in all_items.items():
            zout.writestr(name, data)

    tmp_path.replace(epub_path)


def _patch_opf(opf_bytes: bytes, meta: EpubMeta) -> bytes:
    """
    解析 OPF XML，更新指定的 dc:* 字段，返回修改后的 bytes。
    只更新非空的字段（空字符串 = 用户清空了，也写回）。
    identifier 不可编辑，跳过。
    """
    parser = etree.XMLParser(remove_blank_text=False)
    root   = etree.fromstring(opf_bytes, parser)

    metadata = root.find(f"{{{_OPF}}}metadata")
    if metadata is None:
        metadata = root.find("metadata")
    if metadata is None:
        return opf_bytes   # 找不到 metadata，原样返回

    updates = {
        "title":       meta.title,
        "creator":     meta.author,
        "language":    meta.language,
        "publisher":   meta.publisher,
        "date":        meta.date,
        "description": meta.description,
    }

    for key, value in updates.items():
        tag = _DC_FIELDS[key]
        el  = metadata.find(tag)
        if el is not None:
            el.text = value
        elif value:
            # 字段原本不存在但用户填了值，新建节点
            new_el = etree.SubElement(metadata, tag)
            new_el.text = value

    return etree.tostring(root, xml_declaration=True,
                          encoding="utf-8", pretty_print=False)


# ------------------------------------------------------------------
# 内部工具
# ------------------------------------------------------------------

def _get_opf_xml(epub_path: Path) -> bytes:
    """从 epub zip 包里读出 OPF 文件的原始 bytes。"""
    with zipfile.ZipFile(epub_path, "r") as zf:
        return zf.read(_find_opf_name(epub_path))


def _find_opf_name(epub_path: Path) -> str:
    """通过 META-INF/container.xml 找到 OPF 文件在 zip 里的路径。"""
    with zipfile.ZipFile(epub_path, "r") as zf:
        container = zf.read("META-INF/container.xml")

    root = etree.fromstring(container)
    ns   = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
    rf   = root.find(".//c:rootfile", ns)
    if rf is None:
        raise ValueError("container.xml 里找不到 rootfile")
    return rf.get("full-path")
