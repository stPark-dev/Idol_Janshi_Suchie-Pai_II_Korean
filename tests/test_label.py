import pytest
from PIL import Image

from suchie2 import label
from suchie2.title import Texture


def _tex(rows):
    w, h = len(rows[0]), len(rows)
    t = Texture(w, h, bytearray(w * h // 2))
    for y, r in enumerate(rows):
        for x, ch in enumerate(r):
            t.set(x, y, int(ch, 16))
    return t


def _rows(t):
    return ["".join("%x" % t.pixel(x, y) for x in range(t.width)) for y in range(t.height)]


def test_clean_rows_fills_text_with_row_background():
    t = _tex(["89999998",
              "8a3113a8",
              "8b1221b8",
              "88888888"])
    label.clean_rows(t, (1, 0, 7, 4), text_idx={1, 2, 3})
    assert _rows(t) == ["89999998", "8aaaaaa8", "8bbbbbb8", "88888888"]


def test_clean_rows_leaves_pixels_outside_box():
    t = _tex(["31911913",
              "31911913"])
    label.clean_rows(t, (2, 0, 6, 2), text_idx={1})
    assert _rows(t) == ["31999913", "31999913"]


def test_clean_rows_row_without_background_raises():
    t = _tex(["1111", "2222"])
    with pytest.raises(label.LabelError):
        label.clean_rows(t, (0, 0, 4, 2), text_idx={1, 2})


def test_clean_tile_restores_periodic_pattern():
    base = ["cfcf" * 4] * 4
    rows = [list(r) for r in base]
    rows[1][4:8] = list("1221")
    rows[2][5:7] = list("33")
    t = _tex(["".join(r) for r in rows])
    label.clean_tile(t, (0, 0, 16, 4), text_idx={1, 2, 3}, period=(2, 1))
    assert _rows(t) == base


def test_compose_plain_text_on_transparent():
    t = Texture(16, 8, bytearray(64))
    pal = [0, 0x001F, 0x7FFF] + [0] * 13     # 1 red, 2 white
    layer = Image.new("RGBA", (16, 8), (0, 0, 0, 0))
    for x in range(3, 9):
        layer.putpixel((x, 4), (250, 0, 0, 255))
    layer.putpixel((10, 4), (255, 255, 255, 60))          # faint: dropped on transparent bg
    label.compose(t, pal, (0, 0, 16, 8), layer, allowed=range(1, 16))
    assert [t.pixel(x, 4) for x in range(3, 9)] == [1] * 6
    assert t.pixel(10, 4) == 0 and t.pixel(0, 0) == 0


def test_compose_blends_over_opaque_background_and_respects_box():
    t = _tex(["4444", "4444"])
    pal = [0, 0x0000, 0x7FFF, 0x3DEF, 0x0000] + [0] * 11   # 1 black, 2 white, 3 grey, 4 black bg
    layer = Image.new("RGBA", (4, 2), (255, 255, 255, 128))   # half white everywhere
    label.compose(t, pal, (0, 0, 2, 2), layer, allowed=[1, 2, 3])
    assert _rows(t) == ["3344", "3344"]                      # grey inside box, untouched outside


def test_render_lines_draws_outline_and_fill():
    pal = [0, 0x001F, 0x7FFF] + [0] * 13
    layer = label.render_lines((40, 20), [
        {"text": "가", "size": 14, "x": "center", "y": 3, "fill": 2, "outline": 1}], pal)
    px = layer.load()
    colours = {px[x, y][:3] for x in range(40) for y in range(20) if px[x, y][3] == 255}
    assert (255, 255, 255) in colours and (255, 0, 0) in colours


def test_render_lines_rejects_overflow():
    with pytest.raises(label.LabelError, match="does not fit"):
        label.render_lines((20, 10), [{"text": "스테이지 선택", "size": 14, "x": 0, "y": 0, "fill": 1}],
                           [0, 0x7FFF] + [0] * 14)


def test_render_lines_colour_segments():
    pal = [0, 0x001F, 0x03E0, 0x7C00] + [0] * 12   # red, green, blue
    layer = label.render_lines((60, 20), [
        {"text": "가|나", "size": 14, "x": 0, "y": 2, "fill": [1, 3]}], pal)
    px = layer.load()
    left = {px[x, y][:3] for x in range(0, 14) for y in range(20) if px[x, y][3] == 255}
    right = {px[x, y][:3] for x in range(16, 40) for y in range(20) if px[x, y][3] == 255}
    assert (255, 0, 0) in left and (0, 0, 255) in right


def test_render_lines_centres_inside_area():
    pal = [0, 0x7FFF] + [0] * 14
    layer = label.render_lines((80, 30), [
        {"text": "가", "size": 12, "x": "center", "y": "center", "area": [40, 10, 80, 30], "fill": 1}], pal)
    x0, y0, x1, y1 = layer.getbbox()
    assert 40 <= x0 and x1 <= 80 and 10 <= y0 and y1 <= 30
    assert abs((x0 + x1) / 2 - 60) <= 1.5 and abs((y0 + y1) / 2 - 20) <= 1.5


def test_clear_box_sets_only_box_to_transparent():
    t = _tex(["1234", "5678"])
    label.clear_box(t, (1, 0, 3, 2))
    assert _rows(t) == ["1004", "5008"]


def test_render_lines_vertical_gradient_fill():
    pal = [0, 0x7FFF, 0x001F] + [0] * 13          # 1 white (top), 2 red (bottom)
    layer = label.render_lines((40, 30), [
        {"text": "를", "size": 20, "x": "center", "y": "center", "fill": {"v": [1, 2]}}], pal)
    x0, y0, x1, y1 = layer.getbbox()
    px = layer.load()
    top = {px[x, y][:3] for x in range(x0, x1) for y in range(y0, y0 + 3) if px[x, y][3] == 255}
    bottom = {px[x, y][:3] for x in range(x0, x1) for y in range(y1 - 3, y1) if px[x, y][3] == 255}
    assert top == {(255, 255, 255)} and bottom == {(255, 0, 0)}


def test_render_lines_weight_thickens_glyph():
    pal = [0, 0x7FFF] + [0] * 14
    base = label.render_lines((40, 24), [{"text": "가", "size": 14, "x": 4, "y": 4, "fill": 1}], pal)
    bold = label.render_lines((40, 24), [{"text": "가", "size": 14, "x": 4, "y": 4, "fill": 1, "weight": 1}], pal)
    count = lambda im: sum(1 for a in im.getchannel("A").getdata() if a == 255)  # noqa: E731
    assert count(bold) > count(base) * 1.3


def test_render_lines_without_antialiasing_has_no_partial_alpha():
    pal = [0, 0x7FFF] + [0] * 14
    im = label.render_lines((40, 24), [{"text": "하", "size": 13, "x": 4, "y": 4, "fill": 1, "aa": False}], pal)
    assert set(im.getchannel("A").getdata()) <= {0, 255}


def test_clean_tile_never_copies_from_outside_the_box():
    rows = ["de" + "cf" * 8 for _ in range(3)]
    grid = [list(r) for r in rows]
    for y in range(3):
        for x in range(2, 14):
            grid[y][x] = "1"                                   # text over most of the pattern
    t = _tex(["".join(r) for r in grid])
    label.clean_tile(t, (2, 0, 18, 3), text_idx={1}, period=(2, 0))
    inside = {t.pixel(x, y) for y in range(3) for x in range(2, 18)}
    assert inside <= {0xC, 0xF}


@pytest.mark.parametrize("line", [
    {"text": "가", "size": 14, "x": 2, "y": 0, "fill": 1, "weight": 3},           # stroke grows past top/left (-1,-2)
    {"text": "가", "size": 14, "x": 0, "y": 0, "fill": 1, "outline": 1},          # outline cut at the left (-1)
    {"text": "가", "size": 14, "x": 20, "y": 4, "fill": 1, "shadow": {"idx": 1, "dx": 8, "dy": 0}},
    {"text": "가", "size": 14, "x": 4, "y": 4, "fill": 1, "shadow": {"idx": 1, "dx": -8, "dy": 0}},
])
def test_render_lines_rejects_any_clipping(line):
    with pytest.raises(label.LabelError, match="does not fit"):
        label.render_lines((40, 30), [line], [0, 0x7FFF] + [0] * 14)


def test_render_lines_checks_against_given_box():
    line = {"text": "가", "size": 14, "x": 2, "y": 2, "fill": 1}
    label.render_lines((40, 30), [line], [0, 0x7FFF] + [0] * 14)
    with pytest.raises(label.LabelError, match="does not fit"):
        label.render_lines((40, 30), [line], [0, 0x7FFF] + [0] * 14, box=(0, 0, 40, 10))


def test_shadow_is_offset_not_wrapped():
    pal = [0, 0x7FFF, 0x001F] + [0] * 13
    im = label.render_lines((40, 30), [{"text": "가", "size": 12, "x": 4, "y": 4, "fill": 1,
                                        "shadow": {"idx": 2, "dx": 3, "dy": 2}}], pal)
    x0, y0, x1, y1 = im.getbbox()
    assert x0 >= 4 and y0 >= 4


def test_centring_accounts_for_weight():
    pal = [0, 0x7FFF] + [0] * 14
    im = label.render_lines((60, 30), [{"text": "가", "size": 12, "x": "center", "y": "center",
                                        "fill": 1, "weight": 2}], pal)
    x0, y0, x1, y1 = im.getbbox()
    assert abs((x0 + x1) / 2 - 30) <= 1.5 and abs((y0 + y1) / 2 - 15) <= 1.5
