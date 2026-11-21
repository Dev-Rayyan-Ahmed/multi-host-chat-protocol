# tests/test_client_state.py
"""GUI-independent client logic: delivery reconciliation, dedup, sync, validation,
plus live NetworkClient handshake-stage events against a real server."""
import queue

from client.network import NetworkClient
from client.state import (
    BROADCAST_KEY,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_SENDING,
    STATUS_SENT,
    ChatState,
    text_problem,
    username_problem,
)
from server.main import HorizonServer


def deliver(seq, sender, to, text, source="live", ts=1_700_000_000):
    return {"type": "DELIVER", "seq": seq, "from": sender, "to": to, "text": text, "ts": ts, "source": source}


def run_tests():
    print("1. Broadcast self-DELIVER merges with the optimistic local row (no duplicate)...")
    st = ChatState()
    st.start_session("Alice")
    local = st.begin_local_send("ALL", "hi all")
    assert local.status == STATUS_SENDING
    res = st.handle_deliver(deliver(7, "Alice", "ALL", "hi all"))
    assert res.kind == "merged" and res.message is local and local.seq == 7
    acked = st.handle_ack(7, "delivered")
    assert acked is local and local.status == STATUS_SENT
    assert len(st.conversations[BROADCAST_KEY].messages) == 1
    print("   -> Passed!")

    print("2. Repeated DELIVER with the same seq is ignored...")
    res = st.handle_deliver(deliver(8, "Bob", "ALL", "yo"))
    assert res.kind == "new"
    res2 = st.handle_deliver(deliver(8, "Bob", "ALL", "yo", source="history"))
    assert res2.kind == "duplicate"
    assert len(st.conversations[BROADCAST_KEY].messages) == 2
    print("   -> Passed!")

    print("3. SEND_ACK queued / ERROR map to the oldest outstanding SEND (FIFO)...")
    a = st.begin_local_send("Carol", "are you there?")
    b = st.begin_local_send("ghost", "hello?")
    st.handle_ack(9, "queued")
    assert a.status == STATUS_QUEUED and a.seq == 9
    failed = st.handle_send_error("USER_NOT_FOUND", "User 'ghost' does not exist.")
    assert failed is b and b.status == STATUS_FAILED
    assert st.handle_send_error("AUTH_FAILED", "x") is None  # not a SEND error
    print("   -> Passed!")

    print("4. Sync: pending + history land in the right conversations, sorted by seq...")
    st2 = ChatState()
    st2.start_session("Bob")
    st2.begin_sync(2, 2)
    st2.handle_deliver(deliver(12, "Alice", "Bob", "p1", "pending"))
    st2.handle_deliver(deliver(13, "Alice", "Bob", "p2", "pending"))
    st2.handle_deliver(deliver(3, "Alice", "ALL", "h1", "history"))
    st2.handle_deliver(deliver(5, "Bob", "Alice", "h2", "history"))
    st2.end_sync()
    dm = st2.conversations["alice"]
    assert [m.seq for m in dm.messages] == [5, 12, 13]
    assert dm.messages[0].outgoing and not dm.messages[1].outgoing
    assert st2.sync_received == {"pending": 2, "history": 2}
    assert st2.last_seq == 13
    print("   -> Passed!")

    print("5. Same account re-login keeps history and dedups replay; other account clears...")
    kept = st2.start_session("bob")
    assert kept and st2.handle_deliver(deliver(5, "Bob", "Alice", "h2", "history")).kind == "duplicate"
    assert not st2.start_session("Carol") and not st2.direct_conversations()
    print("   -> Passed!")

    print("6. Disconnect fails messages still awaiting an answer...")
    st3 = ChatState()
    st3.start_session("Dan")
    m = st3.begin_local_send("ALL", "x")
    assert st3.end_session() == [m] and m.status == STATUS_FAILED
    print("   -> Passed!")

    print("7. Client-side validation mirrors the protocol (UTF-8 bytes, usernames)...")
    assert text_problem("a" * 2000) is None
    assert text_problem("é" * 1001) is not None  # 2002 bytes, only 1001 characters
    assert text_problem("   ") is not None
    assert username_problem("all") and username_problem("ab") and username_problem("bad name")
    assert username_problem("Good_Name_1") is None
    print("   -> Passed!")

    print("8. NetworkClient publishes real handshake stages and SYNC_COMPLETE confirmation...")
    server = HorizonServer(host="127.0.0.1", port=0, db_path=":memory:")
    port = server.start_background()
    try:
        c = NetworkClient()
        c.connect("127.0.0.1", port)
        seen = []
        while "_CONNECTED" not in seen:
            ev = c.incoming_queue.get(timeout=3)
            seen.append(ev["type"] if ev["type"] != "_HANDSHAKE_STAGE" else ev["stage"])
        assert seen == ["tcp", "identity", "secure", "_CONNECTED"], seen
        c.post({"type": "REGISTER", "username": "Erin", "password": "password123"})
        c.post({"type": "LOGIN", "username": "Erin", "password": "password123"})
        types = []
        while "_SYNC_COMPLETE_SENT" not in types:
            types.append(c.incoming_queue.get(timeout=3)["type"])
        assert types.index("SYNC_END") < types.index("_SYNC_COMPLETE_SENT")
        assert c.send_record_counter >= 3 and c.recv_record_counter >= 5
        c.quit()
        while True:
            ev = c.incoming_queue.get(timeout=3)
            if ev["type"] == "_DISCONNECTED":
                assert ev["user_initiated"]
                break
        try:
            extra = c.incoming_queue.get(timeout=0.3)
            assert extra["type"] != "_DISCONNECTED", "duplicate _DISCONNECTED"
        except queue.Empty:
            pass

        bad = NetworkClient(trusted_server_pub_bytes=bytes(32))  # wrong trusted key
        bad.connect("127.0.0.1", port)
        while True:
            ev = bad.incoming_queue.get(timeout=3)
            if ev["type"] == "_CONNECT_FAILED":
                assert ev["stage"] == "identity" and ev["kind"] == "identity", ev
                break
            assert ev.get("stage") != "identity", "identity must not be reported verified"
    finally:
        server.stop()
    print("   -> Passed! (stages, sync ack, record counters, single disconnect, rejected identity)")

    print("\nALL CLIENT STATE TESTS PASSED!")


def test_client_state():  # pytest entry point
    run_tests()


if __name__ == "__main__":
    run_tests()
