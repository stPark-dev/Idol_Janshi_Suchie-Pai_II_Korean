"""Primary product build: source BIN/CUE -> patched BIN/CUE + manifest.json.

Current scope: the title-screen sprite bundle inside PROLOG.BIN (LZSS-compressed Yc block) and
the menu sprite bundle SELECT1.BIN (uncompressed Yc). All output changes go through one
WritePlan over the raw Track 1 image, one write per touched sector.
"""
import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image

from . import cdsector, iso9660, lzss, select1 as select1_mod, title, yc
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
    },
)
STAGE_FILES = list(JP_DISC1.files)


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


def _plan_bundle(t1: _Track1, profile: SourceProfile, job: dict) -> list[tuple[dict, list]]:
    """Render one translation/layout pair into every listed file (all protected checks run per
    file). Returns (info, changes) per file; sector writes are registered later, once per file."""
    translation, layout = Path(job["translation"]), Path(job["layout"])
    glossary = Path(job["glossary"]) if job.get("glossary") else None
    font = json.loads(layout.read_text()).get("font", select1_mod.label.DEFAULT_FONT)
    bundle = int(json.loads(translation.read_text())["bundle_offset"], 16)
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
        res = select1_mod.render(data, translation, layout, font=font, glossary=glossary, file_name=name)
        changes = [(offsets[i], tex) for i, tex in sorted(res.textures.items()) if tex != ents[i].data]
        info = {"file": name, "lba": lba, "entries": res.ids, "states": res.states, "distribution": res.distribution,
                "changes": [[o, len(t)] for o, t in changes], "sectors": [],
                "translation": _rel(translation), "translation_sha1": _sha1(translation),
                "layout": _rel(layout), "layout_sha1": _sha1(layout),
                "glossary": _rel(glossary) if glossary else None, "glossary_sha1": _sha1(glossary) if glossary else None,
                "font": font, "font_sha1": _sha1(Path(font))}
        out.append((info, changes))
    return out


def _build(source_cue: Path, out_dir: Path, title_spec: Path | None, profile: SourceProfile,
           select1=None, bundles=None, title: bool = True) -> dict:
    tr1, tr2 = _cue_tracks(source_cue)
    if _sha1(tr1) != profile.track1_sha1:
        raise BuildError(f"Track 1 SHA-1 mismatch: {tr1}")
    if _sha1(tr2) != profile.track2_sha1:
        raise BuildError(f"Track 2 SHA-1 mismatch: {tr2}")
    if title_spec and not title:
        raise BuildError("a title spec was given but the title bundle is disabled")
    jobs = list(bundles or [])
    if select1:
        jobs.insert(0, {"files": [profile.select1_name or "SELECT1.BIN"], "translation": select1[0], "layout": select1[1],
                        "glossary": select1[2] if len(select1) > 2 else None, "_select1": True})
    lo, hi = profile.title_region
    t1 = _Track1(tr1)
    try:
        extents = [_file_extent(profile, n) for job in jobs for n in job["files"]]
        if title:
            if not 0 <= lo < hi <= profile.prolog_size:
                raise BuildError(f"title region {lo:#x}-{hi:#x} outside {profile.prolog_name}")
            prolog_lba, size = iso9660.find_file(t1.user, profile.prolog_name)
            if (prolog_lba, size) != (profile.prolog_lba, profile.prolog_size):
                raise BuildError(f"{profile.prolog_name} extent {prolog_lba}/{size} differs from profile")
            extents.append((prolog_lba, size))
        plan = WritePlan(tr1, protected=_outside(extents, tr1.stat().st_size))
        if title:
            region = _read_file_range(t1, prolog_lba, lo, hi)
            if hashlib.sha1(region).hexdigest() != profile.title_region_sha1:
                raise BuildError("title region bytes differ from the supported source")
            entries = yc.parse(lzss.decompress(region))
            new_entries = compose_title(entries, title_spec) if title_spec else entries
            tbundle = yc.build(new_entries)
            stream = lzss.compress(tbundle)
            if lzss.decompress(stream) != tbundle or lzss.decompress(stream, stale=b"\xa5" * lzss.STALE_LEN) != tbundle:
                raise BuildError("recompressed title block does not round-trip")
            if len(stream) > hi - lo:
                raise BuildError(f"title block {len(stream)} bytes exceeds region {hi - lo}")
            lbas = _file_writes(plan, t1, prolog_lba, [(lo, stream + bytes(hi - lo - len(stream)))], "title")
        results = [(job, _plan_bundle(t1, profile, job)) for job in jobs]
        per_file: dict[str, list] = {}
        for _, infos in results:
            for info, changes in infos:
                per_file.setdefault(info["file"], []).append((info, changes))
        for name, items in per_file.items():
            lba = items[0][0]["lba"]
            sectors = _file_writes(plan, t1, lba, [c for _, ch in items for c in ch], name)
            for info, ch in items:
                touched = {lba + (o + k) // cdsector.USER for o, t in ch for k in (0, len(t) - 1)}
                info["sectors"] = [x for x in sectors if min(touched, default=-1) <= x <= max(touched, default=-1)]
    finally:
        t1.close()

    o1, o2 = f"{OUT_STEM} (Track 1).bin", f"{OUT_STEM} (Track 2).bin"
    plan.apply(out_dir / o1)
    shutil.copyfile(tr2, out_dir / o2)
    if _sha1(out_dir / o2) != profile.track2_sha1:
        raise BuildError("Track 2 copy differs from source")
    if title:
        _verify_output(out_dir / o1, prolog_lba, lo, hi, stream, tbundle, lbas)
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
        title_info = {"mode": "ko" if title_spec else "original-recompressed",
                      "region": [lo, hi], "stream_bytes": len(stream), "sectors": lbas}
        if title_spec:
            spec = json.loads(title_spec.read_text())
            title_info.update(spec=_rel(title_spec), spec_sha1=_sha1(title_spec),
                              logo_sha1=_sha1(title_spec.parent / spec["logo"]),
                              presentation=spec.get("presentation", "needs_human_review"))
    title_ok = title_spec is None or json.loads(title_spec.read_text()).get("presentation") == "approved"
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
          select1=None, bundles=None, title: bool = True) -> dict:
    """Build into a fresh staging directory and replace out_dir only when every check passed."""
    profile = profile or JP_DISC1
    source_cue, out_dir = Path(source_cue), Path(out_dir)
    title_spec = Path(title_spec) if title_spec else None
    stage = out_dir.with_name(out_dir.name + ".partial")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    try:
        manifest = _build(source_cue, stage, title_spec, profile, select1, bundles, title)
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
