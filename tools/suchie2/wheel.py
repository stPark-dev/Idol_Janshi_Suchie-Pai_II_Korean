"""Roulette wheel of the panel match (PMATCH.BIN, docs/initial-survey.md 3.18).

One wheel is a 256x256 8bpp (256-colour) picture split over four 128x128 Yc entries in the order
TL, TR, BL, BR. Eight wedges of 45 degrees; angles run counter-clockwise from east. A wedge's text
reads upright when the wedge points down (270 degrees), with the first line nearest the hub.

1. Clean: inside the wedge, every pixel between r0 and r1 takes the most common colour of its
   1-px ring among colours close to the wedge's base colour (the text is far from it). Between r1
   and rim (the glossy rim gradient) only pixels whose colour does not occur in the same ring
   near the wedge edges are replaced.
2. Draw: the Korean lines are rendered upright in the wedge-points-down frame and rotated to the
   wedge angle with nearest-neighbour sampling (palette indices stay exact). Ink outside the
   wedge is an error, never clipped.
"""
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from PIL import Image

from . import label, select1
from .title import rgb555

SIDE, QUAD = 256, 128
MODE_256 = 4
ORIGINS = ((0, 0), (QUAD, 0), (0, QUAD), (QUAD, QUAD))       # TL, TR, BL, BR
WEDGE = 45                                                     # degrees per wedge; centres on multiples of it
LINE_KEYS = {"size", "fill", "outline", "weight"}


class WheelError(ValueError):
    pass


def assemble(quads) -> list[bytearray]:
    if len(quads) != 4 or any(len(q) != QUAD * QUAD for q in quads):
        raise WheelError("a wheel needs four 128x128 8bpp quadrants")
    W = [bytearray(SIDE) for _ in range(SIDE)]
    for q, (ox, oy) in zip(quads, ORIGINS):
        for y in range(QUAD):
            W[oy + y][ox:ox + QUAD] = q[y * QUAD:(y + 1) * QUAD]
    return W


def split(W) -> list[bytes]:
    return [b"".join(bytes(W[oy + y][ox:ox + QUAD]) for y in range(QUAD)) for ox, oy in ORIGINS]


def polar(x: int, y: int) -> tuple[float, float]:
    dx, dy = x + 0.5 - SIDE / 2, SIDE / 2 - (y + 0.5)
    return math.hypot(dx, dy), math.degrees(math.atan2(dy, dx)) % 360


def _da(a: float, centre: float) -> float:
    return (a - centre + 180) % 360 - 180


def in_wedge(x: int, y: int, centre: float, r0: float, r1: float, half: float) -> bool:
    r, a = polar(x, y)
    return r0 <= r <= r1 and abs(_da(a, centre)) < half


def _dist(a, b) -> float:
    return math.dist(a, b)


def clean_wedge(W, pal_rgb, centre: float, r0: float, r1: float, rim: float, half: float, dmax: float) -> None:
    cells = []
    for y in range(SIDE):
        for x in range(SIDE):
            r, a = polar(x, y)
            da = abs(_da(a, centre))
            if r0 - 4 <= r <= rim and da < half:
                cells.append((x, y, int(r), da))
    mid = (r0 + r1) / 2
    base = pal_rgb[Counter(W[y][x] for x, y, r, _ in cells if abs(r - mid) <= 10).most_common(1)[0][0]]
    ring, edge = {}, {}
    for x, y, r, da in cells:
        c = W[y][x]
        if da >= half - 4:
            edge.setdefault(r, Counter())[c] += 1
        if _dist(pal_rgb[c], base) <= dmax:
            ring.setdefault(r, Counter())[c] += 1
    for x, y, r, _ in cells:
        if r0 <= r <= r1:
            k = next((r + s for s in (0, 1, -1, 2, -2, 3, -3, 4, -4) if r + s in ring), None)
            if k is None:
                raise WheelError(f"wedge {centre}: no background colour near radius {r}")
            W[y][x] = ring[k].most_common(1)[0][0]
        elif r1 < r <= rim and not any(W[y][x] in edge.get(r + s, ()) for s in (-1, 0, 1)):
            W[y][x] = edge[r].most_common(1)[0][0]


def _indices(v) -> list[int]:
    return [i for u in v for i in _indices(u)] if isinstance(v, list) else [v]


def draw_wedge(W, pal, centre: float, lines, texts, r, ink, font: str = label.DEFAULT_FONT) -> None:
    """pal: 256 RGB555 words; lines: label styles with palette indices (fill, outline)."""
    r0, r1 = r
    n = len(lines)
    rows = []
    for k, (style, text) in enumerate(zip(lines, texts)):
        y0 = SIDE // 2 + r0 + (r1 - r0) * k // n
        y1 = SIDE // 2 + r0 + (r1 - r0) * (k + 1) // n
        rows.append({**style, "text": text, "x": "center", "y": "center", "area": [0, y0, SIDE, y1], "aa": False})
    try:
        layer = label.render_lines((SIDE, SIDE), rows, pal, font)
    except label.LabelError as err:
        raise WheelError(f"{texts}: text leaves the canvas ({err})") from err
    layer = layer.rotate(centre - 270, resample=Image.NEAREST, center=(SIDE // 2, SIDE // 2))
    used = {rgb555(pal[i]): i for s in lines for key in ("fill", "outline") if key in s for i in _indices(s[key])}
    px = layer.load()
    for y in range(SIDE):
        for x in range(SIDE):
            cr, cg, cb, ca = px[x, y]
            if ca < 128:
                continue
            if not in_wedge(x, y, centre, *ink):
                raise WheelError(f"{texts}: text leaves the wedge at {centre} degrees ({x},{y})")
            W[y][x] = used[(cr, cg, cb)]


def _rgb(v) -> bool:
    return isinstance(v, list) and len(v) == 3 and all(isinstance(c, int) for c in v)


def _resolve(v, lookup, where: str):
    if _rgb(v):
        if tuple(v) not in lookup:
            raise WheelError(f"{where}: colour {v} is not in the wheel palette")
        return lookup[tuple(v)]
    if isinstance(v, list) and v and all(_rgb(u) for u in v):
        return [_resolve(u, lookup, where) for u in v]
    raise WheelError(f"{where}: colour must be [r, g, b] or a list of them per '|' segment, got {v!r}")


def _check_wheel(w: dict, ents, data: bytes, cram: int) -> None:
    name, nums = w["name"], w["entries"]
    if len(nums) != 4:
        raise WheelError(f"wheel {name}: needs four entries (TL, TR, BL, BR), got {len(nums)}")
    if len(set(nums)) != 4:
        raise WheelError(f"wheel {name}: duplicate entries {nums}")
    if len(w["src_sha1"]) != 4:
        raise WheelError(f"wheel {name}: needs four src_sha1, got {len(w['src_sha1'])}")
    if any(not 0 <= i < len(ents) for i in nums):
        raise WheelError(f"wheel {name}: entries {nums} out of range 0..{len(ents) - 1}")
    colr = int(w["colr"], 16)
    for i, sha in zip(nums, w["src_sha1"]):
        e = ents[i]
        if (e.attr >> 3) & 7 != MODE_256 or (e.width, e.height) != (QUAD, QUAD):
            raise WheelError(f"wheel {name}: entry {i} is not a 256-colour 128x128 texture")
        if e.colr != colr:
            raise WheelError(f"wheel {name}: entry {i} colr {e.colr:#x} differs from {w['colr']}")
        if hashlib.sha1(e.data).hexdigest() != sha:
            raise WheelError(f"wheel {name}: entry {i} source texture differs from the translation baseline")
    if hashlib.sha1(select1.palette_bytes(data, colr, cram, 256)).hexdigest() != w["palette_sha1"]:
        raise WheelError(f"wheel {name}: palette differs from the translation baseline")


def _check_geometry(lay: dict) -> None:
    c, k = lay["clean"], lay["ink"]
    if set(c) != {"r0", "r1", "rim", "half", "dmax"} or not (
            0 < c["r0"] < c["r1"] <= c["rim"] <= SIDE // 2 and 0 < c["half"] <= WEDGE / 2 and c["dmax"] > 0):
        raise WheelError(f"clean {c} needs r0, r1, rim, half, dmax with 0 < r0 < r1 <= rim <= 128, "
                         f"0 < half <= 22.5, dmax > 0")
    if set(k) != {"r0", "r1", "half"} or not (0 < k["r0"] < k["r1"] <= SIDE // 2 and 0 < k["half"] <= WEDGE / 2):
        raise WheelError(f"ink {k} needs r0, r1, half with 0 < r0 < r1 <= 128, 0 < half <= 22.5")
    for s in lay["entries"]:
        r0, r1 = s["r"]
        if not 0 < r0 < r1 <= SIDE // 2:
            raise WheelError(f"{s['id']}: r {s['r']} must satisfy 0 < r0 < r1 <= 128")
        extra = sorted({key for ln in s["lines"] for key in ln} - LINE_KEYS)
        if extra:
            raise WheelError(f"{s['id']}: unsupported line keys {extra} (allowed {sorted(LINE_KEYS)})")
        for name, w in lay["wheels"].items():
            if (s["angle"] + w["rotation"]) % WEDGE:
                raise WheelError(f"{s['id']}: angle {s['angle']} on wheel {name} is off the {WEDGE}-degree wedge grid")


def _check_entries(tr: dict, lay: dict, glossary: dict | None) -> None:
    items = tr["entries"]
    ids = [e["id"] for e in items]
    if len(set(ids)) != len(ids) or len({s["id"] for s in lay["entries"]}) != len(lay["entries"]):
        raise WheelError("duplicate ids in the translation or layout")
    lay_ids = {s["id"] for s in lay["entries"]}
    if lay_ids - set(ids):
        raise WheelError(f"layout refers to unknown translation id {sorted(lay_ids - set(ids))}")
    terms = {t["id"]: t["state"] for t in glossary["terms"]} if glossary else None
    for e in items:
        if e["state"] not in select1.STATES:
            raise WheelError(f"{e['id']}: unknown state {e['state']!r}")
        if e["state"] == "untranslated":
            if e["id"] in lay_ids:
                raise WheelError(f"{e['id']}: untranslated entries must not have a layout")
            if e["ko"]:
                raise WheelError(f"{e['id']}: ko must be empty for untranslated entries")
        elif e["id"] not in lay_ids:
            raise WheelError(f"{e['id']}: translated entry with no layout")
        elif any(not t.strip() for t in e["ko"].split("\n")):
            raise WheelError(f"{e['id']}: empty line in ko would erase the wedge text without drawing any")
        for t in e["terms"] if terms is not None else ():
            if t not in terms:
                raise WheelError(f"{e['id']}: unknown term {t}")
            if e["state"] == "distribution_eligible" and terms[t] != "approved":
                raise WheelError(f"{e['id']}: eligible entry uses term {t} that is not approved")
    specs = lay["entries"]
    half = lay["ink"]["half"]
    for a in range(len(specs)):
        for b in range(a + 1, len(specs)):
            if abs(_da(specs[a]["angle"], specs[b]["angle"])) < 2 * half:
                raise WheelError(f"wedges {specs[a]['id']} and {specs[b]['id']} overlap")


def render(data: bytes, translation: Path, layout: Path, font: str = label.DEFAULT_FONT,
           glossary: Path | None = None, file_name: str | None = None) -> select1.Result:
    tr = json.loads(Path(translation).read_text())
    lay = json.loads(Path(layout).read_text())
    prefix = tr.get("id_prefix", "")
    if prefix and not prefix.endswith("."):
        raise WheelError(f"id_prefix {prefix!r} must end with '.'")
    if any(not e["id"].startswith(prefix) for e in tr["entries"]):
        raise WheelError(f"translation ids must start with id_prefix {prefix!r}")
    gl = json.loads(Path(glossary).read_text()) if glossary else None
    ents = select1.entries(data, int(tr["bundle_offset"], 16))
    cram = int(tr.get("palette_offset", "0x0"), 16)
    if not tr["wheels"]:
        raise WheelError("the table lists no wheels")
    names = [w["name"] for w in tr["wheels"]]
    if sorted(names) != sorted(lay["wheels"]) or len(set(names)) != len(names):
        raise WheelError(f"layout wheels {sorted(lay['wheels'])} differ from the table's wheels {names}")
    for w in tr["wheels"]:
        _check_wheel(w, ents, data, cram)
    owned = [i for w in tr["wheels"] for i in w["entries"]]
    if len(set(owned)) != len(owned):
        raise WheelError(f"wheels share entries: {sorted(i for i in set(owned) if owned.count(i) > 1)}")
    _check_geometry(lay)
    _check_entries(tr, lay, gl)
    by_id = {e["id"]: e for e in tr["entries"]}
    for s in lay["entries"]:
        if len(by_id[s["id"]]["ko"].split("\n")) != len(s["lines"]):
            raise WheelError(f"{s['id']}: translated lines differ from the {len(s['lines'])} layout lines")
    res = select1.Result()
    for w in tr["wheels"]:
        if not lay["entries"]:
            break
        pal = select1.palette(data, int(w["colr"], 16), cram, colours=256)
        lookup = {}
        for k in range(len(pal) - 1, 0, -1):          # index 0 is transparent; lowest index wins
            lookup[rgb555(pal[k])] = k
        W = assemble([ents[i].data for i in w["entries"]])
        for s in lay["entries"]:
            where = f"{s['id']} (wheel {w['name']})"
            lines = [{**ln, **{k: _resolve(ln[k], lookup, where) for k in ("fill", "outline") if k in ln}}
                     for ln in s["lines"]]
            centre = (s["angle"] + lay["wheels"][w["name"]]["rotation"]) % 360
            clean_wedge(W, [rgb555(c) for c in pal], centre, **lay["clean"])
            ink = (lay["ink"]["r0"], lay["ink"]["r1"], lay["ink"]["half"])
            try:
                draw_wedge(W, pal, centre, lines, by_id[s["id"]]["ko"].split("\n"), s["r"], ink, font)
            except WheelError as err:
                raise WheelError(f"{where}: {err}") from err
        for i, q in zip(w["entries"], split(W)):
            res.textures[i] = q
    res.ids = [s["id"] for s in lay["entries"]]
    res.states = dict(Counter(e["state"] for e in tr["entries"]))
    res.distribution = bool(res.states) and set(res.states) == {"distribution_eligible"}
    return res
