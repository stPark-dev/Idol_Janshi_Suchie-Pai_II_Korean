import struct

import pytest

from suchie2 import iso9660


def _dirrec(name: bytes, lba: int, size: int, flags: int = 0) -> bytes:
    n = len(name)
    ln = 33 + n + (1 - n % 2)
    r = bytearray(ln)
    r[0] = ln
    r[2:10] = struct.pack("<I", lba) + struct.pack(">I", lba)
    r[10:18] = struct.pack("<I", size) + struct.pack(">I", size)
    r[25] = flags
    r[32] = n
    r[33:33 + n] = name
    return bytes(r)


def _image(files):
    """user-data sectors: PVD at 16, root dir at 20, file data from 21"""
    secs = {}
    root = bytearray(2048)
    p = 0
    for rec in [_dirrec(b"\x00", 20, 2048, 2), _dirrec(b"\x01", 20, 2048, 2)] + [
            _dirrec(n.encode() + b";1", lba, len(d)) for n, lba, d in files]:
        root[p:p + len(rec)] = rec
        p += len(rec)
    pvd = bytearray(2048)
    pvd[0:6] = b"\x01CD001"
    pvd[156:156 + 34] = _dirrec(b"\x00", 20, 2048, 2)
    secs[16] = bytes(pvd)
    secs[20] = bytes(root)
    for _, lba, d in files:
        for i in range(0, max(len(d), 1), 2048):
            secs[lba + i // 2048] = d[i:i + 2048].ljust(2048, b"\x00")
    return lambda lba: secs.get(lba, bytes(2048))


def test_find_file_returns_extent():
    read = _image([("ABC.BIN", 21, b"x" * 3000), ("0", 30, b"y" * 10)])
    assert iso9660.find_file(read, "ABC.BIN") == (21, 3000)
    assert iso9660.find_file(read, "0") == (30, 10)


def test_find_file_missing_raises():
    read = _image([("ABC.BIN", 21, b"x")])
    with pytest.raises(iso9660.IsoError):
        iso9660.find_file(read, "NOPE.BIN")


def test_bad_pvd_raises():
    with pytest.raises(iso9660.IsoError):
        iso9660.find_file(lambda lba: bytes(2048), "ABC.BIN")
