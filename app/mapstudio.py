"""Map Studio (spec §7): feature metrics, version diffs, validation, KML export and local drafts.

Everything here works on the robot's raw map dict (the `get_map` structure) and never talks to the robot.
Geometry for metrics and checks is in metres in the gps_ref frame (x = west, y = north); every feature is
converted from its own `ref` first, so features recorded against a different origin still compare correctly.

Drafts are edited copies of a map kept on the server. Writing a draft to the robot is not available: the write
format is unverified (spec §7.4, §14 C2/C3), so `apply` is refused before anything is sent.
"""
import copy
import math
from xml.sax.saxutils import escape

from . import geo, tidy

MAX_POINTS = 252          # the largest range the robot has accepted (spec §7.4)
AREA_MAX_POINTS = 262     # ...for an area: the app saved one with 262 points on 2026-10-07


def limit(kind: str) -> int:
    return AREA_MAX_POINTS if kind == "areas" else MAX_POINTS
POLYGONS = geo.POLYGON_KINDS
LINES = geo.LINE_KINDS
# The app's obstacle avoidance modes: an area's avoidance_level and a no-vision zone's by_pass_level (app, 2026-10-10:
# "the same settings as areas"). Only Moderate bypass is matched so far; the rest are added here as they are.
AVOIDANCE_OPTIONS = [[1, "Moderate bypass"]]
KIND_LABELS = {"areas": "Area", "nogozones": "No-go zone", "novisionzones": "No-vision zone", "elec_fence": "Fence",
               "pathways": "Pathway", "sidewalks": "Sidewalk", "deadends": "Dead end"}
ADDABLE = ("areas", "nogozones", "novisionzones", "pathways", "deadends")
# Used only when the map has no feature of that kind to copy the structure from.
NEW_FEATURE = {
    "areas": {"algorithm_type": 0, "area": 0.0, "first_finish": False, "humps": {"hump_level": [], "hump_pts": []},
              "labels": [], "leaf_piles": [], "mapping_module_type": 0, "mult_grid": 0, "push_snow_dir": 0.0,
              "slope_degrees": 9999, "snowPiles": [], "trimming_edges": []},
    "nogozones": {"area_id": -1, "enable": True, "trimming_edges": [], "type": 0},
    "novisionzones": {"area_id": -1, "by_pass_level": 0, "enable": True},
    "pathways": {"connectd_points": [], "connectids": [], "first_finish": False, "head_type": 0,
                 "humps": {"hump_level": [], "hump_pts": []}, "leaf_piles": [], "snowPiles": [], "trimming_edges": []},
    # as the robot returned a new one (save_deadend reply, 2026-10-11)
    "deadends": {"connectd_points": [], "connectids": [], "first_finish": False, "head_type": 0,
                 "humps": {"hump_level": [], "hump_pts": []}, "trimming_edges": []},
}
SKIP_TEMPLATE = {"id", "name", "range", "ref", "start_id", "end_id", "area", "area_id"}


def _blank_like(v):
    """An empty value of the same shape: lists emptied, strings blanked, numbers and flags (the robot's settings
    for that kind of feature) kept, nested refs kept."""
    if isinstance(v, list):
        return []
    if isinstance(v, dict):
        return {k: copy.deepcopy(x) if k == "ref" else _blank_like(x) for k, x in v.items()}
    if isinstance(v, str):
        return ""
    return v


def _new_feature(m: dict, kind: str) -> dict:
    template = next(iter(m.get(kind) or []), None)
    if not template:
        return copy.deepcopy(NEW_FEATURE[kind])
    f = {**copy.deepcopy(NEW_FEATURE[kind]), **{k: _blank_like(v) for k, v in template.items() if k not in SKIP_TEMPLATE}}
    if kind == "areas":
        f["slope_degrees"] = 9999   # "not set"; the robot measures it
    if kind in ("nogozones", "novisionzones"):
        f.update(area_id=-1, enable=True)
    return f


def _endpoint_id(m: dict, p, gps_ref) -> int:
    """The area a path end sits in (or is within 3 m of); -1 if none."""
    best, best_d = -1, 3.0
    for a in m.get("areas") or []:
        ring = local(a, gps_ref)
        if len(ring) < 3:
            continue
        if point_in(p, ring):
            return a.get("id")
        d = _dist_to_shape(p, ring, True)
        if d < best_d:
            best, best_d = a.get("id"), d
    return best


def simplify(pts: list, target: int, closed: bool) -> list[int]:
    """Indices to keep: Visvalingam-Whyatt, dropping the corner whose triangle with its neighbours is smallest
    until `target` remain. A path keeps both ends."""
    keep = list(range(len(pts)))
    floor = 3 if closed else 2
    target = max(target, floor)

    def tri(i):
        n = len(keep)
        if not closed and i in (0, n - 1):
            return float("inf")
        a, b, c = pts[keep[i - 1]], pts[keep[i]], pts[keep[(i + 1) % n]]
        return abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2

    while len(keep) > target:
        i = min(range(len(keep)), key=tri)
        keep.pop(i)
    return keep


# -- geometry -------------------------------------------------------------------------------------------------------
def local(feature: dict, gps_ref) -> list[tuple[float, float]]:
    return geo.feature_poly_local(feature, gps_ref)


def ring_area(pts) -> float:
    if len(pts) < 3:
        return 0.0
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2


def path_length(pts, closed: bool) -> float:
    if len(pts) < 2:
        return 0.0
    segs = list(zip(pts, pts[1:] + (pts[:1] if closed else [])))
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in segs)


def _seg_dist(p, a, b) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _dist_to_shape(p, pts, closed: bool) -> float:
    if len(pts) == 1:
        return math.hypot(p[0] - pts[0][0], p[1] - pts[0][1])
    segs = list(zip(pts, pts[1:] + (pts[:1] if closed else [])))
    return min(_seg_dist(p, a, b) for a, b in segs)


def max_move(a, b, closed: bool) -> float:
    """How far the shape moved: symmetric Hausdorff distance from each one's vertices to the other's outline."""
    if not a or not b:
        return 0.0
    return max(max(_dist_to_shape(p, b, closed) for p in a), max(_dist_to_shape(p, a, closed) for p in b))


def _cross(o, a, b) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _segments_cross(p1, p2, p3, p4) -> bool:
    d1, d2, d3, d4 = _cross(p3, p4, p1), _cross(p3, p4, p2), _cross(p1, p2, p3), _cross(p1, p2, p4)
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0) and 0 not in (d1, d2, d3, d4)


def self_intersections(pts) -> int:
    n = len(pts)
    if n < 4:
        return 0
    edges = [(pts[i], pts[(i + 1) % n]) for i in range(n)]
    hits = 0
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:   # neighbours through the closing edge
                continue
            if _segments_cross(*edges[i], *edges[j]):
                hits += 1
    return hits


def point_in(p, ring) -> bool:
    x, y = p
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


# -- features ---------------------------------------------------------------------------------------------------------
def iter_features(raw: dict):
    for kind in POLYGONS + LINES:
        for f in raw.get(kind) or []:
            if isinstance(f, dict):
                yield kind, f


def find(raw: dict, kind: str, fid) -> dict | None:
    return next((f for f in raw.get(kind) or [] if f.get("id") == fid), None)


def summary(raw: dict, gps_ref, cfd: dict | None = None) -> list[dict]:
    cfd_areas = ((cfd or {}).get("area_low_cfd_configs") or {}) if isinstance(cfd, dict) else {}
    out = []
    for kind, f in iter_features(raw):
        pts = local(f, gps_ref)
        closed = kind in POLYGONS
        row = {"kind": kind, "label": KIND_LABELS.get(kind, kind), "id": f.get("id"), "name": f.get("name") or "",
               "points": len(pts), "area_m2": round(ring_area(pts), 1) if closed else None,
               "length_m": round(path_length(pts, closed), 1), "enable": f.get("enable")}
        if kind in LINES:
            row["start_id"], row["end_id"] = f.get("start_id"), f.get("end_id")
        if kind == "novisionzones":
            level = f.get("by_pass_level")
            row["bypass"] = next((name for v, name in AVOIDANCE_OPTIONS if v == level), None) or \
                (f"{level} (not matched yet)" if level is not None else None)
        if kind == "areas":
            c = cfd_areas.get(str(f.get("id"))) or {}
            row["low_cfd"] = {k: c.get(k) for k in ("area_max_low_cfd_dis", "path_max_low_cfd_dis") if k in c}
            slope = f.get("slope_degrees")
            row["slope_deg"] = slope if isinstance(slope, (int, float)) and 0 <= slope < 90 else None  # 9999 = not set
            row["robot_area_m2"] = round(c["area"], 1) if isinstance(c.get("area"), (int, float)) else None
        out.append(row)
    return out


def geojson(raw: dict, gps_ref) -> dict:
    """The map as GeoJSON, with each feature's metrics in its properties (for the Studio and for export)."""
    fc = geo.map_to_geojson(raw, gps_ref)
    metrics = {(r["kind"], r["id"]): r for r in summary(raw, gps_ref)}
    for feat in fc["features"]:
        p = feat["properties"]
        m = metrics.get((p.get("kind"), p.get("id")))
        if m:
            p.update(area_m2=m["area_m2"], length_m=m["length_m"], label=m["label"])
    return fc


# -- diff -------------------------------------------------------------------------------------------------------------
def diff(a: dict, b: dict, gps_ref) -> dict:
    """What changed from map a to map b, matched by (kind, id)."""
    fa = {(k, f.get("id")): f for k, f in iter_features(a)}
    fb = {(k, f.get("id")): f for k, f in iter_features(b)}
    added, removed, changed, same = [], [], [], 0

    def label(key, f):
        return {"kind": key[0], "id": key[1], "name": f.get("name") or "", "label": KIND_LABELS.get(key[0], key[0])}

    for key, f in fb.items():
        if key not in fa:
            added.append(label(key, f))
    for key, f in fa.items():
        if key not in fb:
            removed.append(label(key, f))
            continue
        g = fb[key]
        closed = key[0] in POLYGONS
        pa, pb = local(f, gps_ref), local(g, gps_ref)
        notes = []
        if (f.get("name") or "") != (g.get("name") or ""):
            notes.append(f"renamed from “{f.get('name') or ''}”")
        if f.get("enable") != g.get("enable"):
            notes.append("enabled" if g.get("enable") else "disabled")
        moved = max_move(pa, pb, closed)
        if len(pa) != len(pb):
            notes.append(f"{len(pa)} → {len(pb)} points")
        if moved >= 0.01:
            notes.append(f"moved up to {moved:.2f} m")
        other = {k for k in set(f) | set(g) if k not in ("range", "ref", "name", "enable") and f.get(k) != g.get(k)}
        if other:
            notes.append("changed " + ", ".join(sorted(other)))
        if notes:
            row = label(key, g)
            row.update(changes=notes, max_move_m=round(moved, 2))
            if closed:
                row["area_delta_m2"] = round(ring_area(pb) - ring_area(pa), 1)
            changed.append(row)
        else:
            same += 1
    return {"added": added, "removed": removed, "changed": changed, "unchanged": same}


def diff_geojson(a: dict, b: dict, gps_ref, d: dict | None = None) -> dict:
    """Overlay for the map: removed and old shapes from a, added and new shapes from b."""
    d = d or diff(a, b, gps_ref)
    ga, gb = geo.map_to_geojson(a, gps_ref), geo.map_to_geojson(b, gps_ref)
    pick = lambda fc, keys: [f for f in fc["features"] if (f["properties"].get("kind"), f["properties"].get("id")) in keys]
    removed = {(r["kind"], r["id"]) for r in d["removed"]}
    added = {(r["kind"], r["id"]) for r in d["added"]}
    changed = {(r["kind"], r["id"]) for r in d["changed"]}
    feats = []
    for status, fc, keys in (("removed", ga, removed), ("old", ga, changed), ("new", gb, changed), ("added", gb, added)):
        for f in pick(fc, keys):
            f = copy.deepcopy(f)
            f["properties"]["status"] = status
            feats.append(f)
    return {"type": "FeatureCollection", "features": feats}


# -- validation (spec §7.4 step 3) --------------------------------------------------------------------------------------
def validate(raw: dict, gps_ref) -> list[dict]:
    issues = []

    def add(level, kind, f, msg, code, count=1):
        issues.append({"level": level, "kind": kind, "id": f.get("id") if f else None,
                       "name": (f.get("name") or "") if f else "", "msg": msg, "code": code, "count": count})

    ids = {}
    for kind, f in iter_features(raw):
        ids.setdefault(f.get("id"), []).append(kind)
    for fid, kinds in ids.items():
        if len(kinds) > 1:
            add("error", kinds[0], {"id": fid}, f"id {fid} is used {len(kinds)} times", "dup_id", len(kinds))

    areas = [(f, local(f, gps_ref)) for f in raw.get("areas") or []]
    endpoints = {f.get("id") for f, _ in areas} | {c.get("id") for c in raw.get("allchargingData") or []} | {0, -1}
    for kind, f in iter_features(raw):
        pts = local(f, gps_ref)
        closed = kind in POLYGONS
        need = 3 if closed else 2
        if len(pts) < need:
            add("error", kind, f, f"needs at least {need} points, has {len(pts)}", "too_few")
            continue
        if len(pts) > limit(kind):
            add("error", kind, f, f"{len(pts)} points; the robot has accepted at most {limit(kind)}", "too_many", len(pts))
        dups = sum(1 for a, b in zip(pts, pts[1:] + (pts[:1] if closed else [])) if math.hypot(b[0] - a[0], b[1] - a[1]) < 0.01)
        if dups:
            add("warning", kind, f, f"{dups} repeated point{'s' if dups > 1 else ''}", "repeat", dups)
        if closed:
            if ring_area(pts) < 0.5:
                add("error", kind, f, f"encloses only {ring_area(pts):.2f} m²", "tiny")
            crossings = self_intersections(pts)
            if crossings:
                add("error", kind, f, f"the outline crosses itself ({crossings}×)", "crossing", crossings)
        if kind in ("nogozones", "novisionzones") and areas:
            outside = sum(1 for p in pts if not any(point_in(p, ring) for _, ring in areas))
            if outside:
                add("warning", kind, f, f"{outside} of {len(pts)} corners are outside every area", "outside", outside)
        if kind == "deadends" and areas and not any(point_in(pts[0], ring) for _, ring in areas):
            # The robot links a dead end to the area it starts in (save_deadend reply, 2026-10-11): one that starts
            # outside every area leads nowhere.
            add("error", kind, f, "starts outside every area; draw it from inside an area outwards", "deadend_start")
        if kind in LINES:
            for end in ("start_id", "end_id"):
                if f.get(end) is not None and f.get(end) not in endpoints:
                    add("warning", kind, f, f"{end} {f.get(end)} is not an area or charging point", "endpoint")
    return issues


def validate_draft(base: dict, draft: dict, gps_ref) -> list[dict]:
    """validate(draft), with each issue the robot's current map already has marked existing (never an error):
    the robot accepted it. A problem only counts against the draft if the draft introduced or worsened it."""
    before = {}
    for i in validate(base or {}, gps_ref):
        k = (i["kind"], i["id"], i["code"])
        before[k] = max(before.get(k, 0), i["count"])
    out = []
    for i in validate(draft, gps_ref):
        k = (i["kind"], i["id"], i["code"])
        if k in before and i["count"] <= before[k]:
            i = {**i, "level": "existing", "msg": i["msg"] + " (already on the robot)"}
        out.append(i)
    return out


def max_feature_id(*maps: dict) -> int:
    ids = [f.get("id") for m in maps for _, f in iter_features(m or {})]
    ids += [c.get("id") for m in maps for c in (m or {}).get("allchargingData") or []]
    return max([i for i in ids if isinstance(i, int)] + [0])


# -- KML export -------------------------------------------------------------------------------------------------------
def to_kml(fc: dict, title: str) -> str:
    def coords(c):
        return " ".join(f"{lon:.8f},{lat:.8f},0" for lon, lat in c)

    marks = []
    for f in fc["features"]:
        p, g = f["properties"], f["geometry"]
        name = escape(f"{p.get('label') or p.get('kind')}: {p.get('name') or p.get('id')}")
        if g["type"] == "Polygon":
            geom = f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{coords(g['coordinates'][0])}</coordinates></LinearRing></outerBoundaryIs></Polygon>"
        elif g["type"] == "LineString":
            geom = f"<LineString><coordinates>{coords(g['coordinates'])}</coordinates></LineString>"
        elif g["type"] == "Point":
            geom = f"<Point><coordinates>{coords([g['coordinates']])}</coordinates></Point>"
        else:
            continue
        marks.append(f"<Placemark><name>{name}</name><description>{escape(p.get('kind') or '')}</description>{geom}</Placemark>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            f"<name>{escape(title)}</name>{''.join(marks)}</Document></kml>\n")


# -- drafts -----------------------------------------------------------------------------------------------------------
class EditError(ValueError):
    pass


def _to_range(feature: dict, coords: list, gps_ref) -> list[dict]:
    """[[lon, lat], ...] -> the feature's own frame, keeping phi for corners that did not move."""
    ref = geo.ref_of(feature, gps_ref)
    if ref is None:
        raise EditError("the feature has no reference point")
    old = [p for p in feature.get("range") or [] if "x" in p and "y" in p]
    out = []
    for c in coords:
        if not (isinstance(c, (list, tuple)) and len(c) == 2):
            raise EditError("coordinates must be [lon, lat] pairs")
        x, y = geo.to_local(ref, float(c[0]), float(c[1]))
        same = next((p for p in old if abs(p["x"] - x) < 0.001 and abs(p["y"] - y) < 0.001), None)
        out.append(copy.deepcopy(same) if same else {"x": round(x, 4), "y": round(y, 4), "phi": 0})
    if len(out) > 1 and (out[0]["x"], out[0]["y"]) == (out[-1]["x"], out[-1]["y"]):
        out.pop()
    return out


def next_id(raw: dict, floor: int = 0) -> int:
    """Above every id in the map and above `floor` (the highest id in any stored version or backup), so a new
    zone never reuses the id of something the robot deleted."""
    return max(max_feature_id(raw), floor) + 1


def revert(draft: dict, current: dict, kind: str, fid) -> tuple[dict, str]:
    """Put one feature back as it is on the robot: undo an add, a change or a delete."""
    m = copy.deepcopy(draft)
    mine, theirs = find(m, kind, fid), find(current, kind, fid)
    if mine is None and theirs is None:
        raise EditError(f"no {kind} with id {fid}")
    name = (theirs or mine).get("name") or str(fid)
    if theirs is None:
        m[kind] = [x for x in m[kind] if x is not mine]
        return m, f"reverted: removed the new {KIND_LABELS[kind].lower()} “{name}”"
    if mine is None:
        m.setdefault(kind, []).append(copy.deepcopy(theirs))
        return m, f"reverted: put back {KIND_LABELS[kind].lower()} “{name}”"
    m[kind] = [copy.deepcopy(theirs) if x is mine else x for x in m[kind]]
    return m, f"reverted {KIND_LABELS[kind].lower()} “{name}” to the robot's version"


TIDY_KINDS = ("areas", "nogozones", "novisionzones", "pathways", "sidewalks", "deadends")


def _tol(v, default: float = tidy.DEFAULT_TOL) -> float:
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        raise EditError("tol must be a number of metres")


def own_points(f: dict) -> list[tuple[float, float]]:
    """A feature's points in its own frame (what its range stores)."""
    return [(p["x"], p["y"]) for p in f.get("range") or [] if "x" in p and "y" in p]


def tidy_run(kind: str, f: dict, tool: str, tol: float) -> dict:
    if kind not in TIDY_KINDS:
        raise EditError(f"{KIND_LABELS.get(kind, kind)}s cannot be tidied")
    return tidy.run(tool, own_points(f), kind in POLYGONS, tol, tidy.BIAS.get(kind))


def tidy_geometry(kind: str, f: dict, pts, gps_ref) -> dict:
    ref = geo.ref_of(f, gps_ref)
    coords = [geo.to_lonlat(ref, x, y) for x, y in pts]
    if kind in POLYGONS:
        return {"type": "Polygon", "coordinates": [coords + coords[:1]]}
    return {"type": "LineString", "coordinates": coords}


def _tidy_feature(m: dict, kind: str, f: dict, tool: str, tol: float, gps_ref) -> str:
    """Replace f's points with the tool's result (in place on the copy `m`); returns the description."""
    r = tidy_run(kind, f, tool, tol)
    name = f.get("name") or str(f.get("id"))
    if not r["ok"]:
        raise EditError(f"{tidy.TOOL_LABELS[tool]} “{name}”: {r['why']}")
    if r["after"] > limit(kind):
        raise EditError(f"{tidy.TOOL_LABELS[tool]} “{name}” would leave {r['after']} points, over the robot's {limit(kind)}")
    old = [p for p in f.get("range") or [] if "x" in p and "y" in p]
    out = []
    for x, y in r["pts"]:
        same = next((p for p in old if abs(p["x"] - x) < 0.001 and abs(p["y"] - y) < 0.001), None)
        out.append(copy.deepcopy(same) if same else {"x": round(x, 4), "y": round(y, 4), "phi": 0})
    f["range"] = out
    if kind in LINES and "start_id" in f:
        pts = local(f, gps_ref)
        f["start_id"], f["end_id"] = _endpoint_id(m, pts[0], gps_ref), _endpoint_id(m, pts[-1], gps_ref)
    return (f"{tidy.TOOL_LABELS[tool].lower()} “{name}”: {r['before']} → {r['after']} points, "
            f"moved at most {r['max_move'] * 100:.0f} cm")


def apply_edit(raw: dict, edit: dict, gps_ref, id_floor: int = 0) -> tuple[dict, str]:
    """Return (new map, description). Never mutates `raw`."""
    m = copy.deepcopy(raw)
    op, kind = edit.get("op"), edit.get("kind")
    if kind is not None and not isinstance(kind, str):
        raise EditError("kind must be a string")
    if op == "tidy_many":                     # the whole-map review: one draft edit, one undo step
        items = edit.get("items") or []
        if not isinstance(items, list) or not items:
            raise EditError("nothing to tidy")
        tol = _tol(edit.get("tol"))
        done = []
        for it in items:
            f = find(m, it.get("kind"), it.get("id")) if isinstance(it, dict) and isinstance(it.get("kind"), str) else None
            if f is None or it.get("tool") not in tidy.TOOLS:
                raise EditError(f"cannot tidy {it.get('kind')} {it.get('id')}")
            done.append(_tidy_feature(m, it["kind"], f, it["tool"], _tol(it.get("tol"), tol), gps_ref))
        return m, f"tidied {len(done)} shape{'s' if len(done) != 1 else ''}: " + "; ".join(done)
    if kind not in POLYGONS + LINES:
        raise EditError(f"unknown kind {kind!r}")
    if op == "add":
        if kind not in ADDABLE:
            raise EditError(f"{KIND_LABELS.get(kind, kind)}s cannot be added here")
        template = next(iter(m.get(kind) or []), None)
        ref = (template or {}).get("ref") or {"latitude": gps_ref[0], "longitude": gps_ref[1]}
        fid = next_id(m, id_floor)
        name = str(edit.get("name") or "").strip()[:40] or f"{KIND_LABELS[kind]} {len(m.get(kind) or []) + 1}"
        f = {**_new_feature(m, kind), "id": fid, "name": name, "ref": copy.deepcopy(ref), "range": []}
        if kind == "novisionzones":                 # as the app makes one (2026-10-10), not copied from a neighbour
            f.update(enable=True, by_pass_level=0)
        f["range"] = _to_range(f, edit.get("coords") or [], gps_ref)
        need = 2 if kind in LINES else 3
        if len(f["range"]) < need:
            raise EditError(f"a {KIND_LABELS[kind].lower()} needs at least {need} points")
        pts = local(f, gps_ref)
        if kind == "areas":
            f["area"] = round(ring_area(pts), 3)
        if kind in LINES:
            f["start_id"], f["end_id"] = _endpoint_id(m, pts[0], gps_ref), _endpoint_id(m, pts[-1], gps_ref)
        m.setdefault(kind, []).append(f)
        return m, f"added {KIND_LABELS[kind].lower()} “{f['name']}”"
    f = find(m, kind, edit.get("id"))
    if f is None:
        raise EditError(f"no {kind} with id {edit.get('id')}")
    name = f.get("name") or str(f.get("id"))
    if op == "shape":
        f["range"] = _to_range(f, edit.get("coords") or [], gps_ref)
        need = 2 if kind in LINES else 3
        if len(f["range"]) < need:
            raise EditError(f"a {KIND_LABELS[kind].lower()} needs at least {need} points")
        if kind in LINES and "start_id" in f:
            pts = local(f, gps_ref)
            f["start_id"], f["end_id"] = _endpoint_id(m, pts[0], gps_ref), _endpoint_id(m, pts[-1], gps_ref)
        return m, f"reshaped {KIND_LABELS[kind].lower()} “{name}”"
    if op == "simplify":
        target = int(edit.get("target") or limit(kind))
        pts = local(f, gps_ref)
        if len(pts) <= target:
            raise EditError(f"already {len(pts)} points")
        keep = simplify(pts, target, kind in POLYGONS)
        kept = [pts[i] for i in keep]
        moved = max_move(pts, kept, kind in POLYGONS)
        f["range"] = [f["range"][i] for i in keep]
        return m, f"simplified “{name}” from {len(pts)} to {len(keep)} points (shape moves at most {moved:.2f} m)"
    if op == "tidy":
        tool = edit.get("tool")
        if tool not in tidy.TOOLS:
            raise EditError(f"unknown tidy tool {tool!r}")
        return m, _tidy_feature(m, kind, f, tool, _tol(edit.get("tol")), gps_ref)
    if op == "rename":
        new = str(edit.get("name") or "").strip()[:40]
        if not new:
            raise EditError("a name is required")
        f["name"] = new
        return m, f"renamed “{name}” to “{new}”"
    if op == "enable":
        if "enable" not in f:
            raise EditError("this feature has no on/off switch")
        f["enable"] = bool(edit.get("enable"))
        return m, f"{'enabled' if f['enable'] else 'disabled'} “{name}”"
    if op == "delete":
        m[kind] = [x for x in m[kind] if x is not f]
        return m, f"deleted {KIND_LABELS[kind].lower()} “{name}”"
    raise EditError(f"unknown edit {op!r}")
