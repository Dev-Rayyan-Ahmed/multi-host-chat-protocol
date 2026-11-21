# client/ui/components/composer.py
"""Bottom-anchored message composer with a live UTF-8 byte counter."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent, QTextOption
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from client.state import utf8_len
from client.ui import icons, theme
from client.ui.components.widgets import label, repolish
from common.protocol import MAX_TEXT_BYTES

MAX_LINES = 8


class _Input(QPlainTextEdit):
    submit = Signal()

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.submit.emit()
            return
        super().keyPressEvent(e)


class Composer(QFrame):
    sendRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Composer")
        self._enabled = True
        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 10, 10, 10)
        outer.setSpacing(12)

        col = QVBoxLayout()
        col.setSpacing(4)
        self.input = _Input()
        self.input.setObjectName("ComposerInput")
        self.input.setTabChangesFocus(True)
        self.input.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.input.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.input.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.input.setFont(theme.font(10.5))
        self.input.document().setDocumentMargin(2)
        self.input.setAccessibleName("Message")
        self.input.installEventFilter(self)
        self.input.textChanged.connect(self._changed)
        self.input.submit.connect(self._submit)
        col.addWidget(self.input)

        meta = QHBoxLayout()
        meta.setSpacing(8)
        self.hint = label("Enter to send  ·  Shift+Enter for a new line", role="subtle", size=8.3)
        self.counter = label(f"0 / {MAX_TEXT_BYTES} bytes", size=8.3, color=theme.TEXT_SUBTLE)
        self.counter.setToolTip("Messages are limited to 2000 UTF-8 bytes (emoji and accents use more than one byte).")
        meta.addWidget(self.hint)
        meta.addStretch(1)
        meta.addWidget(self.counter)
        col.addLayout(meta)
        outer.addLayout(col, 1)

        self.send_btn = QPushButton("Send")
        self.send_btn.setProperty("kind", "primary")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setIcon(icons.icon("send", "#FFFFFF", size=16))
        self.send_btn.setIconSize(QSize(16, 16))
        self.send_btn.setFixedHeight(40)
        self.send_btn.setToolTip("Send (Enter)")
        self.send_btn.clicked.connect(self._submit)
        outer.addWidget(self.send_btn, 0, Qt.AlignmentFlag.AlignBottom)
        self._changed()

    # ------------------------------------------------------------ api
    def set_placeholder(self, text: str) -> None:
        self.input.setPlaceholderText(text)

    def set_composer_enabled(self, enabled: bool, placeholder: str) -> None:
        self._enabled = enabled
        self.input.setReadOnly(not enabled)
        self.input.setPlaceholderText(placeholder)
        self.setProperty("disabled", not enabled)
        repolish(self)
        self._changed()

    def focus(self) -> None:
        self.input.setFocus()

    def clear(self) -> None:
        self.input.clear()

    # ------------------------------------------------------------ internals
    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is self.input and ev.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.setProperty("focused", ev.type() == QEvent.Type.FocusIn and self._enabled)
            repolish(self)
        return super().eventFilter(obj, ev)

    def _changed(self) -> None:
        text = self.input.toPlainText()
        n = utf8_len(text.strip())
        self.counter.setText(f"{n} / {MAX_TEXT_BYTES} bytes")
        color = theme.TEXT_SUBTLE
        if n > MAX_TEXT_BYTES:
            color = theme.ERROR_SOFT
        elif n > MAX_TEXT_BYTES * 0.9:
            color = theme.WARNING
        self.counter.setStyleSheet(f"color: {color};")
        self.send_btn.setEnabled(self._enabled and bool(text.strip()) and n <= MAX_TEXT_BYTES)
        # auto-grow between 1 and MAX_LINES lines
        fm = self.input.fontMetrics()
        doc_h = self.input.document().size().height() * fm.lineSpacing()
        line_h = fm.lineSpacing()
        h = int(min(max(doc_h, line_h), line_h * MAX_LINES)) + 10
        self.input.setFixedHeight(max(h, line_h + 10))

    def _submit(self) -> None:
        if not self._enabled:
            return
        text = self.input.toPlainText().strip()
        if not text or utf8_len(text) > MAX_TEXT_BYTES:
            return
        self.sendRequested.emit(text)
