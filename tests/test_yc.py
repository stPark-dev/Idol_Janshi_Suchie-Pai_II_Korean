import pytest

from suchie2 import yc


def _sample():
    return [
        yc.Entry(width=16, height=8, attr=0x0080, colr=0x2010, data=bytes(range(64))),
        yc.Entry(width=8, height=2, attr=0x0080, colr=0x2020, data=b"\x12" * 8),
    ]


def test_build_layout():
    raw = yc.build(_sample())
    assert raw[:4] == bytes.fromhex("5963 0002")
    assert raw[4:12] == bytes.fromhex("0010 0008 0080 2010")
    assert raw[12:20] == bytes.fromhex("0008 0002 0080 2020")
    assert raw[20:84] == bytes(range(64))
    assert len(raw) == 4 + 2 * 8 + 64 + 8


def test_parse_round_trip():
    raw = yc.build(_sample())
    assert yc.build(yc.parse(raw)) == raw
    assert yc.parse(raw) == _sample()


def test_parse_rejects_bad_magic():
    raw = bytearray(yc.build(_sample()))
    raw[0] = 0
    with pytest.raises(yc.YcError):
        yc.parse(bytes(raw))


def test_parse_rejects_length_mismatch():
    raw = yc.build(_sample())
    with pytest.raises(yc.YcError):
        yc.parse(raw + b"\x00")
    with pytest.raises(yc.YcError):
        yc.parse(raw[:-1])


def test_build_rejects_wrong_data_size():
    with pytest.raises(yc.YcError):
        yc.build([yc.Entry(width=16, height=8, attr=0x0080, colr=0x2010, data=b"\x00" * 63)])


def test_parse_rejects_non_4bpp_mode():
    raw = bytearray(yc.build(_sample()))
    raw[8:10] = (0x0080 | (4 << 3)).to_bytes(2, "big")  # 8bpp colour mode in attr
    with pytest.raises(yc.YcError):
        yc.parse(bytes(raw))
