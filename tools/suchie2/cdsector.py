"""CD-ROM Mode 1 raw sector (2352 bytes) EDC/ECC per ECMA-130."""

SYNC = b"\x00" + b"\xff" * 10 + b"\x00"
RAW = 2352
USER = 2048


class SectorError(ValueError):
    pass


_ECC_F = [0] * 256
_ECC_B = [0] * 256
_EDC = [0] * 256
for _i in range(256):
    _j = ((_i << 1) ^ (0x11D if _i & 0x80 else 0)) & 0xFF
    _ECC_F[_i] = _j
    _ECC_B[_i ^ _j] = _i
    _e = _i
    for _ in range(8):
        _e = (_e >> 1) ^ (0xD8018001 if _e & 1 else 0)
    _EDC[_i] = _e


def edc(data: bytes) -> int:
    e = 0
    for b in data:
        e = (e >> 8) ^ _EDC[(e ^ b) & 0xFF]
    return e


def _ecc(sec: bytearray, major: int, minor: int, mmult: int, minc: int, dest: int) -> None:
    size = major * minor
    for mj in range(major):
        idx = (mj >> 1) * mmult + (mj & 1)
        a = b = 0
        for _ in range(minor):
            t = sec[12 + idx]
            idx += minc
            if idx >= size:
                idx -= size
            a ^= t
            b ^= t
            a = _ECC_F[a]
        a = _ECC_B[_ECC_F[a] ^ b]
        sec[dest + mj] = a
        sec[dest + mj + major] = a ^ b


def fix_mode1(sec: bytearray) -> bytearray:
    """Recompute EDC, the zero field and P/Q parity in place; returns the sector."""
    if len(sec) != RAW:
        raise SectorError(f"raw sector must be {RAW} bytes")
    # an all-zero sync/header is accepted so parity can be computed for linear-code checks
    if sec[15] != 1 and any(sec[:16]):
        raise SectorError(f"not a Mode 1 sector (mode byte {sec[15]})")
    sec[0x810:0x814] = edc(sec[:0x810]).to_bytes(4, "little")
    sec[0x814:0x81C] = bytes(8)
    _ecc(sec, 86, 24, 2, 86, 0x81C)
    _ecc(sec, 52, 43, 86, 88, 0x8C8)
    return sec


def header_lba(sec: bytes) -> int:
    if sec[:12] != SYNC:
        raise SectorError("missing sync pattern")
    m, s, f = (((v >> 4) * 10) + (v & 15) for v in sec[12:15])
    return (m * 60 + s) * 75 + f - 150
