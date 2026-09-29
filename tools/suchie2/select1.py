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


def palette(data: bytes, colr: int) -> list[int]:
    base = colr & 0x7F0
    return [int.from_bytes(data[(base + k) * 2:(base + k) * 2 + 2], "big") for k in range(16)]


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
                label.clean_rows(tex, cbox, set(clean["text_idx"]))
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


def _check_tables(tr: dict, lay: dict, ents: list[yc.Entry], offs: list[int], glossary: dict | None) -> None:
    items = tr["entries"]
    _unique([e["id"] for e in items], "translation id")
    _unique([e["entry"] for e in items], "translated entry number")
    _unique([s["id"] for s in lay["entries"]], "layout id")
    excluded = {e["entry"] for e in tr.get("excluded", [])} | {e["id"] for e in tr.get("excluded", [])}
    both = [e["id"] for e in items if e["entry"] in excluded or e["id"] in excluded]
    if both:
        raise Select1Error(f"entries both translated and excluded: {both}")
    ids = {e["id"] for e in items}
    lay_ids = {s["id"] for s in lay["entries"]}
    if lay_ids - ids:
        raise Select1Error(f"layout refers to unknown translation id {sorted(lay_ids - ids)}")
    if ids - lay_ids:
        raise Select1Error(f"translated entries with no layout: {sorted(ids - lay_ids)}")
    terms = {t["id"]: t["state"] for t in glossary["terms"]} if glossary else None
    for e in items:
        i = e["entry"]
        if not 0 <= i < len(ents):
            raise Select1Error(f"{e['id']}: entry {i} out of range 0..{len(ents) - 1}")
        src = ents[i]
        if (src.attr >> 3) & 7 != 0:
            raise Select1Error(f"{e['id']}: colour mode {(src.attr >> 3) & 7} is not a 4bpp CRAM bank")
        if hashlib.sha1(src.data).hexdigest() != e["src_sha1"]:
            raise Select1Error(f"{e['id']}: source texture differs from the translation baseline")
        if int(e["colr"], 16) != src.colr:
            raise Select1Error(f"{e['id']}: colr {e['colr']} differs from bundle {src.colr:#x}")
        if list(e["size"]) != [src.width, src.height]:
            raise Select1Error(f"{e['id']}: size {e['size']} differs from bundle {src.width}x{src.height}")
        if int(e["offset"], 16) != offs[i]:
            raise Select1Error(f"{e['id']}: offset {e['offset']} differs from bundle {offs[i]:#x}")
        if e["state"] not in STATES:
            raise Select1Error(f"{e['id']}: unknown state {e['state']!r}")
        if terms is not None:
            for t in e["terms"]:
                if t not in terms:
                    raise Select1Error(f"{e['id']}: unknown term {t}")
                if e["state"] == "distribution_eligible" and terms[t] != "approved":
                    raise Select1Error(f"{e['id']}: eligible entry uses term {t} that is not approved")


def render(data: bytes, translation: Path, layout: Path, font: str = label.DEFAULT_FONT,
           glossary: Path | None = None) -> Result:
    tr = json.loads(Path(translation).read_text())
    lay = json.loads(Path(layout).read_text())
    gl = json.loads(Path(glossary).read_text()) if glossary else None
    bundle = int(tr["bundle_offset"], 16)
    ents = entries(data, bundle)
    _check_tables(tr, lay, ents, offsets(data, bundle), gl)
    by_id = {e["id"]: e for e in tr["entries"]}
    res = Result()
    states = Counter()
    for spec in lay["entries"]:
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
        pal = palette(data, src.colr)
        tex = Texture(src.width, src.height, bytearray(src.data))
        k = 0
        for region in regions:
            _apply_region(spec["id"], tex, pal, region, texts[k:k + len(region["lines"])], font)
            k += len(region["lines"])
        res.textures[i] = bytes(tex.data)
        res.ids.append(spec["id"])
        states[t["state"]] += 1
    res.states = dict(states)
    res.distribution = bool(states) and set(states) == {"distribution_eligible"}
    return res
