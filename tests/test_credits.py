"""Ending credit roll: pictures stacked top to bottom inside one chunk file (docs/initial-survey.md 3.20)."""
import hashlib
import json

import pytest

from suchie2 import chunked, credits, lzss, select1

W, H = 64, 16                       # one roll picture; the roll is three of them
COLR = 0x10
CRAM = bytearray(0x40)
CRAM[0x22:0x24] = (0x7FFF).to_bytes(2, "big")    # idx 1 white
CRAM[0x24:0x26] = (0x001F).to_bytes(2, "big")    # idx 2 red
CRAM[0x26:0x28] = (0x7C00).to_bytes(2, "big")    # idx 3 blue


def _roll():
    """48 rows: a 'name' (idx 3) across rows 12..19, i.e. over the boundary of pictures 0 and 1"""
    rows = []
    for y in range(3 * H):
        row = bytearray(W // 2)
        if 12 <= y < 20:
            for x in range(10, 40):
                row[x // 2] |= (3 << 4) if x % 2 == 0 else 3
        rows.append(bytes(row))
    return b"".join(rows)


def _file(slack=1024):
    pix = _roll()
    pics = [chunked.make_picture(W, H, 0x80, pix[k * W * H // 2:(k + 1) * W * H // 2]) for k in range(3)]
    raw = [lzss.compress(b"not a picture" * 5)] + [lzss.compress(p) for p in pics] + [lzss.compress(b"tail data")]
    chunks = [chunked.Chunk(s, bytes(-len(s) % 4)) for s in raw]
    for k in (1, 2, 3):                                  # room in the picture slots, as in the real file
        chunks[k] = chunked.Chunk(chunks[k].stream, chunks[k].tail + bytes(slack))
    chunks[-1] = chunked.Chunk(chunks[-1].stream, chunks[-1].tail + bytes(range(16)))
    return chunked.build(chunks, size=None) + bytes(4096), pics


def _tables(tmp_path, data, tr_over=None, lay_over=None, entry_over=None, region_over=None):
    streams = chunked.parse(data)
    tr = {"file": "ENDX", "chunks": [1, 3], "picture": [W, H, "0x80"], "palette_file": "PAL.DAT",
          "palette_offset": "0x0", "colr": hex(COLR),
          "palette_sha1": hashlib.sha1(select1.palette_bytes(bytes(CRAM), COLR, 0)).hexdigest(),
          "src_sha1": [hashlib.sha1(lzss.decompress(streams[k].stream)).hexdigest() for k in (1, 2, 3)],
          "id_prefix": "cr.",
          "entries": [{"id": "cr.l1", "ja": "かない みか", "ko": "카나이 미카", "state": "needs_review", "terms": [], "note": ""}],
          "excluded": []}
    tr["entries"][0].update(entry_over or {})
    tr.update(tr_over or {})
    lay = {"regions": [{"ids": ["cr.l1"], "box": [0, 8, W, 24], "clean": {"method": "clear"}, "allowed": [1, 2, 3],
                        "lines": [{"size": 10, "x": "center", "y": "center", "area": [0, 8, W, 24], "fill": 1, "outline": 2, "aa": False}]}]}
    lay["regions"][0].update(region_over or {})
    lay.update(lay_over or {})
    (tmp_path / "tr.json").write_text(json.dumps(tr, ensure_ascii=False))
    (tmp_path / "lay.json").write_text(json.dumps(lay, ensure_ascii=False))
    return tmp_path / "tr.json", tmp_path / "lay.json"


def _render(tmp_path, data, **kw):
    gl = kw.pop("glossary", None)
    tr, lay = _tables(tmp_path, data, **kw)
    return credits.render(data, tr, lay, palette_data=bytes(CRAM), glossary=gl)


def _pixels(data, k):
    return chunked.picture(lzss.decompress(chunked.parse(data)[k].stream))[3]


def test_a_line_over_a_picture_boundary_is_redrawn_in_both_pictures(tmp_path):
    data, pics = _file()
    new, res = _render(tmp_path, data)
    assert len(new) == len(data)
    for k in (1, 2):
        pix = _pixels(new, k)
        assert 3 not in {(b >> 4) for b in pix} | {b & 15 for b in pix}        # old 'name' cleaned
    drawn = _pixels(new, 1) + _pixels(new, 2)
    assert {(b >> 4) for b in drawn} | {b & 15 for b in drawn} >= {1, 2}
    assert res.ids == ["cr.l1"] and res.states == {"needs_review": 1} and res.distribution is False


def test_untouched_chunks_stay_byte_identical(tmp_path):
    data, _ = _file()
    new, _ = _render(tmp_path, data)
    old, now = chunked.parse(data), chunked.parse(new)
    assert now[0] == old[0] and now[3] == old[3] and now[4].stream == old[4].stream
    assert now[4].tail[:20] == old[4].tail[:20] and bytes(range(16)) in now[4].tail   # data after the stream kept
    assert now[1] != old[1]


def test_untranslated_lines_leave_the_file_unchanged(tmp_path):
    data, _ = _file()
    new, res = _render(tmp_path, data, entry_over={"state": "untranslated", "ko": ""}, lay_over={"regions": []})
    assert new == data and res.states == {"untranslated": 1}


def test_all_eligible_is_distributable(tmp_path):
    data, _ = _file()
    _, res = _render(tmp_path, data, entry_over={"state": "distribution_eligible"})
    assert res.distribution is True


@pytest.mark.parametrize("kw, msg", [
    ({"tr_over": {"src_sha1": ["0" * 40] * 3}}, "source"),
    ({"tr_over": {"palette_sha1": "0" * 40}}, "palette"),
    ({"tr_over": {"chunks": [0, 2]}}, "picture"),
    ({"tr_over": {"chunks": [1, 9]}}, "range"),
    ({"tr_over": {"picture": [W, 8, "0x80"]}}, "picture"),
    ({"region_over": {"ids": ["cr.nope"]}}, "unknown"),
    ({"lay_over": {"regions": []}}, "no region"),
    ({"entry_over": {"state": "untranslated", "ko": ""}}, "untranslated"),
    ({"entry_over": {"ko": "카나이\n미카"}}, "lines"),
    ({"entry_over": {"ko": ""}}, "empty"),
    ({"region_over": {"box": [0, 40, W, 60]}}, "box"),
    ({"entry_over": {"state": "done"}}, "state"),
    ({"tr_over": {"id_prefix": "cr"}}, "id_prefix"),
])
def test_bad_tables_are_rejected(tmp_path, kw, msg):
    data, _ = _file()
    with pytest.raises(credits.CreditsError, match=msg):
        _render(tmp_path, data, **kw)


def test_overlapping_regions_are_rejected(tmp_path):
    data, _ = _file()
    tr, lay = _tables(tmp_path, data)
    t, ly = json.loads(tr.read_text()), json.loads(lay.read_text())
    t["entries"].append({**t["entries"][0], "id": "cr.l2"})
    ly["regions"].append({**ly["regions"][0], "ids": ["cr.l2"], "box": [0, 20, W, 30]})
    tr.write_text(json.dumps(t, ensure_ascii=False)); lay.write_text(json.dumps(ly))  # noqa: E702
    with pytest.raises(credits.CreditsError, match="overlap"):
        credits.render(data, tr, lay, palette_data=bytes(CRAM))


def test_an_id_in_two_regions_is_rejected(tmp_path):
    data, _ = _file()
    tr, lay = _tables(tmp_path, data)
    ly = json.loads(lay.read_text())
    ly["regions"].append({**ly["regions"][0], "box": [0, 30, W, 40]})
    lay.write_text(json.dumps(ly))
    with pytest.raises(credits.CreditsError, match="more than one region"):
        credits.render(data, tr, lay, palette_data=bytes(CRAM))


def test_eligible_lines_need_approved_terms(tmp_path):
    data, _ = _file()
    gl = tmp_path / "gl.json"
    gl.write_text(json.dumps({"terms": [{"id": "t.x", "state": "proposed"}]}))
    with pytest.raises(credits.CreditsError, match="not approved"):
        _render(tmp_path, data, entry_over={"state": "distribution_eligible", "terms": ["t.x"]}, glossary=gl)


def test_a_picture_that_outgrows_its_slot_fails(tmp_path):
    data, _ = _file(slack=0)
    with pytest.raises(credits.CreditsError, match="slot"):
        _render(tmp_path, data)


def test_a_region_without_lines_to_draw_is_rejected(tmp_path):
    data, _ = _file()
    tr, lay = _tables(tmp_path, data)
    ly = json.loads(lay.read_text())
    ly["regions"].append({"ids": [], "box": [0, 30, W, 40], "clean": {"method": "clear"}, "allowed": [1], "lines": []})
    lay.write_text(json.dumps(ly))
    with pytest.raises(credits.CreditsError, match="no line"):
        credits.render(data, tr, lay, palette_data=bytes(CRAM))


@pytest.mark.parametrize("excluded, msg", [
    ([{"id": "cr.k", "ja": "SUGI"}, {"id": "cr.k", "ja": "SUGI"}], "duplicate"),
    ([{"id": "cr.l1", "ja": "かない みか"}], "both"),
])
def test_bad_exclusions_are_rejected(tmp_path, excluded, msg):
    data, _ = _file()
    with pytest.raises(credits.CreditsError, match=msg):
        _render(tmp_path, data, tr_over={"excluded": excluded})


def test_only_4bpp_rolls_are_supported(tmp_path):
    data, _ = _file()
    with pytest.raises(credits.CreditsError, match="4bpp"):
        _render(tmp_path, data, tr_over={"picture": [W, H, "0xa0"]})


def test_new_streams_are_checked_against_a_stale_window_too(tmp_path, monkeypatch):
    data, _ = _file()
    calls = []
    real = credits.lzss.decompress
    monkeypatch.setattr(credits.lzss, "decompress", lambda s, stale=None: calls.append(stale) or real(s, stale))
    _render(tmp_path, data)
    assert b"\xa5" * credits.lzss.STALE_LEN in calls


ROLLS = __import__("pathlib").Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("roll", ["ENDY", "ED_YUKI.DAT"])          # the shared layout and the one that differs most
def test_real_rolls_change_only_their_picture_slots(roll):
    src = ROLLS / "work/disc1/fs_end" / roll
    if not src.exists():
        pytest.skip("needs the extracted ending files")
    stem = roll.split(".")[0]
    data = src.read_bytes()
    new, res = credits.render(data, ROLLS / f"translation/credits/{stem}.json", ROLLS / f"assets/ending/{stem}.json",
                              palette_data=(ROLLS / "work/disc1/fs_end/ED_ALIS.DAT").read_bytes(),
                              glossary=ROLLS / "translation/glossary.json")
    tr = json.loads((ROLLS / f"translation/credits/{stem}.json").read_text())
    old, now = chunked.parse(data), chunked.parse(new)
    first, last = tr["chunks"]
    assert len(new) == len(data) and new[:4 + 4 * len(old)] == data[:4 + 4 * len(old)]
    assert all(now[k] == old[k] for k in range(len(old)) if not first <= k <= last)
