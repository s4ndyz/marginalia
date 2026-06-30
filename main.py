"""
main.py — Marginalia 入口文件

运行方式:
    uv run main.py
    uv run main.py /path/to/book.epub   # 启动时直接打开某本书
"""

import sys

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Marginalia")

    window = MainWindow()
    window.show()

    # 支持命令行参数直接打开一本书，方便调试
    if len(sys.argv) > 1:
        window.open_book(sys.argv[1])

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
