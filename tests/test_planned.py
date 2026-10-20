"""Planned vs driven (app/planned.py): distances to the plan, off-plan stretches, how much of the plan was driven."""
import math

import numpy as np
import pytest

from app import planned as pl

LON0, LAT0 = -76.5, 39.0
KX = 111320 * math.cos(math.radians(LAT0))


def ll(x, y):
    return [LON0 + x / KX, LAT0 + y / 111320]


def stripes(n=5, length=20.0, gap=0.5):
    """A boustrophedon plan: n lines `length` m long, `gap` m apart, as one [lon, lat] line."""
    out = []
    for i in range(n):
        xs = (0, length) if i % 2 == 0 else (length, 0)
        out += [ll(xs[0], i * gap), ll(xs[1], i * gap)]
    return out


def drive(points, t0=0.0, dt=1.0, planning=1, rtk=4):
    return [{"ts": t0 + i * dt, "lon": p[0], "lat": p[1], "rtk_status": rtk, "planning": planning} for i, p in enumerate(points)]


def test_a_track_on_the_plan_is_on_it_and_a_detour_is_one_stretch():
    plan = stripes()
    on = [ll(x, 0.0) for x in np.arange(0, 20, 0.5)]
    detour = [ll(x, -3.0) for x in np.arange(5, 10, 0.5)]           # 3 m off for 5 m
    track = drive(on[:10] + detour + on[20:])
    r = pl.compare([plan], track)
    assert r["off_plan_pct"] > 0 and r["on_plan_pct"] > 60
    feats = r["off_plan"]["features"]
    # the stretch includes the sideways moves out to the detour and back: they leave the plan too
    assert len(feats) == 1 and 9 <= feats[0]["properties"]["length_m"] <= 12 and 2.9 <= feats[0]["properties"]["max_off_m"] <= 3.1
    assert feats[0]["properties"]["kind"] == "away"


def test_cutting_across_the_stripes_is_off_the_plan_even_though_it_is_close():
    plan = stripes(n=9, gap=0.3)                                    # stripes 0.3 m apart, like the robot's
    across = drive([ll(10.0, y) for y in np.arange(-0.5, 3.0, 0.1)])  # crosses every stripe at right angles
    r = pl.compare([plan], across)
    assert r["off_plan_pct"] > 80 and r["off_plan"]["features"][0]["properties"]["kind"] == "across"


def test_only_rtk_fixed_mowing_samples_count():
    plan = stripes()
    far = [ll(x, 30.0) for x in np.arange(0, 20, 0.5)]
    track = drive(far, planning=3) + drive(far, t0=100, rtk=1) + drive([ll(x, 0.5) for x in range(20)], t0=200)
    r = pl.compare([plan], track)
    assert r["compared"] == 18 and r["off_plan_pct"] == 0.0            # the end samples have no heading


def test_the_share_of_the_plan_driven():
    plan = stripes(n=2)                                            # two lines of 20 m
    r = pl.compare([plan], drive([ll(x, 0.0) for x in np.arange(0, 20.1, 0.2)]))
    assert 40 <= r["plan_driven_pct"] <= 60                         # one of the two lines


def test_nearest_is_exact_within_reach_and_respects_direction():
    pts = np.array([[0.0, 0.0], [5.0, 5.0]])
    targets = np.array([[0.3, 0.4], [50.0, 50.0]])
    d = pl.nearest(pts, targets)
    assert abs(d[0] - 0.5) < 1e-9 and math.isinf(d[1])
    d = pl.nearest(pts[:1], targets[:1], p_ang=np.array([0.0]), t_ang=np.array([math.pi]))   # opposite sense: same line
    assert abs(d[0] - 0.5) < 1e-9
    d = pl.nearest(pts[:1], targets[:1], p_ang=np.array([0.0]), t_ang=np.array([math.pi / 2]))
    assert math.isinf(d[0])


# -- the robot's own path (get_plan_feedback cleanPathProgress), 2026-10-11 ----------------------------------------
GPS = (39.0, -76.5)
AREA_REF = {"latitude": 39.0001, "longitude": -76.5002}
AREA = {"id": 11, "ref": AREA_REF, "range": [{"x": 0, "y": 0}, {"x": 20, "y": 0}, {"x": 20, "y": 10}, {"x": 0, "y": 10}]}


def _stripes():
    return [{"x": float(x), "y": float(y)} for y in (1, 3, 5, 7, 9) for x in (range(1, 20) if y % 4 == 1 else range(19, 0, -1))]


def test_a_path_in_the_areas_own_frame_is_placed_there():
    from app import geo, planned
    coords = planned.place_robot_path(_stripes(), AREA, GPS)
    first = geo.to_local((AREA_REF["latitude"], AREA_REF["longitude"]), *coords[0])
    assert coords and abs(first[0] - 1) < 0.01 and abs(first[1] - 1) < 0.01


def test_a_path_in_the_maps_frame_is_found_too():
    from app import geo, planned
    a_ref = (AREA_REF["latitude"], AREA_REF["longitude"])
    in_map = []
    for p in _stripes():                                   # the same stripes, written around the map's reference
        lon, lat = geo.to_lonlat(a_ref, p["x"], p["y"])
        x, y = geo.to_local(GPS, lon, lat)
        in_map.append({"x": x, "y": y})
    coords = planned.place_robot_path(in_map, AREA, GPS)
    first = geo.to_local(a_ref, *coords[0])
    assert abs(first[0] - 1) < 0.05 and abs(first[1] - 1) < 0.05


def test_a_path_that_fits_neither_frame_is_not_used():
    from app import planned
    away = [{"x": p["x"] + 500, "y": p["y"]} for p in _stripes()]
    assert planned.place_robot_path(away, AREA, GPS) is None


def _edge():
    return [{"x": float(x), "y": float(y)} for x, y in ((0.5, 0.5), (19.5, 0.5), (19.5, 9.5), (0.5, 9.5), (0.5, 0.6))]


def _reply(session=(1, 1792269955), edge_index=0, finished=()):
    """A get_plan_feedback body shaped like run 7's (2026-10-18): area 11's edge pass (type 1) then its fill (0)."""
    return {"planId": session[0], "startTime": session[1], "areaIds": [11],
            "finishIds": [{"clean_times": 1, "id": a, "move_type": t} for a, t in finished],
            "cleanPathProgress": [
                {"id": 11, "type": 1, "clean_index": edge_index, "clean_times": 0, "path_slope": [], "path": _edge()},
                {"id": 11, "type": 0, "clean_index": 0, "clean_times": 0, "path_slope": [0.0, -3.14], "path": _stripes()}]}


def test_the_recorder_keeps_every_pass_of_each_area_once(tmp_path):
    from app.recorder import Recorder
    from app.store import Store
    r = Recorder.__new__(Recorder)
    r.store, r.run_id = Store(str(tmp_path)), 5
    r._keep_plan_paths({"cleanPathProgress": []}, 1.0)                                  # run 4's first reply
    r._keep_plan_paths(_reply(edge_index=3), 2.0)
    r._keep_plan_paths({**_reply(edge_index=9), "cleanPathProgress": [
        {"id": 11, "type": 1, "clean_index": 9, "path": _edge()[:3]}]}, 3.0)
    edge, fill = r.store.latest_version("plan_path:5:11:1"), r.store.latest_version("plan_path:5:11:0")
    assert edge["ts"] == 2.0 and len(edge["path"]) == len(_edge()) and edge["clean_index"] == 3
    assert edge["session"] == "1:1792269955" and fill["type"] == 0 and len(fill["path"]) == len(_stripes())


def test_paths_of_a_stale_plan_do_not_block_the_runs_own(tmp_path):
    """Review 2026-10-19: a first reply of an earlier, unfinished plan carries paths too."""
    from app.recorder import Recorder
    from app.store import Store
    r = Recorder.__new__(Recorder)
    r.store = Store(str(tmp_path))
    run = _run(r.store, 1000, "1:1792269955")
    r.run_id = run["id"]
    for reply, ts in ((_reply(session=(1, 1791665481), edge_index=7), 1.0), (_reply(edge_index=3), 2.0)):
        r._keep_plan_reply(reply, ts)
        r._keep_plan_paths(reply, ts)
    assert r.store.latest_version(f"plan_path:{run['id']}:11:1")["session"] == "1:1792269955"
    got = pl.robot_plan(r.store, run)
    assert {(p["type"], p["start_index"]) for p in got["parts"]} == {(0, 0), (1, 3)} and got["area_ids"] == [11]


def test_the_first_plan_reply_is_kept_and_once_replaced_by_one_with_paths(tmp_path):
    from app.recorder import Recorder
    from app.store import Store
    r = Recorder.__new__(Recorder)
    r.store, r.run_id = Store(str(tmp_path)), 6
    stale = {"planId": 1, "startTime": 1791665481, "state": 5, "cleanPathProgress": []}   # run 6: last week's plan
    r._keep_plan_reply(stale, 1.0)
    r._keep_plan_reply(_reply(), 2.0)
    r._keep_plan_reply(_reply(edge_index=50), 3.0)
    assert r.store.latest_version("plan_feedback:6")["ts"] == 2.0 and len(r.store.versions("plan_feedback:6")) == 2


def _run(st, started, session):
    rid = st.start_run(started, 5, None)
    st.set_run_plan(rid, session, None)
    return st.run(rid)


def test_robot_plan_takes_every_pass_from_where_the_run_took_it_up(tmp_path):
    from app.store import Store
    st = Store(str(tmp_path))
    run = _run(st, 1000, "1:1792269955")
    # kept before 2026-10-19: the area's first entry only, and the whole reply
    st.save_version(f"plan_path:{run['id']}:11", {"ts": 1, "area_id": 11, "path": _edge()})
    st.save_version(f"plan_feedback:{run['id']}", {"ts": 1, "data": _reply(edge_index=3)})
    got = pl.robot_plan(st, run)
    assert [(p["area_id"], p["type"], p["start_index"]) for p in got["parts"]] == [(11, 0, 0), (11, 1, 3)]
    assert got["area_ids"] == [11] and got["done"] == set()


def test_robot_plan_leaves_out_another_sessions_reply_and_fills_in_from_earlier_runs(tmp_path):
    from app.store import Store
    st = Store(str(tmp_path))
    six = _run(st, 1000, "1:1792269955")
    seven = _run(st, 60000, "1:1792269955")
    st.save_version(f"plan_feedback:{six['id']}", {"ts": 1, "data": {**_reply(), "startTime": 1791665481}})   # stale
    st.save_version(f"plan_path:{six['id']}:11:0", {"ts": 2, "area_id": 11, "type": 0, "session": "1:1792269955",
                                                    "clean_index": 40, "path": _stripes()})
    st.save_version(f"plan_feedback:{seven['id']}", {"ts": 3, "data": {**_reply(edge_index=2, finished=[(11, 0)]),
                                                                        "cleanPathProgress": _reply(edge_index=2)["cleanPathProgress"][:1]}})
    got = pl.robot_plan(st, seven)
    parts = {(p["area_id"], p["type"]): p for p in got["parts"]}
    assert parts[(11, 1)]["start_index"] == 2 and parts[(11, 1)]["source"] == "the robot's own plan"
    assert parts[(11, 0)]["start_index"] == 0 and f"run {six['id']}" in parts[(11, 0)]["source"]
    assert got["done"] == {(11, 0)}
    alone = pl.robot_plan(st, six)                       # its own reply was last week's plan: only the kept path
    assert [(p["type"], p["start_index"]) for p in alone["parts"]] == [(0, 40)] and alone["area_ids"] is None


def test_a_join_takes_the_later_pieces_kept_plan_along(tmp_path):
    from app.runs import RunStats
    from app.store import Store
    st = Store(str(tmp_path))
    a, b = _run(st, 1000, "1:5")["id"], _run(st, 5000, "1:5")["id"]
    for rid, ts in ((a, 1000), (b, 5000)):
        st.add_telemetry({"ts": ts, "run_id": rid, "x": 0.0, "y": 0.0, "rtk_status": 4})
        st.end_run(rid, ts + 10, "stopped", RunStats())
    st.save_version(f"plan_feedback:{b}", {"ts": 5001, "data": _reply()})
    st.save_version(f"plan_path:{b}:11:1", {"ts": 5001, "area_id": 11, "type": 1, "path": _edge()})
    st.merge_runs(a, b)
    assert st.latest_version(f"plan_feedback:{a}")["ts"] == 5001 and st.latest_version(f"plan_path:{a}:11:1")
    assert not st.versions_like(f"plan_path:{b}:%") and not st.versions(f"plan_feedback:{b}")


def test_planned_vs_driven_uses_the_kept_path_without_asking_the_robot(tmp_path):
    import asyncio

    from app import geo, main
    from tests.test_settings_api import fake_request
    req, r, _ = fake_request(tmp_path)
    r.map_raw, r.gps_ref = {"areas": [AREA]}, GPS

    async def no_robot():
        raise AssertionError("the robot should not be asked")
    r.read_area_records = no_robot
    rid = r.store.start_run(1000, 5, None)
    a_ref = (AREA_REF["latitude"], AREA_REF["longitude"])
    for i, p in enumerate(_stripes()):
        lon, lat = geo.to_lonlat(a_ref, p["x"], p["y"])
        x, y = geo.to_local(GPS, lon, lat)
        r.store.add_telemetry({"ts": 1000 + i, "run_id": rid, "x": x, "y": y, "lon": lon, "lat": lat, "rtk_status": 4,
                               "planning": 1, "speed_l": 0.4, "speed_r": 0.4})
    r.store.end_run(rid, 1200, "plan finished", __import__("app.runs", fromlist=["RunStats"]).RunStats())
    r.store.save_version(f"plan_feedback:{rid}", {"ts": 1001, "data": {"areaIds": [11]}})
    r.store.save_version(f"plan_path:{rid}:11", {"ts": 1002, "area_id": 11, "path": _stripes()})
    out = asyncio.run(main.run_planned(req, rid))
    assert out["basis"].startswith("the robot's own plan") and out["areas"][0]["source"] == "the robot's own plan"
    assert out["planned_time_s"] is None and out["planned_length_m"] > 80


def test_planned_vs_driven_compares_every_pass_and_leaves_out_what_was_done_before(tmp_path):
    """Run 7 (2026-10-18) was compared with area 11's first entry only (its edge pass): 1.35 km of a 6.1 km plan."""
    import asyncio

    from app import geo, main
    from tests.test_settings_api import fake_request
    req, r, _ = fake_request(tmp_path)
    r.map_raw, r.gps_ref = {"areas": [AREA]}, GPS

    async def no_robot():
        raise AssertionError("the robot should not be asked")
    r.read_area_records = no_robot
    rid = r.store.start_run(1000, 5, None)
    r.store.set_run_plan(rid, "1:1792269955", None)
    a_ref = (AREA_REF["latitude"], AREA_REF["longitude"])
    for i, p in enumerate(_stripes()):
        lon, lat = geo.to_lonlat(a_ref, p["x"], p["y"])
        x, y = geo.to_local(GPS, lon, lat)
        r.store.add_telemetry({"ts": 1000 + i, "run_id": rid, "x": x, "y": y, "lon": lon, "lat": lat, "rtk_status": 4,
                               "planning": 1, "speed_l": 0.4, "speed_r": 0.4})
    r.store.end_run(rid, 1200, "plan finished", __import__("app.runs", fromlist=["RunStats"]).RunStats())
    r.store.save_version(f"plan_feedback:{rid}", {"ts": 1001, "data": _reply(edge_index=3)})
    out = asyncio.run(main.run_planned(req, rid))
    area = out["areas"][0]
    assert [(q["type"], q["kind"]) for q in area["passes"]] == [(0, "fill"), (1, "edge")]
    edge = area["passes"][1]
    assert edge["done_before_m"] == pytest.approx(28.0, abs=0.5) and out["done_before_m"] == edge["done_before_m"]
    assert area["length_m"] == pytest.approx(sum(q["length_m"] for q in area["passes"]))
    assert len(out["route"]["features"]) == 2 and {f["properties"]["type"] for f in out["route"]["features"]} == {0, 1}
    assert out["on_plan_pct"] > 90
