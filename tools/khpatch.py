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
    b.add_argument("--select1", choices=["ko", "original"], default="ko",
                   help="'original' leaves the menu sprite bundle SELECT1.BIN unchanged")
    b.add_argument("--match", choices=["ko", "original"], default="ko",
                   help="'original' leaves the match-screen UI in the 13 stage overlays unchanged")
    args = ap.parse_args(argv)
    spec = ROOT / "assets/title/title_ko.json" if args.title == "ko" else None
    sel = (ROOT / "translation/select1.json", ROOT / "assets/select1/layout.json",
           ROOT / "translation/glossary.json") if args.select1 == "ko" else None
    jobs = [] if args.match == "original" else [
        {"files": build.STAGE_FILES, "translation": ROOT / "translation/match.json",
         "layout": ROOT / "assets/match/layout.json", "glossary": ROOT / "translation/glossary.json"},
        {"files": build.STAGE_FILES, "translation": ROOT / "translation/match2.json",
         "layout": ROOT / "assets/match/layout2.json", "glossary": ROOT / "translation/glossary.json"}]
    try:
        m = build.build(args.source, args.out, spec, select1=sel, bundles=jobs)
    except build.BuildError as err:
        print(f"build failed: {err}", file=sys.stderr)
        return 1
    print(f"{args.out / m['cue']}  title={m['title']['mode']} stream={m['title']['stream_bytes']} bytes")
    if m["select1"]:
        print(f"  select1: {len(m['select1']['entries'])} labels, states {m['select1']['states']}, "
              f"sectors {len(m['select1']['sectors'])}")
    if m["bundles"]:
        print(f"  bundles: {len(m['bundles'])} file jobs, sectors {sum(len(b['sectors']) for b in m['bundles'])}")
    print(f"  distribution: {m['distribution']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
