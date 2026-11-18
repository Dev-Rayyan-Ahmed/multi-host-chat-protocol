# tests/test_milestone4.py
import queue
from server.main import HorizonServer
from client.network import NetworkClient


def wait_for_type(client: NetworkClient, expected_type: str, timeout: float = 3.0) -> dict:
    """Waits until a message of `expected_type` arrives in the client's incoming_queue."""
    while True:
        try:
            msg = client.incoming_queue.get(timeout=timeout)
            if msg.get("type") == expected_type:
                return msg
        except queue.Empty:
            raise TimeoutError(f"Timed out waiting for message type: {expected_type}")


def run_tests():
    # Start a real TCP server on an available port (port=0 picks an open port automatically)
    server = HorizonServer(host="127.0.0.1", port=0, db_path=":memory:")
    port = server.start_background()
    print(f"1. Started live TCP Server on 127.0.0.1:{port}")

    try:
        alice = NetworkClient()
        bob = NetworkClient()

        print("2. Connecting Alice & Bob over TCP and running X25519 + Ed25519 Handshake...")
        alice.connect("127.0.0.1", port)
        bob.connect("127.0.0.1", port)

        wait_for_type(alice, "_CONNECTED")
        wait_for_type(bob, "_CONNECTED")
        print("   -> Passed! Both clients completed cryptographic handshakes over TCP.")

        print("3. Testing Encrypted Registration & Login Flow...")
        alice.register("Alice", "password123")
        wait_for_type(alice, "REGISTER_OK")

        bob.register("Bob", "password123")
        wait_for_type(bob, "REGISTER_OK")

        alice.login("Alice", "password123")
        wait_for_type(alice, "LOGIN_OK")
        wait_for_type(alice, "SYNC_END")

        bob.login("Bob", "password123")
        wait_for_type(bob, "LOGIN_OK")
        wait_for_type(bob, "SYNC_END")
        print("   -> Passed! Alice and Bob registered, logged in, and completed sync.")

        print("4. Testing PING / PONG Round-Trip Time (RTT) Measurement...")
        alice.send_ping()
        rtt_event = wait_for_type(alice, "_RTT")
        wait_for_type(alice, "PONG")
        print(f"   -> Passed! Measured live encrypted RTT: {rtt_event['rtt_ms']} ms")

        print("5. Testing Live Encrypted Private & Broadcast Chat...")
        alice.send_chat("Bob", "Hey Bob, this is encrypted over TCP!")
        ack = wait_for_type(alice, "SEND_ACK")
        assert ack["status"] == "delivered"

        delivered = wait_for_type(bob, "DELIVER")
        assert delivered["from"] == "Alice"
        assert delivered["text"] == "Hey Bob, this is encrypted over TCP!"
        print("   -> Passed! Bob received decrypted message:", delivered["text"])

        alice.quit()
        bob.quit()
        print("\nALL MILESTONE 4 LIVE TCP + ENCRYPTION + RTT TESTS PASSED!")
    finally:
        server.stop()


if __name__ == "__main__":
    run_tests()