# client/ui/main_window.py
"""Top-level window: owns the ChatSession and switches Connection → Auth → Workspace."""
from __future__ import annotations

import sys

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QMainWindow, QStackedWidget, QWidget

from client.session import ChatSession
from client.ui import theme
from client.ui.auth_screen import AuthScreen
from client.ui.chat_screen import ChatScreen
from client.ui.components.backdrop import BlackHoleSource
from client.ui.components.brand import app_icon
from client.ui.components.toast import ToastHost
from client.ui.connection_screen import ConnectionScreen
from client.ui.dialogs.modals import ConfirmModal, NewMessageModal


def _dark_title_bar(win: QWidget) -> None:
    """Windows 10/11: dark caption matching the canvas color."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(win.winId())
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))  # immersive dark mode
        color = ctypes.c_int(0x00140D0F)  # COLORREF 0x00BBGGRR for #0F0D14
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(color), ctypes.sizeof(color))  # caption color
        text = ctypes.c_int(0x00F3E8ED)
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text), ctypes.sizeof(text))  # caption text
    except Exception:  # noqa: BLE001 - purely cosmetic
        pass


_warmed = False


def warm_up() -> None:
    """Pay one-time costs before the first window is visible, so nothing hitches later:
    Qt's media backend (background video) and the Windows colour-emoji font, which takes
    ~0.6 s the first time an emoji is laid out in a text box."""
    global _warmed
    if _warmed:
        return
    _warmed = True
    BlackHoleSource.get().prepare()
    from PySide6.QtWidgets import QPlainTextEdit

    probe = QPlainTextEdit()
    probe.setPlainText("👍 🎉 ❤")
    probe.resize(200, 40)
    probe.grab()  # forces layout + glyph rasterisation off-screen
    probe.deleteLater()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Horizon Relay")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(960, 620)
        self.resize(1280, 800)

        warm_up()
        self.session = ChatSession(self)
        self.stack = QStackedWidget()
        self.stack.setObjectName("Screens")
        self.setCentralWidget(self.stack)

        self.connection = ConnectionScreen()
        self.auth = AuthScreen()
        self.chat = ChatScreen(self.session)
        for w in (self.connection, self.auth, self.chat):
            self.stack.addWidget(w)
        # 24 px from the corner; on the chat screen stay clear of the message composer
        self.toasts = ToastHost(self.stack, lambda: 104 if self.stack.currentWidget() is self.chat else 24,
                                self._toast_keep_clear)
        self._fade: QPropertyAnimation | None = None
        self._last_username = ""

        # connection screen
        self.connection.connectRequested.connect(self._connect)
        self.connection.cancelRequested.connect(self.session.cancel_connect)
        # auth screen
        self.auth.loginRequested.connect(self._login)
        self.auth.registerRequested.connect(self._register)
        self.auth.changeServerRequested.connect(self._change_server)
        # chat screen
        self.chat.disconnectRequested.connect(self._confirm_disconnect)
        self.chat.reconnectRequested.connect(self._reconnect)
        self.chat.newMessageRequested.connect(self._new_message)

        s = self.session
        s.handshakeStage.connect(self.connection.on_stage)
        s.connectFailed.connect(self.connection.on_failed)
        s.connected.connect(self._on_connected)
        s.disconnected.connect(self._on_disconnected)
        s.registerOk.connect(self._on_register_ok)
        s.loginOk.connect(self._on_login_ok)
        s.authError.connect(self.auth.on_auth_error)
        s.notice.connect(self.toast)
        s.incomingWhileAway.connect(self._alert)

        self._shortcut("Ctrl+K", lambda: self._on_chat() and self.chat.sidebar.focus_search())
        self._shortcut("Ctrl+N", lambda: self._on_chat() and self._new_message())
        self._shortcut("Ctrl+I", lambda: self._on_chat() and self.chat.drawer.toggle())
        self._shortcut("Esc", self._escape)

    # ------------------------------------------------------------ helpers
    def _shortcut(self, seq: str, fn) -> None:
        sc = QShortcut(QKeySequence(seq), self)
        sc.setContext(Qt.ShortcutContext.WindowShortcut)
        sc.activated.connect(fn)

    def _on_chat(self) -> bool:
        return self.stack.currentWidget() is self.chat

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        _dark_title_bar(self)

    def _toast_keep_clear(self) -> list:
        """Controls a toast must never cover on the current screen."""
        from PySide6.QtCore import QPoint, QRect

        cur = self.stack.currentWidget()
        widgets = {self.connection: [self.connection.card],
                   self.auth: [self.auth.card]}.get(cur, [])
        out = []
        for wd in widgets:
            if wd.isVisible():
                # the card's content area (its empty inner padding may sit under a toast)
                inner = wd.contentsRect().marginsRemoved(wd.layout().contentsMargins())
                out.append(inner.translated(wd.mapTo(self.stack, QPoint(0, 0))))
        return out

    def toast(self, level: str, text: str) -> None:
        self.toasts.show(level, text)

    def switch_to(self, w: QWidget) -> None:
        if self.stack.currentWidget() is w:
            return
        self.stack.setCurrentWidget(w)
        fx = QGraphicsOpacityEffect(w)
        w.setGraphicsEffect(fx)
        anim = QPropertyAnimation(fx, b"opacity", self)
        anim.setDuration(200)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: w.setGraphicsEffect(None))
        anim.start()
        self._fade = anim
        for t in self.toasts.toasts:
            t.raise_()
        self.toasts.layout()

    def _escape(self) -> None:
        if self._on_chat() and self.chat.drawer.open_:
            self.chat.drawer.hide_drawer()

    # ------------------------------------------------------------ flows
    def _connect(self, host: str, port: int) -> None:
        self.session.connect_to(host, port)

    def _on_connected(self) -> None:
        endpoint = f"{self.session.host}:{self.session.port}"
        # Let the user see the final completed step before moving on
        QTimer.singleShot(550, lambda: self._show_auth(endpoint))

    def _show_auth(self, endpoint: str) -> None:
        if not self.session.is_connected:
            return
        self.connection.reset_form()
        self.auth.prepare(endpoint, self._last_username)
        self.switch_to(self.auth)

    def _login(self, username: str, password: str) -> None:
        try:
            self.session.login(username, password)
        except RuntimeError as err:
            self.auth.on_auth_error("NOT_CONNECTED", str(err))

    def _register(self, username: str, password: str) -> None:
        try:
            self.session.register(username, password)
        except RuntimeError as err:
            self.auth.on_auth_error("NOT_CONNECTED", str(err))

    def _on_register_ok(self, username: str) -> None:
        self.auth.on_register_ok(username)
        self.toast("success", f"Account “{username}” created. You can log in now.")

    def _on_login_ok(self, username: str) -> None:
        self._last_username = username
        self.auth.set_busy(False)
        self.chat.start(username)
        self.switch_to(self.chat)
        self.setWindowTitle(f"Horizon Relay — {username}")

    def _on_disconnected(self, reason: str, user_initiated: bool, was_logged_in: bool) -> None:
        current = self.stack.currentWidget()
        if user_initiated:
            self.toast("info", "Disconnected from the server.")
            self.connection.reset_form()
            self.connection.stepper.reset()
            self.connection.error.hide()
            self.switch_to(self.connection)
            self.setWindowTitle("Horizon Relay")
            return
        if current is self.chat:
            self.chat.connection_lost(reason)  # persistent banner with a Reconnect action
            QApplication.alert(self, 0)
        else:
            self.connection.show_disconnected(f"Connection lost. {reason}")
            self.switch_to(self.connection)

    def _reconnect(self) -> None:
        host, port = self.session.host, self.session.port
        self.switch_to(self.connection)
        self.connection.start_connecting(host, port)
        self.session.connect_to(host, port)

    def _change_server(self) -> None:
        self.session.shutdown(silent=True)
        self.connection.reset_form()
        self.connection.stepper.reset()
        self.connection.error.hide()
        self.switch_to(self.connection)

    def _confirm_disconnect(self) -> None:
        if not self.session.is_connected:
            self._change_server()
            self.setWindowTitle("Horizon Relay")
            return
        m = ConfirmModal(self.stack, "Disconnect from server?",
                         "You'll leave the conversation and need to log in again to keep chatting. "
                         "Private messages sent to you while you're away will be queued by the server.",
                         "Disconnect")
        m.confirmed.connect(self.session.disconnect)
        m.open()

    def _new_message(self) -> None:
        m = NewMessageModal(self.stack, self.session.username or "")
        m.submitted.connect(self.chat.start_direct)
        m.open()

    def _alert(self, key: str, _msg) -> None:
        if not self.isActiveWindow():
            QApplication.alert(self, 0)

    def closeEvent(self, e) -> None:  # noqa: N802
        self.session.shutdown(silent=True)
        super().closeEvent(e)


def build_app(argv: list[str]) -> QApplication:
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(argv)
    app.setOrganizationName("HorizonRelay")
    app.setApplicationName("Horizon Relay")
    app.setApplicationDisplayName("Horizon Relay")
    app.setWindowIcon(app_icon())
    theme.apply(app)
    return app
