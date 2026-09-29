"""Product-build contract on a synthetic disc (no game data): ISO + one file with a Yc/LZSS block."""
import hashlib
import json

import pytest
from PIL import Image

from suchie2 import build, cdsector, lzss, yc
from test_iso9660 import _dirrec

FILE_LBA = 21
BLOCK_OFF = 0x100
REGION_END = 0x1900


def _entries():
    return [
        yc.Entry(16, 8, 0x0080, 0x2010, bytes([0x11]) * 64),   # "logo"
        yc.Entry(8, 8, 0x0080, 0x2040, bytes([0x22]) * 32),    # "tm"
    ]


SEL_LBA = 40
SEL_BUNDLE = 0x40


def _select1_file():
    head = bytearray(SEL_BUNDLE)
    head[0x22:0x24] = (0x7FFF).to_bytes(2, "big")      # bank 0x10 idx 1 white
    head[0x24:0x26] = (0x001F).to_bytes(2, "big")      # idx 2 red
    a = yc.Entry(32, 16, 0x0080, 0x10, bytes(256))
    b = yc.Entry(32, 16, 0x0080, 0x10, bytes(256))
    return bytes(head) + yc.build([a, b]) + bytes(100)


def _make_disc(tmp_path):
    prolog = bytearray(0x2000)
    stream = lzss.compress(yc.build(_entries()))
    prolog[BLOCK_OFF:BLOCK_OFF + len(stream)] = stream
    sel = _select1_file()
    user = {}
    root = bytearray(2048)
    p = 0
    for rec in [_dirrec(b"\x00", 20, 2048, 2), _dirrec(b"\x01", 20, 2048, 2),
                _dirrec(b"PROLOG.BIN;1", FILE_LBA, len(prolog)),
                _dirrec(b"SELECT1.BIN;1", SEL_LBA, len(sel))]:
        root[p:p + len(rec)] = rec
        p += len(rec)
    pvd = bytearray(2048)
    pvd[0:6] = b"\x01CD001"
    pvd[156:190] = _dirrec(b"\x00", 20, 2048, 2)
    user[16], user[20] = bytes(pvd), bytes(root)
    for i in range(0, len(prolog), 2048):
        user[FILE_LBA + i // 2048] = bytes(prolog[i:i + 2048])
    for i in range(0, len(sel), 2048):
        user[SEL_LBA + i // 2048] = sel[i:i + 2048].ljust(2048, b"\x00")
    raw = bytearray()
    for lba in range(SEL_LBA + len(sel) // 2048 + 2):
        s = bytearray(2352)
        s[0:12] = cdsector.SYNC
        m, rem = divmod(lba + 150, 4500)
        sec, fr = divmod(rem, 75)
        s[12:15] = bytes(((v // 10) << 4) | (v % 10) for v in (m, sec, fr))
        s[15] = 1
        s[16:2064] = user.get(lba, bytes(2048))
        raw += cdsector.fix_mode1(s)
    t1 = tmp_path / "t1.bin"
    t1.write_bytes(bytes(raw))
    t2 = tmp_path / "t2.bin"
    t2.write_bytes(b"\x00" * 2352 * 3)
    cue = tmp_path / "src.cue"
    cue.write_text('FILE "t1.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n'
                   'FILE "t2.bin" BINARY\n  TRACK 02 AUDIO\n    INDEX 00 00:00:00\n    INDEX 01 00:02:00\n')
    profile = build.SourceProfile(
        track1_sha1=hashlib.sha1(t1.read_bytes()).hexdigest(),
        track2_sha1=hashlib.sha1(t2.read_bytes()).hexdigest(),
        prolog_name="PROLOG.BIN", prolog_lba=FILE_LBA, prolog_size=len(prolog),
        title_region=(BLOCK_OFF, REGION_END),
        title_region_sha1=hashlib.sha1(bytes(prolog[BLOCK_OFF:REGION_END])).hexdigest(),
        select1_name="SELECT1.BIN", select1_lba=SEL_LBA, select1_size=len(sel), select1_bundle=SEL_BUNDLE)
    return cue, profile, prolog


def _spec(tmp_path):
    logo = Image.new("RGBA", (16, 8), (0, 0, 0, 0))
    for x in range(4, 12):
        logo.putpixel((x, 3), (255, 0, 0, 255))
    logo.save(tmp_path / "logo.png")
    spec = {
        "logo": "logo.png",
        "parts": [{"name": "all", "box": [0, 0, 16, 8], "scale": 1.0, "at": [0, 0]}],
        "canvas": [320, 240],
        "sprites": [{"name": "logo", "entry": 0, "rect": [0, 0, 16, 8],
                     "palette": ["0000", "001f"] + ["0000"] * 14}],
        "blank_entries": [1],
    }
    p = tmp_path / "title.json"
    p.write_text(json.dumps(spec))
    return p


def _read_prolog(track1, lba, size):
    data = track1.read_bytes()
    return b"".join(data[(lba + i) * 2352 + 16:(lba + i) * 2352 + 2064] for i in range((size + 2047) // 2048))[:size]


def test_build_replaces_title_entries_and_only_touches_the_region(tmp_path):
    cue, profile, prolog = _make_disc(tmp_path)
    out = tmp_path / "out"
    manifest = build.build(cue, out, _spec(tmp_path), profile=profile)
    t1 = out / manifest["track1"]
    new = _read_prolog(t1, FILE_LBA, len(prolog))
    assert new[:BLOCK_OFF] == prolog[:BLOCK_OFF] and new[REGION_END:] == prolog[REGION_END:]
    n = int.from_bytes(new[BLOCK_OFF:BLOCK_OFF + 4], "big")
    ents = yc.parse(lzss.decompress(new[BLOCK_OFF:BLOCK_OFF + 4 + n]))
    assert ents[0].data[3 * 8 + 2] == 0x11            # row 3, pixels 4-5 -> index 1 (red)
    assert set(ents[0].data[:24]) == {0}              # rows 0-2 transparent
    assert set(ents[1].data) == {0}                   # blanked entry
    assert (ents[0].width, ents[0].colr) == (16, 0x2010)
    for i in range(len(t1.read_bytes()) // 2352):     # every sector still has valid EDC/ECC
        s = bytearray(t1.read_bytes()[i * 2352:(i + 1) * 2352])
        assert cdsector.fix_mode1(bytearray(s)) == s


def test_build_original_mode_keeps_textures(tmp_path):
    cue, profile, prolog = _make_disc(tmp_path)
    manifest = build.build(cue, tmp_path / "out", None, profile=profile)
    new = _read_prolog(tmp_path / "out" / manifest["track1"], FILE_LBA, len(prolog))
    n = int.from_bytes(new[BLOCK_OFF:BLOCK_OFF + 4], "big")
    assert yc.parse(lzss.decompress(new[BLOCK_OFF:BLOCK_OFF + 4 + n])) == _entries()


def test_build_rejects_wrong_source(tmp_path):
    cue, profile, _ = _make_disc(tmp_path)
    bad = build.SourceProfile(**{**profile.__dict__, "track1_sha1": "0" * 40})
    with pytest.raises(build.BuildError, match="Track 1"):
        build.build(cue, tmp_path / "out", None, profile=bad)


def test_build_rejects_block_that_does_not_fit(tmp_path):
    cue, profile, _ = _make_disc(tmp_path)
    tight = build.SourceProfile(**{**profile.__dict__, "title_region": (BLOCK_OFF, BLOCK_OFF + 8)})
    with pytest.raises(build.BuildError):
        build.build(cue, tmp_path / "out", None, profile=tight)


def test_build_rejects_logo_outside_sprites(tmp_path):
    cue, profile, _ = _make_disc(tmp_path)
    spec = _spec(tmp_path)
    d = json.loads(spec.read_text())
    d["parts"][0]["at"] = [100, 100]
    spec.write_text(json.dumps(d))
    with pytest.raises(build.BuildError, match="outside"):
        build.build(cue, tmp_path / "out", spec, profile=profile)
    assert not list((tmp_path / "out").glob("*.bin"))


def test_output_cue_references_outputs(tmp_path):
    cue, profile, _ = _make_disc(tmp_path)
    m = build.build(cue, tmp_path / "out", None, profile=profile)
    text = (tmp_path / "out" / m["cue"]).read_text()
    assert f'"{m["track1"]}"' in text and f'"{m["track2"]}"' in text


def test_build_color_group_parts_go_to_separate_places(tmp_path):
    cue, profile, prolog = _make_disc(tmp_path)
    spec = json.loads(_spec(tmp_path).read_text())
    logo = Image.new("RGBA", (16, 8), (0, 0, 0, 0))
    logo.putpixel((1, 1), (255, 200, 0, 255))       # yellow dot
    logo.putpixel((10, 6), (255, 0, 0, 255))        # red dot, separate component
    logo.save(tmp_path / "logo.png")
    spec["parts"] = [
        {"name": "y", "box": [0, 0, 16, 8], "scale": 1.0, "at": [0, 0],
         "group": {"rgb": [[200, 255], [150, 255], [0, 120]], "keep": True}},
        {"name": "r", "box": [0, 0, 16, 8], "scale": 1.0, "at": [0, 0],
         "group": {"rgb": [[200, 255], [150, 255], [0, 120]], "keep": False}},
    ]
    spec["sprites"][0]["palette"] = ["0000", "001f", "03ff"] + ["0000"] * 13
    (tmp_path / "title.json").write_text(json.dumps(spec))
    m = build.build(cue, tmp_path / "out", tmp_path / "title.json", profile=profile)
    new = _read_prolog(tmp_path / "out" / m["track1"], FILE_LBA, len(prolog))
    n = int.from_bytes(new[BLOCK_OFF:BLOCK_OFF + 4], "big")
    e = yc.parse(lzss.decompress(new[BLOCK_OFF:BLOCK_OFF + 4 + n]))[0]
    px = lambda x, y: (e.data[(y * 16 + x) // 2] >> 4) if x % 2 == 0 else e.data[(y * 16 + x) // 2] & 15  # noqa: E731
    assert px(1, 1) == 2 and px(10, 6) == 1          # yellow -> yellow index, red -> red index


def test_build_rejects_sector_with_wrong_header(tmp_path):
    cue, profile, _ = _make_disc(tmp_path)
    t1 = tmp_path / "t1.bin"
    raw = bytearray(t1.read_bytes())
    lba = FILE_LBA + BLOCK_OFF // 2048
    raw[lba * 2352 + 14] ^= 0x01                      # frame number off by one
    s = bytearray(raw[lba * 2352:(lba + 1) * 2352])
    cdsector.fix_mode1(s)
    raw[lba * 2352:(lba + 1) * 2352] = s
    t1.write_bytes(bytes(raw))
    prof = build.SourceProfile(**{**profile.__dict__, "track1_sha1": hashlib.sha1(bytes(raw)).hexdigest()})
    with pytest.raises(build.BuildError, match="LBA"):
        build.build(cue, tmp_path / "out", None, profile=prof)


def test_failed_build_keeps_previous_output_untouched(tmp_path):
    cue, profile, _ = _make_disc(tmp_path)
    out = tmp_path / "out"
    first = build.build(cue, out, None, profile=profile)
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    tight = build.SourceProfile(**{**profile.__dict__, "title_region": (BLOCK_OFF, BLOCK_OFF + 8)})
    with pytest.raises(build.BuildError):
        build.build(cue, out, None, profile=tight)
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before
    assert first["title"]["mode"] == "original-recompressed"


def test_bad_spec_is_reported_as_build_error(tmp_path):
    cue, profile, _ = _make_disc(tmp_path)
    spec = tmp_path / "broken.json"
    spec.write_text("{not json")
    with pytest.raises(build.BuildError):
        build.build(cue, tmp_path / "out", spec, profile=profile)


def test_manifest_records_spec_and_logo_hashes(tmp_path):
    cue, profile, _ = _make_disc(tmp_path)
    spec = _spec(tmp_path)
    m = build.build(cue, tmp_path / "out", spec, profile=profile)
    assert m["title"]["spec_sha1"] == hashlib.sha1(spec.read_bytes()).hexdigest()
    assert m["title"]["logo_sha1"] == hashlib.sha1((tmp_path / "logo.png").read_bytes()).hexdigest()
    assert not m["title"]["spec"].startswith("/")


def _select1_tables(tmp_path):
    ents = yc.parse(_select1_file()[SEL_BUNDLE:SEL_BUNDLE + yc.length(_select1_file()[SEL_BUNDLE:])])
    base = SEL_BUNDLE + 4 + 8 * len(ents)
    tr = {"file": "SELECT1.BIN", "bundle_offset": hex(SEL_BUNDLE), "excluded": [], "entries": [
        {"id": f"s.e{i}", "entry": i, "offset": hex(base + 256 * i), "size": [32, 16], "colr": "0x10",
         "src_sha1": hashlib.sha1(e.data).hexdigest(), "ja": "あ", "ko": "가", "state": "needs_review",
         "terms": [], "note": ""} for i, e in enumerate(ents)]}
    lay = {"entries": [{"id": f"s.e{i}", "box": [0, 0, 32, 16], "allowed": [1, 2],
                        "lines": [{"size": 12, "x": "center", "y": "center", "fill": 1, "outline": 2}]} for i in (0, 1)]}
    (tmp_path / "s_tr.json").write_text(json.dumps(tr, ensure_ascii=False))
    (tmp_path / "s_lay.json").write_text(json.dumps(lay))
    return tmp_path / "s_tr.json", tmp_path / "s_lay.json"


def test_build_applies_select1_labels_with_one_write_per_sector(tmp_path):
    cue, profile, _ = _make_disc(tmp_path)
    tr, lay = _select1_tables(tmp_path)
    m = build.build(cue, tmp_path / "out", None, profile=profile, select1=(tr, lay))
    sel = _select1_file()
    raw = (tmp_path / "out" / m["track1"]).read_bytes()
    new = b"".join(raw[(SEL_LBA + i) * 2352 + 16:(SEL_LBA + i) * 2352 + 2064] for i in range(2))[:len(sel)]
    ents = yc.parse(new[SEL_BUNDLE:SEL_BUNDLE + yc.length(new[SEL_BUNDLE:])])
    assert any(ents[0].data) and any(ents[1].data)
    assert new[:SEL_BUNDLE] == sel[:SEL_BUNDLE]                       # palettes untouched
    assert m["select1"]["sectors"] == [SEL_LBA]                        # both entries share sector 0
    assert m["select1"]["entries"] == ["s.e0", "s.e1"]
    assert m["distribution"] is False and m["select1"]["states"] == {"needs_review": 2}


def test_select1_readback_rejects_corrupted_output(tmp_path, monkeypatch):
    cue, profile, _ = _make_disc(tmp_path)
    tr, lay = _select1_tables(tmp_path)
    real_apply = build.WritePlan.apply

    def apply_then_corrupt(self, out):
        real_apply(self, out)
        d = bytearray(out.read_bytes())
        d[SEL_LBA * 2352 + 16 + SEL_BUNDLE + 4 + 16 + 40] ^= 0xFF   # inside entry 0 texture
        sec = bytearray(d[SEL_LBA * 2352:(SEL_LBA + 1) * 2352])
        d[SEL_LBA * 2352:(SEL_LBA + 1) * 2352] = cdsector.fix_mode1(sec)  # keep EDC valid: texture check must catch it
        out.write_bytes(bytes(d))
    monkeypatch.setattr(build.WritePlan, "apply", apply_then_corrupt)
    with pytest.raises(build.BuildError, match="differs from the rendered label"):
        build.build(cue, tmp_path / "out", None, profile=profile, select1=(tr, lay))
    assert not (tmp_path / "out").exists()


def test_file_writes_reject_overlapping_changes(tmp_path):
    cue, profile, _ = _make_disc(tmp_path)
    t1 = build._Track1(tmp_path / "t1.bin")
    try:
        plan = build.WritePlan(tmp_path / "t1.bin")
        with pytest.raises(build.BuildError, match="overlap"):
            build._file_writes(plan, t1, SEL_LBA, [(0x100, b"ab"), (0x101, b"cd")], "x")
    finally:
        t1.close()


def test_file_writes_split_changes_across_sectors(tmp_path):
    cue, profile, _ = _make_disc(tmp_path)
    t1 = build._Track1(tmp_path / "t1.bin")
    try:
        plan = build.WritePlan(tmp_path / "t1.bin")
        lbas = build._file_writes(plan, t1, FILE_LBA, [(2040, bytes(range(20)))], "x")
    finally:
        t1.close()
    assert lbas == [FILE_LBA, FILE_LBA + 1]
    plan.apply(tmp_path / "o.bin")
    raw = (tmp_path / "o.bin").read_bytes()
    got = raw[FILE_LBA * 2352 + 16 + 2040:FILE_LBA * 2352 + 16 + 2048] + raw[(FILE_LBA + 1) * 2352 + 16:(FILE_LBA + 1) * 2352 + 16 + 12]
    assert got == bytes(range(20))


def test_distribution_true_when_title_unchanged_and_all_labels_eligible(tmp_path):
    cue, profile, _ = _make_disc(tmp_path)
    tr, lay = _select1_tables(tmp_path)
    t = json.loads(tr.read_text())
    for e in t["entries"]:
        e["state"] = "distribution_eligible"
    tr.write_text(json.dumps(t, ensure_ascii=False))
    gl = tmp_path / "glossary.json"
    gl.write_text(json.dumps({"terms": []}))
    m = build.build(cue, tmp_path / "out", None, profile=profile, select1=(tr, lay, gl))
    assert m["distribution"] is True
    assert len(m["select1"]["font_sha1"]) == 40


def test_title_needs_approved_presentation_for_distribution(tmp_path):
    cue, profile, _ = _make_disc(tmp_path)
    spec = _spec(tmp_path)
    m = build.build(cue, tmp_path / "out", spec, profile=profile)
    assert m["distribution"] is False
    d = json.loads(spec.read_text())
    d["presentation"] = "approved"
    spec.write_text(json.dumps(d))
    assert build.build(cue, tmp_path / "out", spec, profile=profile)["distribution"] is True
