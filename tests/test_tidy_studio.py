"""Tidy in Map Studio: the draft edits, what a tidied shape means for sending, and the read-only endpoints."""
import asyncio
import math
from types import SimpleNamespace

import pytest

from app import main, mapstudio as ms, mapwrite as mw, tidy
from app.store import Store
from tests.test_mapstudio import REF, R, pts, sample_map


def wobbly_tree_ring(fid=45, name="tree"):
    ring = [(6.5 + (1.5 + 0.03 * (-1) ** i) * math.cos(2 * math.pi * i / 14),
             6.5 + (1.5 + 0.03 * (-1) ** i) * math.sin(2 * math.pi * i / 14)) for i in range(14)]
    return {"id": fid, "name": name, "ref": R, "enable": True, "area_id": -1, "type": 0, "trimming_edges": [],
            "range": pts(*ring)}


def a_map():
    m = sample_map()
    m["nogozones"] = [wobbly_tree_ring()]
    wobble = [(0, 0)] + [(i, 0.03 * (-1) ** i) for i in range(1, 20)] + [(20, 0), (20, 20), (0, 20)]
    m["areas"][0]["range"] = pts(*wobble)
    return m


def test_tidy_edit_rounds_a_zone_and_the_send_plan_treats_it_as_a_change():
    base = a_map()
    draft, desc = ms.apply_edit(base, {"op": "tidy", "kind": "nogozones", "id": 45, "tool": "round", "tol": 0.10}, REF)
    assert desc.startswith("round “tree”: 14 →") and "moved at most" in desc
    ring = ms.own_points(ms.find(draft, "nogozones", 45))
    assert tidy.fit_circle(ring)[2] < 0.02
    assert all(set(p) == {"x", "y", "phi"} for p in ms.find(draft, "nogozones", 45)["range"])
    plan = mw.plan(base, draft, REF)
    assert [s["op"] for s in plan["steps"]] == ["add", "delete"] and not plan["blocked"]


def test_a_tidied_area_is_sent_in_place():
    base = a_map()
    draft, desc = ms.apply_edit(base, {"op": "tidy", "kind": "areas", "id": 1, "tool": "straighten", "tol": 0.10}, REF)
    assert "straighten “front”" in desc and ms.own_points(ms.find(draft, "areas", 1)) != ms.own_points(ms.find(base, "areas", 1))
    plan = mw.plan(base, draft, REF)
    assert [(s["op"], s["cmd"], s["robot_id"]) for s in plan["steps"]] == [("update", "save_clean_area", 1)] and not plan["blocked"]


def test_tidy_refusals_are_edit_errors():
    base = a_map()
    with pytest.raises(ms.EditError, match="nothing to change"):
        ms.apply_edit(base, {"op": "tidy", "kind": "areas", "id": 11, "tool": "cleanup"}, REF)
    with pytest.raises(ms.EditError, match="unknown tidy tool"):
        ms.apply_edit(base, {"op": "tidy", "kind": "areas", "id": 1, "tool": "melt"}, REF)
    with pytest.raises(ms.EditError, match="cannot be tidied"):
        ms.apply_edit({**base, "elec_fence": [{"id": 9, "ref": R, "range": pts((0, 0), (1, 0), (1, 1))}]},
                      {"op": "tidy", "kind": "elec_fence", "id": 9, "tool": "cleanup"}, REF)


def test_tidy_many_is_one_edit_and_all_or_nothing():
    base = a_map()
    items = [{"kind": "nogozones", "id": 45, "tool": "round"}, {"kind": "areas", "id": 1, "tool": "straighten"}]
    draft, desc = ms.apply_edit(base, {"op": "tidy_many", "items": items, "tol": 0.10}, REF)
    assert desc.startswith("tidied 2 shapes")
    with pytest.raises(ms.EditError):
        ms.apply_edit(base, {"op": "tidy_many", "items": items + [{"kind": "areas", "id": 11, "tool": "cleanup"}]}, REF)


def fake_request(tmp_path, m):
    store = Store(str(tmp_path))
    r = SimpleNamespace(store=store, map_raw=m, gps_ref=REF)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(rec=r, store=store)))


def test_preview_and_review_change_nothing(tmp_path):
    m = a_map()
    req = fake_request(tmp_path, m)
    p = asyncio.run(main.studio_tidy_preview(req, {"kind": "nogozones", "id": 45, "tol": 0.10}))
    assert set(p["tools"]) == set(tidy.TOOLS) and p["bias"] == "grow"
    assert p["tools"]["round"]["ok"] and p["tools"]["round"]["geometry"]["type"] == "Polygon"
    line = asyncio.run(main.studio_tidy_preview(req, {"kind": "pathways", "id": 52}))
    assert set(line["tools"]) == {"cleanup", "straighten", "smooth"}
    rev = asyncio.run(main.studio_tidy_review(req, {"tol": 0.10}))
    rows = {(row["kind"], row["id"]): row for row in rev["rows"]}
    assert rows[("nogozones", 45)]["tool"] == "round" and rows[("nogozones", 45)]["sendable"]
    assert rows[("areas", 1)]["sendable"] is True              # in place (save_clean_area), like no-vision zones
    assert m == a_map() and req.app.state.store.setting("map_draft") is None


def test_two_edits_on_the_same_draft_cannot_both_win(tmp_path, monkeypatch):
    """Edits run off the event loop; the draft lock keeps a slow tidy and a rename from both landing on one base."""
    store = Store(str(tmp_path))
    m = a_map()
    r = SimpleNamespace(store=store, map_raw=m, gps_ref=REF, cfd=None, run_id=None, t={}, snapshot_at=0)
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(rec=r, store=store)))
    store.set_setting("map_draft", {"map": m, "updated": 1.0, "log": ["start"], "id_floor": 0, "source": "current"})
    real = ms.apply_edit

    def slow(*a, **k):
        import time
        time.sleep(0.3)
        return real(*a, **k)
    monkeypatch.setattr(ms, "apply_edit", slow)
    monkeypatch.setattr(main, "_draft_view", lambda request: {"ok": True})

    async def both():
        return await asyncio.gather(
            main.studio_draft_edit(req, {"op": "tidy", "kind": "nogozones", "id": 45, "tool": "round", "tol": 0.1, "base": 1.0}),
            main.studio_draft_edit(req, {"op": "rename", "kind": "nogozones", "id": 45, "name": "oak", "base": 1.0}),
            return_exceptions=True)
    results = asyncio.run(both())
    assert sum(isinstance(x, main.HTTPException) and x.status_code == 409 for x in results) == 1
    assert len(store.setting("map_draft")["log"]) == 2
