# common/protocol.py
import json
import re

MAX_TEXT_BYTES = 2000
MIN_PASSWORD_LEN = 8
USERNAME_REGEX = re.compile(r"^[A-Za-z0-9_]{3,16}$")


class ProtocolError(Exception):
    """Raised when a message violates JSON or validation rules."""
    def __init__(self, code: int, reason: str, message: str):
        super().__init__(message)
        self.code = code
        self.reason = reason
        self.message = message


def validate_username(username: str) -> bool:
    """Checks username rules: 3-16 chars, alphanumeric/underscore, not 'ALL'."""
    if not isinstance(username, str) or not USERNAME_REGEX.match(username):
        raise ProtocolError(
            422, "INVALID_USERNAME", "Username must be 3-16 alphanumeric characters or underscores."
        )

    if username.upper() == "ALL":
        raise ProtocolError(422, "INVALID_USERNAME", "'ALL' is a reserved username.")

    return True


def validate_password(password: str) -> bool:
    """Checks password minimum length (8 characters)."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LEN:
        raise ProtocolError(400, "BAD_FORMAT", "Password must be at least 8 characters.")
    return True


def validate_text(text: str) -> bool:
    """Checks that chat text is a non-empty string and <= 2000 UTF-8 bytes."""
    if not isinstance(text, str) or len(text) == 0:
        raise ProtocolError(400, "BAD_FORMAT", "Message text cannot be empty.")

    # Enforce UTF-8 byte length, not character count (emojis take 4 bytes!)
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ProtocolError(503, "TEXT_TOO_LONG", f"Text exceeds {MAX_TEXT_BYTES} UTF-8 bytes.")
    return True


# --- Delivery status updates (server -> client) ---------------------------------------
# Sent to the ORIGINAL SENDER of a private message that was queued (SEND_ACK status=queued)
# once the recipient's client acknowledged the pending batch with SYNC_COMPLETE.
# "delivered" means the recipient's application processed the message; it is NOT a read receipt.
DELIVERY_UPDATE = "DELIVERY_UPDATE"
DELIVERY_STATUSES = ("delivered",)


def make_delivery_update(seq: int, to: str, status: str = "delivered") -> dict:
    return {"type": DELIVERY_UPDATE, "seq": seq, "to": to, "status": status}


def parse_delivery_update(msg: dict) -> tuple[int, str, str] | None:
    """Validates a DELIVERY_UPDATE frame; returns (seq, to, status) or None if malformed."""
    seq, to, status = msg.get("seq"), msg.get("to"), msg.get("status")
    if not isinstance(seq, int) or isinstance(seq, bool) or not isinstance(to, str):
        return None
    if status not in DELIVERY_STATUSES:
        return None
    return seq, to, status


def encode_message(data: dict) -> bytes:
    """Converts a Python dict to UTF-8 JSON bytes."""
    if not isinstance(data, dict) or "type" not in data:
        raise ValueError("Outgoing message must be a dict with a 'type' field.")
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


def decode_message(raw_bytes: bytes) -> dict:
    """Parses UTF-8 JSON bytes and validates basic structure."""
    try:
        text = raw_bytes.decode("utf-8")
        obj = json.loads(text)
    except Exception:
        raise ProtocolError(400, "BAD_FORMAT", "Invalid UTF-8 or malformed JSON")

    if not isinstance(obj, dict) or "type" not in obj:
        raise ProtocolError(400, "BAD_FORMAT", "JSON must be an object with a 'type' field")

    if not isinstance(obj["type"], str) or not obj["type"]:
        raise ProtocolError(400, "BAD_FORMAT", "Missing or invalid 'type' field")

    return obj