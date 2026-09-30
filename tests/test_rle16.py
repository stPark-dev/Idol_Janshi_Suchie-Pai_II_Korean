import pytest

from suchie2 import rle16, yc


def test_decode_follows_the_game_rule():
    # control word < 0: copy -n literal words; >= 0: repeat the next word n times
    stream = bytes.fromhex("fffe 1111 2222 0003 abcd ffff 0001 0000 0000")      # ends with the 0000 0000 terminator
    assert rle16.decode_texture(stream, 0, 12) == (bytes.fromhex("1111 2222 abcd abcd abcd 0001"), len(stream))


def test_encode_round_trips_and_uses_runs():
    data = bytes.fromhex("0000" * 40 + "1234 5678" + "ffff" * 5 + "0102")
    enc = rle16.encode_texture(data)
    assert rle16.decode_texture(enc, 0, len(data))[0] == data
    assert len(enc) < len(data)


def test_encode_splits_long_runs_and_literals():
    data = bytes(2 * 70000) + bytes(range(256)) * 300
    enc = rle16.encode_texture(data)
    assert rle16.decode_texture(enc, 0, len(data))[0] == data


def test_decode_rejects_overrun_and_truncation():
    with pytest.raises(rle16.Rle16Error):
        rle16.decode_texture(bytes.fromhex("0005 1111 0000 0000"), 0, 4)       # run passes the texture end
    with pytest.raises(rle16.Rle16Error, match="terminator"):
        rle16.decode_texture(bytes.fromhex("0002 1111 0001 2222"), 0, 4)       # no terminator after the texture
    with pytest.raises(rle16.Rle16Error):
        rle16.decode_texture(bytes.fromhex("fffc 1111"), 0, 8)       # literal runs past the stream


def _bundle():
    return yc.build([yc.Entry(16, 2, 0x0080, 0x10, bytes(16)),
                     yc.Entry(8, 2, 0x00A0, 0x100, bytes(range(16)))])


def test_block_is_uncompressed_header_plus_per_texture_streams():
    b = _bundle()
    block = rle16.compress(b)
    assert block[:4 + 8 * 2] == b[:4 + 8 * 2]
    out, used = rle16.decompress(block + bytes(10))
    assert out == b and used == len(block)


def test_every_encoded_texture_ends_with_the_terminator():
    for data in (bytes(8), bytes(range(8)), bytes.fromhex("0000" * 10 + "1234")):
        assert rle16.encode_texture(data).endswith(bytes(4))


def test_terminator_inside_a_texture_is_rejected():
    with pytest.raises(rle16.Rle16Error, match="terminator after"):
        rle16.decode_texture(bytes.fromhex("0001 1111 0000 0000 0001 2222 0000 0000"), 0, 4)


def _controls(stream, nbytes):
    import struct
    pos, out, seen = 0, 0, []
    while out < nbytes:
        n = struct.unpack(">h", stream[pos:pos + 2])[0]
        seen.append(n)
        pos += 2 + (2 * -n if n < 0 else 2)
        out += 2 * abs(n)
    return seen


@pytest.mark.parametrize("data", [bytes(2 * 0x7FFF), bytes(2 * 0x8000), bytes(range(256)) * 256 + bytes(2)], ids=["run7fff", "run8000", "literal"])
def test_control_words_stay_in_the_symmetric_range(data):
    enc = rle16.encode_texture(data)
    assert rle16.decode_texture(enc, 0, len(data))[0] == data
    assert all(-0x7FFF <= n <= 0x7FFF and n != 0 for n in _controls(enc, len(data)))
