"""The catalog as data: every bundled file is valid, filed where it belongs, checked at a landmark that only its own
country's outlines claim; the generated files are current; old local files and settings are carried over."""
import json
from pathlib import Path

import pytest

from app.imagery import Imagery, covers, kinds, load_catalog
from app.imagery.catalog import CATALOG_DIR, prepare, served_places, unknown_keys, validate
from app.imagery.tools import schema_text, table_text

FILES = sorted(p for p in (CATALOG_DIR / "sources").rglob("*.json") if not p.name.startswith("_"))
WORLD = CATALOG_DIR / "context" / "world.json"
ANNAPOLIS = (-76.50, 38.97)


def test_the_bundled_catalog_loads_cleanly():
    cat = load_catalog()
    assert cat.problems == []
    assert len(cat.sources) == len(FILES) and all(s["origin"] == "bundled" for s in cat.sources.values())
    assert cat.world and cat.world["license"]["commercial"] is False      # the EOX layer: non-commercial, and says so


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_each_file_is_named_and_filed_by_its_id_and_country(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert path.stem == data["id"]
    assert path.parent.name == ("world" if data["country"] == "XX" else data["country"].lower())
    assert data["$schema"] == "../../schema.json"


def test_the_generated_files_are_current():
    """catalog/schema.json and catalog/SOURCES.md: `python -m app.imagery schema --write` / `table --write`."""
    assert (CATALOG_DIR / "schema.json").read_text(encoding="utf-8") == schema_text()
    assert (CATALOG_DIR / "SOURCES.md").read_text(encoding="utf-8") == table_text()


def test_the_schema_knows_every_kind_and_its_fields():
    schema = json.loads(schema_text())
    assert set(schema["properties"]["kind"]["enum"]) == set(kinds.REGISTRY)
    rules = {r["if"]["properties"]["kind"]["const"]: r["then"] for r in schema["allOf"]}
    for name, kind in kinds.REGISTRY.items():
        assert set(rules[name]["properties"]) - {"url"} == set(kind.fields)
        assert ("url" in rules[name]["properties"]) is bool(kind.url_pattern)
        assert set(rules[name].get("required", [])) == {n for n, f in kind.fields.items() if f.required}


@pytest.mark.parametrize("path", FILES + [WORLD], ids=lambda p: p.stem)
def test_every_bundled_file_matches_the_schema(path):
    jsonschema = pytest.importorskip("jsonschema")
    errors = list(jsonschema.Draft202012Validator(json.loads(schema_text())).iter_errors(
        json.loads(path.read_text(encoding="utf-8"))))
    assert [e.message for e in errors] == []


GOOD = {"id": "xx_example", "name": "Example", "country": "NL", "region": "Example", "kind": "tiles",
        "url": "https://example.test/{z}/{x}/{y}.jpg", "native_m": 0.1, "attribution": "Imagery: example",
        "license": {"id": "CC-BY-4.0", "name": "CC BY 4.0", "commercial": True, "credit": True},
        "coverage": {"ref": "nl"}, "verified": {"date": "2026-10-03", "place": "Dam", "landmark": [4.89, 52.37]}}
BAD = {
    "no kind": lambda e: e.pop("kind"),
    "unknown kind": lambda e: e.update(kind="quadkey"),
    "tiles url without {z}": lambda e: e.update(url="https://example.test/tile.jpg"),
    "wms without layers": lambda e: e.update(kind="wms", url="https://example.test/wms"),
    "id with capitals": lambda e: e.update(id="NL-Example"),
    "zero resolution": lambda e: e.update(native_m=0),
    "commercial as text": lambda e: e["license"].update(commercial="yes"),
    "point coverage": lambda e: e.update(coverage={"type": "Point", "coordinates": [4.9, 52.4]}),
    "header without {key}": lambda e: e.update(auth={"label": "a key", "header": {"name": "Authorization", "format": "Bearer"}}),
    "landmark off the map": lambda e: e["verified"].update(landmark=[200, 0]),
    "max_zoom as text": lambda e: e.update(max_zoom="19"),
    "a scheme nobody has": lambda e: e.update(scheme="quadkey"),
}


def test_the_good_example_is_good():
    assert validate(prepare(json.loads(json.dumps(GOOD)), [CATALOG_DIR / "coverage"])) == []


@pytest.mark.parametrize("case", sorted(BAD))
def test_the_runtime_validator_and_the_schema_refuse_the_same_mistakes(case):
    entry = json.loads(json.dumps(GOOD))
    BAD[case](entry)
    assert validate(prepare(dict(entry), [CATALOG_DIR / "coverage"])) != []
    jsonschema = pytest.importorskip("jsonschema")
    assert list(jsonschema.Draft202012Validator(json.loads(schema_text())).iter_errors(entry)) != []


def test_unknown_fields_are_flagged_but_not_fatal():
    entry = prepare({**GOOD, "max_zom": 19}, [CATALOG_DIR / "coverage"])
    assert validate(entry) == [] and unknown_keys(entry) == ["max_zom"]


# -- landmarks: each source checked at a public place only its own country's outlines claim ----------------------------
SOURCES = load_catalog().sources


@pytest.mark.parametrize("sid", sorted(SOURCES))
def test_each_landmark_is_inside_its_own_coverage_and_no_other_countrys(sid):
    src = SOURCES[sid]
    lon, lat = src["verified"]["landmark"]
    assert covers(src, lon, lat)
    if src["coverage"]["type"] == "World":
        return
    claims = [o["id"] for o in SOURCES.values() if o["coverage"]["type"] != "World" and o["country"] != src["country"]
              and covers(o, lon, lat)]
    assert claims == []
    if src["country"] != "US":
        assert not covers(src, *ANNAPOLIS)                    # never asked about a yard in Maryland


def test_each_source_serves_the_states_a_search_should_find_it_under():
    """Browse the catalog finds sources by state: a source serves the state its region names, a country-wide one
    the states inside its outline. Every US state has a source; USGS NAIP serves the lower 48, not Alaska or Hawaii."""
    places = served_places(list(SOURCES.values()))
    assert places["tx_austin_ortho"] == ["Texas"] and places["ia_isu_spring_2023_2025"] == ["Iowa"]
    assert places["de_bb_dop20"] == ["Brandenburg and Berlin"] and "esri_world" not in places
    naip = places["usgs_naip"]
    assert len(naip) == 49 and "District of Columbia" in naip and not {"Alaska", "Hawaii"} & set(naip)
    us = {p for sid, ps in places.items() if SOURCES[sid]["country"] == "US" for p in ps}
    assert len(us - {"District of Columbia"}) == 50


# -- carried over from the first catalog ----------------------------------------------------------------------------------
def test_a_first_catalog_local_file_becomes_local_source_files(tmp_path):
    (tmp_path / "imagery_catalog.json").write_text(json.dumps({
        "sources": [{"id": "county_ortho", "name": "County", "kind": "wmts", "url": "https://c.test/{z}/{x}/{y}.jpg",
                     "native_m": 0.15, "attribution": "x", "terms": "Free with credit.", "needs_key": "a county key",
                     "coverage_from": "md_imap_sixinch"}],
        "context": {"world": None}}), encoding="utf-8")
    imgry = Imagery(str(tmp_path))
    assert not (tmp_path / "imagery_catalog.json").exists() and (tmp_path / "imagery_catalog.json.migrated-v1").exists()
    src = imgry.source("county_ortho")
    assert src["kind"] == "tiles" and src["origin"] == "local"
    assert src["license"]["name"] == "Free with credit." and src["auth"] == {"label": "a county key"}
    assert covers(src, *ANNAPOLIS) and not covers(src, 2.35, 48.86)             # borrowed Maryland's outline
    assert imgry.world is None                                                # "world": null removed the layer


def test_the_first_panels_custom_source_setting_becomes_a_local_source(tmp_path):
    imgry = Imagery(str(tmp_path))
    imgry.adopt_custom({"kind": "arcgis_image", "url": "https://county.test/arcgis/rest/services/Ortho/ImageServer/",
                        "native_m": 0.15, "attribution": "Imagery: county", "name": "County 2024"})
    src = imgry.source("custom")
    assert src["url"].endswith("/ImageServer") and src["max_px"] == 2048 and src["coverage"]["type"] == "World"
    imgry.adopt_custom({"kind": "xyz", "url": "https://other.test/{z}/{x}/{y}.png"})       # an existing file is kept
    assert imgry.source("custom")["name"] == "County 2024"


def test_broken_local_files_are_reported_not_fatal(tmp_path):
    d = tmp_path / "imagery" / "sources"
    d.mkdir(parents=True)
    (d / "broken.json").write_text("{not json", encoding="utf-8")
    (d / "half.json").write_text(json.dumps({"id": "half", "name": "no kind", "url": "https://x.test", "native_m": 1,
                                             "attribution": "x", "coverage": {"type": "World"}}), encoding="utf-8")
    (d / "lost.json").write_text(json.dumps({**GOOD, "id": "lost", "coverage": {"ref": "atlantis"}}), encoding="utf-8")
    imgry = Imagery(str(tmp_path))
    text = " | ".join(imgry.problems)
    assert "broken.json" in text and "half.json" in text and "atlantis" in text
    assert len(imgry.sources) == len(FILES)


# -- from the review: bad local data never stops the panel ---------------------------------------------------------------
def test_malformed_local_data_is_reported_never_fatal(tmp_path):
    root = tmp_path / "imagery"
    (root / "sources").mkdir(parents=True)
    (root / "coverage").mkdir()
    (root / "sources" / "listkind.json").write_text(json.dumps({**GOOD, "id": "listkind", "kind": ["tiles"]}), encoding="utf-8")
    (root / "sources" / "odd_outline.json").write_text(json.dumps({**GOOD, "id": "odd_outline", "coverage": {"ref": "odd"}}), encoding="utf-8")
    (root / "coverage" / "odd.geojson").write_text("[1, 2, 3]", encoding="utf-8")
    (root / "context.json").write_text(json.dumps({"world": {"kind": {"a": 1}}}), encoding="utf-8")
    imgry = Imagery(str(tmp_path))
    imgry.adopt_custom({"kind": "xyz", "url": "https://c.test/{z}/{x}/{y}.png", "native_m": "fine"})
    text = " | ".join(imgry.problems)
    for expected in ("listkind.json", "odd_outline.json", "context.json", "custom source setting"):
        assert expected in text
    assert len(imgry.sources) == len(FILES) and imgry.world is None


def test_outlines_with_heights_still_work():
    three_d = {"type": "Polygon", "coordinates": [[[0, 0, 5], [2, 0, 5], [2, 2, 5], [0, 2, 5], [0, 0, 5]]]}
    assert covers({"coverage": three_d}, 1, 1) and not covers({"coverage": three_d}, 3, 1)


def test_a_first_catalog_entry_that_cannot_move_keeps_the_old_file(tmp_path):
    (tmp_path / "imagery_catalog.json").write_text(json.dumps({"sources": [
        {"id": "County-2024", "name": "County", "kind": "xyz", "url": "https://c.test/{z}/{x}/{y}.jpg", "native_m": 0.15,
         "attribution": "x", "coverage": {"type": "World"}},
        {"id": "good_one", "name": "Good", "kind": "xyz", "url": "https://g.test/{z}/{x}/{y}.jpg", "native_m": 0.15,
         "attribution": "x", "coverage": {"type": "World"}}]}), encoding="utf-8")
    imgry = Imagery(str(tmp_path))
    assert (tmp_path / "imagery_catalog.json").exists()                     # kept: one entry could not move
    assert "good_one" in {s["id"] for s in imgry.sources}
    assert any("County-2024" in p for p in imgry.problems) and any("kept until" in p for p in imgry.problems)


def test_a_local_world_layer_never_reaches_yard_zooms(tmp_path):
    (tmp_path / "imagery").mkdir()
    world = json.loads(WORLD.read_text(encoding="utf-8"))
    (tmp_path / "imagery" / "context.json").write_text(json.dumps({"world": {**world, "max_zoom": 20}}), encoding="utf-8")
    imgry = Imagery(str(tmp_path))
    assert imgry.world["max_zoom"] == 12 and any("capped" in p for p in imgry.problems)
    (tmp_path / "imagery" / "context.json").write_text(json.dumps({"world": {**world, "auth": {"label": "a key"}}}), encoding="utf-8")
    assert Imagery(str(tmp_path)).world is None
