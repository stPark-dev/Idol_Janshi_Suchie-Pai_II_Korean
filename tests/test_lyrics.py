"""Song-lyric lines under the Yuki ending's credits (ED_YUKI.DAT chunks 14..31): 4bpp pictures whose
index is an antialias level, 1 = full ink .. 15 = faintest, 0 = transparent."""
import hashlib
import json
from pathlib import Path

import pytest

from suchie2 import chunked, lyrics, lzss

W, H = 64, 16
ROOT = Path(__file__).resolve().parents[1]


def _pic(level=1):
    rows = []
    for y in range(H):
        row = bytearray(W // 2)
        if 4 <= y < 12:
            for x in range(10, 30):
                row[x // 2] |= (level << 4) if x % 2 == 0 else level
        rows.append(bytes(row))
    return chunked.make_picture(W, H, 0x80, b"".join(rows))


def _file(slack=512):
    raw = [lzss.compress(_pic()), lzss.compress(b"raw data"), lzss.compress(_pic(5))]
    chunks = [chunked.Chunk(s, bytes(-len(s) % 4)) for s in raw]
    for k in (0, 2):
        chunks[k] = chunked.Chunk(chunks[k].stream, chunks[k].tail + bytes(slack))
    return chunked.build(chunks, size=None)


def _sha(data, k):
    return hashlib.sha1(lzss.decompress(chunked.parse(data)[k].stream)).hexdigest()


def _tables(tmp_path, data, ko="가나", state="needs_review", over=None):
    tr = {"file": "ED_YUKI.DAT", "lyrics": True, "id_prefix": "lyrics.yuki.",
          "entries": [{"id": "lyrics.yuki.c0", "chunk": 0, "size": [W, H], "src_sha1": _sha(data, 0),
                       "ja": "歌", "ko": ko, "state": state, "note": ""}]}
    tr["entries"][0].update(over or {})
    lay = {"font": "/usr/share/fonts/truetype/nanum/NanumSquareRoundB.ttf", "size": 14}
    (tmp_path / "tr.json").write_text(json.dumps(tr, ensure_ascii=False))
    (tmp_path / "lay.json").write_text(json.dumps(lay))
    return tmp_path / "tr.json", tmp_path / "lay.json"


def _indices(data, k):
    pix = chunked.picture(lzss.decompress(chunked.parse(data)[k].stream))[3]
    return [v for b in pix for v in (b >> 4, b & 15)]


def test_coverage_maps_to_the_antialias_levels():
    assert lyrics.level(0.0) == 0
    assert lyrics.level(1.0) == 1
    assert lyrics.level(0.5) == 8
    assert all(1 <= lyrics.level(c / 100) <= 15 for c in range(10, 101))


def test_a_line_is_redrawn_in_its_slot_and_other_chunks_stay(tmp_path):
    data = _file()
    new, res = lyrics.render(data, *_tables(tmp_path, data))
    old, now = chunked.parse(data), chunked.parse(new)
    assert len(new) == len(data) and now[1] == old[1] and now[2] == old[2]
    idx = _indices(new, 0)
    assert set(idx) <= set(range(16)) and 1 in idx and idx != _indices(data, 0)
    assert res.ids == ["lyrics.yuki.c0"] and res.states == {"needs_review": 1} and not res.distribution


def test_an_empty_translation_leaves_the_file_unchanged(tmp_path):
    data = _file()
    new, _ = lyrics.render(data, *_tables(tmp_path, data, ko=""))
    assert new == data


def test_all_eligible_is_distributable(tmp_path):
    data = _file()
    _, res = lyrics.render(data, *_tables(tmp_path, data, state="distribution_eligible"))
    assert res.distribution


@pytest.mark.parametrize("over,msg", [({"src_sha1": "0" * 40}, "source"), ({"chunk": 1}, "picture"),
                                      ({"size": [32, 16]}, "size")])
def test_bad_tables_are_rejected(tmp_path, over, msg):
    data = _file()
    with pytest.raises(lyrics.LyricsError, match=msg):
        lyrics.render(data, *_tables(tmp_path, data, over=over))


def test_a_line_wider_than_the_picture_fails(tmp_path):
    data = _file()
    with pytest.raises(lyrics.LyricsError, match="wide"):
        lyrics.render(data, *_tables(tmp_path, data, ko="가나다라마바사아자차카타파하"))


def test_real_lyrics_change_only_their_chunks():
    src = ROOT / "work/disc1/fs_end/ED_YUKI.DAT"
    if not src.exists():
        pytest.skip("needs the extracted ending files")
    data = src.read_bytes()
    tr = ROOT / "translation/lyrics_yuki.json"
    new, res = lyrics.render(data, tr, ROOT / "assets/ending/lyrics_yuki.json")
    old, now = chunked.parse(data), chunked.parse(new)
    chunks = {e["chunk"] for e in json.loads(tr.read_text())["entries"]}
    assert chunks == set(range(14, 32)) and len(res.ids) == 18
    assert len(new) == len(data) and all(now[k] == old[k] for k in range(len(old)) if k not in chunks)
    assert all(now[k] != old[k] for k in chunks)


def test_a_glyph_missing_from_the_font_fails(tmp_path):
    data = _file()
    with pytest.raises(lyrics.LyricsError, match="glyph"):
        lyrics.render(data, *_tables(tmp_path, data, ko="가"))


def test_a_line_cut_at_the_bottom_fails(tmp_path):
    data = _file()
    tr, lay = _tables(tmp_path, data, ko="가")
    lay.write_text(json.dumps({"font": "/usr/share/fonts/truetype/nanum/NanumSquareRoundB.ttf", "size": 22}))
    with pytest.raises(lyrics.LyricsError, match="outside"):
        lyrics.render(data, tr, lay)


def test_tables_must_name_the_file_and_each_chunk_once(tmp_path):
    data = _file()
    tr, lay = _tables(tmp_path, data)
    t = json.loads(tr.read_text())
    t["entries"].append(dict(t["entries"][0], id="lyrics.yuki.dup"))
    tr.write_text(json.dumps(t, ensure_ascii=False))
    with pytest.raises(lyrics.LyricsError, match="twice"):
        lyrics.render(data, tr, lay)
    with pytest.raises(lyrics.LyricsError, match="file"):
        lyrics.render(data, *_tables(tmp_path, data), file_name="OTHER.DAT")
