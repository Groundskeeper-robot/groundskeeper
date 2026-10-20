"""Problem areas (app/problems.py), 2026-10-06."""
from app import problems as pr

MOW = {"run_id": 1, "rtk_status": 4, "heading_dop": 0.9, "odom_conf": 0.95, "pitch": 2.0, "roll": 1.0,
       "speed_l": 0.4, "speed_r": 0.4, "wheel_cur_l": 1.0, "wheel_cur_r": 1.0, "stuck": 0, "planning": 1,
       "planning_paused": 0, "recharging": 0, "error_code": 0}


def stripe(t0, x0, y, n, **over):
    return [{**MOW, "ts": t0 + i, "x": x0 + 0.4 * i, "y": y, **over} for i in range(n)]


def still(t0, x, y, n, **over):
    return [{**MOW, "ts": t0 + i, "x": x + (i % 2) * 0.05, "y": y, "speed_l": 0.0, "speed_r": 0.0, **over} for i in range(n)]


def test_a_stall_while_mowing_is_a_stuck_spot_with_its_duration():
    rows = stripe(0, 0, 0, 50) + still(50, 20, 0, 400) + stripe(450, 20, 0, 50)
    res = pr.analyse(rows)
    stuck = [s for s in res["spots"] if s["cause"] == "stuck"]
    assert len(stuck) == 1 and "no progress while working" in stuck[0]["evidence"] and abs(stuck[0]["x"] - 20) <= 2
    assert res["spots"][0]["cause"] == "stuck"                       # stuck outranks everything


def test_a_pause_is_not_a_stall():
    rows = stripe(0, 0, 0, 50) + still(50, 20, 0, 400, planning_paused=1, planning=0) + stripe(450, 20, 0, 50)
    assert not [s for s in pr.analyse(rows)["spots"] if s["cause"] == "stuck"]


def test_wheels_spinning_without_progress_and_the_robots_own_stuck_flag():
    spin = [{**MOW, "ts": 100 + i, "x": 5 + (i % 2) * 0.3, "y": 5, "speed_l": 0.4, "speed_r": 0.4} for i in range(70)]
    flag = still(300, 30, 30, 20, stuck=1, planning=0)
    whats = [e["what"] for e in pr.analyse(stripe(0, 0, 0, 20) + spin + flag)["events"]]
    assert "wheels driving without progress" in whats and "robot reported stuck" in whats


def test_steep_ground_and_weak_positioning_cells():
    steep = [{**MOW, "ts": i, "x": 1.0, "y": 1.0, "pitch": 22.0} for i in range(30)]
    weak = [{**MOW, "ts": 100 + i, "x": 11.0, "y": 11.0, "rtk_status": 5 if i % 2 else 4} for i in range(30)]
    flat = [{**MOW, "ts": 200 + i, "x": 21.0, "y": 21.0} for i in range(30)]
    cells = {(c["i"], c["j"]): c for c in pr.analyse(steep + weak + flat)["cells"]}
    assert cells[(0, 0)]["cause"] == "steep" and "22°" in cells[(0, 0)]["evidence"]["steep"] and "100 %" in cells[(0, 0)]["evidence"]["steep"]
    assert cells[(5, 5)]["cause"] == "position" and "no RTK fix 50 %" in cells[(5, 5)]["evidence"]["position"]
    assert (10, 10) not in cells


def test_a_weak_link_is_named_only_where_positioning_is_weak_too():
    def cell(x, n=30, **over):
        return [{**MOW, "ts": 1000 * x + i, "x": float(x), "y": 1.0, "rtk_status": 5 if i % 2 else 4, **over} for i in range(n)]
    weak_link = cell(1, halow=-84, rtcm_age=1.2)
    late = cell(11, halow=-55, rtcm_age=-1)
    sky = cell(21, halow=-55, rtcm_age=1.2)
    fine = [{**MOW, "ts": 50000 + i, "x": 31.0, "y": 1.0, "halow": -88, "rtcm_age": 1.0} for i in range(30)]
    cells = {c["i"]: c for c in pr.analyse(weak_link + late + sky + fine)["cells"]}
    assert "weak HaLow -84 dBm" in cells[0]["evidence"]["position"] and cells[0]["stats"]["halow_dbm"] == -84
    assert "corrections late 100 %" in cells[5]["evidence"]["position"] and "HaLow" not in cells[5]["evidence"]["position"]
    assert cells[10]["evidence"]["position"] == "no RTK fix 50 %"                  # good link: the sky, not the link
    assert 15 not in cells                                                          # weak HaLow alone is no problem


def test_reversing_at_turns_alone_is_not_a_problem_but_heavy_load_is():
    turns = [{**MOW, "ts": i, "x": 1.0, "y": 1.0, "speed_l": -0.2 if i % 4 == 0 else 0.3,
              "speed_r": -0.2 if i % 4 == 0 else 0.3} for i in range(40)]
    load = [{**MOW, "ts": 100 + i, "x": 11.0, "y": 1.0, "speed_l": 0.05, "speed_r": 0.05,
             "wheel_cur_l": 4.5 if i % 3 == 0 else 1.0} for i in range(40)]
    cells = {(c["i"], c["j"]): c for c in pr.analyse(turns + load)["cells"]}
    assert (0, 0) not in cells and cells[(5, 0)]["cause"] == "effort"


def test_error_codes_are_pinned_where_they_happened_once():
    rows = stripe(0, 0, 0, 10) + [{**MOW, "ts": 10 + i, "x": 4.0, "y": 0.0, "planning": -43} for i in range(5)]
    res = pr.analyse(rows)
    errs = [e for e in res["events"] if e["kind"] == "error"]
    assert len(errs) == 1 and errs[0]["what"] == "WP016 Unable to navigate obstacle"
    assert any(s["cause"] == "error" for s in res["spots"])


def test_neighbouring_cells_of_one_cause_are_one_spot_with_context():
    rows = [{**MOW, "ts": i, "x": 1.0 + (i % 3) * 2, "y": 1.0, "pitch": 24.0, "rtk_status": 5 if i % 5 == 0 else 4}
            for i in range(90)]
    spots = pr.analyse(rows)["spots"]
    assert len(spots) == 1 and spots[0]["cells"] == 3 and spots[0]["stats"]["max_tilt"] == 24


def test_a_lingering_stuck_code_during_normal_mowing_is_not_stuck():
    """Review: planning_paused 6 left set while the robot drives its plan must not become one long stuck spell."""
    rows = [{**r, "planning_paused": 6} for r in stripe(0, 0, 0, 600)]
    assert not [e for e in pr.analyse(rows)["events"] if e["kind"] == "stuck"]


def test_waiting_on_the_dock_between_areas_is_not_a_stall():
    rows = still(0, 0, 0, 1200, planning=3, recharging=4, charging=2)
    assert not pr.analyse(rows)["events"]


def test_one_incident_seen_by_several_detectors_is_one_event():
    rows = stripe(0, 0, 0, 30) + still(30, 20, 0, 400) + still(430, 20, 0, 60, stuck=1, planning=0, planning_paused=6)
    stuck = [e for e in pr.analyse(rows)["events"] if e["kind"] == "stuck"]
    assert len(stuck) == 1 and stuck[0]["what"] == "robot reported stuck"


def test_errors_that_are_not_about_a_place_are_listed_but_not_drawn():
    rows = stripe(0, 0, 0, 10) + [{**MOW, "ts": 10 + i, "x": 4.0, "y": 0.0, "planning": -24} for i in range(5)]
    res = pr.analyse(rows)
    assert [e["what"] for e in res["events"]] == ["Low battery"] and not any(c["cause"] == "error" for c in res["cells"])


def test_an_error_without_a_position_is_pinned_where_the_robot_was_last_seen():
    rows = stripe(0, 0, 0, 10) + [{**MOW, "ts": 10 + i, "x": None, "y": None, "planning": -30} for i in range(5)]
    e = [e for e in pr.analyse(rows)["events"] if e["kind"] == "error"][0]
    assert e["place"] and abs(e["x"] - 3.6) < 0.01


def test_a_row_with_only_one_coordinate_does_not_break_anything():
    rows = stripe(0, 0, 0, 10) + [{**MOW, "ts": 11, "x": 5.0, "y": None, "stuck": 1}]
    pr.analyse(rows)


def test_turning_on_the_spot_is_not_spinning_in_place():
    pivot = [{**MOW, "ts": i, "x": 5.0, "y": 5.0, "speed_l": 0.4, "speed_r": -0.4} for i in range(90)]
    assert not pr.analyse(pivot)["events"]


def test_per_run_summaries_combine_to_the_same_answer():
    a = stripe(0, 0, 0, 50) + still(50, 20, 0, 400)
    b = [{**r, "run_id": 2, "ts": r["ts"] + 10_000} for r in stripe(0, 0, 4, 50) + [{**MOW, "ts": 60 + i, "x": 1.0, "y": 1.0, "pitch": 22.0} for i in range(30)]]
    whole = pr.analyse(a + b)
    parts = pr.combine([pr.summarise_run(a), pr.summarise_run(b)])
    assert whole["cells"] == parts["cells"] and len(whole["events"]) == len(parts["events"])


def test_problem_map_caches_finished_runs_and_redoes_only_what_changed(tmp_path, monkeypatch):
    import asyncio
    from app import recorder as rec_mod
    from app.recorder import Recorder
    from app.store import Store
    st = Store(str(tmp_path))
    rid = st.start_run(1000, 5, None)
    for r in stripe(1000, 0, 0, 50):
        st.add_telemetry({**r, "run_id": rid})
    st.end_run(rid, 1050, "stopped", __import__("app.runs", fromlist=["RunStats"]).RunStats())
    r = Recorder.__new__(Recorder)
    r.store, r.gps_ref, r._run_summaries, r._problem_lock = st, (39.0, -76.5), {}, asyncio.Lock()
    calls = []
    real = pr.summarise_run
    monkeypatch.setattr(rec_mod.problems, "summarise_run", lambda rows: calls.append(len(rows)) or real(rows))
    monkeypatch.setattr(rec_mod.time, "time", lambda: 2000.0)
    asyncio.run(r.problem_map(60))
    asyncio.run(r.problem_map(60))
    assert calls == [50]                                   # second call served from the cache
    st.add_telemetry({**MOW, "ts": 1051, "x": 30.0, "y": 0.0, "run_id": rid})
    asyncio.run(r.problem_map(60))
    assert calls == [50, 51]


def test_the_robots_stuck_flag_counts_when_the_wheels_stop_even_mid_plan():
    rows = stripe(0, 0, 0, 20) + still(20, 10, 10, 150, stuck=1)           # planning 1, wheels 0, 150 s
    assert [e["what"] for e in pr.analyse(rows)["events"]] == ["robot reported stuck"]


def test_the_dock_is_not_a_position_spot():
    # run 4: 422 samples on the charger with RTK single and late corrections ranked the dock as a spot
    dock = [{**MOW, "ts": i, "x": 1.0, "y": 1.0, "rtk_status": 1, "rtcm_age": -1, "speed_l": 0.0, "speed_r": 0.0,
             "planning": 0, "charging": 1} for i in range(60)]
    brk = [{**MOW, "ts": 100 + i, "x": 1.0, "y": 1.0, "rtk_status": 1, "speed_l": 0.0, "speed_r": 0.0,
            "planning_paused": 2, "charging": 1} for i in range(60)]
    weak = [{**MOW, "ts": 300 + i, "x": 11.0, "y": 11.0, "rtk_status": 5} for i in range(30)]
    cells = {(c["i"], c["j"]): c for c in pr.analyse(dock + brk + weak)["cells"]}
    assert (0, 0) not in cells and cells[(5, 5)]["cause"] == "position"
