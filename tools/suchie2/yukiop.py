"""YUKIOP.BIN: the Suchie-Yuki opening (stage select "Suchie Yuki", docs/initial-survey.md 3.22).

File layout (307200 bytes, loaded whole to WorkRAM-L 0x00200000):
  0x0000-0x0800  CRAM image (1024 colours): banks 0x10-0x3F for the 4bpp sprites, 0x100- for the 8bpp ones
  0x0800-0x1000  zeros
  0x1000-        word-RLE Yc bundle (rle16.py), 8 entries, zero padding up to 0x38000:
                 0 PRESS START, 1-2 speed-line backgrounds, 3-4 Yuki pictures,
                 5 logo "スチユキちゃん" (192x64 4bpp), 6 "美少女雀士" (80x16 4bpp),
                 7 the still title (320x240 16bpp) with both sprites already drawn in
  0x38000        256-colour palette of the story card
  0x38200        story card, 320x240 8bpp, uncompressed ("…大かつやく！…の巻" on a chalkboard)
  0x4AE00-       zeros

The logo comes from the owner's image (both lines in one picture): it is split into the upper line
and the main logo, each fitted to its sprite and reduced to the sprite's 16 colours (its CRAM bank in
this file, checked against the still title, where the game drew the sprites). The still title loses the old sprites (filled
from their surroundings) and gets the new ones at the same places, so the flying sprites and the
still picture match. The story card's chalk text is erased with the board's own texture (borrowed
one texture period up or down) and the translation written in the card's chalk colours.
"""
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import label, rle16, select1, yc

SIZE = 307200
PAL = (0x0, 0x800)
BUNDLE = 0x1000
STORY_PAL = 0x38000
STORY = 0x38200
STORY_W, STORY_H = 320, 240
STORY_END = STORY + STORY_W * STORY_H


class YukiopError(ValueError):
    pass


@dataclass(frozen=True)
class Parts:
    bundle: bytes       # decoded Yc bundle
    stream_len: int
    story: bytes        # 320x240 palette indices


def _zero(data: bytes, lo: int, hi: int, what: str) -> None:
    if any(data[lo:hi]):
        raise YukiopError(f"{what} {lo:#x}-{hi:#x} is not zero")


def parse(data: bytes) -> Parts:
    if len(data) != SIZE:
        raise YukiopError(f"file is {len(data)} bytes, expected {SIZE}")
    _zero(data, PAL[1], BUNDLE, "gap")
    try:
        bundle, n = rle16.decompress(data[BUNDLE:STORY_PAL])
    except rle16.Rle16Error as err:
        raise YukiopError(f"bundle: {err}") from err
    _zero(data, BUNDLE + n, STORY_PAL, "bundle padding")
    _zero(data, STORY_END, SIZE, "tail")
    return Parts(bundle, n, data[STORY:STORY_END])


def assemble(data: bytes, bundle: bytes, story: bytes) -> bytes:
    if len(story) != STORY_END - STORY:
        raise YukiopError("story card has the wrong size")
    stream = rle16.compress(bundle)
    if rle16.decompress(stream) != (bundle, len(stream)):
        raise YukiopError("recompressed bundle does not round-trip")
    room = STORY_PAL - BUNDLE
    if len(stream) > room:
        raise YukiopError(f"bundle stream of {len(stream)} bytes exceeds its {room}-byte region")
    out = bytearray(data)
    out[BUNDLE:STORY_PAL] = stream + bytes(room - len(stream))
    out[STORY:STORY_END] = story
    return bytes(out)


def check_rect(rect: tuple[int, int, int, int], canvas: tuple[int, int], what: str) -> None:
    x, y, w, h = rect
    W, H = canvas
    if not (0 <= x and 0 <= y and x + w <= W and y + h <= H):
        raise YukiopError(f"{what}: {w}x{h} at ({x},{y}) lies outside the {W}x{H} picture")


# ---- pixels ---------------------------------------------------------------------------------
def rgb5(c: np.ndarray) -> np.ndarray:
    c = np.asarray(c, dtype=np.int64)
    return np.stack([c & 31, (c >> 5) & 31, (c >> 10) & 31], -1)


def fill(rgb: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """Fill hole pixels with the nearest known pixel of the same row (the left one on a tie; a row
    with nothing known takes the nearest row's pixel). The bundle's word RLE packs runs along a
    row, so this keeps the still title inside its region; integer copies only, so every build
    gives the same pixels."""
    img = np.asarray(rgb, dtype=np.int64).copy()
    hole = np.asarray(hole, dtype=bool)
    h, w = hole.shape
    rows = [y for y in range(h) if (~hole[y]).any()]
    if not rows:
        raise YukiopError("hole has no known neighbours")
    for y in range(h):
        xs = np.nonzero(hole[y])[0]
        if not len(xs):
            continue
        src = y if (~hole[y]).any() else min(rows, key=lambda r: (abs(r - y), r))
        known = np.nonzero(~hole[src])[0]
        for x in xs:
            j = int(np.searchsorted(known, x))
            near = [known[k] for k in (j - 1, j) if 0 <= k < len(known)]
            img[y, x] = img[src, min(near, key=lambda c: (abs(int(c) - int(x)), c))]
    return img


def sprite_palette(picture: np.ndarray, sprite: np.ndarray, at: tuple[int, int]) -> list[int]:
    """The RGB555 colour of each sprite index, read from a picture the sprite was drawn into."""
    x, y = at
    h, w = sprite.shape
    region = np.asarray(picture, dtype=np.int64)[y:y + h, x:x + w] & 0x7FFF
    pal = [0] * 16
    for k in range(1, 16):
        v = region[sprite == k]
        if not len(v):
            continue
        c = Counter(v.tolist()).most_common(1)[0]
        if c[1] / len(v) < 0.98:
            raise YukiopError(f"sprite index {k} has no single colour in the picture ({c[1]}/{len(v)})")
        pal[k] = c[0]
    return pal


def split_logo(rgba: np.ndarray, search: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Upper line and main logo of one picture. Per column, the seam is the first transparent row
    in `search`; where the two touch, the darkest opaque row there (their outlines meet)."""
    a = np.asarray(rgba)
    alpha = a[..., 3].astype(int)
    lum = (a[..., 0].astype(int) * 3 + a[..., 1].astype(int) * 6 + a[..., 2].astype(int)) / 10
    lo, hi = search
    seam = np.empty(a.shape[1], int)
    for x in range(a.shape[1]):
        z = np.nonzero(alpha[lo:hi, x] <= 8)[0]
        if len(z):
            seam[x] = lo + z[0]
        else:
            seg = np.where(alpha[lo:hi, x] > 127, lum[lo:hi, x], 1e9)
            seam[x] = lo + int(np.argmin(seg))
    upper = np.arange(a.shape[0])[:, None] < seam[None, :]
    top, main = a.copy(), a.copy()
    top[~upper, 3] = 0
    main[upper, 3] = 0
    return top, main


def _majority(q: np.ndarray) -> np.ndarray:
    """A lone index among 5+ equal opaque neighbours takes theirs (quantisation speckle)."""
    h, w = q.shape
    p = np.pad(q, 1, constant_values=-1)
    out = q.copy()
    for y, x in zip(*np.nonzero(q)):
        nb = p[y:y + 3, x:x + 3].ravel()
        nb = nb[nb > 0]
        u, c = np.unique(nb, return_counts=True)
        if c.max() >= 5 and q[y, x] not in u[c >= 3]:
            out[y, x] = u[np.argmax(c)]
    return out


def _despeckle(q: np.ndarray) -> np.ndarray:
    """One pixel between two equal opaque neighbours on its row takes their index (longer runs)."""
    q = q.copy()
    for _ in range(2):
        l, r = np.roll(q, 1, 1), np.roll(q, -1, 1)
        m = (l == r) & (q != l) & (q != 0) & (l != 0)
        m[:, 0] = m[:, -1] = False
        q[m] = l[m]
    return q


def fit_logo(rgba: np.ndarray, size: tuple[int, int], palette: list[int], allowed: list[int]) -> np.ndarray:
    """Scale to fit size (W, H) keeping the aspect (area average), centre, reduce to the allowed
    indices, and clean single-pixel speckle."""
    W, H = size
    a = np.asarray(rgba)
    ys, xs = np.nonzero(a[..., 3] > 8)
    if not len(ys):
        raise YukiopError("logo part is empty")
    r = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.float64)
    pm = r.copy()
    pm[..., :3] *= pm[..., 3:4] / 255.0
    h, w = r.shape[:2]
    s = min(W / w, H / h)
    nw, nh = max(1, round(w * s)), max(1, round(h * s))
    img = Image.fromarray(np.clip(np.rint(pm), 0, 255).astype(np.uint8), "RGBA").resize((nw, nh), Image.BOX)
    p = np.asarray(img).astype(np.float64)
    al = p[..., 3]
    rgb = np.where(al[..., None] > 0, p[..., :3] * 255.0 / np.maximum(al[..., None], 1), 0)
    cand = np.array(sorted(allowed))
    cc = rgb5(np.array([palette[k] for k in cand])) * 255.0 / 31
    q = cand[((rgb[..., None, :] - cc[None, None]) ** 2).sum(-1).argmin(-1)]
    q[al <= 127] = 0
    q = _despeckle(_majority(q))
    out = np.zeros((H, W), int)
    oy, ox = (H - nh) // 2, (W - nw) // 2
    out[oy:oy + nh, ox:ox + nw] = q
    return out


def erase_board(img: np.ndarray, box: tuple[int, int, int, int], board, period: int) -> np.ndarray:
    """Replace every non-board pixel inside box by the board pixel `period` rows up or down."""
    x0, y0, x1, y1 = box
    src = np.asarray(img)
    out = src.copy()
    board = list(board)
    is_board = np.isin(src, board)
    for y, x in zip(*np.nonzero(~is_board[y0:y1, x0:x1])):
        y, x = y + y0, x + x0
        for k in (1, -1, 2, -2, 3, -3, 4, -4, 5, -5, 6, -6):
            yy = y + period * k
            if y0 <= yy < y1 and is_board[yy, x]:
                out[y, x] = src[yy, x]
                break
        else:
            raise YukiopError(f"board pixel ({x},{y}): no board texture within 6 periods to borrow")
    return out


def write_board(img: np.ndarray, pal: np.ndarray, box, lines: list[dict], texts: list[str], colours: list[int]) -> np.ndarray:
    """Chalk text: rasterise, blend white over the board, and take the nearest of `colours`."""
    x0, y0, x1, y1 = box
    W, H = x1 - x0, y1 - y0
    mask = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(mask)
    for ln, t in zip(lines, texts):
        f = ImageFont.truetype(label.resolve_font(ln["font"]), ln["size"])
        missing = label.missing_glyphs(f, t)
        if missing:
            raise YukiopError(f"story line {t!r}: no glyph for {missing} in {ln['font']}")
        tw = dr.textlength(t, font=f)
        if tw > W - 2:
            raise YukiopError(f"story line {t!r} is {tw:.0f}px wide, the board holds {W - 2}")
        x = (W - tw) / 2 if ln.get("align", "center") == "center" else W - tw - ln.get("right", 0)
        bb = dr.textbbox((x, ln["y"]), t, font=f)
        if bb[0] < 0 or bb[1] < 0 or bb[2] > W or bb[3] > H:
            raise YukiopError(f"story line {t!r} reaches outside the {W}x{H} board ({bb})")
        dr.text((x, ln["y"]), t, font=f, fill=255)
    a = np.asarray(mask, dtype=np.float64) / 255.0
    out = np.asarray(img).copy()
    reg = out[y0:y1, x0:x1]
    base = rgb5(pal[reg]).astype(np.float64)
    want = base * (1 - a[..., None]) + 31.0 * a[..., None]
    cand = np.array(colours)
    cc = rgb5(pal[cand]).astype(np.float64)
    pick = cand[((want[..., None, :] - cc[None, None]) ** 2).sum(-1).argmin(-1)]
    reg[a > 0.15] = pick[a > 0.15]
    return out


# ---- the file -------------------------------------------------------------------------------
def _idx4(e: yc.Entry) -> np.ndarray:
    x = np.frombuffer(e.data, np.uint8)
    return np.stack([x >> 4, x & 15], -1).reshape(e.height, e.width).astype(int)


def _pack4(idx: np.ndarray) -> bytes:
    i = np.asarray(idx, dtype=np.uint8)
    return bytes((i[:, 0::2] << 4) | i[:, 1::2])


def render(data: bytes, translation: Path, layout: Path) -> tuple[bytes, select1.Result]:
    translation, layout = Path(translation), Path(layout)
    tr = json.loads(translation.read_text(encoding="utf-8"))
    lay = json.loads(layout.read_text())
    if hashlib.sha1(data).hexdigest() != tr["source_sha1"]:
        raise YukiopError("YUKIOP.BIN differs from the supported source")
    parts = parse(data)
    ents = yc.parse(parts.bundle)
    by_id = {e["id"]: e for e in tr["entries"]}
    new_data = {}
    story = np.frombuffer(parts.story, np.uint8).reshape(STORY_H, STORY_W).astype(int)

    unknown = set(by_id) - {"yukiop.logo", "yukiop.story"}
    if unknown:
        raise YukiopError(f"unknown entries {sorted(unknown)}")
    logo = by_id.get("yukiop.logo")
    if logo and not logo.get("image"):
        raise YukiopError("yukiop.logo has nothing to draw (no image); list it under excluded instead")
    if logo:
        img_path = translation.parent.parent / logo["image"]
        try:
            raw = img_path.read_bytes()
            rgba = np.asarray(Image.open(img_path).convert("RGBA"))
        except OSError as err:
            raise YukiopError(f"logo image {logo['image']}: {err}") from err
        if hashlib.sha1(raw).hexdigest() != logo["image_sha1"]:
            raise YukiopError(f"{logo['image']} differs from the approved image")
        top, main = split_logo(rgba, tuple(lay["split_search"]))
        L, Sm, P = lay["logo"], lay["small"], lay["picture"]
        pic_e = ents[P["entry"]]
        if (yc.bpp(pic_e.attr), pic_e.width, pic_e.height) != (16, 320, 240):
            raise YukiopError("still title is not a 320x240 16bpp entry")
        pic = np.frombuffer(pic_e.data, ">u2").astype(np.int64).reshape(240, 320)
        rgb = rgb5(pic)
        old = np.zeros((240, 320), bool)
        sprites = []
        for spec, part in ((L, main), (Sm, top)):
            e = ents[spec["entry"]]
            if yc.bpp(e.attr) != 4:
                raise YukiopError(f"entry {spec['entry']} is not a 4bpp sprite")
            spr = _idx4(e)
            x, y = spec["at"]
            check_rect((x, y, e.width, e.height), (320, 240), f"entry {spec['entry']}")
            cram = np.frombuffer(data[PAL[0]:PAL[1]], ">u2").astype(np.int64) & 0x7FFF
            pal = [int(c) for c in cram[e.colr:e.colr + 16]]
            seen = sprite_palette(pic, spr, (x, y))
            if any(seen[k] != pal[k] for k in range(1, 16) if seen[k]):
                raise YukiopError(f"entry {spec['entry']}: CRAM bank {e.colr:#x} differs from the still title's colours")
            allowed = sorted({int(k) for k in np.unique(spr) if k})
            nidx = fit_logo(part, (e.width, e.height), pal, allowed)
            old[y:y + e.height, x:x + e.width] |= spr != 0
            sprites.append((spec, nidx, pal))
            new_data[spec["entry"]] = _pack4(nidx)
        rgb = fill(rgb, old)
        for spec, nidx, pal in sprites:
            x, y = spec["at"]
            h, w = nidx.shape
            m = nidx != 0
            rgb[y:y + h, x:x + w][m] = rgb5(np.array(pal))[nidx][m]
        out = (rgb[..., 0] | (rgb[..., 1] << 5) | (rgb[..., 2] << 10) | 0x8000).astype(">u2")
        new_data[P["entry"]] = out.tobytes()

    st = by_id.get("yukiop.story")
    if st and not st.get("ko"):
        raise YukiopError("yukiop.story has nothing to draw (no ko); list it under excluded instead")
    if st:
        S = lay["story"]
        box = tuple(S["box"])
        check_rect((box[0], box[1], box[2] - box[0], box[3] - box[1]), (STORY_W, STORY_H), "story board")
        spal = np.frombuffer(data[STORY_PAL:STORY], ">u2").astype(np.int64)
        x0, y0, x1, y1 = box
        inside = story[y0:y1, x0:x1]
        colours = sorted(set(np.unique(inside).tolist()))      # the board's two greens and the chalk shades
        cleaned = erase_board(story, box, S["board"], S["period"])
        texts = st["ko"].split("\n")
        if len(texts) != len(S["lines"]):
            raise YukiopError(f"story has {len(texts)} lines, the layout {len(S['lines'])}")
        story = write_board(cleaned, spal, box, S["lines"], texts, colours)

    new_bundle = yc.build([yc.Entry(e.width, e.height, e.attr, e.colr, new_data.get(i, e.data))
                           for i, e in enumerate(ents)])
    out = assemble(data, new_bundle, story.astype(np.uint8).tobytes())
    res = select1.Result()
    res.ids = [e["id"] for e in tr["entries"]]
    res.states = dict(Counter(e["state"] for e in tr["entries"]))
    res.distribution = bool(res.states) and set(res.states) == {"distribution_eligible"}
    return out, res
