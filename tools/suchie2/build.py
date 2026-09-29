"""Primary product build: source BIN/CUE -> patched BIN/CUE + manifest.json.

Current scope: the title-screen sprite bundle inside PROLOG.BIN (LZSS-compressed Yc block).
All output changes go through one WritePlan over the raw Track 1 image.
"""
import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image

from . import cdsector, iso9660, lzss, title, yc
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


# Idol Janshi Suchie-Pai II (Japan) (Disc 1), T-5705G V1.001 (docs/initial-survey.md §1, §3.6)
JP_DISC1 = SourceProfile(
    track1_sha1="90efa69e3b3f89503e356d6d6f5112ff8a541f1d",
    track2_sha1="5328aad6e81dc43b59ccde73ada1f51c930be5e4",
    prolog_name="PROLOG.BIN", prolog_lba=267880, prolog_size=1005312,
    title_region=(0x7900, 0xA600),
    title_region_sha1="22088bff3f7fc47c9596660b131d46c391bff82d",
)


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


def _sector_writes(plan: WritePlan, t1: _Track1, file_lba: int, offset: int, new: bytes, writer: str) -> list[int]:
    lbas = []
    pos = 0
    while pos < len(new):
        lba = file_lba + (offset + pos) // cdsector.USER
        so = (offset + pos) % cdsector.USER
        n = min(cdsector.USER - so, len(new) - pos)
        raw = t1.raw(lba)
        if bytes(cdsector.fix_mode1(bytearray(raw))) != raw or cdsector.header_lba(raw) != lba:
            raise BuildError(f"LBA {lba}: source sector does not match the Mode 1 EDC/ECC model")
        sec = bytearray(raw)
        sec[16 + so:16 + so + n] = new[pos:pos + n]
        cdsector.fix_mode1(sec)
        plan.add(f"{writer}@{lba}", lba * cdsector.RAW + 16, raw[16:], bytes(sec[16:]))
        lbas.append(lba)
        pos += n
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


def _verify_output(track1: Path, prolog_lba: int, lo: int, hi: int, stream: bytes, bundle: bytes, lbas: list[int]) -> None:
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
        raise BuildError("output title region differs from the planned stream + zero padding")
    for stale in (None, b"\xa5" * lzss.STALE_LEN):
        if lzss.decompress(got[:len(stream)], stale=stale) != bundle:
            raise BuildError("output title block does not decode to the built bundle")


def _build(source_cue: Path, out_dir: Path, title_spec: Path | None, profile: SourceProfile) -> dict:
    tr1, tr2 = _cue_tracks(source_cue)
    if _sha1(tr1) != profile.track1_sha1:
        raise BuildError(f"Track 1 SHA-1 mismatch: {tr1}")
    if _sha1(tr2) != profile.track2_sha1:
        raise BuildError(f"Track 2 SHA-1 mismatch: {tr2}")
    lo, hi = profile.title_region
    if not 0 <= lo < hi <= profile.prolog_size:
        raise BuildError(f"title region {lo:#x}-{hi:#x} outside {profile.prolog_name}")
    t1 = _Track1(tr1)
    try:
        prolog_lba, size = iso9660.find_file(t1.user, profile.prolog_name)
        if (prolog_lba, size) != (profile.prolog_lba, profile.prolog_size):
            raise BuildError(f"{profile.prolog_name} extent {prolog_lba}/{size} differs from profile")
        region = _read_file_range(t1, prolog_lba, lo, hi)
        if hashlib.sha1(region).hexdigest() != profile.title_region_sha1:
            raise BuildError("title region bytes differ from the supported source")
        entries = yc.parse(lzss.decompress(region))
        new_entries = compose_title(entries, title_spec) if title_spec else entries
        bundle = yc.build(new_entries)
        stream = lzss.compress(bundle)
        if lzss.decompress(stream) != bundle or lzss.decompress(stream, stale=b"\xa5" * lzss.STALE_LEN) != bundle:
            raise BuildError("recompressed title block does not round-trip")
        if len(stream) > hi - lo:
            raise BuildError(f"title block {len(stream)} bytes exceeds region {hi - lo}")
        extent = (prolog_lba * cdsector.RAW, (prolog_lba + (size + cdsector.USER - 1) // cdsector.USER) * cdsector.RAW)
        plan = WritePlan(tr1, protected=[(0, extent[0]), (extent[1], tr1.stat().st_size)])
        lbas = _sector_writes(plan, t1, prolog_lba, lo, stream + bytes(hi - lo - len(stream)), "title")
    finally:
        t1.close()

    o1, o2 = f"{OUT_STEM} (Track 1).bin", f"{OUT_STEM} (Track 2).bin"
    plan.apply(out_dir / o1)
    shutil.copyfile(tr2, out_dir / o2)
    if _sha1(out_dir / o2) != profile.track2_sha1:
        raise BuildError("Track 2 copy differs from source")
    _verify_output(out_dir / o1, prolog_lba, lo, hi, stream, bundle, lbas)
    cue_name = f"{OUT_STEM}.cue"
    (out_dir / cue_name).write_text(
        f'FILE "{o1}" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n'
        f'FILE "{o2}" BINARY\n  TRACK 02 AUDIO\n    INDEX 00 00:00:00\n    INDEX 01 00:02:00\n')
    title_info = {"mode": "ko" if title_spec else "original-recompressed",
                  "region": [lo, hi], "stream_bytes": len(stream), "sectors": lbas}
    if title_spec:
        spec = json.loads(title_spec.read_text())
        title_info.update(spec=_rel(title_spec), spec_sha1=_sha1(title_spec),
                          logo_sha1=_sha1(title_spec.parent / spec["logo"]))
    manifest = {
        "cue": cue_name, "track1": o1, "track2": o2,
        "source": {"track1_sha1": profile.track1_sha1, "track2_sha1": profile.track2_sha1},
        "output": {"track1_sha1": _sha1(out_dir / o1), "track2_sha1": _sha1(out_dir / o2)},
        "title": title_info,
        "profile": asdict(profile),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return manifest


def build(source_cue: Path, out_dir: Path, title_spec: Path | None, profile: SourceProfile = None) -> dict:
    """Build into a fresh staging directory and replace out_dir only when every check passed."""
    profile = profile or JP_DISC1
    source_cue, out_dir = Path(source_cue), Path(out_dir)
    title_spec = Path(title_spec) if title_spec else None
    stage = out_dir.with_name(out_dir.name + ".partial")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    try:
        manifest = _build(source_cue, stage, title_spec, profile)
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
