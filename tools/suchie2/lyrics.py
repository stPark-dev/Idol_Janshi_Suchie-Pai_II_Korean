"""Song-lyric lines under the Yuki ending's credit roll (ED_YUKI.DAT chunks 14..31, docs/initial-survey.md 3.22).

Each line is one 4bpp chunk picture (256x16) whose palette index is an antialias level of the same
ink: 1 = full ink .. 15 = faintest, 0 = transparent (measured on the originals: stroke cores are 1,
edges climb towards 15). A translated line is rasterised in greyscale, centred, and its coverage
mapped onto those levels; the new picture is recompressed into its original slot
(chunked.replace_in_place), so every chunk offset stays.
"""
import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import chunked, label, lzss, select1

THRESHOLD = 0.06            # coverage below this stays transparent


class LyricsError(ValueError):
    pass


def level(coverage: float) -> int:
    if coverage < THRESHOLD:
        return 0
    return int(min(15, max(1, round(15 - 14 * coverage))))


def _line(text: str, w: int, h: int, font: str, size: int, sid: str) -> bytes:
    f = ImageFont.truetype(label.resolve_font(font), size)
    missing = label.missing_glyphs(f, text)
    if missing:
        raise LyricsError(f"{sid}: no glyph for {missing} in {font}")
    im = Image.new("L", (w, h), 0)
    dr = ImageDraw.Draw(im)
    tw = dr.textlength(text, font=f)
    if tw > w - 4:
        raise LyricsError(f"{sid}: line is {tw:.0f}px wide, the picture holds {w - 4}")
    x = (w - tw) / 2
    bb = dr.textbbox((x, 0), text, font=f)
    if bb[0] < 0 or bb[1] < 0 or bb[2] > w or bb[3] > h:
        raise LyricsError(f"{sid}: glyphs reach outside the {w}x{h} picture ({bb})")
    dr.text((x, 0), text, font=f, fill=255)
    cov = np.asarray(im, dtype=np.float64) / 255.0
    idx = np.vectorize(level)(cov).astype(np.uint8)
    return bytes((idx[:, 0::2] << 4) | idx[:, 1::2])


def render(data: bytes, translation: Path, layout: Path, file_name: str | None = None) -> tuple[bytes, select1.Result]:
    tr = json.loads(Path(translation).read_text(encoding="utf-8"))
    lay = json.loads(Path(layout).read_text())
    if file_name is not None and tr.get("file") != file_name:
        raise LyricsError(f"table is for file {tr.get('file')!r}, not {file_name!r}")
    dup = [k for k, n in Counter(e["chunk"] for e in tr["entries"]).items() if n > 1]
    if dup:
        raise LyricsError(f"chunks {dup} are listed twice")
    chunks = chunked.parse(data)
    new = {}
    for e in tr["entries"]:
        sid, k = e["id"], e["chunk"]
        if not 0 <= k < len(chunks):
            raise LyricsError(f"{sid}: chunk {k} out of range")
        dec = lzss.decompress(chunks[k].stream)
        try:
            w, h, attr, _ = chunked.picture(dec)
        except chunked.ChunkError as err:
            raise LyricsError(f"{sid}: chunk {k} is not a picture ({err})") from err
        if hashlib.sha1(dec).hexdigest() != e["src_sha1"]:
            raise LyricsError(f"{sid}: chunk {k} differs from the source picture")
        if [w, h] != list(e["size"]):
            raise LyricsError(f"{sid}: picture is {w}x{h}, table says size {e['size']}")
        if (attr >> 3) & 7 != 0:
            raise LyricsError(f"{sid}: chunk {k} is not 4bpp")
        if not e.get("ko"):
            continue
        pic = chunked.make_picture(w, h, attr, _line(e["ko"], w, h, lay["font"], lay["size"], sid))
        stream = lzss.compress(pic)
        if any(lzss.decompress(stream, stale=st) != pic for st in (None, b"\xa5" * lzss.STALE_LEN)):
            raise LyricsError(f"{sid}: recompressed picture does not decode back")
        new[k] = stream
    try:
        out = chunked.build(chunked.replace_in_place(chunks, new), size=len(data)) if new else data
    except chunked.ChunkError as err:
        raise LyricsError(str(err)) from err
    res = select1.Result()
    res.ids = [e["id"] for e in tr["entries"]]
    res.states = dict(Counter(e["state"] for e in tr["entries"]))
    res.distribution = bool(res.states) and set(res.states) == {"distribution_eligible"}
    return out, res
