"""Voice subtitles: executable stub, SUB.BIN + group files, and new files in the ISO root.

The executable "0" (loaded at 0x06004000) gets, at the same size:
  - the vblank-wait function at 0x060045EC rewritten in 40 bytes instead of 128 (same effect:
    wait for VBLANK, set TVMD.DISP in the register and in its RAM copy);
  - in the 88 freed bytes, a stub that every call of the file loader 0x06006874 now goes
    through: on the first call it reads SUB.BIN to BASE through the loader, purges the cache
    and jumps to BASE (SUB.BIN's load hook, which tail-calls the loader); afterwards it
    jumps to BASE directly; if SUB.BIN did not arrive it falls back to the loader;
  - the 66 literals holding 0x06006874 point at the stub.
SUB.BIN installs its other hooks in RAM (tools/subtitle/sub.c).

New files go into the empty sectors after the last file of Track 1; the root directory is
rewritten in ISO 9660 name order and must keep its 23 sectors.
"""
import hashlib
import re
import struct
from dataclasses import dataclass

from . import cdsector, lzss

EXE_NAME = "0"
EXE_LBA, EXE_SIZE = 43, 708284
EXE_BASE = 0x06004000
LOADER = 0x06006874
FUNC, FUNC_END = 0x060045EC, 0x0600466C
ORIG_FUNC_SHA1 = "7e315f8624d3acf5f124188a9eef0cddbd74d948"   # original 0x060045EC..0x0600466C
STUB = 0x06004614
BASE = 0x060F6800
MAGIC = 0x4B485355          # "KHSU"
CODE_MAX = 0x1400           # SUB.BIN code; the index follows at BASE + CODE_MAX
SUB_MAX = 0x2800            # code + index, read in whole sectors up to the group data area
GROUP_MAX = 0x5800          # one group of SUBDAT.BIN at 0x060F9000..0x060FE800
INDEX_MAGIC = 0x4B484958    # "KHIX"
GROUP_MAGIC = 0x4B484732    # "KHG2"

# literals of the executable that hold the file loader 0x06006874 (all of them)
LOADER_LITS = [
    0x6005b0c, 0x600a840, 0x601ff70, 0x602194c, 0x6030294, 0x6031144, 0x6031224, 0x6031358, 0x60322cc,
    0x60327c8, 0x6032f1c, 0x6034410, 0x6034cd0, 0x6034fc0, 0x6035d08, 0x6035f74, 0x6036ff0, 0x6037990,
    0x603869c, 0x6039544, 0x603adbc, 0x603cbfc, 0x603cccc, 0x603eca4, 0x603ed94, 0x603f548, 0x604187c,
    0x6042b18, 0x6043518, 0x60435e8, 0x6050a14, 0x6050b1c, 0x6050c3c, 0x605162c, 0x60517c4, 0x6051dd4,
    0x6052810, 0x6052994, 0x6054cbc, 0x6054da4, 0x6054ea0, 0x6054f90, 0x6055060, 0x6055138, 0x60551fc,
    0x60552c4, 0x60554bc, 0x6055584, 0x605567c, 0x6055760, 0x6055818, 0x60558c4, 0x6070ee0, 0x60719f0,
    0x6071ed0, 0x607321c, 0x60741e0, 0x6075100, 0x60758d0, 0x60767e0, 0x6077300, 0x607eedc, 0x60824dc,
    0x6082934, 0x6083d9c, 0x6084d40,
]


class SubtitleError(RuntimeError):
    pass


# ---- tiny SH-2 encoder for the stub -------------------------------------------------------
def _movl_pc(pc: int, target: int, rn: int) -> int:
    disp = (target - ((pc & ~3) + 4)) // 4
    if target & 3 or not 0 <= disp <= 255:
        raise SubtitleError(f"mov.l literal out of reach at {pc:#x}")
    return 0xD000 | rn << 8 | disp


def _movw_pc(pc: int, target: int, rn: int) -> int:
    disp = (target - (pc + 4)) // 2
    if target & 1 or not 0 <= disp <= 255:
        raise SubtitleError(f"mov.w literal out of reach at {pc:#x}")
    return 0x9000 | rn << 8 | disp


def _mova(pc: int, target: int) -> int:
    disp = (target - ((pc & ~3) + 4)) // 4
    if target & 3 or not 0 <= disp <= 255:
        raise SubtitleError(f"mova out of reach at {pc:#x}")
    return 0xC700 | disp


def _bcc(op: int, pc: int, target: int) -> int:
    disp = (target - (pc + 4)) // 2
    if not -128 <= disp <= 127:
        raise SubtitleError(f"branch out of reach at {pc:#x}")
    return op << 8 | (disp & 0xFF)


def vblank_wait_and_stub() -> bytes:
    """New bytes for 0x060045EC..0x0600466C: the short vblank-wait function, then the stub."""
    a = FUNC
    lit8000, tvstat, shadow = a + 0x1C, a + 0x20, a + 0x24
    func = [
        _movl_pc(a + 0x00, tvstat, 1),      # r1 = TVSTAT
        0x6011,                             # 1: mov.w @r1,r0
        0xC804,                             # tst #4,r0          (VBLANK)
        _bcc(0x89, a + 0x06, a + 0x02),     # bt 1b
        0x71FC,                             # add #-4,r1         (TVMD)
        _movw_pc(a + 0x0A, lit8000, 2),     # r2 = 0x8000 (DISP)
        0x6011, 0x202B, 0x2101,             # TVMD |= DISP
        _movl_pc(a + 0x12, shadow, 1),      # r1 = RAM copy of TVMD
        0x6011, 0x202B,                     # r0 = copy | DISP
        0x000B, 0x2101,                     # rts; store (delay slot)
        0x8000, 0x0009,
    ]
    out = b"".join(struct.pack(">H", w) for w in func) + struct.pack(">II", 0x25F80004, 0x060B4E30)
    if FUNC + len(out) != STUB:
        raise SubtitleError("vblank-wait rewrite does not end at the stub")
    s = STUB
    ccr, base, magic, load, name = s + 0x3E, s + 0x40, s + 0x44, s + 0x48, s + 0x4C
    stub = [
        _movl_pc(s + 0x00, base, 1),        # r1 = BASE
        0x5014,                             # r0 = @(16,r1)       magic
        _movl_pc(s + 0x04, magic, 2),
        0x3020,                             # cmp/eq r2,r0
        _bcc(0x89, s + 0x08, s + 0x34),     # bt loaded
        0x4F22, 0x2F46, 0x2F56,             # push pr, r4, r5
        _mova(s + 0x10, name),              # r0 = "SUB.BIN"
        0x6403, 0x6513,                     # r4 = name, r5 = BASE
        _movl_pc(s + 0x16, load, 0),
        0x400B, 0x0009,                     # loader("SUB.BIN", BASE)
        0x65F6, 0x64F6, 0x4F26,             # pop r5, r4, pr
        _movw_pc(s + 0x22, ccr, 1),         # r1 = 0xFFFFFE92 (CCR)
        0x6010, 0xCB10, 0x2100,             # CCR |= CP: purge the cache
        _movl_pc(s + 0x2A, base, 1),
        0x5014,
        _movl_pc(s + 0x2E, magic, 2),
        0x3020,
        _bcc(0x8B, s + 0x32, s + 0x38),     # bf fail
        0x412B, 0x0009,                     # loaded: jmp @r1 (BASE)
        _movl_pc(s + 0x38, load, 0),        # fail: the loader itself
        0x402B, 0x0009,
        0xFE92,
    ]
    out += b"".join(struct.pack(">H", w) for w in stub)
    out += struct.pack(">III", BASE, MAGIC, LOADER) + b"SUB.BIN\0"
    if FUNC + len(out) > FUNC_END:
        raise SubtitleError("stub overruns the rewritten function")
    return out + bytes(FUNC_END - FUNC - len(out))


def exe_changes(exe: bytes) -> list[tuple[int, bytes]]:
    """(file offset, new bytes) for the executable; checks the original bytes it replaces."""
    if len(exe) != EXE_SIZE:
        raise SubtitleError(f"executable size {len(exe)} != {EXE_SIZE}")
    lo, hi = FUNC - EXE_BASE, FUNC_END - EXE_BASE
    if hashlib.sha1(exe[lo:hi]).hexdigest() != ORIG_FUNC_SHA1:
        raise SubtitleError("vblank-wait function bytes differ from the supported executable")
    changes = [(lo, vblank_wait_and_stub())]
    for a in LOADER_LITS:
        o = a - EXE_BASE
        if exe[o:o + 4] != struct.pack(">I", LOADER):
            raise SubtitleError(f"literal at {a:#x} does not hold the file loader")
        changes.append((o, struct.pack(">I", STUB)))
    found = [EXE_BASE + o for o in range(0, len(exe) - 3, 2) if exe[o:o + 4] == struct.pack(">I", LOADER)]
    if sorted(found) != sorted(LOADER_LITS):
        raise SubtitleError("file loader literals differ from the known list")
    return changes



# ---- ISO 9660 root directory ---------------------------------------------------------------
@dataclass
class DirRecord:
    raw: bytes

    @property
    def name(self) -> bytes:
        return self.raw[33:33 + self.raw[32]]

    @property
    def lba(self) -> int:
        return struct.unpack("<I", self.raw[2:6])[0]

    @property
    def size(self) -> int:
        return struct.unpack("<I", self.raw[10:14])[0]


def parse_dir(sectors: list[bytes]) -> list[DirRecord]:
    recs = []
    for s in sectors:
        p = 0
        while p < len(s) and s[p]:
            recs.append(DirRecord(s[p:p + s[p]]))
            p += s[p]
    return recs


def new_record(template: DirRecord, name: str, lba: int, size: int) -> DirRecord:
    ident = (name + ";1").encode("ascii")
    n = 33 + len(ident)
    n += n & 1
    r = bytearray(n)
    r[0] = n
    r[2:10] = struct.pack("<I", lba) + struct.pack(">I", lba)
    r[10:18] = struct.pack("<I", size) + struct.pack(">I", size)
    r[18:25] = template.raw[18:25]          # recording date and time
    r[25] = 0                               # file flags
    r[28:32] = template.raw[28:32]          # volume sequence number
    r[32] = len(ident)
    r[33:33 + len(ident)] = ident
    return DirRecord(bytes(r))


def _sort_key(rec: DirRecord):
    nm = rec.name
    if nm in (b"\x00", b"\x01"):
        return (0, nm, b"")
    base = nm.split(b";")[0]
    stem, _, ext = base.partition(b".")
    return (1, stem, ext)


def pack_dir(recs: list[DirRecord], nsectors: int) -> list[bytes]:
    out, cur = [], bytearray()
    for r in recs:
        if len(cur) + len(r.raw) >= cdsector.USER:     # the mastering tool never fills a sector exactly
            out.append(bytes(cur) + bytes(cdsector.USER - len(cur)))
            cur = bytearray()
        cur += r.raw
    out.append(bytes(cur) + bytes(cdsector.USER - len(cur)))
    if len(out) > nsectors:
        raise SubtitleError(f"root directory needs {len(out)} sectors, has {nsectors}")
    return out + [bytes(cdsector.USER)] * (nsectors - len(out))


def with_files(recs: list[DirRecord], new: list[tuple[str, int, int]]) -> list[DirRecord]:
    """Records in ISO name order with the new files added (names must be new)."""
    have = {r.name.split(b";")[0].decode("latin1") for r in recs}
    template = next(r for r in recs if r.name not in (b"\x00", b"\x01"))
    extra = []
    for name, lba, size in new:
        if name in have:
            raise SubtitleError(f"{name} already exists on the disc")
        extra.append(new_record(template, name, lba, size))
    dots = [r for r in recs if r.name in (b"\x00", b"\x01")]
    files = sorted([r for r in recs if r.name not in (b"\x00", b"\x01")] + extra, key=_sort_key)
    return dots + files


# ---- disc layout of the new files ---------------------------------------------------------
DIR_LBA, DIR_SECTORS = 20, 23
FREE_LBA, FREE_END = 270643, 270943     # empty Mode 1 sectors after GEND, up to the end of Track 1
SUB_NAME, DAT_NAME = "SUB.BIN", "SUBDAT.BIN"


def _sectors(data: bytes) -> list[bytes]:
    data = data + bytes(-len(data) % cdsector.USER)
    return [data[i:i + cdsector.USER] for i in range(0, len(data), cdsector.USER)]


def disc_sectors(user, sub_bin: bytes, subdat: bytes) -> tuple[list[tuple[int, bytes]], dict]:
    """User-data sectors to write: the root directory and the two new files.

    `user(lba)` reads 2048 bytes of the source.  The executable's byte changes are separate
    (exe_changes) because they go through the file-level writer."""
    if len(sub_bin) > SUB_MAX:
        raise SubtitleError(f"SUB.BIN is {len(sub_bin)} bytes, room for {SUB_MAX}")
    if struct.unpack(">I", sub_bin[0x10:0x14])[0] != MAGIC:
        raise SubtitleError("SUB.BIN does not carry the magic at +0x10")
    sub_lba = FREE_LBA
    dat_lba = sub_lba + len(_sectors(sub_bin))
    end = dat_lba + len(_sectors(subdat))
    if end > FREE_END:
        raise SubtitleError(f"subtitle files need up to LBA {end}, free space ends at {FREE_END}")
    for lba in range(FREE_LBA, end):
        if any(user(lba)):
            raise SubtitleError(f"LBA {lba} after the last file is not empty")
    old = [user(DIR_LBA + i) for i in range(DIR_SECTORS)]
    recs = parse_dir(old)
    if pack_dir(with_files(recs, []), DIR_SECTORS) != old:
        raise SubtitleError("root directory layout differs from the supported source")
    last = max((r.lba + (r.size + cdsector.USER - 1) // cdsector.USER for r in recs
                if r.name not in (b"\x00", b"\x01") and r.lba < FREE_END), default=0)
    if last != FREE_LBA:
        raise SubtitleError(f"last file ends at LBA {last}, expected {FREE_LBA}")
    new_dir = pack_dir(with_files(recs, [(SUB_NAME, sub_lba, len(sub_bin)), (DAT_NAME, dat_lba, len(subdat))]),
                       DIR_SECTORS)
    out = [(DIR_LBA + i, s) for i, s in enumerate(new_dir) if s != old[i]]
    out += [(sub_lba + i, s) for i, s in enumerate(_sectors(sub_bin))]
    out += [(dat_lba + i, s) for i, s in enumerate(_sectors(subdat))]
    info = {"sub_bin": {"lba": sub_lba, "bytes": len(sub_bin)}, "subdat": {"lba": dat_lba, "bytes": len(subdat)}}
    return out, info


def empty_index() -> bytes:
    return struct.pack(">IHH", INDEX_MAGIC, 0, 0)


def sub_bin(code: bytes, index: bytes) -> bytes:
    if len(code) > CODE_MAX:
        raise SubtitleError(f"subtitle code is {len(code)} bytes, room for {CODE_MAX}")
    return code + bytes(CODE_MAX - len(code)) + index


# ---- subtitle data: glyphs, cues, groups, index ---------------------------------------------
FPS = 59.826                # vblanks per second in the 320-dot modes (measured on Steam Hearts, same clock)
LEAD = 0.1                  # a line appears this much before its voice segment
HOLD = 0.6                  # and stays this much after it, unless the next line starts earlier
MIN_SHOW = 1.0
SCREEN_W, LINE_MAX = 320, 304
GLYPH_ROWS = 16
FONT_SIZE = 14
ALLOWED = re.compile(r"[가-힣 A-Za-z0-9.,!?~…·'\"()\-:;%/&+=]*")   # what the subtitles may contain


def _font(path: str, size: int):
    from PIL import ImageFont

    from .label import resolve_font
    return ImageFont.truetype(resolve_font(path), size)


def render_glyph(ch: str, font) -> tuple[int, bytes]:
    """One character as 2bpp rows (0 clear, 1 black outline, 2 grey edge, 3 white), 16 rows."""
    from PIL import Image, ImageDraw, ImageFilter
    if ch == " ":
        w = max(3, round(font.getlength(" ")))
        return w, bytes(((w + 3) // 4) * GLYPH_ROWS)
    adv = max(1, round(font.getlength(ch)))
    W = adv + 8
    im = Image.new("L", (W, GLYPH_ROWS), 0)
    asc, _ = font.getmetrics()
    top = (GLYPH_ROWS - FONT_SIZE) // 2 - (asc - FONT_SIZE) - 1
    ImageDraw.Draw(im).text((4, top), ch, font=font, fill=255)
    fill = im.point(lambda v: 3 if v >= 150 else (2 if v >= 70 else 0))
    edge = im.point(lambda v: 255 if v >= 70 else 0).filter(ImageFilter.MaxFilter(3))
    px, ex = fill.load(), edge.load()
    cols = [x for x in range(W) if any(ex[x, y] for y in range(GLYPH_ROWS))]
    if not cols:
        raise SubtitleError(f"glyph {ch!r} draws nothing")
    x0, x1 = min(cols), max(cols) + 1
    w = x1 - x0
    pitch = (w + 3) // 4
    out = bytearray(pitch * GLYPH_ROWS)
    for y in range(GLYPH_ROWS):
        for x in range(x0, x1):
            v = px[x, y] or (1 if ex[x, y] else 0)
            if v:
                i = x - x0
                out[y * pitch + i // 4] |= v << (6 - 2 * (i % 4))
    return w, bytes(out)


def wrap(text: str, width_of) -> list[str]:
    """One line, or two split at the space nearest the middle (both must fit LINE_MAX)."""
    text = " ".join(text.split())
    if width_of(text) <= LINE_MAX:
        return [text]
    spaces = [i for i, c in enumerate(text) if c == " "]
    best = None
    for i in spaces:
        a, b = text[:i], text[i + 1:]
        wa, wb = width_of(a), width_of(b)
        if wa <= LINE_MAX and wb <= LINE_MAX and (best is None or max(wa, wb) < best[0]):
            best = (max(wa, wb), [a, b])
    if best is None:
        raise SubtitleError(f"line does not fit in two lines of {LINE_MAX}px: {text}")
    return best[1]


def cue_frames(lines: list[dict]) -> list[tuple[int, int]]:
    out = []
    for i, ln in enumerate(lines):
        start = max(0.0, ln["start"] - LEAD)
        end = max(ln["end"] + HOLD, start + MIN_SHOW)
        if i + 1 < len(lines):
            end = min(end, max(lines[i + 1]["start"] - LEAD, ln["end"]))
        out.append((round(start * FPS), round(end * FPS)))
    return out


def _stem(name: str) -> bytes:
    stem = name.upper().split(".")[0].encode("ascii")
    if len(stem) > 8:
        raise SubtitleError(f"voice name {name} longer than 8 characters")
    return stem + bytes(8 - len(stem))


def build_group(group: int, voices: list[dict], font_path: str) -> bytes:
    """voices: translation docs ({"voice", "lines": [{start, end, ko}]}) in voice-number order."""
    font = _font(font_path, FONT_SIZE)
    glyphs: dict[str, tuple[int, bytes]] = {}
    gid: dict[str, int] = {}

    def width_of(s):
        w = 0
        for ch in s:
            if ch not in glyphs:
                glyphs[ch] = render_glyph(ch, font)
            w += glyphs[ch][0]
        return w

    cues, text = [], []
    for vno, doc in enumerate(voices):
        lines = [ln for ln in doc["lines"] if ln.get("ko")]
        for ln, (s, e) in zip(lines, cue_frames(lines)):
            if not ALLOWED.fullmatch(ln["ko"]):
                bad = sorted(set(re.sub(ALLOWED.pattern[:-1], "", ln["ko"])))
                raise SubtitleError(f"{doc['voice']} {ln['start']}: characters outside the subtitle set {bad}")
            if e <= s:
                raise SubtitleError(f"{doc['voice']} {ln['start']}: line ends before it starts")
            parts = wrap(ln["ko"], width_of)
            ids = []
            for p in parts:
                for ch in p:
                    if ch not in gid:
                        gid[ch] = len(gid)
                    ids.append(gid[ch])
            n1 = len(parts[0])
            n2 = len(parts[1]) if len(parts) > 1 else 0
            xs = [(SCREEN_W - width_of(p)) // 2 for p in parts] + [0]
            if len(parts) == 1:       # a single line sits on the lower row
                cues.append((vno, 2, s, e, len(text), 0, n1, 0, xs[0]))
            else:
                cues.append((vno, 2, s, e, len(text), n1, n2, xs[0], xs[1]))
            text += ids
    order = sorted(gid, key=gid.get)
    gl_tab, bits = b"", b""
    for ch in order:
        w, b = glyphs[ch]
        gl_tab += struct.pack(">HH", w, len(bits))
        bits += b
    names = b"".join(_stem(doc["voice"]) for doc in voices)
    cue_b = b"".join(struct.pack(">BBHHHHHhh", *c) for c in cues)
    text_b = b"".join(struct.pack(">H", t) for t in text)
    o_names = 32
    o_cues = o_names + len(names)
    o_gl = o_cues + len(cue_b)
    o_text = o_gl + len(gl_tab)
    o_bits = o_text + len(text_b)
    o_bits += o_bits & 1
    data = struct.pack(">IHHHHIIIII", GROUP_MAGIC, group, len(voices), len(cues), len(order),
                       o_names, o_cues, o_gl, o_text, o_bits)
    data += names + cue_b + gl_tab + text_b
    data += bytes(o_bits - len(data)) + bits
    if len(data) > GROUP_MAX:
        raise SubtitleError(f"group {group} is {len(data)} bytes, room for {GROUP_MAX}")
    return data


def unpack(stream: bytes) -> tuple[bytes, list[tuple[int, int]]]:
    """Model of sub.c unlzss(): (output, [(output length, input bytes consumed)] per step)."""
    out, steps, i, flags = bytearray(), [], 0, 0
    while i < len(stream):
        flags >>= 1
        if not flags & 0x100:
            flags = stream[i] | 0xFF00
            i += 1
            if i >= len(stream):
                break
        if flags & 1:
            out.append(stream[i])
            i += 1
        else:
            if i + 1 >= len(stream):
                break
            lo, hi = stream[i], stream[i + 1]
            i += 2
            p, n = lo | (hi & 0xF0) << 4, (hi & 0x0F) + 3
            back = (0xFEE + len(out) - p) & 0xFFF or 0x1000
            for _ in range(n):
                k = len(out) - back
                out.append(out[k] if k >= 0 else 0)
        steps.append((len(out), i))
    return bytes(out), steps


def pack_group(data: bytes) -> bytes:
    """LZSS stream (no header) that sub.c can unpack in place: the packed sectors sit at the
    end of the GROUP_MAX area, the output grows from its start and must never pass the input."""
    stream = lzss.compress(data)[4:]
    got, steps = unpack(stream)
    if got != data:
        raise SubtitleError("LZSS round trip failed")
    return stream


def build_index(groups: list[bytes], triggers: list[list[str]]) -> tuple[bytes, bytes]:
    """(index, SUBDAT.BIN) for groups (bytes) and the scene files that trigger each group.

    SUBDAT.BIN holds the groups' LZSS streams back to back; the index gives each one's byte
    offset, packed and unpacked sizes."""
    subdat, table, entries = b"", b"", b""
    for g, (data, files) in enumerate(zip(groups, triggers)):
        stream = pack_group(data)
        off = len(subdat)
        first, last = off // cdsector.USER, (off + len(stream) - 1) // cdsector.USER
        area = (last - first + 1) * cdsector.USER
        start = GROUP_MAX - area + off % cdsector.USER        # where the stream begins in the area
        if area > GROUP_MAX or len(data) > GROUP_MAX:
            raise SubtitleError(f"group {g}: {len(data)} bytes unpacked, {area} bytes of sectors")
        for produced, consumed in unpack(stream)[1]:
            if produced > start + consumed:
                raise SubtitleError(f"group {g}: unpacking would overwrite its own input")
        table += struct.pack(">IHH", off, len(stream), len(data))
        subdat += stream
        for name in files:
            raw = name.upper().encode("ascii")
            if len(raw) > 12:
                raise SubtitleError(f"trigger file name {name} longer than 12 characters")
            entries += raw + bytes(12 - len(raw)) + struct.pack(">B3x", g)
    index = struct.pack(">IHH", INDEX_MAGIC, len(entries) // 16, len(groups)) + table + entries
    if CODE_MAX + len(index) > SUB_MAX:
        raise SubtitleError(f"subtitle index is {len(index)} bytes, room for {SUB_MAX - CODE_MAX}")
    return index, subdat + bytes(-len(subdat) % cdsector.USER) if subdat else bytes(cdsector.USER)


# ---- whole subtitle data set ------------------------------------------------------------------
def load_voice_docs(voice_dir) -> dict:
    import json
    from pathlib import Path
    docs = {}
    for p in sorted(Path(voice_dir).glob("*.json")):
        doc = json.loads(p.read_text(encoding="utf-8"))
        if doc["voice"].upper() in docs:
            raise SubtitleError(f"{p.name}: voice {doc['voice']} translated twice")
        docs[doc["voice"].upper()] = doc
    return docs


def build_data(code: bytes, scenes: dict, docs: dict, font_path: str) -> tuple[bytes, bytes, dict]:
    """(SUB.BIN, SUBDAT.BIN, manifest info) from the scene groups and the voice translations.

    A group goes on the disc when at least one of its voices has a translated line; voices
    without a translation stay in the group with no cues (their names are known, nothing shows)."""
    used, triggers, info_groups = [], [], []
    statuses: dict[str, int] = {}
    known = {v.upper() for g in scenes["groups"] for v in g["voices"]}
    stray = sorted(set(docs) - known)
    if stray:
        raise SubtitleError(f"translations for voices outside every group: {stray}")
    for g in scenes["groups"]:
        voices = [docs.get(v.upper(), {"voice": v, "lines": []}) for v in g["voices"]]
        lines = [ln for d in voices for ln in d["lines"] if ln.get("ko")]
        if not lines:
            continue
        for ln in lines:
            statuses[ln.get("status", "needs_review")] = statuses.get(ln.get("status", "needs_review"), 0) + 1
        used.append(build_group(len(used), voices, font_path))
        triggers.append(g["triggers"])
        info_groups.append({"id": g["id"], "voices": g["voices"], "lines": len(lines), "bytes": len(used[-1])})
    index, subdat = build_index(used, triggers)
    info = {"groups": info_groups, "lines": sum(statuses.values()), "status": statuses,
            "distribution": set(statuses) <= {"distribution_eligible"},
            "index_bytes": len(index), "subdat_bytes": len(subdat)}
    return sub_bin(code, index), subdat, info
