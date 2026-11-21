# client/ui/dialogs/diagnostics.py
"""Connection Details drawer: technical telemetry kept out of the chat itself."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from client.ui import icons, theme
from client.ui.components.brand import LogoMark
from client.ui.components.widgets import Dot, HLine, IconButton, button, label

DRAWER_WIDTH = 392


def _fmt_duration(s: float | None) -> str:
    if s is None:
        return "—"
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else (f"{m}m {sec:02d}s" if m else f"{sec}s")


class _Row:
    def __init__(self, grid: QGridLayout, r: int, icon_name: str, name: str, mono: bool = False) -> None:
        ic = QLabel()
        ic.setPixmap(icons.pixmap(icon_name, theme.TEXT_SUBTLE, 15))
        key = label(name, size=9.2, color=theme.TEXT_MUTED)
        val_box = QHBoxLayout()
        val_box.setSpacing(6)
        self.dot = Dot(theme.SUCCESS, 7)
        self.dot.hide()
        self.value = label("—", size=9.0 if mono else 9.4, weight=QFont.Weight.Medium, mono=mono)
        self.value.setWordWrap(True)
        self.value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        val_box.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        val_box.addWidget(self.value, 1)
        grid.addWidget(ic, r, 0)
        grid.addWidget(key, r, 1)
        grid.addLayout(val_box, r, 2)

    def set(self, text: str, color: str = theme.TEXT, dot: str | None = None) -> None:
        self.value.setText(text)
        self.value.setStyleSheet(f"color: {color};")
        self.dot.setVisible(dot is not None)
        if dot:
            self.dot.set_color(dot)


class DiagnosticsDrawer(QFrame):
    closed = Signal()
    pingRequested = Signal()
    disconnectRequested = Signal()

    def __init__(self, session, parent: QWidget) -> None:
        super().__init__(parent)
        self.session = session
        self.setObjectName("Drawer")
        self.setStyleSheet(
            f"QFrame#Drawer {{ background: {theme.SIDEBAR}; border-left: 1px solid {theme.BORDER_STRONG}; }}"
        )
        self.open_ = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(20, 16, 12, 16)
        head.setSpacing(10)
        shield = QLabel()
        shield.setPixmap(icons.pixmap("shield-check", theme.ACCENT_HOVER, 20))
        title = label("Connection Details", size=11.5, weight=QFont.Weight.DemiBold)
        close = IconButton("x", "Close (Esc)", 30, 16)
        close.clicked.connect(self.hide_drawer)
        head.addWidget(shield)
        head.addWidget(title, 1)
        head.addWidget(close)
        outer.addLayout(head)
        outer.addWidget(HLine())

        scroll = self.scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        b = QVBoxLayout(body)
        b.setContentsMargins(20, 16, 20, 20)
        b.setSpacing(0)

        def section(text: str, trailing: QWidget | None = None) -> QGridLayout:
            hrow = QHBoxLayout()
            hrow.addWidget(label(text, role="section"), 1)
            if trailing is not None:
                hrow.addWidget(trailing)
            b.addLayout(hrow)
            b.addSpacing(8)
            box = QFrame()
            box.setObjectName("Box")
            box.setStyleSheet(
                f"QFrame#Box {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; border-radius: 10px; }}"
            )
            g = QGridLayout(box)
            g.setContentsMargins(14, 12, 14, 12)
            g.setHorizontalSpacing(10)
            g.setVerticalSpacing(10)
            g.setColumnStretch(2, 1)
            g.setColumnMinimumWidth(1, 118)
            b.addWidget(box)
            b.addSpacing(18)
            return g

        ping = button("Ping server", "ghost", "zap", theme.ACCENT_HOVER)
        ping.setFixedHeight(24)
        ping.setStyleSheet("QPushButton { min-height: 22px; padding: 0 8px; font-size: 8.4pt; color: %s; }" % theme.ACCENT_HOVER)
        ping.setToolTip("Send an encrypted PING and measure the round-trip time")
        ping.clicked.connect(self.pingRequested)
        g = section("CONNECTION", ping)
        self.r_tcp = _Row(g, 0, "activity", "TCP status")
        self.r_addr = _Row(g, 1, "server", "Server", mono=True)
        self.r_up = _Row(g, 2, "clock", "Connected for")
        self.r_rtt = _Row(g, 3, "zap", "Round-trip time", mono=True)

        g = section("SECURITY")
        self.r_hs = _Row(g, 0, "shield-check", "Handshake")
        self.r_cipher = _Row(g, 1, "lock", "Cipher", mono=True)
        self.r_kx = _Row(g, 2, "key", "Key exchange", mono=True)
        self.r_auth = _Row(g, 3, "shield", "Server auth", mono=True)
        self.r_scope = _Row(g, 4, "globe", "Scope")

        g = section("PROTOCOL")
        self.r_seq = _Row(g, 0, "hash", "Latest message seq", mono=True)
        self.r_tx = _Row(g, 1, "arrow-right", "Records sent", mono=True)
        self.r_rx = _Row(g, 2, "arrow-down", "Records received", mono=True)
        self.r_sync = _Row(g, 3, "refresh", "Synchronization")
        hint = label("Message seq numbers are assigned by the server to chat messages. "
                     "Record counters number AES-GCM records per direction on this connection.",
                     role="subtle", size=8.2)
        hint.setWordWrap(True)
        g.addWidget(hint, 4, 0, 1, 3)

        # identity disclosure
        self.id_toggle = button("Show identity details", "ghost", "chevron-right", theme.TEXT_MUTED)
        self.id_toggle.setStyleSheet("QPushButton { text-align: left; padding-left: 0; }")
        self.id_toggle.clicked.connect(self._toggle_identity)
        b.addWidget(self.id_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.identity = QFrame()
        self.identity.setObjectName("Box")
        self.identity.setStyleSheet(
            f"QFrame#Box {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; border-radius: 10px; }}"
        )
        il = QVBoxLayout(self.identity)
        il.setContentsMargins(14, 12, 14, 12)
        il.setSpacing(8)
        frow = QHBoxLayout()
        frow.addWidget(label("Trusted server key · SHA-256 fingerprint", size=8.6, color=theme.TEXT_MUTED), 1)
        copy = IconButton("copy", "Copy fingerprint", 26, 15)
        copy.clicked.connect(self._copy)
        frow.addWidget(copy)
        il.addLayout(frow)
        self.fp = label("", size=8.6, mono=True)
        self.fp.setWordWrap(True)
        self.fp.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        il.addWidget(self.fp)
        warn = QFrame()
        warn.setObjectName("Warn")
        warn.setStyleSheet(
            "QFrame#Warn { background: rgba(214,164,94,0.08); border: 1px solid rgba(214,164,94,0.35); border-radius: 8px; }"
        )
        wl = QHBoxLayout(warn)
        wl.setContentsMargins(10, 8, 10, 8)
        wl.setSpacing(8)
        wic = QLabel()
        wic.setPixmap(icons.pixmap("alert", theme.WARNING, 15))
        wt = label("This key is derived from a fixed seed published in the project source, so anyone "
                   "with the source could impersonate the server. Verification demonstrates the "
                   "mechanism; it is not proof of a trusted server.", size=8.2, color="#E2C391")
        wt.setWordWrap(True)
        wl.addWidget(wic, 0, Qt.AlignmentFlag.AlignTop)
        wl.addWidget(wt, 1)
        il.addWidget(warn)
        self.identity.hide()
        b.addSpacing(6)
        b.addWidget(self.identity)
        b.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        foot = QHBoxLayout()
        foot.setContentsMargins(20, 12, 20, 16)
        mark = LogoMark(18)
        foot.addWidget(mark)
        foot.addWidget(label("Horizon Relay · protocol v0.3", role="subtle", size=8.4), 1)
        disc = button("Disconnect", "danger", "logout", theme.ERROR_SOFT)
        disc.setFixedHeight(34)
        disc.setStyleSheet("QPushButton { min-height: 32px; }")
        disc.clicked.connect(self.disconnectRequested)
        foot.addWidget(disc)
        outer.addWidget(HLine())
        outer.addLayout(foot)

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.refresh)
        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.hide()

    # ------------------------------------------------------------ api
    def toggle(self) -> None:
        self.hide_drawer() if self.open_ else self.show_drawer()

    def show_drawer(self) -> None:
        if self.open_:
            return
        self.open_ = True
        self.refresh()
        p = self.parentWidget()
        end = QRect(p.width() - DRAWER_WIDTH, 0, DRAWER_WIDTH, p.height())
        self.setGeometry(end.translated(DRAWER_WIDTH, 0))
        self.show()
        self.raise_()
        self._anim.stop()
        try:
            self._anim.finished.disconnect()
        except (RuntimeError, TypeError):
            pass
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(end)
        self._anim.start()
        self._timer.start()

    def hide_drawer(self) -> None:
        if not self.open_:
            return
        self.open_ = False
        self._timer.stop()
        self._anim.stop()
        try:
            self._anim.finished.disconnect()
        except (RuntimeError, TypeError):
            pass
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(self.geometry().translated(DRAWER_WIDTH, 0))
        self._anim.finished.connect(self.hide)
        self._anim.start()
        self.closed.emit()

    def reposition(self) -> None:
        if self.open_ and self._anim.state() != QPropertyAnimation.State.Running:
            p = self.parentWidget()
            self.setGeometry(p.width() - DRAWER_WIDTH, 0, DRAWER_WIDTH, p.height())

    def refresh(self) -> None:
        d = self.session.diagnostics()
        ok = d["connected"]
        self.r_tcp.set(d["tcp"], theme.SUCCESS if ok else theme.ERROR_SOFT, theme.SUCCESS if ok else theme.ERROR)
        self.r_addr.set(d["endpoint"])
        self.r_up.set(_fmt_duration(d["uptime"]))
        rtt = d["rtt"]
        self.r_rtt.set(f"{rtt:.1f} ms" if rtt is not None else "—")
        self.r_hs.set("Verified ✓" if d["handshake_ok"] else d["handshake"],
                      theme.SUCCESS if d["handshake_ok"] else theme.TEXT_MUTED)
        self.r_cipher.set(d["cipher"])
        self.r_kx.set(d["key_exchange"])
        self.r_auth.set(d["server_auth"])
        self.r_scope.set("Client ↔ server only — not end-to-end", theme.TEXT_MUTED)
        self.r_seq.set(f"#{d['last_seq']}" if d["last_seq"] is not None else "—")
        self.r_tx.set(f"#{d['send_records']}" if d["send_records"] is not None else "—")
        self.r_rx.set(f"#{d['recv_records']}" if d["recv_records"] is not None else "—")
        sync = d["sync"]
        self.r_sync.set(sync, theme.WARNING if sync == "Synchronizing" else theme.TEXT)
        self.fp.setText(self.session.server_fingerprint())

    def _toggle_identity(self) -> None:
        vis = not self.identity.isVisible()
        self.identity.setVisible(vis)
        self.id_toggle.setText("Hide identity details" if vis else "Show identity details")
        self.id_toggle.setIcon(icons.icon("chevron-down" if vis else "chevron-right", theme.TEXT_MUTED, size=16))
        if vis:
            sb = self.scroll.verticalScrollBar()
            QTimer.singleShot(30, lambda: sb.setValue(sb.maximum()))

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.session.server_fingerprint())
        win = self.window()
        if hasattr(win, "toast"):
            win.toast("success", "Fingerprint copied to clipboard.")
