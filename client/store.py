# client/store.py
"""
Per-account local persistence for the desktop client (SQLite, one file per server + account).

Keeps, across reconnects and application restarts:
  * messages that have a server-assigned `seq` (incoming and outgoing),
  * the delivery status of the user's own messages (sent / queued / delivered),
  * "While you were away" notices: one per (sync batch, conversation), and whether the
    user has already seen it.

The store is used only from the GUI thread (ChatState runs there), so one connection is
enough. Writes are tiny and committed immediately (WAL journal), so they don't stall the UI.

Note: message text is stored unencrypted on this device, like the server's own database.
The design keeps notice state local; a server-side per-account table could later sync it
across devices without changing the ChatState API.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path

# Status precedence: a later, weaker status never overwrites a stronger one
# (e.g. a duplicate SEND_ACK "queued" arriving after DELIVERY_UPDATE "delivered").
_RANK = {None: 0, "sending": 1, "failed": 1, "queued": 2, "sent": 3, "delivered": 4}


def default_data_dir() -> Path:
    env = os.environ.get("HORIZON_RELAY_DATA_DIR")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "HorizonRelay"


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text)


class LocalStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        with self.conn:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    seq INTEGER PRIMARY KEY,
                    sender TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    text TEXT NOT NULL,
                    ts REAL NOT NULL,
                    outgoing INTEGER NOT NULL,
                    status TEXT,
                    source TEXT NOT NULL,
                    sync_id TEXT
                );
                CREATE TABLE IF NOT EXISTS sync_notices (
                    sync_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    notice_seen INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    seen_at REAL,
                    PRIMARY KEY (sync_id, conversation_id)
                );
                """
            )

    @classmethod
    def for_account(cls, host: str, port: int, username: str, data_dir: Path | None = None) -> "LocalStore":
        d = data_dir or default_data_dir()
        return cls(d / f"{_slug(host)}_{port}_{_slug(username.lower())}.db")

    # ------------------------------------------------------------------ messages
    def load_messages(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM messages ORDER BY seq ASC").fetchall()

    def save_message(self, seq: int, sender: str, recipient: str, text: str, ts: float, outgoing: bool,
                     status: str | None, source: str, sync_id: str | None) -> None:
        """Insert, or update the status only (never downgrading it, never rewriting
        the original text, timestamp, source or sync batch)."""
        with self.conn:
            row = self.conn.execute("SELECT status FROM messages WHERE seq = ?", (seq,)).fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO messages (seq, sender, recipient, text, ts, outgoing, status, source, sync_id)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (seq, sender, recipient, text, ts, int(outgoing), status, source, sync_id),
                )
            elif _RANK.get(status, 0) > _RANK.get(row["status"], 0):
                self.conn.execute("UPDATE messages SET status = ? WHERE seq = ?", (status, seq))

    def set_status(self, seq: int, status: str) -> bool:
        """Raise the stored status of an outgoing message. True if something changed."""
        with self.conn:
            row = self.conn.execute("SELECT status FROM messages WHERE seq = ? AND outgoing = 1", (seq,)).fetchone()
            if row is None or _RANK.get(status, 0) <= _RANK.get(row["status"], 0):
                return False
            self.conn.execute("UPDATE messages SET status = ? WHERE seq = ?", (status, seq))
            return True

    def get_status(self, seq: int) -> str | None:
        row = self.conn.execute("SELECT status FROM messages WHERE seq = ?", (seq,)).fetchone()
        return row["status"] if row else None

    # ------------------------------------------------------------------ notices
    def add_notice(self, sync_id: str, conversation_id: str) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO sync_notices (sync_id, conversation_id, notice_seen, created_at)"
                " VALUES (?, ?, 0, ?)",
                (sync_id, conversation_id, time.time()),
            )
            return cur.rowcount > 0

    def unseen_notices(self) -> set[tuple[str, str]]:
        rows = self.conn.execute(
            "SELECT sync_id, conversation_id FROM sync_notices WHERE notice_seen = 0"
        ).fetchall()
        return {(r["sync_id"], r["conversation_id"]) for r in rows}

    def mark_seen(self, conversation_id: str, sync_ids: set[str]) -> None:
        if not sync_ids:
            return
        now = time.time()
        with self.conn:
            self.conn.executemany(
                "UPDATE sync_notices SET notice_seen = 1, seen_at = ?"
                " WHERE sync_id = ? AND conversation_id = ? AND notice_seen = 0",
                [(now, s, conversation_id) for s in sync_ids],
            )

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass
