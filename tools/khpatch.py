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
                   help="'original' recompresses the untouched title bundle (logo and 最初から/続きから, codec control)")
    b.add_argument("--select1", choices=["ko", "original"], default="ko",
                   help="'original' leaves the menu sprite bundle SELECT1.BIN unchanged")
    b.add_argument("--match", choices=["ko", "original"], default="ko",
                   help="'original' leaves the match-screen UI in the 13 stage overlays unchanged")
    b.add_argument("--cards", choices=["ko", "original"], default="ko",
                   help="'original' leaves the opponent introduction cards (AP*.BIN) unchanged")
    b.add_argument("--opening", choices=["ko", "original"], default="ko",
                   help="'original' leaves the opening big letters and name plates (PROLOG.BIN LZSS blocks) unchanged")
    b.add_argument("--boot", choices=["ko", "original"], default="ko",
                   help="'original' leaves the first-boot notice (BACKRAM.BIN word-RLE block) unchanged")
    b.add_argument("--panel", choices=["ko", "original"], default="ko",
                   help="'original' leaves the panel match bonus game (PMATCH.BIN, roulette included) unchanged")
    b.add_argument("--ending", choices=["ko", "original"], default="ko",
                   help="'original' leaves the ending credit rolls (ENDY, ED_*.DAT) and the after-ending screens (CLEAR.DAT) unchanged")
    b.add_argument("--subtitles", choices=["ko", "original"], default="ko",
                   help="'original' leaves out the voice subtitles (executable stub, SUB.BIN, SUBDAT.BIN)")
    b.add_argument("--bonus", choices=["ko", "original"], default="ko",
                   help="'original' leaves the big-win bonus screens (MAXGRP1/2/3.BIN) unchanged")
    args = ap.parse_args(argv)
    spec = ROOT / "assets/title/title_ko.json" if args.title == "ko" else None
    sel = (ROOT / "translation/select1.json", ROOT / "assets/select1/layout.json",
           ROOT / "translation/glossary.json") if args.select1 == "ko" else None
    jobs = [] if args.match == "original" else [
        {"files": build.STAGE_FILES, "translation": ROOT / "translation/match.json",
         "layout": ROOT / "assets/match/layout.json", "glossary": ROOT / "translation/glossary.json"},
        {"files": build.STAGE_FILES, "translation": ROOT / "translation/match2.json",
         "layout": ROOT / "assets/match/layout2.json", "glossary": ROOT / "translation/glossary.json"}]
    if args.cards == "ko":
        jobs += [{"files": [f], "translation": ROOT / f"translation/cards/{f[:-4]}.json",
                  "layout": ROOT / "assets/cards/layout.json", "glossary": ROOT / "translation/glossary.json"}
                 for f in build.CARD_FILES]
    if args.opening == "ko":
        jobs.append({"packed": "letters", "translation": ROOT / "translation/letters.json",
                     "layout": ROOT / "assets/opening/letters_layout.json", "glossary": ROOT / "translation/glossary.json"})
        jobs.append({"packed": "opening", "translation": ROOT / "translation/opening.json",
                     "layout": ROOT / "assets/opening/layout.json", "glossary": ROOT / "translation/glossary.json"})
    if args.panel == "ko":
        jobs.append({"files": ["PMATCH.BIN"], "translation": ROOT / "translation/panel.json",
                     "layout": ROOT / "assets/panel/layout.json", "glossary": ROOT / "translation/glossary.json"})
        jobs.append({"files": ["PMATCH.BIN"], "translation": ROOT / "translation/roulette.json",
                     "layout": ROOT / "assets/roulette/layout.json", "glossary": ROOT / "translation/glossary.json"})
    if args.bonus == "ko":
        jobs.append({"files": build.BONUS_FILES, "translation": ROOT / "translation/maxgrp.json",
                     "layout": ROOT / "assets/maxgrp/layout.json", "glossary": ROOT / "translation/glossary.json"})
    if args.ending == "ko":
        jobs += [{"files": [f], "translation": ROOT / f"translation/credits/{f.split('.')[0]}.json",
                  "layout": ROOT / f"assets/ending/{f.split('.')[0]}.json", "glossary": ROOT / "translation/glossary.json"}
                 for f in build.ROLL_FILES]
        jobs.append({"files": ["CLEAR.DAT"], "translation": ROOT / "translation/clear.json",
                     "layout": ROOT / "assets/ending/clear.json", "glossary": ROOT / "translation/glossary.json"})
    if args.boot == "ko":
        jobs.append({"packed": "boot_notice", "translation": ROOT / "translation/boot_notice.json",
                     "layout": ROOT / "assets/boot/layout.json", "glossary": ROOT / "translation/glossary.json"})
        jobs += [{"packed": n, "translation": ROOT / f"translation/{n}.json", "layout": ROOT / "assets/boot/notices_layout.json",
                  "glossary": ROOT / "translation/glossary.json"} for n in ("boot_unready", "boot_ram", "boot_savefail")]
    try:
        labels = (ROOT / "translation/title_labels.json", ROOT / "assets/title/labels_layout.json",
                  ROOT / "translation/glossary.json") if args.title == "ko" else None
        subs = {"code": ROOT / "assets/subtitle/SUB.BIN", "scenes": ROOT / "assets/subtitle/scenes.json",
                "voices": ROOT / "translation/voice"} if args.subtitles == "ko" else None
        m = build.build(args.source, args.out, spec, select1=sel, bundles=jobs, title_labels=labels, subtitles=subs)
    except build.BuildError as err:
        print(f"build failed: {err}", file=sys.stderr)
        return 1
    print(f"{args.out / m['cue']}  title={m['title']['mode']} stream={m['title']['stream_bytes']} bytes")
    if m["select1"]:
        print(f"  select1: {len(m['select1']['entries'])} labels, states {m['select1']['states']}, "
              f"sectors {len(m['select1']['sectors'])}")
    if m["bundles"]:
        print(f"  bundles: {len(m['bundles'])} file jobs, sectors {sum(len(b['sectors']) for b in m['bundles'])}")
    if m.get("subtitles"):
        st = m["subtitles"]
        print(f"  subtitles: {len(st['groups'])} groups, {st['lines']} lines {st['status']}, "
              f"SUBDAT {st['subdat_bytes']} bytes")
    print(f"  distribution: {m['distribution']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
