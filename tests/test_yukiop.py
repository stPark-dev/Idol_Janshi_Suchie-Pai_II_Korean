"""YUKIOP.BIN, the Suchie-Yuki opening (stage select "Suchie Yuki"): title logo and story card."""
import json
from pathlib import Path

import numpy as np
import pytest

from suchie2 import rle16, yc, yukiop as Y

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "work/disc1/fs/YUKIOP.BIN"


def _bundle(n=8):
    ents = [yc.Entry(16, 4, 0x80, 0x10, bytes(range(32)))] * (n - 1)
    ents.append(yc.Entry(8, 2, 0xA8, 0, bytes(range(32))))
    return yc.build(ents)


def _file(bundle=None):
    d = bytearray(Y.SIZE)
    d[Y.PAL[0]:Y.PAL[1]] = bytes(range(256)) * 8
    s = rle16.compress(bundle or _bundle())
    d[Y.BUNDLE:Y.BUNDLE + len(s)] = s
    d[Y.STORY_PAL:Y.STORY] = bytes(range(256)) * 2
    d[Y.STORY:Y.STORY_END] = bytes(i % 251 for i in range(Y.STORY_END - Y.STORY))
    return bytes(d)


def test_layout_explains_the_whole_file():
    assert Y.PAL == (0x0, 0x800) and Y.BUNDLE == 0x1000 and Y.STORY_PAL == 0x38000
    assert Y.STORY == Y.STORY_PAL + 0x200 and Y.STORY_END == Y.STORY + 320 * 240 and Y.STORY_END <= Y.SIZE


def test_parse_and_assemble_round_trip():
    d = _file()
    p = Y.parse(d)
    assert p.bundle == _bundle() and p.story == d[Y.STORY:Y.STORY_END]
    assert Y.assemble(d, p.bundle, p.story) == d


@pytest.mark.parametrize("at", [0x900, Y.STORY_PAL - 2, Y.STORY_END + 4])
def test_parse_rejects_data_where_zeros_belong(at):
    d = bytearray(_file())
    d[at] = 1
    with pytest.raises(Y.YukiopError, match="zero"):
        Y.parse(bytes(d))


def test_assemble_rejects_a_bundle_that_outgrows_its_region():
    d = _file()
    big = yc.build([yc.Entry(320, 240, 0xA8, 0, bytes((i * 7919) % 256 for i in range(153600)))] * 3)
    with pytest.raises(Y.YukiopError, match="exceeds"):
        Y.assemble(d, big, Y.parse(d).story)


def test_fill_changes_only_the_hole_and_is_deterministic():
    rgb = np.zeros((20, 30, 3), int)
    rgb[..., 0] = np.arange(30)[None, :]          # horizontal ramp
    rgb[..., 2] = 17
    hole = np.zeros((20, 30), bool)
    hole[5:15, 10:20] = True
    out = Y.fill(rgb, hole)
    assert out.dtype.kind == "i" and (out[~hole] == rgb[~hole]).all()
    assert (out[hole][:, 2] == 17).all()
    assert np.abs(out[hole][:, 0] - rgb[hole][:, 0]).max() <= 6
    assert (Y.fill(rgb, hole) == out).all()


def test_sprite_palette_is_read_back_from_the_picture():
    pic = np.full((40, 60), 0x1234, np.int64)
    spr = np.zeros((8, 10), int)
    spr[2:6, 2:8] = 3
    spr[3, 4] = 5
    cols = {3: 0x7C00, 5: 0x03E0}
    for k, c in cols.items():
        pic[10:18, 20:30][spr == k] = c
    pal = Y.sprite_palette(pic, spr, (20, 10))
    assert pal[3] == 0x7C00 and pal[5] == 0x03E0 and pal[0] == 0
    pic[12, 23] = 0x0001
    with pytest.raises(Y.YukiopError, match="colour"):
        Y.sprite_palette(pic, spr, (20, 10))


def test_split_logo_separates_the_upper_line():
    a = np.zeros((100, 200, 4), np.uint8)
    a[10:40, 20:120] = (255, 200, 0, 255)          # upper line
    a[60:95, 10:190] = (255, 0, 160, 255)          # main logo
    a[40:60, 60:62] = (80, 0, 40, 255)             # outlines touching between them
    top, main = Y.split_logo(a, search=(30, 70))
    assert top[10:40, 20:120, 3].all() and not top[60:, :, 3].any()
    assert main[60:95, 10:190, 3].all() and not main[:40, :, 3].any()


def test_fit_logo_uses_only_allowed_indices_and_keeps_transparency():
    a = np.zeros((50, 100, 4), np.uint8)
    a[10:40, 10:90] = (255, 0, 160, 255)
    a[20:30, 30:70] = (255, 255, 255, 255)
    pal = [0] * 16
    pal[4], pal[13], pal[9] = 0x5C1E, 0x7FFF, 0x1809
    idx = Y.fit_logo(a, (40, 16), pal, [4, 9, 13])
    assert idx.shape == (16, 40)
    assert set(np.unique(idx)) <= {0, 4, 9, 13} and {4, 13} <= set(np.unique(idx))
    assert not idx.all(1).all() and ((idx == 0).all(1).any() or (idx == 0).all(0).any())   # aspect kept: a blank row or column


def test_erase_board_borrows_board_texture_only():
    rng = np.random.default_rng(1)
    img = rng.choice([153, 154], size=(60, 80))
    img[20:30, 30:50] = 255
    out = Y.erase_board(img, (10, 10, 70, 50), (153, 154), period=18)
    assert set(np.unique(out[10:50, 10:70])) <= {153, 154}
    assert (out[:10] == img[:10]).all() and (out[50:] == img[50:]).all()


def test_real_file_changes_only_logo_and_story_board():
    if not SRC.exists():
        pytest.skip("needs the extracted YUKIOP.BIN")
    data = SRC.read_bytes()
    new, res = Y.render(data, ROOT / "translation/yukiop.json", ROOT / "assets/yuki/layout.json")
    assert len(new) == len(data)
    old, now = Y.parse(data), Y.parse(new)
    a, b = yc.parse(old.bundle), yc.parse(now.bundle)
    assert [(e.width, e.height, e.attr, e.colr) for e in a] == [(e.width, e.height, e.attr, e.colr) for e in b]
    assert all(a[i].data == b[i].data for i in range(5))
    p0 = np.frombuffer(a[7].data, ">u2").reshape(240, 320)
    p1 = np.frombuffer(b[7].data, ">u2").reshape(240, 320)
    ys, xs = np.nonzero(p0 != p1)
    lay = json.loads((ROOT / "assets/yuki/layout.json").read_text())
    x0, y0 = min(lay["logo"]["at"][0], lay["small"]["at"][0]), lay["small"]["at"][1]
    assert xs.min() >= x0 and ys.min() >= y0 and xs.max() < x0 + 192 + 2 and ys.max() < lay["logo"]["at"][1] + 64
    s0 = np.frombuffer(old.story, np.uint8).reshape(240, 320)
    s1 = np.frombuffer(now.story, np.uint8).reshape(240, 320)
    ys, xs = np.nonzero(s0 != s1)
    bx0, by0, bx1, by1 = lay["story"]["box"]
    assert bx0 <= xs.min() and xs.max() < bx1 and by0 <= ys.min() and ys.max() < by1
    assert new[Y.STORY_PAL:Y.STORY] == data[Y.STORY_PAL:Y.STORY] and new[Y.PAL[0]:Y.PAL[1]] == data[Y.PAL[0]:Y.PAL[1]]
    assert res.ids == ["yukiop.logo", "yukiop.story"]


# ---- guards (review 2026-10-16) -----------------------------------------------------------
FONT = "/usr/share/fonts/truetype/nanum/NanumBarunpenB.ttf"


def _board():
    rng = np.random.default_rng(2)
    return rng.choice([153, 154], size=(60, 80))


def _pal():
    pal = np.zeros(256, np.int64)
    pal[153], pal[154], pal[255] = 0x2280, 0x2A80, 0x7FFF
    return pal


def test_write_board_rejects_a_line_cut_at_the_bottom():
    with pytest.raises(Y.YukiopError, match="outside"):
        Y.write_board(_board(), _pal(), (10, 10, 70, 40), [{"font": FONT, "size": 19, "y": 20}], ["가나"], [153, 154, 255])


def test_write_board_rejects_a_right_aligned_line_past_the_left_edge():
    with pytest.raises(Y.YukiopError, match="outside"):
        Y.write_board(_board(), _pal(), (10, 10, 70, 50),
                      [{"font": FONT, "size": 16, "y": 0, "align": "right", "right": 50}], ["가나다"], [153, 154, 255])


def test_write_board_rejects_a_missing_glyph():
    with pytest.raises(Y.YukiopError, match="glyph"):
        Y.write_board(_board(), _pal(), (10, 10, 70, 50), [{"font": FONT, "size": 16, "y": 0}], ["가"], [153, 154, 255])


def test_erase_board_fails_without_texture_nearby():
    img = _board()
    img[10:50, 10:70] = 255
    with pytest.raises(Y.YukiopError, match="texture"):
        Y.erase_board(img, (10, 10, 70, 50), (153, 154), period=18)


def test_rect_checks_reject_a_sprite_outside_the_picture():
    with pytest.raises(Y.YukiopError, match="outside"):
        Y.check_rect((300, 200, 192, 64), (320, 240), "logo")
    Y.check_rect((65, 151, 192, 64), (320, 240), "logo")


def _tables(tmp_path, data, logo=None, story=None):
    import hashlib
    tr = {"yukiop": True, "file": "YUKIOP.BIN", "source_sha1": hashlib.sha1(data).hexdigest(),
          "entries": [e for e in (logo, story) if e is not None]}
    (tmp_path / "translation").mkdir(exist_ok=True)
    p = tmp_path / "translation" / "y.json"
    p.write_text(json.dumps(tr, ensure_ascii=False))
    lay = tmp_path / "lay.json"
    lay.write_text(json.dumps({}))
    return p, lay


def test_render_rejects_a_different_source(tmp_path):
    d = _file()
    tr, lay = _tables(tmp_path, d, story={"id": "yukiop.story", "ko": "가", "state": "needs_review"})
    with pytest.raises(Y.YukiopError, match="source"):
        Y.render(d[:-1] + b"\x01", tr, lay)


def test_render_rejects_an_entry_with_nothing_to_draw(tmp_path):
    d = _file()
    tr, lay = _tables(tmp_path, d, logo={"id": "yukiop.logo", "state": "needs_review"})
    with pytest.raises(Y.YukiopError, match="nothing"):
        Y.render(d, tr, lay)


def test_render_reports_a_missing_logo_image(tmp_path):
    d = _file()
    tr, lay = _tables(tmp_path, d, logo={"id": "yukiop.logo", "image": "nope.png", "image_sha1": "0" * 40,
                                         "state": "needs_review"})
    with pytest.raises(Y.YukiopError, match="nope.png"):
        Y.render(d, tr, lay)
