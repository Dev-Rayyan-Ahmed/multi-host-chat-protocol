# tools/gui_e2e.py
"""
End-to-end GUI verification + screenshot capture.

Starts a real Horizon Relay server on a free port (temporary database), opens several
real MainWindow instances (each with its own NetworkClient / TCP connection), drives them
through the actual protocol and asserts on the resulting GUI state. Screenshots of the
live windows are written to docs/screenshots/.

    python -m tools.gui_e2e              # visible windows
    python -m tools.gui_e2e --offscreen  # headless (uses C:/Windows/Fonts on Windows)

The only artificial step: to photograph the synchronization banner (which normally lasts
a few milliseconds on localhost) the driver holds back the replayed frames for ~1 s on the
GUI side, as a slow network would. Everything else is real traffic.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

parser = argparse.ArgumentParser()
parser.add_argument("--offscreen", action="store_true")
parser.add_argument("--out", default=str(ROOT / "docs" / "screenshots"))
ARGS = parser.parse_args()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if ARGS.offscreen:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    if sys.platform == "win32":
        os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PySide6.QtCore import QSettings, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from client.state import STATUS_DELIVERED, STATUS_FAILED, STATUS_QUEUED, STATUS_SENT  # noqa: E402
from client.ui.main_window import MainWindow, build_app  # noqa: E402
from server.main import HorizonServer  # noqa: E402

OUT = Path(ARGS.out)
OUT.mkdir(parents=True, exist_ok=True)
PW = "relay-pass-2026"
RESULTS: list[tuple[str, bool, str]] = []

QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
os.environ["HORIZON_RELAY_DATA_DIR"] = tempfile.mkdtemp(prefix="hr_e2e_store_")  # never touch real history
app = build_app(sys.argv)

# --- event-loop responsiveness probe (UI must never freeze on network work) ---
_probe = {"last": time.perf_counter(), "max_gap": 0.0, "paused": False, "phase": "", "where": ""}


def _beat() -> None:
    now = time.perf_counter()
    if not _probe["paused"] and now - _probe["last"] > _probe["max_gap"]:
        _probe["max_gap"] = now - _probe["last"]
        _probe["where"] = _probe["phase"]
    _probe["last"] = now


_hb = QTimer()
_hb.setInterval(10)
_hb.timeout.connect(_beat)
_hb.start()


def pump(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def wait(cond, what: str, timeout: float = 8.0) -> None:
    end = time.time() + timeout
    while not cond():
        app.processEvents()
        time.sleep(0.005)
        if time.time() > end:
            raise TimeoutError(f"Timed out waiting for: {what}")


def phase(name: str) -> None:
    _probe["phase"] = name
    print()
    print(name)


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")


def shot(win: MainWindow, name: str, settle: float = 0.45) -> None:
    pump(settle)
    _probe["paused"] = True
    win.grab().save(str(OUT / f"{name}.png"))
    app.processEvents()
    _probe["last"] = time.perf_counter()
    _probe["paused"] = False
    print(f"  saved {name}.png")


def new_window(x: int = 40) -> MainWindow:
    _probe["paused"] = True  # widget construction is not network work
    w = MainWindow()
    w.resize(1280, 800)
    w.move(x, 40)
    w.show()
    app.processEvents()
    _probe["last"] = time.perf_counter()
    _probe["paused"] = False
    pump(0.1)
    return w


def connect(w: MainWindow, port: int) -> None:
    w.connection.host.setText("127.0.0.1")
    w.connection.port.setText(str(port))
    w.connection._submit()
    wait(lambda: w.stack.currentWidget() is w.auth, "auth screen")


def register(w: MainWindow, user: str) -> None:
    w.auth.tabs.set_index(1, emit=True)
    w.auth.username.setText(user)
    w.auth.password.setText(PW)
    w.auth.confirm.setText(PW)
    w.auth._submit()
    wait(lambda: w.auth.success_wrap.isVisible() or w.auth.username_err.isVisible(), f"register {user}")


def login(w: MainWindow, user: str) -> None:
    w.auth.tabs.set_index(0, emit=True)
    w.auth.username.setText(user)
    w.auth.password.setText(PW)
    w.auth._submit()
    wait(lambda: w.stack.currentWidget() is w.chat, f"login {user}")
    wait(lambda: w.session.can_send, f"{user} sync complete")


def send(w: MainWindow, text: str) -> None:
    w.chat.composer.input.setPlainText(text)
    w.chat.composer._submit()


def away_dividers(w: MainWindow) -> int:
    lay = w.chat.view.lay
    return sum(1 for i in range(lay.count())
               if (wd := lay.itemAt(i).widget()) is not None and getattr(wd, "tone", None) == "away")


def rows_with(w: MainWindow, text: str) -> list:
    return [r for r in w.chat.view.rows.values() if r.msg.text == text]


def main() -> int:
    db = Path(tempfile.mkdtemp()) / "e2e.db"
    server = HorizonServer(host="127.0.0.1", port=0, db_path=str(db))
    port = server.start_background()
    print(f"Server on 127.0.0.1:{port}")
    try:
        run(port)
    finally:
        server.stop()
    print(f"\nEvent loop max gap: {_probe['max_gap'] * 1000:.0f} ms (during: {_probe['where']})")
    check("GUI thread never blocked > 250 ms", _probe["max_gap"] < 0.25, f"{_probe['max_gap'] * 1000:.0f} ms")
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


def run(port: int) -> None:
    phase("1. Connection screen & failures")
    ray = new_window()
    ray.connection.host.setText("127.0.0.1")
    ray.connection.port.setText(str(port))
    shot(ray, "01_connection")

    # closed port -> refused, stepper must not mark anything complete
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()
    ray.connection.port.setText(str(dead_port))
    ray.connection._submit()
    wait(lambda: ray.connection.error.isVisible(), "connection error")
    st = ray.connection.stepper
    check("Refused connection shows error", "refused" in ray.connection.error_text.text().lower(),
          ray.connection.error_text.text())
    check("No handshake step marked done on failure", all(b.state != "done" for b in st.badges.values()))
    shot(ray, "02_connection_error")

    phase("2. Handshake progress uses real stage events")
    stages: list[str] = []
    ray.session.handshakeStage.connect(stages.append)
    ray.connection.port.setText(str(port))
    ray.connection._submit()
    wait(lambda: ray.stack.currentWidget() is ray.auth, "auth screen")
    check("Stages arrive in order tcp → identity → secure", stages == ["tcp", "identity", "secure"], str(stages))
    check("Auth screen only after handshake", ray.session.handshake_ok)
    shot(ray, "03_login")

    phase("3. Registration")
    ray.auth.tabs.set_index(1, emit=True)
    ray.auth.username.setText("ALL")
    ray.auth.password.setText("short")
    ray.auth.confirm.setText("short")
    ray.auth._submit()
    check("Reserved username rejected client-side", ray.auth.username_err.isVisible(), ray.auth.username_err.text())
    check("Short password rejected client-side", ray.auth.password_err.isVisible(), ray.auth.password_err.text())
    ray.auth.username.setText("Rayyan")
    ray.auth.password.setText(PW)
    ray.auth.confirm.setText(PW)
    shot(ray, "04_register")
    ray.auth._submit()
    wait(lambda: ray.auth.success_wrap.isVisible(), "REGISTER_OK")
    check("REGISTER_OK returns to Login tab, not logged in",
          ray.auth.tabs.index() == 0 and not ray.session.logged_in and ray.stack.currentWidget() is ray.auth)
    shot(ray, "05_register_success")
    register(ray, "rayyan")
    check("Case-insensitive duplicate username rejected", ray.auth.username_err.isVisible(),
          ray.auth.username_err.text())

    alex, sarah, dan = new_window(80), new_window(120), new_window(160)
    for w, u in ((alex, "Alex_Morgan"), (sarah, "Sarah_Chen"), (dan, "Daniel_Kim")):
        connect(w, port)
        register(w, u)

    phase("4. Offline queue + history while Rayyan is away")
    login(alex, "Alex_Morgan")
    login(sarah, "Sarah_Chen")
    login(dan, "Daniel_Kim")
    send(alex, "Morning all! Is the relay up for everyone?")
    wait(lambda: any(r.msg.status == STATUS_SENT for r in rows_with(alex, "Morning all! Is the relay up for everyone?")), "ack")
    send(sarah, "Yep, connected from the lab machine.")
    wait(lambda: rows_with(alex, "Yep, connected from the lab machine."), "sarah broadcast at alex")
    send(dan, "Same here. Handshake took a few ms.")
    wait(lambda: rows_with(alex, "Same here. Handshake took a few ms."), "dan broadcast at alex")
    check("Broadcast sender sees own message exactly once",
          len(rows_with(alex, "Morning all! Is the relay up for everyone?")) == 1)

    alex.chat.start_direct("Rayyan")
    for t in ("Hey Rayyan, are you joining the demo at 3?",
              "I pushed the new build — check the diagnostics panel when you're on."):
        send(alex, t)
        wait(lambda t=t: any(r.msg.status == STATUS_QUEUED for r in rows_with(alex, t)), "queued ack")
    check("Message to offline user shows Queued",
          all(r.msg.status == STATUS_QUEUED for r in alex.chat.view.rows.values() if r.msg.outgoing))
    shot(alex, "16_sender_queued")
    sarah.chat.start_direct("Rayyan")
    send(sarah, "Sending you the slides tonight.")
    wait(lambda: any(r.msg.status == STATUS_QUEUED for r in rows_with(sarah, "Sending you the slides tonight.")), "q")

    phase("5. Login synchronization")
    held: list[dict] = []
    gate = {"hold": False}
    orig = ray.session._dispatch

    def throttled(ev: dict) -> None:
        if gate["hold"]:
            held.append(ev)
            return
        orig(ev)
        if ev.get("type") == "SYNC_BEGIN":
            gate["hold"] = True

    ray.session._dispatch = throttled
    ray.auth.tabs.set_index(0, emit=True)
    ray.auth.username.setText("Rayyan")
    ray.auth.password.setText(PW)
    ray.auth._submit()
    wait(lambda: gate["hold"], "SYNC_BEGIN")
    check("Composer locked during sync", not ray.chat.composer._enabled)
    check("Sync banner shows counts", "3 queued" in ray.chat.banner.text.text(), ray.chat.banner.text.text())
    shot(ray, "06_synchronizing", settle=0.6)
    gate["hold"] = False
    for ev in held:
        orig(ev)
    ray.session._dispatch = orig
    wait(lambda: ray.session.can_send, "sync ack")
    check("Composer unlocked only after SYNC_COMPLETE sent", ray.chat.composer._enabled and ray.session.sync_acknowledged)
    queued_alex = [r.msg for r in alex.chat.view.rows.values() if r.msg.outgoing]
    wait(lambda: all(m.status == STATUS_DELIVERED for m in queued_alex), "DELIVERY_UPDATE at Alex")
    check("Sender's Queued messages turn Delivered after recipient's SYNC_COMPLETE",
          len(queued_alex) == 2 and all("Delivered" in r.text.text() for r in alex.chat.view.rows.values()
                                        if r.msg.outgoing))
    shot(alex, "17_sender_delivered")

    phase("6. Broadcast workspace")
    send(ray, "Just got in — everything synced. Nice work on the relay!")
    wait(lambda: any(r.msg.status == STATUS_SENT for r in rows_with(ray, "Just got in — everything synced. Nice work on the relay!")), "ack")
    send(ray, "Diagnostics show the encrypted session is healthy on my side.")
    alex.chat.open_conversation("ALL")
    send(alex, "Welcome back! Pending messages should be in your DMs.")
    wait(lambda: rows_with(ray, "Welcome back! Pending messages should be in your DMs."), "live broadcast")
    check("No duplicate rows for own broadcast (self DELIVER merged by seq)",
          len(rows_with(ray, "Just got in — everything synced. Nice work on the relay!")) == 1)
    check("Recent history divider present", any(
        type(ray.chat.view.lay.itemAt(i).widget()).__name__ == "Divider"
        and ray.chat.view.lay.itemAt(i).widget().tone == "history"
        for i in range(ray.chat.view.lay.count()) if ray.chat.view.lay.itemAt(i).widget()))
    shot(ray, "07_broadcast")

    phase("7. Private conversation")
    ray.chat.open_conversation("alex_morgan")
    away = [r for r in ray.chat.view.rows.values() if r.msg.source == "pending"]
    check("Pending messages restored under 'While you were away'", len(away) == 2)
    send(ray, "Yes, I'll be there at 3. Thanks for the heads-up!")
    wait(lambda: any(r.msg.status == STATUS_SENT for r in rows_with(ray, "Yes, I'll be there at 3. Thanks for the heads-up!")), "dm ack")
    wait(lambda: alex.chat.session.state.conversations["rayyan"].messages[-1].text.startswith("Yes, I'll"), "alex got dm")
    alex.chat.open_conversation("rayyan")
    send(alex, "Perfect, see you then 👍")
    wait(lambda: rows_with(ray, "Perfect, see you then 👍"), "dm reply")
    shot(ray, "08_private_conversation")
    check("Recipient sees exactly one 'While you were away' divider", away_dividers(ray) == 1)
    shot(ray, "18_recipient_away_divider", settle=0.1)
    ray.chat.open_conversation("ALL")
    pump(0.2)
    ray.chat.open_conversation("alex_morgan")
    check("Reopening the conversation hides the old divider", away_dividers(ray) == 0
          and len([r for r in ray.chat.view.rows.values() if r.msg.source == "pending"]) == 2)
    shot(ray, "19_recipient_reopened")

    phase("8. Offline DM, unknown user, oversized text")
    dan.session.disconnect()
    wait(lambda: not ray.session.state.is_online("Daniel_Kim"), "LEAVE")
    ray.chat.start_direct("Daniel_Kim")
    send(ray, "Saw you dropped off — ping me when you're back.")
    wait(lambda: any(r.msg.status == STATUS_QUEUED for r in ray.chat.view.rows.values()), "queued")
    check("DM to user who went offline → Queued", True)
    shot(ray, "09_queued_offline")
    ray.chat.start_direct("ghost_user")
    send(ray, "hello?")
    wait(lambda: any(r.msg.status == STATUS_FAILED for r in ray.chat.view.rows.values()), "USER_NOT_FOUND")
    check("Unknown recipient → Failed + reason", any(
        r.msg.error and "exist" in r.msg.error for r in ray.chat.view.rows.values()))
    big = "é" * 1001  # 1001 chars, 2002 UTF-8 bytes
    ray.chat.composer.input.setPlainText(big)
    check("Oversized (bytes) message: Send disabled", not ray.chat.composer.send_btn.isEnabled(),
          ray.chat.composer.counter.text())
    check("Session rejects >2000-byte text", ray.session.send_text("ALL", big) is None)
    ray.chat.composer.clear()

    phase("9. Diagnostics & dialogs")
    ray.chat.open_conversation("ALL")
    check("Unknown-user conversation pruned after leaving it", "ghost_user" not in ray.session.state.conversations)
    for t in list(ray.toasts.toasts):  # keep the screenshot uncluttered
        t.hide()
        ray.toasts.remove(t)
    ray.session.ping()
    wait(lambda: ray.session.client.last_rtt_ms is not None, "PONG")
    ray.chat.drawer.show_drawer()
    ray.chat.drawer._toggle_identity()
    pump(0.6)
    d = ray.session.diagnostics()
    max_seq = max(m.seq for c in ray.session.state.conversations.values() for m in c.messages if m.seq)
    check("Diagnostics: message seq and record counters come from different sources",
          d["last_seq"] == max_seq and d["send_records"] == ray.session.client.session.send_counter
          and d["recv_records"] == ray.session.client.session.recv_counter,
          f"seq={d['last_seq']} tx={d['send_records']} rx={d['recv_records']}")
    shot(ray, "10_diagnostics")
    ray.chat.drawer.hide_drawer()
    pump(0.3)
    ray._new_message()
    from client.ui.dialogs.modals import ConfirmModal, NewMessageModal

    nm = ray.stack.findChildren(NewMessageModal)[-1]
    nm.field.setText("Sarah_Chen")
    shot(ray, "11_new_message")
    nm._submit()
    pump(0.2)
    check("New Message opens DM conversation", ray.chat.current == "sarah_chen")
    ray._confirm_disconnect()
    shot(ray, "12_disconnect_confirm")
    ray.stack.findChildren(ConfirmModal)[-1].close_modal()
    pump(0.2)

    phase("10. Connection loss, reconnect, dedup on replay")
    ray.chat.open_conversation("ALL")
    before = len([r for r in ray.chat.view.rows.values()])
    ray.session.client.sock.shutdown(socket.SHUT_RDWR)
    wait(lambda: ray.chat._lost, "connection lost banner")
    check("Composer disabled while disconnected", not ray.chat.composer._enabled)
    shot(ray, "13_connection_lost")
    ray.chat.banner.action.emit()
    wait(lambda: ray.stack.currentWidget() is ray.auth, "reconnected to auth")
    check("Username prefilled after reconnect", ray.auth.username.text() == "Rayyan")
    ray.auth.password.setText(PW)
    ray.auth._submit()
    wait(lambda: ray.stack.currentWidget() is ray.chat and ray.session.can_send, "relogin")
    after = len([r for r in ray.chat.view.rows.values()])
    texts = [r.msg.text for r in ray.chat.view.rows.values()]
    check("History replay after reconnect creates no duplicates",
          len(texts) == len(set(texts)) and after == before, f"before={before} after={after}")

    phase("11. Window sizes")
    ray.resize(960, 620)
    shot(ray, "14_workspace_min_size")
    ray.resize(1600, 960)
    shot(ray, "15_workspace_large")
    ray.resize(1280, 800)

    for w in (ray, alex, sarah, dan):
        w.session.shutdown(silent=True)
        w.close()


if __name__ == "__main__":
    sys.exit(main())
