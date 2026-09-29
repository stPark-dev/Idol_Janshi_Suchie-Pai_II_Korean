""""Yc" sprite bundle (magic 0x5963) as consumed by the title screen code.

u16 magic, u16 count, count x (u16 width, u16 height, u16 attr, u16 colr), then the
textures back to back in entry order. Texture size follows the VDP1 colour mode in attr bits
5-3: 0/1 = 4bpp, 2/3/4 = 8bpp, 5 = 16bpp; modes 6/7 are rejected. The total length must be
exactly explained by the entries.
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


BITS = {0: 4, 1: 4, 2: 8, 3: 8, 4: 8, 5: 16}


def bpp(attr: int) -> int:
    mode = (attr >> 3) & 7
    if mode not in BITS:
        raise YcError(f"unsupported colour mode {mode} in attr {attr:#06x}")
    return BITS[mode]


def _size(width: int, height: int, attr: int) -> int:
    bits = bpp(attr)
    if width % 8 or width <= 0 or height <= 0:
        raise YcError(f"bad texture size {width}x{height}")
    return width * height * bits // 8


def length(raw: bytes) -> int:
    """Byte length of the bundle at the start of raw, from its entry table (ignores padding)."""
    if len(raw) < 4 or int.from_bytes(raw[:2], "big") != MAGIC:
        raise YcError("missing Yc magic")
    count = int.from_bytes(raw[2:4], "big")
    if 4 + 8 * count > len(raw):
        raise YcError("entry table truncated")
    return 4 + 8 * count + sum(
        _size(*(int.from_bytes(raw[4 + i * 8 + k:6 + i * 8 + k], "big") for k in (0, 2, 4))) for i in range(count))


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
