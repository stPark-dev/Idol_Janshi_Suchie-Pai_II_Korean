"""16-bit word RLE used by BACKRAM.BIN (boot notice screens).

Block = uncompressed "Yc" header (u16 magic, u16 count, count x 8-byte entries) followed by one
stream per texture, back to back. Stream: signed big-endian control word n;
    n < 0  -> copy the next -n words literally
    n > 0  -> repeat the next word n times
and every texture's stream ends with the terminator 0000 0000 (n = 0 plus a zero word). The game
stops a texture at the terminator: a re-encoded block without it made the next texture decode as
a continuation (garbled boot notice, docs/initial-survey.md 3.15). The decoded size must equal the
entry's texture size exactly.
"""
import struct

from . import yc

MAX_RUN = 0x7FFF
MAX_LIT = 0x7FFF          # 0x8000 would need control word -32768, never seen in the original data
MIN_RUN = 3
TERMINATOR = bytes(4)


class Rle16Error(ValueError):
    pass


def decode_texture(stream: bytes, pos: int, nbytes: int) -> tuple[bytes, int]:
    """Decode one texture starting at pos; returns (texture, position after its terminator)."""
    out = bytearray()
    while len(out) < nbytes:
        if pos + 2 > len(stream):
            raise Rle16Error("stream ends inside a texture")
        n = struct.unpack(">h", stream[pos:pos + 2])[0]
        pos += 2
        if n == 0:
            raise Rle16Error(f"terminator after {len(out)} of {nbytes} bytes")
        if n < 0:
            chunk = stream[pos:pos - 2 * n]
            if len(chunk) != -2 * n:
                raise Rle16Error("literal run passes the end of the stream")
            out += chunk
            pos -= 2 * n
        else:
            word = stream[pos:pos + 2]
            if len(word) != 2:
                raise Rle16Error("repeat word missing at the end of the stream")
            out += word * n
            pos += 2
        if len(out) > nbytes:
            raise Rle16Error(f"run passes the texture end ({len(out)} > {nbytes})")
    if stream[pos:pos + 4] != TERMINATOR:
        raise Rle16Error(f"no 0000 0000 terminator after the texture at {pos:#x}")
    return bytes(out), pos + 4


def encode_texture(data: bytes) -> bytes:
    if len(data) % 2:
        raise Rle16Error("texture size is not a whole number of words")
    words = [data[i:i + 2] for i in range(0, len(data), 2)]
    out = bytearray()
    lit: list[bytes] = []

    def flush():
        while lit:
            part = lit[:MAX_LIT]
            del lit[:MAX_LIT]
            out.extend(struct.pack(">h", -len(part)) + b"".join(part))

    i = 0
    while i < len(words):
        j = i
        while j < len(words) and j - i < MAX_RUN and words[j] == words[i]:
            j += 1
        if j - i >= MIN_RUN:
            flush()
            out.extend(struct.pack(">h", j - i) + words[i])
            i = j
        else:
            lit.append(words[i])
            i += 1
    flush()
    return bytes(out) + TERMINATOR


def _header(raw: bytes) -> tuple[int, list[tuple[int, int, int, int]]]:
    if len(raw) < 4 or struct.unpack(">H", raw[:2])[0] != yc.MAGIC:
        raise Rle16Error("missing Yc magic")
    count = struct.unpack(">H", raw[2:4])[0]
    head = 4 + 8 * count
    if head > len(raw):
        raise Rle16Error("entry table truncated")
    return head, [struct.unpack(">HHHH", raw[4 + 8 * i:12 + 8 * i]) for i in range(count)]


def decompress(block: bytes) -> tuple[bytes, int]:
    """Decode a block into a plain Yc bundle. Returns (bundle, bytes of block consumed)."""
    head, ents = _header(block)
    out = bytearray(block[:head])
    pos = head
    for w, h, attr, _ in ents:
        tex, pos = decode_texture(block, pos, w * h * yc.bpp(attr) // 8)
        out += tex
    return bytes(out), pos


def compress(bundle: bytes) -> bytes:
    ents = yc.parse(bundle)
    head = 4 + 8 * len(ents)
    return bundle[:head] + b"".join(encode_texture(e.data) for e in ents)
