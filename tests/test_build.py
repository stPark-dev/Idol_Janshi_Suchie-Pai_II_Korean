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


def _make_disc(tmp_path):
    prolog = bytearray(0x2000)
    stream = lzss.compress(yc.build(_entries()))
    prolog[BLOCK_OFF:BLOCK_OFF + len(stream)] = stream
    user = {}
    root = bytearray(2048)
    p = 0
    for rec in [_dirrec(b"\x00", 20, 2048, 2), _dirrec(b"\x01", 20, 2048, 2),
                _dirrec(b"PROLOG.BIN;1", FILE_LBA, len(prolog))]:
        root[p:p + len(rec)] = rec
        p += len(rec)
    pvd = bytearray(2048)
    pvd[0:6] = b"\x01CD001"
    pvd[156:190] = _dirrec(b"\x00", 20, 2048, 2)
    user[16], user[20] = bytes(pvd), bytes(root)
    for i in range(0, len(prolog), 2048):
        user[FILE_LBA + i // 2048] = bytes(prolog[i:i + 2048])
    raw = bytearray()
    for lba in range(FILE_LBA + len(prolog) // 2048 + 2):
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
        title_region_sha1=hashlib.sha1(bytes(prolog[BLOCK_OFF:REGION_END])).hexdigest())
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
