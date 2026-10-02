"""Primary product build: source BIN/CUE -> patched BIN/CUE + manifest.json.

Current scope: the title-screen sprite bundle inside PROLOG.BIN (LZSS-compressed Yc block),
other LZSS-compressed Yc blocks ("packed" jobs, e.g. the opening name plates), and uncompressed
Yc bundles in whole files (menu, match screen, cards). All output changes go through one
WritePlan over the raw Track 1 image, one write per touched sector (changes merged per file).
"""
import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image

from . import cdsector, credits as credits_mod, iso9660, lzss, rle16, select1 as select1_mod, title, wheel as wheel_mod, yc
from .writeplan import PlanError, WritePlan

OUT_STEM = "Idol Janshi Suchie-Pai II (Korean) (Disc 1)"


class BuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceProfile:
    track1_sha1: str
    track2_sha1: str
    prolog_name: str
    prolog_lba: int
    prolog_size: int
    title_region: tuple[int, int]      # PROLOG.BIN offsets: compressed block + following zero gap
    title_region_sha1: str
    select1_name: str | None = None
    select1_lba: int | None = None
    select1_size: int | None = None
    select1_bundle: int | None = None  # SELECT1.BIN offset of the Yc bundle
    files: dict = field(default_factory=dict)   # other patched files: name -> (lba, size)
    packed: dict = field(default_factory=dict)  # compressed Yc blocks: name -> (file, lo, hi, region_sha1[, codec])
    read_only: dict = field(default_factory=dict)  # files read (e.g. CRAM images) but never written


# Idol Janshi Suchie-Pai II (Japan) (Disc 1), T-5705G V1.001 (docs/initial-survey.md §1, §3.6)
JP_DISC1 = SourceProfile(
    track1_sha1="90efa69e3b3f89503e356d6d6f5112ff8a541f1d",
    track2_sha1="5328aad6e81dc43b59ccde73ada1f51c930be5e4",
    prolog_name="PROLOG.BIN", prolog_lba=267880, prolog_size=1005312,
    title_region=(0x7900, 0xA600),
    title_region_sha1="22088bff3f7fc47c9596660b131d46c391bff82d",
    select1_name="SELECT1.BIN", select1_lba=14841, select1_size=442368, select1_bundle=0x2FC0,
    files={  # stage overlays carrying the match-screen UI bundles (docs/initial-survey.md §3.9)
        "ALICE.BIN": (260655, 1048576),
        "TUKASA.BIN": (261167, 1048576),
        "SANAE.BIN": (261679, 1048576),
        "RUMI.BIN": (262191, 1048576),
        "YUKI.BIN": (262703, 1048576),
        "SIHO.BIN": (263215, 1048576),
        "SESIL.BIN": (263727, 1048576),
        "SESIL2.BIN": (264239, 1048576),
        "HIMITU.BIN": (264751, 1048576),
        "NAZO.BIN": (265263, 1048576),
        "SECRET.BIN": (265775, 1048576),
        "HIDDEN.BIN": (266287, 1048576),
        "KAKUSHI.BIN": (266799, 1048576),
        # opponent introduction cards and the save notice (docs/initial-survey.md §3.10)
        "APALICE.BIN": (21042, 393216),
        "APDEVIL.BIN": (26293, 393216),
        "APKYOKO.BIN": (26485, 393216),
        "APMILK.BIN": (24852, 393216),
        "APRUMI.BIN": (24660, 393216),
        "APSANAE.BIN": (23599, 393216),
        "APSECIL.BIN": (26101, 393216),
        "APSHIHO.BIN": (25909, 393216),
        "APSUB.BIN": (27652, 32768),
        "APTSUKA.BIN": (22279, 393216),
        "APYUKI.BIN": (25717, 393216),
        # boot notice screens (word-RLE Yc blocks, docs/initial-survey.md 3.15)
        "BACKRAM.BIN": (259889, 430080),
        # panel match bonus game (uncompressed Yc @0x6000, docs/initial-survey.md 3.16)
        "PMATCH.BIN": (389, 442368),
        # bonus screens after a big win (uncompressed Yc @0x5000, docs/initial-survey.md 3.17)
        "MAXGRP1.BIN": (2213, 286720),
        "MAXGRP2.BIN": (2400, 204800),
        "MAXGRP3.BIN": (2580, 331776),
        # ending chunk files with a Japanese credit roll each (§3.20)
        "ENDY": (269989, 741060), "ED_MILK.DAT": (268417, 212260), "ED_YUKI.DAT": (268521, 240712),
        "ED_SANAE.DAT": (269381, 394836), "ED_ALIS.DAT": (269574, 343308),
    },
    # opening character-intro name plates (docs/initial-survey.md §3.11)
    packed={"letters": ("PROLOG.BIN", 0x0, 0x3C00, "6a3247b67c3b042d7a92724be1288c4b8f3167e5"),   # big letters (§3.12)
            "opening": ("PROLOG.BIN", 0x3C00, 0x7900, "ab1dd22d24cf71013f7f7cce22b55a0bae0c75a1"),
            "boot_notice": ("BACKRAM.BIN", 0x17000, 0x27000, "50db63e7caff6b638684d6175481c0b9d084d75f", "rle16"),
            # other BACKRAM.BIN notices (§3.15): backup not ready / RAM full or record broken / save failed
            "boot_unready": ("BACKRAM.BIN", 0x4000, 0x17000, "b37ce8c78d053a3d61ad96f49fc26c853dad1b43", "rle16"),
            "boot_ram": ("BACKRAM.BIN", 0x27000, 0x46000, "8de2c4aa3dfc8f10732228217d161504808a84f3", "rle16"),
            "boot_savefail": ("BACKRAM.BIN", 0x46000, 0x69000, "dc44631071bb9de1e1bc14ba3733cae09cff2d19", "rle16")},
    read_only={"OPENING1.BIN": (267390, 1001728),   # CRAM image of the opening and title (banks 0x50/0x60/0x70/0x80)
               # source copy of a written file: the credit rolls' CRAM bank (§3.20); _plan_roll keeps it unchanged
               "ED_ALIS.DAT": (269574, 343308)},   # CRAM image of the opening and title (banks 0x50/0x60/0x70/0x80)
)
STAGE_FILES = ["ALICE.BIN", "TUKASA.BIN", "SANAE.BIN", "RUMI.BIN", "YUKI.BIN", "SIHO.BIN", "SESIL.BIN",
               "SESIL2.BIN", "HIMITU.BIN", "NAZO.BIN", "SECRET.BIN", "HIDDEN.BIN", "KAKUSHI.BIN"]
CARD_FILES = ["APALICE.BIN", "APDEVIL.BIN", "APKYOKO.BIN", "APMILK.BIN", "APRUMI.BIN", "APSANAE.BIN",
              "APSECIL.BIN", "APSHIHO.BIN", "APSUB.BIN", "APTSUKA.BIN", "APYUKI.BIN"]
BONUS_FILES = ["MAXGRP1.BIN", "MAXGRP2.BIN", "MAXGRP3.BIN"]
ROLL_FILES = ["ENDY", "ED_ALIS.DAT", "ED_MILK.DAT", "ED_SANAE.DAT", "ED_YUKI.DAT"]
assert sorted(STAGE_FILES + CARD_FILES + BONUS_FILES + ROLL_FILES + ["BACKRAM.BIN", "PMATCH.BIN"]) == sorted(JP_DISC1.files)


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def _cue_tracks(cue: Path) -> tuple[Path, Path]:
    files = re.findall(r'^\s*FILE\s+"([^"]+)"', cue.read_text(), re.M)
    if len(files) != 2:
        raise BuildError(f"{cue}: expected 2 FILE entries, found {len(files)}")
    return cue.parent / files[0], cue.parent / files[1]


class _Track1:
    def __init__(self, path: Path):
        self.path = path
        self.f = open(path, "rb")

    def raw(self, lba: int) -> bytes:
        self.f.seek(lba * cdsector.RAW)
        s = self.f.read(cdsector.RAW)
        if len(s) != cdsector.RAW:
            raise BuildError(f"Track 1 ends before LBA {lba}")
        return s

    def user(self, lba: int) -> bytes:
        return self.raw(lba)[16:16 + cdsector.USER]

    def close(self):
        self.f.close()


def _palette(values) -> list[int]:
    pal = [int(v, 16) for v in values]
    if len(pal) != 16:
        raise BuildError("palette must have 16 entries")
    return pal


def compose_title(entries: list[yc.Entry], spec_path: Path) -> list[yc.Entry]:
    spec = json.loads(spec_path.read_text())
    logo = Image.open(spec_path.parent / spec["logo"]).convert("RGBA")
    canvas = Image.new("RGBA", tuple(spec["canvas"]), (0, 0, 0, 0))
    masks = {}
    for part in spec["parts"]:
        src = logo
        if "group" in part:
            key = json.dumps(part["group"]["rgb"])
            if key not in masks:
                masks[key] = title.color_group_mask(logo, part["group"]["rgb"])
            src = title.apply_mask(logo, masks[key], keep=part["group"]["keep"])
        piece = src.crop(tuple(part["box"]))
        canvas.alpha_composite(title.place(piece, canvas.size, part["scale"], *part["at"]))
    sprites = []
    for s in spec["sprites"]:
        e = entries[s["entry"]]
        if (e.width, e.height) != tuple(s["rect"][2:]):
            raise BuildError(f"sprite {s['name']}: rect size {s['rect'][2:]} != entry {e.width}x{e.height}")
        sprites.append(title.Sprite(s["name"], tuple(s["rect"]), _palette(s["palette"])))
    try:
        tex = title.assign(canvas, sprites)
    except title.LayoutError as err:
        raise BuildError(str(err)) from err
    out = list(entries)
    for s in spec["sprites"]:
        e = out[s["entry"]]
        out[s["entry"]] = yc.Entry(e.width, e.height, e.attr, e.colr, bytes(tex[s["name"]].data))
    for i in spec.get("blank_entries", []):
        e = out[i]
        out[i] = yc.Entry(e.width, e.height, e.attr, e.colr, bytes(len(e.data)))
    return out


def _file_writes(plan: WritePlan, t1: _Track1, file_lba: int, changes: list[tuple[int, bytes]], writer: str) -> list[int]:
    """Register one sector write per touched sector for byte changes given in file offsets."""
    spans = sorted((off, off + len(new)) for off, new in changes)
    for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
        if b0 < a1:
            raise BuildError(f"{writer}: changes overlap at file offset {b0:#x}")
    per_sector: dict[int, list[tuple[int, bytes]]] = {}
    for off, new in changes:
        pos = 0
        while pos < len(new):
            sec = (off + pos) // cdsector.USER
            so = (off + pos) % cdsector.USER
            n = min(cdsector.USER - so, len(new) - pos)
            per_sector.setdefault(sec, []).append((so, new[pos:pos + n]))
            pos += n
    lbas = []
    for sec_no in sorted(per_sector):
        lba = file_lba + sec_no
        raw = t1.raw(lba)
        if bytes(cdsector.fix_mode1(bytearray(raw))) != raw or cdsector.header_lba(raw) != lba:
            raise BuildError(f"LBA {lba}: source sector does not match the Mode 1 EDC/ECC model")
        sec = bytearray(raw)
        for so, piece in per_sector[sec_no]:
            sec[16 + so:16 + so + len(piece)] = piece
        cdsector.fix_mode1(sec)
        plan.add(f"{writer}@{lba}", lba * cdsector.RAW + 16, raw[16:], bytes(sec[16:]))
        lbas.append(lba)
    return lbas


def _read_file_range(t1: _Track1, file_lba: int, lo: int, hi: int) -> bytes:
    first, last = lo // cdsector.USER, (hi - 1) // cdsector.USER
    data = b"".join(t1.user(file_lba + i) for i in range(first, last + 1))
    return data[lo - first * cdsector.USER:hi - first * cdsector.USER]


def _rel(path: Path) -> str:
    root = Path(__file__).resolve().parents[2]
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return path.name


def _verify_output(track1: Path, prolog_lba: int, lo: int, hi: int, stream: bytes, bundle: bytes, lbas: list[int],
                   what: str = "title", codec: str = "lzss") -> None:
    t = _Track1(track1)
    try:
        for lba in lbas:
            raw = t.raw(lba)
            if bytes(cdsector.fix_mode1(bytearray(raw))) != raw:
                raise BuildError(f"output LBA {lba}: EDC/ECC inconsistent")
        got = _read_file_range(t, prolog_lba, lo, hi)
    finally:
        t.close()
    if got[:len(stream)] != stream or any(got[len(stream):]):
        raise BuildError(f"output {what} region differs from the planned stream + zero padding")
    if not CODECS[codec]["decodes_to"](got[:len(stream)], bundle):
        raise BuildError(f"output {what} block does not decode to the built bundle")


def _outside(extents: list[tuple[int, int]], total: int) -> list[tuple[int, int]]:
    """Protected byte ranges of the raw image: everything outside the given file extents."""
    spans = sorted((lba * cdsector.RAW, (lba + (size + cdsector.USER - 1) // cdsector.USER) * cdsector.RAW)
                   for lba, size in extents)
    out, pos = [], 0
    for a, b in spans:
        if a > pos:
            out.append((pos, a))
        pos = max(pos, b)
    if pos < total:
        out.append((pos, total))
    return out


def _file_extent(profile: SourceProfile, name: str) -> tuple[int, int]:
    if name in profile.files:
        return profile.files[name]
    if name == profile.select1_name and profile.select1_lba is not None:
        return profile.select1_lba, profile.select1_size
    raise BuildError(f"{name} is not in the source profile")


def _packed_extent(profile: SourceProfile, name: str) -> tuple[int, int]:
    """Extent of a file holding compressed blocks (PROLOG.BIN or a profile file)."""
    if name == profile.prolog_name:
        return profile.prolog_lba, profile.prolog_size
    return _file_extent(profile, name)


def _plan_bundle(t1: _Track1, profile: SourceProfile, job: dict) -> list[tuple[dict, list]]:
    """Render one translation/layout pair into every listed file (all protected checks run per
    file). Returns (info, changes) per file; sector writes are registered later, once per file."""
    translation, layout = Path(job["translation"]), Path(job["layout"])
    glossary = Path(job["glossary"]) if job.get("glossary") else None
    font = json.loads(layout.read_text()).get("font", select1_mod.label.DEFAULT_FONT)
    tdoc = json.loads(translation.read_text())
    if "chunks" in tdoc:
        return _plan_roll(t1, profile, job, tdoc, font)
    if "packed" in tdoc or "palette_file" in tdoc:
        raise BuildError(f"{translation.name}: packed table used in a file job")
    bundle = int(tdoc["bundle_offset"], 16)
    if "files" in tdoc and sorted(tdoc["files"]) != sorted(job["files"]):
        raise BuildError(f"{translation.name}: job files {job['files']} differ from the table's files {tdoc['files']}")
    if profile.select1_bundle is not None and profile.select1_name in job["files"] and bundle != profile.select1_bundle:
        raise BuildError(f"{translation.name}: bundle offset {bundle:#x} differs from profile {profile.select1_bundle:#x}")
    if len(set(job["files"])) != len(job["files"]):
        raise BuildError(f"{translation.name}: a file is listed more than once in one job")
    out = []
    for name in job["files"]:
        lba, size = _file_extent(profile, name)
        found = iso9660.find_file(t1.user, name)
        if found != (lba, size):
            raise BuildError(f"{name} extent {found} differs from profile {(lba, size)}")
        data = _read_file_range(t1, lba, 0, size)
        ents = select1_mod.entries(data, bundle)
        offsets = select1_mod.offsets(data, bundle)
        render = wheel_mod.render if "wheels" in tdoc else select1_mod.render     # roulette: one picture over 4 entries
        res = render(data, translation, layout, font=font, glossary=glossary, file_name=name)
        changes = [(offsets[i], tex) for i, tex in sorted(res.textures.items()) if tex != ents[i].data]
        info = {"file": name, "lba": lba, "entries": res.ids, "states": res.states, "distribution": res.distribution,
                "changes": [[o, len(t)] for o, t in changes], "sectors": [],
                "translation": _rel(translation), "translation_sha1": _sha1(translation),
                "layout": _rel(layout), "layout_sha1": _sha1(layout),
                "glossary": _rel(glossary) if glossary else None, "glossary_sha1": _sha1(glossary) if glossary else None,
                "font": font, "font_sha1": _sha1(Path(font))}
        out.append((info, changes))
    return out


def _plan_roll(t1: _Track1, profile: SourceProfile, job: dict, tdoc: dict, font: str) -> list[tuple[dict, list]]:
    """Credit roll in a chunk file (credits.py): the whole file is rebuilt at its original size;
    the palette comes from a read-only profile file."""
    translation, layout = Path(job["translation"]), Path(job["layout"])
    glossary = Path(job["glossary"]) if job.get("glossary") else None
    if job["files"] != [tdoc.get("file")]:
        raise BuildError(f"{translation.name}: table is for file {tdoc.get('file')!r}, job lists {job['files']}")
    pf = tdoc.get("palette_file")
    if pf not in profile.read_only:
        raise BuildError(f"{translation.name}: palette_file {pf!r} is not a read-only file of the profile")
    pal_data = _checked_file(t1, profile.read_only[pf], pf)       # always the source bytes
    out = []
    for name in job["files"]:
        lba, size = _file_extent(profile, name)
        data = _checked_file(t1, (lba, size), name)
        new, res = credits_mod.render(data, translation, layout, font=font, glossary=glossary, file_name=name,
                                      palette_data=pal_data)
        if len(new) != len(data):
            raise BuildError(f"{name}: rebuilt roll file changed size {len(data)} -> {len(new)}")
        if pf == name:          # the palette lives in the file being rewritten: its bank must stay as it was
            bank, n = select1_mod.bank_span(int(tdoc["colr"], 16))
            lo = int(tdoc["palette_offset"], 16) + 2 * bank
            if new[lo:lo + 2 * n] != data[lo:lo + 2 * n]:
                raise BuildError(f"{name}: rewriting the roll changes its own palette bank at {lo:#x}")
        changes = [(0, new)] if new != data else []
        info = {"file": name, "lba": lba, "entries": res.ids, "states": res.states, "distribution": res.distribution,
                "changes": [[o, len(t)] for o, t in changes], "sectors": [],
                "palette_file": pf, "palette_file_sha1": hashlib.sha1(pal_data).hexdigest(),
                "translation": _rel(translation), "translation_sha1": _sha1(translation),
                "layout": _rel(layout), "layout_sha1": _sha1(layout),
                "glossary": _rel(glossary) if glossary else None, "glossary_sha1": _sha1(glossary) if glossary else None,
                "font": font, "font_sha1": _sha1(Path(font))}
        out.append((info, changes))
    return out


def _checked_file(t1: _Track1, extent: tuple[int, int], name: str) -> bytes:
    found = iso9660.find_file(t1.user, name)
    if found != tuple(extent):
        raise BuildError(f"{name} extent {found} differs from profile {tuple(extent)}")
    return _read_file_range(t1, extent[0], 0, extent[1])


def _rle16_recompress(bundle: bytes, room: int, what: str) -> bytes:
    stream = rle16.compress(bundle)
    if rle16.decompress(stream) != (bundle, len(stream)):
        raise BuildError(f"recompressed {what} block does not round-trip")
    if len(stream) > room:
        raise BuildError(f"{what} block {len(stream)} bytes exceeds region {room}")
    return stream


def _recompress(bundle: bytes, room: int, what: str) -> bytes:
    stream = lzss.compress(bundle)
    if lzss.decompress(stream) != bundle or lzss.decompress(stream, stale=b"\xa5" * lzss.STALE_LEN) != bundle:
        raise BuildError(f"recompressed {what} block does not round-trip")
    if len(stream) > room:
        raise BuildError(f"{what} block {len(stream)} bytes exceeds region {room}")
    return stream


def _render_block_labels(t1: _Track1, profile: SourceProfile, job: dict, name: str, bundle: bytes,
                         fname: str, rewritten: list[tuple[int, int]] | None = None) -> tuple[bytes, dict]:
    """Draw a label table into a decoded Yc block. The table must name this block (`packed`),
    index the decoded block (bundle_offset 0x0) and pin its palette, read from the source: either a
    read-only profile file, or the block's own file when no palette bank lies in any region this
    build rewrites in that file (`rewritten`). Returns (rebuilt bundle, provenance info)."""
    translation, layout = Path(job["translation"]), Path(job["layout"])
    glossary = Path(job["glossary"]) if job.get("glossary") else None
    font = json.loads(layout.read_text()).get("font", select1_mod.label.DEFAULT_FONT)
    tdoc = json.loads(translation.read_text())
    if tdoc.get("packed") != name:
        raise BuildError(f"{translation.name}: table is for packed block {tdoc.get('packed')!r}, job is {name!r}")
    if int(tdoc.get("bundle_offset", "0x0"), 16) != 0:
        raise BuildError(f"{translation.name}: packed tables index the decoded block, bundle_offset must be 0x0")
    unpinned = [e["id"] for e in tdoc["entries"] if len(e.get("palette_sha1", "")) != 40]
    if "palette_offset" not in tdoc or unpinned:
        raise BuildError(f"{translation.name}: packed tables need palette_offset and every entry's palette_sha1 "
                         f"(missing: {unpinned})")
    pf = tdoc.get("palette_file")
    if pf == fname and rewritten is not None:
        # CRAM image in the block's own file: read from the source; it must not lie in the rewritten region
        pal_lo = int(tdoc["palette_offset"], 16)
        src_ents = yc.parse(bundle)
        spans = set()
        for e in tdoc["entries"]:
            mode = (src_ents[e["entry"]].attr >> 3) & 7 if e["entry"] < len(src_ents) else 0
            bank, n = select1_mod.bank_span(int(e["colr"], 16), select1_mod.COLOURS.get(mode, 16))
            spans.add((pal_lo + bank * 2, 2 * n))
        if any(b < hi and lo < b + n for b, n in spans for lo, hi in rewritten):
            raise BuildError(f"{translation.name}: a palette bank at {sorted(hex(b) for b, _ in spans)} overlaps the rewritten region")
        pal_data = _checked_file(t1, _packed_extent(profile, fname), fname)
    elif pf in profile.read_only:
        pal_data = _checked_file(t1, profile.read_only[pf], pf)
    else:
        raise BuildError(f"{translation.name}: palette_file {pf!r} is not a read-only file of the profile")
    ents = yc.parse(bundle)
    res = select1_mod.render(bundle, translation, layout, font=font, glossary=glossary, file_name=fname,
                             palette_data=pal_data)
    new = yc.build([yc.Entry(e.width, e.height, e.attr, e.colr, res.textures.get(i, e.data)) for i, e in enumerate(ents)])
    info = {"entries": res.ids, "states": res.states, "distribution": res.distribution,
            "palette_file": pf, "palette_file_sha1": hashlib.sha1(pal_data).hexdigest(),
            "translation": _rel(translation), "translation_sha1": _sha1(translation),
            "layout": _rel(layout), "layout_sha1": _sha1(layout),
            "glossary": _rel(glossary) if glossary else None, "glossary_sha1": _sha1(glossary) if glossary else None,
            "font": font, "font_sha1": _sha1(Path(font))}
    return new, info


CODECS = {
    # stream_len: bytes of the source region taken by the stream; decode: region -> Yc bundle
    "lzss": {"stream_len": lambda region: 4 + int.from_bytes(region[:4], "big"),
             "decode": lambda region: lzss.decompress(region),
             "encode": lambda bundle, room, what: _recompress(bundle, room, what),
             "decodes_to": lambda got, bundle: all(lzss.decompress(got, stale=st) == bundle
                                                   for st in (None, b"\xa5" * lzss.STALE_LEN))},
    "rle16": {"stream_len": lambda region: rle16.decompress(region)[1],
              "decode": lambda region: rle16.decompress(region)[0],
              "encode": lambda bundle, room, what: _rle16_recompress(bundle, room, what),
              "decodes_to": lambda got, bundle: rle16.decompress(got) == (bundle, len(got))},
}


def _packed_entry(profile: SourceProfile, name: str) -> tuple[str, int, int, str, str]:
    fname, lo, hi, region_sha1, *rest = profile.packed[name]
    codec = rest[0] if rest else "lzss"
    if codec not in CODECS:
        raise BuildError(f"{name}: unknown codec {codec!r}")
    return fname, lo, hi, region_sha1, codec


def _plan_packed(t1: _Track1, profile: SourceProfile, job: dict,
                 writes: list[tuple[str, int, int]]) -> tuple[dict, list, bytes, bytes]:
    """Render one translation/layout pair into a compressed Yc block of the profile (codec per
    profile entry) and recompress it into the same region. `writes` = every (file, lo, hi) region
    this build rewrites. Returns (info, changes, stream, rebuilt bundle)."""
    name = job["packed"]
    fname, lo, hi, region_sha1, codec = _packed_entry(profile, name)
    lba, size = _packed_extent(profile, fname)
    if not 0 <= lo < hi <= size:
        raise BuildError(f"{name}: region {lo:#x}-{hi:#x} outside {fname}")
    region = _checked_file(t1, (lba, size), fname)[lo:hi]
    if hashlib.sha1(region).hexdigest() != region_sha1:
        raise BuildError(f"{name}: region bytes differ from the supported source")
    c = CODECS[codec]
    if any(region[c["stream_len"](region):]):
        raise BuildError(f"{name}: region tail after the source stream is not zero padding")
    new, labels = _render_block_labels(t1, profile, job, name, c["decode"](region), fname,
                                       rewritten=[(a, b) for f, a, b in writes if f == fname])
    stream = c["encode"](new, hi - lo, name)
    info = {"packed": name, "codec": codec, "file": fname, "lba": lba, "region": [lo, hi], "stream_bytes": len(stream),
            "sectors": [], **labels}
    return info, [(lo, stream + bytes(hi - lo - len(stream)))], stream, new


def _build(source_cue: Path, out_dir: Path, title_spec: Path | None, profile: SourceProfile,
           select1=None, bundles=None, title: bool = True, title_labels=None) -> dict:
    tr1, tr2 = _cue_tracks(source_cue)
    if _sha1(tr1) != profile.track1_sha1:
        raise BuildError(f"Track 1 SHA-1 mismatch: {tr1}")
    if _sha1(tr2) != profile.track2_sha1:
        raise BuildError(f"Track 2 SHA-1 mismatch: {tr2}")
    if title_spec and not title:
        raise BuildError("a title spec was given but the title bundle is disabled")
    if title_labels is not None and len(title_labels) < 2:
        raise BuildError("title_labels needs (translation, layout[, glossary])")
    if title_labels is not None and not title:
        raise BuildError("title labels were given but the title bundle is disabled")
    jobs = [j for j in (bundles or []) if "packed" not in j]
    packed_jobs = [j for j in (bundles or []) if "packed" in j]
    if any("files" in j for j in packed_jobs):
        raise BuildError("a job is either packed or files, not both")
    unknown = [j["packed"] for j in packed_jobs if j["packed"] not in profile.packed]
    if unknown:
        raise BuildError(f"packed blocks {unknown} are not in the source profile")
    compressed = {f for f, *_ in profile.packed.values()} | {profile.prolog_name}
    held = sorted({n for j in jobs for n in j["files"] if n in compressed})
    if held:
        raise BuildError(f"{held} hold compressed blocks; use a packed job")
    if select1:
        jobs.insert(0, {"files": [profile.select1_name or "SELECT1.BIN"], "translation": select1[0], "layout": select1[1],
                        "glossary": select1[2] if len(select1) > 2 else None, "_select1": True})
    lo, hi = profile.title_region
    label_info = None
    t1 = _Track1(tr1)
    try:
        extents = [_file_extent(profile, n) for job in jobs for n in job["files"]]
        extents += [_packed_extent(profile, profile.packed[j["packed"]][0]) for j in packed_jobs]
        if title:
            if not 0 <= lo < hi <= profile.prolog_size:
                raise BuildError(f"title region {lo:#x}-{hi:#x} outside {profile.prolog_name}")
            prolog_lba, size = iso9660.find_file(t1.user, profile.prolog_name)
            if (prolog_lba, size) != (profile.prolog_lba, profile.prolog_size):
                raise BuildError(f"{profile.prolog_name} extent {prolog_lba}/{size} differs from profile")
            extents.append((prolog_lba, size))
        plan = WritePlan(tr1, protected=_outside(extents, tr1.stat().st_size))
        regions = [(profile.prolog_name, lo, hi, "title")] if title else []
        regions += [(*profile.packed[j["packed"]][:3], j["packed"]) for j in packed_jobs]
        for i, (fa, a0, a1, na) in enumerate(regions):      # early, named form of the _file_writes check
            for fb, b0, b1, nb in regions[i + 1:]:
                if fa == fb and a0 < b1 and b0 < a1:
                    raise BuildError(f"compressed regions {na} and {nb} overlap in {fa}")
        if title:
            region = _read_file_range(t1, prolog_lba, lo, hi)
            if hashlib.sha1(region).hexdigest() != profile.title_region_sha1:
                raise BuildError("title region bytes differ from the supported source")
            entries = yc.parse(lzss.decompress(region))
            new_entries = compose_title(entries, title_spec) if title_spec else entries
            tbundle = yc.build(new_entries)
            if title_labels is not None:
                job = {"translation": title_labels[0], "layout": title_labels[1],
                       "glossary": title_labels[2] if len(title_labels) > 2 else None}
                if title_spec:
                    spec = json.loads(title_spec.read_text())
                    owned = {sp["entry"] for sp in spec["sprites"]} | set(spec.get("blank_entries", []))
                    drawn = {e["entry"] for e in json.loads(Path(job["translation"]).read_text())["entries"]}
                    if owned & drawn:
                        raise BuildError(f"title labels redraw entries {sorted(owned & drawn)} owned by the logo spec")
                tbundle, label_info = _render_block_labels(t1, profile, job, "title", tbundle, profile.prolog_name)
            stream = _recompress(tbundle, hi - lo, "title")
        results = [(job, _plan_bundle(t1, profile, job)) for job in jobs]
        writes = [(f, a, b) for f, a, b, _ in regions]
        packed = [_plan_packed(t1, profile, job, writes) for job in packed_jobs]
        results += [(job, [(info, changes)]) for job, (info, changes, _, _) in zip(packed_jobs, packed)]
        per_file: dict[str, list] = {}
        title_slot = {"file": profile.prolog_name, "lba": prolog_lba} if title else None
        if title:
            per_file[profile.prolog_name] = [(title_slot, [(lo, stream + bytes(hi - lo - len(stream)))])]
        for _, infos in results:
            for info, changes in infos:
                per_file.setdefault(info["file"], []).append((info, changes))
        for name, items in per_file.items():
            lba = items[0][0]["lba"]
            if any(info["lba"] != lba for info, _ in items):
                raise BuildError(f"{name}: jobs disagree on the file's LBA")
            sectors = _file_writes(plan, t1, lba, [c for _, ch in items for c in ch], name)
            for info, ch in items:
                touched = {lba + (o + k) // cdsector.USER for o, t in ch for k in (0, len(t) - 1)}
                info["sectors"] = [x for x in sectors if min(touched, default=-1) <= x <= max(touched, default=-1)]
        lbas = title_slot["sectors"] if title else []
    finally:
        t1.close()

    o1, o2 = f"{OUT_STEM} (Track 1).bin", f"{OUT_STEM} (Track 2).bin"
    plan.apply(out_dir / o1)
    shutil.copyfile(tr2, out_dir / o2)
    if _sha1(out_dir / o2) != profile.track2_sha1:
        raise BuildError("Track 2 copy differs from source")
    if title:
        _verify_output(out_dir / o1, prolog_lba, lo, hi, stream, tbundle, lbas)
    for info, _, pstream, bundle in packed:
        _verify_output(out_dir / o1, info["lba"], *info["region"], pstream, bundle, info["sectors"],
                       what=info["packed"], codec=info["codec"])
    t = _Track1(out_dir / o1)
    try:
        for _, per_file in results:
            for info, changes in per_file:
                for lba in info["sectors"]:
                    raw = t.raw(lba)
                    if bytes(cdsector.fix_mode1(bytearray(raw))) != raw:
                        raise BuildError(f"output LBA {lba}: EDC/ECC inconsistent")
                for off, tex in changes:
                    if _read_file_range(t, info["lba"], off, off + len(tex)) != tex:
                        raise BuildError(f"output {info['file']} texture at {off:#x} differs from the rendered label")
    finally:
        t.close()
    cue_name = f"{OUT_STEM}.cue"
    (out_dir / cue_name).write_text(
        f'FILE "{o1}" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n'
        f'FILE "{o2}" BINARY\n  TRACK 02 AUDIO\n    INDEX 00 00:00:00\n    INDEX 01 00:02:00\n')
    title_info = None
    if title:
        title_info = {"mode": "ko" if title_spec else ("labels-only" if label_info else "original-recompressed"),
                      "region": [lo, hi], "stream_bytes": len(stream), "sectors": lbas}
        if title_spec:
            spec = json.loads(title_spec.read_text())
            title_info.update(spec=_rel(title_spec), spec_sha1=_sha1(title_spec),
                              logo_sha1=_sha1(title_spec.parent / spec["logo"]),
                              presentation=spec.get("presentation", "needs_human_review"))
        title_info["labels"] = label_info
    title_ok = title_spec is None or json.loads(title_spec.read_text()).get("presentation") == "approved"
    if label_info is not None and not label_info["distribution"]:
        title_ok = False
    infos = [info for _, per_file in results for info, _ in per_file]
    sel = next((per_file[0][0] for job, per_file in results if job.get("_select1")), None)
    manifest = {
        "cue": cue_name, "track1": o1, "track2": o2,
        "distribution": bool(title_ok and all(i["distribution"] for i in infos)),
        "source": {"track1_sha1": profile.track1_sha1, "track2_sha1": profile.track2_sha1},
        "output": {"track1_sha1": _sha1(out_dir / o1), "track2_sha1": _sha1(out_dir / o2)},
        "title": title_info,
        "select1": sel,
        "bundles": [i for i in infos if i is not sel],
        "profile": asdict(profile),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return manifest


def build(source_cue: Path, out_dir: Path, title_spec: Path | None, profile: SourceProfile = None,
          select1=None, bundles=None, title: bool = True, title_labels=None) -> dict:
    """Build into a fresh staging directory and replace out_dir only when every check passed."""
    profile = profile or JP_DISC1
    source_cue, out_dir = Path(source_cue), Path(out_dir)
    title_spec = Path(title_spec) if title_spec else None
    stage = out_dir.with_name(out_dir.name + ".partial")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    try:
        manifest = _build(source_cue, stage, title_spec, profile, select1, bundles, title, title_labels)
    except BuildError:
        raise
    except (PlanError, ValueError, OSError, KeyError) as err:
        raise BuildError(f"{type(err).__name__}: {err}") from err
    finally:
        if stage.exists() and not (stage / "manifest.json").exists():
            shutil.rmtree(stage)
    old = out_dir.with_name(out_dir.name + ".old")
    if old.exists():
        shutil.rmtree(old)
    if out_dir.exists():
        out_dir.rename(old)
    stage.rename(out_dir)
    if old.exists():
        shutil.rmtree(old)
    return manifest
