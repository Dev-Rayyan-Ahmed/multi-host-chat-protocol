<div align="center">

# 🌌 HORIZON RELAY
### End-to-End Encrypted Multi-Host TCP Chat Protocol & Desktop Client

[![Python](https://img.shields.io/badge/Python-3.11+-7C3AED?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Security](https://img.shields.io/badge/Crypto-X25519%20%7C%20Ed25519%20%7C%20AES--256--GCM-10B981?style=for-the-badge)]()
[![Transport](https://img.shields.io/badge/Transport-Custom%20Framed%20TCP-A855F7?style=for-the-badge)]()
[![Database](https://img.shields.io/badge/Persistence-SQLite%20%2B%20Argon2id-3B82F6?style=for-the-badge)]()

*A custom application-layer chat protocol built from scratch over raw TCP sockets—featuring authenticated elliptic-curve key exchange, replay-protected AES-256-GCM records, transactional offline synchronization, and a dark violet desktop interface.*

</div>

---

## ✨ Key Features

- **Custom Length-Prefixed Framing:** Solves TCP stream fragmentation and packet coalescing using a 4-byte unsigned big-endian length header (`[4B Length][Payload]`) with strict `16 KB` payload bounds.

- **Custom Cryptographic Handshake (No TLS):**
  - **X25519** ephemeral Diffie-Hellman key agreement per connection for forward secrecy.
  - **Ed25519** digital signatures over the full handshake transcript to prevent Man-in-the-Middle (MitM) impersonation.
  - **HKDF-SHA-256** key derivation producing independent Client-to-Server (`C2S`) and Server-to-Client (`S2C`) keys and nonce prefixes.

- **Tamper & Replay-Proof Records:** Every post-handshake message is encrypted with **AES-256-GCM** bound to Associated Authenticated Data (AAD) and a strict monotonic **8-byte sequence counter** to block bit-flipping, packet reflection, and replay attacks.

- **Reliable Offline Store-and-Forward Sync:** Queues up to 30 private messages per offline recipient in SQLite and uses a two-phase batch acknowledgement protocol (`SYNC_BEGIN` → `DELIVER` → `SYNC_END` → `SYNC_COMPLETE`) with sequence deduplication so messages are never lost on sudden disconnects.

- **Zero-Plaintext Credential Storage:** User passwords are salted and hashed with **Argon2id**; usernames are validated and enforced case-insensitively.

- **Modern Purple-Themed Desktop GUI:** Non-blocking multi-threaded client featuring live presence updates, broadcast (`#ALL`) and private DMs, offline delivery receipts, and real-time `PING`/`PONG` Round-Trip Time (RTT) telemetry.

---

## 📦 Architecture Overview

The codebase is cleanly separated into **4 core modules**:

- **`common/`** — Shared protocol specifications, UTF-8 JSON validation, 4-byte TCP framing engine, and the X25519/Ed25519/AES-256-GCM cryptographic session layer.

- **`server/`** — Multi-threaded TCP listener (`0.0.0.0:5000`), per-client handshake state machine, SQLite persistence layer, message router, and synchronization coordinator.

- **`client/`** — Background socket worker thread (preventing UI freezes), automatic sync acknowledgement/deduplication engine, RTT calculator, and the desktop GUI.

- **`tests/`** — Automated test suites verifying stream fragmentation, cryptographic tamper/replay rejection, database queue caps, and live end-to-end TCP flows.

---

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install cryptography argon2-cffi PySide6
```

### 2. Start the Central Server

Run on the host machine (binds to all LAN interfaces on port `5000` by default):

```bash
python -m server.main --host 0.0.0.0 --port 5000
```

> **LAN Deployment:** Check the server's local IPv4 address (`ipconfig` on Windows or `ip a` on Linux/macOS) and enter that IP in the client connection screen.

### 3. Launch the Desktop Client

Run on the same machine (`127.0.0.1`) or any laptop on the same local network:

```bash
python -m client.main
```

### 4. Run Automated Verification Suites

```bash
python -m tests.test_milestone1   # Framing & JSON protocol validation
python -m tests.test_milestone2   # Handshake, MitM, bit-flip & replay security tests
python -m tests.test_milestone3   # SQLite storage, 30-msg queue cap & sync crash recovery
python -m tests.test_milestone4   # Live multi-client TCP sockets & RTT measurement
```

---

## 🔍 Wireshark Traffic Inspection

To inspect the protocol live on the wire:

1. Open **Wireshark** and capture on your **Wi-Fi** interface (or **Loopback** adapter for localhost).

2. Apply the display filter:

   ```text
   tcp.port == 5000
   ```

3. Observe the initial `CLIENT_HELLO` / `SERVER_HELLO` key exchange followed by encrypted records (`[4B length][8B counter][ciphertext + 16B GCM tag]`) and TCP/application RTT metrics.
