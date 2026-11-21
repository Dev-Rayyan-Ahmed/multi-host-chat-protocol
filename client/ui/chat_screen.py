# client/ui/chat_screen.py
"""Main workspace: sidebar + chat canvas (header, banners, timeline, composer)."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QVBoxLayout, QWidget

from client.session import ChatSession
from client.state import BROADCAST_KEY, ChatMessage
from client.ui import icons, theme
from client.ui.components.backdrop import StaticBackdrop, asset
from client.ui.components.composer import Composer
from client.ui.components.message_view import MessageView
from client.ui.components.sidebar import Sidebar
from client.ui.components.widgets import Avatar, IconButton, Spinner, button, label
from client.ui.dialogs.diagnostics import DiagnosticsDrawer


class Banner(QFrame):
    """Collapsible status strip under the chat header (sync / connection lost)."""

    action = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Banner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 9, 14, 9)
        lay.setSpacing(10)
        self.spinner = Spinner(15, theme.WARNING, 2.0)
        self.icon = QLabel()
        self.text = label("", size=9.4, weight=QFont.Weight.Medium)
        self.text.setWordWrap(True)
        self.btn = button("", "secondary")
        self.btn.setFixedHeight(28)
        self.btn.setStyleSheet("QPushButton { min-height: 26px; padding: 0 12px; font-size: 8.8pt; }")
        self.btn.clicked.connect(self.action)
        lay.addWidget(self.spinner)
        lay.addWidget(self.icon)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.btn)
        self.setMaximumHeight(0)
        self._anim = QPropertyAnimation(self, b"maximumHeight", self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.shown = False

    def present(self, tone: str, text: str, busy: bool = False, icon_name: str | None = None,
                action: str | None = None) -> None:
        color = {"warning": theme.WARNING, "error": theme.ERROR_SOFT, "success": theme.SUCCESS}[tone]
        rgb = {"warning": "214,164,94", "error": "239,68,68", "success": "99,185,149"}[tone]
        self.setStyleSheet(
            f"QFrame#Banner {{ background: rgba({rgb},0.09); border-bottom: 1px solid rgba({rgb},0.30); }}"
        )
        self.text.setText(text)
        self.text.setStyleSheet(f"color: {color};")
        self.spinner.set_color(color)
        self.spinner.setVisible(busy)
        self.icon.setVisible(bool(icon_name) and not busy)
        if icon_name:
            self.icon.setPixmap(icons.pixmap(icon_name, color, 16))
        self.btn.setVisible(bool(action))
        if action:
            self.btn.setText(action)
        if not self.shown:
            self.shown = True
            self._animate(self.sizeHint().height() + 4)

    def dismiss(self) -> None:
        if self.shown:
            self.shown = False
            self._animate(0)

    def _animate(self, end: int) -> None:
        self._anim.stop()
        self._anim.setStartValue(self.maximumHeight())
        self._anim.setEndValue(end)
        self._anim.start()


class ChatHeader(QFrame):
    detailsRequested = Signal()
    userPicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ChatHeader")
        self.setStyleSheet(f"QFrame#ChatHeader {{ border-bottom: 1px solid {theme.BORDER}; }}")
        self.setFixedHeight(72)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 0, 16, 0)
        lay.setSpacing(14)
        self.hash = QLabel()
        self.hash.setPixmap(icons.pixmap("hash", theme.TEXT_MUTED, 28, 2.0))
        self.hash.setFixedWidth(30)
        self.avatar = Avatar("", 38, False, ring=theme.BG)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.title = label("", size=13.5, weight=QFont.Weight.DemiBold)
        self.sub = label("", size=9.0, color=theme.TEXT_MUTED)
        col.addStretch(1)
        col.addWidget(self.title)
        col.addWidget(self.sub)
        col.addStretch(1)
        lay.addWidget(self.hash)
        lay.addWidget(self.avatar)
        lay.addLayout(col, 1)
        self.online = button("", "ghost", "users", theme.TEXT_MUTED)
        self.online.setToolTip("Who's online")
        self.online.clicked.connect(self._online_menu)
        lay.addWidget(self.online)
        self.info = IconButton("info", "Connection details (Ctrl+I)", 34, 20)
        self.info.clicked.connect(self.detailsRequested)
        lay.addWidget(self.info)
        self._users: list[str] = []
        self._me = ""

    def set_broadcast(self, online_count: int, users: list[str], me: str) -> None:
        self.hash.show()
        self.avatar.hide()
        self.title.setText("Broadcast")
        self.sub.setText("Messages visible to everyone online")
        self.online.setVisible(online_count > 0)
        self.online.setText(f" {online_count} online")
        self._users, self._me = users, me

    def set_direct(self, name: str, online: bool, online_count: int, users: list[str], me: str) -> None:
        self.hash.hide()
        self.avatar.show()
        self.avatar.set_name(name)
        self.avatar.set_presence(online)
        self.title.setText(name)
        self.sub.setText("Online · direct message" if online
                         else "Offline · messages are queued until they log in")
        self.online.setVisible(online_count > 0)
        self.online.setText(f" {online_count} online")
        self._users, self._me = users, me

    def _online_menu(self) -> None:
        menu = QMenu(self)
        if not self._users:
            a = menu.addAction("Nobody online")
            a.setEnabled(False)
        for u in sorted(self._users, key=str.lower):
            is_me = u.lower() == self._me.lower()
            act = menu.addAction(icons.icon("user", theme.SUCCESS, size=14), f"{u}{'  (you)' if is_me else ''}")
            act.setEnabled(not is_me)
            act.triggered.connect(lambda _=False, u=u: self.userPicked.emit(u))
        menu.exec(self.online.mapToGlobal(self.online.rect().bottomLeft()))


class ChatScreen(QWidget):
    disconnectRequested = Signal()
    reconnectRequested = Signal()
    newMessageRequested = Signal()

    def __init__(self, session: ChatSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.current = BROADCAST_KEY
        self._lost = False
        self._dirty_during_sync = False

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.sidebar = Sidebar()
        root.addWidget(self.sidebar)

        # Static ambient image only - no animation in the active workspace.
        self.canvas = StaticBackdrop(asset("workspace_ambient.png", "Horizon_Relay_Workspace_Ambient.png"),
                                     overlay=0.18)
        self.canvas.setObjectName("Canvas")
        c = QVBoxLayout(self.canvas)
        c.setContentsMargins(0, 0, 0, 0)
        c.setSpacing(0)
        self.header = ChatHeader()
        c.addWidget(self.header)
        self.banner = Banner()
        c.addWidget(self.banner)
        self.view = MessageView()
        c.addWidget(self.view, 1)
        comp_wrap = QWidget()
        cw = QVBoxLayout(comp_wrap)
        cw.setContentsMargins(24, 4, 24, 20)
        self.composer = Composer()
        cw.addWidget(self.composer)
        c.addWidget(comp_wrap)
        root.addWidget(self.canvas, 1)

        self.drawer = DiagnosticsDrawer(session, self)

        # wiring: widgets -> session
        self.sidebar.conversationSelected.connect(self.open_conversation)
        self.sidebar.newMessageRequested.connect(self.newMessageRequested)
        self.sidebar.startConversation.connect(self.start_direct)
        self.sidebar.detailsRequested.connect(self.drawer.toggle)
        self.sidebar.disconnectRequested.connect(self.disconnectRequested)
        self.header.detailsRequested.connect(self.drawer.toggle)
        self.header.userPicked.connect(self.start_direct)
        self.drawer.pingRequested.connect(session.ping)
        self.drawer.disconnectRequested.connect(self.disconnectRequested)
        self.composer.sendRequested.connect(self._send)
        self.view.retryRequested.connect(self._retry)
        self.banner.action.connect(self.reconnectRequested)

        # wiring: session -> widgets
        session.presenceChanged.connect(self.refresh_lists)
        session.conversationsChanged.connect(self.refresh_lists)
        session.messageAdded.connect(self._on_message_added)
        session.messageChanged.connect(self._on_message_changed)
        session.syncStarted.connect(self._on_sync_started)
        session.syncFinished.connect(self._on_sync_finished)
        session.rttUpdated.connect(lambda _r: self._update_connection_state())

    # ------------------------------------------------------------ lifecycle
    def start(self, username: str) -> None:
        """Called on LOGIN_OK."""
        self._lost = False
        self.banner.dismiss()
        self.sidebar.set_me(username)
        if self.current not in self.session.state.conversations:
            self.current = BROADCAST_KEY
        self.sidebar.select(self.current)
        self.refresh_lists()
        self.open_conversation(self.current, focus=False)
        self._update_connection_state()

    def connection_lost(self, reason: str) -> None:
        self._lost = True
        self.banner.present("error", f"Connection lost — {reason}", icon_name="wifi-off", action="Reconnect")
        self._update_connection_state()
        self.refresh_lists()

    # ------------------------------------------------------------ conversations
    def open_conversation(self, key: str, focus: bool = True) -> None:
        state = self.session.state
        state.prune_unknown(keep=key)
        conv = state.conversations.get(key)
        if conv is None:
            if key == BROADCAST_KEY:
                return
            conv = state.conversation_for_peer(state.online.get(key, key))
        self.current = conv.key
        conv.unread = 0
        self.sidebar.select(conv.key)
        self._update_header()
        if state.syncing:
            empty = ("hourglass", "Catching up…", "Restoring your messages from the server.")
        elif conv.is_broadcast:
            empty = ("hash", "Welcome to Broadcast",
                     "Messages here go to everyone who is online right now. "
                     "The last 30 messages are replayed to people when they log in.")
        else:
            online = state.is_online(conv.title)
            empty = ("message", f"This is the start of your conversation with {conv.title}",
                     "Messages are encrypted between each app and the server."
                     + ("" if online else f" {conv.title} is offline — the server will queue your "
                        "messages (up to 30) and deliver them when they next log in."))
        # "While you were away" shows only for sync batches this conversation hasn't displayed
        # yet. Showing it now counts as seen (persisted), so reopening, reconnecting or
        # restarting won't bring the old divider back; a new offline batch gets a new notice.
        away = state.notice_syncs(conv.key)
        self.view.show_conversation(conv, empty, frozenset(away))
        if away:
            self.session.mark_notices_seen(conv.key, away)
        self._update_composer()
        self.refresh_lists()
        if focus:
            self.composer.focus()

    def start_direct(self, name: str) -> None:
        conv = self.session.state.conversation_for_peer(name)
        self.refresh_lists()
        self.open_conversation(conv.key)

    def refresh_lists(self) -> None:
        state = self.session.state
        unread = {k: c.unread for k, c in state.conversations.items()}
        peers = state.sidebar_peers()
        missing = {k for k, c in state.conversations.items() if c.not_found}
        self.sidebar.update_list(peers, set(state.online.keys()), unread, state.online_count, missing)
        self.sidebar.broadcast.count_pill.setVisible(not self._lost and self.session.is_connected)
        self._update_header()

    def _update_header(self) -> None:
        state = self.session.state
        users = list(state.online.values())
        me = state.me or ""
        conv = state.conversations.get(self.current)
        if conv is None or conv.is_broadcast:
            self.header.set_broadcast(state.online_count, users, me)
        else:
            self.header.set_direct(state.online.get(conv.key, conv.title), state.is_online(conv.key),
                                   state.online_count, users, me)

    # ------------------------------------------------------------ composer
    def _update_composer(self) -> None:
        conv = self.session.state.conversations.get(self.current)
        if self._lost or not self.session.is_connected:
            self.composer.set_composer_enabled(False, "You're offline — reconnect to send messages")
        elif self.session.state.syncing or not self.session.sync_acknowledged:
            self.composer.set_composer_enabled(False, "Synchronizing messages…")
        else:
            target = "everyone" if (conv is None or conv.is_broadcast) else conv.title
            self.composer.set_composer_enabled(True, f"Message {target}…")

    def _update_connection_state(self) -> None:
        if self._lost or not self.session.is_connected:
            self.sidebar.set_connection("offline")
        elif self.session.state.syncing or not self.session.sync_acknowledged:
            self.sidebar.set_connection("syncing")
        else:
            c = self.session.client
            self.sidebar.set_connection("connected", c.last_rtt_ms if c else None)
        self._update_composer()

    def _send(self, text: str) -> None:
        conv = self.session.state.conversations.get(self.current)
        if conv is None:
            return
        to = BROADCAST_KEY if conv.is_broadcast else conv.title
        if self.session.send_text(to, text) is not None:
            self.composer.clear()

    def _retry(self, msg: ChatMessage) -> None:
        self.session.retry(msg)
        self.view.rebuild()

    # ------------------------------------------------------------ session events
    def _on_message_added(self, key: str, msg: ChatMessage, appended: bool) -> None:
        if self.session.state.syncing:
            self._dirty_during_sync = True
            return
        if key == self.current:
            conv = self.session.state.conversations.get(key)
            if conv:
                conv.unread = 0
            if appended:
                self.view.append(msg)
            else:
                self.view.rebuild()

    def _on_message_changed(self, key: str, msg: ChatMessage) -> None:
        if key == self.current:
            self.view.update_message(msg)

    def _on_sync_started(self, pending: int, history: int) -> None:
        parts = []
        if pending:
            parts.append(f"{pending} queued message{'s' if pending != 1 else ''}")
        if history:
            parts.append(f"{history} history message{'s' if history != 1 else ''}")
        what = " and ".join(parts) if parts else "your conversations"
        self.banner.present("warning", f"Synchronizing {what}…", busy=True)
        self._dirty_during_sync = False
        self.open_conversation(self.current, focus=False)
        self._update_connection_state()

    def _on_sync_finished(self, pending: int, history: int) -> None:
        self.open_conversation(self.current, focus=False)
        self._update_connection_state()
        if pending:
            self.banner.present("success",
                                f"Up to date · {pending} message{'s' if pending != 1 else ''} arrived while you were away",
                                icon_name="check")
        else:
            self.banner.present("success", "Up to date", icon_name="check")
        QTimer.singleShot(2200, lambda: (not self._lost) and self.banner.dismiss())
        self.composer.focus()

    # ------------------------------------------------------------ geometry
    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self.drawer.reposition()
