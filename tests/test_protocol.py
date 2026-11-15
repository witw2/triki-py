from triki.protocol import CMD_START, FrameParser, RawFrame, encode_frame, start_command


def test_start_command_matches_known_bytes():
    assert CMD_START == bytes([0x20, 0x10, 0x00, 0xD0, 0x07, 0x68, 0x00, 0x01])
    assert start_command(16, 2000, 104) == CMD_START


def test_roundtrip():
    data = encode_frame(True, (1, -2, 3, -32768, 32767, 0))
    assert len(data) == 14
    assert FrameParser().feed(data) == [RawFrame(True, (1, -2, 3, -32768, 32767, 0))]


def test_fragmented_stream_reassembles():
    frames = [encode_frame(i % 2 == 0, (i, i + 1, i + 2, -i, -i - 1, -i - 2)) for i in range(20)]
    stream = b"".join(frames)
    parser = FrameParser()
    out = []
    pos = 0
    for size in [1, 5, 13, 14, 15, 2, 40, 3, 100, 1000]:  # awkward chunk sizes
        out += parser.feed(stream[pos:pos + size])
        pos += size
    assert len(out) == 20
    assert out[7].axes == (7, 8, 9, -7, -8, -9)
    assert out[7].button is False and out[8].button is True


def test_status_frames_and_resync():
    seen = []
    parser = FrameParser(on_status=seen.append)
    frame = encode_frame(False, (1, 2, 3, 4, 5, 6))
    out = parser.feed(b"\x21\x00\xaa\xbb" + b"\x99\x98" + frame)
    assert seen == [b"\x21\x00\xaa\xbb"]
    assert parser.skipped_bytes == 2
    assert out == [RawFrame(False, (1, 2, 3, 4, 5, 6))]
