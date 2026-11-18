# server/connection.py
import socket
import threading
from cryptography.hazmat.primitives.asymmetric import ed25519

from common.framing import FrameReader, FramingError, encode_frame
from common.protocol import ProtocolError
from common.secure_session import SecurityError, ServerHandshake, SecureSession
from server.services import ChatService, ClientHandle


class ClientConnection(threading.Thread):
    """
    Runs one background thread per connected TCP client on the server.
    Enforces the HANDSHAKE state before allowing any application messages.
    """
    def __init__(
        self,
        sock: socket.socket,
        addr: tuple[str, int],
        service: ChatService,
        identity_priv_key: ed25519.Ed25519PrivateKey,
    ):
        super().__init__(daemon=True)
        self.sock = sock
        self.addr = addr
        self.service = service
        self.reader = FrameReader()
        self.send_lock = threading.Lock()

        # Handshake state machine
        self.hs = ServerHandshake(identity_priv_key)
        self.hs_stage = "WAIT_CLIENT_HELLO"  # WAIT_CLIENT_HELLO -> WAIT_CLIENT_FINISHED -> ESTABLISHED
        self.secure_session: SecureSession | None = None

        # Application-layer handle used by ChatService once handshake finishes
        self.handle = ClientHandle(send_callback=self.send_encrypted_dict)

    def send_raw_frame(self, payload: bytes) -> None:
        """Sends an unencrypted length-prefixed frame (used ONLY during handshake)."""
        framed = encode_frame(payload)
        with self.send_lock:
            self.sock.sendall(framed)

    def send_encrypted_dict(self, data: dict) -> None:
        """Encrypts a dictionary with AES-256-GCM and sends it over TCP."""
        if not self.secure_session:
            raise RuntimeError("Cannot send application message before handshake completes.")
        try:
            encrypted_payload = self.secure_session.encrypt_dict(data)
            framed = encode_frame(encrypted_payload)
            with self.send_lock:
                self.sock.sendall(framed)
        except OSError:
            # Socket already closed by peer
            pass

    def run(self) -> None:
        try:
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break

                frames = self.reader.feed(chunk)
                for frame_bytes in frames:
                    should_continue = self._process_frame(frame_bytes)
                    if not should_continue:
                        return
        except (FramingError, SecurityError) as err:
            # Spec 4.6 & 10: Close immediately on oversized frame, bad format, or crypto failure
            print(f"[Server] Closing connection {self.addr} due to security/framing error: {err}")
        except OSError:
            pass
        except Exception as err:
            print(f"[Server] Unexpected error from {self.addr}: {err}")
        finally:
            self.service.disconnect_client(self.handle)
            try:
                self.sock.close()
            except OSError:
                pass

    def _process_frame(self, frame_bytes: bytes) -> bool:
        """Routes incoming frame based on whether the handshake is completed."""
        # Stage 1: Waiting for unencrypted CLIENT_HELLO
        if self.hs_stage == "WAIT_CLIENT_HELLO":
            server_hello_bytes = self.hs.process_client_hello_and_create_server_hello(frame_bytes)
            self.send_raw_frame(server_hello_bytes)
            self.hs_stage = "WAIT_CLIENT_FINISHED"
            return True

        # Stage 2: Waiting for encrypted CLIENT FINISHED confirmation
        if self.hs_stage == "WAIT_CLIENT_FINISHED":
            server_finished_record, session = (
                self.hs.verify_client_finished_and_create_server_finished(frame_bytes)
            )
            self.secure_session = session
            self.send_raw_frame(server_finished_record)
            self.hs_stage = "ESTABLISHED"
            return True

        # Stage 3: Encrypted channel established! Decrypt record and pass to ChatService
        assert self.secure_session is not None
        try:
            msg_dict = self.secure_session.decrypt_dict(frame_bytes)
        except ProtocolError as err:
            # Decryption succeeded, but inside JSON was malformed
            self.send_encrypted_dict(
                {"type": "ERROR", "code": err.code, "reason": err.reason, "message": err.message}
            )
            return True

        return self.service.handle_message(self.handle, msg_dict)