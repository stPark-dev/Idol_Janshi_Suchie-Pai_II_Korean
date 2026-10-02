"""Disc-free consistency checks of the committed translation tables and layouts."""
import json
from pathlib import Path

import pytest

from suchie2 import build

ROOT = Path(__file__).resolve().parents[1]
GLOSSARY = {t["id"] for t in json.loads((ROOT / "translation/glossary.json").read_text())["terms"]}


def _load(p):
    return json.loads(Path(p).read_text())


def test_card_tables_and_shared_layout_agree():
    layout = _load(ROOT / "assets/cards/layout.json")
    lay_ids = [e["id"] for e in layout["entries"]]
    tables = sorted((ROOT / "translation/cards").glob("*.json"))
    assert sorted(p.stem + ".BIN" for p in tables) == sorted(build.CARD_FILES)
    claimed = []
    for p in tables:
        t = _load(p)
        assert t["id_prefix"] == f"cards.{p.stem}." and t["files"] == [p.stem + ".BIN"]
        mine = [i for i in lay_ids if i.startswith(t["id_prefix"])]
        assert sorted(mine) == sorted(e["id"] for e in t["entries"])
        claimed += mine
        for e in t["entries"]:
            spec = next(x for x in layout["entries"] if x["id"] == e["id"])
            lines = (layout["templates"][spec["use"]] if "use" in spec else spec)["lines"]
            assert e["ko"].count("\n") + 1 == len(lines), e["id"]
            assert set(e["terms"]) <= GLOSSARY, e["id"]
            assert "palette_sha1" in e
    assert sorted(claimed) == sorted(lay_ids)


def test_every_table_protects_its_palettes_and_references_known_terms():
    for p in [ROOT / "translation/select1.json", ROOT / "translation/match.json", ROOT / "translation/match2.json",
              ROOT / "translation/opening.json", ROOT / "translation/letters.json",
              ROOT / "translation/title_labels.json", ROOT / "translation/boot_notice.json",
              ROOT / "translation/panel.json", ROOT / "translation/maxgrp.json",
              *[ROOT / f"translation/{n}.json" for n in ("boot_unready", "boot_ram", "boot_savefail")], *sorted((ROOT / "translation/cards").glob("*.json"))]:
        t = _load(p)
        for e in t["entries"]:
            assert len(e.get("palette_sha1", "")) == 40, (p.name, e["id"])
            assert set(e["terms"]) <= GLOSSARY, (p.name, e["id"])


def test_opening_table_names_the_profile_block_and_its_palette_file():
    t = _load(ROOT / "translation/opening.json")
    layout = _load(ROOT / "assets/opening/layout.json")
    assert t["packed"] in build.JP_DISC1.packed and t["palette_file"] in build.JP_DISC1.read_only
    assert sorted(e["id"] for e in layout["entries"]) == sorted(e["id"] for e in t["entries"])
    assert sorted([e["entry"] for e in t["entries"]] + [e["entry"] for e in t["excluded"]]) == list(range(47))
    for e in t["entries"]:
        spec = next(x for x in layout["entries"] if x["id"] == e["id"])
        segs = e["ko"].count("|") + 1
        fill = layout["templates"][spec["use"]]["lines"][0]["fill"]
        assert segs == (len(fill) if isinstance(fill, list) else 1), e["id"]


def test_opening_text_stays_inside_each_line_area():
    """The voice line must stay right of x 14: the name sprite sits 28px right of the '( 声 )' frame,
    whose Korean label occupies frame x 8-40. Extents do not depend on palette values."""
    from suchie2 import label
    t = _load(ROOT / "translation/opening.json")
    layout = _load(ROOT / "assets/opening/layout.json")
    by_id = {e["id"]: e for e in t["entries"]}
    pal = list(range(16))
    for spec in layout["entries"]:
        tpl = layout["templates"][spec["use"]]
        ln = {**tpl["lines"][0], **spec.get("line_overrides", {}).get("0", {})}
        w, h = tpl["box"][2], tpl["box"][3]
        label.render_lines((w, h), [{**ln, "text": by_id[spec["id"]]["ko"]}], pal, font_path=layout["font"],
                           box=ln["area"])
    x0, _, x1, _ = layout["templates"]["voice"]["lines"][0]["area"]
    assert x0 >= 14 and x1 <= 98              # frame x 42..126: between the Korean label and ')'


def test_letters_table_covers_the_whole_block_and_blank_slots_are_explained():
    t = _load(ROOT / "translation/letters.json")
    layout = _load(ROOT / "assets/opening/letters_layout.json")
    assert t["packed"] == "letters" and t["packed"] in build.JP_DISC1.packed
    assert t["palette_file"] in build.JP_DISC1.read_only
    assert sorted(e["entry"] for e in t["entries"]) == list(range(12)) and not t["excluded"]
    assert sorted(e["id"] for e in layout["entries"]) == sorted(e["id"] for e in t["entries"])
    for e in t["entries"]:
        assert "\n" not in e["ko"] and "|" not in e["ko"], e["id"]
        if e["ko"] == "":
            assert "빈칸" in e["note"], e["id"]          # an intentionally empty slot must say so


def test_title_labels_avoid_the_logo_spec_entries():
    t = _load(ROOT / "translation/title_labels.json")
    spec = _load(ROOT / "assets/title/title_ko.json")
    owned = {sp["entry"] for sp in spec["sprites"]} | set(spec["blank_entries"])
    assert t["packed"] == "title" and t["palette_file"] in build.JP_DISC1.read_only
    assert not owned & {e["entry"] for e in t["entries"]}
    title_entries = 11                      # the real title Yc block (docs/initial-survey.md 3.6)
    assert sorted([e["entry"] for e in t["entries"]] + [e["entry"] for e in t["excluded"]]) == list(range(title_entries))


def test_boot_notice_table_covers_its_block():
    t = _load(ROOT / "translation/boot_notice.json")
    layout = _load(ROOT / "assets/boot/layout.json")
    assert t["packed"] in build.JP_DISC1.packed and build.JP_DISC1.packed[t["packed"]][4] == "rle16"
    assert t["palette_file"] == build.JP_DISC1.packed[t["packed"]][0]       # CRAM image in the same file
    assert sorted([e["entry"] for e in t["entries"]] + [e["entry"] for e in t["excluded"]]) == list(range(8))
    assert sorted(e["id"] for e in layout["entries"]) == sorted(e["id"] for e in t["entries"])


def test_panel_table_classifies_every_entry_and_pending_ones_are_marked():
    t = _load(ROOT / "translation/panel.json")
    layout = _load(ROOT / "assets/panel/layout.json")
    assert t["files"] == ["PMATCH.BIN"] and "PMATCH.BIN" in build.JP_DISC1.files
    entries = sorted([e["entry"] for e in t["entries"]] + [e["entry"] for e in t["excluded"]])
    assert entries == list(range(205))
    translated = {e["id"] for e in t["entries"] if e["state"] != "untranslated"}
    assert sorted(s["id"] for s in layout["entries"]) == sorted(translated)
    for e in t["entries"]:
        if e["state"] == "untranslated":
            assert e["ko"] == "" and "다음 단계" in e["note"], e["id"]


def test_maxgrp_kanji_reuse_the_match_screen_translation():
    t = _load(ROOT / "translation/maxgrp.json")
    m = {e["src_sha1"]: e for e in _load(ROOT / "translation/match.json")["entries"]}
    assert t["files"] == ["MAXGRP1.BIN", "MAXGRP2.BIN", "MAXGRP3.BIN"]
    assert all(f in build.JP_DISC1.files for f in t["files"])
    assert sorted([e["entry"] for e in t["entries"]] + [e["entry"] for e in t["excluded"]]) == list(range(38))
    shared = [e for e in t["entries"] if e["src_sha1"] in m]
    assert len(shared) == 6 and all(e["ko"] == m[e["src_sha1"]]["ko"] for e in shared)


def test_roulette_owns_exactly_the_wheel_entries_the_panel_table_hands_over():
    t = _load(ROOT / "translation/roulette.json")
    lay = _load(ROOT / "assets/roulette/layout.json")
    panel = _load(ROOT / "translation/panel.json")
    handed = {e["entry"] for e in panel["excluded"] if "roulette.json" in e["reason"]}
    wheel_entries = [i for w in t["wheels"] for i in w["entries"]]
    assert sorted(wheel_entries) == sorted(handed) == list(range(197, 205))
    srcs = {e["entry"]: e["src_sha1"] for e in panel["excluded"]}
    for w in t["wheels"]:
        assert [srcs[i] for i in w["entries"]] == w["src_sha1"] and len(w["palette_sha1"]) == 40
    assert sorted(lay["wheels"]) == sorted(w["name"] for w in t["wheels"])
    assert t["files"] == ["PMATCH.BIN"]
    specs = {s["id"]: s for s in lay["entries"]}
    for e in t["entries"]:
        assert set(e["terms"]) <= GLOSSARY, e["id"]
        if e["state"] != "untranslated":
            assert e["ko"].count("\n") + 1 == len(specs[e["id"]]["lines"]), e["id"]


NOTICE_TABLES = {"boot_unready": (0x4000, "0x0", 3), "boot_ram": (0x27000, "0x2000", 7),
                 "boot_savefail": (0x46000, "0x3000", 6)}


def test_backram_blocks_tile_the_file_without_overlap():
    blocks = sorted((lo, hi) for f, lo, hi, *_ in build.JP_DISC1.packed.values() if f == "BACKRAM.BIN")
    assert blocks == [(0x4000, 0x17000), (0x17000, 0x27000), (0x27000, 0x46000), (0x46000, 0x69000)]
    assert build.JP_DISC1.files["BACKRAM.BIN"][1] == 0x69000


def test_other_boot_notice_tables_cover_their_blocks():
    layout = _load(ROOT / "assets/boot/notices_layout.json")
    lay_ids = []
    for name, (lo, pal, n) in NOTICE_TABLES.items():
        t = _load(ROOT / f"translation/{name}.json")
        f, plo, _, _, codec = build.JP_DISC1.packed[name]
        assert t["packed"] == name and (f, plo, codec) == ("BACKRAM.BIN", lo, "rle16")
        assert t["palette_file"] == "BACKRAM.BIN" and t["palette_offset"] == pal
        assert sorted([e["entry"] for e in t["entries"]] + [e["entry"] for e in t["excluded"]]) == list(range(n))
        for e in t["entries"]:
            assert e["ko"].count("\n") + 1 == sum(len(r["lines"]) for r in
                                                  next(s for s in layout["entries"] if s["id"] == e["id"])["regions"])
            assert set(e["terms"]) <= GLOSSARY and len(e["palette_sha1"]) == 40
        lay_ids += [e["id"] for e in t["entries"]]
    assert sorted(s["id"] for s in layout["entries"]) == sorted(lay_ids)


BACKRAM = ROOT / "work/disc1/fs/BACKRAM.BIN"


def _kept_pieces():
    """(table, id, region box, kept pixels {(x, y)}) for every region that leaves art left of its clean box"""
    from suchie2 import rle16, yc
    data = BACKRAM.read_bytes()
    layout = {s["id"]: s for s in _load(ROOT / "assets/boot/notices_layout.json")["entries"]}
    for name, (lo, _, _) in NOTICE_TABLES.items():
        ents = yc.parse(rle16.decompress(data[lo:])[0])
        for e in _load(ROOT / f"translation/{name}.json")["entries"]:
            src = ents[e["entry"]]
            px = lambda x, y: (src.data[(y * src.width + x) // 2] >> (4 if x % 2 == 0 else 0)) & 15  # noqa: E731
            cleans = [r["clean"]["box"] for r in layout[e["id"]]["regions"]]
            left = {(x, y) for y in range(src.height) for x in range(src.width)
                    if px(x, y) and not any(c[0] <= x < c[2] and c[1] <= y < c[3] for c in cleans)}
            for r in layout[e["id"]]["regions"]:
                b = r["box"]
                piece = {(x, y) for x, y in left if b[1] <= y < b[3]}
                if r["clean"]["box"][0] > 0 or piece:
                    yield name, e["id"], b, piece, px


@pytest.mark.skipif(not BACKRAM.exists(), reason="needs the extracted source file")
def test_only_whole_button_art_survives_the_notice_cleaning():
    pieces = {}
    for name, sid, b, piece, px in _kept_pieces():
        assert piece, f"{sid}: region {b} keeps art but none is there"
        x0, y0 = min(x for x, _ in piece), min(y for _, y in piece)
        pieces.setdefault(max(x for x, _ in piece), []).append(
            (sid, frozenset((x - x0, y - y0, px(x, y)) for x, y in piece)))
    for right, group in pieces.items():
        shapes = {s for _, s in group}
        assert len(shapes) == 1 or right < 110, f"art ending at x={right} differs between {[i for i, _ in group]}"
    assert len(pieces[150]) == 3 and len(pieces[82]) == 5      # ⒶⒷⒸ+START on three screens, (Ⓒ on five


@pytest.mark.parametrize("roll", build.ROLL_FILES)
def test_credit_roll_table_and_layout_agree(roll):
    t = _load(ROOT / f"translation/credits/{roll.split('.')[0]}.json")
    lay = _load(ROOT / f"assets/ending/{roll.split('.')[0]}.json")
    assert t["file"] == roll
    assert t["file"] in build.JP_DISC1.files and t["palette_file"] in build.JP_DISC1.read_only
    assert len(t["src_sha1"]) == t["chunks"][1] - t["chunks"][0] + 1
    ids = [e["id"] for e in t["entries"]]
    placed = [i for r in lay["regions"] for i in r["ids"]]
    assert sorted(placed) == sorted(ids) and len(set(ids)) == len(ids)
    assert not {e["id"] for e in t["excluded"]} & set(ids)
    by_id = {e["id"]: e for e in t["entries"]}
    for r in lay["regions"]:
        assert sum(by_id[i]["ko"].count("\n") + 1 for i in r["ids"]) == len(r["lines"])
    for e in t["entries"]:
        assert set(e["terms"]) <= GLOSSARY, e["id"]
