"""Export for sharing: a source of the user's own as a catalog file (and outline) for a pull request.

What goes into the file is public, so the guards are about the user's home:
- the landmark must be a public place at least MIN_FROM_YARD_M from the yard, and inside the coverage;
- an outline is rebuilt from outer rings and [lon, lat] pairs only (holes, heights and anything else dropped); each
  of its parts must be at least MIN_OUTLINE_KM across, and none may come within MIN_FROM_YARD_M of the yard, so a
  yard can never be shared as, or inside, an outline;
- "the whole world" goes with country XX and only with it;
- credentials are taken out of the address: key parameters become {key}, a user name and password are removed;
  local-only fields (where the file lives, its origin) are dropped;
- the resolution is given to two significant figures: for a Web Mercator service it was worked out at the yard's
  latitude, and four decimals would put the yard in a band a few km tall.
"""
import json
import math
import re
from datetime import date
from urllib.parse import urlsplit, urlunsplit

from . import coverage as cov
from . import kinds
from .catalog import CATALOG_DIR, ID_RE, REF_RE, bundled_problems, clean, prepare
from .common import KEY_NAMES, ImageryError
from .mercator import metres_to_box

MIN_FROM_YARD_M = 2000
MIN_OUTLINE_KM = (20, 10)                     # each part: the longer side, the shorter side
MAX_OUTLINE_POINTS = 5000
KEY_PARAM = re.compile(r"(?i)([?&](?:" + "|".join(re.escape(k) for k in KEY_NAMES) + r")=)(?!\{key\})([^&#]+)")
USERINFO = re.compile(r"(?i)\b[^\s/@:]+:[^\s/@]+@(?=[a-z0-9-]+(?:\.[a-z0-9-]+)+\b)")    # user:pw@host.tld in text
ORDER = ("$schema", "id", "name", "provider", "country", "region", "homepage", "kind", "url")
TAIL = ("native_m", "vintage", "attribution", "license", "auth", "cache_days", "coverage", "verified")


def strip_keys(url: str) -> tuple[str, bool]:
    """(the address with key parameters as {key} and no user name or password, whether anything was taken out)."""
    out = KEY_PARAM.sub(r"\1{key}", url)
    parts = urlsplit(out)
    if "@" in parts.netloc:
        out = urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[1], parts.path, parts.query, parts.fragment))
    return out, out != url


def dumps(obj) -> str:
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    return re.sub(r"\[\s+(-?[\d.]+),\s+(-?[\d.]+)\s+\]", r"[\1, \2]", text) + "\n"


def _distance_m(lon, lat, point) -> float:
    return metres_to_box(lon, lat, point[0], point[1], point[0], point[1])


def _nearest_edge_m(ring: list, yard) -> float:
    """How close an outline's edges come to the yard (metres, on a local flat projection: fine at these distances)."""
    kx, ky = 111320 * math.cos(math.radians(yard[1])), 110540
    pts = [((p[0] - yard[0]) * kx, (p[1] - yard[1]) * ky) for p in ring]
    best = math.inf
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        dx, dy = x2 - x1, y2 - y1
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(x1 + t * dx, y1 + t * dy))
    return best


def _outline(coverage: dict, worldwide: bool, yard) -> tuple[dict, str | None, dict | None]:
    """(geometry for checks, shared ref or "" for a new outline or None for World, GeoJSON to write or None)."""
    if not isinstance(coverage, dict):
        raise ImageryError("choose where the service has imagery")
    if coverage.get("world"):
        if not worldwide:
            raise ImageryError("\"the whole world\" is only for a service that covers the whole world")
        return {"type": "World"}, None, None
    if coverage.get("ref"):
        ref = str(coverage["ref"])
        path = CATALOG_DIR / "coverage" / f"{ref}.geojson"
        if not REF_RE.fullmatch(ref) or not path.exists():
            raise ImageryError(f"there is no shared outline called {ref!r}")
        return cov.read_outline(path), ref, None
    if coverage.get("extent"):
        try:
            w, s, e, n = (float(v) for v in coverage["extent"])
        except (TypeError, ValueError):
            raise ImageryError("the service's extent is not [west, south, east, north]") from None
        raw = {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}
        source = "the service's own extent, as its capabilities report it"
    elif coverage.get("geojson") is not None:
        try:
            raw = cov.from_geojson(coverage["geojson"])
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ImageryError(f"the outline is not usable GeoJSON: {exc}") from None
        source = "drawn by the contributor"
    else:
        raise ImageryError("choose where the service has imagery")
    problems = cov.problems(raw)
    if problems:
        raise ImageryError("; ".join(problems))
    rings = cov.polygons(raw)
    if sum(len(r) for r in rings) > MAX_OUTLINE_POINTS:
        raise ImageryError(f"the outline has more than {MAX_OUTLINE_POINTS} points; simplify it")
    for ring in rings:
        long_km, short_km = sorted(cov.ring_extent_km(ring), reverse=True)
        if long_km < MIN_OUTLINE_KM[0] or short_km < MIN_OUTLINE_KM[1]:
            raise ImageryError(f"a part of the outline is {long_km:.0f} by {short_km:.0f} km; each part must be at least "
                               f"{MIN_OUTLINE_KM[0]} by {MIN_OUTLINE_KM[1]} km, so that a yard can never be shared as one")
        if yard and _nearest_edge_m(ring, yard) < MIN_FROM_YARD_M:
            raise ImageryError(f"the outline passes within {MIN_FROM_YARD_M / 1000:g} km of your yard; draw it wider there")
    rings = [[[round(p[0], 4), round(p[1], 4)] for p in ring] for ring in rings]
    geom = ({"type": "Polygon", "coordinates": [rings[0]]} if len(rings) == 1
            else {"type": "MultiPolygon", "coordinates": [[r] for r in rings]})
    return geom, "", {"type": "Feature", "properties": {"source": source,
                                                       "note": "Generous on purpose: it only decides which sources are "
                                                               "worth asking about a yard."}, "geometry": geom}


def _free_id(sid: str, taken: set[str]) -> str:
    sid = sid[:40].rstrip("_")
    base, n = sid[:37].rstrip("_"), 2
    while sid in taken:
        sid, n = f"{base}_{n}", n + 1
    return sid


def export(src: dict, *, country: str, region: str, place: str, landmark, coverage: dict, worldwide: bool = False,
           yard: tuple[float, float] | None = None, today: str | None = None) -> dict:
    """The catalog files for a source of the user's own. Raises ImageryError with what to fix."""
    cc = str(country or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", cc):
        raise ImageryError("the country is its two-letter code (US, NL, DE…); XX for a worldwide service")
    region, place = str(region or "").strip(), str(place or "").strip()
    if not region:
        raise ImageryError("say which region it covers (a state, county or country), as a user would read it")
    if not place:
        raise ImageryError("name the public landmark you checked it at")
    try:
        lon, lat = (float(v) for v in landmark)
        assert -180 <= lon <= 180 and -90 <= lat <= 90 and (lon, lat) != (0.0, 0.0)
    except (TypeError, ValueError, AssertionError):
        raise ImageryError("give the landmark's longitude and latitude") from None
    notes = ["Check the address for anything private the panel cannot recognise (a key inside the path, say): "
             "the file is public."]
    if yard:
        away = _distance_m(lon, lat, yard)
        if away < MIN_FROM_YARD_M:
            raise ImageryError(f"the landmark is {away:,.0f} m from your yard. It goes into a public file: use a public "
                               f"place at least {MIN_FROM_YARD_M / 1000:g} km away (a courthouse, a capitol, a cathedral)")
    else:
        notes.append("The yard is not known yet, so the landmark and outline could not be checked against it: make "
                     "sure neither is near your home.")
    geom, ref, outline = _outline(coverage, worldwide, yard)
    if (geom["type"] == "World") != (cc == "XX"):
        raise ImageryError("a worldwide service has country XX and the whole world as its coverage; a regional one "
                           "has its own country and an outline")
    if geom["type"] != "World" and not cov.contains(geom, lon, lat):
        raise ImageryError("the landmark is outside the outline: pick a place inside the area the service covers")
    taken = {p.stem for p in (CATALOG_DIR / "sources").rglob("*.json")}
    sid = str(src["id"])
    sid = sid if sid.startswith(f"{cc.lower()}_") else f"{cc.lower()}_{sid}"
    sid = _free_id(sid, taken | ({p.stem for p in (CATALOG_DIR / "coverage").glob("*.geojson")} if outline else set()))
    if not ID_RE.fullmatch(sid) or (outline is not None and not REF_RE.fullmatch(sid)):
        raise ImageryError(f"{sid!r} cannot be a catalog id")
    if outline is not None:
        ref = sid
        outline["properties"] = {"name": region, **outline["properties"]}
    entry = clean(src)
    url, took = strip_keys(entry["url"])
    if took:
        entry.setdefault("auth", {"label": "a key for this service"})
        notes.append("Credentials in its address were taken out ({key} stands for each user's own key).")
    entry["native_m"] = float(f"{float(entry['native_m']):.2g}")    # see the module docstring
    for k in ("name", "attribution", "provider", "vintage"):
        if isinstance(entry.get(k), str):
            entry[k] = USERINFO.sub("", entry[k])
    lic = {"id": "unknown", "name": "Not stated", "commercial": None, "credit": True, **(entry.get("license") or {})}
    if lic["id"] == "unknown":
        notes.append("Its licence is \"not stated\": find the service's terms (and whether commercial use is allowed) "
                     "before opening the pull request.")
    kind = kinds.get(entry["kind"])
    body = {**entry, "$schema": "../../schema.json", "id": sid, "country": cc, "region": region, "url": url,
            "license": lic, "coverage": {"ref": ref} if ref else {"type": "World"},
            "verified": {"date": today or date.today().isoformat(), "place": place, "landmark": [round(lon, 5), round(lat, 5)]}}
    ordered = {k: body[k] for k in ORDER if k in body}
    ordered.update({k: body[k] for k in kind.fields if k in body})
    ordered.update({k: body[k] for k in TAIL if k in body})
    check = prepare({**ordered, "coverage": geom}, [CATALOG_DIR / "coverage"])
    problems = bundled_problems(check)
    if problems:
        raise ImageryError("the file would not pass the catalog checks: " + "; ".join(problems))
    folder = "world" if cc == "XX" else cc.lower()
    files = [{"path": f"catalog/sources/{folder}/{sid}.json", "text": dumps(ordered)}]
    if outline is not None:
        files.append({"path": f"catalog/coverage/{ref}.geojson", "text": dumps(outline)})
    return {"id": sid, "files": files, "notes": notes}
