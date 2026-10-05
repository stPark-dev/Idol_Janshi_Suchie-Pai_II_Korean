#!/usr/bin/env python3
"""Make the distribution zip from a finished build (BPS patches only, no game data).

  python tools/mkrelease.py --source "/path/Idol Janshi Suchie-Pai II (Japan) (Disc 1).cue" \
      --build out/v11 --version 1.1

Layout:
  Suchie-Pai2_KR_v<ver>/README.txt                                   <- docs/release/README.txt
  Suchie-Pai2_KR_v<ver>/Redump/... (Disc 1) (Track 1).bin.bps         Redump track 1 -> Korean track 1
  Suchie-Pai2_KR_v<ver>/Redump/Idol Janshi Suchie-Pai II (Korean) (Disc 1).cue
                                                                      Korean track 1 + original track 2
  Suchie-Pai2_KR_v<ver>/CHD/Idol Janshi Suchie-Pai II (Japan) (Disc 1).bin.bps
                                                                      chdman's single BIN -> Korean BIN
  Suchie-Pai2_KR_v<ver>/CHD/Idol Janshi Suchie-Pai II (Korean) (Disc 1).cue

The source is the Redump BIN/CUE (two tracks); the single BIN that `chdman extractcd` writes from
a CHD of it is the two tracks joined, so its patch is made from that join.  Every patch is
applied back to its source and checked against the build before zipping.
"""
import argparse
import hashlib
import re
import sys
import zipfile
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TRACK1_SHA1 = "90efa69e3b3f89503e356d6d6f5112ff8a541f1d"
TRACK2_SHA1 = "5328aad6e81dc43b59ccde73ada1f51c930be5e4"
JOINED_SHA1 = "c943e23cdd74bb223e1cb2de7fb10e3c27379042"   # chdman extractcd of the Redump image
JP = "Idol Janshi Suchie-Pai II (Japan) (Disc 1)"
KO = "Idol Janshi Suchie-Pai II (Korean) (Disc 1)"
MERGE_GAP = 16      # equal runs shorter than this stay inside a TargetRead


def _num(n: int) -> bytes:
    out = bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        if n == 0:
            out.append(0x80 | x)
            return bytes(out)
        out.append(x)
        n -= 1


def bps_create(src: bytes, tgt: bytes) -> bytes:
    """Linear BPS: SourceRead where the bytes match in place, TargetRead elsewhere."""
    n = min(len(src), len(tgt))
    a = np.frombuffer(src, np.uint8, n)
    b = np.frombuffer(tgt, np.uint8, n)
    diff = np.flatnonzero(a != b)
    runs = []                                    # [start, end) of differing bytes
    if len(diff):
        cut = np.flatnonzero(np.diff(diff) > MERGE_GAP)
        starts = np.concatenate(([diff[0]], diff[cut + 1]))
        ends = np.concatenate((diff[cut], [diff[-1]])) + 1
        runs = list(zip(starts.tolist(), ends.tolist()))
    if len(tgt) > n:
        if runs and runs[-1][1] >= n - MERGE_GAP:
            runs[-1] = (runs[-1][0], len(tgt))
        else:
            runs.append((n, len(tgt)))
    out = bytearray(b"BPS1" + _num(len(src)) + _num(len(tgt)) + _num(0))
    pos = 0
    for s, e in runs:
        if s > pos:
            out += _num(((s - pos) - 1) << 2 | 0)
        out += _num(((e - s) - 1) << 2 | 1) + tgt[s:e]
        pos = e
    if pos < len(tgt):
        out += _num(((len(tgt) - pos) - 1) << 2 | 0)
    out += (zlib.crc32(src) & 0xFFFFFFFF).to_bytes(4, "little")
    out += (zlib.crc32(tgt) & 0xFFFFFFFF).to_bytes(4, "little")
    out += (zlib.crc32(out) & 0xFFFFFFFF).to_bytes(4, "little")
    return bytes(out)


def bps_apply(patch: bytes, src: bytes) -> bytes:
    """Full BPS decoder (all four actions), used to check every patch we write."""
    assert patch[:4] == b"BPS1"
    assert zlib.crc32(patch[:-4]) & 0xFFFFFFFF == int.from_bytes(patch[-4:], "little"), "patch crc"
    p = 4

    def num():
        nonlocal p
        data, shift = 0, 1
        while True:
            x = patch[p]
            p += 1
            data += (x & 0x7F) * shift
            if x & 0x80:
                return data
            shift <<= 7
            data += shift

    ssize, tsize, msize = num(), num(), num()
    p += msize
    assert ssize == len(src), "source size"
    assert zlib.crc32(src) & 0xFFFFFFFF == int.from_bytes(patch[-12:-8], "little"), "source crc"
    out = bytearray(tsize)
    o = srel = trel = 0
    end = len(patch) - 12
    while p < end:
        d = num()
        cmd, ln = d & 3, (d >> 2) + 1
        if cmd == 0:
            out[o:o + ln] = src[o:o + ln]
        elif cmd == 1:
            out[o:o + ln] = patch[p:p + ln]
            p += ln
        else:
            v = num()
            delta = -(v >> 1) if v & 1 else v >> 1
            if cmd == 2:
                srel += delta
                out[o:o + ln] = src[srel:srel + ln]
                srel += ln
            else:
                trel += delta
                for i in range(ln):
                    out[o + i] = out[trel + i]
                trel += ln
        o += ln
    assert o == tsize, "target size"
    assert zlib.crc32(out) & 0xFFFFFFFF == int.from_bytes(patch[-8:-4], "little"), "target crc"
    return bytes(out)


def sha1(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def tracks(cue: Path) -> list[Path]:
    return [cue.parent / f for f in re.findall(r'^\s*FILE\s+"([^"]+)"', cue.read_text(encoding="utf-8"), re.M)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="Redump CUE of the original disc 1")
    ap.add_argument("--build", default=str(ROOT / "out"), help="khpatch build output (distribution: true)")
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", default=str(ROOT / "out/release"))
    a = ap.parse_args()

    s1, s2 = (p.read_bytes() for p in tracks(Path(a.source)))
    if sha1(s1) != TRACK1_SHA1 or sha1(s2) != TRACK2_SHA1:
        sys.exit("source is not the supported Redump disc 1")
    build = Path(a.build)
    manifest = (build / "manifest.json").read_text(encoding="utf-8")
    if '"distribution": true' not in manifest:
        sys.exit("the build is not marked distribution: true")
    t1, t2 = (p.read_bytes() for p in tracks(next(build.glob("*.cue"))))
    if t2 != s2 or len(t1) != len(s1):
        sys.exit("build does not match the source layout (track 1 only may differ)")
    if sha1(s1 + s2) != JOINED_SHA1:
        sys.exit("joined source BIN hash differs from the chdman single BIN")

    name = f"Suchie-Pai2_KR_v{a.version}"
    top = Path(a.out) / name
    (top / "Redump").mkdir(parents=True, exist_ok=True)
    (top / "CHD").mkdir(exist_ok=True)
    readme = (ROOT / "docs/release/README.txt").read_text(encoding="utf-8")
    (top / "README.txt").write_bytes(readme.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))

    redump = bps_create(s1, t1)
    assert bps_apply(redump, s1) == t1
    (top / f"Redump/{JP} (Track 1).bin.bps").write_bytes(redump)
    cue = [f'FILE "{KO} (Track 1).bin" BINARY', "  TRACK 01 MODE1/2352", "    INDEX 01 00:00:00",
           f'FILE "{JP} (Track 2).bin" BINARY', "  TRACK 02 AUDIO", "    INDEX 00 00:00:00", "    INDEX 01 00:02:00"]
    (top / f"Redump/{KO}.cue").write_bytes(("\r\n".join(cue) + "\r\n").encode())

    chd = bps_create(s1 + s2, t1 + t2)
    assert bps_apply(chd, s1 + s2) == t1 + t2
    (top / f"CHD/{JP}.bin.bps").write_bytes(chd)
    lba2 = len(s1) // 2352
    msf = lambda l: f"{l // 4500:02d}:{l // 75 % 60:02d}:{l % 75:02d}"  # noqa: E731
    chd_cue = [f'FILE "{KO}.bin" BINARY', "  TRACK 01 MODE1/2352", "    INDEX 01 00:00:00",
               "  TRACK 02 AUDIO", f"    INDEX 00 {msf(lba2)}", f"    INDEX 01 {msf(lba2 + 150)}"]
    (top / f"CHD/{KO}.cue").write_bytes(("\r\n".join(chd_cue) + "\r\n").encode())

    zpath = Path(a.out) / f"{name}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(top.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(top.parent).as_posix())
    print(f"Korean track 1  SHA-1 {sha1(t1)}")
    print(f"Korean BIN      SHA-1 {sha1(t1 + t2)}")
    print(f"Redump patch    {len(redump):,} bytes, CHD patch {len(chd):,} bytes")
    print(f"{zpath}  {zpath.stat().st_size:,} bytes  SHA-1 {sha1(zpath.read_bytes())}")


if __name__ == "__main__":
    main()
