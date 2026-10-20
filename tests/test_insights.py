"""Insights (app/insights.py), 2026-10-06."""
from app import insights as ins

RUN = {"id": 2, "started": 1000.0, "ended": 1000.0 + 7.1 * 3600, "end_reason": "returned to dock", "head_type": 5,
       "distance_m": 7427.1, "rtk_fixed_pct": 98.6, "battery_start": 100, "battery_end": 25, "plan_progress_pct": None}
ANALYSIS = {"areas": [{"covered_m2": 1674, "gap_m2": 29.8, "uncovered_m2": 42, "gaps": [{}] * 9},
                      {"covered_m2": 96, "gap_m2": 0, "uncovered_m2": 4, "gaps": []}]}
PROBLEMS = {"spots": [{"cause": "stuck"}, {"cause": "stuck"}, {"cause": "steep"}]}


def test_a_mow_row_from_run_coverage_and_problems():
    r = ins.mow_row(RUN, ANALYSIS, PROBLEMS)
    assert r["covered_m2"] == 1770 and r["coverage_pct"] == 97.5 and r["gap_count"] == 9
    assert r["m2_per_hour"] == 249 and r["energy_per_100m2"] == 4.24 and r["battery_used"] == 75
    assert (r["problem_spots"], r["stuck_spots"]) == (3, 2)


def test_a_mow_without_coverage_yet_still_has_a_row():
    r = ins.mow_row(RUN, None, None)
    assert r["coverage_pct"] is None and r["m2_per_hour"] is None and r["problem_spots"] == 0


def test_totals_and_cadence():
    a = ins.mow_row(RUN, ANALYSIS, PROBLEMS)
    b = {**a, "started": RUN["started"] + 3 * 86400, "coverage_pct": 99.0, "stuck_spots": 0}
    t = ins.totals([a, b], now=RUN["started"] + 4 * 86400)
    assert t["mows"] == 2 and t["days_between"] == 3.0 and t["days_since"] == 1.0
    assert t["coverage_pct"] == 98.2 and t["stuck_spots"] == 2
    assert ins.totals([], now=0) == {"mows": 0}


def test_recurring_needs_two_mows_and_ranks_stuck_first():
    ring = [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]
    cells = [{"cause": "steep", "runs": 3, "severity": 0.9, "evidence": {"steep": "s"}, "ring": ring},
             {"cause": "stuck", "runs": 2, "severity": 0.7, "evidence": {"stuck": "x"}, "ring": ring},
             {"cause": "stuck", "runs": 1, "severity": 1.0, "evidence": {"stuck": "y"}, "ring": ring}]
    out = ins.recurring(cells)
    assert [o["cause"] for o in out] == ["stuck", "steep"] and out[0]["lon"] == 1 and out[0]["lat"] == 1
