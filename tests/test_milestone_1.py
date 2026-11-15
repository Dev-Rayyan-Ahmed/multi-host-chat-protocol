# tests/test_milestone1.py
from common.framing import encode_frame, FrameReader, FramingError
from common.protocol import encode_message, decode_message, validate_username, ProtocolError

def run_tests():
    print("1. Testing basic message + frame encoding...")
    original_msg = {"type": "SEND", "to": "ALL", "text": "Hello Horizon Relay!"}
    json_bytes = encode_message(original_msg)
    framed_bytes = encode_frame(json_bytes)

    reader = FrameReader()
    extracted_frames = reader.feed(framed_bytes)
    assert len(extracted_frames) == 1
    decoded_msg = decode_message(extracted_frames[0])
    assert decoded_msg == original_msg
    print("   -> Passed! Decoded:", decoded_msg)

    print("2. Testing fragmented TCP stream (1 byte at a time)...")
    # Simulate a slow network that delivers 1 byte per recv() call
    results = []
    for single_byte in framed_bytes:
        out = reader.feed(bytes([single_byte]))
        results.extend(out)
    assert len(results) == 1
    assert decode_message(results[0]) == original_msg
    print("   -> Passed! Reassembled fragmented bytes cleanly.")

    print("3. Testing coalesced TCP stream (2 messages stuck together)...")
    msg1 = encode_frame(encode_message({"type": "PING", "ts": 100}))
    msg2 = encode_frame(encode_message({"type": "PONG", "ts": 100}))
    both_at_once = reader.feed(msg1 + msg2)
    assert len(both_at_once) == 2
    print("   -> Passed! Split 2 stuck-together frames cleanly.")

    print("4. Testing username rules...")
    validate_username("Alice_99")  # Should pass
    try:
        validate_username("all")   # Should fail ('ALL' is reserved)
        assert False, "Should have rejected 'all'"
    except ProtocolError as e:
        print(f"   -> Passed! Correctly rejected reserved username ({e.reason}).")

    print("\nALL MILESTONE 1 TESTS PASSED!")

if __name__ == "__main__":
    run_tests()