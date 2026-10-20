import json
import math
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..common import KEY_NAMES, ImageryError, merge_query
from ..mercator import from_merc
from .base import ExportKind, Field

MAX_PX = Field("integer", "The largest image the server returns per request (maxImageWidth in its ?f=json).",
               default=4096, minimum=256, maximum=16384, label="Largest image (px)")
MERCATOR = {3857, 102100, 102113, 900913}
DEGREES = {4326, 4269, 4258, 4283, 4617, 4167, 4674}
UNITS = (("feet", 0.3048), ("metres", 1.0))     # what projected grids use; anything within 8 % snaps to one
SERVICE_RE = re.compile(r"^(.*?/(ImageServer|MapServer))(?:/.*)?$", re.I)


class ArcGISImage(ExportKind):
    name = "arcgis_image"
    label = "ArcGIS ImageServer"
    url_hint = "https://…/rest/services/…/ImageServer"
    fields = {"max_px": MAX_PX}
    endpoint = "exportImage"
    server = "imageserver"
    extra = {"compressionQuality": 88}

    def _at(self, url: str, key: str, endpoint: str, params: dict) -> tuple[str, dict]:
        """The service's operation address, with the service address's own query (a token, say) kept as params."""
        parts = urlsplit(url)
        op = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + ("/" + endpoint if endpoint else ""),
                         parts.query, ""))
        return merge_query(op, params, key or "")

    def request(self, src, key, box, width, height):
        x0, y0, x1, y1 = box
        headers = self.headers(src, key)
        url, params = self._at(src["url"], key, self.endpoint,
                               {"bbox": f"{x0},{y0},{x1},{y1}", "bboxSR": 3857, "imageSR": 3857,
                                "size": f"{width},{height}", "format": "jpg", "f": "image", **self.extra})
        if key and not headers and not any(k.lower() in KEY_NAMES for k in params):
            params["token"] = key
        return url, params, headers

    def _base(self, url: str) -> str | None:
        """The service address (with any credential parameter from the pasted one), or None if not this kind."""
        parts = urlsplit(url)
        m = SERVICE_RE.match(parts.path)
        if (not m or m.group(2).lower() != self.server or re.search(r"/tile/", parts.path, re.I)
                or re.search(r"/(WMSServer|WMTS|WCSServer|WFSServer|KmlServer)(/|$)", parts.path, re.I)):
            return None                          # its tiles, or its OGC endpoints: other kinds read those
        keys = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() in KEY_NAMES]
        return urlunsplit((parts.scheme, parts.netloc, m.group(1), urlencode(keys, safe="{}"), ""))

    def claims(self, url):
        return self._base(url) is not None

    async def discover(self, http, url, lat=None):
        from ..discover import get_json, guess_license, guess_year, host, pretty, result
        base = self._base(url)
        if base is None:
            return None
        info = await get_json(http, *self._at(base, "", "", {"f": "json"}))
        err = info.get("error")
        if err:
            raise ImageryError(f"the service said: {err.get('message') if isinstance(err, dict) else err}"[:200])
        notes, guessed = [], []
        factor, extent, units = await _scale_and_extent(http, self, base, info, lat)
        pixel = self._pixel(info, units)
        if pixel and factor:
            native = pixel * factor
            if units == "feet":
                notes.append(f"The service works in feet: {pixel:g} ft per pixel is {native * 100:.1f} cm.")
        else:
            native = 0.15
            guessed.append("native_m")
            notes.append("The service does not say its resolution; 15 cm is a guess, and the preview will show.")
        limits = [v for v in (info.get("maxImageWidth"), info.get("maxImageHeight")) if isinstance(v, int) and v > 0]
        segment = urlsplit(base).path.rstrip("/").split("/")[-2]
        name = str(info.get("name") or info.get("mapName") or "")
        name = pretty((segment if name.lower() in ("", "layers") else name).split("/")[-1])
        credit = str(info.get("copyrightText") or "").strip()
        entry = {"name": name, "kind": self.name, "url": base, "native_m": round(native, 4),
                 "max_px": min(limits + [4096]) if limits else None,
                 "attribution": f"Imagery: {credit}" if credit else f"Imagery: {host(base)}",
                 "vintage": guess_year(name, info.get("description"), info.get("serviceDescription")),
                 "license": guess_license(credit, info.get("description"), info.get("serviceDescription"))}
        if not credit:
            guessed.append("attribution")
        return result(entry, notes, guessed, extent=extent)

    def _pixel(self, info: dict, units: str | None) -> float | None:
        px = info.get("pixelSizeY" if units == "degrees" and info.get("pixelSizeY") else "pixelSizeX")
        return float(px) if isinstance(px, (int, float)) and px > 0 else None


class ArcGISMap(ArcGISImage):
    name = "arcgis_map"
    label = "ArcGIS MapServer"
    url_hint = "https://…/rest/services/…/MapServer"
    endpoint = "export"
    server = "mapserver"
    extra = {"transparent": "false"}

    def _pixel(self, info, units):
        tile_info = info.get("tileInfo") if isinstance(info.get("tileInfo"), dict) else {}
        lods = tile_info.get("lods") if isinstance(tile_info.get("lods"), list) else []
        res = [l.get("resolution") for l in lods if isinstance(l, dict) and isinstance(l.get("resolution"), (int, float))
               and l["resolution"] > 0]
        return min(res) if res else None


def _extent(info: dict) -> dict:
    for k in ("extent", "fullExtent", "initialExtent"):
        if isinstance(info.get(k), dict):
            return info[k]
    return {}


async def _scale_and_extent(http, kind: ArcGISImage, base: str, info: dict, lat: float | None = None):
    """(ground metres per native unit, [w, s, e, n] in lon/lat, "metres" | "feet" | "degrees" | None).

    Web Mercator and lon/lat services are worked out here; for Web Mercator the scale depends on latitude, so the
    yard's latitude is used when given (here only: it is never in a request). For anything else (State Plane, UTM, a
    national grid) the server is asked to express a small square at the centre of its own extent in Web Mercator, and
    its whole extent in lon/lat (exports with f=json): no location is sent and no projection library is needed. The
    measured scale snaps to feet or metres, which absorbs the few per cent a rotated grid adds."""
    from ..discover import get_json
    ext = _extent(info)
    sr = ext.get("spatialReference") if isinstance(ext.get("spatialReference"), dict) else \
        info.get("spatialReference") if isinstance(info.get("spatialReference"), dict) else {}
    wkid = sr.get("latestWkid") or sr.get("wkid")
    try:
        x0, y0, x1, y1 = (float(ext[k]) for k in ("xmin", "ymin", "xmax", "ymax"))
        assert all(math.isfinite(v) for v in (x0, y0, x1, y1)) and x1 > x0 and y1 > y0
    except (KeyError, TypeError, ValueError, AssertionError):
        return None, None, None
    if wkid in MERCATOR:
        (w, s), (e, n) = from_merc(x0, y0), from_merc(x1, y1)
        return math.cos(math.radians(lat if lat is not None else (s + n) / 2)), [w, s, e, n], "metres"
    if wkid in DEGREES:
        return 110540.0, [x0, y0, x1, y1], "degrees"
    sr_param = json.dumps({k: v for k, v in sr.items() if k in ("wkid", "latestWkid", "wkt")}, separators=(",", ":"))

    async def export(box, image_sr):
        out = await get_json(http, *kind._at(base, "", kind.endpoint, {
            "bbox": ",".join(map(str, box)), "bboxSR": sr_param, "imageSR": image_sr, "size": "64,64",
            "format": "jpg", "f": "json"}))
        e = out["extent"]
        return [float(e[k]) for k in ("xmin", "ymin", "xmax", "ymax")]

    cx, cy, half = (x0 + x1) / 2, (y0 + y1) / 2, min(x1 - x0, y1 - y0) / 200
    try:
        mx0, my0, mx1, my1 = await export((cx - half, cy - half, cx + half, cy + half), 3857)
    except Exception:
        return None, None, None
    (_, s), (_, n) = from_merc(mx0, my0), from_merc(mx1, my1)
    ground = min(mx1 - mx0, my1 - my0) / (2 * half) * math.cos(math.radians((s + n) / 2))
    units = next((u for u, size in UNITS if abs(ground / size - 1) < 0.08), None)
    if units:
        ground = dict(UNITS)[units]
    try:
        extent = await export((x0, y0, x1, y1), 4326)
        if not (-180 <= extent[0] < extent[2] <= 180 and -90 <= extent[1] < extent[3] <= 90):
            extent = None
    except Exception:
        extent = None
    return ground, extent, units
