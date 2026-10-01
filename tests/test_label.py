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


def test_clean_rows_can_borrow_only_listed_rows():
    t = _tex(["dddd", "1221", "dddd"])
    with pytest.raises(label.LabelError):
        label.clean_rows(t, (0, 0, 4, 3), text_idx={1, 2})
    label.clean_rows(t, (0, 0, 4, 3), text_idx={1, 2}, borrow_rows={1})
    assert _rows(t) == ["dddd", "dddd", "dddd"]


def test_clean_rows_unlisted_empty_row_still_fails():
    t = _tex(["dddd", "1221", "2112", "dddd"])
    with pytest.raises(label.LabelError, match="row 2"):
        label.clean_rows(t, (0, 0, 4, 4), text_idx={1, 2}, borrow_rows={1})


def test_clean_rows_borrow_tie_prefers_upper_row_and_all_empty_fails():
    t = _tex(["aaaa", "1221", "bbbb"])
    label.clean_rows(t, (0, 0, 4, 3), text_idx={1, 2}, borrow_rows={1})
    assert _rows(t)[1] == "aaaa"
    with pytest.raises(label.LabelError):
        label.clean_rows(_tex(["1111", "2222"]), (0, 0, 4, 2), text_idx={1, 2}, borrow_rows={0, 1})


def test_render_lines_outline_per_segment():
    pal = [0, 0x001F, 0x03E0, 0x7C00, 0x7FFF] + [0] * 11   # red, green, blue, white
    layer = label.render_lines((60, 20), [
        {"text": "가|나", "size": 14, "x": 2, "y": 2, "fill": 4, "outline": [1, 3], "aa": False}], pal)
    px = layer.load()
    left = {px[x, y][:3] for x in range(0, 14) for y in range(20) if px[x, y][3] == 255}
    right = {px[x, y][:3] for x in range(18, 40) for y in range(20) if px[x, y][3] == 255}
    assert (255, 0, 0) in left and (0, 0, 255) not in left
    assert (0, 0, 255) in right and (255, 0, 0) not in right
    assert (255, 255, 255) in left and (255, 255, 255) in right
    with pytest.raises(label.LabelError, match="outline"):
        label.render_lines((60, 20), [{"text": "가|나", "size": 14, "x": 2, "y": 2, "fill": 4, "outline": [1]}], pal)
    with pytest.raises(label.LabelError, match="outline"):
        label.render_lines((60, 20), [{"text": "가|나", "size": 14, "x": 2, "y": 2, "fill": 4, "outline": [1, None]}], pal)


def test_render_lines_empty_text_draws_nothing_but_blank_looking_text_is_rejected():
    pal = [0, 0x7FFF] + [0] * 14
    line = {"size": 12, "weight": 6, "x": "center", "y": "center", "fill": 1, "aa": False}
    assert label.render_lines((32, 16), [{**line, "text": ""}], pal).getbbox() is None
    for text in (" ", "　", "​"):
        with pytest.raises(label.LabelError, match="nothing visible"):
            label.render_lines((32, 16), [{**line, "text": text}], pal)


def _vline(text, **over):
    return {"text": text, "size": 13, "x": "center", "y": "top", "area": [0, 0, 16, 128], "fill": 1,
            "vertical": True, "aa": False, **over}


def test_vertical_text_stacks_glyphs_top_to_bottom():
    pal = [0, 0x7FFF] + [0] * 14
    one = label.render_lines((16, 128), [_vline("가")], pal).getbbox()
    three = label.render_lines((16, 128), [_vline("가나다")], pal).getbbox()
    assert three[2] - three[0] <= 16 and (three[3] - three[1]) > 2.5 * (one[3] - one[1])


def test_vertical_pairs_of_digits_and_marks_share_one_cell():
    pal = [0, 0x7FFF] + [0] * 14
    a = label.render_lines((16, 128), [_vline("가16가")], pal).getbbox()
    b = label.render_lines((16, 128), [_vline("가가가")], pal).getbbox()
    c = label.render_lines((16, 128), [_vline("가!!")], pal).getbbox()
    d = label.render_lines((16, 128), [_vline("가가")], pal).getbbox()
    assert abs((a[3] - a[1]) - (b[3] - b[1])) <= 3 and (c[3] - c[1]) <= (d[3] - d[1]) + 1


def test_vertical_segments_colour_their_own_cells_and_overflow_fails():
    pal = [0, 0x001F, 0x7C00] + [0] * 13
    layer = label.render_lines((16, 128), [_vline("가|나", fill=[1, 2])], pal)
    px = layer.load()
    top = {px[x, y][:3] for x in range(16) for y in range(0, 14) if px[x, y][3] == 255}
    low = {px[x, y][:3] for x in range(16) for y in range(15, 30) if px[x, y][3] == 255}
    assert (255, 0, 0) in top and (0, 0, 255) in low and (0, 0, 255) not in top
    with pytest.raises(label.LabelError, match="does not fit|runs past"):
        label.render_lines((16, 128), [_vline("가나다라마바사아자차카")], pal)


def test_vertical_marks_keep_full_height_and_ellipsis_turns_upright():
    pal = [0, 0x7FFF] + [0] * 14
    bang = label.render_lines((16, 128), [_vline("!!")], pal).getbbox()
    one = label.render_lines((16, 128), [_vline("!")], pal).getbbox()
    assert (bang[3] - bang[1]) >= (one[3] - one[1]) - 1           # not shrunk
    dots = label.render_lines((16, 128), [_vline("…")], pal).getbbox()
    assert (dots[3] - dots[1]) > (dots[2] - dots[0])               # upright: taller than wide


def test_vertical_two_digit_cell_keeps_glyph_height():
    pal = [0, 0x7FFF] + [0] * 14
    two = label.render_lines((16, 128), [_vline("16")], pal).getbbox()
    one = label.render_lines((16, 128), [_vline("1")], pal).getbbox()
    assert two[2] - two[0] <= 16 and (two[3] - two[1]) >= (one[3] - one[1]) - 1


@pytest.mark.parametrize("line", [dict(text="가나다라", step=40),                    # last cell beyond the canvas
                                  dict(text="가나다라마바사아   자"),
                                  dict(text="가나다라마바사아", area=[0, 0, 16, 100]),  # runs past the area
                                  dict(text="가나다", step=8, y=4)],                   # glyphs taller than a cell
                         ids=["off-canvas", "spaces", "past-area", "overlap"])
def test_vertical_never_drops_or_overlaps_cells_silently(line):
    pal = [0, 0x7FFF] + [0] * 14
    with pytest.raises(label.LabelError):
        label.render_lines((16, 128), [_vline(**line)], pal)


def test_vertical_narrow_column_is_a_label_error_and_ascii_dash_stays():
    pal = [0, 0x7FFF] + [0] * 14
    with pytest.raises(label.LabelError):
        label.render_lines((16, 128), [_vline("16", area=[0, 0, 2, 128])], pal)
    dash = label.render_lines((16, 128), [_vline("-")], pal).getbbox()
    assert (dash[2] - dash[0]) >= (dash[3] - dash[1])               # ASCII '-' is not turned upright


def test_vertical_space_is_half_a_cell_and_outline_hearts_render():
    pal = [0, 0x7FFF, 0x001F, 0x03E0, 0x7C00] + [0] * 11
    a = label.render_lines((16, 128), [_vline("가 가")], pal).getbbox()
    b = label.render_lines((16, 128), [_vline("가가")], pal).getbbox()
    assert 4 <= (a[3] - a[1]) - (b[3] - b[1]) <= 9
    layer = label.render_lines((16, 128), [_vline("해냈어|♥", fill=[1, 2], outline=[3, 4])], pal)
    cols = {layer.getpixel((x, y))[:3] for x in range(16) for y in range(128) if layer.getpixel((x, y))[3] == 255}
    assert {(255, 0, 0), (0, 0, 255)} <= cols


def test_shear_slants_glyphs_to_the_right_and_still_checks_the_box():
    pal = [0, 0x7FFF] + [0] * 14
    line = {"text": "ㅣ", "size": 30, "x": "center", "y": "center", "fill": 1, "aa": False}
    up = label.render_lines((60, 40), [line], pal)
    sl = label.render_lines((60, 40), [{**line, "shear": 0.3}], pal)
    top = lambda im: min(x for x in range(60) for y in range(8, 12) if im.getpixel((x, y))[3])
    bottom = lambda im: min(x for x in range(60) for y in range(28, 32) if im.getpixel((x, y))[3])
    assert top(up) == bottom(up) and top(sl) - bottom(sl) >= 4        # top leans right
    with pytest.raises(label.LabelError, match="does not fit"):
        label.render_lines((60, 40), [{**line, "shear": 3.0}], pal)


@pytest.mark.parametrize("canvas,line", [((60, 40), {"text": "ㅣ|ㅣ", "size": 14, "x": 46, "y": 24, "fill": [1, 2], "shear": 1.0}),
                                         ((80, 40), {"text": "ㅣ|          ㅣ", "size": 10, "x": 0, "y": 28, "fill": [1, 2], "shear": 2}),
                                         ((60, 40), {"text": "ㅣ|ㅣ", "size": 14, "x": 52, "y": 24, "fill": [1, 2]})],
                         ids=["shear-pulls-cut-ink-in", "shear-pushes-segment-out", "segment-drawn-off-canvas"])
def test_no_segment_is_silently_cut_by_the_canvas(canvas, line):
    pal = [0, 0x7FFF, 0x001F] + [0] * 13
    with pytest.raises(label.LabelError):
        label.render_lines(canvas, [{**line, "aa": False}], pal)


def test_clean_rows_on_8bpp_textures_with_high_indices():
    from suchie2.title import Texture
    t = Texture(4, 2, bytearray([50, 181, 204, 50, 60, 60, 204, 60]), bpp=8)
    label.clean_rows(t, (0, 0, 4, 2), {181, 204})
    assert list(t.data) == [50, 50, 50, 50, 60, 60, 60, 60]
