"""Planned vs driven: how closely a run followed the robot's planned mowing route (2026-10-09).

The plan is the robot's own, kept while the run went (get_plan_feedback); where none was kept, the robot plans the
run's areas again (preview_snowbot_area_path, the app's route preview, with today's outlines and settings, and it
says so).

The robot's own plan (get_plan_feedback data.cleanPathProgress, worked out from run 7, 2026-10-18): one entry per
area and pass, {id, type, clean_index, clean_times, path, path_slope}, in the order the robot drives them.
* type 1 is the edge pass: around the area's outline (area 34: median 0.1 m from it) and its obstacles; no slope.
* type 0 is the fill: the stripes; path_slope holds the stripe heading both ways (0.94 and -2.20 rad on area 11,
  as are 286 of its stripes).
* clean_index is how far along that path the robot has got: run 7, the morning resume of run 6, began at point 186
  of area 11's edge pass, 519 m into its 1,196 m, about what run 6 had driven (580 m). The rest were at 0.
* finishIds lists the passes done as {id, move_type} (move_type is the same 0 / 1).
* The path is in the area's own frame (place_robot_path checks).
Together the four entries of run 7's plan come to 6.08 km; it drove 5.93 km after run 6's 0.58 km.

Only samples taken while mowing (StateMSG.on_going_planning 1) with an RTK fix are compared: driving between areas
follows the pathways, not the stripes, and a float or single fix is metres off by itself."""
import math

import numpy as np

NEAR_M = 0.5          # a driven sample this close to the plan follows it
OFF_M = 1.0           # further than this is off the plan
MIN_STRETCH_M = 2.0   # shorter excursions are noise (turns, avoidance nudges)
GAP_S = 5             # a longer gap between samples ends a stretch
STEP_M = 0.2          # the plan is compared as points this far apart along it


def _xy(lonlat: np.ndarray, origin) -> np.ndarray:
    """Metres east and north of `origin` (lon, lat); plenty at yard scale."""
    k = 111320.0
    return np.c_[(lonlat[:, 0] - origin[0]) * k * math.cos(math.radians(origin[1])), (lonlat[:, 1] - origin[1]) * k]


def densify(line: np.ndarray, step: float = STEP_M) -> tuple[np.ndarray, np.ndarray]:
    """Points along a polyline (metres), every `step`, each with the direction of its segment (radians)."""
    first = math.atan2(line[1][1] - line[0][1], line[1][0] - line[0][0]) if len(line) > 1 else 0.0
    pts, ang = [line[:1]], [np.array([first])]
    for a, b in zip(line[:-1], line[1:]):
        n = max(1, int(math.ceil(float(np.hypot(*(b - a))) / step)))
        t = np.arange(1, n + 1)[:, None] / n
        pts.append(a + (b - a) * t)
        ang.append(np.full(n, math.atan2(b[1] - a[1], b[0] - a[0])))
    return np.vstack(pts), np.concatenate(ang)


def headings(pts: np.ndarray, ts: np.ndarray, min_m: float = 0.1) -> np.ndarray:
    """The direction of travel at each sample (from its neighbours), NaN where it barely moved or the samples break."""
    out = np.full(len(pts), np.nan)
    if len(pts) < 3:
        return out
    v = pts[2:] - pts[:-2]
    ok = (np.hypot(v[:, 0], v[:, 1]) >= min_m) & (ts[2:] - ts[:-2] <= 2 * GAP_S)
    inner = out[1:-1]
    inner[ok] = np.arctan2(v[ok, 1], v[ok, 0])
    out[1:-1] = inner
    return out


def _same_way(a: np.ndarray, b: np.ndarray, tol: float) -> np.ndarray:
    """Directions within `tol` of each other either way along a line (a stripe is driven both ways)."""
    d = np.abs(((a - b) + math.pi / 2) % math.pi - math.pi / 2)
    return d <= tol


def nearest(points: np.ndarray, targets: np.ndarray, reach: float = 2.0, p_ang: np.ndarray | None = None,
            t_ang: np.ndarray | None = None, tol: float = math.radians(25)) -> np.ndarray:
    """For each point, the distance to the nearest target (inf beyond about `reach`); with directions given, only
    targets heading the same way (within `tol`, either sense) count. Targets are bucketed in cells of `reach` metres
    and each point looks in its own and the 8 neighbouring cells."""
    out = np.full(len(points), np.inf)
    if not len(points) or not len(targets):
        return out
    use_ang = p_ang is not None and t_ang is not None
    buckets: dict = {}
    for n, key in enumerate(map(tuple, np.floor(targets / reach).astype(np.int64))):
        buckets.setdefault(key, []).append(n)
    buckets = {k: np.asarray(v) for k, v in buckets.items()}
    pkeys = np.floor(points / reach).astype(np.int64)
    order = np.lexsort((pkeys[:, 1], pkeys[:, 0]))
    sorted_keys = pkeys[order]
    starts = np.r_[0, np.nonzero(np.any(np.diff(sorted_keys, axis=0) != 0, axis=1))[0] + 1, len(order)]
    for a, b in zip(starts[:-1], starts[1:]):
        i, j = sorted_keys[a]
        near = [buckets[(i + di, j + dj)] for di in (-1, 0, 1) for dj in (-1, 0, 1) if (i + di, j + dj) in buckets]
        if not near:
            continue
        cidx = np.concatenate(near)
        idx = order[a:b]
        d = np.sqrt(((points[idx, None, :] - targets[None, cidx, :]) ** 2).sum(-1))
        if use_ang:
            d = np.where(_same_way(p_ang[idx, None], t_ang[None, cidx], tol), d, np.inf)
        out[idx] = d.min(1)
    return out


def compare(routes: list[list], track: list[dict]) -> dict:
    """routes: one [[lon, lat], ...] line per area. track: telemetry rows (ts, lon, lat, rtk_status, planning).
    The plan's stripes are closer together than the deck is wide, so being near the plan says nothing by itself: a
    driven sample follows the plan when it is within NEAR_M of the plan going the same way (either sense along a
    stripe). Samples where the robot barely moved are left out. Returns the shares, the stretches off the plan
    (GeoJSON) and how much of the plan was driven."""
    lines = [np.asarray(r, dtype=float) for r in routes if len(r) >= 2]
    mow = [p for p in track if p.get("lon") is not None and p.get("rtk_status") == 4 and p.get("planning") == 1]
    empty = {"compared": 0, "on_plan_pct": None, "off_plan_pct": None, "plan_driven_pct": None,
             "off_plan": {"type": "FeatureCollection", "features": []}}
    if not lines or len(mow) < 3:
        return empty
    origin = (float(lines[0][0, 0]), float(lines[0][0, 1]))
    dense = [densify(_xy(l, origin)) for l in lines]
    plan, plan_ang = np.vstack([q for q, _ in dense]), np.concatenate([a for _, a in dense])
    pts_ll_all = np.array([[q["lon"], q["lat"]] for q in mow], dtype=float)
    ts_all = np.array([q["ts"] for q in mow], dtype=float)
    pts_all = _xy(pts_ll_all, origin)
    ang_all = headings(pts_all, ts_all)
    moving = np.isfinite(ang_all)
    pts, pts_ll, ts, ang = pts_all[moving], pts_ll_all[moving], ts_all[moving], ang_all[moving]
    if len(pts) < 2:
        return empty
    d = nearest(pts, plan, p_ang=ang, t_ang=plan_ang)
    back = nearest(plan, pts, p_ang=plan_ang, t_ang=ang)
    feats, run = [], []

    def flush():
        if len(run) >= 2:
            seg = pts[run]
            length = float(np.hypot(*np.diff(seg, axis=0).T).sum())
            if length >= MIN_STRETCH_M:
                plain = nearest(seg, plan, reach=5.0)            # how far it was from the plan at all
                worst = float(np.minimum(plain, 9.9).max())
                feats.append({"type": "Feature", "properties": {"length_m": round(length, 1),
                                                                "max_off_m": round(worst, 1),
                                                                # never far from some stripe: it cut across them
                                                                "kind": "across" if worst < NEAR_M else "away",
                                                                "from": float(ts[run[0]]), "to": float(ts[run[-1]])},
                              "geometry": {"type": "LineString", "coordinates": pts_ll[run].round(7).tolist()}})

    for i in range(len(pts)):
        off = d[i] > OFF_M
        if off and run and ts[i] - ts[run[-1]] <= GAP_S:
            run.append(i)
        elif off:
            flush()
            run = [i]
        else:
            flush()
            run = []
    flush()
    return {"compared": int(len(pts)),
            "on_plan_pct": round(100 * float((d <= NEAR_M).mean()), 1),
            "off_plan_pct": round(100 * float((d > OFF_M).mean()), 1),
            "plan_driven_pct": round(100 * float((back <= NEAR_M).mean()), 1),
            "off_plan": {"type": "FeatureCollection", "features": feats}}


def place_robot_path(path: list[dict], area: dict, gps_ref) -> list[list[float]] | None:
    """The robot's own path for an area (get_plan_feedback cleanPathProgress) as [lon, lat], or None when it cannot be
    placed. Its frame is not documented, so the area's own reference point and then the map's are tried, and a frame
    counts only when at least ROBOT_PATH_INSIDE of the points fall inside the area (with a 1 m margin)."""
    from . import geo, mapstudio
    ref = area.get("ref") or {}
    if not path or "latitude" not in ref:
        return None
    a_ref = (float(ref["latitude"]), float(ref["longitude"]))
    ring = [(p["x"], p["y"]) for p in area.get("range") or [] if "x" in p and "y" in p]
    if len(ring) < 3:
        return None
    for origin in (a_ref, tuple(gps_ref) if gps_ref else None):
        if origin is None:
            continue
        lonlat = [geo.to_lonlat(origin, p["x"], p["y"]) for p in path]
        local = [geo.to_local(a_ref, lon, lat) for lon, lat in lonlat]          # back into the area's own frame
        inside = sum(mapstudio.point_in(q, ring) or _near_ring(q, ring, 1.0) for q in local)
        if inside >= ROBOT_PATH_INSIDE * len(local):
            return lonlat
    return None


ROBOT_PATH_INSIDE = 0.9


def _near_ring(q, ring, m: float) -> bool:
    from . import mapstudio
    return any(mapstudio._seg_dist(q, a, b) <= m for a, b in zip(ring, ring[1:] + ring[:1]))


PASS_KINDS = {0: "fill", 1: "edge"}


def reply_session(data: dict) -> str | None:
    pid, start = data.get("planId"), data.get("startTime")
    return f"{int(pid)}:{int(start)}" if isinstance(pid, (int, float)) and isinstance(start, (int, float)) else None


def _entry_key(item) -> tuple | None:
    if not isinstance(item, dict) or not isinstance(item.get("id"), int):
        return None
    return item["id"], item["type"] if isinstance(item.get("type"), int) else None


def _points(path) -> list[dict]:
    return [p for p in path or [] if isinstance(p, dict) and "x" in p and "y" in p]


def robot_plan(store, run: dict) -> dict:
    """The robot's own plan for a run, from what was kept while it went: {"parts": [{area_id, type, path, start_index,
    source}], "done": {(area_id, type)}, "area_ids": [...] or None}.

    Every pass of every area counts (several per area: the edge and the fill), not just the first. A part comes from
    the run's own kept paths (plan_path:<run>:<area>:<type>), else its kept replies (plan_feedback:<run>, which carry
    them all), else an earlier run of the same plan session (a resume that was not joined onto it). Replies of another
    plan session are left out (run 6's first reply was the finished plan of the week before). start_index is where the
    robot stood on that path when the run began (its clean_index then); "done" are the passes the run's first reply
    already lists finished. Paths kept before 2026-10-19 (plan_path:<run>:<area>, the area's first entry only) are
    used for an area nothing else covers."""
    session = run.get("plan_session")
    parts: dict = {}
    legacy: dict = {}
    done: set = set()
    area_ids = None

    def add(target: dict, key, path, index, source):
        pts = _points(path)
        if len(pts) < 2:
            return
        index = int(index) if isinstance(index, (int, float)) and index > 0 else 0
        have = target.get(key)
        if have is None:
            target[key] = {"area_id": key[0], "type": key[1], "path": pts, "start_index": index, "source": source}
        else:                                           # a joined run: its first piece started earliest
            have["start_index"] = min(have["start_index"], index)

    runs = [run] + list(reversed(store.earlier_session_runs(run["id"]))) if session else [run]
    for n, rr in enumerate(runs):
        own, rid = n == 0, rr["id"]
        found: dict = {}
        source = "the robot's own plan" if own else f"the robot's own plan, kept in run {rid}"
        for v in sorted(store.versions_like(f"plan_path:{rid}:%"), key=lambda v: v["fetched"]):
            bits = v["kind"].split(":")[2:]
            val = (store.version(v["id"]) or {}).get("json") or {}
            if val.get("session") and session and val["session"] != session:
                continue
            try:
                aid = int(bits[0])
                ptype = int(bits[1]) if len(bits) > 1 and bits[1] != "None" else None
            except ValueError:
                continue
            if len(bits) == 1:
                add(legacy, (aid, None), val.get("path"), 0, source)
            else:
                add(found, (aid, ptype), val.get("path"), val.get("clean_index"), source)
        first = True
        for v in sorted(store.versions(f"plan_feedback:{rid}"), key=lambda v: v["fetched"]):
            data = ((store.version(v["id"]) or {}).get("json") or {}).get("data")
            if not isinstance(data, dict):
                continue
            if session and reply_session(data) != session:
                continue
            if own and first:
                first = False
                ids = [a for a in data.get("areaIds") or [] if isinstance(a, int)]
                area_ids = ids or None
                done = {(f.get("id"), f.get("move_type")) for f in data.get("finishIds") or [] if isinstance(f, dict)}
            for item in data.get("cleanPathProgress") or []:
                key = _entry_key(item)
                if key is not None:
                    add(found, key, item.get("path"), item.get("clean_index"), source)
        for key, part in found.items():
            if key not in parts:
                parts[key] = part if own else {**part, "start_index": 0}
    typed = {k[0] for k in parts}
    for key, part in legacy.items():
        if key[0] not in typed:
            parts[key] = part
    ordered = sorted(parts.values(), key=lambda p: (p["area_id"], -1 if p["type"] is None else p["type"]))
    return {"parts": ordered, "done": done, "area_ids": area_ids}
