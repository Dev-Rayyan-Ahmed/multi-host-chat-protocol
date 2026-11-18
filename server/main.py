# server/main.py
import argparse
import socket
import threading
from common.secure_session import get_default_server_identity
from server.connection import ClientConnection
from server.services import ChatService
from server.storage import Storage


class HorizonServer:
    """
    Central TCP Server for Horizon Relay.
    Listens on host:port and spawns a ClientConnection per client.
    """
    def __init__(self, host: str = "0.0.0.0", port: int = 5000, db_path: str = "horizon_relay.db"):
        self.host = host
        self.port = port
        self.storage = Storage(db_path)
        self.service = ChatService(self.storage)
        self.identity_priv, self.identity_pub_bytes = get_default_server_identity()

        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.running = False

    def start_background(self) -> int:
        """Starts the server listener in a background thread and returns the bound port."""
        self.server_sock.bind((self.host, self.port))
        self.server_sock.listen(50)
        self.port = self.server_sock.getsockname()[1]
        self.running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()
        return self.port

    def serve_forever(self) -> None:
        """Blocks the main thread and accepts connections until Ctrl+C."""
        bound_port = self.start_background()
        print(f"====================================================")
        print(f" Horizon Relay Server listening on {self.host}:{bound_port}")
        print(f" Server Ed25519 Public Key: {self.identity_pub_bytes.hex()}")
        print(f" Press Ctrl+C to stop.")
        print(f"====================================================")
        try:
            while self.running:
                threading.Event().wait(1.0)
        except KeyboardInterrupt:
            print("\n[Server] Shutting down...")
        finally:
            self.stop()

    def _accept_loop(self) -> None:
        while self.running:
            try:
                client_sock, client_addr = self.server_sock.accept()
                conn = ClientConnection(
                    sock=client_sock,
                    addr=client_addr,
                    service=self.service,
                    identity_priv_key=self.identity_priv,
                )
                conn.start()
            except OSError:
                break

    def stop(self) -> None:
        self.running = False
        try:
            self.server_sock.close()
        except OSError:
            pass
        self.storage.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Horizon Relay TCP Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="TCP port (default: 5000)")
    parser.add_argument("--db", default="horizon_relay.db", help="SQLite database file path")
    args = parser.parse_args()

    server = HorizonServer(host=args.host, port=args.port, db_path=args.db)
    server.serve_forever()