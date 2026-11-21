# client/ui/auth_screen.py
"""Login / Create Account. Only reachable after the secure handshake has completed."""
from __future__ import annotations

from PySide6.QtCore import QPointF, Property, QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from client.state import username_problem
from client.ui import icons, theme
from client.ui.components.brand import BrandLockup
from client.ui.components.backdrop import ON_VIDEO_TEXT, OVERLAY_ALPHA, Shade, VideoBackdrop, halo
from client.ui.components.widgets import Field, LoadingButton, StatusPill, button, elevate, label
from common.protocol import MIN_PASSWORD_LEN

LOGIN, REGISTER = 0, 1


class TabBar(QWidget):
    changed = Signal(int)

    def __init__(self, titles: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._index = 0
        self._x = 0.0
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.buttons: list[QPushButton] = []
        for i, t in enumerate(titles):
            b = QPushButton(t)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFlat(True)
            b.setMinimumHeight(42)
            b.clicked.connect(lambda _=False, i=i: self.set_index(i, emit=True))
            lay.addWidget(b, 1)
            self.buttons.append(b)
        self._anim = QPropertyAnimation(self, b"indicator", self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._style()

    def _get(self) -> float:
        return self._x

    def _set(self, v: float) -> None:
        self._x = v
        self.update()

    indicator = Property(float, _get, _set)

    def index(self) -> int:
        return self._index

    def set_index(self, i: int, emit: bool = False) -> None:
        if i == self._index and emit:
            return
        self._index = i
        self._anim.stop()
        self._anim.setStartValue(self._x)
        self._anim.setEndValue(float(i))
        self._anim.start()
        self._style()
        if emit:
            self.changed.emit(i)

    def _style(self) -> None:
        for i, b in enumerate(self.buttons):
            active = i == self._index
            b.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none; border-radius: 0;"
                f" color: {theme.TEXT if active else theme.TEXT_MUTED}; font-size: 10.5pt; font-weight: 600; }}"
                f"QPushButton:hover {{ color: {theme.TEXT}; }}"
            )

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width() / max(1, len(self.buttons))
        h = self.height()
        p.fillRect(QRectF(0, h - 1, self.width(), 1), QColor(theme.BORDER))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ACCENT))
        p.drawRoundedRect(QRectF(self._x * w + 12, h - 2.5, w - 24, 2.5), 1.25, 1.25)


class AuthScreen(VideoBackdrop):
    loginRequested = Signal(str, str)
    registerRequested = Signal(str, str)
    changeServerRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            overlay=OVERLAY_ALPHA,
            shades=[
                # deep pool of black behind the logo + card so the bright disk never sits behind text
                Shade("radial", 0.72, center=self._card_center, radius=560),
                Shade("linear", 0.35, x0=0, y0=0.55, x1=0, y1=1.0),
                Shade("radial", 0.85, center=self._foot_center, radius=380),
            ],
        )
        self._busy = False

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.addStretch(3)

        root.addWidget(BrandLockup(scale=1.0), 0, Qt.AlignmentFlag.AlignHCenter)
        root.addSpacing(22)

        card = QFrame()
        card.setObjectName("Card")
        card.setStyleSheet(
            f"QFrame#Card {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; border-radius: 14px; }}"
        )
        card.setFixedWidth(420)
        elevate(card)
        self.card = card
        c = QVBoxLayout(card)
        c.setContentsMargins(28, 22, 28, 24)
        c.setSpacing(0)

        self.secure_pill = StatusPill("Secure connection established", theme.SUCCESS, "lock")
        c.addWidget(self.secure_pill, 0, Qt.AlignmentFlag.AlignHCenter)
        c.addSpacing(16)

        self.tabs = TabBar(["Log in", "Create account"])
        self.tabs.changed.connect(self._on_tab)
        c.addWidget(self.tabs)
        c.addSpacing(20)

        self.heading = label("", size=13.5, weight=QFont.Weight.DemiBold)
        self.subheading = label("", role="muted", size=9.4)
        self.subheading.setWordWrap(True)
        c.addWidget(self.heading)
        c.addSpacing(3)
        c.addWidget(self.subheading)
        c.addSpacing(16)

        # success banner (after registration)
        self.success = QFrame()
        self.success.setObjectName("Ok")
        self.success.setStyleSheet(
            "QFrame#Ok { background: rgba(99,185,149,0.09); border: 1px solid rgba(99,185,149,0.35); border-radius: 8px; }"
        )
        sl = QHBoxLayout(self.success)
        sl.setContentsMargins(12, 9, 12, 9)
        sl.setSpacing(10)
        sic = QLabel()
        sic.setPixmap(icons.pixmap("check", theme.SUCCESS, 16, 2.4))
        self.success_text = label("", size=9.3, color="#A9DCC5")
        self.success_text.setWordWrap(True)
        sl.addWidget(sic, 0, Qt.AlignmentFlag.AlignTop)
        sl.addWidget(self.success_text, 1)
        self.success_wrap = QWidget()
        swl = QVBoxLayout(self.success_wrap)
        swl.setContentsMargins(0, 0, 0, 14)
        swl.addWidget(self.success)
        self.success_wrap.hide()
        c.addWidget(self.success_wrap)

        self.username = Field("user", "Username")
        self.username.edit.setMaxLength(16)
        self.username_err = label("", role="error")
        self.password = Field("lock", "Password", password=True)
        self.password_err = label("", role="error")
        self.confirm = Field("lock", "Confirm password", password=True)
        self.confirm_err = label("", role="error")
        self.username_hint = label("3–16 characters: letters, digits or underscores.", role="subtle", size=8.6)

        c.addWidget(self.username)
        c.addSpacing(4)
        c.addWidget(self.username_err)
        c.addWidget(self.username_hint)
        c.addSpacing(8)
        c.addWidget(self.password)
        c.addSpacing(4)
        c.addWidget(self.password_err)
        self.confirm_gap = QWidget()
        self.confirm_gap.setFixedHeight(8)
        c.addWidget(self.confirm_gap)
        c.addWidget(self.confirm)
        c.addSpacing(4)
        c.addWidget(self.confirm_err)
        c.addSpacing(14)

        self.form_err = QFrame()
        self.form_err.setObjectName("ErrorBox")
        self.form_err.setStyleSheet(
            "QFrame#ErrorBox { background: rgba(239,68,68,0.08); border: 1px solid rgba(239,68,68,0.35); border-radius: 8px; }"
        )
        fl = QHBoxLayout(self.form_err)
        fl.setContentsMargins(12, 9, 12, 9)
        fl.setSpacing(10)
        fic = QLabel()
        fic.setPixmap(icons.pixmap("alert-circle", theme.ERROR_SOFT, 16))
        self.form_err_text = label("", size=9.3, color="#F3B4B4")
        self.form_err_text.setWordWrap(True)
        fl.addWidget(fic, 0, Qt.AlignmentFlag.AlignTop)
        fl.addWidget(self.form_err_text, 1)
        self.form_err.hide()
        c.addWidget(self.form_err)
        self.form_err_gap = QWidget()
        self.form_err_gap.setFixedHeight(12)
        self.form_err_gap.hide()
        c.addWidget(self.form_err_gap)

        self.submit = LoadingButton("Log in")
        self.submit.clicked.connect(self._submit)
        c.addWidget(self.submit)
        c.addSpacing(16)

        swap = QHBoxLayout()
        swap.setSpacing(5)
        swap.addStretch(1)
        self.swap_text = label("", role="muted", size=9.3)
        self.swap_link = button("", "link")
        self.swap_link.setFont(theme.font(9.3, QFont.Weight.DemiBold))
        self.swap_link.clicked.connect(lambda: self.tabs.set_index(1 - self.tabs.index(), emit=True))
        swap.addWidget(self.swap_text)
        swap.addWidget(self.swap_link)
        swap.addStretch(1)
        c.addLayout(swap)

        root.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addSpacing(14)

        foot = QHBoxLayout()
        foot.setSpacing(6)
        foot.addStretch(1)
        self.endpoint = halo(label("", size=8.8, color=ON_VIDEO_TEXT))
        change = button("Change server", "link")
        change.setFont(theme.font(8.8, QFont.Weight.DemiBold))
        change.clicked.connect(self.changeServerRequested)
        foot.addWidget(self.endpoint)
        foot.addWidget(halo(label("·", size=8.8, color=ON_VIDEO_TEXT)))
        foot.addWidget(change)
        foot.addStretch(1)
        root.addLayout(foot)
        root.addStretch(4)

        for f in (self.username, self.password, self.confirm):
            f.returnPressed.connect(self._submit)
            f.textChanged.connect(lambda _=None, f=f: self._clear_field_error(f))
        self.username.textChanged.connect(lambda _: self._live_username_check())
        self._on_tab(LOGIN)

    def _foot_center(self) -> QPointF:
        e = getattr(self, 'endpoint', None)
        if e is None or not e.isVisible():
            return QPointF(self.width() / 2, self.height() * 0.8)
        return QPointF(self.width() / 2, e.mapTo(self, e.rect().center()).y())

    def _card_center(self) -> QPointF:
        c = getattr(self, 'card', None)
        if c is None:
            return QPointF(self.width() / 2, self.height() / 2)
        g = c.geometry()
        return QPointF(g.center().x(), g.center().y() - 40)

    # ------------------------------------------------------------ public api
    def prepare(self, endpoint: str, username: str = "") -> None:
        self.endpoint.setText(f"Connected to {endpoint}")
        self.set_busy(False)
        self.success_wrap.hide()
        self.form_err.hide()
        self.form_err_gap.hide()
        self.password.setText("")
        self.confirm.setText("")
        if username:
            self.username.setText(username)
        if self.tabs.index() != LOGIN:
            self.tabs.set_index(LOGIN)
            self._on_tab(LOGIN)
        QTimer.singleShot(0, lambda: (self.password if username else self.username).setFocus())

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        text = "Logging in…" if self.tabs.index() == LOGIN else "Creating account…"
        self.submit.set_busy(busy, text)
        for f in (self.username, self.password, self.confirm):
            f.setEnabled(not busy)
        self.tabs.setEnabled(not busy)

    def on_register_ok(self, username: str) -> None:
        self.set_busy(False)
        self.tabs.set_index(LOGIN)
        self._on_tab(LOGIN)
        self.username.setText(username)
        self.password.setText("")
        self.confirm.setText("")
        self.success_text.setText(f"Account “{username}” created. Log in to continue.")
        self.success_wrap.show()
        self.password.setFocus()

    def on_auth_error(self, reason: str, message: str) -> None:
        self.set_busy(False)
        if reason == "AUTH_FAILED":
            self._field_error(self.password, self.password_err, "Incorrect username or password.")
            self.password.setText("")
            self.password.setFocus()
        elif reason in ("USERNAME_TAKEN", "INVALID_USERNAME"):
            text = "That username is already taken." if reason == "USERNAME_TAKEN" else message
            self._field_error(self.username, self.username_err, text)
            self.username.setFocus()
        elif reason == "ALREADY_LOGGED_IN":
            self._form_error("This account already has an active session on another device.")
        else:
            self._form_error(message or reason)

    # ------------------------------------------------------------ internals
    def _on_tab(self, i: int) -> None:
        login = i == LOGIN
        self.heading.setText("Welcome back" if login else "Create your account")
        self.subheading.setText(
            "Log in to join the conversation." if login
            else "Pick a username others will use to message you."
        )
        self.submit.set_label("Log in" if login else "Create account")
        self.swap_text.setText("Don't have an account?" if login else "Already have an account?")
        self.swap_link.setText("Create account" if login else "Log in")
        self.confirm.setVisible(not login)
        self.confirm_gap.setVisible(not login)
        self.username_hint.setVisible(not login)
        self.password.edit.setPlaceholderText("Password" if login else f"Password (min. {MIN_PASSWORD_LEN} characters)")
        if not login:
            self.success_wrap.hide()
        for f, e in ((self.username, self.username_err), (self.password, self.password_err), (self.confirm, self.confirm_err)):
            f.set_invalid(False)
            e.hide()
        self.form_err.hide()
        self.form_err_gap.hide()
        self.confirm_err.setVisible(False)

    def _field_error(self, f: Field, lbl: QLabel, text: str) -> None:
        f.set_invalid(True)
        lbl.setText(text)
        lbl.show()
        if f is self.username:
            self.username_hint.hide()

    def _clear_field_error(self, f: Field) -> None:
        f.set_invalid(False)
        lbl = {self.username: self.username_err, self.password: self.password_err, self.confirm: self.confirm_err}[f]
        lbl.hide()
        self.form_err.hide()
        self.form_err_gap.hide()
        if f is self.username and self.tabs.index() == REGISTER:
            self.username_hint.show()

    def _live_username_check(self) -> None:
        if self.tabs.index() != REGISTER:
            return
        name = self.username.text()
        if name and (p := username_problem(name)) and len(name) >= 3:
            self._field_error(self.username, self.username_err, p)

    def _form_error(self, text: str) -> None:
        self.form_err_text.setText(text)
        self.form_err.show()
        self.form_err_gap.show()

    def _submit(self) -> None:
        if self._busy:
            return
        login = self.tabs.index() == LOGIN
        name = self.username.text().strip()
        pw = self.password.text()
        ok = True
        if p := username_problem(name):
            self._field_error(self.username, self.username_err, p)
            ok = False
        if login:
            if not pw:
                self._field_error(self.password, self.password_err, "Enter your password.")
                ok = False
        else:
            if len(pw) < MIN_PASSWORD_LEN:
                self._field_error(self.password, self.password_err,
                                  f"Password must be at least {MIN_PASSWORD_LEN} characters.")
                ok = False
            elif self.confirm.text() != pw:
                self._field_error(self.confirm, self.confirm_err, "Passwords don't match.")
                ok = False
        if not ok:
            return
        self.success_wrap.hide()
        self.set_busy(True)
        if login:
            self.loginRequested.emit(name, pw)
        else:
            self.registerRequested.emit(name, pw)
