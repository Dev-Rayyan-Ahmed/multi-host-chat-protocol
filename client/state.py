# client/state.py
"""
Pure-Python chat state for the Horizon Relay client (no Qt imports).

Turns protocol events (USERS / JOIN / LEAVE / DELIVER / SEND_ACK / ERROR / SYNC_*)
into conversations and messages, and reconciles optimistic outgoing messages with
the server's answers. Keeping this logic free of widgets makes it unit-testable.

Two different counters exist in this project and must not be confused:
  * message `seq`      - server-assigned global id of an accepted chat message
  * record counter     - per-direction AES-GCM record counter (see SecureSession)
Only `seq` is used here.
"""
from __future__ import annotations

import bisect
import itertools
import time
from collections import deque
from dataclasses import dataclass, field

from common.protocol import MAX_TEXT_BYTES, USERNAME_REGEX

BROADCAST_KEY = "ALL"

# Outgoing delivery states. "sent" == SEND_ACK status "delivered": the server handed the
# message to the recipient's active connection. It does NOT mean the message was read.
STATUS_SENDING = "sending"
STATUS_SENT = "sent"
STATUS_QUEUED = "queued"
# "delivered" == DELIVERY_UPDATE: a message that was queued for an offline recipient has
# been processed and acknowledged (SYNC_COMPLETE) by the recipient's app. Not "read".
STATUS_DELIVERED = "delivered"
STATUS_FAILED = "failed"

# ERROR reasons that answer a SEND request
SEND_ERROR_REASONS = {
    "USER_NOT_FOUND",
    "QUEUE_FULL",
    "TEXT_TOO_LONG",
    "BAD_FORMAT",
    "UNAUTHORIZED",
}

_local_ids = itertools.count(1)


def utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def username_problem(name: str) -> str | None:
    """Mirrors common.protocol.validate_username but returns a friendly message."""
    if not name:
        return "Enter a username."
    if len(name) < 3:
        return "Usernames are at least 3 characters."
    if len(name) > 16:
        return "Usernames are at most 16 characters."
    if not USERNAME_REGEX.match(name):
        return "Use only letters, digits and underscores."
    if name.upper() == "ALL":
        return "'ALL' is reserved for broadcast."
    return None


def text_problem(text: str) -> str | None:
    if not text.strip():
        return "Message is empty."
    if utf8_len(text) > MAX_TEXT_BYTES:
        return f"Message exceeds {MAX_TEXT_BYTES} UTF-8 bytes."
    return None


@dataclass
class ChatMessage:
    sender: str
    recipient: str
    text: str
    ts: float
    outgoing: bool
    seq: int | None = None
    source: str = "live"  # live | pending | history | local
    status: str | None = None
    error: str | None = None
    sync_id: str | None = None  # login sync batch that delivered this pending message
    fresh: bool = True  # received in this app session (False = loaded from the local store)
    local_id: int = field(default_factory=lambda: next(_local_ids))

    def sort_key(self) -> tuple:
        # Server order when known; optimistic local rows stay at the end in send order.
        return (0, self.seq, self.local_id) if self.seq is not None else (1, 0, self.local_id)


@dataclass
class Conversation:
    key: str
    title: str
    is_broadcast: bool = False
    messages: list[ChatMessage] = field(default_factory=list)
    unread: int = 0
    last_activity: float = 0.0
    not_found: bool = False  # server answered USER_NOT_FOUND

    def insert(self, msg: ChatMessage) -> int:
        keys = [m.sort_key() for m in self.messages]
        idx = bisect.bisect_right(keys, msg.sort_key())
        self.messages.insert(idx, msg)
        self.last_activity = max(self.last_activity, msg.ts)
        return idx

    def resort(self) -> None:
        self.messages.sort(key=ChatMessage.sort_key)


@dataclass
class DeliverResult:
    conversation: Conversation
    message: ChatMessage
    kind: str  # "new" | "merged" (self-broadcast matched a local row) | "duplicate"
    appended: bool = True  # inserted at the end of the timeline


class ChatState:
    def __init__(self) -> None:
        self.me: str | None = None
        self.online: dict[str, str] = {}  # lower -> canonical
        self.conversations: dict[str, Conversation] = {}
        self.seen_seqs: set[int] = set()
        self.pending_sends: deque[ChatMessage] = deque()
        self.syncing = False
        self.sync_counts = (0, 0)
        self.sync_received = {"pending": 0, "history": 0}
        self.last_seq: int | None = None
        self.current_sync_id: str | None = None
        self.store = None  # client.store.LocalStore, attached per account
        # (sync_id, conversation key) notices whose "While you were away" divider is unseen
        self.unseen_notices: set[tuple[str, str]] = set()
        self._ensure_broadcast()

    # ------------------------------------------------------------ session
    def _ensure_broadcast(self) -> Conversation:
        conv = self.conversations.get(BROADCAST_KEY)
        if conv is None:
            conv = Conversation(BROADCAST_KEY, "Broadcast", is_broadcast=True)
            self.conversations[BROADCAST_KEY] = conv
        return conv

    def start_session(self, username: str, store=None) -> bool:
        """Called on LOGIN_OK. Keeps local history when the same account logs back in
        (replayed history is deduplicated by seq); clears it for a different account.
        `store` (a LocalStore) restores messages, statuses and notices saved earlier.
        Returns True when previous state was kept."""
        same = self.me is not None and self.me.lower() == username.lower()
        if not same or (store is not None and store is not self.store):
            self.conversations.clear()
            self.seen_seqs.clear()
            self.unseen_notices.clear()
            self.last_seq = None
            if self.store is not None and self.store is not store:
                self.store.close()
            self.store = store
            self.me = username
            if store is not None:
                self._load_from_store()
            same = False
        self.me = username
        self.online.clear()
        self.pending_sends.clear()
        self.syncing = False
        self._ensure_broadcast()
        return same

    def _load_from_store(self) -> None:
        for r in self.store.load_messages():
            msg = ChatMessage(
                sender=r["sender"], recipient=r["recipient"], text=r["text"], ts=r["ts"],
                outgoing=bool(r["outgoing"]), seq=r["seq"], source=r["source"],
                status=r["status"], sync_id=r["sync_id"], fresh=False,
            )
            self.seen_seqs.add(msg.seq)
            self._bump_seq(msg.seq)
            conv = self.conversation_of(msg)
            if not conv.is_broadcast:
                peer = msg.recipient if msg.outgoing else msg.sender
                if not self.is_me(peer):
                    conv.title = peer
            conv.insert(msg)
        self.unseen_notices = self.store.unseen_notices()

    def _persist(self, msg: ChatMessage) -> None:
        if self.store is None or msg.seq is None:
            return
        try:
            self.store.save_message(msg.seq, msg.sender, msg.recipient, msg.text, msg.ts, msg.outgoing,
                                    msg.status if msg.status != STATUS_SENDING else None,
                                    msg.source, msg.sync_id)
        except Exception:  # noqa: BLE001 - local persistence must never break messaging
            pass

    # ------------------------------------------------------------ sync notices
    def notice_syncs(self, conv_key: str) -> set[str]:
        """Sync batches whose 'While you were away' divider is still unseen in this conversation."""
        return {s for (s, c) in self.unseen_notices if c == conv_key}

    def mark_notices_seen(self, conv_key: str, sync_ids: set[str]) -> None:
        if not sync_ids:
            return
        self.unseen_notices -= {(s, conv_key) for s in sync_ids}
        if self.store is not None:
            try:
                self.store.mark_seen(conv_key, set(sync_ids))
            except Exception:  # noqa: BLE001
                pass

    def _add_notice(self, sync_id: str, conv_key: str) -> None:
        if self.store is not None:
            try:
                if not self.store.add_notice(sync_id, conv_key):
                    return  # already known (seen or unseen): never duplicate a notice
            except Exception:  # noqa: BLE001
                pass
        self.unseen_notices.add((sync_id, conv_key))

    def end_session(self) -> list[ChatMessage]:
        """Connection lost or logged out: fail anything still waiting for an answer."""
        failed = self.fail_all_pending("Connection lost before the server confirmed this message.")
        self.online.clear()
        self.syncing = False
        return failed

    def is_me(self, name: str | None) -> bool:
        return bool(name and self.me and name.lower() == self.me.lower())

    # ------------------------------------------------------------ presence
    def set_users(self, users: list[str]) -> None:
        self.online = {u.lower(): u for u in users if isinstance(u, str)}

    def user_joined(self, name: str) -> None:
        self.online[name.lower()] = name
        conv = self.conversations.get(name.lower())
        if conv:
            conv.title = name

    def user_left(self, name: str) -> None:
        self.online.pop(name.lower(), None)

    def is_online(self, name: str) -> bool:
        return name.lower() in self.online

    @property
    def online_count(self) -> int:
        return len(self.online)

    # ------------------------------------------------------------ conversations
    def conversation_for_peer(self, name: str, create: bool = True) -> Conversation | None:
        key = name.lower()
        conv = self.conversations.get(key)
        if conv is None and create:
            title = self.online.get(key, name)
            conv = Conversation(key, title)
            self.conversations[key] = conv
        return conv

    def prune_unknown(self, keep: str | None = None) -> bool:
        """Drop DM conversations for usernames the server said don't exist."""
        dead = [k for k, c in self.conversations.items()
                if c.not_found and k != keep and not any(m.seq is not None for m in c.messages)]
        for k in dead:
            del self.conversations[k]
        return bool(dead)

    def direct_conversations(self) -> list[Conversation]:
        return [c for c in self.conversations.values() if not c.is_broadcast]

    def sidebar_peers(self) -> list[tuple[str, str]]:
        """(key, display name) for the DM list: conversations by recent activity,
        then online users without a conversation, alphabetically."""
        convs = sorted(self.direct_conversations(), key=lambda c: (-c.last_activity, c.key))
        seen = {c.key for c in convs}
        out = [(c.key, self.online.get(c.key, c.title)) for c in convs]
        extra = sorted(
            (k, v) for k, v in self.online.items() if k not in seen and not self.is_me(v)
        )
        out.extend(extra)
        return out

    # ------------------------------------------------------------ sync
    def begin_sync(self, pending: int, history: int, sync_id: str | None = None) -> None:
        self.syncing = True
        self.current_sync_id = sync_id
        self.sync_counts = (pending, history)
        self.sync_received = {"pending": 0, "history": 0}

    def end_sync(self) -> None:
        self.syncing = False
        self.current_sync_id = None

    # ------------------------------------------------------------ outgoing
    def begin_local_send(self, to: str, text: str) -> ChatMessage:
        recipient = BROADCAST_KEY if to.upper() == BROADCAST_KEY else to
        msg = ChatMessage(
            sender=self.me or "",
            recipient=recipient,
            text=text,
            ts=time.time(),
            outgoing=True,
            source="local",
            status=STATUS_SENDING,
        )
        conv = (
            self._ensure_broadcast()
            if recipient == BROADCAST_KEY
            else self.conversation_for_peer(recipient)
        )
        conv.insert(msg)
        self.pending_sends.append(msg)
        return msg

    def conversation_of(self, msg: ChatMessage) -> Conversation:
        if msg.recipient.upper() == BROADCAST_KEY:
            return self._ensure_broadcast()
        peer = msg.recipient if msg.outgoing else msg.sender
        return self.conversation_for_peer(peer)

    def handle_ack(self, seq: int, status: str) -> ChatMessage | None:
        """SEND_ACK answers the oldest outstanding SEND (the server answers in order)."""
        if not self.pending_sends:
            return None
        msg = self.pending_sends.popleft()
        msg.seq = seq
        msg.status = STATUS_QUEUED if status == "queued" else STATUS_SENT
        if isinstance(seq, int):
            self.seen_seqs.add(seq)
            self._bump_seq(seq)
        self.conversation_of(msg).resort()
        self._persist(msg)
        return msg

    def handle_delivery_update(self, seq: int, status: str) -> ChatMessage | None:
        """DELIVERY_UPDATE for one of our queued messages. Idempotent: duplicates and
        updates for unknown seqs return None (the latter are still saved if stored)."""
        if status != STATUS_DELIVERED:
            return None
        msg = self._find_by_seq(seq)
        if msg is None or not msg.outgoing:
            if self.store is not None:
                try:
                    self.store.set_status(seq, STATUS_DELIVERED)
                except Exception:  # noqa: BLE001
                    pass
            return None
        if msg.status == STATUS_DELIVERED:
            return None
        msg.status = STATUS_DELIVERED
        self._persist(msg)
        return msg

    def handle_send_error(self, reason: str, message: str) -> ChatMessage | None:
        if reason not in SEND_ERROR_REASONS or not self.pending_sends:
            return None
        msg = self.pending_sends.popleft()
        msg.status = STATUS_FAILED
        msg.error = message or reason
        return msg

    def fail_all_pending(self, reason: str) -> list[ChatMessage]:
        failed = []
        while self.pending_sends:
            msg = self.pending_sends.popleft()
            msg.status = STATUS_FAILED
            msg.error = reason
            failed.append(msg)
        return failed

    # ------------------------------------------------------------ incoming
    def _bump_seq(self, seq: int) -> None:
        if self.last_seq is None or seq > self.last_seq:
            self.last_seq = seq

    def handle_deliver(self, frame: dict) -> DeliverResult | None:
        seq = frame.get("seq")
        sender = frame.get("from")
        to = frame.get("to")
        text = frame.get("text")
        if not isinstance(sender, str) or not isinstance(to, str) or not isinstance(text, str):
            return None
        source = frame.get("source", "live")
        ts = frame.get("ts")
        ts = float(ts) if isinstance(ts, (int, float)) else time.time()
        outgoing = self.is_me(sender)

        if source in self.sync_received:
            self.sync_received[source] += 1

        if isinstance(seq, int):
            self._bump_seq(seq)
            if seq in self.seen_seqs:
                existing = self._find_by_seq(seq)
                if existing is not None:
                    return DeliverResult(self.conversation_of(existing), existing, "duplicate")
                # seq acked locally but row not found (shouldn't happen) -> fall through

        # The broadcast sender receives its own DELIVER before SEND_ACK. Attach the seq to
        # the optimistic local row instead of rendering a duplicate.
        if outgoing and source == "live" and to.upper() == BROADCAST_KEY:
            for local in self.pending_sends:
                if local.seq is None and local.recipient == BROADCAST_KEY and local.text == text:
                    local.seq = seq if isinstance(seq, int) else None
                    if isinstance(seq, int):
                        self.seen_seqs.add(seq)
                    conv = self._ensure_broadcast()
                    conv.resort()
                    return DeliverResult(conv, local, "merged")

        if isinstance(seq, int):
            self.seen_seqs.add(seq)

        msg = ChatMessage(
            sender=sender,
            recipient=BROADCAST_KEY if to.upper() == BROADCAST_KEY else to,
            text=text,
            ts=ts,
            outgoing=outgoing,
            seq=seq if isinstance(seq, int) else None,
            source=source,
            sync_id=self.current_sync_id if source == "pending" else None,
        )
        if to.upper() == BROADCAST_KEY:
            conv = self._ensure_broadcast()
        else:
            peer = to if outgoing else sender
            conv = self.conversation_for_peer(peer)
            if not self.is_me(peer):
                conv.title = self.online.get(peer.lower(), peer)
        idx = conv.insert(msg)
        if msg.sync_id is not None:
            # one notice per (sync batch, conversation), only for messages new to this client
            self._add_notice(msg.sync_id, conv.key)
        self._persist(msg)
        return DeliverResult(conv, msg, "new", idx == len(conv.messages) - 1)

    def _find_by_seq(self, seq: int) -> ChatMessage | None:
        for conv in self.conversations.values():
            for m in reversed(conv.messages):
                if m.seq == seq:
                    return m
        return None
