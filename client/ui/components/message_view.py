# client/ui/components/message_view.py
"""Message timeline: rows, grouping, date/source dividers, sticky auto-scroll."""
from __future__ import annotations

import html
import re
from datetime import date, datetime

from PySide6.QtCore import QPointF, QRectF, QEasingCurve, QPropertyAnimation, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QRadialGradient
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from client.state import (
    STATUS_DELIVERED,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_SENDING,
    STATUS_SENT,
    ChatMessage,
    Conversation,
)
from client.ui import icons, theme
from client.ui.components.backdrop import StaticBackdrop, asset
from client.ui.components.widgets import Avatar, button, label

GROUP_WINDOW_S = 7 * 60
_LONG_TOKEN = re.compile(r"(\S{48})(?=\S)")


def fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%I:%M %p").lstrip("0")


def fmt_day(d: date) -> str:
    today = date.today()
    delta = (today - d).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    if d.year == today.year:
        return d.strftime("%A, %B ") + str(d.day)
    return d.strftime("%B ") + f"{d.day}, {d.year}"


def display_text(text: str) -> str:
    # Allow very long unbroken tokens (URLs, hashes) to wrap instead of widening the view
    return _LONG_TOKEN.sub("\\1\u200b", text)


class Divider(QWidget):
    """Centered label with hairlines either side. `tone`: date | away | history"""

    def __init__(self, text: str, tone: str = "date", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tone = tone
        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 14 if tone == "date" else 12, 24, 6)
        lay.setSpacing(12)
        line_color = {"date": theme.BORDER, "away": "rgba(214,164,94,0.45)", "history": theme.BORDER_STRONG}[tone]
        left, right = QFrame(), QFrame()
        for ln in (left, right):
            ln.setFixedHeight(1)
            ln.setStyleSheet(f"background: {line_color}; border: none;")
        pill = QLabel(text)
        if tone == "date":
            pill.setFont(theme.font(8.8, QFont.Weight.DemiBold))
            pill.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        elif tone == "away":
            pill.setFont(theme.font(8.8, QFont.Weight.DemiBold))
            pill.setStyleSheet(
                f"color: {theme.WARNING}; background: rgba(214,164,94,0.10); border: 1px solid rgba(214,164,94,0.45);"
                " border-radius: 11px; padding: 3px 12px;"
            )
        else:
            pill.setFont(theme.font(8.8, QFont.Weight.DemiBold))
            pill.setStyleSheet(
                f"color: {theme.TEXT_MUTED}; background: {theme.SURFACE}; border: 1px solid {theme.BORDER_STRONG};"
                " border-radius: 11px; padding: 3px 12px;"
            )
        lay.addWidget(left, 1)
        lay.addWidget(pill)
        lay.addWidget(right, 1)


_STATUS = {
    STATUS_SENDING: ("Sending…", theme.TEXT_SUBTLE, "Waiting for the server to accept this message."),
    STATUS_QUEUED: ("Queued", theme.WARNING,
                    "Recipient is offline. The server will deliver it when they next log in."),
    STATUS_FAILED: ("Failed", theme.ERROR_SOFT, ""),
    STATUS_DELIVERED: ("Delivered", theme.TEXT_SUBTLE,
                       "The recipient's app received and acknowledged this queued message after "
                       "reconnecting. "
                       "This is not a read receipt."),
}


def status_html(msg: ChatMessage, broadcast: bool) -> tuple[str, str]:
    """Inline delivery status appended after the message text. Honest wording only:
    'Sent' = accepted / handed to the recipient's connection, never 'read'."""
    st = msg.status
    if st is None:
        return "", ""
    if st == STATUS_SENT:
        tip = ("Accepted by the server and relayed to everyone online." if broadcast
               else "Handed to the recipient's active connection. This is not a read receipt.")
        text, color = "Sent", theme.TEXT_SUBTLE
    else:
        text, color, tip = _STATUS[st]
    extra = ""
    if st == STATUS_FAILED:
        tip = msg.error or "The message could not be delivered."
        extra = (f'&nbsp;&nbsp;<a href="retry" style="color:{theme.ACCENT_HOVER}; text-decoration:none;">'
                 "Retry</a>")
    span = (f'<span style="color:{color}; font-size:8.4pt; font-weight:600;">'
            f"&nbsp;&nbsp;&nbsp;{text}</span>{extra}")
    return span, tip


class MessageRow(QFrame):
    retryRequested = Signal(object)

    def __init__(self, msg: ChatMessage, compact: bool, broadcast: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.msg = msg
        self.broadcast = broadcast
        self.compact = compact
        self.setObjectName("MsgRow")
        self.setStyleSheet(
            "QFrame#MsgRow { background: transparent; border-radius: 6px; }"
            f"QFrame#MsgRow:hover {{ background: #15121C; }}"
        )
        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 2 if compact else 8, 16, 2)
        outer.setSpacing(14)

        left = QWidget()
        left.setFixedWidth(38)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)
        self.hover_time: QLabel | None = None
        if compact:
            self.hover_time = label(fmt_time(msg.ts), size=7.6, color="transparent")
            self.hover_time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.hover_time.setFixedHeight(22)
            ll.addWidget(self.hover_time)
        else:
            ll.addWidget(Avatar(msg.sender, 38, None), 0, Qt.AlignmentFlag.AlignTop)
            ll.addSpacing(0)
        ll.addStretch(1)
        outer.addWidget(left, 0, Qt.AlignmentFlag.AlignTop)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(2)
        if not compact:
            head = QHBoxLayout()
            head.setSpacing(8)
            name = label(msg.sender, size=10.4, weight=QFont.Weight.DemiBold,
                         color=theme.ACCENT_HOVER if msg.outgoing else theme.TEXT)
            t = label(fmt_time(msg.ts), size=8.2, color=theme.TEXT_SUBTLE)
            t.setToolTip(datetime.fromtimestamp(msg.ts).strftime("%A, %d %B %Y · %H:%M:%S"))
            head.addWidget(name)
            head.addWidget(t, 0, Qt.AlignmentFlag.AlignBaseline)
            head.addStretch(1)
            body.addLayout(head)

        self.text = QLabel()
        # Remote text is HTML-escaped before being placed in rich text (no markup injection).
        self.text.setTextFormat(Qt.TextFormat.RichText)
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.text.setCursor(Qt.CursorShape.IBeamCursor)
        self.text.setFont(theme.font(10.5))
        self.text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.text.linkActivated.connect(lambda _href: self.retryRequested.emit(self.msg))
        body.addWidget(self.text)
        outer.addLayout(body, 1)
        self.refresh_status()

    def refresh_status(self) -> None:
        m = self.msg
        if m.status == STATUS_FAILED:
            color = theme.TEXT_MUTED
        elif m.status == STATUS_SENDING:
            color = "#CFC7DA"
        else:
            color = theme.TEXT if m.outgoing else "#E4DEEC"
        body = html.escape(display_text(m.text)).replace(chr(10), "<br>")
        status, tip = status_html(m, self.broadcast)
        self.text.setText(
            f'<span style="color:{color}; white-space:pre-wrap;">{body}</span>{status}'
        )
        self.text.setToolTip(tip)

    def enterEvent(self, e) -> None:  # noqa: N802
        if self.hover_time:
            self.hover_time.setStyleSheet(f"color: {theme.TEXT_SUBTLE};")
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        if self.hover_time:
            self.hover_time.setStyleSheet("color: transparent;")
        super().leaveEvent(e)


class EmptyState(StaticBackdrop):
    """Shown only while a conversation has no messages; hidden as soon as one arrives."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(asset("empty_state_black_hole.png", "Horizon_Relay_Empty_State_Black_Hole.png"),
                         overlay=0.28, parent=parent, fallback=QColor(0, 0, 0, 0))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 40, 40, 40)
        lay.addStretch(1)
        self.tile = QLabel()
        self.tile.setFixedSize(56, 56)
        self.tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tile.setStyleSheet(f"background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; border-radius: 14px;")
        self.title = label("", size=12.5, weight=QFont.Weight.DemiBold)
        self.body = label("", role="muted", size=9.6)
        self.body.setWordWrap(True)
        self.body.setFixedWidth(440)
        for w in (self.title, self.body):
            w.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.tile, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(14)
        lay.addWidget(self.title, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(6)
        lay.addWidget(self.body, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        self.paint_static(p)
        w, h = self.width(), self.height()
        # soft black pool behind the text, and feathered top/bottom edges into the ambient canvas
        g = QRadialGradient(QPointF(w / 2, h / 2), max(260.0, w * 0.32))
        g.setColorAt(0.0, QColor(0, 0, 0, 120))
        g.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), g)
        for y0, y1 in ((0, 70), (h, h - 70)):
            lg = QLinearGradient(0, y0, 0, y1)
            lg.setColorAt(0.0, QColor(12, 9, 18, 255))
            lg.setColorAt(1.0, QColor(12, 9, 18, 0))
            p.fillRect(QRectF(0, min(y0, y1), w, 70), lg)
        for x0, x1 in ((0, 90), (w, w - 90)):
            lg = QLinearGradient(x0, 0, x1, 0)
            lg.setColorAt(0.0, QColor(12, 9, 18, 230))
            lg.setColorAt(1.0, QColor(12, 9, 18, 0))
            p.fillRect(QRectF(min(x0, x1), 0, 90, h), lg)

    def set_content(self, icon_name: str, title: str, body: str) -> None:
        self.tile.setPixmap(icons.pixmap(icon_name, theme.ACCENT_HOVER, 24))
        self.title.setText(title)
        self.body.setText(body)
        self.body.setFixedHeight(self.body.heightForWidth(self.body.width()))


class JumpPill(QPushButton):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIcon(icons.icon("arrow-down", "#FFFFFF", size=14))
        self.setIconSize(QSize(14, 14))
        self.setStyleSheet(
            f"QPushButton {{ background: {theme.ACCENT}; color: white; border-radius: 15px; padding: 0 14px;"
            f" min-height: 30px; font-size: 9pt; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {theme.ACCENT_HOVER}; }}"
        )
        self.hide()


class MessageView(QScrollArea):
    retryRequested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("QScrollArea, QScrollArea > QWidget#qt_scrollarea_viewport { background: transparent; }")
        self.viewport().setAutoFillBackground(False)
        self.inner = QWidget()
        self.inner.setObjectName("Timeline")
        self.lay = QVBoxLayout(self.inner)
        self.lay.setContentsMargins(8, 8, 8, 18)
        self.lay.setSpacing(0)
        self.lay.addStretch(1)
        self.setWidget(self.inner)
        self.empty = EmptyState()
        self.lay.insertWidget(0, self.empty, 100)

        self.conv: Conversation | None = None
        self.rows: dict[int, MessageRow] = {}
        self._last: ChatMessage | None = None
        self._last_day: date | None = None
        self._seen_sources: set[str] = set()
        self._away: frozenset[str] = frozenset()  # sync batches shown as 'While you were away'
        self._stick = True
        self._unseen = 0

        sb = self.verticalScrollBar()
        sb.rangeChanged.connect(self._on_range)
        sb.valueChanged.connect(self._on_value)
        self._anim = QPropertyAnimation(sb, b"value", self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.jump = JumpPill(self.viewport().parentWidget() or self)
        self.jump.clicked.connect(lambda: self.scroll_to_bottom(animated=True))

    # ------------------------------------------------------------ scrolling
    def _at_bottom(self) -> bool:
        sb = self.verticalScrollBar()
        return sb.value() >= sb.maximum() - 48

    def _on_value(self, _v: int) -> None:
        if self._anim.state() == QPropertyAnimation.State.Running:
            return
        self._stick = self._at_bottom()
        if self._stick:
            self._unseen = 0
            self.jump.hide()
        elif self.verticalScrollBar().maximum() - self.verticalScrollBar().value() > 600 and not self.jump.isVisible():
            self._show_jump("Jump to latest")

    def _on_range(self, _lo: int, hi: int) -> None:
        if self._stick:
            self.verticalScrollBar().setValue(hi)

    def scroll_to_bottom(self, animated: bool = False) -> None:
        sb = self.verticalScrollBar()
        self._stick = True
        self._unseen = 0
        self.jump.hide()
        if animated:
            self._anim.stop()
            self._anim.setStartValue(sb.value())
            self._anim.setEndValue(sb.maximum())
            self._anim.start()
        else:
            sb.setValue(sb.maximum())

    def _show_jump(self, text: str) -> None:
        self.jump.setText(text)
        self.jump.adjustSize()
        self._place_jump()
        self.jump.show()
        self.jump.raise_()

    def _place_jump(self) -> None:
        self.jump.move((self.width() - self.jump.width()) // 2, self.height() - self.jump.height() - 14)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._place_jump()

    # ------------------------------------------------------------ content
    def _clear(self) -> None:
        while self.lay.count() > 2:  # keep empty state (0) and the trailing stretch
            item = self.lay.takeAt(1)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.rows.clear()
        self._last = None
        self._last_day = None
        self._seen_sources = set()

    def show_conversation(self, conv: Conversation, empty: tuple[str, str, str],
                          away_syncs: frozenset[str] = frozenset()) -> None:
        """`away_syncs`: sync batches whose notice was unseen when this conversation was opened.
        Kept for this viewing only, so rebuilds don't make the divider vanish mid-view."""
        self.conv = conv
        self._away = frozenset(away_syncs)
        self._empty_content = empty
        self.setUpdatesEnabled(False)
        self._clear()
        for m in conv.messages:
            self._add(m)
        self._update_empty()
        self.setUpdatesEnabled(True)
        self._stick = True
        QTimer.singleShot(0, self.scroll_to_bottom)

    def rebuild(self) -> None:
        if self.conv is not None:
            keep_bottom = self._stick
            value = self.verticalScrollBar().value()
            self.setUpdatesEnabled(False)
            self._clear()
            for m in self.conv.messages:
                self._add(m)
            self._update_empty()
            self.setUpdatesEnabled(True)
            if keep_bottom:
                QTimer.singleShot(0, self.scroll_to_bottom)
            else:
                QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(value))

    def append(self, msg: ChatMessage) -> None:
        was_bottom = self._stick or self._at_bottom()
        self._add(msg)
        self._update_empty()
        if msg.outgoing or was_bottom:
            self._stick = True
            QTimer.singleShot(0, lambda: self.scroll_to_bottom(animated=True))
        else:
            self._unseen += 1
            self._show_jump(f"{self._unseen} new message{'s' if self._unseen > 1 else ''}")

    def update_message(self, msg: ChatMessage) -> None:
        row = self.rows.get(msg.local_id)
        if row is not None:
            row.refresh_status()

    def _update_empty(self) -> None:
        has = bool(self.rows)
        self.empty.setVisible(not has)
        if not has and getattr(self, "_empty_content", None):
            self.empty.set_content(*self._empty_content)

    def _insert(self, w: QWidget) -> None:
        self.lay.insertWidget(self.lay.count() - 1, w)

    def _group(self, msg: ChatMessage) -> str:
        """Which divider a message falls under in THIS viewing of the conversation.
        'away' only for pending messages whose sync notice was still unseen when the
        conversation was opened; it is not derived from message.source alone."""
        if msg.sync_id is not None and msg.sync_id in self._away:
            return "away"
        if msg.source == "history" and msg.fresh:
            return "history"
        return "normal"

    def _add(self, msg: ChatMessage) -> None:
        day = datetime.fromtimestamp(msg.ts).date()
        divided = False
        if day != self._last_day:
            self._insert(Divider(fmt_day(day), "date"))
            self._last_day = day
            divided = True
        group = self._group(msg)
        if group in ("away", "history") and group not in self._seen_sources:
            self._seen_sources.add(group)
            self._insert(Divider("While you were away" if group == "away" else "Recent history", group))
            divided = True
        elif self._last is not None and self._group(self._last) != group:
            divided = True  # restart grouping after a divided batch
        prev = self._last
        compact = (
            not divided
            and prev is not None
            and prev.sender.lower() == msg.sender.lower()
            and abs(msg.ts - prev.ts) <= GROUP_WINDOW_S
            and self._group(prev) == group
        )
        row = MessageRow(msg, compact, bool(self.conv and self.conv.is_broadcast))
        row.retryRequested.connect(self.retryRequested)
        self._insert(row)
        self.rows[msg.local_id] = row
        self._last = msg

