"""Replace the text inside one 4bpp sprite texture while keeping its frame and palette.

1. Clean plate: text pixels inside the editable box are replaced by background, either the
   dominant non-text index of the same row (banded fills) or the nearest non-text pixel one
   pattern period away (repeating patterns).
2. The Korean text is rendered as an RGBA layer from palette colours (fill, outline, shadow).
3. The layer is composited over the cleaned pixels inside the box and quantised back to the
   sprite's palette. Pixels outside the box are never written.
"""
from collections import Counter

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from .title import ALPHA_CUTOFF, Texture, rgb555

DEFAULT_FONT = "/usr/share/fonts/truetype/nanum/NanumSquareRoundEB.ttf"


class LabelError(ValueError):
    pass


def _box_iter(box):
    x0, y0, x1, y1 = box
    for y in range(y0, y1):
        for x in range(x0, x1):
            yield x, y


def clear_box(tex: Texture, box) -> None:
    for x, y in _box_iter(box):
        tex.set(x, y, 0)


def clean_rows(tex: Texture, box, text_idx, borrow_rows=None) -> None:
    """A row with no background pixel is an error unless it is listed in borrow_rows; a listed
    row takes the nearest row's dominant background index (ties: the upper row)."""
    borrow_rows = set(borrow_rows or ())
    x0, y0, x1, y1 = box
    fills = {}
    for y in range(y0, y1):
        bg = Counter(tex.pixel(x, y) for x in range(x0, x1) if tex.pixel(x, y) not in text_idx)
        if bg:
            fills[y] = bg.most_common(1)[0][0]
    for y in range(y0, y1):
        if y not in fills:
            if y not in borrow_rows or not fills:
                raise LabelError(f"row {y} of box {box} has no background pixel")
            near = min(fills, key=lambda r: (abs(r - y), r))
            fill = fills[near]
        else:
            fill = fills[y]
        for x in range(x0, x1):
            if tex.pixel(x, y) in text_idx:
                tex.set(x, y, fill)


def clean_tile(tex: Texture, box, text_idx, period) -> None:
    px, py = period
    x0, y0, x1, y1 = box
    src = [[tex.pixel(x, y) for x in range(tex.width)] for y in range(tex.height)]
    for x, y in _box_iter(box):
        if src[y][x] not in text_idx:
            continue
        found = None
        for k in range(1, max(tex.width, tex.height)):
            for sx, sy in ((x - k * px, y - k * py), (x + k * px, y + k * py),
                           (x - k * px, y), (x + k * px, y), (x, y - k * py), (x, y + k * py)):
                if x0 <= sx < x1 and y0 <= sy < y1 and src[sy][sx] not in text_idx:
                    found = src[sy][sx]
                    break
            if found is not None:
                break
        if found is None:
            raise LabelError(f"no pattern pixel for ({x},{y})")
        tex.set(x, y, found)


def _colour(pal, idx):
    return rgb555(pal[idx]) + (255,)


def _fill_image(size, pal, fill, mask) -> Image.Image:
    """Solid colour for an index, or a vertical gradient {"v": [idx, ...]} over the mask's rows."""
    if isinstance(fill, dict):
        steps = fill["v"]
        box = mask.getbbox()
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        if box is None:
            return img
        y0, y1 = box[1], box[3]
        dr = ImageDraw.Draw(img)
        for y in range(y0, y1):
            k = min(len(steps) - 1, (y - y0) * len(steps) // (y1 - y0))
            dr.line([(0, y), (size[0], y)], fill=_colour(pal, steps[k]))
        return img
    return Image.new("RGBA", size, _colour(pal, fill))


def render_lines(size, lines, pal, font_path: str = DEFAULT_FONT, box=None) -> Image.Image:
    """lines: [{text, size, x ('center'|'right'|int), y ('center'|int), area ([x0,y0,x1,y1] used
    for centring, default whole canvas), fill (idx, {"v": [idx, ...]} vertical gradient over the
    line, or a list of those per '|' segment), weight (extra stroke px, optional), aa (False =
    no antialiasing, crisp pixel glyphs), outline (idx, optional), shadow ({idx, dx, dy},
    optional)}]. Everything drawn (glyph, stroke, outline, shadow) must lie inside box (default
    the whole canvas); otherwise LabelError, never silent clipping."""
    w, h = size
    bx0, by0, bx1, by1 = box or (0, 0, w, h)
    pad = 4 + max([ln.get("weight", 0) + 1 + max(abs(ln.get("shadow", {}).get("dx", 0)),
                                                  abs(ln.get("shadow", {}).get("dy", 0))) for ln in lines] or [0])
    big = (w + 2 * pad, h + 2 * pad)
    layer = Image.new("RGBA", big, (0, 0, 0, 0))
    for ln in lines:
        font = ImageFont.truetype(font_path, ln["size"])
        weight = ln.get("weight", 0)
        segs = ln["text"].split("|")
        fills = ln["fill"] if isinstance(ln["fill"], list) else [ln["fill"]] * len(segs)
        if len(fills) != len(segs):
            raise LabelError(f"{len(segs)} colour segments but {len(fills)} fill colours: {ln['text']!r}")
        text = "".join(segs)
        ax0, ay0, ax1, ay1 = ln.get("area", (0, 0, w, h))
        tb = ImageDraw.Draw(Image.new("L", (1, 1))).textbbox((0, 0), text, font=font, stroke_width=weight)
        x = {"center": ax0 + (ax1 - ax0 - (tb[2] - tb[0])) / 2 - tb[0],
             "right": ax1 - ln.get("margin", 0) - tb[2]}.get(ln["x"], ln["x"])
        y = ay0 + (ay1 - ay0 - (tb[3] - tb[1])) / 2 - tb[1] if ln["y"] == "center" else ln["y"]
        masks = []
        cx = x
        for seg, fi in zip(segs, fills):
            m = Image.new("L", big, 0)
            dr = ImageDraw.Draw(m)
            if not ln.get("aa", True):
                dr.fontmode = "1"
            dr.text((round(cx) + pad, round(y) + pad), seg, font=font, fill=255,
                    stroke_width=weight, stroke_fill=255)
            masks.append((m, fi))
            cx += font.getlength(seg)
        full = Image.new("L", big, 0)
        for m, _ in masks:
            full = ImageChops.lighter(full, m)
        outline = full.filter(ImageFilter.MaxFilter(3)) if ln.get("outline") is not None else None
        shape = outline or full
        if shape.getbbox() is None:
            continue
        moved = None
        if "shadow" in ln:
            sh = ln["shadow"]
            moved = Image.new("L", big, 0)
            moved.paste(shape, (sh["dx"], sh["dy"]))
        ext = shape if moved is None else ImageChops.lighter(shape, moved)
        ex0, ey0, ex1, ey1 = ext.getbbox()
        if ex0 < bx0 + pad or ey0 < by0 + pad or ex1 > bx1 + pad or ey1 > by1 + pad:
            raise LabelError(f"text {text!r} does not fit in box {(bx0, by0, bx1, by1)} "
                             f"(drawn {ex0 - pad},{ey0 - pad}-{ex1 - pad},{ey1 - pad})")
        if moved is not None:
            layer.paste(Image.new("RGBA", big, _colour(pal, ln["shadow"]["idx"])), (0, 0), moved)
        if outline is not None:
            layer.paste(Image.new("RGBA", big, _colour(pal, ln["outline"])), (0, 0), outline)
        for m, fi in masks:
            layer.paste(_fill_image(big, pal, fi, full), (0, 0), m)
    return layer.crop((pad, pad, pad + w, pad + h))


def _nearest(rgb, pal, allowed):
    best = None
    for idx in allowed:
        pr, pg, pb = rgb555(pal[idx])
        d = 2 * (rgb[0] - pr) ** 2 + 4 * (rgb[1] - pg) ** 2 + 3 * (rgb[2] - pb) ** 2
        if best is None or d < best[0]:
            best = (d, idx)
    return best[1]


def compose(tex: Texture, pal, box, layer: Image.Image, allowed) -> None:
    allowed = list(allowed)
    lp = layer.convert("RGBA").load()
    for x, y in _box_iter(box):
        r, g, b, a = lp[x, y]
        if a == 0:
            continue
        bg = tex.pixel(x, y)
        if bg == 0:
            if a >= ALPHA_CUTOFF:
                tex.set(x, y, _nearest((r, g, b), pal, allowed))
            continue
        br, bgc, bb = rgb555(pal[bg])
        f = a / 255
        mix = (r * f + br * (1 - f), g * f + bgc * (1 - f), b * f + bb * (1 - f))
        tex.set(x, y, _nearest(mix, pal, allowed))
