"""Run totals from stored rows, robot pose time (2026-10-11 sprint: run 4 lost ~1.1 km to restarts and bunching)."""
from app import telemetry
from app.runs import RunStats, path_length, pose_order
from app.store import Store


def row(x, y=0.0, ts=None, odom=None, **kw):
    return {"x": float(x), "y": float(y), "ts": ts if ts is not None else x, "odom_ts": odom, **kw}


def test_odom_time_is_read_in_seconds():
    t = telemetry.extract({"CombinedOdom": {"x": 1.0, "y": 2.0, "timestamp": 1791692237129.247}})
    assert abs(t["odom_ts"] - 1791692237.129247) < 1e-6


def test_pose_order_sorts_by_robot_time_and_drops_repeats():
    rows = [row(0, ts=10, odom=100.0), row(2, ts=11, odom=102.0), row(1, ts=11.01, odom=101.0),   # a burst, mixed
            row(2, ts=12, odom=102.0)]                                                            # the same pose again
    assert [r["x"] for r in pose_order(rows)] == [0, 1, 2]


def test_pose_order_keeps_arrival_order_for_older_rows():
    rows = [row(0, ts=1), row(2, ts=2), row(1, ts=3)]
    assert [r["x"] for r in pose_order(rows)] == [0, 2, 1]


def test_a_pose_out_of_place_and_back_is_not_driving():
    straight = [row(i) for i in range(11)]
    spiked = straight[:5] + [row(5, 3.0)] + straight[6:]          # one pose 3 m off the line, then back
    assert path_length(straight) == 10.0
    assert abs(path_length(spiked) - 10.0) < 0.01                  # not 10 + ~4.3


def test_relocalisation_jumps_are_not_counted():
    assert path_length([row(0), row(1), row(20), row(21)]) == 2.0


def test_totals_from_rows():
    rows = [row(i, ts=i, odom=float(i), rtk_status=4 if i % 2 else 1, battery=90 - i, error_code=0) for i in range(5)]
    s = RunStats.from_rows(rows)
    assert (s.points, s.distance_m, s.fixed_pct, s.battery_start, s.battery_end) == (5, 4.0, 40.0, 90, 86)


def _run_with_rows(st, n, started=1000):
    rid = st.start_run(started, 5, None)
    for i in range(n):
        st.add_telemetry({"ts": started + i, "run_id": rid, "x": float(i), "y": 0.0, "rtk_status": 4,
                          "odom_ts": float(started + i), "battery": 80})
    return rid


def test_a_run_left_open_by_a_restart_gets_its_totals(tmp_path):
    st = Store(str(tmp_path))
    rid = _run_with_rows(st, 30)                       # the process died: no totals were ever written
    assert st.close_orphan_runs() == 1
    r = st.run(rid)
    assert r["end_reason"] == "recorder restarted" and r["points"] == 30 and r["distance_m"] == 29.0


def test_end_run_counts_the_stored_rows_not_the_live_counters(tmp_path):
    st = Store(str(tmp_path))
    rid = _run_with_rows(st, 30)
    partial = RunStats(points=3, distance_m=2.0, battery_start=80, battery_end=80)    # counted since a restart
    st.end_run(rid, 1030, "returned to dock", partial)
    assert st.run(rid)["distance_m"] == 29.0 and st.run(rid)["points"] == 30


def test_stored_runs_are_worked_out_again_once(tmp_path):
    st = Store(str(tmp_path))
    rid = _run_with_rows(st, 30)
    st.db.execute("UPDATE runs SET ended = 1030, distance_m = 5, points = 3 WHERE id = ?", (rid,))
    assert st.recompute_all_runs_once() == 1 and st.run(rid)["distance_m"] == 29.0
    st.db.execute("UPDATE runs SET distance_m = 5 WHERE id = ?", (rid,))
    assert st.recompute_all_runs_once() == 0 and st.run(rid)["distance_m"] == 5     # only the first time


def test_the_kept_plan_feedback_is_shown_by_shape_or_raw(tmp_path):
    import asyncio

    import pytest
    from fastapi import HTTPException

    from app import main
    from tests.test_settings_api import fake_request
    req, r, _ = fake_request(tmp_path)
    with pytest.raises(HTTPException):
        asyncio.run(main.run_plan_feedback(req, 4))
    r.store.save_version("plan_feedback:4", {"ts": 1791665470.0, "data": {
        "planId": 1, "areaIds": [11, 34], "path": [{"x": 1.0, "y": 2.0, "phi": 0.1}, {"x": 1.5, "y": 2.0, "phi": 0.1}]}})
    got = asyncio.run(main.run_plan_feedback(req, 4))
    assert got["shape"] == {"planId": "int", "areaIds": {"list": 2, "item": "int"},
                            "path": {"list": 2, "of": {"x": "float", "y": "float", "phi": "float"}}}
    assert asyncio.run(main.run_plan_feedback(req, 4, raw=True))["data"]["areaIds"] == [11, 34]


def test_a_charging_break_does_not_count_in_the_totals():
    mow = [row(i, ts=i, odom=float(i), rtk_status=4, planning=1, speed_l=0.4, speed_r=0.4) for i in range(10)]
    brk = [row(9, ts=100 + 10 * i, odom=100.0 + 10 * i, rtk_status=1, planning=0, planning_paused=2, recharging=4,
               charging=2, speed_l=0, speed_r=0) for i in range(30)]
    s = RunStats.from_rows(mow + brk)
    assert s.points == 10 and s.fixed_pct == 100.0          # the dock's RTK single is not the run's


def test_parked_jitter_is_not_driving():
    import random
    rnd = random.Random(1)
    mow = [row(i, ts=i, odom=float(i), rtk_status=4, planning=1, speed_l=0.4, speed_r=0.4) for i in range(10)]
    brk = [row(9 + rnd.uniform(-0.2, 0.2), rnd.uniform(-0.2, 0.2), ts=100 + 10 * i, odom=100.0 + 10 * i, rtk_status=1,
               planning=0, planning_paused=2, recharging=4, charging=2, speed_l=0, speed_r=0) for i in range(720)]
    assert RunStats.from_rows(mow + brk).distance_m == 9.0          # the review measured 158 m before


def test_the_slip_check_reads_the_robots_time_from_storage(tmp_path):
    from app import problems
    assert "odom_ts" in problems.COLUMNS
    st = Store(str(tmp_path))
    rid = _run_with_rows(st, 3)
    assert all(r["odom_ts"] is not None for r in Store.read_run_rows(st.path, rid, problems.COLUMNS))


def test_a_failing_recompute_does_not_break_the_run_end(tmp_path, monkeypatch):
    from app import runs
    st = Store(str(tmp_path))
    rid = _run_with_rows(st, 5)
    monkeypatch.setattr(runs.RunStats, "from_rows", classmethod(lambda cls, rows: 1 / 0))
    st.end_run(rid, 1005, "returned to dock", RunStats(points=5, distance_m=4.0))
    assert st.run(rid)["end_reason"] == "returned to dock" and st.run(rid)["distance_m"] == 4.0


def test_recompute_does_not_flush_caches(tmp_path):
    st = Store(str(tmp_path))
    rid = _run_with_rows(st, 5)
    gen = st.write_gen
    st.recompute_run(rid)
    assert st.write_gen == gen


def test_a_run_paused_overnight_counts_only_its_working_time(tmp_path):
    """#62: a plan paused in the evening and resumed in the morning is one run; its pace must not count the night."""
    from app import insights
    st = Store(str(tmp_path))
    rid = st.start_run(1000, 5, None)
    t = 1000
    for _ in range(30):                                   # 29 s mowing
        st.add_telemetry({"ts": t, "run_id": rid, "planning": 1, "speed_l": 0.3, "speed_r": 0.3}); t += 1
    for _ in range(90):                                   # paused for 15 h, a row every 10 min
        st.add_telemetry({"ts": t, "run_id": rid, "planning": 1, "planning_paused": 1}); t += 600
    for _ in range(30):                                   # the morning: 29 s more
        st.add_telemetry({"ts": t, "run_id": rid, "planning": 1, "speed_l": 0.3, "speed_r": 0.3}); t += 1
    st.end_run(rid, t, "plan finished", RunStats())
    run = st.run(rid)
    assert run["working_s"] == 59                          # 29 + 1 (into the pause) + 29; the night is not in it
    row = insights.mow_row(run, None, None)
    assert row["duration_h"] == round(59 / 3600, 2) and run["ended"] - run["started"] > 15 * 3600


def test_a_gap_in_the_rows_counts_for_a_few_minutes_at_most():
    from app.runs import WORK_GAP_S, working_seconds
    rows = [{"ts": 0, "planning": 1, "speed_l": 0.3}, {"ts": 3600, "planning": 1, "speed_l": 0.3}]
    assert working_seconds(rows) == WORK_GAP_S


def test_a_run_without_working_time_falls_back_to_start_to_end():
    from app import insights
    run = {"id": 1, "started": 0, "ended": 7200, "working_s": None}
    assert insights.mow_row(run, None, None)["duration_h"] == 2.0
