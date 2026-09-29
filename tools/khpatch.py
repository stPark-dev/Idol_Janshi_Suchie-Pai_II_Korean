#!/usr/bin/env python3
"""Idol Janshi Suchie-Pai II (Saturn, Disc 1) Korean patch builder."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from suchie2 import build  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build the patched disc image")
    b.add_argument("--source", required=True, type=Path, help="original Disc 1 .cue")
    b.add_argument("--out", type=Path, default=ROOT / "out")
    b.add_argument("--title", choices=["ko", "original"], default="ko",
                   help="'original' recompresses the untouched title bundle (codec control)")
    args = ap.parse_args(argv)
    spec = ROOT / "assets/title/title_ko.json" if args.title == "ko" else None
    try:
        m = build.build(args.source, args.out, spec)
    except build.BuildError as err:
        print(f"build failed: {err}", file=sys.stderr)
        return 1
    print(f"{args.out / m['cue']}  title={m['title']['mode']} stream={m['title']['stream_bytes']} bytes "
          f"sectors={m['title']['sectors']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
