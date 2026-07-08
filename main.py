"""
main.py — Marginalia 入口

启动后先显示书库，双击书封面进入阅读器，阅读器里点「书库」返回。

运行：
    uv run main.py
    uv run main.py /path/to/book.epub   # 直接跳过书库进入阅读器
"""

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QStackedWidget

from ui.library_view import LibraryView
from ui.main_window import MainWindow


class App(QStackedWidget):
    """
    顶层容器：QStackedWidget 在书库（index 0）和阅读器（index 1）之间切换。
    """

    IDX_LIBRARY = 0
    IDX_READER  = 1

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Marginalia")
        self.resize(1200, 1100)

        self.library_view = LibraryView(on_open_book=self._open_book)
        self.reader       = MainWindow(on_back_to_library=self._back_to_library)

        self.addWidget(self.library_view)   # index 0
        self.addWidget(self.reader)         # index 1

        self.setCurrentIndex(self.IDX_LIBRARY)

    def _open_book(self, epub_path: str) -> None:
        self.reader.open_book(epub_path)
        self.setCurrentIndex(self.IDX_READER)

    def _back_to_library(self) -> None:
        self.library_view.refresh()
        self.setCurrentIndex(self.IDX_LIBRARY)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Marginalia")

    window = App()
    window.show()

    # 命令行参数直接打开某本书（跳过书库）
    if len(sys.argv) > 1:
        epub_path = sys.argv[1]
        if Path(epub_path).exists():
            window._open_book(epub_path)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
