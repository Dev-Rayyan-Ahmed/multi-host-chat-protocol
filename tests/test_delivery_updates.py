# tests/test_delivery_updates.py
"""
DELIVERY_UPDATE + persistent "While you were away" notices, end to end.

Drives the real client.session.ChatSession (the object the GUI uses) against a live
HorizonServer over TCP + the encrypted protocol. Each client keeps its local SQLite store
in a temporary folder, so "restarting the app" = building a fresh ChatSession.

    python -m tests.test_delivery_updates
"""
import os
import shutil
import tempfile
import time

DATA_DIR = tempfile.mkdtemp(prefix="hr_client_")
os.environ["HORIZON_RELAY_DATA_DIR"] = DATA_DIR

from PySide6.QtCore import QCoreApplication  # noqa: E402

import client.session as session_mod  # noqa: E402
from client.network import NetworkClient  # noqa: E402
from client.session import ChatSession  # noqa: E402
from client.state import STATUS_DELIVERED, STATUS_QUEUED  # noqa: E402
from server.main import HorizonServer  # noqa: E402

PW = "password123"
app = QCoreApplication.instance() or QCoreApplication([])


def pump(cond, what, timeout=6.0):
    end = time.time() + timeout
    while not cond():
        app.processEvents()
        time.sleep(0.005)
        if time.time() > end:
            raise TimeoutError(f"timed out waiting for: {what}")
    for _ in range(5):  # let follow-up events land
        app.processEvents()


def settle(seconds=0.25):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


class NoAckClient(NetworkClient):
    """Receives the whole sync batch, then the connection dies before SYNC_COMPLETE."""

    def send_message(self, data):
        if data.get("type") == "SYNC_COMPLETE":
            try:
                self.sock.close()
            except OSError:
                pass
            raise ConnectionError("simulated drop before SYNC_COMPLETE")
        super().send_message(data)


def connect(port, client_cls=None):
    s = ChatSession()
    if client_cls is not None:
        session_mod.NetworkClient = client_cls
    try:
        s.connect_to("127.0.0.1", port)
    finally:
        session_mod.NetworkClient = NetworkClient
    pump(lambda: s.handshake_ok, "handshake")
    return s


def login(port, user, client_cls=None, wait_sync=True):
    s = connect(port, client_cls)
    s.login(user, PW)
    if wait_sync:
        pump(lambda: s.can_send, f"{user} login + sync")
    return s


def register(port, user):
    s = connect(port)
    ok = []
    s.registerOk.connect(ok.append)
    s.register(user, PW)
    pump(lambda: ok, f"register {user}")
    s.shutdown()


def logout(s):
    s.disconnect()
    pump(lambda: s.client is None, "disconnect")
    settle(0.15)


def dm(s, peer):
    return s.state.conversations[peer.lower()]


def outgoing_status(s, peer, text):
    return [m.status for m in dm(s, peer).messages if m.outgoing and m.text == text]


def send(s, to, text):
    msg = s.send_text(to, text)
    pump(lambda: msg.seq is not None, f"SEND_ACK for {text!r}")
    return msg


def run_tests():
    server = HorizonServer(host="127.0.0.1", port=0, db_path=os.path.join(DATA_DIR, "server.db"))
    port = server.start_background()
    st = server.storage
    try:
        for u in ("Romeysi", "Kashan", "Hooria"):
            register(port, u)

        print("1. Main scenario: queued -> Delivered, one-time divider")
        rom = login(port, "Romeysi")
        kas = login(port, "Kashan")
        logout(kas)                                                          # 2
        pump(lambda: not rom.state.is_online("Kashan"), "LEAVE")
        m1 = send(rom, "Kashan", "Helloo 2")                                 # 3
        m2 = send(rom, "Kashan", "How are you??")
        assert m1.status == m2.status == STATUS_QUEUED                       # 4
        assert st.delivery_state(m1.seq) == ("pending", 1, 0)
        kas = login(port, "Kashan")                                          # 5-7
        texts = [m.text for m in dm(kas, "Romeysi").messages]
        assert texts.count("Helloo 2") == 1 and texts.count("How are you??") == 1  # 6
        pump(lambda: m1.status == m2.status == STATUS_DELIVERED, "DELIVERY_UPDATE at sender")  # 8
        assert st.delivery_state(m1.seq)[0] == "delivered"
        away = kas.state.notice_syncs("romeysi")
        assert len(away) == 1, away                                          # 9: one notice
        kas.mark_notices_seen("romeysi", away)                               # conversation viewed
        assert kas.state.notice_syncs("romeysi") == set()                    # 10-11: reopen -> hidden
        logout(kas)
        kas = login(port, "Kashan")                                          # 12
        assert kas.state.notice_syncs("romeysi") == set()                    # 13
        seqs = [m.seq for m in dm(kas, "Romeysi").messages]
        assert seqs == sorted(seqs) and m1.seq in seqs and m2.seq in seqs    # 14
        assert len(seqs) == len(set(seqs)), "duplicate rows"
        logout(kas)
        m3 = send(rom, "Kashan", "New batch")                               # 15
        kas = login(port, "Kashan")
        assert len(kas.state.notice_syncs("romeysi")) == 1, "new offline batch -> new divider"
        print("   -> Passed! Queued -> Delivered, single divider, dismissed for good, new batch -> new divider")

        print("2. Status survives a sender restart (local store)")
        pump(lambda: m3.status == STATUS_DELIVERED, "m3 delivered")
        logout(rom)
        rom = login(port, "Romeysi")  # fresh ChatSession == app restart
        assert outgoing_status(rom, "Kashan", "Helloo 2") == [STATUS_DELIVERED]
        assert outgoing_status(rom, "Kashan", "New batch") == [STATUS_DELIVERED]
        print("   -> Passed! Delivered status restored from the local store")

        print("3. Sender offline when the recipient acknowledges")
        logout(kas)
        m4 = send(rom, "Kashan", "Sent while you are away")
        logout(rom)
        kas = login(port, "Kashan")  # acknowledges while Romeysi is offline
        assert st.delivery_state(m4.seq) == ("delivered", 1, 0), "delivered, sender not told yet"
        updates = []
        rom = connect(port)
        rom.client.incoming_queue  # noqa: B018 - events are observed through the session
        orig = rom._on_delivery_update
        rom._on_delivery_update = lambda ev: (updates.append(ev), orig(ev))
        rom.login("Romeysi", PW)
        pump(lambda: rom.can_send, "Romeysi sync")
        assert any(u["seq"] == m4.seq for u in updates), "update delivered in login sync"
        assert outgoing_status(rom, "Kashan", "Sent while you are away") == [STATUS_DELIVERED]
        assert st.delivery_state(m4.seq) == ("delivered", 1, 1), "confirmed by SYNC_COMPLETE"
        logout(rom)
        updates.clear()
        rom = connect(port)
        orig = rom._on_delivery_update
        rom._on_delivery_update = lambda ev: (updates.append(ev), orig(ev))
        rom.login("Romeysi", PW)
        pump(lambda: rom.can_send, "Romeysi sync 2")
        assert not any(u["seq"] == m4.seq for u in updates), "not re-sent once confirmed"
        print("   -> Passed! Update kept on the server, sent at next login, confirmed by SYNC_COMPLETE")

        print("4. Recipient drops before SYNC_COMPLETE; repeated DELIVERs are de-duplicated")
        kas.mark_notices_seen("romeysi", kas.state.notice_syncs("romeysi"))  # "New batch" was viewed
        logout(kas)
        m5 = send(rom, "Kashan", "Interrupted one")
        m6 = send(rom, "Kashan", "Interrupted two")
        kas = login(port, "Kashan", client_cls=NoAckClient, wait_sync=False)
        pump(lambda: kas.client is None, "simulated drop")
        settle(0.3)
        assert st.delivery_state(m5.seq)[0] == "pending", "batch must not be acknowledged"
        assert m5.status == m6.status == STATUS_QUEUED, "sender must still see Queued"
        before = kas.state.notice_syncs("romeysi")
        assert len(before) == 1
        kas.shutdown()
        kas = login(port, "Kashan")  # same messages are replayed with a new sync_id
        rows = [m for m in dm(kas, "Romeysi").messages if m.text.startswith("Interrupted")]
        assert len(rows) == 2, f"duplicates after replay: {len(rows)}"
        assert kas.state.notice_syncs("romeysi") == before, "no duplicate notice for known messages"
        pump(lambda: m5.status == m6.status == STATUS_DELIVERED, "delivered after real ack")
        print("   -> Passed! Pending preserved, replay de-duplicated by seq, single notice")

        print("5. Several senders -> notices per conversation; duplicate updates are harmless")
        kas.mark_notices_seen("romeysi", kas.state.notice_syncs("romeysi"))
        logout(kas)
        hoo = login(port, "Hooria")
        h1 = send(hoo, "Kashan", "From Hooria 1")
        send(hoo, "Kashan", "From Hooria 2")
        send(rom, "Kashan", "From Romeysi again")
        kas = login(port, "Kashan")
        assert len(kas.state.notice_syncs("romeysi")) == 1 and len(kas.state.notice_syncs("hooria")) == 1
        kas.mark_notices_seen("hooria", kas.state.notice_syncs("hooria"))
        assert kas.state.notice_syncs("hooria") == set()
        assert len(kas.state.notice_syncs("romeysi")) == 1, "only the viewed conversation is dismissed"
        pump(lambda: h1.status == STATUS_DELIVERED, "Hooria's messages delivered")
        before = [(m.seq, m.status) for m in dm(hoo, "Kashan").messages]
        hoo._on_delivery_update({"type": "DELIVERY_UPDATE", "seq": h1.seq, "to": "Kashan", "status": "delivered"})
        hoo._on_delivery_update({"type": "DELIVERY_UPDATE", "seq": 10**6, "to": "Kashan", "status": "delivered"})
        hoo._on_delivery_update({"type": "DELIVERY_UPDATE", "seq": "bad", "status": "read"})
        assert [(m.seq, m.status) for m in dm(hoo, "Kashan").messages] == before
        print("   -> Passed! Per-conversation notices; duplicate/unknown/malformed updates ignored")

        for s in (rom, kas, hoo):
            s.shutdown()
            if s.state.store:
                s.state.store.close()
        print("\nALL DELIVERY UPDATE & SYNC NOTICE TESTS PASSED!")
    finally:
        server.stop()
        shutil.rmtree(DATA_DIR, ignore_errors=True)


def test_delivery_updates():  # pytest entry point
    run_tests()


if __name__ == "__main__":
    run_tests()
