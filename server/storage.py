# server/storage.py
import sqlite3
import threading
import time
import uuid
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

PENDING_CAP = 30
HISTORY_LIMIT = 30

# Argon2id password hasher (Required by Section 5 of spec)
ph = PasswordHasher()


class Storage:
    """
    Thread-safe SQLite storage layer for accounts, messages, and sync batches.
    """
    def __init__(self, db_path: str = "horizon_relay.db"):
        # check_same_thread=False allows multiple client threads to use the DB safely with self.lock
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self.lock, self.conn:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    normalized_username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    text TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    delivery_state TEXT NOT NULL -- 'pending' or 'delivered'
                );

                CREATE TABLE IF NOT EXISTS sync_batches (
                    sync_id TEXT PRIMARY KEY,
                    recipient TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    status TEXT NOT NULL -- 'open' or 'completed'
                );

                CREATE TABLE IF NOT EXISTS sync_batch_messages (
                    sync_id TEXT NOT NULL,
                    message_seq INTEGER NOT NULL,
                    PRIMARY KEY (sync_id, message_seq)
                );
                """
            )

    def create_account(self, username: str, password: str) -> bool:
        """
        Creates a new user account with an Argon2id password hash.
        Returns True if created, False if username is already taken (case-insensitively).
        """
        norm = username.lower()
        pw_hash = ph.hash(password)
        now = int(time.time())
        with self.lock:
            try:
                with self.conn:
                    self.conn.execute(
                        """
                        INSERT INTO accounts (username, normalized_username, password_hash, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (username, norm, pw_hash, now),
                    )
                return True
            except sqlite3.IntegrityError:
                return False

    def verify_credentials(self, username: str, password: str) -> str | None:
        """
        Verifies username & password. Returns the canonical (original case) username
        if valid, or None if user doesn't exist or password is wrong.
        """
        norm = username.lower()
        with self.lock:
            cur = self.conn.execute(
                "SELECT username, password_hash FROM accounts WHERE normalized_username = ?",
                (norm,),
            )
            row = cur.fetchone()

        if not row:
            return None

        try:
            ph.verify(row["password_hash"], password)
            return row["username"]
        except VerifyMismatchError:
            return None

    def get_canonical_username(self, username: str) -> str | None:
        """Looks up a registered user case-insensitively and returns their exact username."""
        with self.lock:
            cur = self.conn.execute(
                "SELECT username FROM accounts WHERE normalized_username = ?",
                (username.lower(),),
            )
            row = cur.fetchone()
            return row["username"] if row else None

    def count_pending_messages(self, recipient: str) -> int:
        """Counts how many unread offline ('pending') messages a user currently has."""
        with self.lock:
            cur = self.conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM messages
                WHERE LOWER(recipient) = LOWER(?) AND delivery_state = 'pending'
                """,
                (recipient,),
            )
            return cur.fetchone()["cnt"]

    def store_message(
        self, sender: str, recipient: str, text: str, delivery_state: str
    ) -> tuple[int, int]:
        """
        Saves a message and assigns the next globally increasing sequence number (seq).
        Returns (seq, timestamp).
        """
        ts = int(time.time())
        with self.lock, self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO messages (sender, recipient, text, timestamp, delivery_state)
                VALUES (?, ?, ?, ?, ?)
                """,
                (sender, recipient, text, ts, delivery_state),
            )
            return cur.lastrowid, ts

    def create_sync_batch(self, username: str) -> tuple[str, list[dict], list[dict]]:
        """
        Prepares login synchronization data for a user:
        1. Fetches all 'pending' private messages (oldest first).
        2. Fetches the last 30 'delivered' history messages (broadcasts + private sent/received).
        3. Records a sync_id batch so pending messages are only cleared upon SYNC_COMPLETE.
        """
        sync_id = str(uuid.uuid4())
        now = int(time.time())

        with self.lock, self.conn:
            # 1. Fetch pending messages oldest first (ORDER BY seq ASC)
            cur_pending = self.conn.execute(
                """
                SELECT seq, sender, recipient, text, timestamp
                FROM messages
                WHERE LOWER(recipient) = LOWER(?) AND delivery_state = 'pending'
                ORDER BY seq ASC
                """,
                (username,),
            )
            pending_rows = cur_pending.fetchall()

            # 2. Fetch last 30 relevant delivered messages (excluding pending messages!)
            cur_history = self.conn.execute(
                """
                SELECT seq, sender, recipient, text, timestamp
                FROM (
                    SELECT seq, sender, recipient, text, timestamp
                    FROM messages
                    WHERE delivery_state = 'delivered'
                      AND (
                          recipient = 'ALL'
                          OR LOWER(sender) = LOWER(?)
                          OR LOWER(recipient) = LOWER(?)
                      )
                    ORDER BY seq DESC
                    LIMIT ?
                )
                ORDER BY seq ASC
                """,
                (username, username, HISTORY_LIMIT),
            )
            history_rows = cur_history.fetchall()

            # 3. Record the sync batch and link the pending message sequence numbers
            self.conn.execute(
                "INSERT INTO sync_batches (sync_id, recipient, created_at, status) VALUES (?, ?, ?, 'open')",
                (sync_id, username, now),
            )
            for row in pending_rows:
                self.conn.execute(
                    "INSERT INTO sync_batch_messages (sync_id, message_seq) VALUES (?, ?)",
                    (sync_id, row["seq"]),
                )

        pending_frames = [
            {
                "type": "DELIVER",
                "seq": r["seq"],
                "from": r["sender"],
                "to": r["recipient"],
                "text": r["text"],
                "ts": r["timestamp"],
                "source": "pending",
            }
            for r in pending_rows
        ]

        history_frames = [
            {
                "type": "DELIVER",
                "seq": r["seq"],
                "from": r["sender"],
                "to": r["recipient"],
                "text": r["text"],
                "ts": r["timestamp"],
                "source": "history",
            }
            for r in history_rows
        ]

        return sync_id, pending_frames, history_frames

    def complete_sync_batch(self, username: str, sync_id: str) -> bool:
        """
        Marks all pending messages in the given sync_id batch as 'delivered' transactionally.
        Returns True if valid and completed, False otherwise.
        """
        with self.lock, self.conn:
            cur = self.conn.execute(
                """
                SELECT status FROM sync_batches
                WHERE sync_id = ? AND LOWER(recipient) = LOWER(?)
                """,
                (sync_id, username),
            )
            row = cur.fetchone()
            if not row or row["status"] != "open":
                return False

            self.conn.execute(
                """
                UPDATE messages
                SET delivery_state = 'delivered'
                WHERE seq IN (
                    SELECT message_seq FROM sync_batch_messages WHERE sync_id = ?
                )
                """,
                (sync_id,),
            )
            self.conn.execute(
                "UPDATE sync_batches SET status = 'completed' WHERE sync_id = ?",
                (sync_id,),
            )
            return True

    def close(self) -> None:
        self.conn.close()