from pathlib import Path

import pytest

from suchie2 import chunked, lzss

ENDING = Path(__file__).resolve().parents[1] / "work/disc1/fs_end"


def _file(payloads, tails=None, pad=0):
    tails = tails or [b""] * len(payloads)
    chunks = []
    for p, t in zip(payloads, tails):
        st = lzss.compress(p)
        chunks.append(chunked.Chunk(st, t + bytes(-(len(st) + len(t)) % chunked.ALIGN)))
    return chunked.build(chunks, size=None) + bytes(pad), chunks


def test_parse_returns_each_stream_and_the_bytes_after_it():
    data, chunks = _file([b"abc" * 50, bytes(300), b"xyz"], tails=[b"", bytes.fromhex("7fff7fff") * 4, b""])
    got = chunked.parse(data)
    assert [c.stream for c in got] == [c.stream for c in chunks]
    assert got[1].tail[:16] == bytes.fromhex("7fff7fff") * 4          # data kept after the stream (e.g. colours)
    assert [lzss.decompress(c.stream) for c in got] == [b"abc" * 50, bytes(300), b"xyz"]


def test_rebuilding_the_parsed_chunks_gives_the_same_file():
    data, _ = _file([b"one" * 7, b"two" * 9, b"three"], tails=[b"", bytes(range(16)), b""], pad=16)
    assert chunked.build(chunked.parse(data), size=len(data)) == data


def test_build_writes_count_offsets_and_4_byte_aligned_chunks():
    data, chunks = _file([b"a", b"bb" * 9])
    n = int.from_bytes(data[:4], "big")
    offs = [int.from_bytes(data[4 + 4 * i:8 + 4 * i], "big") for i in range(n)]
    assert n == 2 and offs[0] == 4 + 4 * n
    assert all(o % 4 == 0 for o in offs)
    assert data[offs[1]:offs[1] + len(chunks[1].stream)] == chunks[1].stream


def test_replace_keeps_other_chunks_and_pads_the_new_stream():
    data, _ = _file([b"one" * 40, b"two" * 40, b"tri" * 40], tails=[b"", b"", bytes(range(16))])
    chunks = chunked.parse(data)
    new = chunked.replace(chunks, {1: lzss.compress(b"NEW" * 3)})
    assert new[0] == chunks[0] and new[2] == chunks[2]
    assert lzss.decompress(new[1].stream) == b"NEW" * 3 and len(new[1].stream + new[1].tail) % 4 == 0
    assert chunked.parse(chunked.build(new, size=None))[2].tail[:16] == bytes(range(16))


def test_build_pads_to_the_original_size_and_rejects_overflow():
    chunks = [chunked.Chunk(lzss.compress(bytes(range(200))), b"")]
    plain = chunked.build(chunks, size=None)
    assert chunked.build(chunks, size=len(plain) + 40) == plain + bytes(40)
    with pytest.raises(chunked.ChunkError, match="does not fit"):
        chunked.build(chunks, size=len(plain) - 1)


@pytest.mark.parametrize("patch, msg", [
    (lambda d: (99).to_bytes(4, "big") + d[4:], "table"),                              # count past the data
    (lambda d: d[:4] + (2).to_bytes(4, "big") + d[8:], "offset"),                      # offset inside the table
    (lambda d: d[:8] + (len(d) + 8).to_bytes(4, "big") + d[12:], "offset"),            # offset past the end
])
def test_parse_rejects_broken_tables(patch, msg):
    data, _ = _file([b"one" * 10, b"two" * 10])
    with pytest.raises(chunked.ChunkError, match=msg):
        chunked.parse(patch(data))


def test_parse_rejects_a_stream_running_into_the_next_chunk():
    data, chunks = _file([b"one" * 40, b"two" * 40])
    bad = bytearray(data)
    o0 = int.from_bytes(data[4:8], "big")
    bad[o0:o0 + 4] = (len(chunks[0].stream) + 64).to_bytes(4, "big")
    with pytest.raises(chunked.ChunkError, match="overlaps"):
        chunked.parse(bytes(bad))


def test_picture_header_round_trip():
    pix = bytes(range(16)) * 4                       # 16x8 4bpp
    pic = chunked.make_picture(16, 8, 0x80, pix)
    assert pic[:2] == bytes.fromhex("abcd")
    assert chunked.picture(pic) == (16, 8, 0x80, pix)


@pytest.mark.parametrize("pic, msg", [
    (bytes.fromhex("1234") + bytes(6), "magic"),
    (chunked.make_picture(16, 8, 0x80, bytes(64))[:-1], "size"),
])
def test_picture_rejects_bad_headers(pic, msg):
    with pytest.raises(chunked.ChunkError, match=msg):
        chunked.picture(pic)


@pytest.mark.parametrize("name", ["ENDY", "ENDT", "GEND"])
def test_real_ending_files_rebuild_byte_for_byte(name):
    p = ENDING / name
    if not p.exists():
        pytest.skip("needs the extracted ending files")
    data = p.read_bytes()
    assert chunked.build(chunked.parse(data), size=len(data)) == data


def test_trailing_zero_padding_is_given_up_when_an_earlier_chunk_grows():
    data, _ = _file([b"one" * 10, b"two" * 10], pad=64)
    chunks = chunked.parse(data)
    grown = chunked.replace(chunks, {0: lzss.compress(bytes(range(40)))})
    out = chunked.build(grown, size=len(data))
    assert len(out) == len(data) and [lzss.decompress(c.stream) for c in chunked.parse(out)] == [bytes(range(40)), b"two" * 10]
    with pytest.raises(chunked.ChunkError, match="does not fit"):
        chunked.build(chunked.replace(chunks, {0: lzss.compress(bytes(range(256)) * 2)}), size=len(data))


def test_replace_in_place_keeps_every_offset():
    data, _ = _file([bytes(range(256)) * 2, b"two" * 40, b"tri" * 40], tails=[b"", b"", bytes(range(16))])
    chunks = chunked.parse(data)
    new = chunked.replace_in_place(chunks, {0: lzss.compress(b"short")})
    out = chunked.build(new, size=len(data))
    assert out[:16] == data[:16]                                         # count and offsets unchanged
    assert out[int.from_bytes(data[8:12], "big"):] == data[int.from_bytes(data[8:12], "big"):]
    assert lzss.decompress(chunked.parse(out)[0].stream) == b"short"


def test_replace_in_place_rejects_a_stream_larger_than_its_slot():
    data, _ = _file([b"aa" * 10, b"two" * 40])
    chunks = chunked.parse(data)
    with pytest.raises(chunked.ChunkError, match="slot"):
        chunked.replace_in_place(chunks, {0: lzss.compress(bytes(range(256)) * 2)})


def test_zero_bytes_of_the_last_stream_are_never_cut():
    last = lzss.compress(b"x" * 3) + bytes(5)                      # a stream may end in zero payload bytes
    last = (len(last) - 4).to_bytes(4, "big") + last[4:]
    chunks = [chunked.Chunk(lzss.compress(b"one" * 10), b""), chunked.Chunk(last, bytes(-len(last) % 4))]
    chunks[0] = chunked.Chunk(chunks[0].stream, bytes(-len(chunks[0].stream) % 4))
    data = chunked.build(chunks, size=None)
    st = chunked.parse(data)[0].stream
    plus4 = (len(st) - 4 + 4).to_bytes(4, "big") + st[4:] + bytes(4)     # exactly 4 bytes longer
    grown = chunked.replace(chunked.parse(data), {0: plus4})
    with pytest.raises(chunked.ChunkError, match="does not fit"):
        chunked.build(grown, size=len(data))


@pytest.mark.parametrize("fn", [chunked.replace, chunked.replace_in_place])
def test_a_chunk_with_data_after_its_stream_cannot_be_replaced(fn):
    data, _ = _file([b"aa" * 40, b"bb" * 40], tails=[bytes(range(1, 17)), b""])
    with pytest.raises(chunked.ChunkError, match="tail"):
        fn(chunked.parse(data), {0: lzss.compress(b"a")})
