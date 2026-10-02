"""Single pictures inside a chunk file (CLEAR.DAT: save notice, hints; docs/initial-survey.md 3.21)."""
import hashlib
import json

import pytest

from suchie2 import chunked, chunkpics, lzss

W, H = 64, 24


def _pic(fill_rows=range(6, 14), idx=4):
    rows = []
    for y in range(H):
        row = bytearray(W // 2)
        if y in fill_rows:
            for x in range(8, 50):
                row[x // 2] |= (idx << 4) if x % 2 == 0 else idx
        rows.append(bytes(row))
    return chunked.make_picture(W, H, 0x80, b"".join(rows))


def _file(slack=1024):
    raw = [lzss.compress(_pic()), lzss.compress(b"raw data"), lzss.compress(_pic(idx=6))]
    chunks = [chunked.Chunk(s, bytes(-len(s) % 4)) for s in raw]
    for k in (0, 2):
        chunks[k] = chunked.Chunk(chunks[k].stream, chunks[k].tail + bytes(slack))
    chunks[1] = chunked.Chunk(chunks[1].stream, chunks[1].tail + bytes(range(1, 17)))     # data after a stream
    return chunked.build(chunks, size=None)


def _sha(data, k):
    return hashlib.sha1(lzss.decompress(chunked.parse(data)[k].stream)).hexdigest()


def _tables(tmp_path, data, entry_over=None, tr_over=None, lay_over=None):
    tr = {"file": "CLEAR.DAT", "picture_chunks": True, "id_prefix": "clear.",
          "entries": [{"id": "clear.c0", "chunk": 0, "size": [W, H], "src_sha1": _sha(data, 0), "ja": "セーブ中",
                       "ko": "저장 중", "state": "needs_review", "terms": [], "note": ""}],
          "excluded": [{"id": "clear.c2", "chunk": 2, "src_sha1": _sha(data, 2), "reason": "영어"}]}
    tr["entries"][0].update(entry_over or {})
    tr.update(tr_over or {})
    lay = {"entries": [{"id": "clear.c0", "box": [0, 0, W, H], "clean": {"method": "clear"}, "allowed": list(range(1, 16)),
                        "lines": [{"size": 14, "x": "center", "y": "center", "fill": 4, "outline": 2, "aa": False}]}]}
    lay.update(lay_over or {})
    (tmp_path / "tr.json").write_text(json.dumps(tr, ensure_ascii=False))
    (tmp_path / "lay.json").write_text(json.dumps(lay, ensure_ascii=False))
    return tmp_path / "tr.json", tmp_path / "lay.json"


def _render(tmp_path, data, **kw):
    gl = kw.pop("glossary", None)
    return chunkpics.render(data, *_tables(tmp_path, data, **kw), glossary=gl)


def _indices(data, k):
    pix = chunked.picture(lzss.decompress(chunked.parse(data)[k].stream))[3]
    return {b >> 4 for b in pix} | {b & 15 for b in pix}


def test_a_picture_is_redrawn_with_exact_palette_indices(tmp_path):
    data = _file()
    new, res = _render(tmp_path, data)
    assert len(new) == len(data) and new[:16] == data[:16]
    assert _indices(new, 0) == {0, 2, 4}                        # only the fill and outline indices, no blends
    assert res.ids == ["clear.c0"] and res.states == {"needs_review": 1} and res.distribution is False


def test_other_chunks_keep_their_bytes(tmp_path):
    data = _file()
    new, _ = _render(tmp_path, data)
    old, now = chunked.parse(data), chunked.parse(new)
    assert now[1] == old[1] and now[2] == old[2]


def test_untranslated_entries_leave_the_file_unchanged(tmp_path):
    data = _file()
    new, res = _render(tmp_path, data, entry_over={"state": "untranslated", "ko": ""}, lay_over={"entries": []})
    assert new == data and res.states == {"untranslated": 1}


def test_a_picture_that_outgrows_its_slot_fails(tmp_path):
    data = _file(slack=0)
    with pytest.raises(chunkpics.ChunkPicsError, match="slot"):
        _render(tmp_path, data)


@pytest.mark.parametrize("kw, msg", [
    ({"entry_over": {"src_sha1": "0" * 40}}, "source"),
    ({"entry_over": {"chunk": 1}}, "picture"),
    ({"entry_over": {"chunk": 9}}, "range"),
    ({"entry_over": {"size": [W, 8]}}, "size"),
    ({"entry_over": {"state": "untranslated", "ko": ""}}, "untranslated"),
    ({"lay_over": {"entries": []}}, "no layout"),
    ({"entry_over": {"ko": ""}}, "empty"),
    ({"entry_over": {"ko": "저장\n중"}}, "lines"),
    ({"entry_over": {"state": "done"}}, "state"),
    ({"tr_over": {"id_prefix": "clear"}}, "id_prefix"),
    ({"entry_over": {"chunk": 2}}, "excluded"),
    ({"tr_over": {"excluded": [{"id": "clear.c2", "chunk": 2, "src_sha1": "0" * 40, "reason": "영어"}]}}, "source"),
])
def test_bad_tables_are_rejected(tmp_path, kw, msg):
    data = _file()
    with pytest.raises(chunkpics.ChunkPicsError, match=msg):
        _render(tmp_path, data, **kw)


def test_eligible_entries_need_approved_terms(tmp_path):
    data = _file()
    gl = tmp_path / "gl.json"
    gl.write_text(json.dumps({"terms": [{"id": "t.x", "state": "proposed"}]}))
    with pytest.raises(chunkpics.ChunkPicsError, match="not approved"):
        _render(tmp_path, data, entry_over={"state": "distribution_eligible", "terms": ["t.x"]}, glossary=gl)


def test_all_eligible_is_distributable(tmp_path):
    data = _file()
    _, res = _render(tmp_path, data, entry_over={"state": "distribution_eligible"})
    assert res.distribution is True


# review: everything that cannot stay exact without a palette must fail ------------------------

def _line(**over):
    return {"size": 14, "x": "center", "y": "center", "fill": 4, "outline": 2, "aa": False, **over}


@pytest.mark.parametrize("line, msg", [
    (_line(aa=True), "aa"),
    ({k: v for k, v in _line().items() if k != "aa"}, "aa"),
    (_line(shear=0.2), "shear"),
    (_line(vertical=True), "vertical"),
    (_line(fill=5), "allowed"),
    (_line(fill=-1), "allowed"),
    (_line(outline=0), "allowed"),
    (_line(shadow={"idx": 0, "dx": 1, "dy": 1}), "allowed"),
    (_line(fill=16), "allowed"),
    (_line(fill={"v": [4, 9]}), "allowed"),
])
def test_lines_that_cannot_be_drawn_exactly_are_rejected(tmp_path, line, msg):
    data = _file()
    spec = {"id": "clear.c0", "box": [0, 0, W, H], "clean": {"method": "clear"}, "allowed": [2, 3, 4, 6, 15], "lines": [line]}
    with pytest.raises(chunkpics.ChunkPicsError, match=msg):
        _render(tmp_path, data, lay_over={"entries": [spec]})


def test_a_shadowed_line_uses_only_the_named_indices(tmp_path):
    data = _file()
    spec = {"id": "clear.c0", "box": [0, 0, W, H], "clean": {"method": "clear"}, "allowed": list(range(1, 16)),
            "lines": [_line(shadow={"idx": 15, "dx": 1, "dy": 1})]}
    new, _ = _render(tmp_path, data, lay_over={"entries": [spec]})
    assert _indices(new, 0) == {0, 2, 4, 15}


def test_pixels_outside_the_box_are_kept(tmp_path):
    data = _file()
    spec = {"id": "clear.c0", "box": [0, 0, 30, H], "clean": {"method": "clear"}, "allowed": list(range(1, 16)),
            "lines": [_line(size=10, area=[0, 0, 30, H])]}
    new, _ = _render(tmp_path, data, entry_over={"ko": "가"}, lay_over={"entries": [spec]})
    old_pix = chunked.picture(lzss.decompress(chunked.parse(data)[0].stream))[3]
    new_pix = chunked.picture(lzss.decompress(chunked.parse(new)[0].stream))[3]
    for y in range(H):
        assert new_pix[y * W // 2 + 15:(y + 1) * W // 2] == old_pix[y * W // 2 + 15:(y + 1) * W // 2]


@pytest.mark.parametrize("spec_over, msg", [
    ({"regions": [{"box": [0, 0, W, 12], "clean": {"method": "clear"}, "allowed": [2, 4], "lines": [_line()]},
                  {"box": [0, 8, W, H], "clean": {"method": "clear"}, "allowed": [2, 4], "lines": []}]}, "overlap"),
    ({"regions": [{"box": [0, 0, W, H], "clean": {"method": "clear"}, "allowed": [2, 4], "lines": [_line()]}]}, "either"),
    ({"use": "tpl"}, "template"),
])
def test_malformed_specs_are_rejected(tmp_path, spec_over, msg):
    data = _file()
    spec = {"id": "clear.c0", "box": [0, 0, W, H], "clean": {"method": "clear"}, "allowed": [2, 4], "lines": [_line()], **spec_over}
    if "regions" in spec_over and msg == "overlap":
        spec = {"id": "clear.c0", **spec_over}
    with pytest.raises(chunkpics.ChunkPicsError, match=msg):
        _render(tmp_path, data, lay_over={"entries": [spec]})


def test_untranslated_entries_are_still_checked_against_the_source(tmp_path):
    data = _file()
    with pytest.raises(chunkpics.ChunkPicsError, match="source"):
        _render(tmp_path, data, entry_over={"state": "untranslated", "ko": "", "src_sha1": "0" * 40},
                lay_over={"entries": []})
