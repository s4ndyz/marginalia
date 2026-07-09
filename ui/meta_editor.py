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
        self._build_ui()

    # ------------------------------------------------------------------
    # 构建 UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(16)
        root.setContentsMargins(24, 20, 24, 20)

        # 标题
        heading = QLabel("书籍信息")
        heading.setStyleSheet(
            "font-size: 16px; font-weight: 600; color: #1a1a1a;"
        )
        root.addWidget(heading)

        # 表单
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )

        _field_style = """
            QLineEdit, QPlainTextEdit {
                border: 1px solid #d8d6cf;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
                background: white;
                color: #1a1a1a;
            }
            QLineEdit:focus, QPlainTextEdit:focus {
                border-color: #888;
            }
        """
        _label_style = "font-size: 13px; color: #555;"

        def _line(value: str) -> QLineEdit:
            w = QLineEdit(value)
            w.setStyleSheet(_field_style)
            return w

        def _label(text: str) -> QLabel:
            l = QLabel(text)
            l.setStyleSheet(_label_style)
            return l

        self._f_title     = _line(self._meta.title)
        self._f_author    = _line(self._meta.author)
        self._f_language  = _line(self._meta.language)
        self._f_publisher = _line(self._meta.publisher)
        self._f_date      = _line(self._meta.date)

        self._f_description = QPlainTextEdit(self._meta.description)
        self._f_description.setStyleSheet(_field_style)
        self._f_description.setFixedHeight(90)
        self._f_description.setPlaceholderText("简介（可选）")

        form.addRow(_label("书名"), self._f_title)
        form.addRow(_label("作者"), self._f_author)
        form.addRow(_label("语言"), self._f_language)
        form.addRow(_label("出版商"), self._f_publisher)
        form.addRow(_label("出版日期"), self._f_date)
        form.addRow(_label("简介"), self._f_description)

        # identifier 只读展示
        if self._meta.identifier:
            id_label = QLabel(self._meta.identifier)
            id_label.setStyleSheet("font-size: 12px; color: #aaa;")
            id_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            form.addRow(_label("ISBN / ID"), id_label)

        root.addLayout(form)

        # 按钮
        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: #c00; font-size: 12px;")
        self._error_label.setVisible(False)
        root.addWidget(self._error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.setStyleSheet("""
            QPushButton {
                border: 1px solid #d0cec8; border-radius: 6px;
                padding: 6px 20px; font-size: 13px; background: white;
                color: #333; min-width: 72px;
            }
            QPushButton:hover { background: #f5f4f0; }
            QPushButton[text="保存"] {
                background: #2c2c2c; color: white; border-color: #2c2c2c;
            }
            QPushButton[text="保存"]:hover { background: #111; }
        """)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

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
