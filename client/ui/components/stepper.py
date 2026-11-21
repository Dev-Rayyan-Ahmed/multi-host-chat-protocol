# client/ui/components/stepper.py
"""Vertical handshake progress stepper driven by real NetworkClient stage events."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from client.ui import icons, theme
from client.ui.components.widgets import Spinner

PENDING, ACTIVE, DONE, FAILED = "pending", "active", "done", "failed"


class _StepBadge(QWidget):
    def __init__(self, number: int) -> None:
        super().__init__()
        self.number = number
        self.state = PENDING
        self.setFixedSize(26, 26)
        self.spinner = Spinner(26, theme.ACCENT, 2.0, self)
        self.spinner.hide()

    def set_state(self, state: str) -> None:
        self.state = state
        self.spinner.setVisible(state == ACTIVE)
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1, 1, 24, 24)
        if self.state == DONE:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.ACCENT))
            p.drawEllipse(r)
            p.drawPixmap(6, 6, icons.pixmap("check", "#FFFFFF", 14, 2.6))
        elif self.state == FAILED:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.ERROR))
            p.drawEllipse(r)
            p.drawPixmap(6, 6, icons.pixmap("x", "#FFFFFF", 14, 2.6))
        elif self.state == ACTIVE:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.ACCENT_SOFT))
            p.drawEllipse(r.adjusted(2, 2, -2, -2))
            p.setPen(QColor(theme.ACCENT_HOVER))
            p.setFont(theme.font(8.5, QFont.Weight.Bold))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(self.number))
        else:
            p.setPen(QPen(QColor(theme.BORDER_STRONG), 1.2))
            p.setBrush(QColor(theme.SURFACE_2))
            p.drawEllipse(r)
            p.setPen(QColor(theme.TEXT_SUBTLE))
            p.setFont(theme.font(8.5, QFont.Weight.DemiBold))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(self.number))


class _Connector(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.done = False
        self.setFixedWidth(26)
        self.setMinimumHeight(14)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setPen(QPen(QColor(theme.ACCENT if self.done else theme.BORDER_STRONG), 2))
        x = self.width() / 2
        p.drawLine(int(x), 0, int(x), self.height())


class HandshakeStepper(QWidget):
    STEPS = [
        ("tcp", "TCP connection", "Reach the server over TCP"),
        ("identity", "Server identity", "Verify the Ed25519 signature on the handshake"),
        ("secure", "Encrypted session", "X25519 exchange · HKDF-SHA-256 · AES-256-GCM"),
    ]
    DONE_TITLES = {
        "tcp": "TCP connection established",
        "identity": "Server identity verified",
        "secure": "Encrypted session established",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.badges: dict[str, _StepBadge] = {}
        self.titles: dict[str, QLabel] = {}
        self.subs: dict[str, QLabel] = {}
        self.connectors: list[_Connector] = []
        for i, (key, title, sub) in enumerate(self.STEPS):
            row = QHBoxLayout()
            row.setSpacing(12)
            badge = _StepBadge(i + 1)
            col = QVBoxLayout()
            col.setSpacing(1)
            t = QLabel(title)
            t.setFont(theme.font(9.8, QFont.Weight.DemiBold))
            s = QLabel(sub)
            s.setFont(theme.font(8.6))
            s.setWordWrap(True)
            col.addWidget(t)
            col.addWidget(s)
            row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
            row.addLayout(col, 1)
            lay.addLayout(row)
            if i < len(self.STEPS) - 1:
                c = _Connector()
                crow = QHBoxLayout()
                crow.setContentsMargins(0, 2, 0, 2)
                crow.addWidget(c)
                crow.addStretch(1)
                lay.addLayout(crow)
                self.connectors.append(c)
            self.badges[key], self.titles[key], self.subs[key] = badge, t, s
        self.reset()

    def reset(self) -> None:
        for key, title, sub in self.STEPS:
            self._set(key, PENDING, title, sub)
        for c in self.connectors:
            c.done = False
            c.update()

    def _set(self, key: str, state: str, title: str | None = None, sub: str | None = None) -> None:
        self.badges[key].set_state(state)
        if title is not None:
            self.titles[key].setText(title)
        if sub is not None:
            self.subs[key].setText(sub)
        color = {
            PENDING: theme.TEXT_SUBTLE,
            ACTIVE: theme.TEXT,
            DONE: theme.TEXT,
            FAILED: theme.ERROR_SOFT,
        }[state]
        self.titles[key].setStyleSheet(f"color: {color};")
        self.subs[key].setStyleSheet(
            f"color: {theme.ERROR_SOFT if state == FAILED else theme.TEXT_SUBTLE if state == PENDING else theme.TEXT_MUTED};"
        )

    def start(self, endpoint: str) -> None:
        self.reset()
        self._set("tcp", ACTIVE, "Connecting…", f"Opening a TCP connection to {endpoint}")

    def stage_done(self, stage: str, endpoint: str = "") -> None:
        keys = [k for k, _, _ in self.STEPS]
        if stage not in keys:
            return
        idx = keys.index(stage)
        for i, key in enumerate(keys[: idx + 1]):
            sub = None
            if key == "tcp" and endpoint:
                sub = f"Connected to {endpoint}"
            self._set(key, DONE, self.DONE_TITLES[key], sub)
            if i < len(self.connectors):
                self.connectors[i].done = True
                self.connectors[i].update()
        if idx + 1 < len(keys):
            nxt = keys[idx + 1]
            active_titles = {"identity": "Verifying server identity…", "secure": "Establishing encrypted session…"}
            self._set(nxt, ACTIVE, active_titles.get(nxt))

    def fail(self, stage: str, message: str) -> None:
        keys = [k for k, _, _ in self.STEPS]
        if stage not in keys:
            stage = "tcp"
        failed_titles = {
            "tcp": "TCP connection failed",
            "identity": "Server identity rejected",
            "secure": "Encrypted session failed",
        }
        self._set(stage, FAILED, failed_titles[stage], message)
