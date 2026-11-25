"""Mow a patch (app/patch.py, ADR 0013): a temporary rectangle area added, lined up with the last mow, run once and
removed. The fake robot adds areas with the next free id, keeps plans and per-area settings, deletes both, and
answers the route preview the way the real one was seen to: stripes measured from the outline's long side minus the
route angle, a gap apart, placed from the outline (move the outline and the stripes move with it). Synthetic yard."""
import asyncio
import copy
import math
import time
from types import SimpleNamespace

import pytest

from app import geo, mapstudio as ms, mapwrite as mw, patch
from tests.test_area_settings import READ, SettingsRobot
from tests.test_area_writes import writer
from tests.test_mapstudio import REF, lonlat, sample_map

GAP = READ["gap"]            # 0.3 m
LINE_ANGLE = 30.0            # the last mow's stripes, degrees in the panel's frame (x west, y north)
LINE_PHASE = 0.11            # where they sit across, modulo the gap


def _long_side(ring):
    a, b = max(zip(ring, ring[1:] + ring[:1]), key=lambda e: math.dist(e[0], e[1]))
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180


def _stripes(ring, angle, gap, first):
    """Back-and-forth stripes across a convex ring at `angle`, the first `first` metres in from its low side."""
    t = math.radians(angle)
    u, n = (math.cos(t), math.sin(t)), (-math.sin(t), math.cos(t))
    offs = [x * n[0] + y * n[1] for x, y in ring]
    out, o, k = [], min(offs) + first, 0
    while o < max(offs) - 0.01:
        cuts = []
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            o1, o2 = x1 * n[0] + y1 * n[1], x2 * n[0] + y2 * n[1]
            if (o1 - o) * (o2 - o) < 0:
                f = (o - o1) / (o2 - o1)
                cuts.append((x1 + f * (x2 - x1), y1 + f * (y2 - y1)))
        if len(cuts) == 2:
            cuts.sort(key=lambda p: p[0] * u[0] + p[1] * u[1])
            out += cuts if k % 2 == 0 else cuts[::-1]
            k += 1
        o += gap
    return out


class PatchRobot(SettingsRobot):
    def __init__(self, raw, **kw):
        super().__init__(raw, **kw)
        self.plans = [{"id": 1, "name": "Whole yard", "areaIds": [1, 11], "enable_self_order": True}]
        self.next_area = 900
        self.add_reply_has_id = True

    def _apply(self, cmd, payload):
        if cmd == mw.AREA_SAVE and "id" not in payload:
            rec = {**copy.deepcopy(payload), "id": self.next_area}
            self.next_area += 1
            self.map["areas"].append(rec)
            self._relink()
            return {"state": 0, "msg": "", "data": rec if self.add_reply_has_id else ""}
        if cmd == patch.AREA_DELETE:
            self.map["areas"] = [a for a in self.map["areas"] if a["id"] != payload["id"]]
            self.plans = [p for p in self.plans if payload["id"] not in p["areaIds"]]
            self.params.pop(payload["id"], None)
            return {"state": 0, "msg": "deleted", "data": ""}
        if cmd == mw.PLAN_SAVE:
            self.plans = [p for p in self.plans if p["id"] != payload["id"]] + [copy.deepcopy(payload)]
            return {"state": 0, "msg": "The plan was saved successfully.", "data": ""}
        if cmd == mw.PLAN_DELETE:
            self.plans = [p for p in self.plans if p["id"] != payload["id"]]
            return {"state": 0, "msg": "Plan deleted successfully.", "data": ""}
        return super()._apply(cmd, payload)

    async def read_plans(self):
        return copy.deepcopy(self.plans)

    async def preview(self, payload):
        aid = payload["id"]
        ring = [(p["x"], p["y"]) for p in payload["range"]]        # the shared ref is REF here
        s = self.params.get(aid) or READ
        angle = (_long_side(ring) - s["first_clean_params"]["route_angle"]) % 180
        path = _stripes(ring, angle, s["gap"], 0.2)
        return {"path": [{"x": x, "y": y, "phi": 0} for x, y in path], "total_time": 60.0 * len(path)}


def run_track():
    """A recorded mow over 'front' (0..20 m square): stripes at LINE_ANGLE, LINE_PHASE, sampled every 0.4 m."""
    t = math.radians(LINE_ANGLE)
    u, n = (math.cos(t), math.sin(t)), (-math.sin(t), math.cos(t))
    rows, k = [], 0
    o = LINE_PHASE - 40 * GAP * 100
    while o < 40:
        s = -40.0
        pts = []
        while s < 40:
            x, y = u[0] * s + n[0] * o, u[1] * s + n[1] * o
            if 0.6 < x < 19.4 and 0.6 < y < 19.4:
                pts.append((x, y))
            s += 0.4
        rows += pts if k % 2 == 0 else pts[::-1]
        k += 1
        o += GAP
    return [{"ts": i, "lon": geo.to_lonlat(REF, x, y)[0], "lat": geo.to_lonlat(REF, x, y)[1]} for i, (x, y) in enumerate(rows)]


class FakeController:
    def __init__(self):
        self.sent, self.on, self.settling = [], True, None

    def enabled(self):
        return self.on

    def start_settling(self):
        return self.settling

    async def send(self, cmd, args, actor="user", override=False, wait=True):
        self.sent.append((cmd, args, actor))
        return {"sent": True, "cmd": cmd}


def setup(tmp_path, runs=True):
    robot = PatchRobot(sample_map())
    store, w = writer(robot, tmp_path)
    store.set_setting("map_write_enabled", True)
    track = run_track()
    store.runs = lambda limit=100: [{"id": 7, "ended": time.time() - 3600}] if runs else []
    store.run_points = lambda run_id, since=0: track
    rec = SimpleNamespace(gps_ref=REF, map_raw=copy.deepcopy(robot.map), snapshot_at=time.time(), run_id=None,
                          t={"charging": 1, "planning": 0}, plan_active=lambda: False,
                          reload_plans=robot.read_plans, read_area_records=robot.read_areas,
                          read_area_params=robot.read, preview_area_path=robot.preview)
    ctl = FakeController()
    p = patch.PatchMower(store, w, rec, ctl, SimpleNamespace(why_no_writes=lambda: None))
    return robot, store, p, ctl, rec


CENTER = lonlat(12, 13)


def _lines_of(robot, aid):
    rec = next(a for a in robot.map["areas"] if a["id"] == aid)
    path = asyncio.run(robot.preview({"id": aid, "range": rec["range"]}))["path"]
    return patch.lines(patch.segments([(q["x"], q["y"]) for q in path], 1.0, None), GAP)


def test_the_lines_of_a_recorded_mow_are_found():
    segs = patch.segments([geo.to_local(REF, r["lon"], r["lat"]) for r in run_track()], 0.3, 3.0)
    got = patch.lines(segs, GAP)
    assert got["angle"] == pytest.approx(LINE_ANGLE, abs=0.3)
    assert abs(patch._wrap_gap(got["phase"] - LINE_PHASE, GAP)) < 0.01 and got["coherence"] > 0.9


def test_check_lays_the_rectangle_along_the_last_mow_and_writes_nothing(tmp_path):
    robot, _, p, _, _ = setup(tmp_path)
    r = asyncio.run(p.check(*CENTER, 6.0, 3.0))
    assert r["ok"] and r["area"]["id"] == 1 and r["target"]["source"] == "run", r
    ring = [geo.to_local(REF, *c) for c in r["corners"]]
    assert _long_side(ring) == pytest.approx(LINE_ANGLE, abs=0.3)
    assert robot.sent == []


@pytest.mark.parametrize("center, size, why", [
    (lonlat(19.0, 10), (6.0, 3.0), "must be inside the area"),     # pokes out of 'front'
    (lonlat(18.0, 13), (3.0, 1.5), "inside the area's edge"),      # in, but closer than 0.5 m to the edge
    (lonlat(25, 3), (6.0, 3.0), "inside a mowing area"),           # between the areas
    (lonlat(17.5, 5), (3.0, 1.5), "from where pathways join"),     # the pathway's end at (20, 5)
])
def test_a_patch_that_does_not_fit_says_why(tmp_path, center, size, why):
    _, _, p, _, _ = setup(tmp_path)
    r = asyncio.run(p.check(*center, *size))
    assert not r["ok"] and any(why in x for x in r["problems"]), r


def test_a_patch_is_added_lined_up_and_ready(tmp_path):
    robot, store, p, _, _ = setup(tmp_path)
    r = asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    assert r["ok"], r
    st = p.state()
    assert st["stage"] == "ready" and st["area_id"] == 900 and st["plan_id"] == 2
    cmds = [c for c, _ in robot.sent]
    assert cmds[:4] == [mw.AREA_SAVE, "save_mower_area_params", mw.AREA_SAVE, mw.PLAN_SAVE]
    add = robot.sent[0][1]
    assert "id" not in add and add["name"] == patch.NAME and add["labels"] == [] and add["ref"] == robot.map["areas"][0]["ref"]
    params = robot.params[900]
    assert params["edge_circles"] == 0 and params["buffer_dis"] == 0.0 and params["gap"] == GAP
    assert params["first_clean_params"]["blade_height"] == READ["first_clean_params"]["blade_height"]   # copied
    assert robot.plans[-1] == {"id": 2, "name": patch.NAME, "areaIds": [900], "enable_self_order": True}
    got = _lines_of(robot, 900)                       # the robot's own route for the patch, as it stands now
    assert got["angle"] == pytest.approx(LINE_ANGLE, abs=patch.ANGLE_TOL_DEG)
    assert abs(patch._wrap_gap(got["phase"] - LINE_PHASE, GAP)) <= patch.SHIFT_TOL_M
    assert robot.params[1] == READ and robot.map["areas"][0]["range"] == sample_map()["areas"][0]["range"]  # untouched
    assert any(e["kind"] == "patch_ready" for e in store.events())


def test_with_no_recorded_mow_the_areas_own_route_gives_the_lines(tmp_path):
    robot, _, p, _, _ = setup(tmp_path, runs=False)
    r = asyncio.run(p.check(*CENTER, 6.0, 3.0))
    want = _lines_of(robot, 1)
    assert r["ok"] and r["target"]["source"] == "plan"
    assert r["target"]["angle"] == pytest.approx(want["angle"], abs=0.3)


def test_a_wider_than_long_patch_gets_the_crosswise_angle(tmp_path):
    robot, _, p, _, _ = setup(tmp_path)
    assert asyncio.run(p.prepare(*CENTER, 3.0, 5.0))["ok"]
    assert robot.params[900]["first_clean_params"]["route_angle"] == 90
    assert _lines_of(robot, 900)["angle"] == pytest.approx(LINE_ANGLE, abs=patch.ANGLE_TOL_DEG)


def test_nothing_is_sent_unless_docked_idle_and_switched_on(tmp_path):
    for change, why in ((lambda s, r, c: s.set_setting("map_write_enabled", False), "map writing"),
                        (lambda s, r, c: setattr(c, "on", False), "control is switched off"),
                        (lambda s, r, c: r.t.update(charging=0), "charger"),
                        (lambda s, r, c: setattr(r, "plan_active", lambda: None), "unknown"),
                        (lambda s, r, c: setattr(r, "run_id", 5), "plan is running")):
        robot, store, p, ctl, rec = setup(tmp_path / why.replace(" ", "_"))
        change(store, rec, ctl)
        with pytest.raises(mw.WriteRefused, match=why):
            asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
        assert robot.sent == [] and p.state() is None


def test_a_failure_after_the_add_removes_what_was_added(tmp_path):
    robot, store, p, _, _ = setup(tmp_path)
    robot.fail = {mw.PLAN_SAVE}
    orig = robot._apply

    def failing(cmd, payload):
        if cmd == mw.PLAN_SAVE and robot.fail:
            robot.fail.clear()
            return {"state": -1, "msg": "refused"}           # the robot says no and keeps nothing
        return orig(cmd, payload)
    robot._apply = failing
    robot.plans_before = copy.deepcopy(robot.plans)
    r = asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    assert not r["ok"] and r["removed"]["ok"], r
    assert [a["id"] for a in robot.map["areas"]] == [1, 11] and robot.plans == robot.plans_before
    assert (patch.AREA_DELETE, {"id": 900}) in robot.sent and p.state() is None


def test_start_goes_through_the_controller_as_a_person(tmp_path):
    robot, store, p, ctl, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    res = asyncio.run(p.start())
    assert res["sent"] and ctl.sent == [("start_plan", {"plan_id": 2}, "user")]
    assert p.state()["stage"] == "started" and store.versions("patch")


def test_the_patch_is_removed_once_the_run_is_over_and_the_map_compared(tmp_path):
    robot, store, p, ctl, rec = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    asyncio.run(p.start())
    rec.run_id = 8                                            # mowing: nothing happens
    asyncio.run(p.tick())
    assert p.state()["stage"] == "started"
    rec.run_id = None                                         # back on the charger, plan over, a while later
    st = p.state()
    st["started_at"] -= patch.SETTLE_S + 1
    store.set_setting(patch.STATE, st)
    asyncio.run(p.tick())
    assert p.state() is None
    assert [a["id"] for a in robot.map["areas"]] == [1, 11]
    assert [x["id"] for x in robot.plans] == [1] and 900 not in robot.params
    last = store.setting(patch.STATE + "_last")
    assert last["problems"] == [] and any(e["kind"] == "patch_removed" for e in store.events())


def test_an_area_that_reused_the_patch_id_is_never_deleted(tmp_path):
    robot, store, p, _, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    a = ms.find(robot.map, "areas", 900)
    a["name"] = "Someone's new bed"                          # the app replaced it meanwhile under the same id
    r = asyncio.run(p.remove("test"))
    assert (patch.AREA_DELETE, {"id": 900}) not in robot.sent
    assert any("not the patch" in n for n in r["notes"])


def test_a_patch_never_started_is_removed_after_a_while(tmp_path):
    robot, store, p, _, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    asyncio.run(p.tick())
    assert p.state()["stage"] == "ready"
    st = p.state()
    st["ready_at"] -= patch.EXPIRE_S + 1
    store.set_setting(patch.STATE, st)
    asyncio.run(p.tick())
    assert p.state() is None and [a["id"] for a in robot.map["areas"]] == [1, 11]


def test_a_set_up_cut_short_by_a_restart_is_cleaned_up(tmp_path):
    robot, store, p, _, _ = setup(tmp_path)
    robot.add_reply_has_id = False                            # the add's reply never named the new area
    raw = copy.deepcopy(robot.map)
    plans = copy.deepcopy(robot.plans)
    robot.publish(mw.AREA_SAVE, patch.add_payload(raw["areas"][0], [{"x": 10, "y": 10, "phi": 0},
                                                                    {"x": 12, "y": 10, "phi": 0},
                                                                    {"x": 12, "y": 12, "phi": 0}]))
    store.set_setting(patch.STATE, {"stage": "adding", "created": time.time() - patch.SETTLE_S - 1,
                                    "area_id": None, "plan_id": None, "parent": {"id": 1, "name": "front"},
                                    "before": patch.snapshot(raw, plans), "log": []})
    asyncio.run(p.tick())
    assert p.state() is None and [a["id"] for a in robot.map["areas"]] == [1, 11]


def test_the_routes_are_closed_to_api_tokens():
    from app import auth
    for route in (("POST", "/api/patch"), ("POST", "/api/patch/start"), ("DELETE", "/api/patch"),
                  ("POST", "/api/patch/check")):
        assert route not in auth.TOKEN_WRITES


def test_only_the_patch_deletes_areas():
    """ADR 0013: del_clean_area is sent for a patch the panel added, and from nowhere else."""
    from pathlib import Path
    app = Path(__file__).resolve().parent.parent / "app"
    users = sorted(p.name for p in app.rglob("*.py") if "del_clean_area" in p.read_text(encoding="utf-8"))
    assert users == ["mapwrite.py", "patch.py"]


def _refusing(robot, cmd):
    """The robot refuses `cmd` (keeping what it had) until `robot.ok_again` is set."""
    orig = robot._apply
    robot.ok_again = False

    def apply(c, payload):
        if c == cmd and not robot.ok_again:
            return {"state": -1, "msg": "refused"}
        return orig(c, payload)
    robot._apply = apply


def test_a_plan_that_would_not_go_keeps_its_area_and_the_patch_tracked(tmp_path):
    robot, store, p, _, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    _refusing(robot, mw.PLAN_DELETE)
    r = asyncio.run(p.remove("test"))
    assert not r["ok"] and "plan 2 is still on the robot" in r["error"]
    assert (patch.AREA_DELETE, {"id": 900}) not in robot.sent              # never an orphan plan on a freed id
    assert p.state()["stage"] == "stuck" and p.plan_id() == 2
    asyncio.run(p.tick())                                                   # stuck waits for a person
    assert ms.find(robot.map, "areas", 900) is not None
    robot.ok_again = True
    r = asyncio.run(p.remove("again"))
    assert r["ok"] and p.state() is None and [a["id"] for a in robot.map["areas"]] == [1, 11]


def test_an_area_that_would_not_go_keeps_the_patch_tracked(tmp_path):
    robot, store, p, _, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    _refusing(robot, patch.AREA_DELETE)
    r = asyncio.run(p.remove("test"))
    assert not r["ok"] and "area 900 is still on the robot" in r["error"]
    assert p.state()["stage"] == "stuck" and [x["id"] for x in robot.plans] == [1]
    robot.ok_again = True
    assert asyncio.run(p.remove("again"))["ok"] and p.state() is None


def test_the_patch_mows_one_pass_whatever_the_parent_does_next(tmp_path):
    robot, store, p, _, _ = setup(tmp_path)
    robot.params[1] = {**copy.deepcopy(READ), "current_index": 1, "clean_times": 2}
    assert asyncio.run(p.prepare(*CENTER, 6.0, 3.0))["ok"]
    got = robot.params[900]
    assert got["current_index"] == 0 and got["clean_times"] == 1
    assert got["double_clean_params"]["route_angle"] == got["first_clean_params"]["route_angle"]


def test_start_refuses_a_plan_that_is_not_the_patch_any_more(tmp_path):
    robot, store, p, ctl, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    robot.plans[-1] = {"id": 2, "name": "Back beds", "areaIds": [11], "enable_self_order": True}  # reused in the app
    with pytest.raises(mw.WriteRefused, match="not the patch's"):
        asyncio.run(p.start())
    assert ctl.sent == []


def test_the_patch_plan_starts_only_from_its_own_button(tmp_path):
    from app import control
    from tests.test_phase2 import good_ctx, make_controller
    robot, store, p, _, _ = setup(tmp_path / "p")
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    _, cloud, c = make_controller(tmp_path / "c", ctx=good_ctx(plans=robot.plans), enabled=True)
    c.patch_plan = p.plan_id
    p.controller = c
    for actor in ("user", "mcp", "token", "scheduler"):                    # the picker, assistants, programs
        r = asyncio.run(c.send("start_plan", {"plan_id": 2}, actor=actor, override=True))
        assert not r["sent"] and "Started from Mow a patch" in r["refused"], actor
    assert not asyncio.run(c.preview("start_plan", {"plan_id": 2}))["would_send"]
    assert asyncio.run(c.send("start_plan", {"plan_id": 1}, actor="user"))["sent"]   # other plans as before
    assert asyncio.run(p.preview())["would_send"]
    assert asyncio.run(p.start())["sent"] and cloud.sent[-1] == ("start_plan", {"id": 2})
    assert control.PATCH_START.get() is False


def test_a_shift_onto_the_lines_is_checked_against_the_placement_rules(tmp_path, monkeypatch):
    robot, store, p, _, _ = setup(tmp_path)
    real = patch.shifted
    monkeypatch.setattr(patch, "shifted", lambda ring, angle, d: real(ring, angle, d + 40))   # off the area
    r = asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    assert not r["ok"] and "no longer fits" in r["error"] and r["removed"]["ok"]
    assert [a["id"] for a in robot.map["areas"]] == [1, 11] and p.state() is None


def test_a_start_the_robot_never_took_is_told_apart_from_a_run(tmp_path):
    robot, store, p, ctl, rec = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    asyncio.run(p.start())
    asyncio.run(p.tick())                                     # still docked and idle: the start has not shown
    assert not p.state().get("running_seen")
    st = p.state()
    st["started_at"] -= patch.SETTLE_S + 1
    store.set_setting(patch.STATE, st)
    asyncio.run(p.tick())
    assert p.state() is None
    assert "never showed the patch running" in store.setting(patch.STATE + "_last")["why"]


def test_a_start_the_robot_took_is_marked_running(tmp_path):
    robot, store, p, ctl, rec = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    asyncio.run(p.start())
    rec.run_id = 8
    asyncio.run(p.tick())
    assert p.state()["running_seen"] and p.state()["stage"] == "started"


def test_placing_checks_read_the_areas_settings_once_a_minute(tmp_path):
    robot, store, p, _, rec = setup(tmp_path)
    reads = []
    real = rec.read_area_params

    async def counting(aid):
        reads.append(aid)
        return await real(aid)
    rec.read_area_params = counting
    for dx in (0, 1e-6, 2e-6):
        assert asyncio.run(p.check(CENTER[0] + dx, CENTER[1], 6.0, 3.0))["ok"]
    assert reads == [1]
    assert asyncio.run(p.prepare(*CENTER, 6.0, 3.0))["ok"]
    assert reads.count(1) >= 2                                # the set-up reads afresh


# -- the CodeRabbit review's cases ---------------------------------------------------------------------------------
def test_a_thin_area_crossing_between_the_corners_is_an_overlap():
    from tests.test_mapstudio import pts
    raw = sample_map()
    raw["areas"].append({"id": 12, "name": "strip", "ref": raw["areas"][0]["ref"],
                         "range": pts((11.9, 0.5), (11.95, 0.5), (11.95, 19.5), (11.9, 19.5))})
    ring = patch.rectangle((12, 13), 6.0, 3.0, 30.0)
    assert any("overlaps area “strip”" in x for x in patch.problems(raw, 1, ring, REF))
    raw["areas"].pop()
    assert patch.problems(raw, 1, ring, REF) == []


def test_the_edge_clearance_is_measured_between_outlines():
    raw = sample_map()
    ring = [(19.55, 13.0), (17.0, 13.0), (17.0, 15.0), (19.55, 15.0)]   # 0.45 m from the east edge, at the corners
    assert any("inside the area's edge" in x for x in patch.problems(raw, 1, ring, REF))
    assert patch.ring_gap(ring, [(0, 0), (20, 0), (20, 20), (0, 20)]) == pytest.approx(0.45)


def test_a_plan_using_the_patch_area_keeps_it_and_the_patch_tracked(tmp_path):
    robot, store, p, _, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    robot.plans[0]["areaIds"].append(900)                    # someone added the patch to "Whole yard" in the app
    r = asyncio.run(p.remove("test"))
    assert not r["ok"] and "kept while plan 1 uses it" in r["error"]
    assert (patch.AREA_DELETE, {"id": 900}) not in robot.sent and robot.plans[0]["areaIds"] == [1, 11, 900]
    assert p.state()["stage"] == "stuck"


def test_a_plan_with_the_patch_id_but_other_areas_is_left_alone(tmp_path):
    robot, store, p, _, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    robot.plans[-1]["areaIds"] = [900, 11]                   # the patch plan was widened in the app
    r = asyncio.run(p.remove("test"))
    assert (mw.PLAN_DELETE, {"id": 2}) not in robot.sent
    assert not r["ok"] and p.state()["stage"] == "stuck"     # its area is in that plan, so it stays too


def test_two_new_areas_with_the_patch_name_are_never_guessed_between(tmp_path):
    robot, store, p, _, _ = setup(tmp_path)
    raw, plans = copy.deepcopy(robot.map), copy.deepcopy(robot.plans)
    for x in (10, 14):
        robot.publish(mw.AREA_SAVE, patch.add_payload(raw["areas"][0], [{"x": x, "y": 10, "phi": 0},
                                                                        {"x": x + 2, "y": 10, "phi": 0},
                                                                        {"x": x + 2, "y": 12, "phi": 0}]))
    store.set_setting(patch.STATE, {"stage": "adding", "created": time.time(), "area_id": None, "plan_id": None,
                                    "parent": {"id": 1, "name": "front"}, "before": patch.snapshot(raw, plans),
                                    "log": []})
    r = asyncio.run(p.remove("test"))
    assert not r["ok"] and "2 new areas" in r["error"]
    assert not any(c == patch.AREA_DELETE for c, _ in robot.sent) and p.state()["stage"] == "stuck"


def test_a_map_that_differs_after_removal_keeps_the_patch_until_accepted(tmp_path):
    robot, store, p, _, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    robot.map["pathways"][0]["end_id"] = 99                  # something re-linked meanwhile
    r = asyncio.run(p.remove("test"))
    assert not r["ok"] and r["problems"] and [a["id"] for a in robot.map["areas"]] == [1, 11]
    st = p.state()
    assert st["stage"] == "stuck" and st["differs"] == r["problems"]
    with pytest.raises(mw.WriteRefused, match="already set up"):
        asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    assert not asyncio.run(p.remove("again"))["ok"]          # checking again: still different, still tracked
    r = asyncio.run(p.remove("accepted", accept=True))
    assert p.state() is None and store.setting(patch.STATE + "_last")["accepted"] is True


def test_start_refuses_an_area_changed_in_the_app(tmp_path):
    robot, store, p, ctl, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    a = ms.find(robot.map, "areas", 900)
    a["range"] = [{**q, "x": q["x"] + 1.0} for q in a["range"]]
    with pytest.raises(mw.WriteRefused, match="not the one that was set up"):
        asyncio.run(p.start())
    assert ctl.sent == []


def test_a_gap_changed_between_the_check_and_the_set_up_is_refused(tmp_path):
    robot, store, p, _, rec = setup(tmp_path)
    real, calls = rec.read_area_params, []

    async def changing(aid):
        calls.append(aid)
        r = await real(aid)
        return {**r, "gap": 0.45} if aid == 1 and len(calls) > 1 else r
    rec.read_area_params = changing
    with pytest.raises(mw.WriteRefused, match="line spacing changed"):
        asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    assert robot.sent == []


def test_lines_that_turn_after_the_shift_are_refused(tmp_path):
    robot, store, p, _, rec = setup(tmp_path)
    real, n = rec.preview_area_path, []

    async def turning(payload):
        out = await real(payload)
        n.append(1)
        if len(n) > 1:                                        # after the shift the robot runs them crosswise
            x0, y0 = out["path"][0]["x"], out["path"][0]["y"]
            out["path"] = [{**q, "x": x0 - (q["y"] - y0), "y": y0 + (q["x"] - x0)} for q in out["path"]]
        return out
    rec.preview_area_path = turning
    r = asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    assert not r["ok"] and "do not sit on the last mow" in r["error"] and p.state() is None


def test_a_shift_that_re_links_a_pathway_is_caught(tmp_path):
    robot, store, p, _, _ = setup(tmp_path)
    orig, saves = robot._apply, []

    def relinking(cmd, payload):
        out = orig(cmd, payload)
        if cmd == mw.AREA_SAVE and payload.get("id") == 900:
            saves.append(1)
            if len(saves) == 2:                               # the first is after the settings; the second, the shift
                robot.map["pathways"][0]["end_id"] = 900
        return out
    robot._apply = relinking
    r = asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    assert len(saves) >= 2 and not r["ok"] and "lining the patch up changed the rest of the map" in r["error"]


def test_override_must_be_a_real_boolean():
    from fastapi import HTTPException
    from app.main import _override
    assert _override({}) is False and _override({"override": True}) is True
    for bad in ("false", "true", 1, 0, None, "yes"):
        with pytest.raises(HTTPException):
            _override({"override": bad})


# -- the second CodeRabbit pass -------------------------------------------------------------------------------------
def test_fits_near_0_and_180_degrees_compare_along_one_normal():
    tgt = {"angle": 179.6, "phase": 0.10}
    assert patch.phase_shift(tgt, {"angle": 0.3, "phase": 0.20}, 0.3) == pytest.approx(0.0, abs=1e-9)
    assert patch.phase_shift(tgt, {"angle": 179.2, "phase": 0.08}, 0.3) == pytest.approx(0.02)


def test_start_refuses_settings_changed_in_the_app(tmp_path):
    robot, store, p, ctl, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    robot.params[900]["clean_times"] = 2
    with pytest.raises(mw.WriteRefused, match="mowing settings"):
        asyncio.run(p.start())
    assert ctl.sent == []


def test_start_needs_map_writing_so_the_patch_can_come_off(tmp_path):
    robot, store, p, ctl, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    store.set_setting("map_write_enabled", False)
    with pytest.raises(mw.WriteRefused, match="map writing"):
        asyncio.run(p.start())
    assert ctl.sent == []


def test_two_presses_of_start_send_once(tmp_path):
    robot, store, p, ctl, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    real = ctl.send

    async def both():
        inside, go = asyncio.Event(), asyncio.Event()

        async def held(*a, **k):                             # the first start waits here until the second has pressed
            inside.set()
            await go.wait()
            return await real(*a, **k)
        ctl.send = held
        first = asyncio.ensure_future(p.start())
        await inside.wait()
        second = asyncio.ensure_future(p.start())
        await asyncio.sleep(0.05)
        go.set()
        return await asyncio.gather(first, second, return_exceptions=True)
    out = asyncio.run(both())
    assert len(ctl.sent) == 1 and sum(isinstance(r, mw.WriteRefused) for r in out) == 1


def test_a_start_that_finishes_after_the_removal_does_not_bring_the_patch_back(tmp_path):
    robot, store, p, ctl, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    real = ctl.send

    async def slow(*a, **k):
        store.set_setting(patch.STATE, None)                  # the removal ran while the start was on its way
        return await real(*a, **k)
    ctl.send = slow
    asyncio.run(p.start())
    assert p.state() is None


def test_accepting_takes_only_the_differences_that_were_shown(tmp_path):
    robot, store, p, _, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    robot.map["pathways"][0]["end_id"] = 99
    shown = asyncio.run(p.remove("test"))["problems"]
    robot.map["pathways"].append({"id": 53, "name": "", "ref": robot.map["pathways"][0]["ref"], "start_id": 1,
                                  "end_id": 11, "range": robot.map["pathways"][0]["range"]})
    r = asyncio.run(p.remove("accepted", accept=True))
    assert not r["ok"] and "new pathways 53" in "; ".join(r["problems"]) and p.state()["differs"] != shown
    assert asyncio.run(p.remove("accepted", accept=True))["accepted"] and p.state() is None


# -- the third CodeRabbit pass --------------------------------------------------------------------------------------
def _fit_through(angle, p, gap=0.3):
    t = math.radians(angle)
    return {"angle": angle, "phase": (-p[0] * math.sin(t) + p[1] * math.cos(t)) % gap}


def test_lines_are_compared_where_the_patch_is():
    a, b = _fit_through(0.0, (50, 0)), _fit_through(0.5, (50, 0))       # both pass through the patch at (50, 0)
    assert patch.phase_shift(a, b, 0.3, (50, 0)) == pytest.approx(0.0, abs=1e-9)
    assert abs(patch.phase_shift(a, b, 0.3)) > 0.05                       # measured at the origin they seem apart


def test_start_refuses_a_patch_the_map_has_moved_around(tmp_path):
    robot, store, p, ctl, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    robot.map["areas"].append({"id": 12, "name": "new bed", "ref": robot.map["areas"][0]["ref"],
                               "range": [{"x": 11.5, "y": 12.5, "phi": 0}, {"x": 12.5, "y": 12.5, "phi": 0},
                                         {"x": 12.5, "y": 13.5, "phi": 0}]})
    with pytest.raises(mw.WriteRefused, match="map changed around the patch"):
        asyncio.run(p.start())
    assert ctl.sent == []


def test_start_refuses_a_boundary_extension_added_in_the_app(tmp_path):
    robot, store, p, ctl, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    robot.params[900]["buffer_dis"] = 0.3
    with pytest.raises(mw.WriteRefused, match="buffer_dis"):
        asyncio.run(p.start())


def test_another_plan_with_the_patch_name_is_never_deleted(tmp_path):
    robot, store, p, _, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    robot.plans.append({"id": 3, "name": patch.NAME, "areaIds": [900], "enable_self_order": True})
    r = asyncio.run(p.remove("test"))
    assert (mw.PLAN_DELETE, {"id": 3}) not in robot.sent and (mw.PLAN_DELETE, {"id": 2}) in robot.sent
    assert not r["ok"] and "kept while plan 3 uses it" in r["error"]


def test_accepting_needs_the_map_that_was_shown_not_just_the_same_words(tmp_path):
    robot, store, p, _, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    a = ms.find(robot.map, "areas", 11)
    a["range"] = [{**q, "x": q["x"] + 1.0} for q in a["range"]]
    asyncio.run(p.remove("test"))
    a["range"] = [{**q, "x": q["x"] + 1.0} for q in a["range"]]       # moved again: same message, other outline
    assert not asyncio.run(p.remove("accepted", accept=True))["ok"]
    assert asyncio.run(p.remove("accepted", accept=True))["accepted"] and p.state() is None


# -- the GitHub review ----------------------------------------------------------------------------------------------
def test_map_writing_switched_off_part_way_stops_the_next_write(tmp_path):
    robot, store, p, _, _ = setup(tmp_path)
    orig = robot._apply

    def switch_off(cmd, payload):
        out = orig(cmd, payload)
        if cmd == "save_mower_area_params":
            store.set_setting("map_write_enabled", False)       # switched off while the set-up runs
        return out
    robot._apply = switch_off
    r = asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    cmds = [c for c, _ in robot.sent]
    assert not r["ok"] and cmds == [mw.AREA_SAVE, "save_mower_area_params"]
    assert p.state()["stage"] == "stuck"                        # removal refused too: it waits for the switch


def test_start_refuses_any_copied_setting_changed_in_the_app(tmp_path):
    robot, store, p, ctl, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    robot.params[900]["first_clean_params"]["blade_height"] = 60
    with pytest.raises(mw.WriteRefused, match="blade_height"):
        asyncio.run(p.start())
    assert ctl.sent == []


def test_start_acts_only_on_the_patch_the_person_confirmed(tmp_path):
    robot, store, p, ctl, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    with pytest.raises(mw.WriteRefused, match="another one"):
        asyncio.run(p.start(plan_id=7))
    with pytest.raises(mw.WriteRefused, match="another one"):
        asyncio.run(p.preview(plan_id=7))
    assert ctl.sent == [] and asyncio.run(p.start(plan_id=2))["sent"]


def test_a_route_that_is_not_one_grid_is_no_answer(tmp_path):
    robot, store, p, _, rec = setup(tmp_path)
    real = rec.preview_area_path

    async def two_grids(payload):
        out = await real(payload)
        nx, ny = -math.sin(math.radians(LINE_ANGLE)), math.cos(math.radians(LINE_ANGLE))   # across the lines
        out["path"] = ([{**q, "x": q["x"] + 0.06 * nx, "y": q["y"] + 0.06 * ny} for q in out["path"]]
                       + [{**q, "x": q["x"] - 0.06 * nx, "y": q["y"] - 0.06 * ny} for q in out["path"]])
        return out
    rec.preview_area_path = two_grids
    r = asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    assert not r["ok"] and p.state() is None


def test_a_pathway_that_moved_counts_as_a_difference():
    raw = sample_map()
    before = patch.snapshot(raw, [])
    raw["pathways"][0]["range"][0]["y"] += 0.5
    assert any("pathway 52's outline changed" in d for d in patch.compare(before, patch.snapshot(raw, [])))


def test_the_patch_routes_need_the_plan_id():
    from fastapi import HTTPException
    from app.main import _patch_plan_id
    assert _patch_plan_id({"plan_id": 2}) == 2
    for bad in ({}, {"plan_id": "2"}, {"plan_id": True}, {"plan_id": None}):
        with pytest.raises(HTTPException):
            _patch_plan_id(bad)


def test_a_confirmation_never_starts_a_later_patch_that_reused_its_plan_id(tmp_path):
    robot, store, p, ctl, _ = setup(tmp_path)
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    first = p.state()
    asyncio.run(p.remove("test"))
    first_mark = first["created"] - 5                          # an earlier set-up than the next one
    asyncio.run(p.prepare(*CENTER, 6.0, 3.0))
    assert p.state()["plan_id"] == first["plan_id"]            # the robot handed out the same plan id again
    with pytest.raises(mw.WriteRefused, match="another one"):
        asyncio.run(p.start(plan_id=first["plan_id"], patch=first_mark))
    with pytest.raises(mw.WriteRefused, match="another one"):
        asyncio.run(p.preview(plan_id=first["plan_id"], patch=first_mark))
    assert ctl.sent == []
    assert asyncio.run(p.start(plan_id=first["plan_id"], patch=p.state()["created"]))["sent"]


def test_the_patch_routes_need_the_patch_mark():
    from fastapi import HTTPException
    from app.main import _patch_mark
    assert _patch_mark({"patch": 1795574000.25}) == 1795574000.25
    for bad in ({}, {"patch": "1"}, {"patch": True}, {"patch": None}, {"patch": float("nan")}):
        with pytest.raises(HTTPException):
            _patch_mark(bad)


# -- placing by two corners ------------------------------------------------------------------------------------------
def _local(p):
    return geo.to_local(REF, *p)


def _frame(ring, angle):
    u, n = patch.axes(angle)
    us = [x * u[0] + y * u[1] for x, y in ring]
    ns = [x * n[0] + y * n[1] for x, y in ring]
    return max(us) - min(us), max(ns) - min(ns)


def test_two_corners_make_the_rectangle_between_them_along_the_lines(tmp_path):
    robot, _, p, _, _ = setup(tmp_path)
    a, b = (9.0, 11.0), (12.7, 16.6)                          # 6 m along 30° lines, 3 m across
    r = asyncio.run(p.check_corners(lonlat(*a), lonlat(*b)))
    assert r["ok"], r
    ring = [_local(c) for c in r["corners"]]
    want = patch.span(a, b, r["target"]["angle"])
    assert _frame(ring, r["target"]["angle"]) == pytest.approx((want[1] - want[0], want[3] - want[2]), abs=0.02)
    assert (r["length"], r["width"]) == pytest.approx((want[1] - want[0], want[3] - want[2]), abs=0.02)
    assert r["notes"] == [] and r["requested"] is None and robot.sent == []
    hs = [_local(h) for h in r["handles"]]                    # the handles sit on the corners nearest the taps
    assert min(math.dist(hs[0], c) for c in ring) < 0.01 and math.dist(hs[0], a) < math.dist(hs[1], a)


def test_a_patch_that_sticks_out_is_trimmed_to_fit_and_says_so(tmp_path):
    robot, _, p, _, _ = setup(tmp_path)
    r = asyncio.run(p.check_corners(lonlat(14.0, 11.0), lonlat(22.0, 15.0)))    # the area ends at x = 20
    assert r["ok"], r
    ring = [_local(c) for c in r["corners"]]
    assert all(ms.point_in(c, [(0, 0), (20, 0), (20, 20), (0, 20)]) for c in ring)
    assert patch.ring_gap(ring, [(0, 0), (20, 0), (20, 20), (0, 20)]) >= patch.EDGE_CLEAR_M
    assert r["requested"] and any("trimmed" in x for x in r["notes"])
    assert asyncio.run(p.prepare(*r["center"], r["length"], r["width"]))["ok"]   # set-up takes what was shown


def test_a_patch_mostly_outside_the_area_is_refused(tmp_path):
    _, _, p, _, _ = setup(tmp_path)
    r = asyncio.run(p.check_corners(lonlat(19.2, 11.0), lonlat(19.6, 13.0)))   # at the edge: no room
    assert not r["ok"] and "too little of it" in r["problems"][0] and r["requested"]


def test_a_patch_reaching_into_another_area_is_refused(tmp_path):
    _, _, p, _, _ = setup(tmp_path)
    r = asyncio.run(p.check_corners(lonlat(5.0, 8.0), lonlat(33.0, 9.0)))       # front to inside back
    assert not r["ok"] and "reaches into “back”" in r["problems"][0]


def test_two_taps_close_together_make_the_smallest_patch(tmp_path):
    _, _, p, _, _ = setup(tmp_path)
    r = asyncio.run(p.check_corners(lonlat(12.0, 13.0), lonlat(12.3, 13.2)))
    assert r["ok"] and r["length"] == pytest.approx(patch.MIN_SIDE_M) and r["width"] == pytest.approx(patch.MIN_SIDE_M)
    assert any("smallest" in x for x in r["notes"])


@pytest.mark.parametrize("bad", [None, [1], ["x", 2], [float("nan"), 1], [True, 1]])
def test_corners_must_be_lon_lat_pairs(tmp_path, bad):
    _, _, p, _, _ = setup(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(p.check_corners(bad, lonlat(12.0, 13.0)))


def _lines_that_turn(p, angles):
    """target() answering with the given line angles in turn (then the last one), the rest of its answer kept."""
    real, seen = p.target, []

    async def turning(*a, **k):
        got = await real(*a, **k)
        ang = angles[min(len(seen), len(angles) - 1)]
        seen.append(ang)
        return {**got, "angle": ang} if got else got
    p.target = turning
    return seen


def test_lines_that_differ_around_the_final_rectangle_rebuild_it_from_the_taps(tmp_path):
    _, _, p, _, _ = setup(tmp_path)
    seen = _lines_that_turn(p, [30.0, 120.0])                   # the taps' lines, then other lines around the result
    a, b = (9.0, 11.0), (12.7, 16.6)
    r = asyncio.run(p.check_corners(lonlat(*a), lonlat(*b)))
    assert r["ok"] and r["target"]["angle"] == 120.0 and seen[:3] == [30.0, 120.0, 120.0]
    want = patch.span(a, b, 120.0)                              # built along the lines set-up will use
    assert _frame([_local(c) for c in r["corners"]], 120.0) == pytest.approx(
        (want[1] - want[0], want[3] - want[2]), abs=0.02)


def test_lines_that_never_agree_are_refused(tmp_path):
    _, _, p, _, _ = setup(tmp_path)
    _lines_that_turn(p, [30.0, 120.0, 30.0, 120.0, 30.0, 120.0, 30.0])
    r = asyncio.run(p.check_corners(lonlat(9.0, 11.0), lonlat(12.7, 16.6)))
    assert not r["ok"] and "run two ways" in r["problems"][0]
