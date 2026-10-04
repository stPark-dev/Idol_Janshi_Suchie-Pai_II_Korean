"""Replace the text inside one sprite texture (4bpp or 8bpp CRAM-bank) while keeping its frame and palette.

1. Clean plate: text pixels inside the editable box are replaced by background, either the
   dominant non-text index of the same row (banded fills) or the nearest non-text pixel one
   pattern period away (repeating patterns).
2. The Korean text is rendered as an RGBA layer from palette colours (fill, outline, shadow).
3. The layer is composited over the cleaned pixels inside the box and quantised back to the
   sprite's palette. Pixels outside the box are never written.
"""
import os
import re
from collections import Counter
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from .title import ALPHA_CUTOFF, Texture, rgb555

# Debian/Ubuntu fonts-nanum-extra; elsewhere (Windows) the same file under work/fonts/
_FONT_CANDIDATES = ["/usr/share/fonts/truetype/nanum/NanumSquareRoundEB.ttf",
                    str(Path(__file__).resolve().parents[2] / "work" / "fonts" / "NanumSquareRoundEB.ttf")]
DEFAULT_FONT = next((p for p in _FONT_CANDIDATES if os.path.isfile(p)), _FONT_CANDIDATES[0])


def resolve_font(path: str) -> str:
    """A layout's font path, or the same file name under work/fonts/ when that path is missing."""
    if os.path.isfile(path):
        return path
    local = Path(_FONT_CANDIDATES[1]).parent / Path(path).name
    return str(local) if local.is_file() else path


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


_CELL = re.compile(r"\d\d|!!|!\?|\?!| |.")
_UPRIGHT = set("…〜ー")              # drawn turned 90 degrees in vertical text


def _glyph(t, font, size, weight, aa):
    g = Image.new("L", (size * 3, size * 3), 0)
    gd = ImageDraw.Draw(g)
    if not aa:
        gd.fontmode = "1"
    gd.text((size, size), t, font=font, fill=255, stroke_width=weight, stroke_fill=255)
    box = g.getbbox()
    return g.crop(box) if box else None


def _vertical_masks(ln, segs, fills, font, big, pad, area):
    """Top-to-bottom layout: one cell per character; two digits or '!!'/'!?' share a cell (side by
    side, squeezed horizontally when they do not fit the column), '…'/'ー'/'〜' are turned upright, a
    space is half a cell. Every cell must leave ink inside its own cell height and the column must end
    inside the area; anything else is a LabelError. Returns [(mask, fill)] per segment."""
    size, weight, aa = ln["size"], ln.get("weight", 0), ln.get("aa", True)
    step = ln.get("step", size + 1)
    ax0, ay0, ax1, ay1 = area
    width = ax1 - ax0
    cells = [(k, t) for k, seg in enumerate(segs) for t in _CELL.findall(seg)]
    height = sum(step // 2 if t == " " else step for _, t in cells)
    if ln["y"] == "center":
        y = ay0 + (ay1 - ay0 - height) / 2
    elif ln["y"] == "top" or isinstance(ln["y"], int):
        y = ay0 + (0 if ln["y"] == "top" else ln["y"])
    else:
        raise LabelError(f"vertical y must be 'top', 'center' or an offset, got {ln['y']!r}")
    if y + height > ay1:
        raise LabelError(f"vertical text {ln['text']!r} runs past the area ({y + height:.0f} > {ay1})")
    masks = [Image.new("L", big, 0) for _ in segs]
    for k, t in cells:
        if t == " ":
            y += step // 2
            continue
        g = _glyph(t, font, size, weight, aa)
        if g is None:
            raise LabelError(f"cell {t!r} of {ln['text']!r} draws nothing")
        if t in _UPRIGHT:
            g = g.rotate(-90, expand=True)
        if g.width > width - 2:
            if len(t) == 1 and t not in _UPRIGHT:
                raise LabelError(f"cell {t!r} of {ln['text']!r} is wider than the column ({g.width} > {width - 2})")
            if width - 2 < 1:
                raise LabelError(f"column of width {width} is too narrow for {t!r}")
            g = g.resize((width - 2, g.height), Image.LANCZOS if aa else Image.NEAREST)   # tate-chu-yoko
        if g.height > step:
            raise LabelError(f"cell {t!r} of {ln['text']!r} is taller than its cell ({g.height} > {step})")
        px = round(ax0 + (width - g.width) / 2) + pad
        py = round(y + (step - g.height) / 2) + pad
        cell = Image.new("L", big, 0)
        cell.paste(g, (px, py), g)
        if cell.getbbox() is None:
            raise LabelError(f"cell {t!r} of {ln['text']!r} falls outside the canvas")
        masks[k] = ImageChops.lighter(masks[k], cell)
        y += step
    return list(zip(masks, fills))


def _cut(mask, big) -> bool:
    """True when a segment's ink is gone or touches the padded canvas edge (part of it was cropped)."""
    b = mask.getbbox()
    return b is None or b[0] == 0 or b[1] == 0 or b[2] == big[0] or b[3] == big[1]


def render_lines(size, lines, pal, font_path: str = DEFAULT_FONT, box=None) -> Image.Image:
    """lines: [{text, size, x ('center'|'right'|int), y ('center'|int), area ([x0,y0,x1,y1] used
    for centring, default whole canvas), fill (idx, {"v": [idx, ...]} vertical gradient over the
    line, or a list of those per '|' segment), weight (extra stroke px, optional), aa (False =
    no antialiasing, crisp pixel glyphs), outline (idx or a list per '|' segment, optional),
    shadow ({idx, dx, dy},
    optional), vertical (True = top-to-bottom cells inside area, y 'top'|'center'|offset, optional
    step = cell height)}]. Everything drawn (glyph, stroke, outline, shadow) must lie inside box
    (default the whole canvas); otherwise LabelError, never silent clipping. shear (optional): italic
    slant around the area's vertical centre, x offset per pixel above it (negative leans left)."""
    w, h = size
    bx0, by0, bx1, by1 = box or (0, 0, w, h)
    pad = 4 + max([ln.get("weight", 0) + 1 + max(abs(ln.get("shadow", {}).get("dx", 0)),
                                                  abs(ln.get("shadow", {}).get("dy", 0))) for ln in lines] or [0])
    big = (w + 2 * pad, h + 2 * pad)
    layer = Image.new("RGBA", big, (0, 0, 0, 0))
    for ln in lines:
        font = ImageFont.truetype(resolve_font(font_path), ln["size"])
        weight = ln.get("weight", 0)
        segs = ln["text"].split("|")
        fills = ln["fill"] if isinstance(ln["fill"], list) else [ln["fill"]] * len(segs)
        if len(fills) != len(segs):
            raise LabelError(f"{len(segs)} colour segments but {len(fills)} fill colours: {ln['text']!r}")
        text = "".join(segs)
        ax0, ay0, ax1, ay1 = ln.get("area", (0, 0, w, h))
        if ln.get("vertical"):
            masks = _vertical_masks(ln, segs, fills, font, big, pad, (ax0, ay0, ax1, ay1))
        else:
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
        segs_drawn = [seg.strip() != "" for seg in segs]
        inked = [m.getbbox() is not None for m, _ in masks]
        if any(inked) and any(d and _cut(m, big) for (m, _), d in zip(masks, segs_drawn)):
            raise LabelError(f"text {text!r} does not fit: it runs off the canvas (a segment is cropped or missing)")
        if ln.get("shear"):                    # italic: rows above the area centre move right
            k = float(ln["shear"])
            cy = (ay0 + ay1) / 2 + pad
            rs = Image.NEAREST if not ln.get("aa", True) else Image.BILINEAR
            masks = [(m.transform(big, Image.AFFINE, (1, k, -k * cy, 0, 1, 0), resample=rs), fi) for m, fi in masks]
            if any(inked) and any(d and _cut(m, big) for (m, _), d in zip(masks, segs_drawn)):
                raise LabelError(f"sheared text {text!r} does not fit: it runs off the canvas")
        full = Image.new("L", big, 0)
        for m, _ in masks:
            full = ImageChops.lighter(full, m)
        ol = ln.get("outline")
        if isinstance(ol, list) and (len(ol) != len(segs) or not all(isinstance(o, int) for o in ol)):
            raise LabelError(f"{len(segs)} colour segments need {len(segs)} outline indices, got {ol}: {ln['text']!r}")
        outline = full.filter(ImageFilter.MaxFilter(3)) if ol is not None else None
        shape = outline or full
        if shape.getbbox() is None:
            if text:
                raise LabelError(f"text {text!r} draws nothing visible; use \"\" for an intentionally empty line")
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
        if isinstance(ol, list):
            for (m, _), oi in zip(masks, ol):
                layer.paste(Image.new("RGBA", big, _colour(pal, oi)), (0, 0), m.filter(ImageFilter.MaxFilter(3)))
        elif outline is not None:
            layer.paste(Image.new("RGBA", big, _colour(pal, ol)), (0, 0), outline)
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
