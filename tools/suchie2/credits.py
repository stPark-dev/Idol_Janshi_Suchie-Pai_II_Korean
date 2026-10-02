"""Ending credit roll (ENDY chunks 76-112, docs/initial-survey.md 3.20).

The roll is one tall 4bpp picture cut into equal pictures, stored as consecutive chunks of a
chunk file (chunked.py). Lines cross the cuts, so the pictures are stacked into one texture, the
layout's regions are cleaned and redrawn on it like select1 regions (rows of the roll), and the
texture is cut again. Only chunks whose pixels changed are recompressed, each into its original
slot (a larger stream fails the build), so every chunk offset and every other chunk stays; the
unused rest of a rewritten slot (0-3 alignment bytes in the original) becomes zeros.
"""
import hashlib
import json
from collections import Counter
from pathlib import Path

from . import chunked, label, lzss, select1
from .title import Texture


class CreditsError(ValueError):
    pass


def _pictures(chunks, tr) -> tuple[list[int], int, int, int, list[bytes]]:
    first, last = tr["chunks"]
    if not 0 <= first <= last < len(chunks):
        raise CreditsError(f"chunks {first}..{last} out of range 0..{len(chunks) - 1}")
    w, h, attr = tr["picture"][0], tr["picture"][1], int(tr["picture"][2], 16)
    if (attr >> 3) & 7 != 0:
        raise CreditsError(f"roll attr {attr:#x}: only 4bpp (16-colour bank) rolls are supported")
    ks = list(range(first, last + 1))
    if len(tr["src_sha1"]) != len(ks):
        raise CreditsError(f"{len(ks)} chunks but {len(tr['src_sha1'])} src_sha1")
    pix = []
    for k, sha in zip(ks, tr["src_sha1"]):
        dec = lzss.decompress(chunks[k].stream)
        if hashlib.sha1(dec).hexdigest() != sha:
            raise CreditsError(f"chunk {k}: source picture differs from the translation baseline")
        try:
            got = chunked.picture(dec)
        except chunked.ChunkError as err:
            raise CreditsError(f"chunk {k}: not a roll picture ({err})") from err
        if got[:3] != (w, h, attr):
            raise CreditsError(f"chunk {k}: picture {got[:3]} differs from the roll's {(w, h, attr)}")
        pix.append(got[3])
    return ks, w, h, attr, pix


def _check(tr: dict, lay: dict, glossary: dict | None) -> None:
    prefix = tr.get("id_prefix", "")
    if prefix and not prefix.endswith("."):
        raise CreditsError(f"id_prefix {prefix!r} must end with '.'")
    items = tr["entries"]
    ids = [e["id"] for e in items]
    if len(set(ids)) != len(ids) or any(not i.startswith(prefix) for i in ids):
        raise CreditsError(f"line ids must be unique and start with id_prefix {prefix!r}")
    if any(not r["ids"] for r in lay["regions"]):
        raise CreditsError("a region with no line ids would erase the original without drawing")
    ex = [e["id"] for e in tr.get("excluded", [])]
    if len(set(ex)) != len(ex):
        raise CreditsError(f"duplicate excluded ids: {sorted(i for i in set(ex) if ex.count(i) > 1)}")
    if set(ex) & set(ids):
        raise CreditsError(f"lines both translated and excluded: {sorted(set(ex) & set(ids))}")
    placed = Counter(i for r in lay["regions"] for i in r["ids"])
    if set(placed) - set(ids):
        raise CreditsError(f"layout refers to unknown line id {sorted(set(placed) - set(ids))}")
    twice = [i for i, n in placed.items() if n > 1]
    if twice:
        raise CreditsError(f"lines placed in more than one region: {twice}")
    terms = {t["id"]: t["state"] for t in glossary["terms"]} if glossary else None
    for e in items:
        if e["state"] not in select1.STATES:
            raise CreditsError(f"{e['id']}: unknown state {e['state']!r}")
        if e["state"] == "untranslated":
            if e["id"] in placed:
                raise CreditsError(f"{e['id']}: untranslated lines must not be in a region")
            if e["ko"]:
                raise CreditsError(f"{e['id']}: ko must be empty for untranslated lines")
        elif e["id"] not in placed:
            raise CreditsError(f"{e['id']}: translated line in no region")
        elif any(not t.strip() for t in e["ko"].split("\n")):
            raise CreditsError(f"{e['id']}: empty ko line would erase the original without drawing")
        for t in e["terms"] if terms is not None else ():
            if t not in terms:
                raise CreditsError(f"{e['id']}: unknown term {t}")
            if e["state"] == "distribution_eligible" and terms[t] != "approved":
                raise CreditsError(f"{e['id']}: eligible line uses term {t} that is not approved")
    regs = lay["regions"]
    for a in range(len(regs)):
        for b in range(a + 1, len(regs)):
            if select1._overlap(regs[a]["box"], regs[b]["box"]):
                raise CreditsError(f"regions {regs[a]['ids']} and {regs[b]['ids']} overlap")


def render(data: bytes, translation: Path, layout: Path, font: str = label.DEFAULT_FONT,
           glossary: Path | None = None, file_name: str | None = None,
           palette_data: bytes | None = None) -> tuple[bytes, select1.Result]:
    """Returns (new file bytes, Result with ids/states/distribution; textures stays empty)."""
    tr = json.loads(Path(translation).read_text())
    lay = json.loads(Path(layout).read_text())
    gl = json.loads(Path(glossary).read_text()) if glossary else None
    chunks = chunked.parse(data)
    ks, w, h, attr, pix = _pictures(chunks, tr)
    colr = int(tr["colr"], 16)
    if palette_data is None:
        raise CreditsError("the roll's palette file is required")
    raw_pal = select1.palette_bytes(palette_data, colr, int(tr["palette_offset"], 16))
    if hashlib.sha1(raw_pal).hexdigest() != tr["palette_sha1"]:
        raise CreditsError("palette differs from the translation baseline")
    pal = select1.palette(palette_data, colr, int(tr["palette_offset"], 16))
    _check(tr, lay, gl)
    by_id = {e["id"]: e for e in tr["entries"]}
    tex = Texture(w, h * len(ks), bytearray(b"".join(pix)), bpp=4)
    for r in lay["regions"]:
        texts = [t for i in r["ids"] for t in by_id[i]["ko"].split("\n")]
        if len(texts) != len(r["lines"]):
            raise CreditsError(f"{r['ids']}: {len(texts)} translated lines but {len(r['lines'])} layout lines")
        try:
            select1._apply_region(",".join(r["ids"]), tex, pal, r, texts, font)
        except select1.Select1Error as err:
            raise CreditsError(str(err)) from err
    size = w * h // 2
    new = {}
    for j, k in enumerate(ks):
        cut = bytes(tex.data[j * size:(j + 1) * size])
        if cut != pix[j]:
            pic = chunked.make_picture(w, h, attr, cut)
            stream = lzss.compress(pic)
            if any(lzss.decompress(stream, stale=st) != pic for st in (None, b"\xa5" * lzss.STALE_LEN)):
                raise CreditsError(f"chunk {k}: recompressed picture does not decode back")    # the game keeps a stale window tail
            new[k] = stream
    try:
        out = chunked.build(chunked.replace_in_place(chunks, new), size=len(data))   # every offset kept
    except chunked.ChunkError as err:
        raise CreditsError(str(err)) from err
    res = select1.Result()
    res.ids = [i for r in lay["regions"] for i in r["ids"]]
    res.states = dict(Counter(e["state"] for e in tr["entries"]))
    res.distribution = bool(res.states) and set(res.states) == {"distribution_eligible"}
    return out, res
