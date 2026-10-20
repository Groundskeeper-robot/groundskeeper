"""Problem areas: where the robot has real trouble, by cause, from recorded runs.

Built on the 2026-10-06 research (docs/dev/robot-states.md) and the first real mow:

* stuck     — the robot's own stuck signals (planning_paused 6 while it sits paused, on_going_recharging -9, or
              StateMSG.stuck while not driving, for at least OWN_MIN_S), the panel's stall rule (mowing / between
              areas / heading home but within 1 m for 3 min) and "wheels driving without progress" (10 m of wheel
              travel in 60 s while the robot moved < 1.5 m). Overlapping detections of one incident are merged.
* error     — plan / return-to-dock errors that are about a place (outside the map, boundary, no-go zone, obstacle,
              position, docking) and error_code 902 (tilted), pinned at the last known position. Other codes (low
              battery, plan not found...) are listed as events but not drawn on the map.
* steep     — the 90th percentile of max(|pitch|, |roll|) per cell. Rated 35°; adaptive slope from 5°; the stuck
              spot of 2026-10-05 was mostly over 12°.
* position  — RTK not fixed, heading DOP >= 2 (the robot falls back to IMU/odometry) or position confidence < 0.5
              (dead reckoning). Yarbo's rule of thumb: half an area poor = split or avoid it. The evidence names the
              link when it is weak there too (HaLow at or below -77 dBm, corrections late or missing), so a weak
              link can be told from a poor sky view; on the 2026-10-05..09 mows RTK lost its fix 5x as often at
              <= -82 dBm (where the robot falls back to 4G) as above -77.
* effort    — heavy wheel load at low speed, or reversing far beyond normal (reversing alone is how it turns at
              stripe ends: up to ~30 % of a turning cell on 2026-10-05).

Robot state comes from runs.activity()/runs.progressing(), the same rules the run tracking and live alerts use.
Work is split per run (summarise_run: events + per-cell counters), so finished runs are computed once and cached;
combine() merges summaries and scores the cells. Weak HaLow alone is not a place problem (the Signal layer shows it);
it is only named where positioning is weak too.
"""
import math
import time
from collections import deque

from .runs import MOVING_SPEED, activity, is_moving, progressing
from .telemetry import PLAN_ERRORS, RECHARGE_ERRORS, code_label

CELL_M = 2.0
MIN_SAMPLES = 8
SHOW = 0.25
STALL_RADIUS_M, STALL_S, GAP_S = 1.0, 180, 60
SPIN_WINDOW_S, SPIN_WHEEL_M, SPIN_MOVE_M = 60, 10.0, 1.5
OWN_MIN_S = 10            # the robot's stuck flag blinks for a second during normal mowing; shorter is not counted
MERGE_M = 3.0
STEEP_DEG = 12
LOAD_SHARE = 0.05
REVERSE_SHARE = 0.4
TILT_BINS = 91
HALOW_WEAK_DBM = -77      # the robot falls back to 4G at -82 and returns to HaLow at -77 or better
RTCM_LATE_S = 10          # correction age above this (or -1, none) means corrections were interrupted
LATE_SHARE = 0.05
ERROR_CODES = {902: "Tilted or flipped over"}
# Codes that say something about the place (the rest are about the plan, the battery, the server...).
PLACE_PLAN = {-20, -23, -88, -26, -30, -40, -42, -43, -44, -47, -92}
PLACE_RECHARGE = {-5, -6, -8, -20}
PLACE_EC = {902}
CAUSES = ("stuck", "error", "steep", "position", "effort")
COLUMNS = ("run_id", "ts", "x", "y", "rtk_status", "heading_dop", "odom_conf", "pitch", "roll", "speed_l", "speed_r",
           "wheel_cur_l", "wheel_cur_r", "stuck", "planning", "planning_paused", "recharging", "error_code",
           "charging", "working_state", "halow", "rtcm_age", "odom_ts")
ON_CHARGER = ("docked", "charging_break")
WHAT_RANK = {"robot reported stuck": 0, "no progress while working": 1, "wheels driving without progress": 2}


def _when(ts: float) -> str:
    return time.strftime("%b %d %H:%M", time.localtime(ts))


def _dur(s: float) -> str:
    return f"{round(s)} s" if s < 90 else f"{round(s / 60)} min"


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _xy(r: dict):
    x, y = _num(r.get("x")), _num(r.get("y"))
    return (x, y) if x is not None and y is not None else None


def _event(kind, ts, p, what, place=True):
    return {"kind": kind, "ts": ts, "end": ts, "x": p[0] if p else None, "y": p[1] if p else None, "what": what,
            "place": place and p is not None}


def _merge_stuck(events: list[dict]) -> list[dict]:
    """One incident seen by several detectors (or flickering) becomes one event: overlapping or within GAP_S, and
    within MERGE_M of each other. Keeps the earliest position and the most specific description."""
    stuck = sorted((e for e in events if e["kind"] == "stuck"), key=lambda e: e["ts"])
    out = []
    for e in stuck:
        cur = out[-1] if out else None
        if cur and e["ts"] <= cur["end"] + GAP_S and math.hypot(e["x"] - cur["x"], e["y"] - cur["y"]) <= MERGE_M:
            cur["end"] = max(cur["end"], e["end"])
            if WHAT_RANK.get(e["what"], 9) < WHAT_RANK.get(cur["what"], 9):
                cur["what"] = e["what"]
        else:
            out.append(dict(e))
    return out + [e for e in events if e["kind"] != "stuck"]


def summarise_run(rows: list[dict]) -> dict:
    """One run's rows (any order) -> {"events": [...], "cells": {(i, j): counters}}."""
    rows = sorted(rows, key=lambda r: r["ts"])
    acts = [activity(r) for r in rows]
    events = []

    # the robot's own stuck signals, while it is not driving its plan
    cur = None
    for r, act in zip(rows, acts):
        p = _xy(r)
        wheels = max(abs(_num(r.get("speed_l")) or 0), abs(_num(r.get("speed_r")) or 0))
        own = ((r.get("planning_paused") == 6 and act == "paused") or r.get("recharging") == -9
               or (bool(r.get("stuck")) and wheels <= MOVING_SPEED))      # flagged, and the wheels really stopped
        if not (own and p):
            continue
        if cur and r["ts"] - cur["end"] <= GAP_S and math.hypot(p[0] - cur["x"], p[1] - cur["y"]) <= MERGE_M:
            cur["end"] = r["ts"]
        else:
            cur = _event("stuck", r["ts"], p, "robot reported stuck")
            events.append(cur)
    events = [e for e in events if e["end"] - e["ts"] >= OWN_MIN_S]

    # stalls: progressing but within STALL_RADIUS_M for STALL_S
    anchor, seen, flagged = None, 0.0, None
    for r, act in zip(rows, acts):
        p = _xy(r)
        prog = p is not None and progressing(r, act)
        if not prog or r["ts"] - seen > GAP_S:
            anchor, flagged = None, None
        seen = r["ts"]
        if not prog:
            continue
        if anchor is None or math.hypot(p[0] - anchor[0], p[1] - anchor[1]) > STALL_RADIUS_M:
            anchor, flagged = (p[0], p[1], r["ts"]), None
        elif r["ts"] - anchor[2] >= STALL_S:
            if flagged is None:
                flagged = _event("stuck", anchor[2], anchor, "no progress while working")
                events.append(flagged)
            flagged["end"] = r["ts"]

    # wheels driving (not pivoting) without getting anywhere, while progressing: O(n) sliding window. Time is the
    # robot's own pose time where stored (rows arriving in a burst after a link gap share almost one arrival time).
    def rt(r):
        return r["odom_ts"] if both_robot_time else r["ts"]
    both_robot_time = bool(rows) and all(r.get("odom_ts") is not None for r in rows)
    win, travel, last, prev = deque(), 0.0, None, None
    for r, act in zip(rows, acts):
        p = _xy(r)
        if p is None or not progressing(r, act) or (prev is not None and abs(rt(r) - rt(prev)) > GAP_S):
            win.clear()
            travel, prev = 0.0, (r if p is not None and progressing(r, act) else None)
            if prev is not None:
                win.append((rt(r), p, 0.0, r["ts"]))
            continue
        dt = min(2.0, max(0.0, rt(r) - rt(prev))) if prev is not None else 0.0
        step = abs(((_num(r.get("speed_l")) or 0) + (_num(r.get("speed_r")) or 0)) / 2) * dt
        win.append((rt(r), p, step, r["ts"]))
        travel += step
        prev = r
        while win and rt(r) - win[0][0] > SPIN_WINDOW_S:
            travel -= win.popleft()[2]
        if travel > SPIN_WHEEL_M and len(win) >= 20 and rt(r) - win[0][0] >= SPIN_WINDOW_S * 0.8:
            if max(math.hypot(q[0] - p[0], q[1] - p[1]) for _, q, _, _ in win) < SPIN_MOVE_M:
                if last and r["ts"] - last["end"] <= 30:
                    last["end"] = r["ts"]
                else:
                    last = _event("stuck", win[0][3], p, "wheels driving without progress")   # event times stay arrival times
                    last["end"] = r["ts"]
                    events.append(last)

    # error codes, once per occurrence, pinned at the last known position
    seen_codes, last_p = {}, None
    for r in rows:
        p = _xy(r)
        if p:
            last_p = p
        for key, table, place in (("planning", PLAN_ERRORS, PLACE_PLAN), ("recharging", RECHARGE_ERRORS, PLACE_RECHARGE)):
            code = _num(r.get(key))
            label = code_label(table, code)
            if label and label != seen_codes.get(key) and code != -9:      # -9 (stuck) is the stuck cause
                events.append(_event("error", r["ts"], last_p, label, int(code) in place))
            seen_codes[key] = label
        ec = int(_num(r.get("error_code")) or 0)
        if ec and ec != seen_codes.get("ec"):
            events.append(_event("error", r["ts"], last_p, f"{ERROR_CODES.get(ec, 'error')} ({ec})", ec in PLACE_EC))
        seen_codes["ec"] = ec

    # per-cell counters. Not on the charger: docked (or a charging break) the robot sits with RTK single and late
    # corrections, which ranked the dock itself as a position spot on run 4 (422 samples).
    cells: dict = {}
    for r, act in zip(rows, acts):
        p = _xy(r)
        if p is None or (act in ON_CHARGER and not is_moving(r)):
            continue
        key = (math.floor(p[0] / CELL_M), math.floor(p[1] / CELL_M))
        c = cells.get(key)
        if c is None:
            c = cells[key] = {"n": 0, "hist": [0] * TILT_BINS, "max": 0.0, "weak": 0, "nofix": 0, "dop": 0, "dr": 0,
                              "rev": 0, "load": 0, "hn": 0, "hsum": 0.0, "late": 0}
        c["n"] += 1
        tilt = max(abs(_num(r.get("pitch")) or 0), abs(_num(r.get("roll")) or 0))
        c["hist"][min(TILT_BINS - 1, int(tilt))] += 1
        c["max"] = max(c["max"], tilt)
        rtk = _num(r.get("rtk_status"))
        nofix, dop = rtk is not None and rtk != 4, (_num(r.get("heading_dop")) or 0) >= 2
        conf = _num(r.get("odom_conf"))
        dr = conf is not None and conf < 0.5
        c["nofix"] += nofix
        c["dop"] += dop
        c["dr"] += dr
        c["weak"] += nofix or dop or dr
        halow, age = _num(r.get("halow")), _num(r.get("rtcm_age"))
        if halow is not None and halow < 0:
            c["hn"] += 1
            c["hsum"] += halow
        c["late"] += age is not None and (age < 0 or age > RTCM_LATE_S)
        sl, sr = _num(r.get("speed_l")) or 0, _num(r.get("speed_r")) or 0
        c["rev"] += sl < -0.05 and sr < -0.05
        cur_a = max(abs(_num(r.get("wheel_cur_l")) or 0), abs(_num(r.get("wheel_cur_r")) or 0))
        c["load"] += cur_a > 3 and (abs(sl) + abs(sr)) / 2 < 0.15   # both wheels slow (a pivot turn is not)
    return {"events": sorted(_merge_stuck(events), key=lambda e: e["ts"]), "cells": cells}


def _p90(hist: list[int], n: int) -> int:
    target, acc = 0.9 * n, 0
    for deg, k in enumerate(hist):
        acc += k
        if acc >= target:
            return deg
    return len(hist) - 1


COUNTERS = ("n", "weak", "nofix", "dop", "dr", "rev", "load", "hn", "hsum", "late")


def combine(summaries: list[dict]) -> dict:
    """Merge run summaries and score every cell. Returns {cells, spots, events}."""
    cells: dict = {}
    events = []
    for idx, s in enumerate(summaries):
        events += s["events"]
        for key, c in s["cells"].items():
            m = cells.get(key)
            if m is None:
                m = cells[key] = {**{k: 0 for k in COUNTERS}, "hist": [0] * TILT_BINS, "max": 0.0, "runs": set()}
            for k in COUNTERS:
                m[k] += c.get(k, 0)
            m["hist"] = [a + b for a, b in zip(m["hist"], c["hist"])]
            m["max"] = max(m["max"], c["max"])
            m["runs"].add(idx)
    events = sorted(events, key=lambda e: e["ts"])
    ev_cells: dict = {}
    for e in events:
        if e["place"]:
            ev_cells.setdefault((math.floor(e["x"] / CELL_M), math.floor(e["y"] / CELL_M)), []).append(e)

    out = []
    for key in {k for k, c in cells.items() if c["n"] >= MIN_SAMPLES} | set(ev_cells):
        c = cells.get(key)
        sev, ev = {}, {}
        here = ev_cells.get(key, [])
        stuck = [e for e in here if e["kind"] == "stuck"]
        if stuck:
            seconds = sum(e["end"] - e["ts"] for e in stuck)
            sev["stuck"] = min(1.0, 0.6 + 0.1 * len(stuck) + seconds / 1800)
            longest = max(stuck, key=lambda e: e["end"] - e["ts"])
            ev["stuck"] = (f"{longest['what']}, {_dur(longest['end'] - longest['ts'])} on {_when(longest['ts'])}"
                           + (f" (+{len(stuck) - 1} more)" if len(stuck) > 1 else ""))
        errors = [e for e in here if e["kind"] == "error"]
        if errors:
            sev["error"] = 1.0
            ev["error"] = "; ".join(f"{e['what']} ({_when(e['ts'])})" for e in errors[:3])
        stats = None
        if c and c["n"]:
            n = c["n"]
            over12 = sum(c["hist"][STEEP_DEG:]) / n          # bin 12 holds [12°, 13°)
            stats = {"samples": n, "max_tilt": round(c["max"]), "over12_pct": round(100 * over12),
                     "nofix_pct": round(100 * c["nofix"] / n), "dop2_pct": round(100 * c["dop"] / n),
                     "dr_pct": round(100 * c["dr"] / n), "late_pct": round(100 * c["late"] / n),
                     "halow_dbm": round(c["hsum"] / c["hn"]) if c["hn"] else None}
            if n >= MIN_SAMPLES:
                s = max(0.0, min(1.0, (_p90(c["hist"], n) - 10) / 20))
                if s > 0:
                    sev["steep"] = s
                    ev["steep"] = f"steepest {round(c['max'])}°, {round(100 * over12)} % of samples at {STEEP_DEG}° or more"
                s = max(0.0, min(1.0, (c["weak"] / n - 0.1) / 0.4))
                if s > 0:
                    sev["position"] = s
                    parts = [f"no RTK fix {round(100 * c['nofix'] / n)} %"]
                    if c["dop"]:
                        parts.append(f"heading DOP ≥ 2 {round(100 * c['dop'] / n)} %")
                    if c["dr"]:
                        parts.append(f"dead reckoning {round(100 * c['dr'] / n)} %")
                    if stats["halow_dbm"] is not None and stats["halow_dbm"] <= HALOW_WEAK_DBM:
                        parts.append(f"weak HaLow {stats['halow_dbm']} dBm")
                    if c["late"] / n >= LATE_SHARE:
                        parts.append(f"corrections late {stats['late_pct']} %")
                    ev["position"] = " · ".join(parts)
                load, rev = c["load"] / n, c["rev"] / n
                s = max((load - LOAD_SHARE) / 0.15 + 0.25 if load > LOAD_SHARE else 0.0,
                        (rev - REVERSE_SHARE) / 0.3 + 0.25 if rev > REVERSE_SHARE else 0.0)
                if s > 0:
                    sev["effort"] = min(1.0, s)
                    ev["effort"] = f"heavy wheel load {round(100 * load)} %, reversing {round(100 * rev)} % of the time"
        shown = {k: v for k, v in sev.items() if v >= SHOW}
        if not shown:
            continue
        cause = next((k for k in ("stuck", "error") if k in shown), max(shown, key=shown.get))
        out.append({"i": key[0], "j": key[1], "cell_m": CELL_M, "cause": cause, "severity": round(shown[cause], 2),
                    "causes": {k: round(v, 2) for k, v in shown.items()}, "evidence": {k: ev[k] for k in shown},
                    "stats": stats, "runs": len(c["runs"]) if c else 0})
    return {"cells": out, "spots": spots(out), "events": events}


def analyse(rows: list[dict]) -> dict:
    """rows of one or more runs -> {cells, spots, events} (summarise each run, then combine)."""
    by_run: dict = {}
    for r in rows:
        by_run.setdefault(r.get("run_id"), []).append(r)
    return combine([summarise_run(rr) for rr in by_run.values()])


def spots(cells: list[dict]) -> list[dict]:
    """Neighbouring cells with the same cause, as ranked spots (worst first)."""
    by = {(c["i"], c["j"]): c for c in cells}
    seen, out = set(), []
    for key, c in by.items():
        if key in seen:
            continue
        group, todo = [], [key]
        seen.add(key)
        while todo:
            k = todo.pop()
            group.append(by[k])
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    n = (k[0] + di, k[1] + dj)
                    if n in by and n not in seen and by[n]["cause"] == c["cause"]:
                        seen.add(n)
                        todo.append(n)
        worst = max(group, key=lambda g: g["severity"])
        also = {}
        for g in group:                         # the other causes seen in this spot, strongest evidence of each
            for k, v in g["causes"].items():
                if k != c["cause"] and v > also.get(k, (0, ""))[0]:
                    also[k] = (v, g["evidence"][k])
        out.append({"cause": c["cause"], "severity": worst["severity"], "cells": len(group),
                    "area_m2": round(len(group) * CELL_M * CELL_M), "evidence": worst["evidence"][c["cause"]],
                    "also": {k: e for k, (v, e) in sorted(also.items(), key=lambda kv: -kv[1][0])},
                    "stats": worst.get("stats"),
                    "x": sum((g["i"] + 0.5) * CELL_M for g in group) / len(group),
                    "y": sum((g["j"] + 0.5) * CELL_M for g in group) / len(group)})
    rank = {k: i for i, k in enumerate(CAUSES)}
    out.sort(key=lambda s: (rank[s["cause"]] if s["cause"] in ("stuck", "error") else 2, -s["severity"], -s["cells"]))
    return out
