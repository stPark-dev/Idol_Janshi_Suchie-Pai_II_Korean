from PIL import Image

from suchie2 import title

RED, BLUE = 0x001F, 0x7C00          # RGB555: r in low bits
PAL_R = [0, RED] + [0x0000] * 14
PAL_B = [0, BLUE] + [0x0000] * 14


def _sprites():
    return [
        title.Sprite(name="low", rect=(0, 0, 8, 2), palette=PAL_R),
        title.Sprite(name="top", rect=(4, 0, 8, 2), palette=PAL_B),   # drawn above "low"
    ]


def _canvas(pixels):
    im = Image.new("RGBA", (16, 4), (0, 0, 0, 0))
    for (x, y), c in pixels.items():
        im.putpixel((x, y), c)
    return im


def test_pixel_goes_to_best_palette_and_other_sprite_is_transparent():
    im = _canvas({(5, 0): (255, 0, 0, 255), (6, 0): (0, 0, 255, 255)})
    out = title.assign(im, _sprites())
    low, top = out["low"], out["top"]
    assert low.pixel(5, 0) == 1 and top.pixel(1, 0) == 0      # red -> low sprite, top transparent
    assert top.pixel(2, 0) == 1 and low.pixel(6, 0) == 0      # blue -> top sprite


def test_transparent_pixels_are_index_zero():
    out = title.assign(_canvas({}), _sprites())
    assert set(out["low"].data) == {0} and set(out["top"].data) == {0}


def test_uncovered_opaque_pixel_is_reported():
    im = _canvas({(14, 3): (255, 0, 0, 255)})
    try:
        title.assign(im, _sprites())
    except title.LayoutError as e:
        assert "1 opaque pixel" in str(e)
    else:
        raise AssertionError("uncovered pixel was not rejected")


def test_packed_4bpp_high_nibble_is_left_pixel():
    im = _canvas({(0, 0): (255, 0, 0, 255)})
    low = title.assign(im, _sprites())["low"]
    assert low.data[0] == 0x10 and len(low.data) == 8 * 2 // 2


def test_rgb555_decoding():
    assert title.rgb555(0x001F) == (255, 0, 0)
    assert title.rgb555(0x7FFF) == (255, 255, 255)


def _blobs():
    im = Image.new("RGBA", (20, 10), (0, 0, 0, 0))
    for x in range(1, 6):
        for y in range(1, 4):
            im.putpixel((x, y), (255, 200, 0, 255))        # yellow blob
    im.putpixel((1, 4), (120, 0, 0, 255))                  # its dark outline, touching
    for x in range(3, 9):
        for y in range(6, 9):
            im.putpixel((x, y), (255, 80, 160, 255))       # pink blob, separated by a gap row
    im.putpixel((2, 5), (0, 0, 0, 60))                     # faint edge pixel next to yellow outline
    return im


YELLOW = [[200, 255], [150, 255], [0, 120]]


def test_color_groups_split_separated_components():
    match = title.color_group_mask(_blobs(), YELLOW)
    assert match.getpixel((3, 2)) == 255 and match.getpixel((1, 4)) == 255   # fill and outline
    assert match.getpixel((5, 7)) == 0                                         # pink blob excluded
    assert match.getpixel((2, 5)) == 255                                       # faint edge follows neighbour


def test_color_group_touching_component_stays_whole():
    im = _blobs()
    for y in range(4, 6):
        im.putpixel((4, y), (80, 0, 0, 255))                # bridge: pink now touches yellow
    match = title.color_group_mask(im, YELLOW)
    assert match.getpixel((5, 7)) == 255


def test_apply_group_keeps_or_removes_pixels():
    im = _blobs()
    mask = title.color_group_mask(im, YELLOW)
    only = title.apply_mask(im, mask, keep=True)
    rest = title.apply_mask(im, mask, keep=False)
    assert only.getpixel((5, 7))[3] == 0 and only.getpixel((3, 2))[3] == 255
    assert rest.getpixel((3, 2))[3] == 0 and rest.getpixel((5, 7))[3] == 255


def test_equal_error_tie_goes_to_first_listed_sprite():
    same = [0, RED] + [0] * 14
    sprites = [title.Sprite("a", (0, 0, 8, 2), same), title.Sprite("b", (0, 0, 8, 2), same)]
    out = title.assign(_canvas({(1, 1): (255, 0, 0, 255)}), sprites)
    assert out["a"].pixel(1, 1) == 1 and out["b"].pixel(1, 1) == 0
