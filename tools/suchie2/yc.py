""""Yc" sprite bundle (magic 0x5963) as consumed by the title screen code.

u16 magic, u16 count, count x (u16 width, u16 height, u16 attr, u16 colr), then the
4bpp textures back to back in entry order. Only 4bpp colour modes (attr bits 5-3 = 0/1)
are supported; the total length must be exactly explained by the entries.
"""
from dataclasses import dataclass

MAGIC = 0x5963


class YcError(ValueError):
    pass


@dataclass(frozen=True)
class Entry:
    width: int
    height: int
    attr: int
    colr: int
    data: bytes


def _size(width: int, height: int, attr: int) -> int:
    if (attr >> 3) & 7 not in (0, 1):
        raise YcError(f"unsupported colour mode in attr {attr:#06x}")
    if width % 8 or width <= 0 or height <= 0:
        raise YcError(f"bad texture size {width}x{height}")
    return width * height // 2


def parse(raw: bytes) -> list[Entry]:
    if len(raw) < 4 or int.from_bytes(raw[:2], "big") != MAGIC:
        raise YcError("missing Yc magic")
    count = int.from_bytes(raw[2:4], "big")
    pos = 4 + 8 * count
    if pos > len(raw):
        raise YcError("entry table truncated")
    entries = []
    for i in range(count):
        w, h, a, c = (int.from_bytes(raw[4 + i * 8 + k:6 + i * 8 + k], "big") for k in (0, 2, 4, 6))
        n = _size(w, h, a)
        if pos + n > len(raw):
            raise YcError(f"entry {i} data truncated")
        entries.append(Entry(w, h, a, c, bytes(raw[pos:pos + n])))
        pos += n
    if pos != len(raw):
        raise YcError(f"{len(raw) - pos} trailing bytes not explained by entries")
    return entries


def build(entries: list[Entry]) -> bytes:
    out = bytearray(MAGIC.to_bytes(2, "big") + len(entries).to_bytes(2, "big"))
    for e in entries:
        if len(e.data) != _size(e.width, e.height, e.attr):
            raise YcError(f"data size {len(e.data)} does not match {e.width}x{e.height}")
        for v in (e.width, e.height, e.attr, e.colr):
            out += v.to_bytes(2, "big")
    for e in entries:
        out += e.data
    return bytes(out)
