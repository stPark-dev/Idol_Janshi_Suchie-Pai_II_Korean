import hashlib
import json

import pytest

from suchie2 import select1, yc

PAL = bytearray(0x40)          # CRAM image: bank 0x10 -> entries 0x10..0x1f at byte 0x20
PAL[0x22:0x24] = (0x7FFF).to_bytes(2, "big")   # idx 1 white
PAL[0x24:0x26] = (0x001F).to_bytes(2, "big")   # idx 2 red
PAL[0x26:0x28] = (0x7C00).to_bytes(2, "big")   # idx 3 blue (background)


def _file():
    plain = yc.Entry(32, 16, 0x0080, 0x10, bytes(256))
    band = yc.Entry(32, 16, 0x0080, 0x10, bytes([0x33]) * 256)
    return bytes(PAL) + yc.build([plain, band]), 0x40


def _tables(tmp_path, data, off, **over):
    ents = yc.parse(data[off:])
    pos = [off + 4 + 8 * len(ents)]
    for e in ents[:-1]:
        pos.append(pos[-1] + len(e.data))
    tr = {"file": "X.BIN", "bundle_offset": hex(off), "entries": [
        {"id": f"x.e{i}", "entry": i, "offset": hex(p), "size": [e.width, e.height], "colr": hex(e.colr),
         "src_sha1": hashlib.sha1(e.data).hexdigest(), "ja": "あ", "ko": "가", "state": "needs_review",
         "terms": [], "note": ""} for i, (e, p) in enumerate(zip(ents, pos))], "excluded": []}
    lay = {"entries": [
        {"id": "x.e0", "box": [0, 0, 32, 16], "allowed": [1, 2],
         "lines": [{"size": 12, "x": "center", "y": 1, "fill": 1, "outline": 2}]},
        {"id": "x.e1", "box": [0, 0, 32, 16], "clean": {"method": "rows", "text_idx": [1, 2]}, "allowed": [1, 2, 3],
         "lines": [{"size": 12, "x": "center", "y": 1, "fill": 1}]},
    ]}
    for k, v in over.items():
        tr[k] = v
    (tmp_path / "tr.json").write_text(json.dumps(tr, ensure_ascii=False))
    (tmp_path / "lay.json").write_text(json.dumps(lay, ensure_ascii=False))
    return tmp_path / "tr.json", tmp_path / "lay.json"


def test_renders_changed_entries_and_marks_non_distribution(tmp_path):
    data, off = _file()
    tr, lay = _tables(tmp_path, data, off)
    res = select1.render(data, tr, lay)
    assert set(res.textures) == {0, 1}
    assert res.distribution is False and res.states == {"needs_review": 2}
    e0 = res.textures[0]
    assert any(e0) and len(e0) == 256
    assert set(res.textures[1]) >= {0x33}            # background band kept around the glyph


def test_rejects_source_hash_mismatch(tmp_path):
    data, off = _file()
    tr, lay = _tables(tmp_path, data, off)
    t = json.loads(tr.read_text())
    t["entries"][0]["src_sha1"] = "0" * 40
    tr.write_text(json.dumps(t))
    with pytest.raises(select1.Select1Error, match="source"):
        select1.render(data, tr, lay)


def test_rejects_line_count_mismatch(tmp_path):
    data, off = _file()
    tr, lay = _tables(tmp_path, data, off)
    t = json.loads(tr.read_text())
    t["entries"][0]["ko"] = "가\n나"
    tr.write_text(json.dumps(t, ensure_ascii=False))
    with pytest.raises(select1.Select1Error, match="lines"):
        select1.render(data, tr, lay)


def test_rejects_layout_for_unknown_id(tmp_path):
    data, off = _file()
    tr, lay = _tables(tmp_path, data, off)
    lj = json.loads(lay.read_text())
    lj["entries"].append({"id": "x.e9", "box": [0, 0, 1, 1], "allowed": [1], "lines": []})
    lay.write_text(json.dumps(lj))
    with pytest.raises(select1.Select1Error, match="unknown"):
        select1.render(data, tr, lay)


def test_text_extending_past_the_box_is_rejected(tmp_path):
    data, off = _file()
    tr, lay = _tables(tmp_path, data, off)
    lj = json.loads(lay.read_text())
    lj["entries"][1]["box"] = [0, 0, 32, 8]
    lj["entries"][1]["lines"][0]["y"] = 1                  # glyph extends below row 8
    lay.write_text(json.dumps(lj))
    with pytest.raises(select1.Select1Error, match="does not fit"):
        select1.render(data, tr, lay)


def test_changed_pixels_stay_in_box_and_use_allowed_indices(tmp_path):
    data, off = _file()
    tr, lay = _tables(tmp_path, data, off)
    lj = json.loads(lay.read_text())
    lj["entries"][1]["box"] = [0, 0, 32, 14]
    lj["entries"][1]["lines"][0].update(size=10, y=1)
    lay.write_text(json.dumps(lj))
    tex = select1.render(data, tr, lay).textures[1]
    px = lambda d, x, y: (d[(y * 32 + x) // 2] >> 4) if x % 2 == 0 else d[(y * 32 + x) // 2] & 15  # noqa: E731
    changed = [(x, y, px(tex, x, y)) for y in range(16) for x in range(32) if px(tex, x, y) != 3]
    assert changed and all(y < 14 and v in (1, 2) for x, y, v in changed)


def test_mixed_states_are_not_distributable(tmp_path):
    data, off = _file()
    tr, lay = _mutate(tmp_path, data, off, lambda t: t["entries"][0].update(state="distribution_eligible"))
    res = select1.render(data, tr, lay)
    assert res.distribution is False and res.states == {"distribution_eligible": 1, "needs_review": 1}


def test_distribution_true_only_when_all_selected_are_eligible(tmp_path):
    data, off = _file()
    tr, lay = _tables(tmp_path, data, off)
    t = json.loads(tr.read_text())
    for e in t["entries"]:
        e["state"] = "distribution_eligible"
    tr.write_text(json.dumps(t, ensure_ascii=False))
    assert select1.render(data, tr, lay).distribution is True


def test_regions_consume_translated_lines_in_order(tmp_path):
    data, off = _file()
    tr, lay = _tables(tmp_path, data, off)
    t = json.loads(tr.read_text())
    t["entries"][1]["ko"] = "가\n나"
    tr.write_text(json.dumps(t, ensure_ascii=False))
    lj = json.loads(lay.read_text())
    lj["entries"][1] = {"id": "x.e1", "regions": [
        {"box": [0, 0, 16, 16], "clean": {"method": "rows", "text_idx": [1, 2]}, "allowed": [1, 2, 3],
         "lines": [{"size": 12, "x": "center", "y": "center", "area": [0, 0, 16, 16], "fill": 1}]},
        {"box": [16, 0, 32, 16], "clean": {"method": "rows", "text_idx": [1, 2]}, "allowed": [1, 2, 3],
         "lines": [{"size": 12, "x": "center", "y": "center", "area": [16, 0, 32, 16], "fill": 2}]}]}
    lay.write_text(json.dumps(lj))
    tex = select1.render(data, tr, lay).textures[1]
    left = {tex[(y * 32 + x) // 2] >> 4 for y in range(16) for x in range(0, 16, 2)}
    right = {tex[(y * 32 + x) // 2] >> 4 for y in range(16) for x in range(16, 32, 2)}
    assert 1 in left and 2 not in left and 2 in right and 1 not in right


def _mutate(tmp_path, data, off, tr_fn=None, lay_fn=None):
    tr, lay = _tables(tmp_path, data, off)
    if tr_fn:
        t = json.loads(tr.read_text()); tr_fn(t); tr.write_text(json.dumps(t, ensure_ascii=False))  # noqa: E702
    if lay_fn:
        lj = json.loads(lay.read_text()); lay_fn(lj); lay.write_text(json.dumps(lj))  # noqa: E702
    return tr, lay


@pytest.mark.parametrize("tr_fn,lay_fn,msg", [
    (lambda t: t["entries"].append(dict(t["entries"][0])), None, "duplicate"),
    (lambda t: t["entries"][1].update(entry=0), None, "duplicate"),
    (lambda t: t["excluded"].append({"id": "x.e0", "entry": 0, "reason": "x"}), None, "excluded"),
    (None, lambda lj: lj["entries"].pop(), "no layout"),
    (None, lambda lj: lj["entries"].append(dict(lj["entries"][0])), "duplicate"),
    (lambda t: t["entries"][0].update(entry=9), None, "range"),
    (lambda t: t["entries"][0].update(colr="0x20"), None, "colr"),
    (lambda t: t["entries"][0].update(size=[16, 16]), None, "size"),
    (lambda t: t["entries"][0].update(offset="0x10"), None, "offset"),
    (lambda t: t["entries"][0].update(state="done"), None, "state"),
    (None, lambda lj: lj["entries"][1].update(clean={"method": "rows", "text_idx": [1], "box": [0, 0, 40, 16]}), "box"),
    (None, lambda lj: lj["entries"][0].update(box=[0, 0, 33, 16]), "box"),
    (None, lambda lj: lj["entries"][0].update(allowed=[0, 1]), "allowed"),
    (None, lambda lj: lj["entries"].__setitem__(1, {"id": "x.e1", "regions": [
        {"box": [0, 0, 20, 16], "allowed": [1], "lines": [{"size": 8, "x": 1, "y": 1, "fill": 1}]},
        {"box": [10, 0, 32, 16], "allowed": [1], "lines": []}]}), "overlap"),
])
def test_rejects_inconsistent_tables(tmp_path, tr_fn, lay_fn, msg):
    data, off = _file()
    tr, lay = _mutate(tmp_path, data, off, tr_fn, lay_fn)
    with pytest.raises(select1.Select1Error, match=msg):
        select1.render(data, tr, lay)


def test_rejects_lut_colour_mode(tmp_path):
    data, off = _file()
    raw = bytearray(data)
    raw[off + 4 + 4:off + 4 + 6] = (0x0080 | (1 << 3)).to_bytes(2, "big")   # entry 0 -> mode 1 (LUT)
    tr, lay = _tables(tmp_path, bytes(raw), off)
    with pytest.raises(select1.Select1Error, match="mode"):
        select1.render(bytes(raw), tr, lay)


def test_eligible_entry_needs_approved_terms(tmp_path):
    data, off = _file()
    gl = tmp_path / "glossary.json"
    gl.write_text(json.dumps({"terms": [{"id": "t.a", "state": "proposed"}, {"id": "t.b", "state": "approved"}]}))

    def elig(t):
        for e in t["entries"]:
            e["state"] = "distribution_eligible"
        t["entries"][0]["terms"] = ["t.a"]
    tr, lay = _mutate(tmp_path, data, off, elig)
    with pytest.raises(select1.Select1Error, match="not approved"):
        select1.render(data, tr, lay, glossary=gl)
    t = json.loads(tr.read_text())
    t["entries"][0]["terms"] = ["t.b"]
    tr.write_text(json.dumps(t, ensure_ascii=False))
    assert select1.render(data, tr, lay, glossary=gl).distribution is True
    t["entries"][0]["terms"] = ["t.zz"]
    tr.write_text(json.dumps(t, ensure_ascii=False))
    with pytest.raises(select1.Select1Error, match="unknown term"):
        select1.render(data, tr, lay, glossary=gl)


def test_per_file_offsets_select_the_current_file(tmp_path):
    data, off = _file()
    real = select1.offsets(data, off)

    def per_file(t):
        for e in t["entries"]:
            e["offset"] = {"A.BIN": hex(real[e["entry"]]), "B.BIN": "0x0"}
    tr, lay = _mutate(tmp_path, data, off, per_file)
    assert select1.render(data, tr, lay, file_name="A.BIN").textures
    with pytest.raises(select1.Select1Error, match="offset"):
        select1.render(data, tr, lay, file_name="B.BIN")
    with pytest.raises(select1.Select1Error, match="offset"):
        select1.render(data, tr, lay, file_name="C.BIN")


def test_every_bundle_entry_must_be_classified(tmp_path):
    data, off = _file()
    tr, lay = _mutate(tmp_path, data, off, lambda t: t["entries"].pop(),
                      lambda lj: lj["entries"].pop())
    with pytest.raises(select1.Select1Error, match="unclassified"):
        select1.render(data, tr, lay)


@pytest.mark.parametrize("tr_fn,msg", [
    (lambda t: t["excluded"].append({"id": "x.z", "entry": 99, "reason": "x"}), "range"),
    (lambda t: t["excluded"].extend([{"id": "x.z1", "entry": 5, "reason": "x"}, {"id": "x.z2", "entry": 5, "reason": "x"}]), "duplicate"),
])
def test_rejects_bad_excluded_entries(tmp_path, tr_fn, msg):
    data, off = _file()
    tr, lay = _mutate(tmp_path, data, off, tr_fn)
    with pytest.raises(select1.Select1Error, match=msg):
        select1.render(data, tr, lay)


def test_layout_templates_are_merged_and_entries_override(tmp_path):
    data, off = _file()
    tr, lay = _tables(tmp_path, data, off)
    lj = json.loads(lay.read_text())
    lj["templates"] = {"band": {k: v for k, v in lj["entries"][1].items() if k != "id"}}
    lj["entries"][1] = {"id": "x.e1", "use": "band"}
    lay.write_text(json.dumps(lj))
    a = select1.render(data, tr, lay).textures[1]
    lj["entries"][1] = {"id": "x.e1", "use": "band", "allowed": [2, 3]}
    lay.write_text(json.dumps(lj))
    b = select1.render(data, tr, lay).textures[1]
    px = lambda d: {(v >> 4) for v in d} | {(v & 15) for v in d}  # noqa: E731
    assert 1 in px(a) and 1 not in px(b)
    lj["entries"][1] = {"id": "x.e1", "use": "nope"}
    lay.write_text(json.dumps(lj))
    with pytest.raises(select1.Select1Error, match="template"):
        select1.render(data, tr, lay)


def test_palette_offset_reads_cram_image_elsewhere(tmp_path):
    data, off = _file()
    moved = bytes(0x40) + data[:0x40] + data[0x40:]          # CRAM image now at 0x40, bundle at 0x80
    tr, lay = _tables(tmp_path, moved[0x40:], off)             # tables built from the same bundle bytes
    t = json.loads(tr.read_text())
    t["bundle_offset"] = hex(off + 0x40)
    t["palette_offset"] = "0x40"
    for e in t["entries"]:
        e["offset"] = hex(int(e["offset"], 16) + 0x40)
    tr.write_text(json.dumps(t, ensure_ascii=False))
    assert select1.render(moved, tr, lay).textures == select1.render(data, *_tables(tmp_path, data, off)).textures


def test_id_prefix_lets_several_tables_share_one_layout(tmp_path):
    data, off = _file()
    tr, lay = _tables(tmp_path, data, off)
    lj = json.loads(lay.read_text())
    lj["entries"].append({"id": "other.e0", "box": [0, 0, 1, 1], "allowed": [1], "lines": []})
    lay.write_text(json.dumps(lj))
    with pytest.raises(select1.Select1Error, match="unknown"):
        select1.render(data, tr, lay)
    t = json.loads(tr.read_text())
    t["id_prefix"] = "x."
    tr.write_text(json.dumps(t, ensure_ascii=False))
    assert set(select1.render(data, tr, lay).textures) == {0, 1}
    lj["entries"].pop(0)                                   # an x.* layout missing is still an error
    lay.write_text(json.dumps(lj))
    with pytest.raises(select1.Select1Error, match="no layout"):
        select1.render(data, tr, lay)


def test_line_overrides_change_one_line_of_a_template(tmp_path):
    data, off = _file()
    tr, lay = _tables(tmp_path, data, off)
    lj = json.loads(lay.read_text())
    lj["templates"] = {"band": {k: v for k, v in lj["entries"][1].items() if k != "id"}}
    lj["entries"][1] = {"id": "x.e1", "use": "band", "line_overrides": {"0": {"size": 20}}}
    lay.write_text(json.dumps(lj))
    with pytest.raises(select1.Select1Error, match="does not fit"):   # 20px cannot fit a 16px texture
        select1.render(data, tr, lay)
    lj["entries"][1]["line_overrides"] = {"5": {"size": 8}}
    lay.write_text(json.dumps(lj))
    with pytest.raises(select1.Select1Error, match="line_overrides"):
        select1.render(data, tr, lay)


@pytest.mark.parametrize("off,msg", [("0x100000", "palette_offset"), ("-0x10", "palette_offset")])
def test_palette_offset_must_hold_a_cram_image(tmp_path, off, msg):
    data, o = _file()
    tr, lay = _mutate(tmp_path, data, o, lambda t: t.update(palette_offset=off))
    with pytest.raises(select1.Select1Error, match=msg):
        select1.render(data, tr, lay)


def test_palette_sha1_is_a_protected_field(tmp_path):
    data, o = _file()
    good = hashlib.sha1(select1.palette_bytes(data, 0x10, 0)).hexdigest()
    tr, lay = _mutate(tmp_path, data, o, lambda t: [e.update(palette_sha1=good) for e in t["entries"]])
    assert select1.render(data, tr, lay).textures
    tr, lay = _mutate(tmp_path, data, o, lambda t: [e.update(palette_sha1="0" * 40) for e in t["entries"]])
    with pytest.raises(select1.Select1Error, match="palette"):
        select1.render(data, tr, lay)


@pytest.mark.parametrize("entry,msg", [
    ({"id": "x.e1", "use": "reg", "line_overrides": {"0": {"size": 8}}}, "line_overrides"),
    ({"id": "x.e1", "use": "reg", "lines": [{"size": 8, "x": 0, "y": 0, "fill": 1}]}, "regions"),
    ({"id": "x.e1", "use": "band", "line_overrides": {"a": {}}}, "line_overrides"),
    ({"id": "x.e1", "use": "band", "line_overrides": {"00": {}}}, "line_overrides"),
])
def test_template_merge_rejects_ambiguous_specs(tmp_path, entry, msg):
    data, off = _file()
    tr, lay = _tables(tmp_path, data, off)
    lj = json.loads(lay.read_text())
    band = {k: v for k, v in lj["entries"][1].items() if k != "id"}
    lj["templates"] = {"band": band, "reg": {"regions": [band]}}
    lj["entries"][1] = entry
    lay.write_text(json.dumps(lj))
    with pytest.raises(select1.Select1Error, match=msg):
        select1.render(data, tr, lay)


def test_id_prefix_must_end_with_a_dot(tmp_path):
    data, off = _file()
    tr, lay = _mutate(tmp_path, data, off, lambda t: t.update(id_prefix="x"))
    with pytest.raises(select1.Select1Error, match="id_prefix"):
        select1.render(data, tr, lay)


def test_palette_data_reads_banks_from_another_file(tmp_path):
    data, off = _file()
    tr, lay = _tables(tmp_path, data, off)
    want = select1.render(data, tr, lay).textures
    blank = bytes(0x40) + data[0x40:]                       # same bundle, palette bytes zeroed
    with pytest.raises(select1.Select1Error, match="palette"):
        select1.render(blank, tr, lay, palette_data=bytes(0x10))   # bank 0x10 outside a 16-byte image
    assert select1.render(blank, tr, lay, palette_data=bytes(PAL)).textures == want


def test_empty_ko_with_clear_gives_transparent_texture(tmp_path):
    data, off = _file()
    tr, lay = _tables(tmp_path, data, off)
    t = json.loads(tr.read_text())
    t["entries"][1]["ko"] = ""
    tr.write_text(json.dumps(t, ensure_ascii=False))
    lj = json.loads(lay.read_text())
    lj["entries"][1]["clean"] = {"method": "clear"}
    lay.write_text(json.dumps(lj))
    res = select1.render(data, tr, lay)
    assert 1 in res.textures and "x.e1" in res.ids          # written, not skipped
    assert set(res.textures[1]) == {0}
    assert res.states == {"needs_review": 2}
