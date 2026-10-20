"""DEMO=1: a week of history for the made-up yard (app/demo_robot.py), written once into a fresh DATA_DIR.

Three finished mows over the last week (one with a skipped patch, so the gap finder has something to show), the
first 45 % of the mow that is "in progress" when the panel starts (the demo robot carries on from there and the
recorder joins it onto this run, as it does after a restart mid-plan), events, Wi-Fi survey scans, three days of
weather, two schedule windows, maintenance items part-way through their intervals, and the imagery source for the
demo yard. Everything is made up; nothing comes from a robot. Seeded with fixed random seeds, so two fresh demos
look the same apart from the clock.
"""
import json
import math
import time
from datetime import datetime

from . import demo_robot as dr, geo, maintenance, scheduler, telemetry
from .runs import RunStats

SEEDED = "demo_seeded"          # setting: the history is in (never seeded twice)
SIM = "demo_sim"                # setting: where the in-progress mow was when it was seeded
IMAGERY = {"provider": "or_portland_2025", "mode": "manual"}     # the city's own summer photo, 15 cm, no key
PROGRESS = 0.45                 # how far the in-progress mow is when the panel starts
# (days ago, local start hour, plan id, stripe angle offset, skipped patch (x0, y0, x1, y1) or None)
PAST = [(6, 9.5, 1, 0, None), (4, 16.0, 2, 90, None), (2, 10.25, 1, 90, (-24.0, -26.0, -18.0, -19.5))]


def _local(days_ago: float, hour: float, now: float) -> float:
    day = datetime.fromtimestamp(now - days_ago * 86400).replace(hour=0, minute=0, second=0, microsecond=0)
    return day.timestamp() + hour * 3600


def _event(store, ts: float, kind: str, detail: str) -> None:
    store.db.execute("INSERT INTO events (ts, kind, detail) VALUES (?, ?, ?)", (ts, kind, detail))


def drive(store, robot: "dr.DemoRobot", run_id: int, s_from: float, s_to: float, t0: float, skip=None,
          wifi: bool = True) -> tuple[list[dict], float]:
    """Rows for the route from s_from to s_to, one a second from t0, as the recorder would have written them."""
    route, rows, ts, s = robot.route, [], t0, s_from
    robot.phase = "route"
    while s <= s_to:
        robot.s, robot.pose = s, route.at(s)
        robot.work_s = s / dr.SPEED
        robot.battery = max(5.0, 100.0 - 0.42 * robot.work_s / 60)
        robot.odo += dr.SPEED
        p = robot.pose
        dropped = skip and p["mode"] == dr.MOW and skip[0] <= p["x"] <= skip[2] and skip[1] <= p["y"] <= skip[3]
        if not dropped:
            t = telemetry.extract(robot.snapshot(ts, full=False))
            t.pop("cell_temps", None)
            t["lon"], t["lat"] = geo.to_lonlat(dr.REF, t["x"], t["y"])
            t.update(ts=ts, run_id=run_id)
            store.add_telemetry(t)
            rows.append(t)
            if wifi and len(rows) % 30 == 1:
                nets = robot.wifi()["wifi_list"]
                store.add_wifi_scan(ts, run_id, p["x"], p["y"],
                                    [(n["wifi_name"], float(n["signal"]), n["saved"]) for n in nets])
        ts += 1.0
        s += dr.SPEED * 1.0
    return rows, ts


def _close(store, run_id: int, rows: list[dict], ended: float, reason: str) -> None:
    stats = RunStats.from_rows(rows)
    store.end_run(run_id, ended, reason, stats)


def weather_row(ts: float) -> dict:
    """A mild, dry autumn spell with one shower two days back (made up, deterministic in ts)."""
    hour = datetime.fromtimestamp(ts).hour + datetime.fromtimestamp(ts).minute / 60
    day = math.sin((hour - 9) / 24 * 2 * math.pi)
    temp = 15.5 + 6.5 * day + 0.8 * math.sin(ts / 86400 * 1.3)
    rh = 62 - 16 * day
    sun = max(0.0, math.sin((hour - 7) / 12 * math.pi)) if 7 <= hour <= 19 else 0.0
    shower = 0.0
    ago = (time.time() - ts) / 3600
    if 50 <= ago <= 52:
        shower = 0.35
    return {"ts": ts, "temp_c": round(temp, 1), "rh": round(min(95, rh + (25 if shower else 0)), 1),
            "rain_mm": shower, "precip_type": 1 if shower else 0, "solar": round(620 * sun), "uv": round(4 * sun, 1),
            "wind_avg": round(1.2 + 1.5 * abs(math.sin(ts / 5400)), 1),
            "wind_gust": round(2.5 + 2.5 * abs(math.sin(ts / 5400)), 1), "pressure": 1016.5}


def prepare(store, now: float | None = None) -> dict:
    """Seed a fresh demo DATA_DIR (once) and return where the in-progress mow stands (SIM)."""
    now = now or time.time()
    if store.setting(SEEDED):
        return store.setting(SIM) or {}
    if store.runs(1) or store.latest_version("map") or store.db.execute("SELECT 1 FROM telemetry LIMIT 1").fetchone():
        raise RuntimeError("DEMO=1 needs a DATA_DIR of its own: this one already holds a robot's data")
    store.db.execute("BEGIN")
    try:
        if store.setting("imagery_source") is None:
            store.set_setting("imagery_source", IMAGERY)
        _schedule(store, now)
        for days, hour, plan_id, angle, skip in PAST:
            _past_run(store, _local(days, hour, now), plan_id, angle, skip, days)
        sim = _current_run(store, now)
        for k in range(72 * 12, 0, -1):
            store.add_weather(weather_row(now - k * 300))
        _event(store, now - 51 * 3600, "rain_started", "a shower (demo weather)")
        _maintenance(store, now)
        _event(store, now - 1, "demo_seeded", "a week of synthetic history for the demo yard")
        store.set_setting(SIM, sim)
        store.set_setting(SEEDED, True)
        store.db.execute("COMMIT")
    except Exception:
        store.db.execute("ROLLBACK")
        raise
    return sim


def _past_run(store, start: float, plan_id: int, angle: float, skip, days: int) -> None:
    robot = dr.DemoRobot(start, plan_id=0, seed=100 + days)
    robot.plan = next(p for p in dr.PLANS if p["id"] == plan_id)
    robot.route = dr.Route(robot.plan, angle_offset=angle)
    run_id = store.start_run(start, 5, robot.plan["name"])
    rows, ended = drive(store, robot, run_id, 0.0, robot.route.total, start, skip=skip)
    store.set_run_plan(run_id, f"{plan_id}:{int(start)}", 100.0)
    for aid in robot.plan["areaIds"]:
        store.save_version(f"plan_path:{run_id}:{aid}",
                           {"ts": start + 60, "area_id": aid, "path": robot.route.planned_path(aid)})
    _close(store, run_id, rows, ended, "plan finished")
    dist = round(robot.route.total)
    _event(store, start, "run_started", f"run {run_id} (Mower Pro)")
    _event(store, ended, "run_ended", f"run {run_id}: plan finished, {dist} m")
    window = (store.setting("schedule_windows") or [{}])[0 if plan_id == 1 else -1]
    if window.get("id"):
        date = datetime.fromtimestamp(start).strftime("%Y-%m-%d")
        store.db.execute("INSERT INTO schedule_log (ts, window_id, date, outcome, detail) VALUES (?, ?, ?, ?, ?)",
                         (start - 300, window["id"], date, "confirmed", json.dumps({"result": "sent"})))


def _current_run(store, now: float) -> dict:
    """The first PROGRESS of today's "Mow All", ending now; the demo robot carries on from there."""
    robot = dr.DemoRobot(now, plan_id=0, seed=1)
    robot.plan = dr.PLANS[0]
    robot.route = dr.Route(robot.plan)
    s0 = robot.route.s_for_progress(PROGRESS)
    start = now - s0 / dr.SPEED
    run_id = store.start_run(start, 5, robot.plan["name"])
    rows, _ = drive(store, robot, run_id, 0.0, s0, start)
    store.set_run_plan(run_id, f"1:{int(start)}", round(100 * PROGRESS, 1))
    # Closed as a restart would leave it; the recorder reopens it when the robot's plan session continues.
    _close(store, run_id, rows, rows[-1]["ts"], "recorder restarted")
    _event(store, start, "run_started", f"run {run_id} (Mower Pro)")
    return {"session_start": int(start), "plan_id": 1, "progress": PROGRESS, "at": now}


def _schedule(store, now: float) -> None:
    if store.setting("schedule_windows"):
        return
    store.set_setting("schedule_windows", [
        scheduler.validate(scheduler.new_window(name="Weekday mornings", days=[0, 2, 4], start="09:00", end="13:00",
                                                plan_id=1)),
        scheduler.validate(scheduler.new_window(name="Front lawn, weekend", days=[5, 6], start="15:00", end="18:00",
                                                plan_id=2))])


def _maintenance(store, now: float) -> None:
    items = maintenance.default_items({})
    ago = {"blades": 80, "track_grease": 9, "lenses": 20}
    for it in items:
        days = ago.get(it.get("key"))
        if days:
            it["done_at"] = now - days * 86400
            it["baseline"] = round(it["done_at"] / 86400, 3) if it["unit"] == "days" else 0
    store.set_setting("maintenance_items", items)
    store.set_setting("maintenance_defaults", maintenance.DEFAULTS_VERSION)
    _event(store, now - 9 * 86400, "maintenance_done", "Tracks: grease")
