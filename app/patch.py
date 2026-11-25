"""Mow a patch (ADR 0013, #183): a temporary rectangle area inside one area, mowed once and removed afterwards.

The robot has no "mow part of an area" command, so the panel adds an area of its own and a plan holding only it,
starts that plan like any other, and deletes both once the robot is back on its charger with no plan. Every command
is one captured from the official app (docs/dev/map-writes.md): `save_clean_area` without an id (a new area),
`save_mower_area_params`, `save_plan`, `del_plan`, `del_clean_area`. They all go through `MapWriter` under its lock,
with the map-writing switch on, docked and idle, and every step is read back.

The patch's stripes line up with the last mow (ADR 0013): the robot measures an area's route angle from its outline's
long side and turns its lines 90° from one mow to the next, so the panel takes the line direction and position from
the last recorded run over the patch (or, with no run, from the surrounding area's own route preview), lays the
rectangle along those lines, and after the robot has computed the patch's route moves the rectangle across the lines
(less than one gap) until the two line up. Boundary laps are off (`edge_circles` 0) and the boundary is not extended.

Geometry here is in the panel's frame (metres from gps_ref, x west, y north); outlines sent to the robot are put in
the surrounding area's own frame (its `ref`), which the patch shares.

State lives in the setting `patch_mow` and is written BEFORE the first command, so a restart mid-way still removes
what was added. The robot reuses freed ids: nothing is deleted unless it has the patch's name and was not on the robot before the
patch; the plan goes first, the area only once the plan is gone, and anything left keeps the state for another try."""
import asyncio
import copy
import math
import time

from . import areasettings, geo, mapstudio, mapwrite
from .backups import params_payload
from .control import PATCH_START
from .mapwrite import AREA_SAVE, PLAN_DELETE, PLAN_SAVE, WriteRefused

STATE = "patch_mow"
NAME = "Patch (temporary)"
AREA_DELETE = "del_clean_area"                 # {"id": N}, captured (docs/dev/map-writes.md → Areas → Delete)
MIN_SIDE_M, MAX_SIDE_M = 1.5, 30.0
EDGE_CLEAR_M = 0.5                             # inside the surrounding area's outline by at least this
LINE_CLEAR_M = 1.0                             # away from pathway and dead-end ends, and from the charger
RANGE_TOL_M = 0.05                             # a read-back outline may differ by this much (as in-place area saves)
ANGLE_TOL_DEG = 1.0
SHIFT_TOL_M = 0.03
EXPIRE_S = 30 * 60                             # a patch that is set up but never started is removed after this
PARAMS_CACHE_S = 60                            # an area's settings, reused across the placing checks
SETTLE_S = 120                                 # after a start, wait this long before a docked, idle robot means done
RUN_LOOKBACK = 20                              # recorded runs searched for the last mow over the patch
MIN_RUN_METRES = 8.0                           # stripes a run must have over the patch to count
MIN_COHERENCE = 0.6                            # how well a run's stripes sit on one grid (1 = perfectly)


# -- geometry ------------------------------------------------------------------------------------------------------
def _dist_seg(p, a, b) -> float:
    vx, vy = b[0] - a[0], b[1] - a[1]
    d2 = vx * vx + vy * vy
    t = 0.0 if d2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / d2))
    return math.hypot(p[0] - a[0] - t * vx, p[1] - a[1] - t * vy)


def dist_to_ring(p, ring) -> float:
    return min(_dist_seg(p, a, b) for a, b in zip(ring, ring[1:] + ring[:1]))


def _cross(a, b, c, d) -> bool:
    """Do segments ab and cd cross or touch?"""
    def side(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return 0 if abs(v) < 1e-12 else (1 if v > 0 else -1)

    def on(p, q, r):
        return (min(p[0], q[0]) - 1e-12 <= r[0] <= max(p[0], q[0]) + 1e-12
                and min(p[1], q[1]) - 1e-12 <= r[1] <= max(p[1], q[1]) + 1e-12)
    s1, s2, s3, s4 = side(a, b, c), side(a, b, d), side(c, d, a), side(c, d, b)
    if s1 != s2 and s3 != s4:
        return True
    return ((s1 == 0 and on(a, b, c)) or (s2 == 0 and on(a, b, d)) or (s3 == 0 and on(c, d, a))
            or (s4 == 0 and on(c, d, b)))


def rings_cross(r1, r2) -> bool:
    e1, e2 = list(zip(r1, r1[1:] + r1[:1])), list(zip(r2, r2[1:] + r2[:1]))
    return any(_cross(a, b, c, d) for a, b in e1 for c, d in e2)


def ring_gap(r1, r2) -> float:
    """The shortest distance between two outlines that do not cross: always from a corner of one to an edge of the
    other."""
    return min(min(dist_to_ring(p, r2) for p in r1), min(dist_to_ring(q, r1) for q in r2))


def _densify(ring, step: float = 0.25) -> list:
    out = []
    for a, b in zip(ring, ring[1:] + ring[:1]):
        n = max(1, math.ceil(math.hypot(b[0] - a[0], b[1] - a[1]) / step))
        out += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n)]
    return out


def rectangle(center, length: float, width: float, angle_deg: float) -> list[tuple[float, float]]:
    """The four corners, `length` along angle_deg (the lines) and `width` across, counter-clockwise on the map
    (east-north; the robot's edge laps follow vertex order and the app's outlines run this way)."""
    t = math.radians(angle_deg)
    u, n = (math.cos(t), math.sin(t)), (-math.sin(t), math.cos(t))
    corners = ((-1, -1), (1, -1), (1, 1), (-1, 1))
    c = [(center[0] + su * u[0] * length / 2 + sn * n[0] * width / 2,
          center[1] + su * u[1] * length / 2 + sn * n[1] * width / 2) for su, sn in corners]
    east_north = sum(-x1 * y2 + x2 * y1 for (x1, y1), (x2, y2) in zip(c, c[1:] + c[:1]))   # x is west: flip it
    return c if east_north > 0 else c[::-1]


def shifted(ring, angle_deg: float, d: float) -> list[tuple[float, float]]:
    """The ring moved `d` metres across lines at angle_deg (positive = the side offsets grow towards)."""
    t = math.radians(angle_deg)
    return [(x - math.sin(t) * d, y + math.cos(t) * d) for x, y in ring]


def _wrap_deg(a: float) -> float:
    """An undirected line angle difference folded into -90..90."""
    return (a + 90) % 180 - 90


def _wrap_gap(d: float, gap: float) -> float:
    return (d + gap / 2) % gap - gap / 2


# -- mowing lines --------------------------------------------------------------------------------------------------
def segments(points, min_len: float, max_len: float | None) -> list[tuple]:
    """(mid x, mid y, angle 0..180, length) of each step of a track or route, in the frame of `points`."""
    out = []
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        ln = math.hypot(x2 - x1, y2 - y1)
        if ln < min_len or (max_len is not None and ln > max_len):
            continue
        out.append(((x1 + x2) / 2, (y1 + y2) / 2, math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180, ln))
    return out


def lines(segs, gap: float) -> dict | None:
    """The stripes among `segs`: their direction (the heaviest 1° bin and its neighbours, then the length-weighted
    mean), where they sit across that direction (a circular mean of the offsets modulo the gap) and how well they agree
    (coherence 0..1). None when there is nothing to go on."""
    if not segs or gap <= 0:
        return None
    bins = [0.0] * 180
    for *_, a, ln in segs:
        bins[int(a) % 180] += ln
    best = max(range(180), key=lambda k: sum(bins[(k + d) % 180] for d in (-2, -1, 0, 1, 2)))
    sel = [s for s in segs if abs(_wrap_deg(s[2] - best)) <= 3]
    metres = sum(s[3] for s in sel)
    if metres <= 0:
        return None
    angle = (best + sum(_wrap_deg(s[2] - best) * s[3] for s in sel) / metres) % 180
    t = math.radians(angle)
    sx = cx = 0.0
    for x, y, _, ln in sel:
        o = -x * math.sin(t) + y * math.cos(t)
        sx, cx = sx + ln * math.sin(2 * math.pi * o / gap), cx + ln * math.cos(2 * math.pi * o / gap)
    phase = (math.atan2(sx, cx) / (2 * math.pi) * gap) % gap
    return {"angle": round(angle, 2), "phase": round(phase, 4), "gap": gap, "metres": round(metres, 1),
            "coherence": round(math.hypot(sx, cx) / metres, 3)}


def phase_shift(tgt: dict, got: dict, gap: float) -> float:
    """How far across the lines `got` must move to sit on `tgt`, both measured along tgt's normal. Two fits of the
    same direction can come out near 0° and near 180°, whose normals point opposite ways: the phase flips with them."""
    gp = got["phase"]
    if abs((got["angle"] - tgt["angle"] + 180) % 360 - 180) > 90:
        gp = (-gp) % gap
    return _wrap_gap(tgt["phase"] - gp, gap)


def _near(segs, center, radius: float) -> list:
    return [s for s in segs if math.hypot(s[0] - center[0], s[1] - center[1]) <= radius]


def _inside(segs, ring) -> list:
    return [s for s in segs if mapstudio.point_in((s[0], s[1]), ring)]


# -- where a patch may go ------------------------------------------------------------------------------------------
def problems(raw: dict, parent_id: int, ring, gps_ref) -> list[str]:
    """Why this rectangle cannot be a patch of area `parent_id` (empty = it can). In the gps_ref frame."""
    out = []
    parent = mapstudio.find(raw, "areas", parent_id)
    if parent is None:
        return ["that area is not on the robot's map"]
    pr = mapstudio.local(parent, gps_ref)
    ring = [tuple(p) for p in ring]
    if not all(mapstudio.point_in(p, pr) for p in ring) or rings_cross(ring, pr):
        out.append("the patch must be inside the area")
    elif ring_gap(ring, pr) < EDGE_CLEAR_M:
        out.append(f"keep the patch {EDGE_CLEAR_M:.1f} m inside the area's edge")
    for a in raw.get("areas") or []:
        if a.get("id") == parent_id:
            continue
        ar = mapstudio.local(a, gps_ref)
        if len(ar) >= 3 and (rings_cross(ring, ar) or any(mapstudio.point_in(p, ar) for p in ring)
                             or any(mapstudio.point_in(q, ring) for q in ar)):
            out.append(f"the patch overlaps area “{a.get('name') or a.get('id')}”")
    ends = []
    for kind in ("pathways", "deadends"):
        for f in raw.get(kind) or []:
            lp = mapstudio.local(f, gps_ref)
            ends += [lp[0], lp[-1]] if lp else []
    if any(mapstudio.point_in(e, ring) or dist_to_ring(e, ring) < LINE_CLEAR_M for e in ends):
        out.append(f"keep the patch {LINE_CLEAR_M:.0f} m from where pathways join the area")
    for c in raw.get("allchargingData") or []:
        cp = c.get("chargingPoint") or {}
        if isinstance(cp.get("x"), (int, float)) and isinstance(cp.get("y"), (int, float)):
            ref = geo.ref_of(c, gps_ref)
            p = geo.to_local(gps_ref, *geo.to_lonlat(ref, cp["x"], cp["y"])) if ref else (cp["x"], cp["y"])
            if mapstudio.point_in(p, ring) or dist_to_ring(p, ring) < LINE_CLEAR_M:
                out.append("keep the patch away from the charger")
    return out


def area_at(raw: dict, p, gps_ref) -> dict | None:
    return next((a for a in raw.get("areas") or [] if len(mapstudio.local(a, gps_ref)) >= 3
                 and mapstudio.point_in(p, mapstudio.local(a, gps_ref))), None)


def to_range(ring, ref, gps_ref) -> list[dict]:
    """gps_ref-frame corners -> the robot's range in the frame of `ref` (the surrounding area's)."""
    out = []
    for x, y in ring:
        lon, lat = geo.to_lonlat(gps_ref, x, y)
        rx, ry = geo.to_local(ref, lon, lat)
        out.append({"x": round(rx, 4), "y": round(ry, 4), "phi": 0})
    return out


def _range_matches(feature: dict, ring, gps_ref) -> bool:
    got = mapstudio.local(feature, gps_ref)
    return len(got) == len(ring) and all(math.hypot(a[0] - b[0], a[1] - b[1]) <= RANGE_TOL_M for a, b in zip(got, ring))


def add_payload(parent: dict, rng: list) -> dict:
    """save_clean_area for a new area, exactly the app's keys and values (docs/dev/map-writes.md → Add), with the
    surrounding area's ref so both share one frame. No id: the robot assigns it."""
    return {"algorithm_type": 0, "labels": [], "leaf_piles": [], "name": NAME, "push_snow_dir": 9999.0,
            "range": rng, "ref": copy.deepcopy(parent["ref"]), "slope_degrees": 9999.0, "snowPiles": [],
            "trimming_edges": []}


def patch_settings(parent_read: dict, new_id: int, route_angle: int) -> dict:
    """The surrounding area's settings for the patch: no boundary extension, no boundary laps, its own angle."""
    out = params_payload(parent_read, new_id)
    out["buffer_dis"] = 0.0
    out["edge_circles"] = 0
    out["first_clean_params"]["route_angle"] = int(route_angle) % 180
    # One pass, the first one, and a second-pass angle that matches anyway: the parent's next mow may be its second
    # pass (current_index) or a double mow (clean_times), whose angle is measured from the parent's long side.
    out["current_index"], out["clean_times"] = 0, 1
    if isinstance(out.get("double_clean_params"), dict):
        out["double_clean_params"]["route_angle"] = int(route_angle) % 180
    return out


def settings_changed(read: dict, st: dict) -> list[str]:
    """The settings that make the patch's route, as the set-up left them, that read differently now."""
    angle = (st.get("settings") or {}).get("route_angle")
    want = {"clean_times": 1, "current_index": 0, "edge_circles": 0, "gap": st["target"]["gap"],
            "first_clean_params.route_angle": angle}
    if isinstance(read.get("double_clean_params"), dict):
        want["double_clean_params.route_angle"] = angle
    out = []
    for k, v in want.items():
        a, *b = k.split(".")
        have = read.get(a) if not b else (read.get(a) or {}).get(b[0])
        if v is None or not isinstance(have, (int, float)) or abs(have - v) > 1e-6:
            out.append(f"{k} {have}")
    return out


def snapshot(raw: dict, plans: list) -> dict:
    """What must look the same after the patch is gone."""
    def pts(a):
        return [[round(p.get("x", 0), 4), round(p.get("y", 0), 4)] for p in a.get("range") or []]
    return {"areas": {str(a.get("id")): pts(a) for a in raw.get("areas") or []},
            "refs": {str(a.get("id")): [round(v, 9) if isinstance(v, (int, float)) else v
                                        for v in (geo.ref_of(a, None) or ())] for a in raw.get("areas") or []},
            "plans": sorted([[p.get("id"), sorted(p.get("areaIds") or []), p.get("name")] for p in plans]),
            "links": {str(p.get("id")): [p.get("start_id"), p.get("end_id")] for p in raw.get("pathways") or []}}


def rest_differs(before: dict, raw: dict, plans: list, area_id, plan_id) -> list[str]:
    """compare() for the map and plans as the robot reads them now, the patch's own area and plan left out."""
    now = snapshot(raw, [p for p in plans if p.get("id") != plan_id])
    now["areas"].pop(str(area_id), None)
    return compare(before, now)


def compare(before: dict, after: dict) -> list[str]:
    out = []
    a, b = before["areas"], after["areas"]
    if set(a) != set(b):
        gone, new = sorted(set(a) - set(b)), sorted(set(b) - set(a))
        out.append("areas changed:" + (f" missing {', '.join(gone)}" if gone else "")
                   + (f" new {', '.join(new)}" if new else ""))
    for k in set(a) & set(b):
        moved = any(math.hypot(p[0] - q[0], p[1] - q[1]) > RANGE_TOL_M for p, q in zip(a[k], b[k]))
        if len(a[k]) != len(b[k]) or moved:
            out.append(f"area {k}'s outline changed")
    if before["plans"] != after["plans"]:
        out.append("the plans are not as they were")
    for k, v in before["links"].items():
        if after["links"].get(k) != v:
            out.append(f"pathway {k}'s links changed {v} → {after['links'].get(k)}")
    new = sorted(set(after["links"]) - set(before["links"]))
    if new:
        out.append(f"new pathways {', '.join(new)}")
    for k, v in (before.get("refs") or {}).items():
        if k in (after.get("refs") or {}) and after["refs"][k] != v:
            out.append(f"area {k}'s reference point changed")
    return out


class PatchMower:
    """The one patch at a time. rec: the recorder (map, plans, areas, settings, the route preview, runs);
    writer: the MapWriter; controller: the run commands; link: the route's write state."""

    def __init__(self, store, writer, rec, controller, link):
        self.store, self.writer, self.rec, self.controller, self.link = store, writer, rec, controller, link
        self._runs: dict[int, list] = {}            # run id -> its track's steps in the gps_ref frame
        self._plan_lines: dict[int, tuple] = {}     # area id -> (when, its own route preview's steps)
        self._params: dict[int, tuple] = {}         # area id -> (when, its settings) for the placing checks
        self._start_lock = asyncio.Lock()

    # -- state ---------------------------------------------------------------------------------------------------------
    def state(self) -> dict | None:
        return self.store.setting(STATE)

    def _save(self, st: dict | None) -> None:
        self.store.set_setting(STATE, st)

    def plan_id(self) -> int | None:
        """The patch's plan id while a patch is tracked: the controller starts it only from start() below."""
        st = self.state()
        return st.get("plan_id") if st else None

    def view(self) -> dict:
        st = self.state()
        shown = None if not st else {k: v for k, v in st.items() if k not in ("before", "settings")}
        return {"patch": shown, "blocked": self.block(), "last": self.store.setting(STATE + "_last")}

    def block(self, start: bool = False) -> str | None:
        """Why the patch's map writes cannot go now (ADR 0004: docked, idle, map writing on). start=True also needs
        control on, as the run itself will. Fails closed."""
        r = self.rec
        if not self.store.setting("map_write_enabled", False):
            return "map writing is switched off (Settings → Safety)"
        if start and not self.controller.enabled():
            return "control is switched off (Settings → Safety)"
        age = time.time() - r.snapshot_at if r.snapshot_at else None
        if age is None or age > 120:
            return "no recent data from the robot"
        active = r.plan_active()
        if r.run_id or active or (r.t.get("planning") or 0) > 0:
            return "a plan is running"
        if active is None:
            return "the robot's plan state is unknown"
        if (r.t.get("charging") or 0) <= 0:
            return "the robot is not on its charger"
        return self.link.why_no_writes() or self.controller.start_settling()

    # -- the lines to match --------------------------------------------------------------------------------------------
    def _run_steps(self, run_id: int, gps_ref) -> list:
        if run_id not in self._runs:
            pts = [geo.to_local(gps_ref, p["lon"], p["lat"]) for p in self.store.run_points(run_id)
                   if p.get("lon") is not None and p.get("lat") is not None]
            if len(self._runs) > 40:
                self._runs.clear()
            self._runs[run_id] = segments(pts, 0.3, 3.0)
        return self._runs[run_id]

    async def target(self, parent: dict, center, radius: float, gap: float, gps_ref) -> dict | None:
        """The lines on the lawn at the patch: the newest recorded run with stripes there (its stripes over the whole
        area: one grid per mow), else the area's own route preview (the robot only computes)."""
        ring = mapstudio.local(parent, gps_ref)
        for run in self.store.runs(RUN_LOOKBACK):
            if not run.get("ended"):
                continue
            steps = self._run_steps(run["id"], gps_ref)
            here = lines(_near(steps, center, radius), gap)
            if not here or here["metres"] < MIN_RUN_METRES or here["coherence"] < MIN_COHERENCE:
                continue
            whole = lines([s for s in _inside(steps, ring) if abs(_wrap_deg(s[2] - here["angle"])) <= 3], gap)
            best = whole if whole and whole["coherence"] >= MIN_COHERENCE else here
            return {**best, "source": "run", "run_id": run["id"]}
        cached = self._plan_lines.get(parent["id"])
        if not cached or time.time() - cached[0] > 600:
            record = (await self.rec.read_area_records()).get(parent["id"])
            if record is None:
                return None
            data = await self.rec.preview_area_path(areasettings.preview_payload(record))
            ref = geo.ref_of(record, gps_ref)
            pts = [geo.to_local(gps_ref, *geo.to_lonlat(ref, p["x"], p["y"])) for p in (data or {}).get("path") or []
                   if isinstance(p, dict) and "x" in p and "y" in p]
            cached = (time.time(), segments(pts, 1.0, None))
            self._plan_lines[parent["id"]] = cached
        got = lines(_near(cached[1], center, radius), gap)
        return {**got, "source": "plan"} if got and got["coherence"] >= MIN_COHERENCE else None

    # -- the read-only check while placing -----------------------------------------------------------------------------
    async def _parent_settings(self, aid: int) -> dict:
        """An area's settings for the placing checks, read at most once a minute (every pan is a check). The set-up
        reads them again itself."""
        hit = self._params.get(aid)
        if hit and time.time() - hit[0] < PARAMS_CACHE_S:
            return hit[1]
        read, err = await areasettings.read_settings(self.rec.read_area_params, aid)
        if read is None:
            raise WriteRefused(f"could not read the area's settings ({err})")
        self._params[aid] = (time.time(), read)
        return read

    async def check(self, lon: float, lat: float, length: float, width: float, raw: dict | None = None) -> dict:
        """Where the patch would go, at the centre given: the area, the lines it follows, its corners and why it can't
        go there (if it can't). Nothing is written."""
        gps_ref = self.rec.gps_ref
        raw = raw if raw is not None else self.rec.map_raw or {}
        if not gps_ref or not raw:
            raise WriteRefused("the robot's map has not been read yet")
        for v, name in ((length, "length"), (width, "width")):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not MIN_SIDE_M <= v <= MAX_SIDE_M:
                raise ValueError(f"{name}: {MIN_SIDE_M} to {MAX_SIDE_M:.0f} m")
        center = geo.to_local(gps_ref, float(lon), float(lat))
        parent = area_at(raw, center, gps_ref)
        if parent is None:
            return {"ok": False, "problems": ["put the patch inside a mowing area"]}
        why = areasettings.other_head_block(parent.get("labels", []))     # get_map may leave labels out
        if why:
            return {"ok": False, "area": {"id": parent["id"], "name": parent.get("name")}, "problems": [why]}
        read = await self._parent_settings(parent["id"])
        gap = float(read.get("gap") or 0)
        tgt = await self.target(parent, center, max(length, width) / 2 + 1.0, gap, gps_ref) if gap > 0 else None
        if tgt is None:
            return {"ok": False, "area": {"id": parent["id"], "name": parent.get("name")},
                    "problems": ["no mowing lines to line up with here yet"]}
        ring = rectangle(center, length, width, tgt["angle"])
        probs = problems(raw, parent["id"], ring, gps_ref)
        return {"ok": not probs, "problems": probs, "area": {"id": parent["id"], "name": parent.get("name")},
                "target": tgt, "corners": [geo.to_lonlat(gps_ref, x, y) for x, y in ring],
                "length": length, "width": width, "area_m2": round(length * width, 1)}

    # -- set up --------------------------------------------------------------------------------------------------------
    async def prepare(self, lon: float, lat: float, length: float, width: float) -> dict:
        """Add the patch area, its settings and its plan; line it up with the robot's own route; read it all back.
        Anything wrong after the first send removes what was added. Raises WriteRefused / ValueError before then."""
        gps_ref = self.rec.gps_ref
        async with self.writer.lock:
            why = self.block(start=True)
            if why:
                raise WriteRefused(why)
            if self.state():
                raise WriteRefused("a patch is already set up; start it or remove it first")
            self._params.clear()                                     # the set-up works from fresh reads only
            try:
                raw = await self.writer.read_map()
                records = await self.writer.read_areas()
                plans = await self.rec.reload_plans()
            except Exception as exc:
                raise WriteRefused(f"could not read the robot's map first ({str(exc) or type(exc).__name__})")
            chk = await self.check(lon, lat, length, width, raw)
            if not chk["ok"]:
                raise ValueError("; ".join(chk["problems"]))
            pid = chk["area"]["id"]
            parent = records.get(pid)
            if parent is None or not geo.usable_ref(geo.ref_of(parent, None)):
                raise WriteRefused("the robot has no usable record of that area")
            why = areasettings.other_head_block(parent.get("labels"))
            if why:
                raise WriteRefused(why)
            read, err = await areasettings.read_settings(self.rec.read_area_params, pid)
            if read is None or areasettings.read_problem(read):
                raise WriteRefused(f"the area's settings cannot be copied ({err or areasettings.read_problem(read)})")
            tgt = chk["target"]
            if abs(float(read.get("gap") or 0) - float(tgt["gap"])) > 1e-6:
                raise WriteRefused("the area's line spacing changed since the check; try again")
            center = geo.to_local(gps_ref, float(lon), float(lat))
            ring = rectangle(center, length, width, tgt["angle"])
            st = {"stage": "adding", "created": time.time(), "parent": {"id": pid, "name": parent.get("name")},
                  "length": length, "width": width, "target": tgt, "ring": [list(p) for p in ring],
                  "corners": chk["corners"], "area_id": None, "plan_id": None, "before": snapshot(raw, plans),
                  "log": []}
            self._save(st)                                           # before the first send
            try:
                return await self._add(st, parent, read, raw, ring, plans, gps_ref)
            except Exception as exc:
                st = self.state() or st
                st["log"].append(f"failed: {str(exc) or type(exc).__name__}")
                self._save(st)
                removed = await self._remove_locked(st, f"setting up failed ({str(exc) or type(exc).__name__})")
                return {"ok": False, "error": str(exc) or type(exc).__name__, "removed": removed}

    async def _add(self, st, parent, read, raw, ring, plans, gps_ref) -> dict:
        w = self.writer
        ref = geo.ref_of(parent, gps_ref)
        r = await w._send(AREA_SAVE, add_payload(parent, to_range(ring, ref, gps_ref)))
        data = r.get("data") if isinstance(r.get("data"), dict) else {}
        new_id = data.get("id") if isinstance(data.get("id"), int) and data.get("name") == NAME else None
        if new_id is None and r["sent"]:
            try:
                now = await w.read_areas()
            except Exception:
                now = {}
            hits = [i for i, a in now.items() if a.get("name") == NAME and str(i) not in st["before"]["areas"]]
            new_id = hits[0] if len(hits) == 1 else None
        if new_id is None:
            raise RuntimeError(f"the robot did not add the patch ({r.get('error') or r.get('msg') or 'no id'})")
        if str(new_id) in st["before"]["areas"]:
            raise RuntimeError(f"the robot answered with area {new_id}, which was already on the map")
        st.update(area_id=new_id)
        st["log"].append(f"added area {new_id}")
        self._save(st)
        route_angle = 0 if st["length"] >= st["width"] else 90
        await self._settings(st, read, route_angle)
        pl = mapwrite.plan_payload(plans, [*map(int, st["before"]["areas"]), new_id],
                                   {"name": NAME, "areaIds": [new_id], "enable_self_order": True})
        st.update(plan_id=pl["id"])
        self._save(st)
        p = await w._send(PLAN_SAVE, pl)
        if not p["sent"]:
            raise RuntimeError(f"the patch's plan was not sent ({p.get('error')})")
        got = next((x for x in await self.rec.reload_plans() if x.get("id") == pl["id"]), None)
        if not got or got.get("areaIds") != [new_id]:
            raise RuntimeError("the robot does not list the patch's plan")
        st["log"].append(f"added plan {pl['id']}")
        after = await w.read_map()
        mine = mapstudio.find(after, "areas", new_id)
        if mine is None or not _range_matches(mine, ring, gps_ref):
            raise RuntimeError("the robot stored a different outline for the patch")
        diff = rest_differs(st["before"], after, await self.rec.reload_plans(), new_id, pl["id"])
        if diff:
            raise RuntimeError("adding the patch changed the rest of the map: " + "; ".join(diff))
        st["stage"] = "aligning"
        self._save(st)
        await self._align(st, raw, ring, route_angle, read, gps_ref)
        diff = rest_differs(st["before"], await w.read_map(), await self.rec.reload_plans(), new_id, pl["id"])
        if diff:                                                 # moving the patch re-links pathways too
            raise RuntimeError("lining the patch up changed the rest of the map: " + "; ".join(diff))
        st["stage"], st["ready_at"] = "ready", time.time()
        st["log"].append("ready")
        self._save(st)
        size = f"{st['length']:g} × {st['width']:g} m"
        self.store.event("patch_ready", f"patch in “{st['parent']['name']}”: {size}, area {new_id}, "
                                        f"plan {pl['id']}")
        return {"ok": True, **self.view()}

    async def _settings(self, st, read, route_angle: int) -> None:
        w, aid = self.writer, st["area_id"]
        want = patch_settings(read, aid, route_angle)
        s = await w._send(areasettings.SAVE, want)
        if not s["sent"]:
            raise RuntimeError(f"the patch's settings were not sent ({s.get('error')})")
        record = (await w.read_areas()).get(aid)
        if record is None:
            raise RuntimeError("the robot has no record of the patch")
        await w._send(AREA_SAVE, areasettings.area_record(record))       # as the app does after a settings save
        got, err = await areasettings.read_settings(self.rec.read_area_params, aid)
        if got is None or got.get("id") != aid or areasettings.differs(got, want):
            raise RuntimeError(f"the robot kept other settings for the patch ({err or 'read back differs'})")
        st["settings"] = {"route_angle": route_angle}
        st["log"].append(f"settings copied, route angle {route_angle}")

    async def _preview(self, aid: int, gap: float, gps_ref) -> tuple[dict | None, dict]:
        record = (await self.writer.read_areas()).get(aid)
        if record is None:
            raise RuntimeError("the robot has no record of the patch")
        data = await self.rec.preview_area_path(areasettings.preview_payload(record))
        if not data or not data.get("path"):
            raise RuntimeError("the robot returned no route for the patch")
        ref = geo.ref_of(record, gps_ref)
        pts = [geo.to_local(gps_ref, *geo.to_lonlat(ref, p["x"], p["y"])) for p in data["path"]
               if isinstance(p, dict) and "x" in p and "y" in p]
        summary = {**areasettings.route_summary(data, record["ref"]), "record": record}
        return lines(segments(pts, 1.0, None), gap), summary

    async def _align(self, st, raw, ring, route_angle: int, read, gps_ref) -> None:
        """The robot's route for the patch must run along the target lines and sit on them."""
        tgt, gap, aid = st["target"], st["target"]["gap"], st["area_id"]
        got, summary = await self._preview(aid, gap, gps_ref)
        if got and abs(abs(_wrap_deg(got["angle"] - tgt["angle"])) - 90) <= ANGLE_TOL_DEG:
            route_angle = 90 - route_angle                       # the robot took the other side as the long one
            await self._settings(st, read, route_angle)
            got, summary = await self._preview(aid, gap, gps_ref)
        if not got or abs(_wrap_deg(got["angle"] - tgt["angle"])) > ANGLE_TOL_DEG:
            raise RuntimeError(f"the robot's lines for the patch run at {got and got['angle']}°, not "
                               f"{tgt['angle']}°")
        d = phase_shift(tgt, got, gap)
        if abs(d) > SHIFT_TOL_M:
            ring = shifted(ring, tgt["angle"], d)
            moved = problems(raw, st["parent"]["id"], ring, gps_ref)      # raw: the map before the patch was added
            if moved:
                raise RuntimeError(f"moved {d * 100:+.0f} cm onto the lines, the patch no longer fits: "
                                   + "; ".join(moved) + "; place it a little further in")
            rec = copy.deepcopy(summary["record"])
            payload = {k: rec[k] for k in mapwrite.AREA_KEYS if k in rec}
            payload["range"] = to_range(ring, geo.ref_of(rec, gps_ref), gps_ref)
            s = await self.writer._send(AREA_SAVE, payload)
            if not s["ok"]:
                raise RuntimeError(f"moving the patch onto the lines failed ({s.get('error') or s.get('msg')})")
            st["ring"] = [list(p) for p in ring]
            st["corners"] = [geo.to_lonlat(gps_ref, x, y) for x, y in ring]
            st["log"].append(f"moved {d * 100:+.0f} cm onto the lines")
            got, summary = await self._preview(aid, gap, gps_ref)
            if (not got or abs(_wrap_deg(got["angle"] - tgt["angle"])) > ANGLE_TOL_DEG
                    or abs(phase_shift(tgt, got, gap)) > SHIFT_TOL_M):
                raise RuntimeError("the patch's lines still do not sit on the last mow's")
        mine = mapstudio.find(await self.writer.read_map(), "areas", aid)
        if mine is None or not _range_matches(mine, [tuple(p) for p in st["ring"]], gps_ref):
            raise RuntimeError("the robot stored a different outline for the patch")
        st["route"] = summary["route"]
        st["route_m"], st["time_s"] = summary["length_m"], summary["total_time_s"]
        st["lines"] = got

    # -- start ---------------------------------------------------------------------------------------------------------
    async def preview(self) -> dict:
        """The start's checks as the patch's own Start would meet them (nothing is sent)."""
        st = self.state()
        if not st or st.get("stage") != "ready":
            raise WriteRefused("no patch is ready to start")
        token = PATCH_START.set(True)
        try:
            return await self.controller.preview("start_plan", {"plan_id": st["plan_id"]}, actor="user")
        finally:
            PATCH_START.reset(token)

    async def start(self, override: bool = False) -> dict:
        """Start the patch's plan through the controller, as a person (all the usual checks)."""
        async with self._start_lock:                 # one start per ready patch: a second press finds it started
            return await self._start(override)

    async def _start(self, override: bool) -> dict:
        st = self.state()
        if not st or st.get("stage") != "ready":
            raise WriteRefused("no patch is ready to start")
        if not self.store.setting("map_write_enabled", False):    # it has to come off again afterwards
            raise WriteRefused("map writing is switched off (Settings → Safety): the patch could not come off again")
        try:                                         # still the patch's plan? ids are reused if it went in the app
            plans = await self.rec.reload_plans()
        except Exception as exc:
            raise WriteRefused(f"could not read the robot's plans ({str(exc) or type(exc).__name__})")
        mine = next((p for p in plans if p.get("id") == st["plan_id"]), None)
        if not mine or mine.get("name") != NAME or mine.get("areaIds") != [st["area_id"]]:
            raise WriteRefused(f"plan {st['plan_id']} is not the patch's any more; remove the patch and set it up again")
        try:
            area = mapstudio.find(await self.writer.read_map(), "areas", st["area_id"])
        except Exception as exc:
            raise WriteRefused(f"could not read the robot's map ({str(exc) or type(exc).__name__})")
        if (area is None or area.get("name") != NAME
                or not _range_matches(area, [tuple(p) for p in st["ring"]], self.rec.gps_ref)):
            raise WriteRefused("the patch's area is not the one that was set up any more (changed in the app?); "
                               "remove the patch and set it up again")
        got, err = await areasettings.read_settings(self.rec.read_area_params, st["area_id"])
        changed = settings_changed(got, st) if got is not None else [err or "could not read them"]
        if changed:
            raise WriteRefused("the patch's mowing settings are not the ones that were set up any more ("
                               + ", ".join(changed) + "); remove the patch and set it up again")
        token = PATCH_START.set(True)
        try:
            res = await self.controller.send("start_plan", {"plan_id": st["plan_id"]}, actor="user",
                                             override=override, wait="background")
        finally:
            PATCH_START.reset(token)
        cur = self.state()
        if res.get("sent") and cur and cur.get("created") == st.get("created"):   # not removed meanwhile
            st = cur
            st.update(stage="started", started_at=time.time())
            st["log"].append("started")
            self._save(st)
            self.store.save_version("patch", {k: st.get(k) for k in ("parent", "length", "width", "corners", "target",
                                                                       "area_id", "plan_id", "started_at")})
            self.store.event("patch_started", f"patch in “{st['parent']['name']}”, plan {st['plan_id']}")
        return res

    # -- remove --------------------------------------------------------------------------------------------------------
    async def remove(self, why: str, accept: bool = False) -> dict:
        """accept=True: a person has looked at what differs from before and lets the patch go anyway (only once
        nothing of the patch is left on the robot)."""
        async with self.writer.lock:
            st = self.state()
            if not st:
                return {"ok": True, "nothing": True}
            blocked = self.block()
            if blocked:
                raise WriteRefused(blocked)
            return await self._remove_locked(st, why, accept=accept)

    def _patch_plans(self, st: dict, plans: list) -> list:
        """The robot's plans that are this patch's: its name, only its area, and not on the robot before the patch
        (ids are reused). The stored plan id first; by name when the plan's reply was lost."""
        before = {p[0] for p in st["before"]["plans"]}
        aid = st.get("area_id")
        if aid is None:                         # the area's add reply was lost: only the plan id is known
            return [p for p in plans if p.get("name") == NAME and p.get("id") not in before
                    and p.get("id") == st.get("plan_id")]
        return [p for p in plans if p.get("name") == NAME and p.get("id") not in before and p.get("areaIds") == [aid]]

    async def _remove_locked(self, st: dict, why: str, accept: bool = False) -> dict:
        """del_plan, then del_clean_area, for what this panel added, each only if it is still the patch (its name, and
        not on the robot before the patch: ids are reused) and each read back as gone. The area goes only once its
        plan is gone, or a leftover plan would point at a freed id another area may get. Anything left keeps the state
        (stage stuck) for another try. Then the map and plans are compared with what they were before the patch; a
        difference keeps it stuck too (reported, never repaired by guessing) until a person accepts the map as it is."""
        w, notes = self.writer, []
        st.update(stage="removing")
        self._save(st)
        left, area = [], None
        try:
            for p in self._patch_plans(st, await self.rec.reload_plans()):
                r = await w._send(PLAN_DELETE, {"id": p["id"]})
                notes.append(f"plan {p['id']}: {'deleted' if r['ok'] else r.get('error') or r.get('msg')}")
            plans = await self.rec.reload_plans()
            left += [f"plan {p['id']} is still on the robot" for p in self._patch_plans(st, plans)]
            raw = await w.read_map()
            area = mapstudio.find(raw, "areas", st.get("area_id")) if st.get("area_id") is not None else None
            if area is None and st.get("area_id") is None:     # an add whose reply was lost: look by name
                hits = [a for a in raw.get("areas") or [] if a.get("name") == NAME
                        and str(a.get("id")) not in st["before"]["areas"]]
                if len(hits) > 1:                               # never guess which one is ours
                    left.append(f"{len(hits)} new areas are called “{NAME}” "
                                f"({', '.join(str(a.get('id')) for a in hits)}); delete them in the app")
                area = hits[0] if len(hits) == 1 else None
            if area is not None and (area.get("name") != NAME or str(area.get("id")) in st["before"]["areas"]):
                notes.append(f"area {area.get('id')} is not the patch any more; left alone")
                area = None
            used = [p.get("id") for p in plans if area is not None and area.get("id") in (p.get("areaIds") or [])]
            if area is not None and used:                       # deleting the area drops every plan that uses it
                left.append(f"area {area['id']} is kept while plan {', '.join(map(str, used))} uses it"
                            + ("" if left else "; take it out of that plan in the app"))
            elif area is not None and left:
                left.append(f"area {area['id']} kept until its plan is gone")
            elif area is not None:
                r = await w._send(AREA_DELETE, {"id": area["id"]})
                notes.append(f"area {area['id']}: {'deleted' if r['ok'] else r.get('error') or r.get('msg')}")
                raw = await w.read_map()
                still = mapstudio.find(raw, "areas", area["id"])
                if still is not None and still.get("name") == NAME:
                    left.append(f"area {area['id']} is still on the robot")
            if left:
                raise RuntimeError("; ".join(left))
            after = snapshot(raw, plans)
        except Exception as exc:
            if st.get("area_id") is None and area is not None:
                st["area_id"] = area.get("id")                  # found by name: the next try deletes it by id
            st["log"].append(f"removing failed: {str(exc) or type(exc).__name__}")
            st["stage"] = "stuck"
            self._save(st)
            self.store.event("patch_remove_failed", f"{why}: {str(exc) or type(exc).__name__}; "
                                                    f"look for “{NAME}” in the app")
            return {"ok": False, "error": str(exc) or type(exc).__name__, "notes": notes}
        diff = compare(st["before"], after)
        if diff and not (accept and diff == st.get("differs")):      # accept only what the person was shown
            st.update(stage="stuck", differs=diff)
            st["log"].append("the patch is off, but the map differs from before")
            self._save(st)
            self.store.event("patch_removed_with_differences",
                             f"{why}; {'; '.join(notes) or 'nothing was on the robot'}; differs from before: "
                             f"{'; '.join(diff)}; check the map, then accept it on Live")
            return {"ok": False, "error": "the map differs from before", "problems": diff, "notes": notes}
        last = {"at": time.time(), "why": why, "notes": notes, "problems": diff, "parent": st.get("parent"),
                "accepted": bool(diff)}
        self.store.set_setting(STATE + "_last", last)
        self._save(None)
        self.store.event("patch_removed" if not diff else "patch_removed_with_differences",
                         f"{why}; {'; '.join(notes) or 'nothing was on the robot'}"
                         + (f"; differs from before: {'; '.join(diff)}" if diff else ""))
        return {"ok": not diff, **last}

    # -- over time -----------------------------------------------------------------------------------------------------
    async def tick(self) -> None:
        """Remove the patch once it is done: the robot back on its charger with no plan, a while after the start; a
        patch never started after EXPIRE_S; a set-up interrupted by a restart. Stuck = only by hand."""
        st = self.state()
        if st and st.get("stage") == "started" and not st.get("running_seen") and (self.rec.run_id
                                                                                    or self.rec.plan_active()):
            st["running_seen"] = time.time()             # the robot took the start (the card says so from now on)
            st["log"].append("the robot shows the patch running")
            self._save(st)
        if not st or st.get("stage") == "stuck" or self.writer.lock.locked() or self.block():
            return
        now, stage = time.time(), st.get("stage")
        if stage == "started" and now - st.get("started_at", now) >= SETTLE_S:
            why = "the patch run is over" if st.get("running_seen") else "the robot never showed the patch running"
        elif stage == "ready" and now - st.get("ready_at", now) >= EXPIRE_S:
            why = f"not started within {EXPIRE_S // 60} min"
        elif stage in ("adding", "aligning", "removing") and now - st.get("created", now) >= SETTLE_S:
            why = "a set-up that did not finish (the panel restarted)"
        else:
            return
        try:
            await self.remove(why)
        except WriteRefused:
            pass
