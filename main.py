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
from ui.theme import ThemeManager, qt_palette


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

    # Fusion 是唯一一个"完全听 QPalette 指挥"的内置 style——
    # macOS 原生风格会无视我们设的深色调色板，该亮还是亮。
    # 这份 palette 主要管 QMessageBox / QFileDialog / 右键菜单这些
    # 项目里没有手动 setStyleSheet 的原生控件；工具栏/侧边栏这些
    # 自定义外观各自在 theme.py 描述的"重新上色"方法里处理，互不冲突。
    app.setStyle("Fusion")
    theme_mgr = ThemeManager.instance()
    app.setPalette(qt_palette(theme_mgr.current))
    theme_mgr.changed.connect(lambda theme: app.setPalette(qt_palette(theme)))

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
