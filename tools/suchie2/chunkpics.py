"""Single 4bpp pictures inside a chunk file (CLEAR.DAT after an ending, docs/initial-survey.md 3.21).

Each translated entry names one picture chunk; its layout entry is a select1 spec (box/clean/
allowed/lines, or regions). The pictures carry no palette of their own, so the redraw works on
palette indices only: a stand-in palette with one distinct colour per index lets label.py render
the fill/outline/shadow indices the layout names and map them back exactly. The new picture is
recompressed into its original slot (chunked.replace_in_place): every offset and every other
chunk stays; a stream that outgrows its slot fails the build.
"""
import hashlib
import json
from collections import Counter
from pathlib import Path

from . import chunked, label, lzss, select1
from .title import Texture

STAND_IN = [(k << 10) | (k << 5) | k for k in range(16)]      # one distinct RGB555 grey per index


class ChunkPicsError(ValueError):
    pass


def _picture(chunks, k: int, sid: str):
    if not 0 <= k < len(chunks):
        raise ChunkPicsError(f"{sid}: chunk {k} out of range 0..{len(chunks) - 1}")
    dec = lzss.decompress(chunks[k].stream)
    try:
        w, h, attr, pix = chunked.picture(dec)
    except chunked.ChunkError as err:
        raise ChunkPicsError(f"{sid}: chunk {k} is not a picture ({err})") from err
    if (attr >> 3) & 7 != 0:
        raise ChunkPicsError(f"{sid}: chunk {k} attr {attr:#x} is not 4bpp")
    return dec, w, h, attr, pix


def _named(v) -> list:
    """palette indices a fill/outline value names: int, list per segment, or {"v": [...]} gradient"""
    if isinstance(v, dict):
        return [i for u in v.get("v", []) for i in _named(u)]
    if isinstance(v, list):
        return [i for u in v for i in _named(u)]
    return [v]


def _regions(sid: str, spec: dict) -> list[dict]:
    """The spec's regions, validated so that every drawn pixel keeps an exact palette index."""
    if "use" in spec or "line_overrides" in spec:
        raise ChunkPicsError(f"{sid}: layout templates are not supported for picture chunks")
    if "regions" in spec and ({"box", "lines", "clean"} & set(spec)):
        raise ChunkPicsError(f"{sid}: a spec has either regions or box/lines, not both")
    regions = spec.get("regions") or [spec]
    for a in range(len(regions)):
        for b in range(a + 1, len(regions)):
            if select1._overlap(regions[a]["box"], regions[b]["box"]):
                raise ChunkPicsError(f"{sid}: regions {a} and {b} overlap")
    for r in regions:
        allowed = set(r["allowed"])
        for ln in r["lines"]:
            if ln.get("aa", True):
                raise ChunkPicsError(f"{sid}: picture chunks have no palette; lines need \"aa\": false")
            for key in ("shear", "vertical"):
                if ln.get(key):
                    raise ChunkPicsError(f"{sid}: {key} resamples the glyphs and cannot keep exact indices")
            named = _named(ln["fill"]) + _named(ln.get("outline", [])) + \
                ([ln["shadow"]["idx"]] if "shadow" in ln else [])
            bad = [i for i in named if not (isinstance(i, int) and 1 <= i <= 15 and i in allowed)]
            if bad:
                raise ChunkPicsError(f"{sid}: indices {bad} are not in the region's allowed {sorted(allowed)}")
    return regions


def _check(tr: dict, lay: dict, chunks, glossary: dict | None) -> None:
    prefix = tr.get("id_prefix", "")
    if prefix and not prefix.endswith("."):
        raise ChunkPicsError(f"id_prefix {prefix!r} must end with '.'")
    items, ex = tr["entries"], tr.get("excluded", [])
    ids = [e["id"] for e in items] + [e["id"] for e in ex]
    if len(set(ids)) != len(ids) or any(not i.startswith(prefix) for i in ids):
        raise ChunkPicsError(f"ids must be unique and start with id_prefix {prefix!r}")
    taken = Counter(e["chunk"] for e in items + ex)
    both = [k for k, n in taken.items() if n > 1]
    if both:
        raise ChunkPicsError(f"chunks listed twice (translated or excluded): {both}")
    for e in ex + items:               # every listed picture, translated or not, is pinned
        dec, w, h, _, _ = _picture(chunks, e["chunk"], e["id"])
        if hashlib.sha1(dec).hexdigest() != e["src_sha1"]:
            raise ChunkPicsError(f"{e['id']}: source picture differs from the translation baseline")
        if "size" in e and list(e["size"]) != [w, h]:
            raise ChunkPicsError(f"{e['id']}: size {e['size']} differs from the picture's {w}x{h}")
    lay_ids = [s["id"] for s in lay["entries"]]
    if len(set(lay_ids)) != len(lay_ids) or set(lay_ids) - {e["id"] for e in items}:
        raise ChunkPicsError(f"layout ids must be unique translated ids: {sorted(set(lay_ids) - {e['id'] for e in items})}")
    terms = {t["id"]: t["state"] for t in glossary["terms"]} if glossary else None
    for e in items:
        if e["state"] not in select1.STATES:
            raise ChunkPicsError(f"{e['id']}: unknown state {e['state']!r}")
        if e["state"] == "untranslated":
            if e["id"] in lay_ids:
                raise ChunkPicsError(f"{e['id']}: untranslated entries must not have a layout")
            if e["ko"]:
                raise ChunkPicsError(f"{e['id']}: ko must be empty for untranslated entries")
        elif e["id"] not in lay_ids:
            raise ChunkPicsError(f"{e['id']}: translated entry with no layout")
        elif any(not t.strip() for t in e["ko"].split("\n")):
            raise ChunkPicsError(f"{e['id']}: empty ko line would erase the original without drawing")
        for t in e["terms"] if terms is not None else ():
            if t not in terms:
                raise ChunkPicsError(f"{e['id']}: unknown term {t}")
            if e["state"] == "distribution_eligible" and terms[t] != "approved":
                raise ChunkPicsError(f"{e['id']}: eligible entry uses term {t} that is not approved")


def render(data: bytes, translation: Path, layout: Path, font: str = label.DEFAULT_FONT,
           glossary: Path | None = None, file_name: str | None = None,
           palette_data: bytes | None = None) -> tuple[bytes, select1.Result]:
    """Returns (new file bytes, Result with ids/states/distribution). palette_data is unused."""
    tr = json.loads(Path(translation).read_text())
    lay = json.loads(Path(layout).read_text())
    gl = json.loads(Path(glossary).read_text()) if glossary else None
    chunks = chunked.parse(data)
    _check(tr, lay, chunks, gl)
    by_id = {e["id"]: e for e in tr["entries"]}
    new = {}
    for spec in lay["entries"]:
        e = by_id[spec["id"]]
        dec, w, h, attr, pix = _picture(chunks, e["chunk"], e["id"])
        regions = _regions(e["id"], spec)
        texts = e["ko"].split("\n")
        if len(texts) != sum(len(r["lines"]) for r in regions):
            raise ChunkPicsError(f"{e['id']}: {len(texts)} translated lines but "
                                 f"{sum(len(r['lines']) for r in regions)} layout lines")
        tex = Texture(w, h, bytearray(pix), bpp=4)
        k = 0
        for r in regions:
            try:
                select1._apply_region(e["id"], tex, STAND_IN, r, texts[k:k + len(r["lines"])], font)
            except select1.Select1Error as err:
                raise ChunkPicsError(str(err)) from err
            k += len(r["lines"])
        named = {i for r in regions for ln in r["lines"] for i in _named(ln["fill"]) + _named(ln.get("outline", []))
                 + ([ln["shadow"]["idx"]] if "shadow" in ln else [])}
        changed = {tex.pixel(x, y) for y in range(h) for x in range(w)
                   if tex.pixel(x, y) != ((pix[(y * w + x) // 2] >> 4) if x % 2 == 0 else pix[(y * w + x) // 2] & 15)}
        if changed - named - {0}:
            raise ChunkPicsError(f"{e['id']}: drew indices {sorted(changed - named - {0})} the layout does not name")
        pic = chunked.make_picture(w, h, attr, bytes(tex.data))
        stream = lzss.compress(pic)
        if any(lzss.decompress(stream, stale=st) != pic for st in (None, b"\xa5" * lzss.STALE_LEN)):
            raise ChunkPicsError(f"{e['id']}: recompressed picture does not decode back")
        new[e["chunk"]] = stream
    try:
        out = chunked.build(chunked.replace_in_place(chunks, new), size=len(data))
    except chunked.ChunkError as err:
        raise ChunkPicsError(str(err)) from err
    res = select1.Result()
    res.ids = [s["id"] for s in lay["entries"]]
    res.states = dict(Counter(e["state"] for e in tr["entries"]))
    res.distribution = bool(res.states) and set(res.states) == {"distribution_eligible"}
    return out, res
