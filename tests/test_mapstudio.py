import copy
import math

import pytest

from app import geo, mapstudio as ms

REF = (39.0, -76.5)
R = {"latitude": REF[0], "longitude": REF[1]}


def pts(*xy, phi=0):
    return [{"x": x, "y": y, "phi": phi} for x, y in xy]


def sample_map():
    return {
        "areas": [{"id": 1, "name": "front", "ref": R, "range": pts((0, 0), (20, 0), (20, 20), (0, 20), phi=0.5)},
                  {"id": 11, "name": "back", "ref": R, "range": pts((30, 0), (40, 0), (40, 10), (30, 10))}],
        "nogozones": [{"id": 45, "name": "bed", "ref": R, "enable": True, "area_id": -1, "type": 0,
                       "trimming_edges": [], "range": pts((5, 5), (8, 5), (8, 8), (5, 8))}],
        "novisionzones": [],
        "pathways": [{"id": 52, "name": "", "ref": R, "start_id": 1, "end_id": 11, "range": pts((20, 5), (30, 5))}],
        "sidewalks": [], "deadends": [], "elec_fence": [],
        "allchargingData": [{"id": 2, "chargingPoint": {"x": 1, "y": 1}, "ref": R}],
    }


def lonlat(x, y):
    return geo.to_lonlat(REF, x, y)


def test_summary_metrics():
    rows = {(r["kind"], r["id"]): r for r in ms.summary(sample_map(), REF)}
    assert rows[("areas", 1)]["area_m2"] == pytest.approx(400, abs=0.1)
    assert rows[("areas", 1)]["length_m"] == pytest.approx(80, abs=0.1)
    assert rows[("pathways", 52)]["length_m"] == pytest.approx(10, abs=0.01)
    assert rows[("nogozones", 45)]["points"] == 4


def test_diff_finds_moves_adds_and_removals():
    a = sample_map()
    b = copy.deepcopy(a)
    b["areas"][0]["range"][2]["x"] = 22                      # one corner 2 m further west
    b["nogozones"] = []
    b["novisionzones"] = [{"id": 99, "name": "porch", "ref": R, "enable": True, "range": pts((1, 1), (2, 1), (2, 2))}]
    d = ms.diff(a, b, REF)
    assert [r["id"] for r in d["added"]] == [99]
    assert [r["id"] for r in d["removed"]] == [45]
    (ch,) = d["changed"]
    assert ch["id"] == 1 and ch["max_move_m"] == pytest.approx(2, abs=0.01) and ch["area_delta_m2"] > 0
    assert d["unchanged"] == 2
    overlay = ms.diff_geojson(a, b, REF, d)
    assert sorted(f["properties"]["status"] for f in overlay["features"]) == ["added", "new", "old", "removed"]


def test_identical_maps_have_no_diff():
    d = ms.diff(sample_map(), sample_map(), REF)
    assert d["added"] == d["removed"] == d["changed"] == []


def test_validation_rules():
    m = sample_map()
    assert [i for i in ms.validate(m, REF) if i["level"] == "error"] == []
    m["nogozones"].append({"id": 46, "name": "bowtie", "ref": R, "enable": True,
                           "range": pts((2, 2), (4, 4), (4, 2), (2, 4))})
    m["nogozones"].append({"id": 47, "name": "outside", "ref": R, "enable": True,
                           "range": pts((50, 50), (52, 50), (52, 52))})
    m["areas"].append({"id": 12, "name": "huge", "ref": R, "range": pts(*[(100 + 10 * math.cos(i / 300 * 2 * math.pi), 100 + 10 * math.sin(i / 300 * 2 * math.pi)) for i in range(300)])})
    m["pathways"][0]["end_id"] = 777
    m["sidewalks"].append({"id": 45, "name": "dup", "ref": R, "range": pts((0, 0), (1, 1))})
    msgs = {(i["id"], i["level"]): i["msg"] for i in ms.validate(m, REF)}
    assert "crosses itself" in msgs[(46, "error")]
    assert "outside every area" in msgs[(47, "warning")]
    assert f"accepted at most {ms.limit('areas')}" in msgs[(12, "error")]
    assert "end_id 777" in msgs[(52, "warning")]
    assert "used 2 times" in msgs[(45, "error")]


def test_add_reshape_rename_delete():
    m = sample_map()
    m2, desc = ms.apply_edit(m, {"op": "add", "kind": "nogozones", "name": "tree",
                                 "coords": [lonlat(10, 10), lonlat(12, 10), lonlat(12, 12), lonlat(10, 12)]}, REF)
    new = m2["nogozones"][-1]
    assert new["id"] == 53 and new["name"] == "tree" and new["area_id"] == -1 and "added" in desc
    assert new["range"][1]["x"] == pytest.approx(12, abs=1e-3) and len(new["range"]) == 4
    assert len(m["nogozones"]) == 1  # the original is untouched

    ring = [lonlat(p["x"], p["y"]) for p in m["areas"][0]["range"]]
    ring[2] = lonlat(25, 20)
    m3, _ = ms.apply_edit(m, {"op": "shape", "kind": "areas", "id": 1, "coords": ring + [ring[0]]}, REF)
    r = m3["areas"][0]["range"]
    assert len(r) == 4 and r[2]["x"] == pytest.approx(25, abs=1e-3) and r[2]["phi"] == 0
    assert r[0] == m["areas"][0]["range"][0]  # untouched corners keep their exact values and phi

    m4, _ = ms.apply_edit(m, {"op": "rename", "kind": "nogozones", "id": 45, "name": "rose bed"}, REF)
    assert m4["nogozones"][0]["name"] == "rose bed"
    m5, _ = ms.apply_edit(m, {"op": "enable", "kind": "nogozones", "id": 45, "enable": False}, REF)
    assert m5["nogozones"][0]["enable"] is False
    m6, _ = ms.apply_edit(m, {"op": "delete", "kind": "nogozones", "id": 45}, REF)
    assert m6["nogozones"] == []


@pytest.mark.parametrize("edit", [
    {"op": "add", "kind": "sidewalks", "coords": []},
    {"op": "add", "kind": "areas", "coords": []},
    {"op": "add", "kind": "nogozones", "coords": [[0, 0], [1, 1]]},
    {"op": "shape", "kind": "areas", "id": 999, "coords": []},
    {"op": "rename", "kind": "areas", "id": 1, "name": "  "},
    {"op": "enable", "kind": "areas", "id": 1, "enable": False},
    {"op": "explode", "kind": "areas", "id": 1},
])
def test_bad_edits_are_refused(edit):
    with pytest.raises(ms.EditError):
        ms.apply_edit(sample_map(), edit, REF)


def test_kml_export():
    kml = ms.to_kml(ms.geojson(sample_map(), REF), "Yard & co")
    assert kml.count("<Placemark>") == 5 and "Yard &amp; co" in kml and "<Polygon>" in kml and "<LineString>" in kml


def test_backup_reply_decoding():
    import base64
    import json
    import zlib

    from app.codec import decode_any, locate_map
    m = sample_map()
    packed = base64.b64encode(zlib.compress(json.dumps(m).encode())).decode()
    assert decode_any(packed) == m
    assert locate_map({"state": 0, "data": packed}) == m
    assert locate_map({"data": {"map": json.dumps(m)}}) == m
    assert locate_map("not a map") is None


def test_captures_skip_the_same_message_from_the_other_broker(tmp_path):
    from app.store import Store
    s = Store(str(tmp_path))
    assert s.add_capture("save_clean_area", 100.0, '{"a": 1}')
    assert not s.add_capture("save_clean_area", 101.0, '{"a": 1}')
    assert s.add_capture("save_clean_area", 101.0, '{"a": 2}')
    assert s.add_capture("save_clean_area", 200.0, '{"a": 1}')
    assert [c["name"] for c in s.captures()] == ["save_clean_area"] * 3
    assert s.capture(s.captures()[-1]["id"])["payload"] == {"a": 1}


def test_backup_with_singular_keys_is_normalised():
    from app.codec import locate_map
    m = sample_map()
    backup = {"area": m["areas"], "nogozone": m["nogozones"], "novisionzone": [], "pathway": m["pathways"],
              "sidewalk": [], "deadend": [], "elec_fence": [], "chargingPoints": m["allchargingData"],
              "id": 4322, "name": "Auto Saved", "timestamp": 1790718749, "is_auto_backup": True}
    got = locate_map(backup)
    assert got["areas"] == m["areas"] and got["pathways"] == m["pathways"] and got["allchargingData"]
    assert ms.diff(m, got, REF)["added"] == ms.diff(m, got, REF)["removed"] == []


def test_problems_the_robot_already_has_do_not_block_a_draft():
    base = sample_map()
    base["areas"][1]["range"] = pts((30, 0), (40, 0), (30, 10), (40, 10))      # the robot's own bow-tie
    draft, _ = ms.apply_edit(base, {"op": "rename", "kind": "areas", "id": 11, "name": "back lawn"}, REF)
    (i,) = [i for i in ms.validate_draft(base, draft, REF) if i["id"] == 11 and i["code"] == "crossing"]
    assert i["level"] == "existing"
    worse = copy.deepcopy(draft)
    worse["nogozones"][0]["range"] = pts((5, 5), (8, 8), (8, 5), (5, 8))        # a new crossing the draft made
    assert any(i["id"] == 45 and i["level"] == "error" for i in ms.validate_draft(base, worse, REF))


def test_new_ids_skip_ids_the_robot_used_before():
    m2, _ = ms.apply_edit(sample_map(), {"op": "add", "kind": "nogozones",
                                         "coords": [lonlat(1, 1), lonlat(3, 1), lonlat(3, 3)]}, REF, id_floor=767)
    assert m2["nogozones"][-1]["id"] == 768


def test_add_area_and_pathway_copy_the_robots_structure():
    m = sample_map()
    m["areas"][0].update(algorithm_type=2, mapping_module_type=1, slope_degrees=4.0, labels=[3], snowPiles=[{"x": 1}])
    m2, _ = ms.apply_edit(m, {"op": "add", "kind": "areas", "coords": [lonlat(50, 0), lonlat(60, 0), lonlat(60, 10), lonlat(50, 10)]}, REF)
    a = m2["areas"][-1]
    assert a["algorithm_type"] == 2 and a["mapping_module_type"] == 1 and a["labels"] == [] and a["snowPiles"] == []
    assert a["slope_degrees"] == 9999 and a["area"] == pytest.approx(100, abs=0.5) and a["name"] == "Area 3"
    # a path from inside "front" to just outside "back" (within 3 m) gets both ends
    m3, _ = ms.apply_edit(m2, {"op": "add", "kind": "pathways", "coords": [lonlat(10, 10), lonlat(28, 5)]}, REF)
    pw = m3["pathways"][-1]
    assert (pw["start_id"], pw["end_id"]) == (1, 11) and pw["connectids"] == []
    m4, _ = ms.apply_edit(m2, {"op": "add", "kind": "pathways", "coords": [lonlat(10, 10), lonlat(100, 100)]}, REF)
    assert m4["pathways"][-1]["end_id"] == -1


def test_simplify_brings_a_shape_under_the_limit():
    import math
    ring = [(100 + 10 * math.cos(i / 300 * 2 * math.pi), 100 + 10 * math.sin(i / 300 * 2 * math.pi)) for i in range(300)]
    m = sample_map()
    m["areas"].append({"id": 12, "name": "round", "ref": R, "range": pts(*ring)})
    m2, desc = ms.apply_edit(m, {"op": "simplify", "kind": "areas", "id": 12}, REF)
    assert len(m2["areas"][-1]["range"]) == ms.limit("areas") and f"300 to {ms.limit('areas')}" in desc
    assert not [i for i in ms.validate(m2, REF) if i["id"] == 12 and i["level"] == "error"]
    with pytest.raises(ms.EditError):
        ms.apply_edit(m2, {"op": "simplify", "kind": "areas", "id": 12}, REF)


def test_draft_edits_from_a_stale_window_are_refused():
    from fastapi import HTTPException
    from app.main import _check_base
    d = {"updated": 100.5}
    _check_base(d, 100.5, True)          # up to date
    _check_base(d, None, False)          # a script that does not send a base
    _check_base(None, None, True)        # nobody has a draft; the first edit may start one
    for draft, base in ((d, 99.0), (d, None), (None, 100.5)):
        with pytest.raises(HTTPException) as exc:
            _check_base(draft, base, True)
        assert exc.value.status_code == 409


def test_the_edit_endpoint_refuses_a_stale_window(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace

    from fastapi import HTTPException
    from app import main
    from app.store import Store
    store = Store(str(tmp_path))
    store.set_setting("map_draft", {"from": "current", "from_label": "x", "base_sha": None, "created": 1.0, "updated": 5.0,
                                    "map": sample_map(), "id_floor": 0, "log": ["started"]})
    rec = SimpleNamespace(store=store, gps_ref=REF, map_raw=sample_map(), cfd=None, t={}, snapshot_at=None, run_id=None)
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(rec=rec)))
    monkeypatch.setattr(main, "_draft_view", lambda request: {"ok": True})
    edit = {"op": "rename", "kind": "nogozones", "id": 45, "name": "x"}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.studio_draft_edit(req, {**edit, "base": None}))      # this window never saw the draft
    assert exc.value.status_code == 409
    assert asyncio.run(main.studio_draft_edit(req, {**edit, "base": 5.0})) == {"ok": True}


def test_revert_undoes_one_feature():
    base = sample_map()
    d, _ = ms.apply_edit(base, {"op": "rename", "kind": "areas", "id": 1, "name": "front lawn"}, REF)
    d, _ = ms.apply_edit(d, {"op": "delete", "kind": "nogozones", "id": 45}, REF)
    d, _ = ms.apply_edit(d, {"op": "add", "kind": "nogozones", "coords": [lonlat(1, 1), lonlat(3, 1), lonlat(3, 3)]}, REF)
    new_id = d["nogozones"][-1]["id"]
    d, _ = ms.revert(d, base, "areas", 1)
    d, _ = ms.revert(d, base, "nogozones", 45)
    d, _ = ms.revert(d, base, "nogozones", new_id)
    assert ms.diff(base, d, REF) == {"added": [], "removed": [], "changed": [], "unchanged": 4}


def test_a_map_put_back_as_it_was_becomes_current_again(tmp_path):
    import time as _t
    from app.store import Store
    st = Store(str(tmp_path))
    a, b = sample_map(), sample_map()
    b["nogozones"] = []
    assert st.save_version("map", a)
    _t.sleep(0.01)
    assert st.save_version("map", b)
    _t.sleep(0.01)
    assert st.save_version("map", a)            # deleted the test zone again: back to a
    assert st.latest_version("map") == a
    assert not st.save_version("map", a)        # unchanged now
    assert len(st.versions("map")) == 2 and st.versions("map")[0]["sha"] != st.versions("map")[1]["sha"]
