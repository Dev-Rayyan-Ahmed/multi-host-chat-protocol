# client/ui/components/toast.py
"""Non-blocking toast notifications stacked at the bottom-right of the window."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, QRect, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QWidget

from client.ui import icons, theme
from client.ui.components.widgets import IconButton

# Icon colour per level; the toast surface itself stays neutral (no accent stripe).
_LEVELS = {
    "success": ("#7CC4A4", "check"),
    "info": ("#B8A8DC", "info"),
    "warning": ("#D9B27A", "alert"),
    "error": ("#E58C8C", "alert-circle"),
}
TOAST_BG = "#141019"        # dark purple-black
TOAST_BORDER = "#2C2536"    # subtle neutral-violet hairline
EDGE_MARGIN = 24            # distance from the window's right / bottom edges
TOAST_WIDTH = 340


class Toast(QFrame):
    def __init__(self, host: "ToastHost", level: str, text: str, ms: int) -> None:
        super().__init__(host.window_widget)
        color, icon_name = _LEVELS.get(level, _LEVELS["info"])
        self.setObjectName("Toast")
        self.setStyleSheet(
            f"QFrame#Toast {{ background: {TOAST_BG}; border: 1px solid {TOAST_BORDER}; border-radius: 10px; }}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 11, 8, 11)
        lay.setSpacing(10)
        ic = QLabel()
        ic.setFixedSize(16, 16)
        ic.setPixmap(icons.pixmap(icon_name, color, 15, 1.9))
        msg = QLabel(text)
        msg.setWordWrap(True)
        msg.setFont(theme.font(9.4))
        msg.setStyleSheet(f"color: {theme.TEXT};")
        close = IconButton("x", "Dismiss", 22, 13, color=theme.TEXT_SUBTLE, hover=theme.TEXT)
        close.clicked.connect(self.dismiss)
        lay.addWidget(ic, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(msg, 1)
        lay.addWidget(close, 0, Qt.AlignmentFlag.AlignVCenter)
        self.host = host
        self.setFixedWidth(TOAST_WIDTH)
        self.adjustSize()

        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(0.0)
        self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._closing = False
        QTimer.singleShot(ms, self.dismiss)

    def dismiss(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._anim.stop()
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self._remove)
        self._anim.start()

    def _remove(self) -> None:
        self.host.remove(self)
        self.deleteLater()


class ToastHost:
    def __init__(self, window_widget: QWidget, bottom_margin=None, keep_clear=None) -> None:
        self.window_widget = window_widget
        # callable -> px from the bottom edge (the chat screen needs room for the composer)
        self.bottom_margin = bottom_margin or (lambda: EDGE_MARGIN)
        # callable -> list[QRect] (window_widget coords) that toasts must not cover
        self.keep_clear = keep_clear or (lambda: [])
        self.toasts: list[Toast] = []
        window_widget.installEventFilter(_ResizeFilter(self, window_widget))

    def show(self, level: str, text: str, ms: int = 4200) -> None:
        # Collapse identical consecutive toasts
        if self.toasts and getattr(self.toasts[-1], "_text", None) == (level, text):
            return
        t = Toast(self, level, text, ms)
        t._text = (level, text)
        self.toasts.append(t)
        if len(self.toasts) > 4:
            self.toasts[0].dismiss()
        t.show()
        t.raise_()
        self.layout()

    def remove(self, t: Toast) -> None:
        if t in self.toasts:
            self.toasts.remove(t)
        self.layout()

    def layout(self) -> None:
        w = self.window_widget
        for t in self.toasts:
            t.adjustSize()
        total = sum(t.height() for t in self.toasts) + 10 * max(0, len(self.toasts) - 1)
        bottom = w.height() - self.bottom_margin()
        # Bottom-right corner by default; bottom-left only if the right corner would cover
        # important controls (e.g. the connection card in a minimum-size window).
        x = w.width() - TOAST_WIDTH - EDGE_MARGIN
        area = QRect(x, bottom - total, TOAST_WIDTH, total)
        if any(area.intersects(r) for r in self.keep_clear()):
            left = QRect(EDGE_MARGIN, bottom - total, TOAST_WIDTH, total)
            if not any(left.intersects(r) for r in self.keep_clear()):
                x = EDGE_MARGIN
        y = bottom
        for t in reversed(self.toasts):
            y -= t.height()
            t.move(x, y)
            y -= 10


class _ResizeFilter(QWidget):
    def __init__(self, host: ToastHost, parent: QWidget) -> None:
        super().__init__(parent)
        self.host = host
        self.hide()

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if ev.type() == QEvent.Type.Resize:
            try:
                self.host.layout()
            except RuntimeError:  # window is being torn down; its widgets are already gone
                pass
        return False
