"""
epub_writer.py — 把章节内容修改写回 epub 文件

设计原则：
    只做一件事：接收"zip内路径 → 新内容"的映射，
    把对应文件替换后重新打包成合法的 epub。
    不解析内容、不验证 HTML——这是调用方（编辑器 UI）的职责。

与 epub_meta.py 的关系：
    共用相同的备份 + 重打包模式。
    可以组合使用：先 write_chapters 再 write_meta，或者合并调用
    write_epub（同时改内容和元数据）。

zip 内路径 vs 临时目录路径：
    epub_loader 把整本书解压到 /tmp/marginalia_xxx/ 目录下，
    保留了 epub 内的目录结构。比如：
        zip 内路径:  EPUB/chap01.xhtml
        临时目录路径: /tmp/marginalia_xxx/EPUB/chap01.xhtml
    temp_path_to_zip_name() 负责做这个转换，
    上层编辑器拿到 chapter.file_path 后用它换算出 zip 内路径。
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path


def temp_path_to_zip_name(temp_path: str | Path, temp_root: str | Path) -> str:
    """
    把解压临时目录里的文件路径转换回 zip 包内的相对路径。

    例子：
        temp_root = "/tmp/marginalia_abc123"
        temp_path = "/tmp/marginalia_abc123/EPUB/chap01.xhtml"
        返回:      "EPUB/chap01.xhtml"

    参数：
        temp_path: chapter.file_path，解压后的本地绝对路径
        temp_root: 解压根目录，即 book.temp_dir.name
    """
    return str(Path(temp_path).relative_to(Path(temp_root)))


def write_chapters(
    epub_path: str | Path,
    updates: dict[str, bytes],
) -> None:
    """
    把修改后的章节内容写回 epub 文件。

    参数：
        epub_path: epub 文件的路径
        updates:   {zip内路径: 新内容bytes} 的字典
                   例如 {"EPUB/chap01.xhtml": b"<html>...</html>"}

    流程：
        1. 备份原文件（.bak）
        2. 读出所有 zip 条目
        3. 替换 updates 里指定的条目
        4. 重新打包（mimetype 第一且不压缩，符合 epub 规范）
        5. 成功后删除备份；失败时自动还原
    """
    epub_path = Path(epub_path).resolve()
    if not epub_path.exists():
        raise FileNotFoundError(f"epub 文件不存在：{epub_path}")
    if not updates:
        return  # 没有改动，直接返回

    bak_path = epub_path.with_suffix(".epub.bak")
    shutil.copy2(epub_path, bak_path)

    try:
        _repack(epub_path, updates)
        bak_path.unlink(missing_ok=True)
    except Exception:
        shutil.copy2(bak_path, epub_path)
        bak_path.unlink(missing_ok=True)
        raise


def write_epub(
    epub_path: str | Path,
    chapter_updates: dict[str, bytes] | None = None,
    extra_updates: dict[str, bytes] | None = None,
) -> None:
    """
    通用写入入口，把任意 zip 条目的修改合并后一次性重打包。

    chapter_updates: 章节 HTML 内容的修改
    extra_updates:   其他文件的修改（OPF、CSS 等），由更高层传入

    分开两个参数只是语义清晰，实际合并后统一处理。
    """
    all_updates: dict[str, bytes] = {}
    if chapter_updates:
        all_updates.update(chapter_updates)
    if extra_updates:
        all_updates.update(extra_updates)
    write_chapters(epub_path, all_updates)


def read_chapter_html(
    epub_path: str | Path,
    zip_name: str,
) -> str:
    """
    从 epub zip 里读出某个章节文件的原始 HTML 字符串。

    编辑器打开章节时用这个拿到"干净的原始内容"，
    而不是从解压临时目录读（临时目录可能因为高亮注入而被修改过）。
    """
    with zipfile.ZipFile(epub_path, "r") as zf:
        raw = zf.read(zip_name)
    # 尝试 utf-8，失败时退回 latin-1（老 epub 偶尔会用）
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


# ------------------------------------------------------------------
# 内部工具
# ------------------------------------------------------------------

def _repack(epub_path: Path, updates: dict[str, bytes]) -> None:
    """读出所有条目，替换 updates 里的部分，重新打包。"""

    with zipfile.ZipFile(epub_path, "r") as zin:
        # 保留原始条目的压缩信息（compress_type），mimetype 必须 STORED
        entries: list[tuple[zipfile.ZipInfo, bytes]] = []
        for info in zin.infolist():
            data = updates.get(info.filename, zin.read(info.filename))
            entries.append((info, data))

    tmp_path = epub_path.with_suffix(".epub.tmp")
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for info, data in entries:
            if info.filename == "mimetype":
                # epub 规范：mimetype 必须是第一个条目且不压缩
                zi = zipfile.ZipInfo("mimetype")
                zi.compress_type = zipfile.ZIP_STORED
                zout.writestr(zi, data)
            else:
                # 已修改的条目用默认压缩；未修改的保留原压缩类型
                if info.filename in updates:
                    zout.writestr(info.filename, data)
                else:
                    info_copy = zipfile.ZipInfo(info.filename)
                    info_copy.compress_type = info.compress_type
                    info_copy.comment       = info.comment
                    info_copy.date_time     = info.date_time
                    zout.writestr(info_copy, data)

    tmp_path.replace(epub_path)
