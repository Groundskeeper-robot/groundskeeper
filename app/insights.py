"""Insights: how the mowing goes over time, one row per mow plus totals, cadence and problems that keep coming back.

Pure functions over stored runs, their cached coverage analysis (coverage.analyze) and their problem summaries
(problems.combine for one run); the endpoint gathers those (app/main.py /api/insights).
"""
import statistics

MIN_RUN_M = 50          # shorter runs are tests or nudges, not mows


def mow_row(run: dict, analysis: dict | None, problems: dict | None) -> dict:
    # Working time where it is known (a run can span a night's pause, #62), else start to end.
    span = (run["ended"] - run["started"]) if run.get("ended") else None
    working = run.get("working_s")
    duration_h = max(0.0, (working if working is not None else span) / 3600) if span is not None else None
    used = (run["battery_start"] - run["battery_end"]
            if run.get("battery_start") is not None and run.get("battery_end") is not None else None)
    covered = known = gaps = None
    gap_count = 0
    if analysis:
        areas = analysis.get("areas") or []
        covered = sum(a.get("covered_m2") or 0 for a in areas)
        gaps = sum(a.get("gap_m2") or 0 for a in areas)
        known = covered + sum((a.get("uncovered_m2") if a.get("uncovered_m2") is not None else a.get("gap_m2")) or 0
                              for a in areas)
        gap_count = sum(len(a.get("gaps") or []) for a in areas)
    spots = (problems or {}).get("spots") or []
    return {
        "id": run["id"], "started": run["started"], "ended": run.get("ended"), "end_reason": run.get("end_reason"),
        "head_type": run.get("head_type"), "duration_h": round(duration_h, 2) if duration_h is not None else None,
        "distance_m": run.get("distance_m"), "rtk_fixed_pct": run.get("rtk_fixed_pct"), "battery_used": used,
        "covered_m2": round(covered) if covered is not None else None,
        "coverage_pct": round(100 * covered / known, 1) if covered is not None and known else None,
        "gaps_m2": round(gaps, 1) if gaps is not None else None, "gap_count": gap_count,
        "m2_per_hour": round(covered / duration_h) if covered and duration_h else None,
        "energy_per_100m2": round(100 * used / covered, 2) if used is not None and covered else None,
        "problem_spots": len(spots), "stuck_spots": sum(1 for s in spots if s["cause"] == "stuck"),
        "plan_progress_pct": run.get("plan_progress_pct"),
    }


def _avg(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(statistics.mean(vals), 1) if vals else None


def totals(rows: list[dict], now: float) -> dict:
    if not rows:
        return {"mows": 0}
    starts = sorted(r["started"] for r in rows)
    gaps = [(b - a) / 86400 for a, b in zip(starts, starts[1:])]
    return {
        "mows": len(rows),
        "hours": round(sum(r["duration_h"] or 0 for r in rows), 1),
        "distance_km": round(sum(r["distance_m"] or 0 for r in rows) / 1000, 1),
        "covered_m2": round(sum(r["covered_m2"] or 0 for r in rows)),
        "coverage_pct": _avg(rows, "coverage_pct"),
        "m2_per_hour": _avg(rows, "m2_per_hour"),
        "energy_per_100m2": _avg(rows, "energy_per_100m2"),
        "rtk_fixed_pct": _avg(rows, "rtk_fixed_pct"),
        "stuck_spots": sum(r["stuck_spots"] for r in rows),
        "last_mow": starts[-1], "days_since": round((now - starts[-1]) / 86400, 1),
        "days_between": round(statistics.mean(gaps), 1) if gaps else None,
    }


def recurring(cells: list[dict], min_runs: int = 2, limit: int = 12) -> list[dict]:
    """Problem cells seen in at least `min_runs` mows (the ones worth fixing on the ground), worst first."""
    rank = {"stuck": 0, "error": 1}
    hits = [c for c in cells if c.get("runs", 0) >= min_runs]
    hits.sort(key=lambda c: (rank.get(c["cause"], 2), -c["runs"], -c["severity"]))
    out = []
    for c in hits[:limit]:
        ring = c.get("ring") or []
        lon = sum(p[0] for p in ring[:4]) / 4 if len(ring) >= 4 else None
        lat = sum(p[1] for p in ring[:4]) / 4 if len(ring) >= 4 else None
        out.append({"cause": c["cause"], "runs": c["runs"], "severity": c["severity"],
                    "evidence": c["evidence"][c["cause"]], "lon": lon, "lat": lat})
    return out
