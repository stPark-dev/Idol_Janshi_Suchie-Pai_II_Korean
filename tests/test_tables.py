"""Disc-free consistency checks of the committed translation tables and layouts."""
import json
from pathlib import Path

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
              ROOT / "translation/opening.json", *sorted((ROOT / "translation/cards").glob("*.json"))]:
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
