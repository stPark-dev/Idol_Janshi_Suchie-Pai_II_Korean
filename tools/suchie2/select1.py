"""Korean labels for the SELECT1.BIN sprite bundle (uncompressed Yc; file head = CRAM image).

Wording comes only from translation/select1.json; layout/style only from the layout spec.
Before rendering, the tables are checked against each other and against the source bundle:
unique ids and entry numbers, translated/excluded disjoint, exactly one layout per translated
entry, protected fields (texture SHA-1, colr, size, offset) equal to the bundle, colour mode 0
(CRAM bank), valid boxes, valid states, and approved glossary terms for eligible entries.
"""
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import label, yc
from .title import Texture

STATES = {"untranslated", "in_progress", "needs_review", "needs_human_review", "distribution_eligible"}


class Select1Error(ValueError):
    pass


@dataclass
class Result:
    textures: dict[int, bytes] = field(default_factory=dict)
    states: dict[str, int] = field(default_factory=dict)
    distribution: bool = False
    ids: list[str] = field(default_factory=list)


def palette_bytes(data: bytes, colr: int, cram_offset: int = 0) -> bytes:
    """The 32 bytes of colr's 16-colour bank in a CRAM image stored at cram_offset in the file."""
    start = cram_offset + (colr & 0x7F0) * 2
    if cram_offset < 0 or start + 32 > len(data):
        raise Select1Error(f"palette_offset {cram_offset:#x}: bank {colr & 0x7F0:#x} lies outside the file")
    return data[start:start + 32]


def palette(data: bytes, colr: int, cram_offset: int = 0) -> list[int]:
    raw = palette_bytes(data, colr, cram_offset)
    return [int.from_bytes(raw[k * 2:k * 2 + 2], "big") for k in range(16)]


def entries(data: bytes, bundle_offset: int) -> list[yc.Entry]:
    raw = data[bundle_offset:]
    return yc.parse(raw[:yc.length(raw)])


def offsets(data: bytes, bundle_offset: int) -> list[int]:
    ents = entries(data, bundle_offset)
    pos, out = bundle_offset + 4 + 8 * len(ents), []
    for e in ents:
        out.append(pos)
        pos += len(e.data)
    return out


def _check_box(sid: str, b, w: int, h: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = b
    if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
        raise Select1Error(f"{sid}: box {list(b)} outside texture {w}x{h}")
    return x0, y0, x1, y1


def _inside(a, b) -> bool:
    return b[0] <= a[0] and b[1] <= a[1] and a[2] <= b[2] and a[3] <= b[3]


def _overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _apply_region(sid: str, tex: Texture, pal: list[int], region: dict, texts: list[str], font: str) -> None:
    box = _check_box(sid, region["box"], tex.width, tex.height)
    if not set(region["allowed"]) <= set(range(1, 16)) or not region["allowed"]:
        raise Select1Error(f"{sid}: allowed indices must be a non-empty subset of 1..15")
    clean = region.get("clean")
    try:
        if clean:
            cbox = _check_box(sid, clean.get("box", box), tex.width, tex.height)
            if not _inside(cbox, box):
                raise Select1Error(f"{sid}: clean box {list(cbox)} extends outside box {list(box)}")
            if clean["method"] == "clear":
                label.clear_box(tex, cbox)
            elif clean["method"] == "rows":
                label.clean_rows(tex, cbox, set(clean["text_idx"]), borrow_rows=clean.get("borrow_rows"))
            elif clean["method"] == "tile":
                label.clean_tile(tex, cbox, set(clean["text_idx"]), tuple(clean["period"]))
            else:
                raise Select1Error(f"{sid}: unknown clean method {clean['method']}")
        lines = [{**style, "text": text} for style, text in zip(region["lines"], texts)]
        layer = label.render_lines((tex.width, tex.height), lines, pal, font, box=box)
        label.compose(tex, pal, box, layer, region["allowed"])
    except label.LabelError as err:
        raise Select1Error(f"{sid}: {err}") from err


def _unique(values, what: str) -> None:
    dup = [v for v, n in Counter(values).items() if n > 1]
    if dup:
        raise Select1Error(f"duplicate {what}: {dup}")


def _check_tables(tr: dict, lay: dict, ents: list[yc.Entry], offs: list[int], glossary: dict | None,
                  file_name: str | None, data: bytes = b"", cram: int = 0) -> None:
    items = tr["entries"]
    _unique([e["id"] for e in items], "translation id")
    _unique([e["entry"] for e in items], "translated entry number")
    _unique([s["id"] for s in lay["entries"]], "layout id")
    _unique([e["entry"] for e in tr.get("excluded", [])], "excluded entry number")
    bad = [e["entry"] for e in tr.get("excluded", []) if not 0 <= e["entry"] < len(ents)]
    if bad:
        raise Select1Error(f"excluded entries out of range 0..{len(ents) - 1}: {bad}")
    excluded = {e["entry"] for e in tr.get("excluded", [])} | {e["id"] for e in tr.get("excluded", [])}
    both = [e["id"] for e in items if e["entry"] in excluded or e["id"] in excluded]
    if both:
        raise Select1Error(f"entries both translated and excluded: {both}")
    ids = {e["id"] for e in items}
    lay_ids = {s["id"] for s in lay["entries"]}
    if lay_ids - ids:
        raise Select1Error(f"layout refers to unknown translation id {sorted(lay_ids - ids)}")
    pending = {e["id"] for e in items if e["state"] == "untranslated"}
    if pending & lay_ids:     # a layout would clean (wipe) the source art; 8bpp art would be garbled
        raise Select1Error(f"untranslated entries must not have a layout: {sorted(pending & lay_ids)}")
    filled = [e["id"] for e in items if e["state"] == "untranslated" and e["ko"]]
    if filled:
        raise Select1Error(f"ko must be empty for untranslated entries (set a review state instead): {filled}")
    if ids - lay_ids - pending:
        raise Select1Error(f"translated entries with no layout: {sorted(ids - lay_ids - pending)}")
    terms = {t["id"]: t["state"] for t in glossary["terms"]} if glossary else None
    for e in items:
        i = e["entry"]
        if not 0 <= i < len(ents):
            raise Select1Error(f"{e['id']}: entry {i} out of range 0..{len(ents) - 1}")
        src = ents[i]
        if (src.attr >> 3) & 7 != 0 and e["state"] != "untranslated":   # only redrawn entries must be 4bpp
            raise Select1Error(f"{e['id']}: colour mode {(src.attr >> 3) & 7} is not a 4bpp CRAM bank")
        if hashlib.sha1(src.data).hexdigest() != e["src_sha1"]:
            raise Select1Error(f"{e['id']}: source texture differs from the translation baseline")
        if int(e["colr"], 16) != src.colr:
            raise Select1Error(f"{e['id']}: colr {e['colr']} differs from bundle {src.colr:#x}")
        if list(e["size"]) != [src.width, src.height]:
            raise Select1Error(f"{e['id']}: size {e['size']} differs from bundle {src.width}x{src.height}")
        off = e["offset"]
        if isinstance(off, dict):
            if file_name not in off:
                raise Select1Error(f"{e['id']}: no offset recorded for file {file_name}")
            off = off[file_name]
        if int(off, 16) != offs[i]:
            raise Select1Error(f"{e['id']}: offset {off} differs from bundle {offs[i]:#x}")
        pal = palette_bytes(data, src.colr, cram)
        if "palette_sha1" in e and hashlib.sha1(pal).hexdigest() != e["palette_sha1"]:
            raise Select1Error(f"{e['id']}: palette bank {src.colr & 0x7F0:#x} differs from the translation baseline")
        if e["state"] not in STATES:
            raise Select1Error(f"{e['id']}: unknown state {e['state']!r}")
        if terms is not None:
            for t in e["terms"]:
                if t not in terms:
                    raise Select1Error(f"{e['id']}: unknown term {t}")
                if e["state"] == "distribution_eligible" and terms[t] != "approved":
                    raise Select1Error(f"{e['id']}: eligible entry uses term {t} that is not approved")
    classified = {e["entry"] for e in items} | {e["entry"] for e in tr.get("excluded", [])}
    missing = sorted(set(range(len(ents))) - classified)
    if missing:
        raise Select1Error(f"unclassified bundle entries (neither translated nor excluded): {missing}")


def render(data: bytes, translation: Path, layout: Path, font: str = label.DEFAULT_FONT,
           glossary: Path | None = None, file_name: str | None = None, palette_data: bytes | None = None) -> Result:
    """palette_data: the file holding the CRAM image when it is not the bundle's own file."""
    tr = json.loads(Path(translation).read_text())
    lay = json.loads(Path(layout).read_text())
    prefix = tr.get("id_prefix")
    if prefix is not None and not prefix.endswith("."):
        raise Select1Error(f"id_prefix {prefix!r} must end with '.'")
    if prefix:
        lay = {**lay, "entries": [s for s in lay["entries"] if s["id"].startswith(prefix)]}
        if any(not e["id"].startswith(prefix) for e in tr["entries"]):
            raise Select1Error(f"translation ids must start with id_prefix {prefix!r}")
    gl = json.loads(Path(glossary).read_text()) if glossary else None
    bundle = int(tr["bundle_offset"], 16)
    ents = entries(data, bundle)
    cram = int(tr.get("palette_offset", "0x0"), 16)
    pal_src = data if palette_data is None else palette_data
    _check_tables(tr, lay, ents, offsets(data, bundle), gl, file_name, pal_src, cram)
    by_id = {e["id"]: e for e in tr["entries"]}
    templates = lay.get("templates", {})
    res = Result()
    states = Counter()
    for spec in lay["entries"]:
        if "use" in spec:
            if spec["use"] not in templates:
                raise Select1Error(f"{spec['id']}: unknown layout template {spec['use']!r}")
            tpl = templates[spec["use"]]
            if "regions" in tpl and ({"lines", "box"} & set(spec)):
                raise Select1Error(f"{spec['id']}: template {spec['use']!r} uses regions; entry cannot add lines/box")
            spec = {**tpl, **{k: v for k, v in spec.items() if k not in ("use", "line_overrides")},
                    "line_overrides": spec.get("line_overrides", {})}
        if "regions" in spec and ({"lines", "box"} & set(spec)):
            raise Select1Error(f"{spec['id']}: a spec has either regions or box/lines, not both")
        if spec.get("line_overrides"):
            if "lines" not in spec:
                raise Select1Error(f"{spec['id']}: line_overrides needs a box/lines spec")
            lines = [dict(ln) for ln in spec["lines"]]
            for k, over in spec["line_overrides"].items():
                if not (isinstance(k, str) and k.isdigit() and str(int(k)) == k and int(k) < len(lines)):
                    raise Select1Error(f"{spec['id']}: bad line_overrides index {k!r}")
                lines[int(k)].update(over)
            spec = {**spec, "lines": lines}
        t = by_id[spec["id"]]
        i = t["entry"]
        src = ents[i]
        texts = t["ko"].split("\n")
        regions = spec.get("regions") or [spec]
        n_lines = sum(len(r["lines"]) for r in regions)
        if len(texts) != n_lines:
            raise Select1Error(f"{spec['id']}: {len(texts)} translated lines but {n_lines} layout lines")
        for a in range(len(regions)):
            for b in range(a + 1, len(regions)):
                if _overlap(regions[a]["box"], regions[b]["box"]):
                    raise Select1Error(f"{spec['id']}: regions {a} and {b} overlap")
        pal = palette(pal_src, src.colr, cram)
        tex = Texture(src.width, src.height, bytearray(src.data))
        k = 0
        for region in regions:
            _apply_region(spec["id"], tex, pal, region, texts[k:k + len(region["lines"])], font)
            k += len(region["lines"])
        res.textures[i] = bytes(tex.data)
        res.ids.append(spec["id"])
        states[t["state"]] += 1
    for t in tr["entries"]:                       # untranslated entries keep their source texture
        if t["state"] == "untranslated" and t["id"] not in res.ids:
            states["untranslated"] += 1
    res.states = dict(states)
    res.distribution = bool(states) and set(states) == {"distribution_eligible"}
    return res
