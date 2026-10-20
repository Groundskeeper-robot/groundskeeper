"""Maintenance timers (spec §15). Counters come from recorded telemetry. Each default item starts at Yarbo's own
recommended interval where Yarbo gives a number (RECOMMENDED, with its source); where Yarbo only says "regularly" the
interval starts empty. "Done" stores the counter value at that moment; progress is the distance since then."""
import time
import uuid

UNITS = {
    "blade_hours": "blade-motor hours",
    "distance_km": "km of runs",
    "mows": "mower runs",
    "runs": "runs (any head)",
    "charge_cycles": "charge cycles",
    "days": "days",
    "head_change": "on head change",
}

# Yarbo's recommendations (wiki.yarbo.com/maintenance/*, forum.yarbo.com staff posts; read 2026-10-09).
# key -> (name, unit, interval or None when Yarbo gives no number, what Yarbo says, source)
RECOMMENDED = {
    "blades": ("Blades: replace", "days", 90,
               "Yarbo: every three months, sooner in thick grass; disc blades can be flipped once",
               "https://wiki.yarbo.com/maintenance/guide"),
    "track_grease": ("Tracks: grease", "days", 14, "Yarbo: grease the tracks every two weeks",
                     "https://forum.yarbo.com/t/how-often-do-you-grease-the-tracks/5474"),
    "deck": ("Deck cleaning", "mows", None, "Yarbo: inspect the cutting disc area regularly for grass and debris",
             "https://wiki.yarbo.com/maintenance/guide"),
    "tracks": ("Wheels and treads: clean and check", "distance_km", None,
               "Yarbo: clean the underside of the tracks regularly; check for cracks and loose axles",
               "https://wiki.yarbo.com/maintenance/guide"),
    "lenses": ("Camera and lidar lenses", "days", None, "Yarbo: wipe the lenses to keep obstacle detection working",
               "https://wiki.yarbo.com/maintenance/routine-maintenance"),
    "battery": ("Battery health review", "charge_cycles", 500, "Yarbo: rated for 500 cycles at 80 % capacity",
                "https://www.yarbo.com/products/yarbo-battery"),
    "season": ("Season service (head change)", "head_change", 1, "Yarbo: a system check before each season",
               "https://wiki.yarbo.com/maintenance/routine-maintenance"),
}
# Names the first defaults had (before RECOMMENDED), so an install from then is matched to its key.
OLD_NAMES = {"Blades: inspect or replace": "blades", "Deck cleaning": "deck", "Wheels and treads": "tracks",
             "Camera and lidar lenses": "lenses", "Battery health review": "battery",
             "Season service (head change)": "season"}
DEFAULTS_VERSION = 1


def _baseline(unit: str, counters: dict, done_at: float) -> float:
    return round(done_at / 86400, 3) if unit == "days" else counters.get(unit, 0)


def default_items(counters: dict) -> list[dict]:
    now = time.time()
    return [{"id": uuid.uuid4().hex[:8], "key": key, "name": name, "unit": unit, "interval": interval, "done_at": now,
             "baseline": _baseline(unit, counters, now), "note": ""}
            for key, (name, unit, interval, _, _) in RECOMMENDED.items()]


def upgrade(items: list[dict], counters: dict) -> list[dict]:
    """Bring an install made before the recommendations up to them, once: a default item whose interval was never
    set takes Yarbo's (and its unit); one with the user's own interval keeps it; missing defaults are added. Items
    the user made are untouched."""
    out, seen = [], set()
    for it in items:
        key = it.get("key") or OLD_NAMES.get(it.get("name"))
        if key in RECOMMENDED and key not in seen:
            seen.add(key)
            name, unit, interval, _, _ = RECOMMENDED[key]
            it = {**it, "key": key}
            if it.get("interval") is None:
                it.update(name=name, interval=interval)
                if unit != it.get("unit"):                   # a new count: from now, not from install day
                    now = time.time()
                    it.update(unit=unit, done_at=now, baseline=_baseline(unit, counters, now))
        out.append(it)
    now = time.time()
    for key, (name, unit, interval, _, _) in RECOMMENDED.items():
        if key not in seen:
            out.append({"id": uuid.uuid4().hex[:8], "key": key, "name": name, "unit": unit, "interval": interval,
                        "done_at": now, "baseline": _baseline(unit, counters, now), "note": ""})
    return out


def counters_from(store, head_changes: int) -> dict:
    db = store.db
    blade_s = 0.0
    prev = None
    for ts, run_id, rpm_l, rpm_r in db.execute(
            "SELECT ts, run_id, blade_rpm_l, blade_rpm_r FROM telemetry WHERE run_id IS NOT NULL ORDER BY ts"):
        if prev and prev[1] == run_id and ts - prev[0] <= 5 and ((prev[2] or 0) > 0 or (prev[3] or 0) > 0):
            blade_s += ts - prev[0]
        prev = (ts, run_id, rpm_l, rpm_r)
    distance = db.execute("SELECT COALESCE(SUM(distance_m), 0) FROM runs").fetchone()[0] or 0
    mows = db.execute("SELECT COUNT(*) FROM runs WHERE head_type IN (3, 5)").fetchone()[0]
    runs = db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    cycles, last = 0, None
    for (charging,) in db.execute("SELECT charging FROM telemetry WHERE charging IS NOT NULL ORDER BY ts"):
        on = charging > 0
        if on and last is False:
            cycles += 1
        last = on
    return {"blade_hours": round(blade_s / 3600, 2), "distance_km": round(distance / 1000, 2), "mows": mows,
            "runs": runs, "charge_cycles": cycles, "days": round(time.time() / 86400, 3), "head_change": head_changes}


def status(items: list[dict], counters: dict) -> list[dict]:
    out = []
    for it in items:
        used = counters.get(it["unit"], 0) - (it.get("baseline") or 0)
        interval = it.get("interval")
        pct = None if not interval else round(100 * used / interval, 1)
        due = "set an interval" if not interval else "overdue" if pct >= 100 else "due soon" if pct >= 85 else "ok"
        if it["unit"] == "head_change":
            pct, due = (100.0, "due") if used >= 1 else (0.0, "ok")
        rec = RECOMMENDED.get(it.get("key"))
        recommended = None if not rec else {"unit": rec[1], "interval": rec[2], "says": rec[3], "source": rec[4]}
        out.append({**it, "used": round(used, 2), "pct": pct, "due": due, "unit_label": UNITS.get(it["unit"], it["unit"]),
                    "recommended": recommended})
    return out
