# client/ui/dialogs/modals.py
"""In-window modal overlays (dimmed backdrop + compact card)."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QVBoxLayout, QWidget

from client.state import username_problem
from client.ui import theme
from client.ui.components.widgets import Field, IconButton, button, label


class Modal(QWidget):
    """Covers its parent, dims it, and centers `self.card`. Esc / backdrop click closes."""

    closed = Signal()

    def __init__(self, parent: QWidget, width: int = 420) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.card = QFrame(self)
        self.card.setObjectName("ModalCard")
        self.card.setStyleSheet(
            f"QFrame#ModalCard {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER_STRONG}; border-radius: 14px; }}"
        )
        self.card.setFixedWidth(width)
        self.body = QVBoxLayout(self.card)
        self.body.setContentsMargins(24, 20, 24, 24)
        self.body.setSpacing(0)
        parent.installEventFilter(self)
        self._fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def header(self, title: str) -> None:
        row = QHBoxLayout()
        row.addWidget(label(title, size=13, weight=QFont.Weight.DemiBold), 1)
        close = IconButton("x", "Close (Esc)", 30, 17)
        close.clicked.connect(self.close_modal)
        row.addWidget(close)
        self.body.addLayout(row)

    def open(self) -> None:
        self.setGeometry(self.parentWidget().rect())
        self._center()
        self.show()
        self.raise_()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self.setFocus()

    def close_modal(self) -> None:
        self.closed.emit()
        self.close()

    def _center(self) -> None:
        self.card.adjustSize()
        self.card.move((self.width() - self.card.width()) // 2, max(40, (self.height() - self.card.height()) // 2 - 30))

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is self.parentWidget() and ev.type() == QEvent.Type.Resize:
            self.setGeometry(self.parentWidget().rect())
            self._center()
        return False

    def paintEvent(self, _e) -> None:  # noqa: N802
        QPainter(self).fillRect(self.rect(), QColor(6, 4, 10, 165))

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if not self.card.geometry().contains(e.position().toPoint()):
            self.close_modal()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.close_modal()
            return
        super().keyPressEvent(e)


class NewMessageModal(Modal):
    """Start a DM with any registered username (online or offline)."""

    submitted = Signal(str)

    def __init__(self, parent: QWidget, me: str) -> None:
        super().__init__(parent, 420)
        self.me = me
        self.header("New message")
        self.body.addSpacing(16)
        self.field = Field("user", "Enter a username…")
        self.field.edit.setMaxLength(16)
        self.body.addWidget(self.field)
        self.body.addSpacing(6)
        self.error = label("", role="error")
        self.error.hide()
        self.body.addWidget(self.error)
        self.body.addSpacing(8)
        note = label("Message any registered user. If they're offline, the server queues\n"
                     "your message (up to 30) and delivers it when they next log in.",
                     role="muted", size=8.9)
        note.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.body.addWidget(note)
        self.body.addSpacing(18)
        self.go = button("Start conversation", "primary")
        self.go.clicked.connect(self._submit)
        self.body.addWidget(self.go)
        self.field.returnPressed.connect(self._submit)
        self.field.textChanged.connect(self._clear)

    def open(self) -> None:
        super().open()
        QTimer.singleShot(0, self.field.setFocus)

    def _clear(self, _t: str) -> None:
        self.error.hide()
        self.field.set_invalid(False)
        self._center()

    def _submit(self) -> None:
        name = self.field.text().strip()
        problem = username_problem(name)
        if not problem and name.lower() == self.me.lower():
            problem = "That's you — pick someone else."
        if problem:
            self.error.setText(problem)
            self.error.show()
            self.field.set_invalid(True)
            self._center()
            return
        self.submitted.emit(name)
        self.close_modal()


class ConfirmModal(Modal):
    confirmed = Signal()

    def __init__(self, parent: QWidget, title: str, text: str, confirm_text: str, danger: bool = True) -> None:
        super().__init__(parent, 400)
        self.header(title)
        self.body.addSpacing(8)
        t = label(text, role="muted", size=9.6)
        t.setWordWrap(True)
        self.body.addWidget(t)
        self.body.addSpacing(22)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        cancel = button("Cancel", "secondary")
        cancel.clicked.connect(self.close_modal)
        ok = button(confirm_text, "danger" if danger else "primary")
        ok.clicked.connect(self._ok)
        row.addWidget(cancel)
        row.addWidget(ok)
        self.body.addLayout(row)
        self._ok_btn = ok

    def open(self) -> None:
        super().open()
        QTimer.singleShot(0, self._ok_btn.setFocus)

    def _ok(self) -> None:
        self.confirmed.emit()
        self.close_modal()
