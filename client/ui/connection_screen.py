# client/ui/connection_screen.py
"""Connection screen: brand panel + server form + live handshake progress."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QIntValidator
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from client.ui import icons, theme
from client.ui.components.backdrop import HOLE_U, HOLE_V, ON_VIDEO_TEXT, OVERLAY_ALPHA, Shade, VideoBackdrop, halo
from client.ui.components.brand import BrandLockup
from client.ui.components.stepper import HandshakeStepper
from client.ui.components.widgets import Field, LoadingButton, button, elevate, label

DEFAULT_PORT = 5000


class BrandGroup(QWidget):
    """Logo + HORIZON RELAY + tagline, kept together as one unit. It floats over the
    left (34 %) area and is centred on the black hole's dark disk, not on the panel."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(BrandLockup(scale=1.55, stacked=True), 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(18)
        tag = halo(label("Encrypted relay messaging\nfor your local network.", size=10.5, color=ON_VIDEO_TEXT))
        tag.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(tag)
        self.adjustSize()


class ConnectionScreen(VideoBackdrop):
    connectRequested = Signal(str, int)
    cancelRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            overlay=OVERLAY_ALPHA,
            focus_x=0.30,  # put the black hole behind the branding side, not behind the card
            shades=[
                # extra darkness behind the logo + tagline
                Shade("radial", 0.80, center=self._brand_center, radius=380),
                # calmer right side so the card and the security note sit on near-black
                Shade("linear", 0.55, x0=0.30, x1=0.80),
                # pool of black under the security note below the card
                Shade("radial", 0.85, center=self._note_center, radius=420),
            ],
        )
        self.settings = QSettings()
        self._endpoint = ""
        self._connecting = False

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # The left 34 % column stays in the layout as space; the branding floats over it (_place_brand).
        self.brand_area = QWidget()
        self.brand_area.setMinimumWidth(300)
        root.addWidget(self.brand_area, 34)
        self.brand = BrandGroup(self)

        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(32, 32, 32, 22)
        rlay.addStretch(1)

        card = QFrame()
        card.setObjectName("Card")
        card.setStyleSheet(
            f"QFrame#Card {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; border-radius: 14px; }}"
        )
        card.setFixedWidth(420)
        elevate(card)
        c = QVBoxLayout(card)
        c.setContentsMargins(28, 28, 28, 24)
        c.setSpacing(0)

        title = label("Connect to server", size=15, weight=QFont.Weight.DemiBold)
        sub = label("Enter the address of a Horizon Relay server.", role="muted", size=9.6)
        c.addWidget(title)
        c.addSpacing(4)
        c.addWidget(sub)
        c.addSpacing(20)

        c.addWidget(label("Server address", role="muted", size=8.8, weight=QFont.Weight.DemiBold))
        c.addSpacing(6)
        self.host = Field("server", "IP address or hostname")
        self.host.setText(self.settings.value("server/host", "127.0.0.1"))
        c.addWidget(self.host)
        c.addSpacing(12)
        c.addWidget(label("Port", role="muted", size=8.8, weight=QFont.Weight.DemiBold))
        c.addSpacing(6)
        self.port = Field("port", "5000")
        self.port.edit.setValidator(QIntValidator(1, 65535, self))
        self.port.setText(str(self.settings.value("server/port", DEFAULT_PORT)))
        c.addWidget(self.port)
        c.addSpacing(12)
        self.remember = QCheckBox("Remember this server")
        self.remember.setChecked(self.settings.value("server/remember", True, type=bool))
        self.remember.setCursor(Qt.CursorShape.PointingHandCursor)
        c.addWidget(self.remember)
        c.addSpacing(18)

        self.connect_btn = LoadingButton("Establish Secure Connection", icon_name="arrow-right")
        self.connect_btn.clicked.connect(self._submit)
        c.addWidget(self.connect_btn)
        self.cancel_btn = button("Cancel", "ghost")
        self.cancel_btn.clicked.connect(self._cancel)
        self.cancel_btn.hide()
        c.addSpacing(6)
        c.addWidget(self.cancel_btn, 0, Qt.AlignmentFlag.AlignHCenter)

        # error banner
        self.error = QFrame()
        self.error.setObjectName("ErrorBox")
        self.error.setStyleSheet(
            "QFrame#ErrorBox { background: rgba(239,68,68,0.08); border: 1px solid rgba(239,68,68,0.35); border-radius: 8px; }"
        )
        el = QHBoxLayout(self.error)
        el.setContentsMargins(12, 10, 12, 10)
        el.setSpacing(10)
        eic = QLabel()
        eic.setPixmap(icons.pixmap("alert-circle", theme.ERROR_SOFT, 17))
        self.error_text = label("", size=9.3, color="#F3B4B4")
        self.error_text.setWordWrap(True)
        el.addWidget(eic, 0, Qt.AlignmentFlag.AlignTop)
        el.addWidget(self.error_text, 1)
        self.error.hide()
        c.addSpacing(12)
        c.addWidget(self.error)

        c.addSpacing(18)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {theme.BORDER};")
        c.addWidget(sep)
        c.addSpacing(18)
        c.addWidget(label("SECURE HANDSHAKE", role="section"))
        c.addSpacing(12)
        self.stepper = HandshakeStepper()
        c.addWidget(self.stepper)

        self.card = card
        # The card sits right of centre in the right-hand area (3 : 1 side margins).
        card_row = QHBoxLayout()
        card_row.addStretch(3)
        card_row.addWidget(card)
        card_row.addStretch(1)
        rlay.addLayout(card_row)
        rlay.addSpacing(18)

        # Product line directly under the card, centred on it.
        note = QWidget()
        nl = QVBoxLayout(note)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(4)
        headline = halo(label("Secure communication, Built for your Local Network",
                              size=9.6, weight=QFont.Weight.Medium, color=theme.TEXT))
        headline.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        nl.addWidget(headline)
        note.setFixedWidth(460)
        self.note = note
        note_row = QHBoxLayout()
        note_row.addStretch(3)
        note_row.addWidget(note)
        note_row.addStretch(1)
        rlay.addLayout(note_row)
        rlay.addStretch(1)  # card + line stay together, vertically centred
        # On short windows (e.g. 1080p laptops at 150 % scaling) the card scrolls instead of
        # being squashed; the scroll area is transparent so the video stays visible behind it.
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.Shape.NoFrame)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # No visible scrollbar; on very short windows the card still scrolls with the wheel / Tab focus.
        scroller.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroller.setStyleSheet("QScrollArea, QScrollArea > QWidget#qt_scrollarea_viewport { background: transparent; }")
        scroller.viewport().setAutoFillBackground(False)
        right.setAutoFillBackground(False)
        right.setObjectName("RightPanel")
        right.setStyleSheet("QWidget#RightPanel { background: transparent; }")
        scroller.setWidget(right)
        self.scroller = scroller
        root.addWidget(scroller, 66)

        self.host.returnPressed.connect(self._submit)
        self.port.returnPressed.connect(self._submit)
        self.host.textChanged.connect(lambda _: self._clear_invalid())
        self.port.textChanged.connect(lambda _: self._clear_invalid())

    def _note_center(self) -> QPointF:
        n = getattr(self, 'note', None)
        if n is None or not n.isVisible():
            return QPointF(self.width() * 0.67, self.height() * 0.9)
        return QPointF(n.mapTo(self, n.rect().center()))

    def _brand_center(self) -> QPointF:
        return QPointF(self.brand.geometry().center())

    def _place_brand(self) -> None:
        """Centre the branding group on the black hole as the video is actually drawn;
        never let it run under the card or off the window."""
        hole = self.video_point(HOLE_U, HOLE_V)
        self.brand.adjustSize()
        bw, bh = self.brand.width(), self.brand.height()
        x, y = hole.x() - bw / 2, hole.y() - bh / 2
        card_left = self.card.mapTo(self, self.card.rect().topLeft()).x()
        x = max(16, min(x, card_left - 32 - bw))
        y = max(16, min(y, self.height() - 16 - bh))
        self.brand.move(round(x), round(y))
        self.brand.raise_()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        QTimer.singleShot(0, self._place_brand)  # after the layout has placed the card

    # ------------------------------------------------------------ behaviour
    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        QTimer.singleShot(0, self._place_brand)
        if not self._connecting:
            QTimer.singleShot(0, lambda: self.connect_btn.setFocus())

    def _clear_invalid(self) -> None:
        self.host.set_invalid(False)
        self.port.set_invalid(False)

    def _submit(self) -> None:
        if self._connecting:
            return
        host = self.host.text().strip()
        port_txt = self.port.text().strip()
        if not host:
            self.host.set_invalid(True)
            self.show_error("Enter the server's IP address or hostname.")
            self.host.setFocus()
            return
        if not port_txt.isdigit() or not (1 <= int(port_txt) <= 65535):
            self.port.set_invalid(True)
            self.show_error("Port must be a number between 1 and 65535.")
            self.port.setFocus()
            return
        port = int(port_txt)
        if self.remember.isChecked():
            self.settings.setValue("server/host", host)
            self.settings.setValue("server/port", port)
        self.settings.setValue("server/remember", self.remember.isChecked())
        self.start_connecting(host, port)
        self.connectRequested.emit(host, port)

    def start_connecting(self, host: str, port: int) -> None:
        self._connecting = True
        self._endpoint = f"{host}:{port}"
        self.host.setText(host)
        self.port.setText(str(port))
        self.error.hide()
        self.host.setEnabled(False)
        self.port.setEnabled(False)
        self.remember.setEnabled(False)
        self.connect_btn.set_busy(True, "Connecting…")
        self.cancel_btn.show()
        self.stepper.start(self._endpoint)

    def _cancel(self) -> None:
        self.cancelRequested.emit()
        self.reset_form()
        self.stepper.reset()

    def reset_form(self) -> None:
        self._connecting = False
        self.host.setEnabled(True)
        self.port.setEnabled(True)
        self.remember.setEnabled(True)
        self.connect_btn.set_busy(False)
        self.cancel_btn.hide()

    def on_stage(self, stage: str) -> None:
        self.stepper.stage_done(stage, self._endpoint)
        if stage == "secure":
            self.connect_btn.set_busy(True, "Secure session ready")
            self.cancel_btn.hide()

    def on_failed(self, stage: str, kind: str, message: str) -> None:
        self.stepper.fail(stage, _short_failure(kind))
        self.reset_form()
        self.connect_btn.set_label("Retry Connection")
        self.show_error(message)

    def show_error(self, message: str) -> None:
        self.error_text.setText(message)
        self.error.show()

    def show_disconnected(self, message: str) -> None:
        """Returned here after an established connection dropped."""
        self.reset_form()
        self.stepper.reset()
        self.connect_btn.set_label("Reconnect")
        self.show_error(message)


def _short_failure(kind: str) -> str:
    return {
        "refused": "The server refused the connection.",
        "timeout": "No response from the server.",
        "dns": "Host name could not be resolved.",
        "identity": "Signature did not match the trusted server key.",
        "handshake": "The server's handshake could not be verified.",
    }.get(kind, "The connection was interrupted.")
