# server/services.py
import threading
from typing import Callable
from common.protocol import (
    ProtocolError,
    make_delivery_update,
    validate_password,
    validate_text,
    validate_username,
)
from server.storage import PENDING_CAP, Storage


class ClientHandle:
    """
    Represents a single connected client's session state on the server.
    `send_callback` is a function that sends a dict to this specific client.
    """
    def __init__(self, send_callback: Callable[[dict], None]):
        self.send = send_callback
        self.state = "CONNECTED"  # Transitions: CONNECTED -> LOGGED_IN -> CLOSED
        self.username: str | None = None
        self.active_sync_id: str | None = None
        self.held_live_messages: list[dict] = []

    def deliver_live(self, deliver_msg: dict) -> None:
        """
        Sends a live DELIVER message immediately if the user is done syncing,
        or holds it in memory if the user is currently in the middle of a login sync.
        """
        if self.active_sync_id is not None:
            self.held_live_messages.append(deliver_msg)
        else:
            self.send(deliver_msg)


class ChatService:
    """
    Coordinates registration, authentication, presence, message routing, and synchronization.
    """
    def __init__(self, storage: Storage):
        self.storage = storage
        self.lock = threading.Lock()
        # Maps lowercase username -> active ClientHandle
        self.online_users: dict[str, ClientHandle] = {}

    def handle_message(self, client: ClientHandle, msg: dict) -> bool:
        """
        Processes an incoming decoded dictionary from a client.
        Returns False if the connection should be closed (e.g., on QUIT), True otherwise.
        """
        mtype = msg.get("type")

        try:
            if mtype == "REGISTER":
                self._handle_register(client, msg)
            elif mtype == "LOGIN":
                self._handle_login(client, msg)
            elif mtype == "SEND":
                self._handle_send(client, msg)
            elif mtype == "PING":
                self._handle_ping(client, msg)
            elif mtype == "SYNC_COMPLETE":
                self._handle_sync_complete(client, msg)
            elif mtype == "QUIT":
                self.disconnect_client(client)
                return False
            else:
                raise ProtocolError(501, "UNKNOWN_TYPE", f"Unsupported message type: {mtype}")
        except ProtocolError as err:
            client.send(
                {
                    "type": "ERROR",
                    "code": err.code,
                    "reason": err.reason,
                    "message": err.message,
                }
            )
        return True

    def _handle_register(self, client: ClientHandle, msg: dict) -> None:
        if client.state == "LOGGED_IN":
            raise ProtocolError(403, "ALREADY_LOGGED_IN", "Cannot register while logged in.")

        username = msg.get("username")
        password = msg.get("password")
        validate_username(username)
        validate_password(password)

        created = self.storage.create_account(username, password)
        if not created:
            raise ProtocolError(409, "USERNAME_TAKEN", "Username already exists.")

        # Spec 7.2: REGISTER_OK does not automatically log the user in; state stays CONNECTED
        client.send({"type": "REGISTER_OK", "username": username})

    def _handle_login(self, client: ClientHandle, msg: dict) -> None:
        if client.state == "LOGGED_IN":
            raise ProtocolError(403, "ALREADY_LOGGED_IN", "Session is already logged in.")

        username = msg.get("username")
        password = msg.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            raise ProtocolError(400, "BAD_FORMAT", "Missing username or password.")

        # Spec 7.2: Unknown username and wrong password must produce the exact same 502 AUTH_FAILED
        canonical_user = self.storage.verify_credentials(username, password)
        if not canonical_user:
            raise ProtocolError(502, "AUTH_FAILED", "Invalid username or password.")

        norm_user = canonical_user.lower()

        with self.lock:
            if norm_user in self.online_users:
                raise ProtocolError(
                    403, "ALREADY_LOGGED_IN", "Account already has an active session."
                )

            # Mark client as logged in and preparing for sync
            client.state = "LOGGED_IN"
            client.username = canonical_user
            sync_id, pending_frames, history_frames = self.storage.create_sync_batch(canonical_user)
            # Delivery news for messages this user queued earlier (delivered while they were away)
            update_frames = self.storage.attach_status_updates(sync_id, canonical_user)
            client.active_sync_id = sync_id
            self.online_users[norm_user] = client

            current_users = [h.username for h in self.online_users.values() if h.username]

            # Spec 7.2 exact login sequence order:
            # 1. LOGIN_OK
            client.send({"type": "LOGIN_OK", "username": canonical_user})
            # 2. USERS
            client.send({"type": "USERS", "users": current_users})
            # 3. SYNC_BEGIN
            client.send(
                {
                    "type": "SYNC_BEGIN",
                    "sync_id": sync_id,
                    "pending": len(pending_frames),
                    "history": len(history_frames),
                    "updates": len(update_frames),
                }
            )
            # 4. All pending DELIVER frames (oldest first)
            for p_frame in pending_frames:
                client.send(p_frame)
            # 5. All history DELIVER frames
            for h_frame in history_frames:
                client.send(h_frame)
            # 5b. DELIVERY_UPDATE frames (confirmed by this batch's SYNC_COMPLETE)
            for u_frame in update_frames:
                client.send(u_frame)
            # 6. SYNC_END
            client.send({"type": "SYNC_END", "sync_id": sync_id})

            # 7. Broadcast JOIN to all logged-in users (including the newly logged-in user)
            join_msg = {"type": "JOIN", "username": canonical_user}
            for online_handle in self.online_users.values():
                online_handle.send(join_msg)

    def _handle_send(self, client: ClientHandle, msg: dict) -> None:
        if client.state != "LOGGED_IN" or not client.username:
            raise ProtocolError(401, "UNAUTHORIZED", "Must be logged in to send messages.")

        to_raw = msg.get("to")
        text = msg.get("text")
        if not isinstance(to_raw, str) or not to_raw:
            raise ProtocolError(400, "BAD_FORMAT", "Missing 'to' recipient field.")
        validate_text(text)

        sender = client.username

        with self.lock:
            # Case A: Broadcast message (to == "ALL" case-insensitively)
            if to_raw.upper() == "ALL":
                seq, ts = self.storage.store_message(sender, "ALL", text, "delivered")
                deliver_frame = {
                    "type": "DELIVER",
                    "seq": seq,
                    "from": sender,
                    "to": "ALL",
                    "text": text,
                    "ts": ts,
                    "source": "live",
                }
                for online_handle in self.online_users.values():
                    online_handle.deliver_live(deliver_frame)

                client.send({"type": "SEND_ACK", "seq": seq, "status": "delivered"})
                return

            # Case B: Private message to a specific user
            canonical_recipient = self.storage.get_canonical_username(to_raw)
            if not canonical_recipient:
                raise ProtocolError(404, "USER_NOT_FOUND", f"User '{to_raw}' does not exist.")

            norm_recipient = canonical_recipient.lower()
            recipient_handle = self.online_users.get(norm_recipient)

            # Recipient is currently online
            if recipient_handle is not None:
                seq, ts = self.storage.store_message(
                    sender, canonical_recipient, text, "delivered"
                )
                deliver_frame = {
                    "type": "DELIVER",
                    "seq": seq,
                    "from": sender,
                    "to": canonical_recipient,
                    "text": text,
                    "ts": ts,
                    "source": "live",
                }
                recipient_handle.deliver_live(deliver_frame)
                client.send({"type": "SEND_ACK", "seq": seq, "status": "delivered"})
            else:
                # Recipient is offline -> check 30 pending message cap!
                pending_count = self.storage.count_pending_messages(canonical_recipient)
                if pending_count >= PENDING_CAP:
                    raise ProtocolError(
                        429, "QUEUE_FULL", "Recipient's offline message queue is full (max 30)."
                    )
                seq, _ = self.storage.store_message(
                    sender, canonical_recipient, text, "pending"
                )
                client.send({"type": "SEND_ACK", "seq": seq, "status": "queued"})

    def _handle_sync_complete(self, client: ClientHandle, msg: dict) -> None:
        if client.state != "LOGGED_IN" or not client.username:
            raise ProtocolError(401, "UNAUTHORIZED", "Must be logged in.")

        sync_id = msg.get("sync_id")
        if not isinstance(sync_id, str) or sync_id != client.active_sync_id:
            raise ProtocolError(400, "BAD_FORMAT", "Invalid or unexpected sync_id.")

        with self.lock:
            _ok, newly_delivered = self.storage.complete_sync_batch_detailed(client.username, sync_id)
            client.active_sync_id = None
            # The recipient's app has acknowledged these queued messages: tell each original
            # sender who is online now. This live push is best effort; the durable path is the
            # sender's next login sync (sender_notified stays 0 until a sync batch confirms it).
            for item in newly_delivered:
                sender_handle = self.online_users.get(item["sender"].lower())
                if sender_handle is not None and sender_handle is not client:
                    sender_handle.deliver_live(make_delivery_update(item["seq"], item["recipient"]))
            # Release any live messages that arrived while this client was syncing!
            for held_msg in client.held_live_messages:
                client.send(held_msg)
            client.held_live_messages.clear()

    def _handle_ping(self, client: ClientHandle, msg: dict) -> None:
        if client.state != "LOGGED_IN":
            raise ProtocolError(401, "UNAUTHORIZED", "Must be logged in to PING.")
        if "ts" not in msg or not isinstance(msg["ts"], (int, float)):
            raise ProtocolError(400, "BAD_FORMAT", "PING requires numeric 'ts'.")
        client.send({"type": "PONG", "ts": msg["ts"]})

    def disconnect_client(self, client: ClientHandle) -> None:
        """Removes a client from online users and broadcasts LEAVE if they were logged in."""
        with self.lock:
            if client.state == "LOGGED_IN" and client.username:
                norm_user = client.username.lower()
                if self.online_users.get(norm_user) is client:
                    del self.online_users[norm_user]
                    leave_msg = {"type": "LEAVE", "username": client.username}
                    for online_handle in self.online_users.values():
                        online_handle.send(leave_msg)
            client.state = "CLOSED"
            client.active_sync_id = None
            client.held_live_messages.clear()