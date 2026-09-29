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
              *sorted((ROOT / "translation/cards").glob("*.json"))]:
        t = _load(p)
        for e in t["entries"]:
            assert len(e.get("palette_sha1", "")) == 40, (p.name, e["id"])
            assert set(e["terms"]) <= GLOSSARY, (p.name, e["id"])
