"""
theme.py — 浅色 / 深色主题

设计思路：
    项目里各个界面文件（main_window.py / library_view.py / meta_editor.py /
    epub_editor.py）原本到处写死颜色值（"#fafafa"、"#333" 这种）。
    没有用 Qt 的 QPalette 自动铺全局色，是因为这些界面大量用了自定义
    QSS（比如 QListWidget::item:selected 这种细粒度选择器），QPalette
    管不到这些地方，改了也白改。

    所以做法是：把颜色值都换成从 Theme 对象里取的"语义化"token
   （比如 theme.bg_toolbar 而不是 "#fafafa"），每个界面自己保留一个
    "重新上色"的方法，主题切换时被统一调用一次，把所有 setStyleSheet
    重新跑一遍新颜色。

    ThemeManager 是全局单例：
      - 记住当前浅色/深色（存 QSettings，下次启动记得住）
      - 切换时发一个 Qt Signal，所有打开着的窗口/对话框都连着这个信号，
        收到就各自刷新自己的样式
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtGui import QColor, QPalette


@dataclass(frozen=True)
class Theme:
    name: str
    is_dark: bool

    # 背景
    bg: str              # 弹窗/大面积容器背景
    bg_toolbar: str       # 顶部工具栏
    bg_sidebar: str       # 侧边栏 / 章节列表
    bg_input: str         # 输入框、文本编辑区背景
    bg_hover: str         # 悬停态背景
    bg_selected: str      # 选中态背景（列表项、目录树节点）

    # 边框
    border: str
    border_input: str

    # 文字
    text: str              # 主文字
    text_secondary: str     # 次要文字（说明、副标题）
    text_muted: str         # 更弱文字（占位符、禁用态、页脚小字）

    # 主操作按钮（保存、导入这类强调按钮）
    button_primary_bg: str
    button_primary_text: str
    button_primary_hover: str

    # 危险 / 提示色
    danger: str

    # 阅读区域网页容器背景（QWebEngineView 加载前的底色，跟正文内容
    # 本身的深色 CSS 注入是两回事，见 main_window.py 的 _READER_DARK_CSS_JS）
    reader_bg: str

    # 高亮选中的圆点占位色（BookCard 无封面时的色块，两种主题共用同一组，
    # 因为它代表"书籍身份色"，不随主题变化，这里只是占位方便未来扩展）


LIGHT = Theme(
    name="light", is_dark=False,
    bg="#ffffff",
    bg_toolbar="#fafafa",
    bg_sidebar="#f5f5f3",
    bg_input="#ffffff",
    bg_hover="#f0eeea",
    bg_selected="#e8e6df",
    border="#e5e5e5",
    border_input="#d8d6cf",
    text="#1a1a1a",
    text_secondary="#555555",
    text_muted="#888888",
    button_primary_bg="#2c2c2c",
    button_primary_text="#ffffff",
    button_primary_hover="#111111",
    danger="#cc0000",
    reader_bg="#fdfdfb",
)

DARK = Theme(
    name="dark", is_dark=True,
    bg="#1e1e1e",
    bg_toolbar="#252525",
    bg_sidebar="#202020",
    bg_input="#2a2a2a",
    bg_hover="#333333",
    bg_selected="#3a3a38",
    border="#3a3a3a",
    border_input="#454543",
    text="#e6e4e0",
    text_secondary="#b0aeaa",
    text_muted="#7a7874",
    button_primary_bg="#e6e4e0",
    button_primary_text="#1a1a1a",
    button_primary_hover="#ffffff",
    danger="#e57373",
    reader_bg="#181818",
)


_ORG = "Marginalia"
_APP = "Marginalia"
_SETTINGS_KEY = "theme/is_dark"


class ThemeManager(QObject):
    """全局单例。用 ThemeManager.instance() 取，不要自己 new 一个。"""

    changed = Signal(object)  # 发出新的 Theme 实例

    _instance: "ThemeManager | None" = None

    def __init__(self) -> None:
        super().__init__()
        is_dark = QSettings(_ORG, _APP).value(_SETTINGS_KEY, False, type=bool)
        self._current: Theme = DARK if is_dark else LIGHT

    @classmethod
    def instance(cls) -> "ThemeManager":
        if cls._instance is None:
            cls._instance = ThemeManager()
        return cls._instance

    @property
    def current(self) -> Theme:
        return self._current

    def set_dark(self, is_dark: bool) -> None:
        new_theme = DARK if is_dark else LIGHT
        if new_theme.name == self._current.name:
            return
        self._current = new_theme
        QSettings(_ORG, _APP).setValue(_SETTINGS_KEY, is_dark)
        self.changed.emit(new_theme)

    def toggle(self) -> None:
        self.set_dark(not self._current.is_dark)


def qt_palette(theme: Theme) -> QPalette:
    """
    生成一份 QPalette，配合 QApplication.setStyle("Fusion") 使用。

    作用范围：主要是那些"没有被项目自己 setStyleSheet 覆盖"的地方——
    QMessageBox、QFileDialog、QMenu、右键菜单、滚动条这些系统原生
    控件。项目里自定义的工具栏/侧边栏/列表 QSS 优先级更高，
    该走 theme.py 里各界面自己的"重新上色"方法，跟这份 palette 互不冲突。
    """
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(theme.bg))
    p.setColor(QPalette.ColorRole.WindowText, QColor(theme.text))
    p.setColor(QPalette.ColorRole.Base, QColor(theme.bg_input))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(theme.bg_hover))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(theme.bg))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(theme.text))
    p.setColor(QPalette.ColorRole.Text, QColor(theme.text))
    p.setColor(QPalette.ColorRole.Button, QColor(theme.bg_toolbar))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(theme.text))
    p.setColor(QPalette.ColorRole.BrightText, QColor("#ff5555"))
    p.setColor(QPalette.ColorRole.Link, QColor("#4a9eff"))
    p.setColor(QPalette.ColorRole.Highlight, QColor(theme.bg_selected))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(theme.text))
    p.setColor(
        QPalette.ColorRole.PlaceholderText, QColor(theme.text_muted)
    )
    if theme.is_dark:
        p.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Text,
            QColor(theme.text_muted),
        )
        p.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.WindowText,
            QColor(theme.text_muted),
        )
    return p
