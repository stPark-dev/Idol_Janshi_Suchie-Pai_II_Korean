import os

import pytest

from suchie2 import cdsector


def _bitwise_edc(data: bytes) -> int:
    # independent reference: reflected CRC-32, polynomial 0xD8018001, init 0, no final xor
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ (0xD8018001 if crc & 1 else 0)
    return crc


def _sector(lba: int, payload: bytes) -> bytearray:
    s = bytearray(2352)
    s[0:12] = b"\x00" + b"\xff" * 10 + b"\x00"
    m, rem = divmod(lba + 150, 75 * 60)
    sec, fr = divmod(rem, 75)
    s[12:15] = bytes(((v // 10) << 4) | (v % 10) for v in (m, sec, fr))
    s[15] = 1
    s[16:16 + len(payload)] = payload
    return s


def test_edc_matches_bitwise_reference():
    data = os.urandom(0x810)
    assert cdsector.edc(data) == _bitwise_edc(data)


def test_fix_mode1_writes_edc_little_endian_and_zero_field():
    s = cdsector.fix_mode1(_sector(1234, os.urandom(2048)))
    assert int.from_bytes(s[0x810:0x814], "little") == _bitwise_edc(bytes(s[:0x810]))
    assert s[0x814:0x81C] == bytes(8)


def test_ecc_is_zero_for_zero_input_and_linear():
    zero = bytearray(2352)
    cdsector.fix_mode1(zero)
    assert zero[0x81C:] == bytes(2352 - 0x81C)
    a = cdsector.fix_mode1(_sector(10, os.urandom(2048)))
    b = cdsector.fix_mode1(_sector(10, os.urandom(2048)))
    x = bytearray(p ^ q for p, q in zip(a, b))
    x[0x810:] = bytes(len(x) - 0x810)
    cdsector.fix_mode1(x)
    assert bytes(x[0x81C:]) == bytes(p ^ q for p, q in zip(a[0x81C:], b[0x81C:]))


def test_fix_mode1_is_idempotent_and_detects_changes():
    s = cdsector.fix_mode1(_sector(99, os.urandom(2048)))
    again = cdsector.fix_mode1(bytearray(s))
    assert again == s
    s2 = bytearray(s)
    s2[100] ^= 1
    cdsector.fix_mode1(s2)
    assert s2[0x810:] != s[0x810:]


def test_fix_mode1_rejects_non_mode1():
    s = _sector(5, b"")
    s[15] = 2
    with pytest.raises(cdsector.SectorError):
        cdsector.fix_mode1(s)
    with pytest.raises(cdsector.SectorError):
        cdsector.fix_mode1(bytearray(2048))


def test_msf_header_round_trip():
    s = _sector(271000, b"")
    assert cdsector.header_lba(bytes(s)) == 271000
