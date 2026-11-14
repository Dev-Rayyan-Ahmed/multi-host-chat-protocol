# common/secure_session.py
import os
import struct
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

PROTOCOL_ID = b"HORIZON_RELAY_V0.3"

class SecureSession:
    def __init__(self, send_key: bytes, send_prefix: bytes, recv_key: bytes, recv_prefix: bytes, is_server: bool):
        self.send_aes = AESGCM(send_key)
        self.send_prefix = send_prefix  # 4 bytes
        self.send_counter = 0

        self.recv_aes = AESGCM(recv_key)
        self.recv_prefix = recv_prefix  # 4 bytes
        self.recv_counter = 0

        self.send_dir = b"S2C" if is_server else b"C2S"
        self.recv_dir = b"C2S" if is_server else b"S2C"

    @classmethod
    def from_shared_secret(cls, shared_secret: bytes, transcript_hash: bytes, is_server: bool):
        """Derives directional AES keys (32B) and nonce prefixes (4B) using HKDF-SHA256."""
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=72,  # 32 (c2s_key) + 32 (s2c_key) + 4 (c2s_prefix) + 4 (s2c_prefix)
            salt=transcript_hash,
            info=PROTOCOL_ID + b"_KEYS",
        )
        key_material = hkdf.derive(shared_secret)
        c2s_key = key_material[0:32]
        s2c_key = key_material[32:64]
        c2s_prefix = key_material[64:68]
        s2c_prefix = key_material[68:72]

        if is_server:
            return cls(s2c_key, s2c_prefix, c2s_key, c2s_prefix, is_server=True)
        else:
            return cls(c2s_key, c2s_prefix, s2c_key, s2c_prefix, is_server=False)

    def encrypt_record(self, json_bytes: bytes) -> bytes:
        """Returns [8-byte counter][ciphertext + 16-byte GCM tag]"""
        counter_bytes = struct.pack(">Q", self.send_counter)  # 8-byte unsigned int
        nonce = self.send_prefix + counter_bytes              # 4B prefix + 8B counter = 12B nonce

        # Frame length = 8B counter + plaintext len + 16B GCM tag
        frame_len = 8 + len(json_bytes) + 16
        aad = PROTOCOL_ID + self.send_dir + struct.pack(">IQ", frame_len, self.send_counter)

        ciphertext_and_tag = self.send_aes.encrypt(nonce, json_bytes, aad)
        self.send_counter += 1
        return counter_bytes + ciphertext_and_tag

    def decrypt_record(self, record_bytes: bytes) -> bytes:
        """Verifies counter & GCM tag, returns decrypted json_bytes."""
        if len(record_bytes) < 24:  # 8B counter + at least 16B tag
            raise ValueError("Record too short")

        (counter,) = struct.unpack(">Q", record_bytes[:8])
        if counter != self.recv_counter:
            raise ValueError(f"Replay or out-of-order counter! Expected {self.recv_counter}, got {counter}")

        nonce = self.recv_prefix + record_bytes[:8]
        frame_len = len(record_bytes)
        aad = PROTOCOL_ID + self.recv_dir + struct.pack(">IQ", frame_len, counter)

        plaintext = self.recv_aes.decrypt(nonce, record_bytes[8:], aad)
        self.recv_counter += 1
        return plaintext