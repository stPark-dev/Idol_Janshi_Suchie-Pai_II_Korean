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


STG = [("STG1.BIN", 60), ("STG2.BIN", 70)]


def _stage_disc(tmp_path):
    """disc with two identical stage files carrying a CRAM head + Yc bundle"""
    sel = _select1_file()
    user = {}
    root = bytearray(2048)
    p = 0
    for rec in [_dirrec(b"\x00", 20, 2048, 2), _dirrec(b"\x01", 20, 2048, 2)] + [
            _dirrec(n.encode() + b";1", lba, len(sel)) for n, lba in STG]:
        root[p:p + len(rec)] = rec
        p += len(rec)
    pvd = bytearray(2048)
    pvd[0:6] = b"\x01CD001"
    pvd[156:190] = _dirrec(b"\x00", 20, 2048, 2)
    user[16], user[20] = bytes(pvd), bytes(root)
    for _, lba in STG:
        for i in range(0, len(sel), 2048):
            user[lba + i // 2048] = sel[i:i + 2048].ljust(2048, b"\x00")
    raw = bytearray()
    for lba in range(80):
        s = bytearray(2352)
        s[0:12] = cdsector.SYNC
        m, rem = divmod(lba + 150, 4500)
        sec, fr = divmod(rem, 75)
        s[12:15] = bytes(((v // 10) << 4) | (v % 10) for v in (m, sec, fr))
        s[15] = 1
        s[16:2064] = user.get(lba, bytes(2048))
        raw += cdsector.fix_mode1(s)
    (tmp_path / "t1.bin").write_bytes(bytes(raw))
    (tmp_path / "t2.bin").write_bytes(b"\x00" * 2352 * 3)
    cue = tmp_path / "src.cue"
    cue.write_text('FILE "t1.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n'
                   'FILE "t2.bin" BINARY\n  TRACK 02 AUDIO\n    INDEX 00 00:00:00\n    INDEX 01 00:02:00\n')
    profile = build.SourceProfile(
        track1_sha1=hashlib.sha1(bytes(raw)).hexdigest(), track2_sha1=hashlib.sha1(b"\x00" * 2352 * 3).hexdigest(),
        prolog_name="PROLOG.BIN", prolog_lba=0, prolog_size=0, title_region=(0, 1), title_region_sha1="",   # title unused
        files={n: (lba, len(sel)) for n, lba in STG})
    return cue, profile, sel


def test_bundle_job_writes_every_listed_file(tmp_path):
    cue, profile, sel = _stage_disc(tmp_path)
    tr, lay = _select1_tables(tmp_path)
    job = {"files": [n for n, _ in STG], "translation": tr, "layout": lay}
    m = build.build(cue, tmp_path / "out", None, profile=profile, bundles=[job], title=False)
    raw = (tmp_path / "out" / m["track1"]).read_bytes()
    outs = []
    for n, lba in STG:
        new = b"".join(raw[(lba + i) * 2352 + 16:(lba + i) * 2352 + 2064] for i in range(2))[:len(sel)]
        outs.append(new)
        ents = yc.parse(new[SEL_BUNDLE:SEL_BUNDLE + yc.length(new[SEL_BUNDLE:])])
        assert any(ents[0].data) and any(ents[1].data)
    assert outs[0] == outs[1]
    assert [b["file"] for b in m["bundles"]] == ["STG1.BIN", "STG2.BIN"]
    assert m["distribution"] is False


def test_bundle_job_rejects_file_not_in_profile(tmp_path):
    cue, profile, _ = _stage_disc(tmp_path)
    tr, lay = _select1_tables(tmp_path)
    with pytest.raises(build.BuildError, match="profile"):
        build.build(cue, tmp_path / "out", None, profile=profile,
                    bundles=[{"files": ["NOPE.BIN"], "translation": tr, "layout": lay}], title=False)


def test_two_jobs_on_one_file_share_sectors_without_false_overlap(tmp_path):
    cue, profile, sel = _stage_disc(tmp_path)
    tr, lay = _select1_tables(tmp_path)
    t = json.loads(tr.read_text())
    lj = json.loads(lay.read_text())
    tr_a, tr_b = tmp_path / "a.json", tmp_path / "b.json"
    la, lb = tmp_path / "la.json", tmp_path / "lb.json"
    ea, eb = dict(t), dict(t)
    ea["entries"], ea["excluded"] = [t["entries"][0]], [{"id": "z1", "entry": 1, "reason": "other job"}]
    eb["entries"], eb["excluded"] = [t["entries"][1]], [{"id": "z0", "entry": 0, "reason": "other job"}]
    tr_a.write_text(json.dumps(ea, ensure_ascii=False)); tr_b.write_text(json.dumps(eb, ensure_ascii=False))  # noqa: E702
    la.write_text(json.dumps({"entries": [lj["entries"][0]]})); lb.write_text(json.dumps({"entries": [lj["entries"][1]]}))  # noqa: E702
    jobs = [{"files": ["STG1.BIN"], "translation": tr_a, "layout": la},
            {"files": ["STG1.BIN"], "translation": tr_b, "layout": lb}]
    m = build.build(cue, tmp_path / "out", None, profile=profile, bundles=jobs, title=False)
    raw = (tmp_path / "out" / m["track1"]).read_bytes()
    new = b"".join(raw[(60 + i) * 2352 + 16:(60 + i) * 2352 + 2064] for i in range(2))[:len(sel)]
    ents = yc.parse(new[SEL_BUNDLE:SEL_BUNDLE + yc.length(new[SEL_BUNDLE:])])
    assert any(ents[0].data) and any(ents[1].data)


def test_duplicate_file_in_one_job_is_rejected(tmp_path):
    cue, profile, _ = _stage_disc(tmp_path)
    tr, lay = _select1_tables(tmp_path)
    with pytest.raises(build.BuildError, match="more than once"):
        build.build(cue, tmp_path / "out", None, profile=profile, title=False,
                    bundles=[{"files": ["STG1.BIN", "STG1.BIN"], "translation": tr, "layout": lay}])


def test_title_spec_with_title_disabled_is_rejected(tmp_path):
    cue, profile, _ = _stage_disc(tmp_path)
    with pytest.raises(build.BuildError, match="title"):
        build.build(cue, tmp_path / "out", _spec(tmp_path), profile=profile, title=False)


def test_bundle_output_equals_render_and_leaves_other_sectors(tmp_path):
    cue, profile, sel = _stage_disc(tmp_path)
    tr, lay = _select1_tables(tmp_path)
    m = build.build(cue, tmp_path / "out", None, profile=profile, title=False,
                    bundles=[{"files": ["STG1.BIN"], "translation": tr, "layout": lay}])
    from suchie2 import select1
    want = select1.render(sel, tr, lay, file_name="STG1.BIN").textures
    raw = (tmp_path / "out" / m["track1"]).read_bytes()
    src = (tmp_path / "t1.bin").read_bytes()
    new = b"".join(raw[(60 + i) * 2352 + 16:(60 + i) * 2352 + 2064] for i in range(2))[:len(sel)]
    ents = yc.parse(new[SEL_BUNDLE:SEL_BUNDLE + yc.length(new[SEL_BUNDLE:])])
    assert {i: e.data for i, e in enumerate(ents)} == want
    for lba in range(80):
        if lba not in m["bundles"][0]["sectors"]:
            assert raw[lba * 2352:(lba + 1) * 2352] == src[lba * 2352:(lba + 1) * 2352]


# --- compressed ("packed") Yc bundles: PROLOG.BIN block whose CRAM image lives in another file ---
PK_LO, PK_HI = 0x500, 0x1900
TITLE_HI = 0x500                     # title block 0x100-0x500 shares sector 0 with the packed block
PAL_LBA = 50


def _packed_bundle():
    return yc.build([yc.Entry(32, 16, 0x0080, 0x10, bytes(256)), yc.Entry(32, 16, 0x0080, 0x10, bytes([0x33]) * 256)])


def _pal_file():
    pal = bytearray(0x40)
    pal[0x22:0x24] = (0x7FFF).to_bytes(2, "big")
    pal[0x24:0x26] = (0x001F).to_bytes(2, "big")
    pal[0x26:0x28] = (0x7C00).to_bytes(2, "big")
    return bytes(pal)


def _packed_disc(tmp_path, tail=b""):
    prolog = bytearray(0x2000)
    ts = lzss.compress(yc.build(_entries()))
    prolog[BLOCK_OFF:BLOCK_OFF + len(ts)] = ts
    ps = lzss.compress(_packed_bundle())
    prolog[PK_LO:PK_LO + len(ps)] = ps
    prolog[PK_LO + len(ps):PK_LO + len(ps) + len(tail)] = tail
    pal = _pal_file()
    user = {}
    root = bytearray(2048)
    p = 0
    for rec in [_dirrec(b"\x00", 20, 2048, 2), _dirrec(b"\x01", 20, 2048, 2),
                _dirrec(b"PAL.BIN;1", PAL_LBA, len(pal)), _dirrec(b"PROLOG.BIN;1", FILE_LBA, len(prolog))]:
        root[p:p + len(rec)] = rec
        p += len(rec)
    pvd = bytearray(2048)
    pvd[0:6] = b"\x01CD001"
    pvd[156:190] = _dirrec(b"\x00", 20, 2048, 2)
    user[16], user[20] = bytes(pvd), bytes(root)
    for i in range(0, len(prolog), 2048):
        user[FILE_LBA + i // 2048] = bytes(prolog[i:i + 2048])
    user[PAL_LBA] = pal.ljust(2048, b"\x00")
    raw = bytearray()
    for lba in range(PAL_LBA + 2):
        s = bytearray(2352)
        s[0:12] = cdsector.SYNC
        m, rem = divmod(lba + 150, 4500)
        sec, fr = divmod(rem, 75)
        s[12:15] = bytes(((v // 10) << 4) | (v % 10) for v in (m, sec, fr))
        s[15] = 1
        s[16:2064] = user.get(lba, bytes(2048))
        raw += cdsector.fix_mode1(s)
    (tmp_path / "t1.bin").write_bytes(bytes(raw))
    (tmp_path / "t2.bin").write_bytes(b"\x00" * 2352 * 3)
    cue = tmp_path / "src.cue"
    cue.write_text('FILE "t1.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n'
                   'FILE "t2.bin" BINARY\n  TRACK 02 AUDIO\n    INDEX 00 00:00:00\n    INDEX 01 00:02:00\n')
    profile = build.SourceProfile(
        track1_sha1=hashlib.sha1(bytes(raw)).hexdigest(), track2_sha1=hashlib.sha1(b"\x00" * 2352 * 3).hexdigest(),
        prolog_name="PROLOG.BIN", prolog_lba=FILE_LBA, prolog_size=len(prolog),
        title_region=(BLOCK_OFF, TITLE_HI),
        title_region_sha1=hashlib.sha1(bytes(prolog[BLOCK_OFF:TITLE_HI])).hexdigest(),
        packed={"opening": ("PROLOG.BIN", PK_LO, PK_HI, hashlib.sha1(bytes(prolog[PK_LO:PK_HI])).hexdigest())},
        read_only={"PAL.BIN": (PAL_LBA, len(pal))})
    return cue, profile, prolog


def _packed_tables(tmp_path, **over):
    ents = yc.parse(_packed_bundle())
    pos = [4 + 8 * len(ents)]
    for e in ents[:-1]:
        pos.append(pos[-1] + len(e.data))
    tr = {"packed": "opening", "bundle_offset": "0x0", "palette_file": "PAL.BIN", "palette_offset": "0x0",
          "entries": [{"id": f"op.e{i}", "entry": i, "offset": hex(p), "size": [e.width, e.height], "colr": hex(e.colr),
                       "src_sha1": hashlib.sha1(e.data).hexdigest(), "ja": "あ", "ko": "가", "state": "needs_review",
                       "terms": [], "note": "", "palette_sha1": hashlib.sha1(_pal_file()[0x20:0x40]).hexdigest()}
                      for i, (e, p) in enumerate(zip(ents, pos))], "excluded": []}
    tr.update(over)
    lay = {"entries": [
        {"id": "op.e0", "box": [0, 0, 32, 16], "allowed": [1, 2],
         "lines": [{"size": 12, "x": "center", "y": 1, "fill": 1, "outline": 2}]},
        {"id": "op.e1", "box": [0, 0, 32, 16], "clean": {"method": "rows", "text_idx": [1, 2]}, "allowed": [1, 2, 3],
         "lines": [{"size": 12, "x": "center", "y": 1, "fill": 1}]}]}
    (tmp_path / "ptr.json").write_text(json.dumps(tr, ensure_ascii=False))
    (tmp_path / "play.json").write_text(json.dumps(lay, ensure_ascii=False))
    return {"packed": "opening", "translation": tmp_path / "ptr.json", "layout": tmp_path / "play.json"}


def _decode_at(data, off):
    n = int.from_bytes(data[off:off + 4], "big")
    return lzss.decompress(data[off:off + 4 + n]), 4 + n


def test_packed_job_rewrites_compressed_bundle_in_place(tmp_path):
    cue, profile, prolog = _packed_disc(tmp_path)
    job = _packed_tables(tmp_path)
    m = build.build(cue, tmp_path / "out", None, profile=profile, bundles=[job], title=False)
    new = _read_prolog(tmp_path / "out" / m["track1"], FILE_LBA, len(prolog))
    from suchie2 import select1
    want = select1.render(_packed_bundle(), job["translation"], job["layout"], palette_data=_pal_file()).textures
    bundle, n = _decode_at(new, PK_LO)
    assert {i: e.data for i, e in enumerate(yc.parse(bundle))} == want
    assert not any(new[PK_LO + n:PK_HI])                         # rest of the region is zero padding
    assert new[:PK_LO] == prolog[:PK_LO] and new[PK_HI:] == prolog[PK_HI:]
    info = m["bundles"][0]
    assert info["packed"] == "opening" and info["file"] == "PROLOG.BIN" and info["stream_bytes"] == n
    assert info["palette_file"] == "PAL.BIN" and info["sectors"] == [FILE_LBA + s for s in range(PK_LO // 2048, (PK_HI - 1) // 2048 + 1)]


def test_packed_and_title_blocks_share_a_sector(tmp_path):
    cue, profile, prolog = _packed_disc(tmp_path)
    m = build.build(cue, tmp_path / "out", _spec(tmp_path), profile=profile, bundles=[_packed_tables(tmp_path)])
    new = _read_prolog(tmp_path / "out" / m["track1"], FILE_LBA, len(prolog))
    title_ents = yc.parse(_decode_at(new, BLOCK_OFF)[0])
    assert set(title_ents[1].data) == {0} and title_ents[0].data[3 * 8 + 2] == 0x11
    assert any(yc.parse(_decode_at(new, PK_LO)[0])[0].data)
    assert m["title"]["sectors"] == [FILE_LBA]
    assert FILE_LBA in next(b for b in m["bundles"] if b.get("packed"))["sectors"]
    pal_out = (tmp_path / "out" / m["track1"]).read_bytes()[PAL_LBA * 2352:(PAL_LBA + 1) * 2352]
    assert pal_out == (tmp_path / "t1.bin").read_bytes()[PAL_LBA * 2352:(PAL_LBA + 1) * 2352]   # read-only file


def test_packed_region_must_match_the_profile_hash(tmp_path):
    cue, profile, _ = _packed_disc(tmp_path)
    bad = build.SourceProfile(**{**profile.__dict__, "packed": {"opening": ("PROLOG.BIN", PK_LO, PK_HI, "0" * 40)}})
    with pytest.raises(build.BuildError, match="opening"):
        build.build(cue, tmp_path / "out", None, profile=bad, bundles=[_packed_tables(tmp_path)], title=False)


def test_packed_block_that_does_not_fit_is_rejected(tmp_path, monkeypatch):
    cue, profile, _ = _packed_disc(tmp_path)
    job = _packed_tables(tmp_path)
    real = lzss.compress
    monkeypatch.setattr(build.lzss, "compress", lambda b: real(b) + bytes(PK_HI - PK_LO))
    with pytest.raises(build.BuildError, match="exceeds"):
        build.build(cue, tmp_path / "out", None, profile=profile, bundles=[job], title=False)


@pytest.mark.parametrize("over,msg", [({"palette_file": "OTHER.BIN"}, "palette_file"),
                                      ({"packed": "nope"}, "packed"),
                                      ({"bundle_offset": "0x10"}, "bundle_offset"),
                                      ({"palette_offset": None}, "palette_offset")])
def test_packed_table_must_name_the_profile_block(tmp_path, over, msg):
    cue, profile, _ = _packed_disc(tmp_path)
    job = _packed_tables(tmp_path, **over)
    t = json.loads(job["translation"].read_text())
    t = {k: v for k, v in t.items() if v is not None}
    job["translation"].write_text(json.dumps(t, ensure_ascii=False))
    with pytest.raises(build.BuildError, match=msg):
        build.build(cue, tmp_path / "out", None, profile=profile, bundles=[job], title=False)


def test_packed_region_overlapping_the_title_is_rejected(tmp_path):
    cue, profile, prolog = _packed_disc(tmp_path)
    bad = build.SourceProfile(**{**profile.__dict__, "packed": {
        "opening": ("PROLOG.BIN", TITLE_HI - 0x10, PK_HI, hashlib.sha1(bytes(prolog[TITLE_HI - 0x10:PK_HI])).hexdigest())}})
    with pytest.raises(build.BuildError, match="overlap"):
        build.build(cue, tmp_path / "out", _spec(tmp_path), profile=bad, bundles=[_packed_tables(tmp_path)])


def test_packed_readback_rejects_corrupted_output(tmp_path, monkeypatch):
    cue, profile, _ = _packed_disc(tmp_path)
    job = _packed_tables(tmp_path)
    real = build.WritePlan.apply

    def corrupt(self, path):
        real(self, path)
        data = bytearray(path.read_bytes())
        data[(FILE_LBA + 1) * 2352 + 16 + 5] ^= 0xFF       # inside the packed stream, EDC left stale
        path.write_bytes(bytes(data))
    monkeypatch.setattr(build.WritePlan, "apply", corrupt)
    with pytest.raises(build.BuildError, match="LBA 22"):
        build.build(cue, tmp_path / "out", None, profile=profile, bundles=[job], title=False)


def test_packed_entries_need_a_palette_hash(tmp_path):
    cue, profile, _ = _packed_disc(tmp_path)
    job = _packed_tables(tmp_path)
    t = json.loads(job["translation"].read_text())
    del t["entries"][1]["palette_sha1"]
    job["translation"].write_text(json.dumps(t, ensure_ascii=False))
    with pytest.raises(build.BuildError, match="palette_sha1"):
        build.build(cue, tmp_path / "out", None, profile=profile, bundles=[job], title=False)


def test_packed_region_tail_must_be_zero_padding(tmp_path):
    cue, profile, _ = _packed_disc(tmp_path, tail=b"\x01")
    with pytest.raises(build.BuildError, match="padding"):
        build.build(cue, tmp_path / "out", None, profile=profile, bundles=[_packed_tables(tmp_path)], title=False)


def test_unknown_packed_job_fails_before_rendering(tmp_path, monkeypatch):
    cue, profile, _ = _packed_disc(tmp_path)
    job = {**_packed_tables(tmp_path), "packed": "nope"}
    monkeypatch.setattr(build, "_plan_bundle", lambda *a: pytest.fail("rendered before validating jobs"))
    with pytest.raises(build.BuildError, match="not in the source profile"):
        build.build(cue, tmp_path / "out", None, profile=profile, bundles=[job], title=False)


def test_job_is_either_packed_or_files(tmp_path):
    cue, profile, _ = _packed_disc(tmp_path)
    job = {**_packed_tables(tmp_path), "files": ["PROLOG.BIN"]}
    with pytest.raises(build.BuildError, match="either"):
        build.build(cue, tmp_path / "out", None, profile=profile, bundles=[job], title=False)


def test_file_job_cannot_target_a_file_with_compressed_blocks(tmp_path):
    cue, profile, _ = _packed_disc(tmp_path)
    tr, lay = _select1_tables(tmp_path)
    with pytest.raises(build.BuildError, match="compressed"):
        build.build(cue, tmp_path / "out", None, profile=profile, title=False,
                    bundles=[{"files": ["PROLOG.BIN"], "translation": tr, "layout": lay}])


def test_packed_table_cannot_be_used_by_a_file_job(tmp_path):
    cue, profile, _ = _stage_disc(tmp_path)
    job = _packed_tables(tmp_path)
    with pytest.raises(build.BuildError, match="packed table"):
        build.build(cue, tmp_path / "out", None, profile=profile, title=False,
                    bundles=[{"files": ["STG1.BIN"], "translation": job["translation"], "layout": job["layout"]}])


# --- labels drawn into the title block (e.g. 最初から/続きから), palette from a read-only file ---
def _title_label_tables(tmp_path, entry=0, **over):
    ents = _entries()
    pos = [4 + 8 * len(ents)]
    for e in ents[:-1]:
        pos.append(pos[-1] + len(e.data))
    e = ents[entry]
    tr = {"packed": "title", "bundle_offset": "0x0", "palette_file": "PAL.BIN", "palette_offset": "0x0",
          "entries": [{"id": f"tl.e{entry}", "entry": entry, "offset": hex(pos[entry]), "size": [e.width, e.height],
                       "colr": hex(e.colr), "src_sha1": hashlib.sha1(e.data).hexdigest(), "ja": "あ", "ko": "가",
                       "state": "needs_review", "terms": [], "note": "",
                       "palette_sha1": hashlib.sha1(_pal_file()[0x20:0x40]).hexdigest()}],
          "excluded": [{"id": f"tl.x{i}", "entry": i, "reason": "other"} for i in range(len(ents)) if i != entry]}
    tr.update(over)
    lay = {"entries": [{"id": f"tl.e{entry}", "box": [0, 0, e.width, e.height], "clean": {"method": "clear"},
                        "allowed": [1, 2], "lines": [{"size": 8, "x": "center", "y": "center", "fill": 1, "aa": False}]}]}
    (tmp_path / "ttr.json").write_text(json.dumps(tr, ensure_ascii=False))
    (tmp_path / "tlay.json").write_text(json.dumps(lay, ensure_ascii=False))
    return (tmp_path / "ttr.json", tmp_path / "tlay.json", None)


def test_title_labels_are_drawn_into_the_title_block(tmp_path):
    cue, profile, prolog = _packed_disc(tmp_path)
    labels = _title_label_tables(tmp_path)
    m = build.build(cue, tmp_path / "out", None, profile=profile, title_labels=labels)
    new = _read_prolog(tmp_path / "out" / m["track1"], FILE_LBA, len(prolog))
    from suchie2 import select1
    want = select1.render(yc.build(_entries()), labels[0], labels[1], palette_data=_pal_file()).textures
    ents = yc.parse(_decode_at(new, BLOCK_OFF)[0])
    assert ents[0].data == want[0] and any(ents[0].data) and ents[0].data != _entries()[0].data
    assert ents[1] == _entries()[1]
    assert m["title"]["labels"]["entries"] == ["tl.e0"] and m["title"]["labels"]["states"] == {"needs_review": 1}
    assert m["title"]["mode"] == "labels-only"
    assert m["distribution"] is False


def test_title_labels_cannot_redraw_an_entry_the_logo_spec_owns(tmp_path):
    cue, profile, _ = _packed_disc(tmp_path)
    with pytest.raises(build.BuildError, match="owned by the logo spec"):
        build.build(cue, tmp_path / "out", _spec(tmp_path), profile=profile, title_labels=_title_label_tables(tmp_path))


def test_title_labels_need_the_title_block(tmp_path):
    cue, profile, _ = _packed_disc(tmp_path)
    with pytest.raises(build.BuildError, match="title bundle is disabled"):
        build.build(cue, tmp_path / "out", None, profile=profile, title=False, title_labels=_title_label_tables(tmp_path))


@pytest.mark.parametrize("over,msg", [({"packed": "opening"}, "packed"), ({"palette_file": "NOPE.BIN"}, "palette_file")])
def test_title_label_table_must_be_a_title_table(tmp_path, over, msg):
    cue, profile, _ = _packed_disc(tmp_path)
    with pytest.raises(build.BuildError, match=msg):
        build.build(cue, tmp_path / "out", None, profile=profile, title_labels=_title_label_tables(tmp_path, **over))


def _spec_on_entry1(tmp_path, presentation="approved"):
    logo = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    for x in range(2, 6):
        logo.putpixel((x, 3), (255, 0, 0, 255))
    logo.save(tmp_path / "logo1.png")
    spec = {"logo": "logo1.png", "parts": [{"name": "all", "box": [0, 0, 8, 8], "scale": 1.0, "at": [0, 0]}],
            "canvas": [320, 240], "sprites": [{"name": "tm", "entry": 1, "rect": [0, 0, 8, 8],
                                               "palette": ["0000", "001f"] + ["0000"] * 14}],
            "blank_entries": [], "presentation": presentation}
    (tmp_path / "title1.json").write_text(json.dumps(spec))
    return tmp_path / "title1.json"


@pytest.mark.parametrize("state,dist", [("distribution_eligible", True), ("needs_review", False)])
def test_logo_spec_and_title_labels_share_the_block(tmp_path, state, dist):
    cue, profile, prolog = _packed_disc(tmp_path)
    labels = _title_label_tables(tmp_path)
    t = json.loads(labels[0].read_text())
    t["entries"][0]["state"] = state
    labels[0].write_text(json.dumps(t, ensure_ascii=False))
    spec = _spec_on_entry1(tmp_path)
    m = build.build(cue, tmp_path / "out", spec, profile=profile, title_labels=labels)
    new = _read_prolog(tmp_path / "out" / m["track1"], FILE_LBA, len(prolog))
    ents = yc.parse(_decode_at(new, BLOCK_OFF)[0])
    from suchie2 import select1
    assert ents[0].data == select1.render(yc.build(_entries()), labels[0], labels[1], palette_data=_pal_file()).textures[0]
    assert ents[1].data == build.compose_title(_entries(), spec)[1].data
    assert m["title"]["mode"] == "ko" and m["distribution"] is dist


def test_title_label_source_mismatch_fails_and_leaves_no_output(tmp_path):
    cue, profile, _ = _packed_disc(tmp_path)
    labels = _title_label_tables(tmp_path)
    t = json.loads(labels[0].read_text())
    t["entries"][0]["src_sha1"] = "0" * 40
    labels[0].write_text(json.dumps(t, ensure_ascii=False))
    with pytest.raises(build.BuildError, match="translation baseline"):
        build.build(cue, tmp_path / "out", None, profile=profile, title_labels=labels)
    assert not (tmp_path / "out").exists()


def test_empty_title_labels_are_not_silently_ignored(tmp_path):
    cue, profile, _ = _packed_disc(tmp_path)
    with pytest.raises(build.BuildError, match="title_labels"):
        build.build(cue, tmp_path / "out", None, profile=profile, title_labels=())


# --- word-RLE ("rle16") Yc block inside a profile file, palette from the same file ---
RB_LBA, RB_LO, RB_HI = 30, 0x800, 0x1800


def _rle_disc(tmp_path, tail=b""):
    from suchie2 import rle16
    f = bytearray(0x2000)
    f[0:0x40] = _pal_file()                                  # CRAM image at 0x0 (bank 0x10 at 0x20)
    blk = rle16.compress(_packed_bundle())
    f[RB_LO:RB_LO + len(blk)] = blk
    f[RB_LO + len(blk):RB_LO + len(blk) + len(tail)] = tail
    user = {}
    root = bytearray(2048)
    p = 0
    for rec in [_dirrec(b"\x00", 20, 2048, 2), _dirrec(b"\x01", 20, 2048, 2),
                _dirrec(b"BACK.BIN;1", RB_LBA, len(f))]:
        root[p:p + len(rec)] = rec
        p += len(rec)
    pvd = bytearray(2048)
    pvd[0:6] = b"\x01CD001"
    pvd[156:190] = _dirrec(b"\x00", 20, 2048, 2)
    user[16], user[20] = bytes(pvd), bytes(root)
    for i in range(0, len(f), 2048):
        user[RB_LBA + i // 2048] = bytes(f[i:i + 2048])
    raw = bytearray()
    for lba in range(RB_LBA + 6):
        s = bytearray(2352)
        s[0:12] = cdsector.SYNC
        m, rem = divmod(lba + 150, 4500)
        sec, fr = divmod(rem, 75)
        s[12:15] = bytes(((v // 10) << 4) | (v % 10) for v in (m, sec, fr))
        s[15] = 1
        s[16:2064] = user.get(lba, bytes(2048))
        raw += cdsector.fix_mode1(s)
    (tmp_path / "t1.bin").write_bytes(bytes(raw))
    (tmp_path / "t2.bin").write_bytes(b"\x00" * 2352 * 3)
    cue = tmp_path / "src.cue"
    cue.write_text('FILE "t1.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n'
                   'FILE "t2.bin" BINARY\n  TRACK 02 AUDIO\n    INDEX 00 00:00:00\n    INDEX 01 00:02:00\n')
    profile = build.SourceProfile(
        track1_sha1=hashlib.sha1(bytes(raw)).hexdigest(), track2_sha1=hashlib.sha1(b"\x00" * 2352 * 3).hexdigest(),
        prolog_name="PROLOG.BIN", prolog_lba=0, prolog_size=0, title_region=(0, 1), title_region_sha1="",
        files={"BACK.BIN": (RB_LBA, len(f))},
        packed={"boot": ("BACK.BIN", RB_LO, RB_HI, hashlib.sha1(bytes(f[RB_LO:RB_HI])).hexdigest(), "rle16")})
    return cue, profile, bytes(f)


def _rle_job(tmp_path, **over):
    job = _packed_tables(tmp_path, packed="boot", palette_file="BACK.BIN", **over)
    return {**job, "packed": "boot"}


def test_rle16_block_is_rewritten_in_place(tmp_path):
    from suchie2 import rle16, select1
    cue, profile, src = _rle_disc(tmp_path)
    job = _rle_job(tmp_path)
    m = build.build(cue, tmp_path / "out", None, profile=profile, bundles=[job], title=False)
    new = _read_prolog(tmp_path / "out" / m["track1"], RB_LBA, len(src))
    bundle, used = rle16.decompress(new[RB_LO:RB_HI])
    want = select1.render(_packed_bundle(), job["translation"], job["layout"], palette_data=src).textures
    assert {i: e.data for i, e in enumerate(yc.parse(bundle))} == want
    assert not any(new[RB_LO + used:RB_HI]) and new[:RB_LO] == src[:RB_LO] and new[RB_HI:] == src[RB_HI:]
    assert m["bundles"][0]["codec"] == "rle16" and m["bundles"][0]["stream_bytes"] == used


def test_rle16_region_tail_must_be_zero(tmp_path):
    cue, profile, _ = _rle_disc(tmp_path, tail=b"\x01")
    with pytest.raises(build.BuildError, match="padding"):
        build.build(cue, tmp_path / "out", None, profile=profile, bundles=[_rle_job(tmp_path)], title=False)


@pytest.mark.parametrize("pal_off,ok", [(RB_LO - 0x30, False),      # bank 0x10 -> RB_LO-0x10 .. RB_LO+0x10
                                         (RB_HI - 0x30, False),      # bank starts inside the region (RB_HI-0x10)
                                         (RB_HI - 0x20, True),       # bank starts exactly at RB_HI
                                         (RB_LO - 0x40, True)])      # bank ends exactly at RB_LO
def test_palette_in_the_same_file_must_lie_outside_the_rewritten_region(tmp_path, pal_off, ok):
    cue, profile, src = _rle_disc(tmp_path)
    job = _rle_job(tmp_path, palette_offset=hex(pal_off))
    t = json.loads(job["translation"].read_text())
    for e in t["entries"]:
        e["palette_sha1"] = hashlib.sha1(src[pal_off + 0x20:pal_off + 0x40]).hexdigest()
    job["translation"].write_text(json.dumps(t, ensure_ascii=False))
    if ok:
        build.build(cue, tmp_path / "out", None, profile=profile, bundles=[job], title=False)
    else:
        with pytest.raises(build.BuildError, match="palette"):
            build.build(cue, tmp_path / "out", None, profile=profile, bundles=[job], title=False)


def test_palette_must_not_lie_in_another_block_rewritten_in_the_same_file(tmp_path):
    from suchie2 import rle16
    cue, profile, src = _rle_disc(tmp_path)
    # a second packed block over the CRAM image at 0x0 (it holds no valid stream, but the check comes first)
    two = build.SourceProfile(**{**profile.__dict__, "packed": {**profile.packed,
                                 "pal": ("BACK.BIN", 0x0, 0x800, hashlib.sha1(src[:0x800]).hexdigest(), "rle16")}})
    job2 = {**_rle_job(tmp_path), "packed": "boot"}
    with pytest.raises(build.BuildError, match="palette"):
        build.build(cue, tmp_path / "out", None, profile=two, title=False,
                    bundles=[job2, {"packed": "pal", "translation": job2["translation"], "layout": job2["layout"]}])


def test_rle16_readback_rejects_corrupted_output(tmp_path, monkeypatch):
    cue, profile, _ = _rle_disc(tmp_path)
    job = _rle_job(tmp_path)
    real = build.WritePlan.apply

    def corrupt(self, path):
        real(self, path)
        data = bytearray(path.read_bytes())
        off = RB_LO + 0x10                                     # inside the stream (file offset)
        data[(RB_LBA + off // 2048) * 2352 + 16 + off % 2048] ^= 0xFF
        path.write_bytes(bytes(data))
    monkeypatch.setattr(build.WritePlan, "apply", corrupt)
    with pytest.raises(build.BuildError):
        build.build(cue, tmp_path / "out", None, profile=profile, bundles=[job], title=False)


def test_unknown_codec_is_rejected(tmp_path):
    cue, profile, src = _rle_disc(tmp_path)
    fname, lo, hi, sha, _ = profile.packed["boot"]
    bad = build.SourceProfile(**{**profile.__dict__, "packed": {"boot": (fname, lo, hi, sha, "zip")}})
    with pytest.raises(build.BuildError, match="codec"):
        build.build(cue, tmp_path / "out", None, profile=bad, bundles=[_rle_job(tmp_path)], title=False)


def test_wheel_table_is_rendered_by_the_wheel_module(tmp_path, monkeypatch):
    cue, profile, sel = _stage_disc(tmp_path)
    tr, lay = _select1_tables(tmp_path)
    t = json.loads(tr.read_text())
    t["wheels"] = []
    tr.write_text(json.dumps(t, ensure_ascii=False))
    ents = yc.parse(sel[SEL_BUNDLE:SEL_BUNDLE + yc.length(sel[SEL_BUNDLE:])])
    seen = []

    def fake(data, translation, layout, font, glossary, file_name):
        seen.append(file_name)
        return build.select1_mod.Result(textures={1: bytes([0x12]) * len(ents[1].data)}, states={"needs_review": 1},
                                        ids=["w.x"])
    monkeypatch.setattr(build.wheel_mod, "render", fake)
    monkeypatch.setattr(build.select1_mod, "render", lambda *a, **k: pytest.fail("wheel table sent to select1"))
    m = build.build(cue, tmp_path / "out", None, profile=profile, title=False,
                    bundles=[{"files": ["STG1.BIN"], "translation": tr, "layout": lay}])
    raw = (tmp_path / "out" / m["track1"]).read_bytes()
    new = b"".join(raw[(60 + i) * 2352 + 16:(60 + i) * 2352 + 2064] for i in range(2))[:len(sel)]
    out = yc.parse(new[SEL_BUNDLE:SEL_BUNDLE + yc.length(new[SEL_BUNDLE:])])
    assert seen == ["STG1.BIN"] and out[1].data == bytes([0x12]) * len(ents[1].data) and out[0].data == ents[0].data
    assert m["bundles"][0]["entries"] == ["w.x"]
