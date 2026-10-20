"""Map geometry. The robot's frame is metres from a reference lat/lon with x = WEST and y = NORTH.

Conversion uses the official SDK's convert_local_to_gps so our maths matches Yarbo's.
"""
import math

from yarbo_robot_sdk.device_helpers import convert_local_to_gps

POLYGON_KINDS = ("areas", "nogozones", "novisionzones", "elec_fence")
LINE_KINDS = ("pathways", "sidewalks", "deadends")
METRES_PER_DEG_LAT = 111320.0


def ref_of(feature: dict, fallback: tuple[float, float] | None) -> tuple[float, float] | None:
    ref = feature.get("ref") or {}
    lat, lon = ref.get("latitude"), ref.get("longitude")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return float(lat), float(lon)
    return fallback


def to_lonlat(ref: tuple[float, float], x: float, y: float) -> list[float]:
    lat, lon = convert_local_to_gps(ref[0], ref[1], x, y)
    return [lon, lat]


def _ring(ref, points) -> list[list[float]]:
    coords = [to_lonlat(ref, p["x"], p["y"]) for p in points if "x" in p and "y" in p]
    if coords and coords[0] != coords[-1]:
        coords.append(coords[0])
    return coords


def map_to_geojson(raw_map: dict, gps_ref: tuple[float, float] | None) -> dict:
    features = []

    def add(geometry, **props):
        features.append({"type": "Feature", "geometry": geometry, "properties": props})

    for kind in POLYGON_KINDS:
        for f in raw_map.get(kind) or []:
            ref = ref_of(f, gps_ref)
            points = f.get("range") or []
            if ref and len(points) >= 3:
                add({"type": "Polygon", "coordinates": [_ring(ref, points)]},
                    kind=kind, id=f.get("id"), name=f.get("name") or "", enable=f.get("enable", True),
                    points=len(points))
            for pile in f.get("snowPiles") or []:
                pref = ref_of(pile, ref)
                if pref and len(pile.get("range") or []) >= 3:
                    add({"type": "Polygon", "coordinates": [_ring(pref, pile["range"])]},
                        kind="snowpiles", id=None, name="snow pile", parent=f.get("name") or "")
    for kind in LINE_KINDS:
        for f in raw_map.get(kind) or []:
            ref = ref_of(f, gps_ref)
            points = f.get("range") or []
            if ref and len(points) >= 2:
                add({"type": "LineString", "coordinates": [to_lonlat(ref, p["x"], p["y"]) for p in points]},
                    kind=kind, id=f.get("id"), name=f.get("name") or "", points=len(points))
            for pile in f.get("snowPiles") or []:
                pref = ref_of(pile, ref)
                if pref and len(pile.get("range") or []) >= 3:
                    add({"type": "Polygon", "coordinates": [_ring(pref, pile["range"])]},
                        kind="snowpiles", id=None, name="snow pile", parent=f.get("name") or "")
    for f in raw_map.get("allchargingData") or []:
        ref = ref_of(f, gps_ref)
        cp = f.get("chargingPoint") or {}
        if ref and "x" in cp and "y" in cp:
            add({"type": "Point", "coordinates": to_lonlat(ref, cp["x"], cp["y"])},
                kind="charging", id=f.get("id"), name=f.get("name") or "charging point",
                station=bool(f.get("hasChargingStation")))
    return {"type": "FeatureCollection", "features": features}


def bounds(fc: dict) -> list[float] | None:
    """[west, south, east, north] of every coordinate in the collection."""
    xs, ys = [], []

    def walk(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
        elif isinstance(c, (list, tuple)):
            for item in c:
                walk(item)

    for f in fc.get("features", []):
        walk(f["geometry"]["coordinates"])
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def pad(bbox: list[float], metres: float) -> list[float]:
    west, south, east, north = bbox
    lat = (south + north) / 2
    dlat = metres / METRES_PER_DEG_LAT
    dlon = metres / (METRES_PER_DEG_LAT * math.cos(math.radians(lat)))
    return [west - dlon, south - dlat, east + dlon, north + dlat]


def distance_m(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


def to_local(ref: tuple[float, float], lon: float, lat: float) -> tuple[float, float]:
    """Inverse of to_lonlat: lon/lat to metres from ref, x = west, y = north (same maths as the SDK)."""
    y = (lat - ref[0]) * METRES_PER_DEG_LAT
    x = -(lon - ref[1]) * METRES_PER_DEG_LAT * math.cos(math.radians(ref[0]))
    return x, y


def ring_area_m2(ring: list) -> float:
    """Area of a [[lon, lat], ...] ring in m² (shoelace in metres around its first corner; fine at yard scale)."""
    if len(ring) < 3:
        return 0.0
    ref = (ring[0][1], ring[0][0])
    pts = [to_local(ref, lon, lat) for lon, lat in ring]
    a = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))
    return abs(a) / 2


def clean_ring(ring: list) -> list[list[float]]:
    """Floats, with repeated corners (a double-click) and a closing copy of the first corner removed."""
    out: list[list[float]] = []
    for lon, lat in ring:
        p = [float(lon), float(lat)]
        if not out or p != out[-1]:
            out.append(p)
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def feature_poly_local(feature: dict, gps_ref: tuple[float, float]) -> list[tuple[float, float]]:
    """A map feature's range in the gps_ref frame (features normally share it; convert when they don't)."""
    ref = ref_of(feature, gps_ref)
    pts = [(p["x"], p["y"]) for p in feature.get("range") or [] if "x" in p and "y" in p]
    if ref is None or ref == gps_ref:
        return pts
    return [to_local(gps_ref, *to_lonlat(ref, x, y)) for x, y in pts]
