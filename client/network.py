# client/network.py
import queue
import socket
import threading
import time
from common.framing import FrameReader, encode_frame
from common.secure_session import (
    ClientHandshake,
    SecureSession,
    get_default_server_identity,
)


class NetworkClient:
    """
    Background networking worker for the Horizon Relay Client.
    Handles TCP connection, X25519/Ed25519 handshake, AES-256-GCM records,
    automatic SYNC_COMPLETE acknowledgement, seq deduplication, and RTT calculation.
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

        # Thread-safe queue that the GUI reads from every 50ms
        self.incoming_queue: queue.Queue[dict] = queue.Queue()
        # Spec 8.2: Store processed message sequence values and deduplicate repeated DELIVER frames
        self.seen_seqs: set[int] = set()
        # Last measured Round-Trip Time in milliseconds
        self.last_rtt_ms: float | None = None

    def connect(self, host: str, port: int = 5000, timeout: float = 5.0) -> None:
        """Starts a background thread to connect and complete the cryptographic handshake."""
        threading.Thread(
            target=self._connect_and_listen_worker,
            args=(host, port, timeout),
            daemon=True,
        ).start()

    def _connect_and_listen_worker(self, host: str, port: int, timeout: float) -> None:
        reader = FrameReader()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((host, port))
            self.sock = sock

            # --- 3-STEP CRYPTOGRAPHIC HANDSHAKE ---
            hs = ClientHandshake(self.trusted_server_pub_bytes)

            # 1. Send CLIENT_HELLO
            sock.sendall(encode_frame(hs.create_client_hello()))

            # 2. Read SERVER_HELLO, verify Ed25519 signature, send encrypted CLIENT FINISHED
            s_hello_frame = self._read_single_frame(sock, reader)
            c_finished_record = hs.process_server_hello_and_create_finished(s_hello_frame)
            sock.sendall(encode_frame(c_finished_record))

            # 3. Read and verify SERVER FINISHED
            s_finished_frame = self._read_single_frame(sock, reader)
            self.session = hs.verify_server_finished(s_finished_frame)

            # Switch socket to blocking mode for continuous listening
            sock.settimeout(None)
            self.connected = True
            self.incoming_queue.put({"type": "_CONNECTED"})

            # --- CONTINUOUS ENCRYPTED RECEIVE LOOP ---
            while self.connected:
                # process any leftover frames already in reader buffer
                while True:
                    frames = reader.feed(b"")
                    if not frames:
                        break
                    for frame in frames:
                        self._handle_encrypted_frame(frame)

                chunk = sock.recv(4096)
                if not chunk:
                    break
                for frame in reader.feed(chunk):
                    self._handle_encrypted_frame(frame)

        except Exception as err:
            self.incoming_queue.put({"type": "_DISCONNECTED", "reason": str(err)})
        finally:
            self.connected = False
            self.session = None
            if self.sock:
                try:
                    self.sock.close()
                except OSError:
                    pass
            self.incoming_queue.put({"type": "_DISCONNECTED", "reason": "Connection closed."})

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
            # Spec 8.1: Automatically send SYNC_COMPLETE after processing all replay frames
            self.send_message({"type": "SYNC_COMPLETE", "sync_id": sync_id})
            self.syncing = False
            self.incoming_queue.put(msg)

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

    def send_message(self, data: dict) -> None:
        """Encrypts and sends a dictionary to the server."""
        if not self.connected or not self.session or not self.sock:
            raise RuntimeError("Client is not connected with an active secure session.")
        encrypted_record = self.session.encrypt_dict(data)
        framed = encode_frame(encrypted_record)
        with self.send_lock:
            self.sock.sendall(framed)

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

    def quit(self) -> None:
        if self.connected:
            try:
                self.send_message({"type": "QUIT"})
            except Exception:
                pass
            self.connected = False
            if self.sock:
                try:
                    self.sock.close()
                except OSError:
                    pass