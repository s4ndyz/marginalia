"""
meta_editor.py — epub 元数据编辑对话框

调用方式（在 main_window.py 里）：
    dlg = MetaEditorDialog(epub_path, parent=self)
    if dlg.exec():
        # 用户点了保存，元数据已写回 epub
        # 如果书在书库里，同步更新书库记录
        sync_library(epub_path, dlg.saved_meta)
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.epub_meta import EpubMeta, read_meta, write_meta
from ui.theme import Theme, ThemeManager


def _short_date(value: str) -> str:
    """
    把 ISO 8601 时间戳截断成纯日期，用于展示和编辑。
    "2021-06-14T17:00:00+00:00" -> "2021-06-14"
    非标准格式或已经是短日期的原样返回。
    """
    value = value.strip()
    if len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value[:10]
    return value


class MetaEditorDialog(QDialog):
    """
    模态对话框，编辑一本 epub 的元数据。

    属性：
        saved_meta: 用户点「保存」后写回的 EpubMeta，可供调用方同步书库。
    """

    def __init__(self, epub_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.epub_path  = epub_path
        self.saved_meta: EpubMeta | None = None

        self.setWindowTitle("编辑书籍信息")
        self.setMinimumWidth(460)
        self.setModal(True)

        self._meta = read_meta(epub_path)
        self._theme_mgr = ThemeManager.instance()
        self._build_ui()
        self._apply_theme(self._theme_mgr.current)
        self._theme_mgr.changed.connect(self._apply_theme)
        # 对话框关闭后就没用了，及时断开，不然 ThemeManager 会一直
        # 持有一个指向已销毁窗口的连接
        self.finished.connect(
            lambda _: self._theme_mgr.changed.disconnect(self._apply_theme)
        )

    # ------------------------------------------------------------------
    # 构建 UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(16)
        root.setContentsMargins(24, 20, 24, 20)

        # 标题
        self._heading = QLabel("书籍信息")
        root.addWidget(self._heading)

        # 表单
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )

        # 表单里的输入框/说明文字标签，按主题重新上色时要挨个遍历，
        # 所以在构建时把它们都收集进这两个列表
        self._field_widgets: list[QLineEdit | QPlainTextEdit] = []
        self._label_widgets: list[QLabel] = []

        def _line(value: str) -> QLineEdit:
            w = QLineEdit(value)
            self._field_widgets.append(w)
            return w

        def _label(text: str) -> QLabel:
            l = QLabel(text)
            self._label_widgets.append(l)
            return l

        self._f_title     = _line(self._meta.title)
        self._f_author    = _line(self._meta.author)
        self._f_language  = _line(self._meta.language)
        self._f_publisher = _line(self._meta.publisher)
        self._f_date      = _line(_short_date(self._meta.date))
        self._f_date.setPlaceholderText("YYYY-MM-DD")

        self._f_description = QPlainTextEdit(self._meta.description)
        self._field_widgets.append(self._f_description)
        self._f_description.setFixedHeight(90)
        self._f_description.setPlaceholderText("简介（可选）")

        form.addRow(_label("书名"), self._f_title)
        form.addRow(_label("作者"), self._f_author)
        form.addRow(_label("语言"), self._f_language)
        form.addRow(_label("出版商"), self._f_publisher)
        form.addRow(_label("出版日期"), self._f_date)
        form.addRow(_label("简介"), self._f_description)

        # identifier 只读展示
        self._id_label = None
        if self._meta.identifier:
            self._id_label = QLabel(self._meta.identifier)
            self._id_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            form.addRow(_label("ISBN / ID"), self._id_label)

        root.addLayout(form)

        # 按钮
        self._error_label = QLabel("")
        self._error_label.setVisible(False)
        root.addWidget(self._error_label)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        self._buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self._buttons.accepted.connect(self._save)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

    # ------------------------------------------------------------------
    # 主题
    # ------------------------------------------------------------------

    def _apply_theme(self, t: Theme) -> None:
        self.setStyleSheet(f"QDialog {{ background: {t.bg}; }}")
        self._heading.setStyleSheet(
            f"font-size: 16px; font-weight: 600; color: {t.text};"
        )
        for lbl in self._label_widgets:
            lbl.setStyleSheet(f"font-size: 13px; color: {t.text_secondary};")

        field_style = f"""
            QLineEdit, QPlainTextEdit {{
                border: 1px solid {t.border_input};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
                background: {t.bg_input};
                color: {t.text};
            }}
            QLineEdit:focus, QPlainTextEdit:focus {{
                border-color: {t.text_muted};
            }}
        """
        for w in self._field_widgets:
            w.setStyleSheet(field_style)

        if self._id_label is not None:
            self._id_label.setStyleSheet(f"font-size: 12px; color: {t.text_muted};")

        self._error_label.setStyleSheet(f"color: {t.danger}; font-size: 12px;")

        self._buttons.setStyleSheet(f"""
            QPushButton {{
                border: 1px solid {t.border_input}; border-radius: 6px;
                padding: 6px 20px; font-size: 13px; background: {t.bg_input};
                color: {t.text_secondary}; min-width: 72px;
            }}
            QPushButton:hover {{ background: {t.bg_hover}; }}
            QPushButton[text="保存"] {{
                background: {t.button_primary_bg}; color: {t.button_primary_text};
                border-color: {t.button_primary_bg};
            }}
            QPushButton[text="保存"]:hover {{ background: {t.button_primary_hover}; }}
        """)

    # ------------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------------

    def _save(self) -> None:
        title = self._f_title.text().strip()
        if not title:
            self._error_label.setText("书名不能为空")
            self._error_label.setVisible(True)
            self._f_title.setFocus()
            return

        meta = EpubMeta(
            title=       title,
            author=      self._f_author.text().strip(),
            language=    self._f_language.text().strip(),
            publisher=   self._f_publisher.text().strip(),
            date=        self._f_date.text().strip(),
            description= self._f_description.toPlainText().strip(),
            identifier=  self._meta.identifier,   # 不允许修改
        )

        try:
            write_meta(self.epub_path, meta)
        except Exception as e:
            QMessageBox.critical(
                self, "保存失败",
                f"写回 epub 时出错：\n{e}\n\n原文件已自动还原。"
            )
            return

        self.saved_meta = meta
        self.accept()
