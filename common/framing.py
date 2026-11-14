# common/framing.py
import struct

MAX_PAYLOAD_SIZE = 16384  # 16 KB limit per spec

class FramingError(Exception):
    pass

def encode_frame(payload: bytes) -> bytes:
    """Packs payload with a 4-byte big-endian length header: [4-byte len][payload]"""
    length = len(payload)
    if length == 0 or length > MAX_PAYLOAD_SIZE:
        raise FramingError(f"Invalid payload length: {length}")
    # '>I' means Big-Endian (>), Unsigned 4-byte Integer (I)
    return struct.pack(">I", length) + payload

class FrameReader:
    """Buffers incoming TCP bytes and yields complete frames one by one."""
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self.buffer.extend(data)
        frames = []

        while len(self.buffer) >= 4:
            # Peek at the first 4 bytes to get expected message length
            (length,) = struct.unpack(">I", self.buffer[:4])

            if length == 0:
                raise FramingError("BAD_FORMAT: Zero-length frame")
            if length > MAX_PAYLOAD_SIZE:
                raise FramingError("TOO_LARGE: Frame exceeds MAX_PAYLOAD_SIZE")

            # Wait until the full payload has arrived in the buffer
            if len(self.buffer) < 4 + length:
                break

            # Extract the exact payload and remove it from the buffer
            payload = bytes(self.buffer[4 : 4 + length])
            del self.buffer[: 4 + length]
            frames.append(payload)

        return frames





reader.feed(encode_frame(b"hello") + encode_frame(b"world"))