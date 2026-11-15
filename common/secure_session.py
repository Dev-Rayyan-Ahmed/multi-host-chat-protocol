# common/secure_session.py
import base64
import hashlib
import os
import struct
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from common.protocol import decode_message, encode_message

PROTOCOL_ID = b"HORIZON_RELAY_V0.3"

# Fixed project identity seed so the Server and Client share a known trusted Server Public Key
# across LAN machines without needing to copy certificate files manually.
DEFAULT_SERVER_SEED = b"horizon-relay-academic-server-key-seed-2026"


class SecurityError(Exception):
    """Raised when any handshake signature, GCM tag, or replay counter fails."""
    pass


def get_default_server_identity() -> tuple[ed25519.Ed25519PrivateKey, bytes]:
    """Generates a deterministic Ed25519 keypair for the server and returns (priv_key, pub_bytes)."""
    seed = hashlib.sha256(DEFAULT_SERVER_SEED).digest()
    priv_key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    pub_bytes = priv_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return priv_key, pub_bytes


def compute_transcript_hash(
    client_eph_pub: bytes,
    client_nonce: bytes,
    server_eph_pub: bytes,
    server_nonce: bytes,
) -> bytes:
    """
    Creates a unique SHA-256 fingerprint of the entire handshake conversation.
    Signed by the server and used as context in HKDF key derivation.
    """
    h = hashlib.sha256()
    h.update(PROTOCOL_ID)
    h.update(b"|CLIENT|")
    h.update(client_eph_pub)
    h.update(client_nonce)
    h.update(b"|SERVER|")
    h.update(server_eph_pub)
    h.update(server_nonce)
    return h.digest()


class SecureSession:
    """
    Handles AES-256-GCM encryption and decryption after the handshake finishes.
    Record format: [8-byte unsigned big-endian counter][AES-GCM ciphertext + 16-byte tag]
    """
    def __init__(
        self,
        send_key: bytes,
        send_prefix: bytes,
        recv_key: bytes,
        recv_prefix: bytes,
        is_server: bool,
    ):
        self.send_aes = AESGCM(send_key)
        self.send_prefix = send_prefix  # 4 bytes
        self.send_counter = 0

        self.recv_aes = AESGCM(recv_key)
        self.recv_prefix = recv_prefix  # 4 bytes
        self.recv_counter = 0

        # Direction labels prevent an attacker from reflecting a client's own message back to it
        self.send_dir = b"S2C" if is_server else b"C2S"
        self.recv_dir = b"C2S" if is_server else b"S2C"

    @classmethod
    def from_shared_secret(
        cls, shared_secret: bytes, transcript_hash: bytes, is_server: bool
    ) -> "SecureSession":
        """Uses HKDF-SHA-256 to derive separate Client->Server and Server->Client keys and prefixes."""
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=72,  # 32B c2s_key + 32B s2c_key + 4B c2s_prefix + 4B s2c_prefix
            salt=transcript_hash,
            info=PROTOCOL_ID + b"_SESSION_KEYS",
        )
        material = hkdf.derive(shared_secret)
        c2s_key = material[0:32]
        s2c_key = material[32:64]
        c2s_prefix = material[64:68]
        s2c_prefix = material[68:72]

        if is_server:
            return cls(s2c_key, s2c_prefix, c2s_key, c2s_prefix, is_server=True)
        return cls(c2s_key, c2s_prefix, s2c_key, s2c_prefix, is_server=False)

    def _build_aad(self, direction: bytes, frame_len: int, counter: int) -> bytes:
        """
        Builds Associated Authenticated Data (AAD) for AES-GCM:
        Authenticates protocol ID, direction, frame length, and 8-byte counter.
        """
        return PROTOCOL_ID + direction + struct.pack(">IQ", frame_len, counter)

    def encrypt_record(self, json_bytes: bytes) -> bytes:
        """Encrypts plaintext JSON bytes into an encrypted record payload."""
        if self.send_counter >= (1 << 64) - 1:
            raise SecurityError("Record counter wrapped; must reconnect.")

        counter_bytes = struct.pack(">Q", self.send_counter)
        nonce = self.send_prefix + counter_bytes  # 4-byte prefix + 8-byte counter = 12-byte GCM nonce

        # Complete encrypted payload length = 8B counter + plaintext length + 16B GCM tag
        frame_len = 8 + len(json_bytes) + 16
        aad = self._build_aad(self.send_dir, frame_len, self.send_counter)

        ciphertext_and_tag = self.send_aes.encrypt(nonce, json_bytes, aad)
        self.send_counter += 1
        return counter_bytes + ciphertext_and_tag

    def decrypt_record(self, record_bytes: bytes) -> bytes:
        """Verifies record counter and AES-GCM tag, returning decrypted JSON bytes."""
        # Minimum valid record is 8-byte counter + 16-byte GCM tag = 24 bytes
        if len(record_bytes) < 24:
            raise SecurityError("Malformed encrypted record: too short.")

        (counter,) = struct.unpack(">Q", record_bytes[:8])
        if counter != self.recv_counter:
            raise SecurityError(
                f"Invalid record counter: expected {self.recv_counter}, got {counter}."
            )

        nonce = self.recv_prefix + record_bytes[:8]
        frame_len = len(record_bytes)
        aad = self._build_aad(self.recv_dir, frame_len, counter)

        try:
            plaintext = self.recv_aes.decrypt(nonce, record_bytes[8:], aad)
        except Exception as exc:
            raise SecurityError("AES-GCM authentication tag verification failed.") from exc

        self.recv_counter += 1
        return plaintext

    def encrypt_dict(self, data: dict) -> bytes:
        """Helper: encodes a Python dict to JSON bytes and encrypts it."""
        return self.encrypt_record(encode_message(data))

    def decrypt_dict(self, record_bytes: bytes) -> dict:
        """Helper: decrypts a record and decodes the JSON bytes into a Python dict."""
        return decode_message(self.decrypt_record(record_bytes))


class ClientHandshake:
    """Manages the 3-step handshake for the Client."""
    def __init__(self, trusted_server_pub_bytes: bytes):
        self.trusted_server_pub = ed25519.Ed25519PublicKey.from_public_bytes(
            trusted_server_pub_bytes
        )
        self.eph_priv = x25519.X25519PrivateKey.generate()
        self.eph_pub_bytes = self.eph_priv.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        self.client_nonce = os.urandom(16)
        self.session: SecureSession | None = None

    def create_client_hello(self) -> bytes:
        """Step 1: Client sends ephemeral X25519 public key and random 16-byte nonce."""
        msg = {
            "type": "CLIENT_HELLO",
            "version": "0.3",
            "client_eph_pub": base64.b64encode(self.eph_pub_bytes).decode("ascii"),
            "client_nonce": base64.b64encode(self.client_nonce).decode("ascii"),
        }
        return encode_message(msg)

    def process_server_hello_and_create_finished(self, raw_json_bytes: bytes) -> bytes:
        """
        Step 2: Client verifies Server's Ed25519 signature, derives keys,
        and returns an encrypted FINISHED confirmation record.
        """
        try:
            msg = decode_message(raw_json_bytes)
            if msg.get("type") != "SERVER_HELLO":
                raise SecurityError("Expected SERVER_HELLO.")
            server_eph_pub_bytes = base64.b64decode(msg["server_eph_pub"])
            server_nonce = base64.b64decode(msg["server_nonce"])
            signature = base64.b64decode(msg["signature"])
        except Exception as exc:
            raise SecurityError("Malformed SERVER_HELLO message.") from exc

        transcript_hash = compute_transcript_hash(
            self.eph_pub_bytes, self.client_nonce, server_eph_pub_bytes, server_nonce
        )

        # Verify the server's Ed25519 signature over the transcript hash
        try:
            self.trusted_server_pub.verify(signature, transcript_hash)
        except Exception as exc:
            raise SecurityError("Server Ed25519 signature verification failed!") from exc

        # Calculate X25519 shared secret and derive session keys
        try:
            server_eph_pub = x25519.X25519PublicKey.from_public_bytes(server_eph_pub_bytes)
            shared_secret = self.eph_priv.exchange(server_eph_pub)
        except Exception as exc:
            raise SecurityError("Invalid server X25519 public key.") from exc

        self.session = SecureSession.from_shared_secret(
            shared_secret, transcript_hash, is_server=False
        )
        return self.session.encrypt_dict({"type": "FINISHED", "role": "CLIENT"})

    def verify_server_finished(self, encrypted_record: bytes) -> SecureSession:
        """Step 3: Client decrypts and verifies Server's FINISHED confirmation."""
        if not self.session:
            raise SecurityError("Handshake out of order.")
        msg = self.session.decrypt_dict(encrypted_record)
        if msg.get("type") != "FINISHED" or msg.get("role") != "SERVER":
            raise SecurityError("Invalid server FINISHED confirmation.")
        return self.session


class ServerHandshake:
    """Manages the 3-step handshake for the Server."""
    def __init__(self, identity_priv_key: ed25519.Ed25519PrivateKey):
        self.identity_priv = identity_priv_key
        self.eph_priv = x25519.X25519PrivateKey.generate()
        self.eph_pub_bytes = self.eph_priv.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        self.server_nonce = os.urandom(16)
        self.session: SecureSession | None = None

    def process_client_hello_and_create_server_hello(self, raw_json_bytes: bytes) -> bytes:
        """
        Step 1 (Server): Reads CLIENT_HELLO, signs the handshake transcript with Ed25519,
        derives session keys, and returns SERVER_HELLO.
        """
        try:
            msg = decode_message(raw_json_bytes)
            if msg.get("type") != "CLIENT_HELLO":
                raise SecurityError("Expected CLIENT_HELLO.")
            client_eph_pub_bytes = base64.b64decode(msg["client_eph_pub"])
            client_nonce = base64.b64decode(msg["client_nonce"])
            if len(client_nonce) != 16:
                raise SecurityError("Invalid client nonce length.")
        except Exception as exc:
            raise SecurityError("Malformed CLIENT_HELLO message.") from exc

        transcript_hash = compute_transcript_hash(
            client_eph_pub_bytes, client_nonce, self.eph_pub_bytes, self.server_nonce
        )
        signature = self.identity_priv.sign(transcript_hash)

        try:
            client_eph_pub = x25519.X25519PublicKey.from_public_bytes(client_eph_pub_bytes)
            shared_secret = self.eph_priv.exchange(client_eph_pub)
        except Exception as exc:
            raise SecurityError("Invalid client X25519 public key.") from exc

        self.session = SecureSession.from_shared_secret(
            shared_secret, transcript_hash, is_server=True
        )

        reply = {
            "type": "SERVER_HELLO",
            "version": "0.3",
            "server_eph_pub": base64.b64encode(self.eph_pub_bytes).decode("ascii"),
            "server_nonce": base64.b64encode(self.server_nonce).decode("ascii"),
            "signature": base64.b64encode(signature).decode("ascii"),
        }
        return encode_message(reply)

    def verify_client_finished_and_create_server_finished(
        self, encrypted_record: bytes
    ) -> tuple[bytes, SecureSession]:
        """
        Step 2 (Server): Verifies Client's encrypted FINISHED message and returns
        Server's encrypted FINISHED message + the ready SecureSession.
        """
        if not self.session:
            raise SecurityError("Handshake out of order.")
        msg = self.session.decrypt_dict(encrypted_record)
        if msg.get("type") != "FINISHED" or msg.get("role") != "CLIENT":
            raise SecurityError("Invalid client FINISHED confirmation.")

        server_finished = self.session.encrypt_dict({"type": "FINISHED", "role": "SERVER"})
        return server_finished, self.session