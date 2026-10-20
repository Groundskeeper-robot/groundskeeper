"""Run detection, driven by the robot's own plan state.

A run is one plan session: from the moment the robot starts moving until its plan is over. It survives a pause
(guard, phone app or panel) and a mid-plan charging break, so a resume continues the same run instead of opening a
new one. The signals, checked against the 2026-10-05 recorded runs:

* StateMSG.planning_paused: 0 = no paused plan, 1 = paused (stays 1 while the paused plan waits, even after
  working_state drops to 0), 2 = parked on the charger in the middle of a plan (it goes back out afterwards);
  also 3 power restart, 4 e-stop, 5 bumper, 6 stuck, 7 fault, 9 rain (telemetry.PAUSE_REASONS).
* StateMSG.on_going_planning: 1 = mowing, 3 = driving between areas / along a pathway, 2 = planning a route.
* StateMSG.on_going_recharging: 1 = driving to the dock, 3 = docking, 4 = on the charger.
* StateMSG.car_controller: True while the plan drives the wheels, False while paused.
* get_plan_feedback (a read request): state 0 with the plan's progress while a plan is in progress, state -1
  "no plan working" otherwise. Its planId + startTime identify the plan session.

Without plan flags (older firmware, missing fields) it falls back to movement: a run ends after idle_end_s without
movement, or CHARGER_GRACE_S after the robot is back on its charger.
"""
from dataclasses import dataclass, field

MOVING_SPEED = 0.05      # m/s on either wheel
CHARGER_GRACE_S = 30
PAUSE_CAP_S = 3 * 3600   # a plan paused (or parked mid-plan) longer than this closes the run anyway

# activity -> label shown on the panel's state chip (and Home Assistant's state sensor)
ACTIVITY_LABELS = {
    "mowing": "mowing",
    "transit": "between areas",
    "planning": "planning route",
    "paused": "paused",
    "charging_break": "charging mid-plan",
    "returning": "returning to dock",
    "docking": "docking",
    "moving": "moving",
    "docked": "docked, charging",
    "idle": "idle",
}


def _int(v) -> int | None:
    return int(v) if isinstance(v, (int, float)) else None


def is_moving(t: dict) -> bool:
    speeds = [abs(v) for v in (t.get("speed_l"), t.get("speed_r")) if v is not None]
    if speeds and max(speeds) > MOVING_SPEED:
        return True
    planning = t.get("planning")
    return bool(planning) and planning > 0 and not t.get("planning_paused")


def on_charger(t: dict) -> bool:
    charging = t.get("charging")
    return charging is not None and charging > 0


def activity(t: dict) -> str:
    """What the robot is doing right now, from its own state flags (see the module docstring)."""
    paused, planning, recharging = _int(t.get("planning_paused")), _int(t.get("planning")), _int(t.get("recharging"))
    if paused == 2:
        return "charging_break"
    if recharging in (1, 2):         # 1 returning along a pathway, 2 returning inside an area
        return "returning"
    if recharging in (3, 99):        # 3 repositioning onto the dock, 99 verifying
        return "docking"
    if paused and paused > 0:        # 1 manual, 3 power restart, 4 e-stop, 5 bumper, 6 stuck, 7 fault, 9 rain
        # A reason code other than 1 can linger after the robot carried on by itself; wheels driving the plan win.
        speeds = [abs(v) for v in (t.get("speed_l"), t.get("speed_r")) if v is not None]
        driving = planning in (1, 3) and bool(speeds) and max(speeds) > MOVING_SPEED
        if paused == 1 or not driving:
            return "paused"
    if planning == 1:
        return "mowing"
    if planning == 3:
        return "transit"
    if planning == 2:
        return "planning"
    if on_charger(t):
        return "docked"
    if is_moving(t):
        return "moving"
    return "idle"


PROGRESSING = {"mowing", "transit", "returning"}


def progressing(t: dict, act: str | None = None) -> bool:
    """Should the robot be getting somewhere? Mowing, between areas or heading home, and not on the dock (it can wait
    on the charger between areas with planning 3, or reposition / verify while docking)."""
    act = act or activity(t)
    return act in PROGRESSING and not on_charger(t) and _int(t.get("recharging")) not in (3, 4, 99)


def plan_flags_active(t: dict) -> bool | None:
    """True while a plan is part-way through (mowing, between areas, paused, parked mid-plan); False when the flags
    say no plan; None when the robot does not report them."""
    paused, planning = _int(t.get("planning_paused")), _int(t.get("planning"))
    if paused is None and planning is None:
        return None
    return (paused or 0) > 0 or (planning or 0) > 0


@dataclass
class PlanStatus:
    """The useful part of a get_plan_feedback reply (the path arrays are dropped: they are ~28 KB)."""
    active: bool
    session: str | None = None           # "{planId}:{startTime}", the same across pauses, resumes and charge breaks
    plan_id: int | None = None
    started: float | None = None
    area_ids: list = field(default_factory=list)
    current_area: int | None = None
    finished_areas: list = field(default_factory=list)
    done_m2: float | None = None
    total_m2: float | None = None
    left_s: float | None = None
    msg: str = ""

    @property
    def progress_pct(self) -> float | None:
        if self.done_m2 is None or not self.total_m2:
            return None
        return round(min(100.0, 100 * self.done_m2 / self.total_m2), 1)

    @property
    def complete(self) -> bool:
        """Best effort: at least 90 % of the plan's area done; only when the robot gives no progress, every area of
        the plan reported finished. finishIds alone is not trusted: run 4's area 11 was listed finished while it was
        still being mowed (15:00-15:15), which would have ended a one-area plan as "plan finished"."""
        pct = self.progress_pct
        if pct is not None:
            return pct >= 90
        return bool(self.area_ids) and set(self.area_ids) <= set(self.finished_areas)

    def view(self) -> dict:
        return {"active": self.active, "session": self.session, "plan_id": self.plan_id, "started": self.started,
                "area_ids": self.area_ids, "current_area": self.current_area, "finished_areas": self.finished_areas,
                "progress_pct": self.progress_pct, "left_s": self.left_s, "msg": self.msg}


def parse_plan_feedback(reply: dict | None) -> PlanStatus | None:
    """A get_plan_feedback data_feedback body -> PlanStatus; None when the reply is not one we understand."""
    if not isinstance(reply, dict):
        return None
    state, data = reply.get("state"), reply.get("data")
    if state == -1:
        return PlanStatus(active=False, msg=str(reply.get("msg") or "").strip())
    if state != 0 or not isinstance(data, dict):
        return None
    pid, start = _int(data.get("planId")), data.get("startTime")
    has_start = isinstance(start, (int, float))
    finished = [f.get("id") for f in data.get("finishIds") or [] if isinstance(f, dict)]

    def num(k):
        v = data.get(k)
        return float(v) if isinstance(v, (int, float)) else None

    return PlanStatus(active=True, session=f"{pid}:{int(start)}" if pid is not None and has_start else None,
                      plan_id=pid, started=float(start) if has_start else None,
                      area_ids=[a for a in data.get("areaIds") or [] if isinstance(a, int)],
                      current_area=_int(data.get("cleanAreaId")), finished_areas=finished,
                      done_m2=num("finishCleanArea"), total_m2=num("totalCleanArea"), left_s=num("leftTime"),
                      msg=str(reply.get("msg") or "").strip())


HELD = {"paused", "charging_break"}     # parked mid-plan (the recorder's HELD)
PLAN_BLIP_S = 30     # a plan that stops showing for less than this, while the robot was held mid-plan, is a blip


@dataclass
class RunDetector:
    idle_end_s: float = 300
    open: bool = False
    started: float = 0.0
    last_moving: float = 0.0
    held_since: float | None = None      # when the robot went still with its plan still in progress
    paused_out: bool = False             # the last end was the PAUSE_CAP_S timeout
    unheld_at: float | None = None       # when a held robot's plan stopped showing as in progress

    def update(self, ts: float, t: dict, plan_active: bool | None = None) -> str | None:
        """Returns 'start', 'end' or None. plan_active: is a plan in progress (Recorder.plan_active)."""
        if is_moving(t):
            self.last_moving = ts
            self.held_since = self.unheld_at = None
            if not self.open:
                self.open, self.started, self.paused_out = True, ts, False
                return "start"
            return None
        if not self.open:
            return None
        if plan_active:
            # Paused or parked mid-plan: the run stays open (a resume continues it), up to PAUSE_CAP_S.
            self.unheld_at = None
            if self.held_since is None:
                self.held_since = ts
            if ts - self.held_since < PAUSE_CAP_S:
                return None
            self.open, self.held_since, self.paused_out = False, None, True
            return "end"
        if self.held_since is not None:
            # It was held mid-plan a moment ago. Leaving a charging break the robot shows "no plan" for a few
            # seconds before it drives out (run 4, 20:36): that blip must not end the run.
            if self.unheld_at is None:
                self.unheld_at = ts
            if ts - self.unheld_at < PLAN_BLIP_S:
                return None
            self.held_since = self.unheld_at = None
        idle = ts - self.last_moving
        if idle >= self.idle_end_s or (on_charger(t) and idle >= CHARGER_GRACE_S):
            self.open, self.held_since = False, None
            return "end"
        return None


@dataclass
class RunStats:
    points: int = 0
    fixed: int = 0
    distance_m: float = 0.0
    battery_start: float | None = None
    battery_end: float | None = None
    max_error: int = 0
    last_xy: tuple[float, float] | None = field(default=None, repr=False)

    def add(self, t: dict) -> None:
        self.points += 1
        if t.get("rtk_status") == 4:
            self.fixed += 1
        if t.get("battery") is not None:
            if self.battery_start is None:
                self.battery_start = t["battery"]
            self.battery_end = t["battery"]
        if t.get("error_code"):
            self.max_error = max(self.max_error, int(t["error_code"]))
        x, y = t.get("x"), t.get("y")
        if x is not None and y is not None:
            if self.last_xy is not None:
                step = ((x - self.last_xy[0]) ** 2 + (y - self.last_xy[1]) ** 2) ** 0.5
                if step < 5:  # ignore pose jumps (relocalisation)
                    self.distance_m += step
            self.last_xy = (x, y)

    @property
    def fixed_pct(self) -> float | None:
        return round(100 * self.fixed / self.points, 1) if self.points else None

    @classmethod
    def from_rows(cls, rows: list[dict]) -> "RunStats":
        """A run's totals worked out again from its stored telemetry: in the robot's own pose order (odom_ts, else
        arrival), one row per pose, and a pose that is out of place and back (A -> B -> A) left out of the distance."""
        stats, driving = cls(), []
        for r in rows:
            if activity(r) in HELD and not is_moving(r):     # parked mid-plan: as the live counters, only errors
                if r.get("error_code"):
                    stats.max_error = max(stats.max_error, int(r["error_code"]))
                continue
            driving.append(r)
            stats.add({k: v for k, v in r.items() if k not in ("x", "y")})
        stats.distance_m = path_length(driving)               # parked position jitter is not driving either
        return stats

    @classmethod
    def from_run(cls, run: dict) -> "RunStats":
        """Carry on counting into a stored run (a resume of the same plan session joins it)."""
        points = int(run.get("points") or 0)
        return cls(points=points, fixed=round(points * (run.get("rtk_fixed_pct") or 0) / 100),
                   distance_m=float(run.get("distance_m") or 0), battery_start=run.get("battery_start"),
                   battery_end=run.get("battery_end"), max_error=int(run.get("max_error") or 0))


def pose_order(rows: list[dict]) -> list[dict]:
    """Rows with a position, in the robot's pose order, one per pose (the same odom_ts again is the same pose).
    Rows without odom_ts (recorded before it was stored) keep their arrival order."""
    out, seen = [], set()
    pos = [r for r in rows if r.get("x") is not None and r.get("y") is not None]
    if any(r.get("odom_ts") is None for r in pos):
        return pos
    for r in sorted(pos, key=lambda r: r["odom_ts"]):
        if r["odom_ts"] not in seen:
            seen.add(r["odom_ts"])
            out.append(r)
    return out


SPIKE_M = 0.5        # a pose this far off both its neighbours, which are close to each other, is out of place
JUMP_M = 5.0         # a step this long is a relocalisation, not driving


def path_length(rows: list[dict]) -> float:
    pts = [(r["x"], r["y"]) for r in pose_order(rows)]
    d = lambda a, b: ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5    # noqa: E731
    keep = [p for i, p in enumerate(pts)
            if not (0 < i < len(pts) - 1 and d(pts[i - 1], p) > SPIKE_M and d(p, pts[i + 1]) > SPIKE_M
                    and d(pts[i - 1], pts[i + 1]) < 0.75 * min(d(pts[i - 1], p), d(p, pts[i + 1])))]
    return sum(s for s in (d(a, b) for a, b in zip(keep, keep[1:])) if s < JUMP_M)


def end_reason(t: dict, stats: RunStats, plan: "PlanStatus | None" = None, paused_out: bool = False) -> str:
    if stats.max_error:
        return f"error {stats.max_error}"
    if paused_out:
        return "paused, not resumed"
    if plan is not None and plan.complete:
        return "plan finished"
    if on_charger(t):
        return "returned to dock"
    return "stopped"
