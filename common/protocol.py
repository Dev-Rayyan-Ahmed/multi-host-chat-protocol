# common/protocol.py
import json
import re

MAX_TEXT_BYTES = 2000
USERNAME_REGEX = re.compile(r"^[A-Za-z0-9_]{3,16}$")

class ProtocolError(Exception):
    def __init__(self, code: int, reason: str, message: str):
        self.code = code
        self.reason = reason
        self.message = message

def validate_username(username: str) -> bool:
    if not isinstance(username, str) or username.upper() == "ALL":
        return False
    return bool(USERNAME_REGEX.match(username))

def encode_message(data: dict) -> bytes:
    """Converts a Python dict to UTF-8 JSON bytes."""
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
    return obj