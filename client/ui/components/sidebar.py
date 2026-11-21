# client/ui/components/sidebar.py
"""Conversation sidebar: brand, status, search, Broadcast, Direct Messages, profile."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from client.state import BROADCAST_KEY, username_problem
from client.ui import icons, theme
from client.ui.components.brand import BrandLockup
from client.ui.components.widgets import Avatar, Dot, Field, IconButton, StatusPill, label, repolish


class Badge(QLabel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFont(theme.font(8.2, QFont.Weight.Bold))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(20)
        self.setMinimumWidth(20)
        self.setStyleSheet(
            f"background: {theme.ACCENT}; color: white; border-radius: 10px; padding: 0 6px;"
        )
        self.hide()

    def set_count(self, n: int) -> None:
        self.setText(str(n) if n < 100 else "99+")
        self.setVisible(n > 0)


class ConversationItem(QFrame):
    clicked = Signal(str)

    def __init__(self, key: str, title: str, broadcast: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self.broadcast = broadcast
        self.setObjectName("ConvItem")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setProperty("selected", False)
        self.setStyleSheet(
            "QFrame#ConvItem { background: transparent; border-radius: 8px; border: none; }"
            f"QFrame#ConvItem:hover {{ background: {theme.HOVER}; }}"
            f"QFrame#ConvItem:focus {{ background: {theme.HOVER}; }}"
            f"QFrame#ConvItem[selected=\"true\"] {{ background: {theme.ACCENT_SOFT}; }}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8 if broadcast else 6, 10, 8 if broadcast else 6)
        lay.setSpacing(11)
        if broadcast:
            tile = QLabel()
            tile.setFixedSize(36, 36)
            tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tile.setPixmap(icons.pixmap("hash", theme.TEXT, 20, 2.0))
            tile.setStyleSheet(f"background: {theme.HOVER}; border-radius: 9px;")
            lay.addWidget(tile)
            self.avatar = None
        else:
            self.avatar = Avatar(title, 34, False)
            lay.addWidget(self.avatar)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.title = label(title, size=10.2, weight=QFont.Weight.DemiBold if broadcast else QFont.Weight.Medium)
        sub_row = QHBoxLayout()
        sub_row.setSpacing(5)
        self.sub_dot = Dot(theme.SUCCESS, 6)
        self.sub = label("", size=8.6, color=theme.TEXT_MUTED)
        self.sub.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        sub_row.addWidget(self.sub_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        sub_row.addWidget(self.sub, 1)
        col.addWidget(self.title)
        col.addLayout(sub_row)
        lay.addLayout(col, 1)
        self.badge = Badge()
        self.count_pill = QLabel()
        self.count_pill.setFont(theme.font(8.2, QFont.Weight.DemiBold))
        self.count_pill.setStyleSheet(
            f"background: {theme.ACCENT}; color: #FFFFFF; border-radius: 10px; padding: 2px 9px;"
        )
        self.count_pill.setVisible(broadcast)
        lay.addWidget(self.count_pill)
        lay.addWidget(self.badge)
        if broadcast:
            self.sub_dot.hide()
            self.sub.setText("Everyone online")

    def set_selected(self, sel: bool) -> None:
        if bool(self.property("selected")) != sel:
            self.setProperty("selected", sel)
            repolish(self)

    def set_peer(self, title: str, online: bool, not_found: bool = False) -> None:
        self.title.setText(title)
        if self.avatar:
            self.avatar.set_name(title)
            self.avatar.set_presence(online)
        if not_found:
            self.sub_dot.set_color(theme.ERROR)
            self.sub.setText("No such user")
            self.setToolTip(f"The server has no account named {title}")
            return
        self.sub_dot.set_color(theme.SUCCESS if online else "#6B6377")
        self.sub.setText("Online" if online else "Offline")
        self.setToolTip(f"{title} · {'online' if online else 'offline — messages are queued'}")
        self.setAccessibleName(f"Direct message with {title}, {'online' if online else 'offline'}")

    def set_online_count(self, n: int) -> None:
        self.count_pill.setText(f"{n} online")

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self.key)
            return
        super().keyPressEvent(e)


class StartItem(QFrame):
    """Shown in search results when the query is a valid username with no match."""

    clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.name = ""
        self.setObjectName("StartItem")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"QFrame#StartItem {{ background: transparent; border: 1px dashed {theme.BORDER_STRONG}; border-radius: 8px; }}"
            f"QFrame#StartItem:hover {{ background: {theme.HOVER}; }}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("message", theme.ACCENT_HOVER, 17))
        self.text = label("", size=9.4, color=theme.TEXT)
        lay.addWidget(ic)
        lay.addWidget(self.text, 1)

    def set_name(self, name: str) -> None:
        self.name = name
        self.text.setText(f"Message “{name}”")

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self.clicked.emit(self.name)
        super().mousePressEvent(e)


class Sidebar(QFrame):
    conversationSelected = Signal(str)
    newMessageRequested = Signal()
    startConversation = Signal(str)
    detailsRequested = Signal()
    disconnectRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(theme.SIDEBAR_WIDTH)
        self.setStyleSheet(
            f"QFrame#Sidebar {{ background: {theme.SIDEBAR}; border-right: 1px solid {theme.BORDER}; }}"
        )
        self.selected = BROADCAST_KEY
        self.items: dict[str, ConversationItem] = {}
        self._me = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 16, 12, 0)
        lay.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(6, 0, 2, 0)
        head.addWidget(BrandLockup(scale=0.82))
        head.addStretch(1)
        self.status = StatusPill("Connected", theme.SUCCESS)
        head.addWidget(self.status)
        lay.addLayout(head)
        lay.addSpacing(16)

        self.search = Field("search", "Find a conversation…")
        self.search.edit.setStyleSheet("font-size: 9.6pt;")
        kb = label("Ctrl K", size=7.6, color=theme.TEXT_SUBTLE)
        kb.setStyleSheet(
            f"color: {theme.TEXT_SUBTLE}; border: 1px solid {theme.BORDER_STRONG}; border-radius: 4px; padding: 0 4px;"
        )
        kb.setFixedHeight(18)
        self.search.layout().addWidget(kb)
        self.search.layout().setContentsMargins(12, 0, 8, 0)
        self.search.setFixedHeight(38)
        self.search.edit.setToolTip("Search conversations (Ctrl+K)")
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._open_first)
        self.search.edit.installEventFilter(self)
        lay.addWidget(self.search)
        lay.addSpacing(18)

        lay.addWidget(self._section("CONVERSATIONS"))
        lay.addSpacing(6)
        self.broadcast = ConversationItem(BROADCAST_KEY, "Broadcast", broadcast=True)
        self.broadcast.clicked.connect(self._select)
        self.items[BROADCAST_KEY] = self.broadcast
        lay.addWidget(self.broadcast)
        lay.addSpacing(16)

        dm_head = QHBoxLayout()
        dm_head.setContentsMargins(0, 0, 0, 0)
        dm_head.addWidget(self._section("DIRECT MESSAGES"))
        dm_head.addStretch(1)
        self.new_btn = IconButton("plus", "New message (Ctrl+N)", 26, 16)
        self.new_btn.clicked.connect(self.newMessageRequested)
        dm_head.addWidget(self.new_btn)
        lay.addLayout(dm_head)
        lay.addSpacing(4)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        self.list = QVBoxLayout(inner)
        self.list.setContentsMargins(0, 0, 0, 8)
        self.list.setSpacing(2)
        self.start_item = StartItem()
        self.start_item.clicked.connect(self.startConversation)
        self.start_item.hide()
        self.list.addWidget(self.start_item)
        self.empty = label("No direct messages yet.\nUse + to message anyone by username.", role="subtle", size=8.8)
        self.empty.setContentsMargins(8, 8, 8, 8)
        self.list.addWidget(self.empty)
        self.no_match = label("No matching conversations.", role="subtle", size=8.8)
        self.no_match.setContentsMargins(8, 8, 8, 8)
        self.no_match.hide()
        self.list.addWidget(self.no_match)
        self.list.addStretch(1)
        self.scroll.setWidget(inner)
        lay.addWidget(self.scroll, 1)

        # profile footer
        foot = QFrame()
        foot.setObjectName("Profile")
        foot.setStyleSheet(f"QFrame#Profile {{ border-top: 1px solid {theme.BORDER}; }}")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(6, 12, 2, 14)
        fl.setSpacing(10)
        self.me_avatar = Avatar("", 36, True)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.me_name = label("", size=10.2, weight=QFont.Weight.DemiBold)
        srow = QHBoxLayout()
        srow.setSpacing(5)
        self.me_dot = Dot(theme.SUCCESS, 6)
        self.me_status = label("Connected", size=8.6, color=theme.TEXT_MUTED)
        srow.addWidget(self.me_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        srow.addWidget(self.me_status, 1)
        col.addWidget(self.me_name)
        col.addLayout(srow)
        fl.addWidget(self.me_avatar)
        fl.addLayout(col, 1)
        self.details_btn = IconButton("settings", "Connection details (Ctrl+I)", 32, 18)
        self.details_btn.clicked.connect(self.detailsRequested)
        self.logout_btn = IconButton("logout", "Disconnect", 32, 18, color="#D9787F", hover=theme.ERROR)
        self.logout_btn.clicked.connect(self.disconnectRequested)
        fl.addWidget(self.details_btn)
        fl.addWidget(self.logout_btn)
        lay.addWidget(foot)

        self._select(BROADCAST_KEY, emit=False)

    def _section(self, text: str) -> QLabel:
        lbl = label(text, role="section")
        lbl.setContentsMargins(6, 0, 0, 0)
        return lbl

    # ------------------------------------------------------------ api
    def set_me(self, name: str) -> None:
        self._me = name
        self.me_name.setText(name)
        self.me_avatar.set_name(name)

    def set_connection(self, state: str, rtt: float | None = None) -> None:
        """state: connected | syncing | offline"""
        if state == "connected":
            self.status.set_state("Connected", theme.SUCCESS)
            self.me_dot.set_color(theme.SUCCESS)
            self.me_avatar.set_presence(True)
            txt = "Connected" + (f" · {rtt:.0f} ms" if rtt is not None else "")
        elif state == "syncing":
            self.status.set_state("Syncing", theme.WARNING)
            self.me_dot.set_color(theme.WARNING)
            self.me_avatar.set_presence(True)
            txt = "Synchronizing…"
        else:
            self.status.set_state("Offline", theme.ERROR_SOFT)
            self.me_dot.set_color("#6B6377")
            self.me_avatar.set_presence(False)
            txt = "Disconnected"
        self.me_status.setText(txt)

    def update_list(self, peers: list[tuple[str, str]], online: set[str], unread: dict[str, int], online_count: int,
                    missing: set[str] | None = None) -> None:
        self.broadcast.set_online_count(online_count)
        self.broadcast.badge.set_count(unread.get(BROADCAST_KEY, 0))
        keep = set()
        # remove from layout (keeping widgets) then re-add in order
        for key, item in list(self.items.items()):
            if key != BROADCAST_KEY:
                self.list.removeWidget(item)
        insert_at = 1  # after start_item
        for key, name in peers:
            item = self.items.get(key)
            if item is None:
                item = ConversationItem(key, name)
                item.clicked.connect(self._select)
                self.items[key] = item
            item.set_peer(name, key in online, key in (missing or set()))
            item.badge.set_count(unread.get(key, 0))
            item.set_selected(key == self.selected)
            self.list.insertWidget(insert_at, item)
            insert_at += 1
            keep.add(key)
        for key in [k for k in self.items if k != BROADCAST_KEY and k not in keep]:
            dead = self.items.pop(key)
            dead.hide()
            dead.setParent(None)
            dead.deleteLater()
        self.empty.setVisible(not keep)
        self._filter(self.search.text())

    def select(self, key: str) -> None:
        self._select(key, emit=False)

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.edit.selectAll()

    def reset(self) -> None:
        for key in [k for k in self.items if k != BROADCAST_KEY]:
            dead = self.items.pop(key)
            dead.hide()
            dead.setParent(None)
            dead.deleteLater()
        self.search.setText("")
        self._select(BROADCAST_KEY, emit=False)

    # ------------------------------------------------------------ internals
    def _select(self, key: str, emit: bool = True) -> None:
        self.selected = key
        for k, item in self.items.items():
            item.set_selected(k == key)
        if emit:
            self.conversationSelected.emit(key)

    def _filter(self, text: str) -> None:
        q = text.strip().lower()
        any_dm = False
        for key, item in self.items.items():
            if key == BROADCAST_KEY:
                item.setVisible(not q or q in "broadcast" or q in "everyone")
                continue
            vis = not q or q in item.title.text().lower()
            item.setVisible(vis)
            any_dm = any_dm or vis
        exact = q and any(k == q for k in self.items)
        show_start = bool(q) and not exact and username_problem(text.strip()) is None and q != self._me.lower()
        if show_start:
            self.start_item.set_name(text.strip())
        self.start_item.setVisible(show_start)
        self.no_match.setVisible(bool(q) and not any_dm and not show_start and len(self.items) > 1)
        if q:
            self.empty.hide()
        else:
            self.empty.setVisible(len(self.items) <= 1)

    def _open_first(self) -> None:
        q = self.search.text().strip().lower()
        if not q:
            return
        for key, item in self.items.items():
            if item.isVisible() and key != BROADCAST_KEY:
                self.search.setText("")
                self._select(key)
                return
        if self.start_item.isVisible():
            name = self.start_item.name
            self.search.setText("")
            self.startConversation.emit(name)
        elif self.broadcast.isVisible():
            self.search.setText("")
            self._select(BROADCAST_KEY)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is self.search.edit and ev.type() == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Escape:
            self.search.setText("")
            self.conversationSelected.emit(self.selected)  # returns focus to composer
            return True
        return super().eventFilter(obj, ev)
