# client/network.py
import queue
import socket
import threading
import time

from common.framing import FrameReader, encode_frame
from common.secure_session import (
    ClientHandshake,
    SecureSession,
    SecurityError,
    get_default_server_identity,
)

# Handshake stages published to the GUI as {"type": "_HANDSHAKE_STAGE", "stage": ...}
STAGE_TCP = "tcp"            # TCP socket connected
STAGE_IDENTITY = "identity"  # SERVER_HELLO Ed25519 signature verified, keys derived
STAGE_SECURE = "secure"      # Server FINISHED verified -> encrypted session ready

CIPHER_SUITE = "AES-256-GCM"
KEY_EXCHANGE = "X25519 + HKDF-SHA-256"
SERVER_AUTH = "Ed25519 signature"


class NetworkClient:
    """
    Background networking worker for the Horizon Relay Client.
    Handles TCP connection, X25519/Ed25519 handshake, AES-256-GCM records,
    automatic SYNC_COMPLETE acknowledgement, seq deduplication, and RTT calculation.

    All blocking socket work happens on background threads:
      * the receive thread (connect + handshake + decrypt loop)
      * the writer thread (drains `post()` requests so GUI code never blocks on sendall)

    Events for the GUI are pushed to `incoming_queue`. Internal events start with "_".
    """

    def __init__(self, trusted_server_pub_bytes: bytes | None = None):
        if trusted_server_pub_bytes is None:
            _, trusted_server_pub_bytes = get_default_server_identity()
        self.trusted_server_pub_bytes = trusted_server_pub_bytes

        self.sock: socket.socket | None = None
        self.session: SecureSession | None = None
        self.send_lock = threading.Lock()
        self.connected = False
        self.syncing = False
        self.host: str | None = None
        self.port: int | None = None
        self.handshake_stage: str | None = None  # last completed stage
        self.connected_at: float | None = None
        self._closing = False

        # Thread-safe queue that the GUI drains on its own thread
        self.incoming_queue: queue.Queue[dict] = queue.Queue()
        # Outgoing requests posted by the GUI; drained by the writer thread
        self._outbox: queue.Queue[dict | None] = queue.Queue()
        # Spec 8.2: Store processed message sequence values and deduplicate repeated DELIVER frames
        self.seen_seqs: set[int] = set()
        # Last measured Round-Trip Time in milliseconds
        self.last_rtt_ms: float | None = None

    # ------------------------------------------------------------------ connect
    def connect(self, host: str, port: int = 5000, timeout: float = 5.0) -> None:
        """Starts a background thread to connect and complete the cryptographic handshake."""
        self.host, self.port = host, port
        threading.Thread(
            target=self._connect_and_listen_worker,
            args=(host, port, timeout),
            daemon=True,
            name="horizon-recv",
        ).start()

    def _stage(self, stage: str) -> None:
        self.handshake_stage = stage
        self.incoming_queue.put({"type": "_HANDSHAKE_STAGE", "stage": stage})

    def _connect_and_listen_worker(self, host: str, port: int, timeout: float) -> None:
        reader = FrameReader()
        reason = "Connection closed."
        failed_stage = "tcp"
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            self.sock = sock
            if self._closing:
                raise ConnectionError("Connection cancelled.")
            self._stage(STAGE_TCP)
            failed_stage = STAGE_IDENTITY

            # --- 3-STEP CRYPTOGRAPHIC HANDSHAKE ---
            hs = ClientHandshake(self.trusted_server_pub_bytes)

            # 1. Send CLIENT_HELLO
            sock.sendall(encode_frame(hs.create_client_hello()))

            # 2. Read SERVER_HELLO, verify Ed25519 signature, send encrypted CLIENT FINISHED
            s_hello_frame = self._read_single_frame(sock, reader)
            c_finished_record = hs.process_server_hello_and_create_finished(s_hello_frame)
            self._stage(STAGE_IDENTITY)
            failed_stage = STAGE_SECURE
            sock.sendall(encode_frame(c_finished_record))

            # 3. Read and verify SERVER FINISHED
            s_finished_frame = self._read_single_frame(sock, reader)
            self.session = hs.verify_server_finished(s_finished_frame)

            # Switch socket to blocking mode for continuous listening
            sock.settimeout(None)
            self.connected = True
            self.connected_at = time.time()
            self._stage(STAGE_SECURE)
            failed_stage = None
            threading.Thread(target=self._writer_loop, daemon=True, name="horizon-send").start()
            self.incoming_queue.put({"type": "_CONNECTED"})

            # --- CONTINUOUS ENCRYPTED RECEIVE LOOP ---
            while self.connected:
                # process any leftover frames already in reader buffer
                for frame in reader.feed(b""):
                    self._handle_encrypted_frame(frame)

                chunk = sock.recv(4096)
                if not chunk:
                    reason = "The server closed the connection."
                    break
                for frame in reader.feed(chunk):
                    self._handle_encrypted_frame(frame)

        except Exception as err:  # noqa: BLE001 - every failure is reported to the GUI
            if self._closing:
                reason = "Disconnected."
            else:
                reason = self._describe_error(err, failed_stage)
                if failed_stage is not None:
                    self.incoming_queue.put(
                        {
                            "type": "_CONNECT_FAILED",
                            "stage": failed_stage,
                            "kind": self._classify_error(err, failed_stage),
                            "reason": reason,
                        }
                    )
        finally:
            was_connected = self.connected or failed_stage is None
            self.connected = False
            self.syncing = False
            self.session = None
            self._outbox.put(None)  # stop writer thread
            if self.sock:
                try:
                    self.sock.close()
                except OSError:
                    pass
            # Exactly one _DISCONNECTED event per connection attempt
            self.incoming_queue.put(
                {
                    "type": "_DISCONNECTED",
                    "reason": "Disconnected." if self._closing else reason,
                    "user_initiated": self._closing,
                    "was_connected": was_connected,
                }
            )

    @staticmethod
    def _classify_error(err: Exception, stage: str | None) -> str:
        if isinstance(err, SecurityError):
            return "identity" if stage == STAGE_IDENTITY else "handshake"
        if isinstance(err, ConnectionRefusedError):
            return "refused"
        if isinstance(err, (socket.timeout, TimeoutError)):
            return "timeout"
        if isinstance(err, socket.gaierror):
            return "dns"
        return "network"

    def _describe_error(self, err: Exception, stage: str | None) -> str:
        endpoint = f"{self.host}:{self.port}"
        kind = self._classify_error(err, stage)
        if kind == "refused":
            return f"Connection refused by {endpoint}. Is the server running?"
        if kind == "timeout":
            return f"Timed out while contacting {endpoint}."
        if kind == "dns":
            return f"Could not resolve host '{self.host}'."
        if kind == "identity":
            return "Server identity could not be verified: its Ed25519 signature does not match the trusted key."
        if kind == "handshake":
            return f"Secure handshake failed: {err}"
        if isinstance(err, ConnectionError) and str(err):
            return str(err)
        if isinstance(err, OSError):
            return f"Network error: {err.strerror or err}"
        return f"{type(err).__name__}: {err}"

    def _read_single_frame(self, sock: socket.socket, reader: FrameReader) -> bytes:
        """Helper used during the initial handshake to read exactly one frame."""
        while True:
            frames = reader.feed(b"")
            if frames:
                return frames[0]
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("Server closed connection during handshake.")
            frames = reader.feed(chunk)
            if frames:
                return frames[0]

    # ------------------------------------------------------------------ receive
    def _handle_encrypted_frame(self, frame_bytes: bytes) -> None:
        """Decrypts an incoming record and handles sync/deduplication/RTT automatically."""
        assert self.session is not None
        msg = self.session.decrypt_dict(frame_bytes)
        mtype = msg.get("type")

        if mtype == "SYNC_BEGIN":
            self.syncing = True
            self.incoming_queue.put(msg)

        elif mtype == "DELIVER":
            seq = msg.get("seq")
            # Deduplicate DELIVER frames by seq (Spec 8.2)
            if seq not in self.seen_seqs:
                self.seen_seqs.add(seq)
                self.incoming_queue.put(msg)

        elif mtype == "SYNC_END":
            sync_id = msg.get("sync_id")
            self.incoming_queue.put(msg)
            # Spec 8.1: Automatically send SYNC_COMPLETE after processing all replay frames.
            # A failure here raises and tears the connection down (pending stays pending server-side).
            self.send_message({"type": "SYNC_COMPLETE", "sync_id": sync_id})
            self.syncing = False
            self.incoming_queue.put({"type": "_SYNC_COMPLETE_SENT", "sync_id": sync_id})

        elif mtype == "PONG":
            # Calculate Round-Trip Time (RTT) in milliseconds!
            sent_ts = msg.get("ts")
            if isinstance(sent_ts, (int, float)):
                rtt_ms = (time.time() - sent_ts) * 1000.0
                self.last_rtt_ms = round(rtt_ms, 2)
                self.incoming_queue.put({"type": "_RTT", "rtt_ms": self.last_rtt_ms})
            self.incoming_queue.put(msg)

        else:
            self.incoming_queue.put(msg)

    # ------------------------------------------------------------------ send
    def send_message(self, data: dict) -> None:
        """Encrypts and sends a dictionary to the server (blocking; not for the GUI thread)."""
        if not self.connected or not self.session or not self.sock:
            raise RuntimeError("Client is not connected with an active secure session.")
        # Encryption assigns the next record counter, so encrypt + send must be atomic:
        # otherwise two threads could put records on the wire out of counter order.
        with self.send_lock:
            encrypted_record = self.session.encrypt_dict(data)
            self.sock.sendall(encode_frame(encrypted_record))

    def post(self, data: dict) -> None:
        """Non-blocking send: queues `data` for the writer thread. Failures are reported
        as {"type": "_SEND_FAILED"} events on `incoming_queue`."""
        if not self.connected:
            raise RuntimeError("Client is not connected with an active secure session.")
        self._outbox.put(data)

    def _writer_loop(self) -> None:
        while True:
            data = self._outbox.get()
            if data is None:
                return
            try:
                self.send_message(data)
            except Exception as err:  # noqa: BLE001
                self.incoming_queue.put(
                    {"type": "_SEND_FAILED", "request": data.get("type"), "reason": str(err)}
                )
                if not self.connected:
                    return

    # --- Convenient Helper Methods for the GUI ---
    def register(self, username: str, password: str) -> None:
        self.send_message({"type": "REGISTER", "username": username, "password": password})

    def login(self, username: str, password: str) -> None:
        self.send_message({"type": "LOGIN", "username": username, "password": password})

    def send_chat(self, to: str, text: str) -> None:
        if self.syncing:
            raise RuntimeError("Cannot send chat messages while login synchronization is active.")
        self.send_message({"type": "SEND", "to": to, "text": text})

    def send_ping(self) -> None:
        """Sends an encrypted PING with current high-precision timestamp to measure RTT."""
        self.send_message({"type": "PING", "ts": time.time()})

    # --- Diagnostics (read-only, safe to call from any thread) ---
    @property
    def send_record_counter(self) -> int | None:
        return self.session.send_counter if self.session else None

    @property
    def recv_record_counter(self) -> int | None:
        return self.session.recv_counter if self.session else None

    def quit(self) -> None:
        self._closing = True
        if self.connected:
            try:
                self.send_message({"type": "QUIT"})
            except Exception:
                pass
        self.connected = False
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
