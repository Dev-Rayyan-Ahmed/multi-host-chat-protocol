# tests/test_milestone3.py
from server.storage import Storage
from server.services import ChatService, ClientHandle


def make_test_client():
    """Creates a ClientHandle that records all messages sent to it in a list."""
    inbox = []
    handle = ClientHandle(send_callback=lambda msg: inbox.append(msg))
    return handle, inbox


def run_tests():
    # Use ':memory:' so SQLite runs super fast in RAM for testing
    storage = Storage(":memory:")
    service = ChatService(storage)

    print("1. Testing Registration, Case-Insensitive Uniqueness & Login...")
    alice, alice_inbox = make_test_client()
    bob, bob_inbox = make_test_client()

    # Register Alice and Bob
    service.handle_message(alice, {"type": "REGISTER", "username": "Alice", "password": "password123"})
    assert alice_inbox[-1] == {"type": "REGISTER_OK", "username": "Alice"}

    # Try registering 'alice' in lowercase -> Must fail with 409 USERNAME_TAKEN
    service.handle_message(bob, {"type": "REGISTER", "username": "alice", "password": "password123"})
    assert bob_inbox[-1]["code"] == 409

    # Register Bob properly
    service.handle_message(bob, {"type": "REGISTER", "username": "Bob", "password": "password123"})
    assert bob_inbox[-1] == {"type": "REGISTER_OK", "username": "Bob"}
    print("   -> Passed! Accounts registered and case-insensitive duplicates blocked.")

    print("2. Testing Login & Simultaneous Duplicate Login Protection...")
    alice_inbox.clear()
    service.handle_message(alice, {"type": "LOGIN", "username": "alice", "password": "password123"})
    # Check exact login sequence order: LOGIN_OK, USERS, SYNC_BEGIN, SYNC_END, JOIN
    types_received = [m["type"] for m in alice_inbox]
    assert types_received == ["LOGIN_OK", "USERS", "SYNC_BEGIN", "SYNC_END", "JOIN"]

    # Complete Alice's sync
    sync_id = alice_inbox[2]["sync_id"]
    service.handle_message(alice, {"type": "SYNC_COMPLETE", "sync_id": sync_id})

    # Try logging into Alice from a second window -> Must fail with 403 ALREADY_LOGGED_IN
    alice2, alice2_inbox = make_test_client()
    service.handle_message(alice2, {"type": "LOGIN", "username": "Alice", "password": "password123"})
    assert alice2_inbox[-1]["code"] == 403
    print("   -> Passed! Login sequence verified and duplicate session blocked.")

    print("3. Testing Offline Queue Cap (30 messages max)...")
    # Bob is offline. Alice sends 30 private messages to Bob.
    for i in range(1, 31):
        alice_inbox.clear()
        service.handle_message(alice, {"type": "SEND", "to": "Bob", "text": f"Offline msg #{i}"})
        assert alice_inbox[-1]["type"] == "SEND_ACK"
        assert alice_inbox[-1]["status"] == "queued"

    # 31st message to offline Bob must fail with 429 QUEUE_FULL!
    alice_inbox.clear()
    service.handle_message(alice, {"type": "SEND", "to": "Bob", "text": "Msg #31 overflow!"})
    assert alice_inbox[-1]["code"] == 429
    assert alice_inbox[-1]["reason"] == "QUEUE_FULL"
    print("   -> Passed! Queued 30 offline messages and rejected the 31st with 429 QUEUE_FULL.")

    print("4. Testing Disconnect Before SYNC_COMPLETE & Live Message Holding...")
    bob_inbox.clear()
    service.handle_message(bob, {"type": "LOGIN", "username": "Bob", "password": "password123"})
    # Bob received 30 pending messages, 0 history, but disconnects BEFORE sending SYNC_COMPLETE!
    service.disconnect_client(bob)

    # Bob reconnects and logs in again -> All 30 pending messages must still be there!
    bob_retry, bob_retry_inbox = make_test_client()
    service.handle_message(bob_retry, {"type": "LOGIN", "username": "Bob", "password": "password123"})
    sync_begin = [m for m in bob_retry_inbox if m["type"] == "SYNC_BEGIN"][0]
    assert sync_begin["pending"] == 30

    # While Bob is STILL syncing (before SYNC_COMPLETE), Alice sends a LIVE message to Bob!
    service.handle_message(alice, {"type": "SEND", "to": "Bob", "text": "Live message during sync!"})
    # Bob should NOT have received the live message yet!
    live_msgs_before = [m for m in bob_retry_inbox if m.get("source") == "live"]
    assert len(live_msgs_before) == 0

    # Now Bob sends SYNC_COMPLETE -> The held live message must immediately pop out!
    bob_retry_inbox.clear()
    service.handle_message(bob_retry, {"type": "SYNC_COMPLETE", "sync_id": sync_begin["sync_id"]})
    assert len(bob_retry_inbox) == 1
    assert bob_retry_inbox[0]["source"] == "live"
    assert bob_retry_inbox[0]["text"] == "Live message during sync!"
    print("   -> Passed! Offline messages survived disconnect & live messages waited for SYNC_COMPLETE.")

    storage.close()
    print("\nALL MILESTONE 3 DATABASE & SERVER TESTS PASSED!")


if __name__ == "__main__":
    run_tests()