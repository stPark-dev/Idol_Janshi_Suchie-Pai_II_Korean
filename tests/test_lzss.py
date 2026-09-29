import os
import random

import pytest

from suchie2 import lzss


def test_decode_hand_vector_literal_then_overlapping_reference():
    # flag 0x01: bit0 literal 'A', bit1 reference ring 0xFEE (where 'A' went) len 3 -> "AAAA"
    stream = bytes.fromhex("00000004" "01" "41" "EEF0")
    assert lzss.decompress(stream) == b"AAAA"


def test_decode_reference_into_cleared_window_yields_zeros():
    # reference offset 0x000 len 18 reads the zero-cleared part of the window
    stream = bytes.fromhex("00000003" "00" "000F")
    assert lzss.decompress(stream) == bytes(18)


def test_decode_stops_when_count_runs_out_after_low_byte():
    # count 3 = flag + literal + lo byte only; the game drops the half reference
    stream = bytes.fromhex("00000003" "01" "41" "EE")
    assert lzss.decompress(stream) == b"A"


def test_decode_rejects_count_beyond_stream():
    with pytest.raises(lzss.LzssError):
        lzss.decompress(bytes.fromhex("00000010" "01" "41"))


def test_decode_rejects_short_header():
    with pytest.raises(lzss.LzssError):
        lzss.decompress(b"\x00\x00")


def test_stale_window_tail_changes_output_of_a_stream_that_reads_it():
    # sanity check of the stale-tail model used below
    stream = bytes.fromhex("00000003" "00" "FFF0")  # reference ring 0xFFF (len 3) before it is written
    assert lzss.decompress(stream, stale=b"\x11" * 18) != lzss.decompress(stream, stale=b"\x22" * 18)


@pytest.mark.parametrize("data", [
    b"",
    b"A",
    b"AAAA",
    bytes(5000),
    b"\xff" * 70000,
    bytes(range(256)) * 40,
    os.urandom(3000),
])
def test_round_trip(data):
    assert lzss.decompress(lzss.compress(data)) == data


def test_round_trip_structured_4bpp_like_data():
    rnd = random.Random(7)
    rows = [bytes(rnd.choice([0x00, 0x11, 0x45, 0x54, 0x23]) for _ in range(64)) for _ in range(20)]
    data = b"".join(rnd.choice(rows) for _ in range(500))
    packed = lzss.compress(data)
    assert lzss.decompress(packed) == data
    assert len(packed) < len(data) // 3


def test_compressor_never_reads_the_stale_window_tail():
    rnd = random.Random(1)
    for _ in range(30):
        data = bytes(rnd.choice(b"\x00\x01\xee\xff") for _ in range(rnd.randrange(1, 9000)))
        packed = lzss.compress(data)
        assert lzss.decompress(packed, stale=b"\xa5" * 18) == data
        assert lzss.decompress(packed, stale=b"\x5a" * 18) == data


def test_header_counts_payload_bytes():
    packed = lzss.compress(b"hello hello hello")
    assert int.from_bytes(packed[:4], "big") == len(packed) - 4


def _refs(stream):
    """(position in output, distance) of every reference in a stream."""
    out, i, end, flags, refs = 0, 4, 4 + int.from_bytes(stream[:4], "big"), 0, []
    while True:
        flags >>= 1
        if not flags & 0x100:
            if i >= end:
                return refs
            flags = stream[i] | 0xFF00
            i += 1
        if i >= end:
            return refs
        if flags & 1:
            i += 1
            out += 1
        else:
            off = stream[i] | ((stream[i + 1] & 0xF0) << 4)
            ln = (stream[i + 1] & 15) + 3
            refs.append((out, (lzss.START + out - off) % 4096))
            i += 2
            out += ln


def test_reference_at_maximum_distance_round_trips():
    rnd = random.Random(3)
    head = bytes(rnd.randrange(1, 256) for _ in range(4095))
    data = head + head[:18]
    packed = lzss.compress(data)
    assert lzss.decompress(packed) == data
    assert any(pos == 4095 and dist == 4095 for pos, dist in _refs(packed))


def test_leading_zero_run_does_not_depend_on_stale_tail():
    data = bytes(40) + b"xyz" + bytes(20)
    packed = lzss.compress(data)
    assert lzss.decompress(packed, stale=b"\x00" * 18) == data
    assert lzss.decompress(packed, stale=b"\xff" * 18) == data
