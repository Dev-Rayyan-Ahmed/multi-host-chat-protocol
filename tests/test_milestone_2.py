# tests/test_milestone2.py
from cryptography.hazmat.primitives.asymmetric import ed25519
from common.secure_session import (
    ClientHandshake,
    ServerHandshake,
    SecurityError,
    get_default_server_identity,
)


def run_tests():
    server_priv, server_pub_bytes = get_default_server_identity()

    print("1. Testing full X25519 + Ed25519 Handshake & Encrypted Round-Trip...")
    client_hs = ClientHandshake(trusted_server_pub_bytes=server_pub_bytes)
    server_hs = ServerHandshake(identity_priv_key=server_priv)

    # Step A: Client -> Server (CLIENT_HELLO)
    c_hello = client_hs.create_client_hello()
    # Step B: Server -> Client (SERVER_HELLO)
    s_hello = server_hs.process_client_hello_and_create_server_hello(c_hello)
    # Step C: Client verifies signature & sends encrypted FINISHED
    c_finished = client_hs.process_server_hello_and_create_finished(s_hello)
    # Step D: Server verifies Client FINISHED & sends Server FINISHED
    s_finished, server_session = server_hs.verify_client_finished_and_create_server_finished(c_finished)
    # Step E: Client verifies Server FINISHED
    client_session = client_hs.verify_server_finished(s_finished)

    # Now send an encrypted chat message Client -> Server
    secret_chat = {"type": "SEND", "to": "ALL", "text": "Top secret message!"}
    encrypted_packet = client_session.encrypt_dict(secret_chat)
    decrypted_chat = server_session.decrypt_dict(encrypted_packet)
    assert decrypted_chat == secret_chat
    print("   -> Passed! Handshake completed and AES-256-GCM decrypted:", decrypted_chat)

    print("2. Testing Fake Server Signature (Imposter Server Attack)...")
    imposter_priv = ed25519.Ed25519PrivateKey.generate()
    imposter_server_hs = ServerHandshake(identity_priv_key=imposter_priv)
    victim_client_hs = ClientHandshake(trusted_server_pub_bytes=server_pub_bytes)

    c_hello2 = victim_client_hs.create_client_hello()
    fake_s_hello = imposter_server_hs.process_client_hello_and_create_server_hello(c_hello2)
    try:
        victim_client_hs.process_server_hello_and_create_finished(fake_s_hello)
        assert False, "Should have rejected imposter server signature!"
    except SecurityError as e:
        print(f"   -> Passed! Blocked imposter server: {e}")

    print("3. Testing Tampered Ciphertext (1-Bit Flip Attack)...")
    valid_packet = bytearray(client_session.encrypt_dict({"type": "PING", "ts": 12345}))
    # Flip a single bit in the encrypted ciphertext
    valid_packet[12] ^= 0x01
    try:
        server_session.decrypt_dict(bytes(valid_packet))
        assert False, "Should have rejected tampered ciphertext!"
    except SecurityError as e:
        print(f"   -> Passed! Detected tampered bytes: {e}")

    print("4. Testing Replay / Out-of-Order Counter Attack...")
    # Because Test 3 failed, server_session.recv_counter is still waiting for counter #2,
    # while client_session.send_counter has moved to #3! Sending another packet now skips a counter.
    skipped_counter_packet = client_session.encrypt_dict({"type": "PING", "ts": 99999})
    try:
        server_session.decrypt_dict(skipped_counter_packet)
        assert False, "Should have rejected skipped counter!"
    except SecurityError as e:
        print(f"   -> Passed! Blocked out-of-order counter: {e}")

    print("5. Testing Wrong Direction / Reflection Attack...")
    # What if an attacker takes a Server->Client packet and sends it back to the Server?
    s2c_packet = server_session.encrypt_dict({"type": "PONG", "ts": 12345})
    try:
        server_session.decrypt_dict(s2c_packet)
        assert False, "Should have rejected reflected packet!"
    except SecurityError as e:
        print(f"   -> Passed! Blocked wrong-direction reflection: {e}")

    print("\nALL MILESTONE 2 SECURITY TESTS PASSED!")


if __name__ == "__main__":
    run_tests()