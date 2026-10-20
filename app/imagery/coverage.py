"""Coverage outlines: where a source has imagery.

The panel decides LOCALLY, from these outlines, which sources may be asked about a yard; a source whose outline does
not contain the yard is never contacted. Outlines are meant to be generous (a buffered country, not its exact border)
and holes are ignored.
"""
import json
import math
from pathlib import Path


def ring_contains(ring: list, lon: float, lat: float) -> bool:
    inside = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        (x1, y1), (x2, y2) = a[:2], b[:2]                 # GeoJSON may carry a third (height) value
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def contains(geom: dict | None, lon: float, lat: float) -> bool:
    kind = (geom or {}).get("type")
    if kind == "World":
        return True
    if kind == "Polygon":
        return ring_contains(geom["coordinates"][0], lon, lat)
    if kind == "MultiPolygon":
        return any(ring_contains(poly[0], lon, lat) for poly in geom["coordinates"])
    return False


def covers(src: dict, lon: float, lat: float) -> bool:
    """Does the source's coverage outline contain (lon, lat)?"""
    return contains(src.get("coverage"), lon, lat)


def _finite(v) -> bool:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(float(v))
    except OverflowError:
        return False


def problems(geom) -> list[str]:
    """What is wrong with a coverage geometry (empty when it is usable)."""
    if not isinstance(geom, dict):
        return ["coverage must be an object"]
    kind = geom.get("type")
    if kind == "World":
        return []
    if kind not in ("Polygon", "MultiPolygon"):
        return ["coverage must be World, a ref to an outline file, or a GeoJSON Polygon / MultiPolygon"]
    polys = [geom.get("coordinates")] if kind == "Polygon" else geom.get("coordinates")
    if not isinstance(polys, list) or not polys:
        return ["coverage has no coordinates"]
    for poly in polys:
        if not isinstance(poly, list) or not poly or not isinstance(poly[0], list) or len(poly[0]) < 4:
            return ["each coverage polygon needs an outer ring of at least 4 points"]
        for pt in poly[0]:
            if (not isinstance(pt, list) or len(pt) < 2
                    or not all(_finite(v) for v in pt[:2])
                    or not (-180 <= pt[0] <= 180 and -90 <= pt[1] <= 90)):
                return [f"coverage point {pt!r} is not a [longitude, latitude] pair"]
    return []


def read_outline(path: Path) -> dict:
    """A GeoJSON file (a geometry, a Feature or a FeatureCollection) as one Polygon or MultiPolygon."""
    return from_geojson(json.loads(path.read_text(encoding="utf-8")), path.name)


def from_geojson(data, label: str = "the outline") -> dict:
    """GeoJSON (a geometry, a Feature or a FeatureCollection) as one Polygon or MultiPolygon; ValueError if none."""
    if not isinstance(data, dict):
        raise ValueError(f"{label} is not a GeoJSON object")
    if data.get("type") == "FeatureCollection":
        geoms = [f.get("geometry") or {} for f in data.get("features") or []]
    elif data.get("type") == "Feature":
        geoms = [data.get("geometry") or {}]
    else:
        geoms = [data]
    polys = []
    for g in geoms:
        if not isinstance(g, dict):
            continue
        if g.get("type") == "Polygon":
            polys.append(g["coordinates"])
        elif g.get("type") == "MultiPolygon":
            polys.extend(g["coordinates"])
    if not polys:
        raise ValueError(f"{label} holds no Polygon or MultiPolygon")
    return {"type": "Polygon", "coordinates": polys[0]} if len(polys) == 1 else {"type": "MultiPolygon", "coordinates": polys}


def extent_km(geom: dict) -> tuple[float, float]:
    """(east-west, north-south) size in km of a Polygon / MultiPolygon's bounding box."""
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    pts = [pt for poly in polys for pt in poly[0]]
    lons, lats = [p[0] for p in pts], [p[1] for p in pts]
    mid = math.radians((min(lats) + max(lats)) / 2)
    return (max(lons) - min(lons)) * 111.32 * math.cos(mid), (max(lats) - min(lats)) * 110.57


def bounds(geom: dict) -> tuple[float, float, float, float]:
    """(west, south, east, north) of a Polygon / MultiPolygon; the whole world for anything else."""
    if geom.get("type") not in ("Polygon", "MultiPolygon"):
        return -180.0, -90.0, 180.0, 90.0
    pts = [pt for ring in polygons(geom) for pt in ring]
    return min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)


def polygons(geom: dict) -> list[list]:
    """The outer rings of a Polygon / MultiPolygon, each as [[lon, lat], ...] (holes and extra values dropped)."""
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    return [[[float(p[0]), float(p[1])] for p in poly[0]] for poly in polys]


def ring_extent_km(ring: list) -> tuple[float, float]:
    lons, lats = [p[0] for p in ring], [p[1] for p in ring]
    mid = math.radians((min(lats) + max(lats)) / 2)
    return (max(lons) - min(lons)) * 111.32 * math.cos(mid), (max(lats) - min(lats)) * 110.57
