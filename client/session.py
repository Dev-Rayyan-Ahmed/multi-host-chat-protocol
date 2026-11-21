# client/session.py
"""
Qt bridge between the background NetworkClient and the GUI.

* The NetworkClient does all blocking socket work on its own threads and pushes
  events to a thread-safe queue.
* ChatSession drains that queue on the GUI thread (QTimer) and turns events into
  Qt signals + ChatState updates. Widgets never touch sockets directly.
"""
from __future__ import annotations

import hashlib
import queue
import time

from PySide6.QtCore import QObject, QTimer, Signal

from client.network import CIPHER_SUITE, KEY_EXCHANGE, SERVER_AUTH, NetworkClient
from client.state import BROADCAST_KEY, ChatMessage, ChatState, text_problem
from client.store import LocalStore
from common.protocol import parse_delivery_update

PING_INTERVAL_MS = 15_000
DRAIN_INTERVAL_MS = 15


class ChatSession(QObject):
    # connection lifecycle
    handshakeStage = Signal(str)  # tcp | identity | secure
    connectFailed = Signal(str, str, str)  # stage, kind, message
    connected = Signal()
    disconnected = Signal(str, bool, bool)  # reason, user_initiated, was_logged_in

    # authentication
    registerOk = Signal(str)
    loginOk = Signal(str)
    authError = Signal(str, str)  # reason, message

    # workspace
    presenceChanged = Signal()
    syncStarted = Signal(int, int)  # pending, history
    syncFinished = Signal(int, int)  # pending restored, history replayed
    messageAdded = Signal(str, object, bool)  # conversation key, ChatMessage, appended
    messageChanged = Signal(str, object)  # conversation key, ChatMessage
    conversationsChanged = Signal()
    notice = Signal(str, str)  # level, text
    rttUpdated = Signal(float)
    incomingWhileAway = Signal(str, object)  # key, msg (for taskbar alert)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.client: NetworkClient | None = None
        self.state = ChatState()
        self.host = ""
        self.port = 5000
        self.logged_in = False
        self.auth_pending: str | None = None  # "LOGIN" | "REGISTER"
        self.handshake_ok = False
        self.sync_acknowledged = False
        self.connected_at: float | None = None
        self._pending_restored = 0
        self._store_key: tuple | None = None

        self._drain = QTimer(self)
        self._drain.setInterval(DRAIN_INTERVAL_MS)
        self._drain.timeout.connect(self._drain_queue)

        self._ping = QTimer(self)
        self._ping.setInterval(PING_INTERVAL_MS)
        self._ping.timeout.connect(self.ping)

    # ------------------------------------------------------------------ state helpers
    @property
    def is_connected(self) -> bool:
        return bool(self.client and self.client.connected)

    @property
    def can_send(self) -> bool:
        return (
            self.is_connected
            and self.logged_in
            and not self.state.syncing
            and self.sync_acknowledged
        )

    @property
    def username(self) -> str | None:
        return self.state.me

    def server_fingerprint(self) -> str:
        pub = (self.client.trusted_server_pub_bytes if self.client else None) or b""
        digest = hashlib.sha256(pub).hexdigest().upper()
        return " ".join(digest[i : i + 4] for i in range(0, len(digest), 4))

    def server_public_key_hex(self) -> str:
        return self.client.trusted_server_pub_bytes.hex() if self.client else ""

    def diagnostics(self) -> dict:
        c = self.client
        return {
            "tcp": "Connected" if self.is_connected else "Disconnected",
            "connected": self.is_connected,
            "endpoint": f"{self.host}:{self.port}" if self.host else "—",
            "handshake": "Verified" if self.handshake_ok and self.is_connected else "Not established",
            "handshake_ok": self.handshake_ok and self.is_connected,
            "cipher": CIPHER_SUITE,
            "key_exchange": KEY_EXCHANGE,
            "server_auth": SERVER_AUTH,
            "rtt": c.last_rtt_ms if c else None,
            "last_seq": self.state.last_seq,
            "send_records": c.send_record_counter if c else None,
            "recv_records": c.recv_record_counter if c else None,
            "sync": (
                "Synchronizing" if self.state.syncing
                else ("Complete" if self.sync_acknowledged else ("Waiting for login" if not self.logged_in else "Pending"))
            ),
            "uptime": (time.time() - self.connected_at) if (self.connected_at and self.is_connected) else None,
            "user": self.state.me if self.logged_in else None,
        }

    # ------------------------------------------------------------------ actions
    def connect_to(self, host: str, port: int) -> None:
        self.shutdown(silent=True)
        self.host, self.port = host, port
        self.handshake_ok = False
        self.logged_in = False
        self.sync_acknowledged = False
        self.client = NetworkClient()
        self.client.connect(host, port)
        self._drain.start()

    def cancel_connect(self) -> None:
        self.shutdown(silent=True)

    def register(self, username: str, password: str) -> None:
        self._post({"type": "REGISTER", "username": username, "password": password})
        self.auth_pending = "REGISTER"

    def login(self, username: str, password: str) -> None:
        self._post({"type": "LOGIN", "username": username, "password": password})
        self.auth_pending = "LOGIN"

    def send_text(self, to: str, text: str) -> ChatMessage | None:
        problem = text_problem(text)
        if problem:
            self.notice.emit("error", problem)
            return None
        if not self.can_send:
            self.notice.emit("warning", "You can't send messages right now.")
            return None
        msg = self.state.begin_local_send(to, text)
        conv = self.state.conversation_of(msg)
        self.messageAdded.emit(conv.key, msg, True)
        self.conversationsChanged.emit()
        try:
            self._post({"type": "SEND", "to": BROADCAST_KEY if conv.is_broadcast else msg.recipient, "text": text})
        except RuntimeError as err:
            self.state.pending_sends.remove(msg)
            msg.status, msg.error = "failed", str(err)
            self.messageChanged.emit(conv.key, msg)
        return msg

    def retry(self, msg: ChatMessage) -> None:
        conv = self.state.conversation_of(msg)
        if msg in conv.messages:
            conv.messages.remove(msg)
        self.conversationsChanged.emit()
        self.send_text(BROADCAST_KEY if conv.is_broadcast else msg.recipient, msg.text)

    def ping(self) -> None:
        if self.is_connected and self.logged_in:
            try:
                self._post({"type": "PING", "ts": time.time()})
            except RuntimeError:
                pass

    def disconnect(self) -> None:
        self.shutdown(silent=False)

    def shutdown(self, silent: bool = True) -> None:
        """Close the current connection. `silent` drops the resulting events."""
        self._ping.stop()
        if self.client is not None:
            client = self.client
            client.quit()
            if silent:
                self.client = None
                self._drain.stop()
                self._after_disconnect()

    def _after_disconnect(self) -> None:
        was_logged_in = self.logged_in
        self.logged_in = False
        self.handshake_ok = False
        self.sync_acknowledged = False
        self.auth_pending = None
        for msg in self.state.end_session():
            self.messageChanged.emit(self.state.conversation_of(msg).key, msg)
        if was_logged_in:
            self.presenceChanged.emit()
        return was_logged_in

    def _post(self, data: dict) -> None:
        if not self.client or not self.client.connected:
            raise RuntimeError("Not connected to the server.")
        self.client.post(data)

    # ------------------------------------------------------------------ event pump
    def _drain_queue(self) -> None:
        client = self.client
        if client is None:
            return
        for _ in range(500):  # bounded per tick so the GUI stays responsive
            try:
                event = client.incoming_queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._dispatch(event)
            except Exception as err:  # noqa: BLE001 - never let one bad frame kill the GUI
                self.notice.emit("error", f"Unexpected client error: {err}")
            if self.client is not client:
                break

    def _dispatch(self, ev: dict) -> None:
        t = ev.get("type")
        handler = getattr(self, f"_on_{t.lstrip('_').lower()}", None) if isinstance(t, str) else None
        if handler:
            handler(ev)

    # --- connection
    def _on_handshake_stage(self, ev: dict) -> None:
        self.handshakeStage.emit(ev.get("stage", ""))

    def _on_connected(self, ev: dict) -> None:
        self.handshake_ok = True
        self.connected_at = time.time()
        self.connected.emit()

    def _on_connect_failed(self, ev: dict) -> None:
        self.connectFailed.emit(ev.get("stage", ""), ev.get("kind", ""), ev.get("reason", ""))

    def _on_disconnected(self, ev: dict) -> None:
        self._drain.stop()
        self._ping.stop()
        was_logged_in = self._after_disconnect()
        self.client = None
        if ev.get("was_connected"):
            self.disconnected.emit(ev.get("reason", ""), bool(ev.get("user_initiated")), was_logged_in)

    def _on_send_failed(self, ev: dict) -> None:
        if ev.get("request") == "SEND":
            msg = self.state.handle_send_error("BAD_FORMAT", f"Could not send: {ev.get('reason')}")
            if msg:
                self.messageChanged.emit(self.state.conversation_of(msg).key, msg)

    # --- auth
    def _on_register_ok(self, ev: dict) -> None:
        self.auth_pending = None
        self.registerOk.emit(ev.get("username", ""))

    def _on_login_ok(self, ev: dict) -> None:
        self.auth_pending = None
        self.logged_in = True
        self.sync_acknowledged = False
        username = ev.get("username", "")
        self.state.start_session(username, self._open_store(username))
        self.loginOk.emit(self.state.me or "")
        self.ping()
        self._ping.start()

    def _open_store(self, username: str):
        """Local per-account store (messages, delivery status, sync notices). Reused for the
        same server + account; a failure only disables persistence, never messaging."""
        key = (self.host.lower(), self.port, username.lower())
        if self._store_key == key and self.state.store is not None:
            return self.state.store
        try:
            store = LocalStore.for_account(self.host, self.port, username)
        except Exception as err:  # noqa: BLE001
            self.notice.emit("warning", f"Local message history unavailable: {err}")
            return None
        self._store_key = key
        return store

    def mark_notices_seen(self, conv_key: str, sync_ids: set[str]) -> None:
        self.state.mark_notices_seen(conv_key, sync_ids)

    def _on_error(self, ev: dict) -> None:
        reason = ev.get("reason", "ERROR")
        message = ev.get("message", reason)
        if self.auth_pending and not self.logged_in:
            self.auth_pending = None
            self.authError.emit(reason, message)
            return
        msg = self.state.handle_send_error(reason, message)
        if msg is not None:
            conv = self.state.conversation_of(msg)
            self.messageChanged.emit(conv.key, msg)
            if reason == "USER_NOT_FOUND":
                conv.not_found = True
                self.conversationsChanged.emit()
                self.notice.emit("error", f"No registered user named '{msg.recipient}'.")
            elif reason == "QUEUE_FULL":
                self.notice.emit("warning", f"{conv.title}'s offline queue is full (30 messages). Try again later.")
            else:
                self.notice.emit("error", message)
        else:
            self.notice.emit("error", message)

    # --- presence
    def _on_users(self, ev: dict) -> None:
        self.state.set_users(ev.get("users") or [])
        self.presenceChanged.emit()

    def _on_join(self, ev: dict) -> None:
        name = ev.get("username")
        if isinstance(name, str):
            self.state.user_joined(name)
            self.presenceChanged.emit()

    def _on_leave(self, ev: dict) -> None:
        name = ev.get("username")
        if isinstance(name, str):
            self.state.user_left(name)
            self.presenceChanged.emit()

    # --- sync
    def _on_sync_begin(self, ev: dict) -> None:
        pending, history = int(ev.get("pending") or 0), int(ev.get("history") or 0)
        sync_id = ev.get("sync_id") if isinstance(ev.get("sync_id"), str) else None
        self.state.begin_sync(pending, history, sync_id)
        self.sync_acknowledged = False
        self.syncStarted.emit(pending, history)

    def _on_sync_end(self, ev: dict) -> None:
        # NetworkClient sends SYNC_COMPLETE right after this event; we unlock on _SYNC_COMPLETE_SENT.
        pass

    def _on_sync_complete_sent(self, ev: dict) -> None:
        self.state.end_sync()
        self.sync_acknowledged = True
        got = self.state.sync_received
        for conv in self.state.conversations.values():
            conv.resort()
        self.conversationsChanged.emit()
        self.syncFinished.emit(got["pending"], got["history"])

    # --- messages
    def _on_deliver(self, ev: dict) -> None:
        result = self.state.handle_deliver(ev)
        if result is None:
            return
        key = result.conversation.key
        if result.kind == "merged":
            self.messageChanged.emit(key, result.message)
            return
        if result.kind == "duplicate":
            return
        msg = result.message
        if not msg.outgoing and msg.source in ("live", "pending"):
            result.conversation.unread += 1
        self.messageAdded.emit(key, msg, result.appended)
        self.conversationsChanged.emit()
        if not msg.outgoing and msg.source == "live":
            self.incomingWhileAway.emit(key, msg)

    def _on_delivery_update(self, ev: dict) -> None:
        parsed = parse_delivery_update(ev)
        if parsed is None:
            return
        seq, _to, status = parsed
        msg = self.state.handle_delivery_update(seq, status)
        if msg is not None:  # None for duplicates: nothing to redraw
            self.messageChanged.emit(self.state.conversation_of(msg).key, msg)

    def _on_send_ack(self, ev: dict) -> None:
        msg = self.state.handle_ack(ev.get("seq"), ev.get("status", ""))
        if msg is not None:
            self.messageChanged.emit(self.state.conversation_of(msg).key, msg)

    def _on_rtt(self, ev: dict) -> None:
        rtt = ev.get("rtt_ms")
        if isinstance(rtt, (int, float)):
            self.rttUpdated.emit(float(rtt))
