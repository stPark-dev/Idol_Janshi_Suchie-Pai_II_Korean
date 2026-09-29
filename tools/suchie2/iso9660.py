"""Minimal ISO 9660 lookup: root-directory file name -> (LBA, size)."""
import struct
from typing import Callable

ReadUser = Callable[[int], bytes]   # lba -> 2048 bytes of user data


class IsoError(ValueError):
    pass


def _records(read: ReadUser, lba: int, size: int):
    data = b"".join(read(lba + i) for i in range((size + 2047) // 2048))
    p = 0
    while p < len(data):
        ln = data[p]
        if ln == 0:
            p = (p // 2048 + 1) * 2048
            continue
        yield data[p:p + ln]
        p += ln


def find_file(read: ReadUser, name: str) -> tuple[int, int]:
    pvd = read(16)
    if pvd[:6] != b"\x01CD001":
        raise IsoError("no primary volume descriptor at LBA 16")
    root = pvd[156:190]
    lba, size = struct.unpack("<I", root[2:6])[0], struct.unpack("<I", root[10:14])[0]
    for rec in _records(read, lba, size):
        nm = rec[33:33 + rec[32]]
        if nm in (b"\x00", b"\x01") or rec[25] & 2:
            continue
        if nm.decode("latin1").split(";")[0] == name:
            return struct.unpack("<I", rec[2:6])[0], struct.unpack("<I", rec[10:14])[0]
    raise IsoError(f"{name} not found in root directory")
