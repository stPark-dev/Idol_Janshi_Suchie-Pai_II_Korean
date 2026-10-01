"""Roulette wheel: one 256x256 8bpp picture split over four 128x128 Yc entries (TL, TR, BL, BR);
the text of each wedge is replaced in place (docs/initial-survey.md 3.18)."""
import hashlib
import json

import pytest

from suchie2 import select1, wheel, yc

BG, TEXT, OUT = 1, 2, 3              # wedge background, Japanese text, outside every wedge
RGB = {BG: (0, 82, 255), TEXT: (49, 239, 156), OUT: (99, 99, 99), 4: (255, 247, 0), 5: (41, 41, 66)}
COLR = 0x400
CRAM = 0x1000                        # CRAM image size; the bundle follows it


def _word(rgb):
    r, g, b = (round(v * 31 / 255) for v in rgb)
    return (b << 10) | (g << 5) | r


def _pal():
    raw = bytearray(CRAM)
    for k, rgb in RGB.items():
        o = 2 * (COLR + k)
        raw[o:o + 2] = _word(rgb).to_bytes(2, "big")
    return raw


def _picture(centre=90, text=True):
    W = [bytearray([OUT]) * 256 for _ in range(256)]
    for y in range(256):
        for x in range(256):
            if wheel.in_wedge(x, y, centre, 40, 118, 22.5):
                W[y][x] = BG
                r, _ = wheel.polar(x, y)
                if text and 80 <= r <= 90 and abs(x - 128) < 10:
                    W[y][x] = TEXT
    return W


def _file(W=None):
    quads = wheel.split(W or _picture())
    ents = [yc.Entry(128, 128, 0x0020, COLR, q) for q in quads]       # mode 4: 256 colours
    return bytes(_pal()) + yc.build(ents)


def _tables(tmp_path, data, tr_over=None, lay_over=None, entry_over=None, spec_over=None):
    ents = select1.entries(data, CRAM)
    tr = {"file": "P.BIN", "bundle_offset": hex(CRAM), "palette_offset": "0x0", "id_prefix": "w.",
          "wheels": [{"name": "A", "entries": [0, 1, 2, 3], "colr": hex(COLR),
                      "src_sha1": [hashlib.sha1(e.data).hexdigest() for e in ents],
                      "palette_sha1": hashlib.sha1(select1.palette_bytes(data, COLR, 0, 256)).hexdigest()}],
          "entries": [{"id": "w.reset", "ja": "リセット", "ko": "리셋", "state": "needs_review", "terms": [], "note": ""}]}
    tr["entries"][0].update(entry_over or {})
    tr.update(tr_over or {})
    lay = {"wheels": {"A": {"rotation": 0}},
           "clean": {"r0": 50, "r1": 106, "rim": 117, "half": 20.5, "dmax": 110},
           "ink": {"r0": 48, "r1": 114, "half": 21.5},
           "entries": [{"id": "w.reset", "angle": 90, "r": [62, 110],
                        "lines": [{"size": 20, "fill": [255, 247, 0], "outline": [41, 41, 66]}]}]}
    lay["entries"][0].update(spec_over or {})
    lay.update(lay_over or {})
    (tmp_path / "tr.json").write_text(json.dumps(tr, ensure_ascii=False))
    (tmp_path / "lay.json").write_text(json.dumps(lay, ensure_ascii=False))
    return tmp_path / "tr.json", tmp_path / "lay.json"


def _assembled(res):
    return wheel.assemble([res.textures[i] for i in range(4)])


# geometry ---------------------------------------------------------------------------------

def test_split_and_assemble_use_tl_tr_bl_br_order():
    W = [bytearray(256) for _ in range(256)]
    W[0][0], W[0][255], W[255][0], W[255][255] = 1, 2, 3, 4
    q = wheel.split(W)
    assert [q[0][0], q[1][127], q[2][127 * 128], q[3][128 * 128 - 1]] == [1, 2, 3, 4]
    assert wheel.assemble(q) == W


def test_assemble_rejects_wrong_quadrant_size():
    with pytest.raises(wheel.WheelError, match="128x128"):
        wheel.assemble([bytes(10)] * 4)


def test_angles_run_counter_clockwise_from_east():
    assert wheel.polar(200, 127)[1] == pytest.approx(0, abs=1)
    assert wheel.polar(127, 50)[1] == pytest.approx(90, abs=1)
    assert wheel.polar(50, 127)[1] == pytest.approx(180, abs=1)
    assert wheel.polar(127, 200)[1] == pytest.approx(270, abs=1)
    assert wheel.in_wedge(127, 50, 90, 40, 118, 22.5) and not wheel.in_wedge(200, 127, 90, 40, 118, 22.5)


# cleaning ---------------------------------------------------------------------------------

def test_clean_replaces_foreign_pixels_and_keeps_the_rest():
    W = _picture()
    rgb = {k: v for k, v in RGB.items()}
    pal = [rgb.get(k, (0, 0, 0)) for k in range(256)]
    before = [bytearray(r) for r in W]
    wheel.clean_wedge(W, pal, 90, r0=50, r1=106, rim=117, half=20.5, dmax=110)
    assert not any(TEXT in row for row in W)
    changed = {(x, y) for y in range(256) for x in range(256) if W[y][x] != before[y][x]}
    assert changed == {(x, y) for y in range(256) for x in range(256) if before[y][x] == TEXT}


def test_clean_keeps_rim_colours_that_belong_to_the_ring():
    W = _picture(text=False)
    for y in range(256):
        for x in range(256):
            if wheel.in_wedge(x, y, 90, 110, 118, 22.5):
                W[y][x] = 4                          # a rim band: far from the base hue, but a full ring
    before = [bytearray(r) for r in W]
    pal = [RGB.get(k, (0, 0, 0)) for k in range(256)]
    wheel.clean_wedge(W, pal, 90, r0=50, r1=106, rim=117, half=20.5, dmax=110)
    assert W == before


# drawing ----------------------------------------------------------------------------------

def _ink(W, before):
    return [(x, y) for y in range(256) for x in range(256) if W[y][x] != before[y][x]]


def _pal555():
    return [_word(RGB[k]) if k in RGB else 0 for k in range(256)]


@pytest.mark.parametrize("angle, side", [(270, "below"), (0, "right"), (90, "above")])
def test_text_is_drawn_inside_its_wedge(angle, side):
    W = [bytearray([BG]) * 256 for _ in range(256)]
    before = [bytearray(r) for r in W]
    lines = [{"size": 20, "fill": 4, "outline": 5}]
    wheel.draw_wedge(W, _pal555(), angle, lines, ["리셋"], (62, 110), ink=(48, 114, 21.5))
    ink = _ink(W, before)
    assert ink and all(wheel.in_wedge(x, y, angle, 48, 114, 21.5) for x, y in ink)
    xs, ys = [x for x, _ in ink], [y for _, y in ink]
    assert {"below": min(ys) > 128, "right": min(xs) > 128, "above": max(ys) < 128}[side]


def test_text_reads_upright_when_its_wedge_points_down():
    # first line sits nearest the hub: at 270 degrees line 1 is above line 2
    W = [bytearray([BG]) * 256 for _ in range(256)]
    lines = [{"size": 16, "fill": 4}, {"size": 16, "fill": 5}]
    wheel.draw_wedge(W, _pal555(), 270, lines, ["가가", "나나"], (62, 110), ink=(48, 114, 21.5))
    y4 = [y for y in range(256) for x in range(256) if W[y][x] == 4]
    y5 = [y for y in range(256) for x in range(256) if W[y][x] == 5]
    assert max(y4) < min(y5)


def test_text_leaving_the_wedge_is_an_error_not_clipped():
    W = [bytearray([BG]) * 256 for _ in range(256)]
    with pytest.raises(wheel.WheelError, match="leaves"):
        wheel.draw_wedge(W, _pal555(), 90, [{"size": 40, "fill": 4}], ["서비스!!"], (62, 110), ink=(48, 114, 21.5))


# tables -----------------------------------------------------------------------------------

def test_render_replaces_the_wedge_text_on_all_four_entries(tmp_path):
    data = _file()
    res = wheel.render(data, *_tables(tmp_path, data))
    W = _assembled(res)
    assert not any(TEXT in row for row in W)
    assert sum(row.count(4) for row in W) > 20
    assert sorted(res.textures) == [0, 1, 2, 3]
    assert res.ids == ["w.reset"] and res.states == {"needs_review": 1} and res.distribution is False


def test_wheel_rotation_moves_the_wedge(tmp_path):
    data = _file(_picture(centre=45))
    res = wheel.render(data, *_tables(tmp_path, data, lay_over={"wheels": {"A": {"rotation": -45}}}))
    W = _assembled(res)
    assert not any(TEXT in row for row in W)
    ink = [(x, y) for y in range(256) for x in range(256) if W[y][x] == 4]
    assert ink and all(wheel.in_wedge(x, y, 45, 48, 114, 21.5) for x, y in ink)


def test_untranslated_entry_keeps_the_picture(tmp_path):
    data = _file()
    tr, lay = _tables(tmp_path, data, entry_over={"state": "untranslated", "ko": ""}, lay_over={"entries": []})
    res = wheel.render(data, tr, lay)
    ents = select1.entries(data, CRAM)
    assert res.textures == {} and res.states == {"untranslated": 1}
    assert all(e.data for e in ents)


@pytest.mark.parametrize("kw, msg", [
    ({"entry_over": {"state": "untranslated", "ko": ""}}, "untranslated"),
    ({"entry_over": {"ko": "리셋\n둘"}}, "lines"),
    ({"spec_over": {"lines": [{"size": 20, "fill": [1, 2, 3]}]}}, "colour"),
    ({"lay_over": {"wheels": {"B": {"rotation": 0}}}}, "wheels"),
    ({"spec_over": {"id": "w.other"}}, "unknown"),
    ({"entry_over": {"state": "done"}}, "state"),
    ({"entry_over": {"terms": ["t.x"]}}, "term"),
])
def test_bad_tables_are_rejected(tmp_path, kw, msg):
    data = _file()
    with pytest.raises(wheel.WheelError, match=msg):
        wheel.render(data, *_tables(tmp_path, data, **kw), glossary=_glossary(tmp_path))


def _glossary(tmp_path):
    p = tmp_path / "gl.json"
    p.write_text(json.dumps({"terms": [{"id": "t.ok", "state": "approved"}]}))
    return p


def test_translated_entry_without_layout_is_rejected(tmp_path):
    data = _file()
    with pytest.raises(wheel.WheelError, match="no layout"):
        wheel.render(data, *_tables(tmp_path, data, lay_over={"entries": []}))


def test_overlapping_wedges_are_rejected(tmp_path):
    data = _file()
    tr, lay = _tables(tmp_path, data)
    t, ly = json.loads(tr.read_text()), json.loads(lay.read_text())
    t["entries"].append({**t["entries"][0], "id": "w.two"})
    ly["entries"].append({**ly["entries"][0], "id": "w.two", "angle": 450})       # same wedge as 90
    tr.write_text(json.dumps(t, ensure_ascii=False)); lay.write_text(json.dumps(ly))  # noqa: E702
    with pytest.raises(wheel.WheelError, match="overlap"):
        wheel.render(data, tr, lay)


@pytest.mark.parametrize("field, value, msg", [
    ("src_sha1", ["0" * 40] * 4, "source"),
    ("palette_sha1", "0" * 40, "palette"),
    ("colr", "0x500", "colr"),
    ("entries", [0, 1, 2, 9], "range"),
    ("entries", [0, 1, 2], "four"),
])
def test_wheel_source_must_match_the_baseline(tmp_path, field, value, msg):
    data = _file()
    tr, lay = _tables(tmp_path, data)
    t = json.loads(tr.read_text())
    t["wheels"][0][field] = value
    tr.write_text(json.dumps(t, ensure_ascii=False))
    with pytest.raises(wheel.WheelError, match=msg):
        wheel.render(data, tr, lay)


def test_quadrants_must_be_256_colour_128x128(tmp_path):
    quads = wheel.split(_picture())
    ents = [yc.Entry(128, 128, 0x0020, COLR, q) for q in quads[:3]] + [yc.Entry(128, 128, 0x0000, COLR, quads[3][:8192])]
    data = bytes(_pal()) + yc.build(ents)
    with pytest.raises(wheel.WheelError, match="256-colour"):
        wheel.render(data, *_tables(tmp_path, data))


def test_eligible_needs_approved_terms(tmp_path):
    data = _file()
    gl = tmp_path / "gl.json"
    gl.write_text(json.dumps({"terms": [{"id": "t.x", "state": "proposed"}]}))
    tr, lay = _tables(tmp_path, data, entry_over={"state": "distribution_eligible", "terms": ["t.x"]})
    with pytest.raises(wheel.WheelError, match="not approved"):
        wheel.render(data, tr, lay, glossary=gl)


# review: inputs that used to pass silently or crash -----------------------------------------

def _two_wheels(data):
    w = json.loads(json.dumps(_wheel0(data)))
    return [w, {**w, "name": "B"}]


def _wheel0(data):
    ents = select1.entries(data, CRAM)
    return {"name": "A", "entries": [0, 1, 2, 3], "colr": hex(COLR),
            "src_sha1": [hashlib.sha1(e.data).hexdigest() for e in ents],
            "palette_sha1": hashlib.sha1(select1.palette_bytes(data, COLR, 0, 256)).hexdigest()}


@pytest.mark.parametrize("field, value, msg", [
    ("src_sha1", ["0" * 40], "src_sha1"),
    ("entries", [0, 0, 2, 3], "duplicate"),
])
def test_wheel_entry_list_must_be_complete_and_distinct(tmp_path, field, value, msg):
    data = _file()
    tr, lay = _tables(tmp_path, data)
    t = json.loads(tr.read_text())
    t["wheels"][0][field] = value
    tr.write_text(json.dumps(t, ensure_ascii=False))
    with pytest.raises(wheel.WheelError, match=msg):
        wheel.render(data, tr, lay)


def test_two_wheels_cannot_share_entries(tmp_path):
    data = _file()
    tr, lay = _tables(tmp_path, data, tr_over={"wheels": _two_wheels(data)},
                      lay_over={"wheels": {"A": {"rotation": 0}, "B": {"rotation": 0}}})
    with pytest.raises(wheel.WheelError, match="share"):
        wheel.render(data, tr, lay)


@pytest.mark.parametrize("kw, msg", [
    ({"spec_over": {"lines": [{"size": 20, "fill": [255, 247, 0], "shadow": {"idx": 3, "dx": 1, "dy": 1}}]}}, "shadow"),
    ({"lay_over": {"clean": {"r0": 50, "r1": 106, "rim": 117, "half": 20.5}}}, "clean"),
    ({"lay_over": {"clean": {"r0": 50, "r1": 106, "rim": 117, "half": 0, "dmax": 110}}}, "clean"),
    ({"lay_over": {"ink": {"r0": 48, "r1": 140, "half": 21.5}}}, "ink"),
    ({"spec_over": {"r": [110, 62]}}, "r "),
    ({"entry_over": {"ko": ""}}, "empty"),
    ({"entry_over": {"ko": "리셋\n"}, "spec_over": {"lines": [{"size": 16, "fill": [255, 247, 0]}] * 2}}, "empty"),
    ({"tr_over": {"wheels": []}, "lay_over": {"wheels": {}}}, "wheel"),
    ({"spec_over": {"angle": 112}}, "45"),
    ({"tr_over": {"id_prefix": "w"}}, "id_prefix"),
    ({"tr_over": {"id_prefix": "z."}}, "id_prefix"),
])
def test_more_bad_tables_are_rejected(tmp_path, kw, msg):
    data = _file()
    with pytest.raises(wheel.WheelError, match=msg):
        wheel.render(data, *_tables(tmp_path, data, **kw))


def test_clean_replaces_foreign_pixels_in_the_rim_band():
    W = _picture(text=False)
    for y in range(256):
        for x in range(256):
            if wheel.in_wedge(x, y, 90, 110, 118, 22.5):
                W[y][x] = 4                                   # rim band
    W[13][127] = TEXT                                         # r ~114.5, centre of the wedge
    assert wheel.polar(127, 13)[0] > 106
    pal = [RGB.get(k, (0, 0, 0)) for k in range(256)]
    wheel.clean_wedge(W, pal, 90, r0=50, r1=106, rim=117, half=20.5, dmax=110)
    assert W[13][127] == 4


@pytest.mark.parametrize("angle", [45, 135, 225, 315])
def test_text_on_diagonal_wedges_stays_inside(angle):
    W = [bytearray([BG]) * 256 for _ in range(256)]
    before = [bytearray(r) for r in W]
    wheel.draw_wedge(W, _pal555(), angle, [{"size": 20, "fill": 4, "outline": 5}], ["셔플"], (62, 110),
                     ink=(48, 114, 21.5))
    ink = _ink(W, before)
    assert ink and all(wheel.in_wedge(x, y, angle, 48, 114, 21.5) for x, y in ink)
