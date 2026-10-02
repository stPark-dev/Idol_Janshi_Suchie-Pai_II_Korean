"""Chunk container of the ending files (ENDY, ENDT, GEND; docs/initial-survey.md 3.20).

File = u32 big-endian count n, n u32 offsets, then the chunks. Each chunk is one game LZSS stream
(u32 compressed length + payload, see lzss.py) on a 4-byte boundary, followed by the chunk's tail:
alignment padding and, for some chunks, raw data the game keeps after the stream (16 or 256
bytes, colour values). The bytes after the last stream up to the end of the file are its tail.
A decoded chunk is often a picture: u16 0xABCD, u16 width, u16 height, u16 attr (Yc colour mode
in bits 3-5, see yc.py), then the pixels.
"""
from dataclasses import dataclass

from . import yc

ALIGN = 4
MAGIC = 0xABCD


class ChunkError(ValueError):
    pass


@dataclass(frozen=True)
class Chunk:
    stream: bytes       # LZSS stream with its u32 length header
    tail: bytes         # bytes up to the next chunk (or the end of the file)


def parse(data: bytes) -> list[Chunk]:
    """The chunks in table order; build(parse(d), len(d)) == d."""
    if len(data) < 4:
        raise ChunkError("file shorter than its chunk count")
    n = int.from_bytes(data[:4], "big")
    head = 4 + 4 * n
    if head > len(data):
        raise ChunkError(f"chunk table of {n} entries runs past the {len(data)}-byte file")
    offs = [int.from_bytes(data[4 + 4 * i:8 + 4 * i], "big") for i in range(n)]
    streams = []
    for i, o in enumerate(offs):
        if not head <= o < len(data):
            raise ChunkError(f"chunk {i}: offset {o:#x} outside {head:#x}..{len(data):#x}")
        end = 4 + o + int.from_bytes(data[o:o + 4], "big")
        nxt = offs[i + 1] if i + 1 < n else len(data)
        if nxt < o or end > nxt:
            raise ChunkError(f"chunk {i}: stream {o:#x}-{end:#x} overlaps the next chunk at {nxt:#x}")
        streams.append(Chunk(data[o:end], data[end:nxt]))
    if offs and offs[0] != head:
        raise ChunkError(f"chunk 0: offset {offs[0]:#x} does not follow the {head:#x}-byte table")
    return streams


def _padding_only(chunks: list[Chunk], streams: dict[int, bytes]) -> None:
    for i in streams:
        if len(chunks[i].tail) >= ALIGN and any(chunks[i].tail):   # beyond alignment and not zeros: data
            raise ChunkError(f"chunk {i}: its {len(chunks[i].tail)}-byte tail holds data and would be lost")


def replace(chunks: list[Chunk], streams: dict[int, bytes]) -> list[Chunk]:
    """New streams for some chunks; their tail becomes zero padding to the 4-byte boundary."""
    _padding_only(chunks, streams)
    return [Chunk(streams[i], bytes(-len(streams[i]) % ALIGN)) if i in streams else c for i, c in enumerate(chunks)]


def replace_in_place(chunks: list[Chunk], streams: dict[int, bytes]) -> list[Chunk]:
    """New streams that keep every chunk offset: each must fit the original stream + tail; the
    unused rest of the slot becomes zeros."""
    _padding_only(chunks, streams)
    out = list(chunks)
    for i, s in streams.items():
        slot = len(chunks[i].stream) + len(chunks[i].tail)
        if len(s) > slot:
            raise ChunkError(f"chunk {i}: new stream of {len(s)} bytes exceeds its {slot}-byte slot")
        out[i] = Chunk(s, bytes(slot - len(s)))
    return out


def build(chunks: list[Chunk], size: int | None) -> bytes:
    """Table + chunks back to back, zero-padded to `size` (the original file size) when given."""
    out = bytearray(4 + 4 * len(chunks))
    out[:4] = len(chunks).to_bytes(4, "big")
    for i, c in enumerate(chunks):
        if len(out) % ALIGN:
            raise ChunkError(f"chunk {i} would start at {len(out):#x}, not on a {ALIGN}-byte boundary")
        out[4 + 4 * i:8 + 4 * i] = len(out).to_bytes(4, "big")
        out += c.stream + c.tail
    if size is not None:
        last_end = len(out) - (len(chunks[-1].tail) if chunks else 0)
        if last_end <= size < len(out) and not any(out[size:]):
            del out[size:]                 # only zero padding after the last stream is given up
        if len(out) > size:
            raise ChunkError(f"rebuilt file of {len(out)} bytes does not fit the original {size}")
        out += bytes(size - len(out))
    return bytes(out)


def picture(decoded: bytes) -> tuple[int, int, int, bytes]:
    if len(decoded) < 8 or int.from_bytes(decoded[:2], "big") != MAGIC:
        raise ChunkError("missing 0xABCD picture magic")
    w, h, attr = (int.from_bytes(decoded[k:k + 2], "big") for k in (2, 4, 6))
    need = w * h * yc.bpp(attr) // 8
    if len(decoded) - 8 != need:
        raise ChunkError(f"picture {w}x{h} attr {attr:#x} needs {need} bytes, has {len(decoded) - 8} (size)")
    return w, h, attr, decoded[8:]


def make_picture(w: int, h: int, attr: int, pixels: bytes) -> bytes:
    pic = b"".join(v.to_bytes(2, "big") for v in (MAGIC, w, h, attr)) + pixels
    picture(pic)
    return pic
