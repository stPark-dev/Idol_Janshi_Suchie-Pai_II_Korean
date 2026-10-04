"""Voice subtitles: executable stub encoding, ISO root rewrite, group/index format (no game data)."""
import struct

import pytest

from suchie2 import label, subtitles as S


def _w(code: bytes, addr: int) -> int:
    o = addr - S.FUNC
    return struct.unpack(">H", code[o:o + 2])[0]


def _movl_target(code: bytes, addr: int) -> int:
    ins = _w(code, addr)
    assert ins >> 12 == 0xD
    lit = ((addr & ~3) + 4) + (ins & 0xFF) * 4
    o = lit - S.FUNC
    return struct.unpack(">I", code[o:o + 4])[0]


def _branch_target(code: bytes, addr: int) -> int:
    ins = _w(code, addr)
    disp = ins & 0xFF
    disp -= 0x100 if disp & 0x80 else 0
    return addr + 4 + disp * 2


def test_vblank_wait_and_stub_literals_and_branches():
    code = S.vblank_wait_and_stub()
    assert len(code) == S.FUNC_END - S.FUNC
    f = S.FUNC
    assert _movl_target(code, f) == 0x25F80004                   # TVSTAT
    assert _branch_target(code, f + 6) == f + 2                  # wait loop
    assert _movl_target(code, f + 0x12) == 0x060B4E30            # TVMD RAM copy
    s = S.STUB
    assert _movl_target(code, s) == S.BASE
    assert _movl_target(code, s + 4) == S.MAGIC
    assert _branch_target(code, s + 8) == s + 0x34               # loaded -> jmp @r1
    assert _movl_target(code, s + 0x16) == S.LOADER
    mova = _w(code, s + 0x10)
    name_at = ((s + 0x10) & ~3) + 4 + (mova & 0xFF) * 4
    assert code[name_at - f:name_at - f + 8] == b"SUB.BIN\0"
    assert _branch_target(code, s + 0x32) == s + 0x38            # bf fail -> the loader itself
    assert _movl_target(code, s + 0x38) == S.LOADER
    assert _w(code, s + 0x34) == 0x412B                          # jmp @r1


def test_exe_changes_refuse_an_unknown_executable():
    with pytest.raises(S.SubtitleError):
        S.exe_changes(bytes(S.EXE_SIZE))
    with pytest.raises(S.SubtitleError):
        S.exe_changes(bytes(10))


def _rec(name: str, lba: int, size: int, flags: int = 0) -> S.DirRecord:
    tmpl = S.DirRecord(bytes(34))
    r = bytearray(S.new_record(tmpl, name, lba, size).raw)
    r[25] = flags
    return S.DirRecord(bytes(r))


def _dot(n: bytes) -> S.DirRecord:
    r = bytearray(34)
    r[0], r[32], r[33], r[25] = 34, 1, n[0], 2
    return S.DirRecord(bytes(r))


def test_root_directory_rewrite_keeps_iso_order_and_sector_rule():
    files = [_rec(f"F{i:03d}.BIN", 100 + i, 2048) for i in range(60)]
    recs = [_dot(b"\0"), _dot(b"\1")] + files
    sectors = S.pack_dir(recs, 3)
    assert S.parse_dir(sectors) == recs or [r.raw for r in S.parse_dir(sectors)] == [r.raw for r in recs]
    new = S.with_files(recs, [("SUB.BIN", 900, 5000), ("SUBDAT.BIN", 903, 9000)])
    names = [r.name for r in new[2:]]
    assert names == sorted(names, key=lambda n: (n.split(b";")[0].partition(b".")[0], n.split(b";")[0].partition(b".")[2]))
    sub = next(r for r in new if r.name == b"SUB.BIN;1")
    assert (sub.lba, sub.size) == (900, 5000)
    with pytest.raises(S.SubtitleError):
        S.with_files(recs, [("F001.BIN", 1, 1)])
    with pytest.raises(S.SubtitleError):
        S.pack_dir(new * 3, 3)


def test_pack_dir_never_fills_a_sector_exactly():
    recs = [S.DirRecord(bytes([44]) + bytes(43)) for _ in range(46)]  # 46 * 44 = 2024, +44 would be 2068
    out = S.pack_dir(recs[:46], 2)
    assert len(S.parse_dir(out[:1])) == 46
    exact = [S.DirRecord(bytes([64]) + bytes(63)) for _ in range(32)]  # 32 * 64 = 2048 exactly
    out = S.pack_dir(exact, 2)
    assert len(S.parse_dir(out[:1])) == 31


def _parse_group(data: bytes):
    magic, group, nvoices, ncues, nglyphs, o_names, o_cues, o_gl, o_text, o_bits = struct.unpack(">IHHHHIIIII", data[:32])
    names = [data[o_names + 8 * i:o_names + 8 * i + 8].rstrip(b"\0").decode() for i in range(nvoices)]
    cues = [struct.unpack(">BBHHHHHhh", data[o_cues + 16 * i:o_cues + 16 * i + 16]) for i in range(ncues)]
    return magic, group, names, cues, nglyphs


def test_group_format_wraps_long_lines_and_counts_vblanks():
    doc = {"voice": "STP001.AIF", "lines": [
        {"start": 1.0, "end": 2.0, "ko": "오랜만이야."},
        {"start": 2.5, "end": 6.0, "ko": "유서 깊은 피치 왕가에서 대대로 물려받은 진품이라고요."},
        {"start": 7.0, "end": 8.0, "ko": ""},
    ]}
    data = S.build_group(3, [doc, {"voice": "Q70B.AIF", "lines": []}], label.DEFAULT_FONT)
    magic, group, names, cues, nglyphs = _parse_group(data)
    assert (magic, group, names) == (S.GROUP_MAGIC, 3, ["STP001", "Q70B"])
    assert len(cues) == 2
    voice, _, start, end, _, n1, n2, x1, x2 = cues[0]
    assert (voice, n1) == (0, 0) and n2 == len("오랜만이야.")              # one line sits on the lower row
    assert start == round((1.0 - S.LEAD) * S.FPS)
    assert end <= round((2.5 - S.LEAD) * S.FPS)                              # stops before the next line
    _, _, _, _, _, n1, n2, x1, x2 = cues[1]
    assert n1 > 0 and n2 > 0                                                  # wrapped into two lines
    assert 0 <= x1 < 160 and 0 <= x2 < 160


def test_group_rejects_characters_outside_the_font_set():
    doc = {"voice": "B70B.AIF", "lines": [{"start": 0.0, "end": 1.0, "ko": "부끄러워♡"}]}
    with pytest.raises(S.SubtitleError, match="characters outside"):
        S.build_group(0, [doc], label.DEFAULT_FONT)


def test_index_packs_groups_and_lists_triggers():
    g0 = S.build_group(0, [{"voice": "A.AIF", "lines": [{"start": 0, "end": 1, "ko": "가"}]}], label.DEFAULT_FONT)
    g1 = S.build_group(1, [{"voice": "B.AIF", "lines": [{"start": 0, "end": 1, "ko": "나"}]}], label.DEFAULT_FONT)
    index, subdat = S.build_index([g0, g1], [["P000.MTS"], ["DTRM00.BIN", "DTRM10.MSX"]])
    magic, ntrig, ngroups = struct.unpack(">IHH", index[:8])
    assert (magic, ntrig, ngroups) == (S.INDEX_MAGIC, 3, 2)
    (o0, p0, n0), (o1, p1, n1) = struct.unpack(">IHH", index[8:16]), struct.unpack(">IHH", index[16:24])
    assert (o0, n0, n1) == (0, len(g0), len(g1)) and o1 == p0
    assert S.unpack(subdat[o0:o0 + p0])[0] == g0 and S.unpack(subdat[o1:o1 + p1])[0] == g1
    assert len(subdat) % 2048 == 0
    t = index[24 + 16:24 + 32]
    assert t[:12].rstrip(bytes(1)) == b"DTRM00.BIN" and t[12] == 1
    with pytest.raises(S.SubtitleError):
        S.build_index([g0], [["SPSANA2B.SPRX"]])


def test_unpack_model_matches_the_game_codec():
    from suchie2 import lzss
    data = bytes(range(256)) * 3 + bytes(500) + b"abcabcabc" * 40
    stream = lzss.compress(data)
    assert S.unpack(stream[4:])[0] == lzss.decompress(stream) == data


def test_disc_sectors_refuse_used_free_space():
    def user(lba):
        return b"\x01" + bytes(2047) if lba == S.FREE_LBA + 1 else bytes(2048)
    code = bytes(0x10) + struct.pack(">I", S.MAGIC) + bytes(12)
    with pytest.raises(S.SubtitleError, match="not empty"):
        S.disc_sectors(user, S.sub_bin(code, S.empty_index()), bytes(2048))


def test_sub_bin_checks_sizes():
    with pytest.raises(S.SubtitleError):
        S.sub_bin(bytes(S.CODE_MAX + 1), S.empty_index())
    img = S.sub_bin(bytes(16), S.empty_index())
    assert len(img) == S.CODE_MAX + 8 and img[S.CODE_MAX:S.CODE_MAX + 4] == b"KHIX"
