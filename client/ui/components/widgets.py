# client/ui/components/widgets.py
"""Reusable building blocks: avatars, fields, buttons, spinner, status pills."""
from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from client.ui import icons, theme


def repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def label(text: str = "", role: str | None = None, size: float | None = None,
          weight: QFont.Weight | None = None, color: str | None = None, mono: bool = False) -> QLabel:
    lbl = QLabel(text)
    if role:
        lbl.setProperty("role", role)
    if size is not None or weight is not None or mono:
        lbl.setFont(theme.font(size or 10, weight or QFont.Weight.Normal, mono=mono))
    if color:
        lbl.setStyleSheet(f"color: {color};")
    return lbl


def button(text: str, kind: str = "primary", icon_name: str | None = None,
           icon_color: str = "#FFFFFF", icon_right: bool = False) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("kind", kind)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon_name:
        b.setIcon(icons.icon(icon_name, icon_color, size=16))
        b.setIconSize(QSize(16, 16))
        if icon_right:
            b.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    return b


class IconButton(QToolButton):
    """Square icon-only button whose glyph brightens on hover."""

    def __init__(self, name: str, tooltip: str = "", size: int = 32, icon_size: int = 18,
                 color: str = theme.TEXT_MUTED, hover: str = theme.TEXT, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name, self._color, self._hover, self._isz = name, color, hover, icon_size
        self.setFixedSize(size, size)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip or name)
        self._apply(False)

    def set_icon_name(self, name: str) -> None:
        self._name = name
        self._apply(self.underMouse())

    def set_colors(self, color: str, hover: str) -> None:
        self._color, self._hover = color, hover
        self._apply(self.underMouse())

    def _apply(self, hovered: bool) -> None:
        col = self._hover if hovered else self._color
        if not self.isEnabled():
            col = "#4C4458"
        self.setIcon(icons.icon(self._name, col, size=self._isz))

    def enterEvent(self, e) -> None:  # noqa: N802
        self._apply(True)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._apply(False)
        super().leaveEvent(e)

    def changeEvent(self, e) -> None:  # noqa: N802
        if e.type() == QEvent.Type.EnabledChange:
            self._apply(False)
        super().changeEvent(e)


class Avatar(QWidget):
    """Initial-letter circle in a muted purple variation, with optional presence dot."""

    def __init__(self, name: str = "", size: int = 36, presence: bool | None = None,
                 ring: str = theme.SIDEBAR, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = name
        self._presence = presence
        self._ring = ring
        self._size = size
        self.setFixedSize(size, size)

    def set_name(self, name: str) -> None:
        self._name = name
        self.update()

    def set_presence(self, presence: bool | None) -> None:
        self._presence = presence
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self._size
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.avatar_color(self._name or "?")))
        p.drawEllipse(QRectF(0, 0, s, s))
        p.setPen(QColor("#F4F0FA"))
        p.setFont(theme.font(max(7.5, s * 0.30), QFont.Weight.DemiBold))
        initial = (self._name[:1] or "?").upper()
        p.drawText(QRectF(0, 0, s, s), Qt.AlignmentFlag.AlignCenter, initial)
        if self._presence is not None:
            d = max(8.0, s * 0.30)
            r = QRectF(s - d, s - d, d, d)
            p.setPen(QPen(QColor(self._ring), max(2.0, s * 0.06)))
            p.setBrush(QColor(theme.SUCCESS if self._presence else "#6B6377"))
            p.drawEllipse(r.adjusted(0.5, 0.5, -0.5, -0.5))


class Dot(QWidget):
    def __init__(self, color: str, size: int = 8, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = color
        self.setFixedSize(size, size)

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._color))
        p.drawEllipse(QRectF(0, 0, self.width(), self.height()))


class StatusPill(QFrame):
    """Rounded pill with a colored dot: Connected / Syncing / Offline ..."""

    def __init__(self, text: str = "", color: str = theme.SUCCESS, icon_name: str | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 3, 11, 3)
        lay.setSpacing(6)
        self._dot = Dot(color, 7)
        self._icon = QLabel()
        self._text = QLabel(text)
        self._text.setFont(theme.font(8.8, QFont.Weight.DemiBold))
        lay.addWidget(self._dot, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._text, 0, Qt.AlignmentFlag.AlignVCenter)
        self._icon_name = icon_name
        self.set_state(text, color, icon_name)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_state(self, text: str, color: str, icon_name: str | None = None) -> None:
        self._text.setText(text)
        self._dot.set_color(color)
        c = QColor(color)
        self._dot.setVisible(icon_name is None)
        self._icon.setVisible(icon_name is not None)
        if icon_name:
            self._icon.setPixmap(icons.pixmap(icon_name, color, 13, 2.0))
        self._text.setStyleSheet(f"color: {color};")
        self.setStyleSheet(
            f"StatusPill {{ background: rgba({c.red()},{c.green()},{c.blue()},0.10);"
            f" border: 1px solid rgba({c.red()},{c.green()},{c.blue()},0.32); border-radius: 11px; }}"
        )


class Spinner(QWidget):
    """Lightweight indeterminate arc spinner."""

    def __init__(self, size: int = 16, color: str = theme.ACCENT, width: float = 2.0,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._angle = 0
        self._color = color
        self._w = width
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def _tick(self) -> None:
        self._angle = (self._angle + 8) % 360
        self.update()

    def showEvent(self, e) -> None:  # noqa: N802
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self._w, self._w, self.width() - 2 * self._w, self.height() - 2 * self._w)
        track = QColor(self._color)
        track.setAlpha(50)
        p.setPen(QPen(track, self._w))
        p.drawEllipse(r)
        pen = QPen(QColor(self._color), self._w)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(r, int(-self._angle * 16), int(100 * 16))


class LoadingButton(QPushButton):
    """Primary button that shows an inline spinner + alternate text while busy."""

    def __init__(self, text: str, kind: str = "primary", icon_name: str | None = None, parent=None) -> None:
        super().__init__(text, parent)
        self._text = text
        self._icon_name = icon_name
        self.setProperty("kind", kind)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if icon_name:
            self.setIcon(icons.icon(icon_name, "#FFFFFF", size=16))
            self.setIconSize(QSize(16, 16))
            self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._spinner = Spinner(16, "#FFFFFF", 2.0, self)
        self._spinner.hide()
        self.busy = False

    def set_busy(self, busy: bool, text: str | None = None) -> None:
        self.busy = busy
        self.setEnabled(not busy)
        self.setText(text if (busy and text) else self._text)
        if self._icon_name:
            self.setIcon(icons.icon(self._icon_name, "#FFFFFF", size=16) if not busy else QIcon())
        self._spinner.set_color("#E9E2F7")
        self._spinner.setVisible(busy)
        self._place()

    def set_label(self, text: str) -> None:
        self._text = text
        if not self.busy:
            self.setText(text)

    def _place(self) -> None:
        fm = self.fontMetrics()
        tw = fm.horizontalAdvance(self.text())
        x = (self.width() - tw) // 2 - 26
        self._spinner.move(max(10, x), (self.height() - 16) // 2)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._place()


class Field(QFrame):
    """Input with a leading icon and optional trailing action (e.g. password reveal)."""

    textChanged = Signal(str)
    returnPressed = Signal()

    def __init__(self, icon_name: str, placeholder: str, password: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Field")
        self._icon_name = icon_name
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 6, 0)
        lay.setSpacing(10)
        self.icon = QLabel()
        self.icon.setFixedWidth(18)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setAccessibleName(placeholder)
        self.edit.installEventFilter(self)
        lay.addWidget(self.icon)
        lay.addWidget(self.edit, 1)
        self.reveal: IconButton | None = None
        if password:
            self.edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.reveal = IconButton("eye", "Show password", 30, 17)
            self.reveal.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            self.reveal.clicked.connect(self._toggle_reveal)
            lay.addWidget(self.reveal)
        else:
            lay.addSpacing(6)
        self.edit.textChanged.connect(self.textChanged)
        self.edit.returnPressed.connect(self.returnPressed)
        self._set_icon(False)

    def _set_icon(self, focused: bool) -> None:
        self.icon.setPixmap(icons.pixmap(self._icon_name, theme.ACCENT_HOVER if focused else theme.TEXT_SUBTLE, 17))

    def _toggle_reveal(self) -> None:
        hidden = self.edit.echoMode() == QLineEdit.EchoMode.Password
        self.edit.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
        assert self.reveal is not None
        self.reveal.set_icon_name("eye-off" if hidden else "eye")
        self.reveal.setToolTip("Hide password" if hidden else "Show password")

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is self.edit and ev.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            focused = ev.type() == QEvent.Type.FocusIn
            self.setProperty("focused", focused)
            self._set_icon(focused)
            repolish(self)
        return super().eventFilter(obj, ev)

    def text(self) -> str:
        return self.edit.text()

    def setText(self, t: str) -> None:  # noqa: N802
        self.edit.setText(t)

    def set_invalid(self, invalid: bool) -> None:
        if bool(self.property("invalid")) != invalid:
            self.setProperty("invalid", invalid)
            repolish(self)

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        super().setEnabled(enabled)
        self.setProperty("disabled", not enabled)
        repolish(self)

    def setFocus(self, *args) -> None:  # noqa: N802
        self.edit.setFocus(*args)


class FadeWidget(QWidget):
    """Base for widgets that fade via an `opacity` property (paint-time)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._opacity = 1.0

    def _get_opacity(self) -> float:
        return self._opacity

    def _set_opacity(self, v: float) -> None:
        self._opacity = v
        self.update()

    opacity = Property(float, _get_opacity, _set_opacity)

    def animate_opacity(self, start: float, end: float, ms: int = 180, on_done=None) -> QPropertyAnimation:
        anim = QPropertyAnimation(self, b"opacity", self)
        anim.setDuration(ms)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        if on_done:
            anim.finished.connect(on_done)
        anim.start()
        return anim


class HLine(QFrame):
    def __init__(self, color: str = theme.BORDER, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setStyleSheet(f"background: {color}; border: none;")


def elevate(w: QWidget, blur: int = 56, dy: int = 14, alpha: int = 170) -> None:
    """Soft black drop shadow that lifts an opaque card off a busy background."""
    from PySide6.QtWidgets import QGraphicsDropShadowEffect

    fx = QGraphicsDropShadowEffect(w)
    fx.setBlurRadius(blur)
    fx.setOffset(0, dy)
    fx.setColor(QColor(0, 0, 0, alpha))
    w.setGraphicsEffect(fx)
