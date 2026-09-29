"""Compose a replacement title logo over several fixed-position 4bpp sprites.

The logo is laid out once on a screen-sized canvas. Every opaque canvas pixel is given to
the covering sprite whose 15-colour palette represents it best (ties: first listed sprite);
every other sprite covering that pixel stays transparent (index 0) there, so draw order
cannot hide the chosen colour.
An opaque pixel outside all sprite rectangles fails the build instead of being dropped.
"""
from dataclasses import dataclass

from PIL import Image

ALPHA_CUTOFF = 128


class LayoutError(ValueError):
    pass


@dataclass(frozen=True)
class Sprite:
    name: str
    rect: tuple[int, int, int, int]   # screen x, y, width, height
    palette: list[int]                # 16 RGB555 entries; index 0 is transparent


@dataclass
class Texture:
    width: int
    height: int
    data: bytearray

    def pixel(self, x: int, y: int) -> int:
        b = self.data[(y * self.width + x) // 2]
        return b >> 4 if x % 2 == 0 else b & 0x0F

    def set(self, x: int, y: int, v: int) -> None:
        i = (y * self.width + x) // 2
        if x % 2 == 0:
            self.data[i] = (self.data[i] & 0x0F) | (v << 4)
        else:
            self.data[i] = (self.data[i] & 0xF0) | v


def rgb555(v: int) -> tuple[int, int, int]:
    def x(c):
        return (c << 3) | (c >> 2)
    return x(v & 31), x((v >> 5) & 31), x((v >> 10) & 31)


def _nearest(rgb, palette):
    best = None
    for idx in range(1, 16):
        pr, pg, pb = rgb555(palette[idx])
        # weighted RGB distance (approximate perceptual weights)
        d = 2 * (rgb[0] - pr) ** 2 + 4 * (rgb[1] - pg) ** 2 + 3 * (rgb[2] - pb) ** 2
        if best is None or d < best[0]:
            best = (d, idx)
    return best


def assign(canvas: Image.Image, sprites: list[Sprite]) -> dict[str, Texture]:
    canvas = canvas.convert("RGBA")
    tex = {s.name: Texture(s.rect[2], s.rect[3], bytearray(s.rect[2] * s.rect[3] // 2)) for s in sprites}
    px = canvas.load()
    uncovered = 0
    for y in range(canvas.height):
        for x in range(canvas.width):
            r, g, b, a = px[x, y]
            if a < ALPHA_CUTOFF:
                continue
            best = None
            for s in sprites:
                sx, sy, sw, sh = s.rect
                if sx <= x < sx + sw and sy <= y < sy + sh:
                    d, idx = _nearest((r, g, b), s.palette)
                    if best is None or d < best[0]:
                        best = (d, s, idx)
            if best is None:
                uncovered += 1
                continue
            _, s, idx = best
            tex[s.name].set(x - s.rect[0], y - s.rect[1], idx)
    if uncovered:
        raise LayoutError(f"{uncovered} opaque pixel(s) fall outside every sprite rectangle")
    return tex


def color_group_mask(img: Image.Image, rgb_range, edge_passes: int = 3) -> Image.Image:
    """Mask (L, 255 = member) of every 4-connected opaque component that contains at least one
    pixel inside rgb_range [[rlo, rhi], [glo, ghi], [blo, bhi]]; faint edge pixels
    (0 < alpha < cutoff) join the mask when they 8-touch it, up to edge_passes pixels deep, so a
    faint pixel between two groups goes to the masked group."""
    img = img.convert("RGBA")
    w, h = img.size
    px = img.tobytes()
    opaque = bytearray(1 if px[i * 4 + 3] >= ALPHA_CUTOFF else 0 for i in range(w * h))
    mask = bytearray(w * h)
    seen = bytearray(w * h)
    (rlo, rhi), (glo, ghi), (blo, bhi) = rgb_range
    for start in range(w * h):
        if not opaque[start] or seen[start]:
            continue
        comp, stack, hit = [], [start], False
        seen[start] = 1
        while stack:
            i = stack.pop()
            comp.append(i)
            r, g, b = px[i * 4], px[i * 4 + 1], px[i * 4 + 2]
            if rlo <= r <= rhi and glo <= g <= ghi and blo <= b <= bhi:
                hit = True
            x, y = i % w, i // w
            for j in ((i - 1) if x else -1, (i + 1) if x + 1 < w else -1, i - w if y else -1, i + w if y + 1 < h else -1):
                if j >= 0 and opaque[j] and not seen[j]:
                    seen[j] = 1
                    stack.append(j)
        if hit:
            for i in comp:
                mask[i] = 255
    for _ in range(edge_passes):
        grow = []
        for i in range(w * h):
            if mask[i] or opaque[i] or px[i * 4 + 3] == 0:
                continue
            x, y = i % w, i // w
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < w and 0 <= yy < h and mask[yy * w + xx]:
                        grow.append(i)
                        break
                else:
                    continue
                break
        if not grow:
            break
        for i in grow:
            mask[i] = 255
    return Image.frombytes("L", (w, h), bytes(mask))


def apply_mask(img: Image.Image, mask: Image.Image, keep: bool) -> Image.Image:
    """Keep only the masked pixels (keep=True) or only the unmasked ones (keep=False)."""
    img = img.convert("RGBA")
    m = mask if keep else mask.point(lambda v: 255 - v)
    alpha = Image.composite(img.getchannel("A"), Image.new("L", img.size, 0), m)
    out = img.copy()
    out.putalpha(alpha)
    return out


def place(logo: Image.Image, size: tuple[int, int], scale: float, x: int, y: int) -> Image.Image:
    """Scale the logo with premultiplied alpha and put its top-left corner at (x, y)."""
    w, h = round(logo.width * scale), round(logo.height * scale)
    small = logo.convert("RGBa").resize((w, h), Image.LANCZOS).convert("RGBA")
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.alpha_composite(small, (x, y))
    return canvas
