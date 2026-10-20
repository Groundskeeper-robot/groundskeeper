import math
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pyexpat

from ..common import ImageryError, merge_query
from ..mercator import from_merc
from .base import ExportKind, Field

# expat 2.4.1 and later refuse entity-expansion ("billion laughs") documents, and ElementTree never fetches external
# entities; capabilities are only parsed with such an expat.
SAFE_XML = pyexpat.version_info >= (2, 4, 1)
WMS_KEYS = {"SERVICE", "REQUEST", "VERSION", "LAYERS", "STYLES", "CRS", "SRS", "BBOX", "WIDTH", "HEIGHT", "FORMAT",
            "TRANSPARENT", "BGCOLOR", "EXCEPTIONS"}
IMAGERY_WORDS = re.compile(r"ortho|aerial|luchtfoto|orto|dop|photo|imag|satell|rgb", re.I)
FINEST_PLAUSIBLE_M = 0.02                      # a scale finer than 2 cm a pixel is a placeholder, not a resolution


class WMS(ExportKind):
    name = "wms"
    label = "WMS (GetMap)"
    url_hint = "https://…/wms"
    fields = {
        "layers": Field("string", "Layer name(s), comma-separated, exactly as GetCapabilities lists them.", required=True,
                        label="Layer"),
        "styles": Field("string", "Style name(s); usually empty.", default="", label="Styles"),
        "version": Field("string", "WMS version.", choices=("1.3.0", "1.1.1"), default="1.3.0", label="WMS version"),
        "crs": Field("string", "Coordinate system to ask in; EPSG:3857 when the service offers it.",
                     choices=("EPSG:3857", "EPSG:4326"), default="EPSG:3857", label="Coordinates"),
        "format": Field("string", "Image format to ask for.", default="image/jpeg", label="Image format"),
        "max_px": Field("integer", "The largest image the server returns per request (MaxWidth in GetCapabilities).",
                        default=2048, minimum=256, maximum=16384, label="Largest image (px)"),
    }

    def request(self, src, key, box, width, height):
        url, params = merge_query(src["url"], wms_params(src, *box, width, height), key or "")
        return url, params, self.headers(src, key)

    def claims(self, url):
        u = url.lower()
        return "service=wms" in u or "wmsserver" in u or bool(re.search(r"/wms(/|\?|$)", u))

    async def discover(self, http, url, lat=None):
        from ..discover import get, guess_license, guess_year, host, pretty, result, scrub_query
        claimed = self.claims(url)
        parts = urlsplit(scrub_query(url))
        keep = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.upper() not in WMS_KEYS]
        base = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(keep, safe="{}:/"), ""))
        r = await get(http, *merge_query(base, {"SERVICE": "WMS", "REQUEST": "GetCapabilities"}))

        def not_wms(why):
            if claimed:
                raise ImageryError(why)
            return None

        if r.status_code >= 400:
            return not_wms(f"{host(base)} answered HTTP {r.status_code} to GetCapabilities")
        if not SAFE_XML:
            raise ImageryError("this server's XML parser is too old to read capabilities safely")
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError:
            return not_wms("GetCapabilities did not answer with XML")
        if _local(root.tag) not in ("WMS_Capabilities", "WMT_MS_Capabilities"):
            return not_wms(f"the answer is not WMS capabilities ({_local(root.tag)})")
        version = root.get("version") or "1.3.0"
        capability = _child(root, "Capability")
        layers = []
        for top in (c for c in (capability if capability is not None else []) if _local(c.tag) == "Layer"):
            _walk(top, {"crs": set(), "bbox": None, "native_m": None, "attribution": ""}, layers)
        if not layers:
            raise ImageryError("the WMS lists no named layers")
        service = _child(root, "Service")
        title = _text(service, "Title")
        terms = f"{_text(service, 'Fees')} {_text(service, 'AccessConstraints')}"
        limits = [int(t) for t in (_text(service, "MaxWidth"), _text(service, "MaxHeight")) if re.fullmatch(r"[0-9]{1,6}", t)]
        pick = next((l for l in layers if IMAGERY_WORDS.search(f"{l['name']} {l['title']}")), layers[0])
        notes, guessed = [], []
        native = pick["native_m"]
        if native:
            notes.append(f"Resolution from the layer's finest scale: {native * 100:.1f} cm.")
        else:
            native = 0.25
            guessed.append("native_m")
            notes.append("The WMS does not say its resolution; 25 cm is a guess, and the preview will show.")
        if len(layers) > 1:
            notes.append(f"It offers {len(layers)} layers; the panel picked {pick['title']!r}. Change it below.")
        mercator = "EPSG:3857" in pick["crs"]
        if not mercator:
            notes.append("That layer is not offered in Web Mercator (EPSG:3857), so the panel asks in EPSG:4326.")
        credit = pick["attribution"] or title
        entry = {"name": pretty(pick["title"] if len(layers) > 1 or not title else title), "kind": self.name,
                 "url": base, "layers": pick["name"], "version": "1.3.0" if version.startswith("1.3") else "1.1.1",
                 "crs": "EPSG:3857" if mercator else "EPSG:4326",
                 "max_px": min(limits + [4096]) if limits else None, "native_m": round(native, 4),
                 "attribution": f"Imagery: {credit}" if credit else f"Imagery: {host(base)}",
                 "vintage": guess_year(pick["name"], pick["title"], title), "license": guess_license(terms)}
        if not credit:
            guessed.append("attribution")
        shown = layers[:300] + ([pick] if pick not in layers[:300] else [])
        choices = {"layers": [{k: l[k] for k in ("name", "title", "native_m", "bbox")} for l in shown]}
        return result(entry, notes, guessed, choices, extent=pick["bbox"])


def _walk(el, inherited: dict, out: list) -> None:
    """Layers nest; a child inherits its parent's coordinate systems, bounding box, scale and attribution."""
    here = {"crs": inherited["crs"] | {(c.text or "").strip().upper() for c in el if _local(c.tag) in ("CRS", "SRS")},
            "bbox": _bbox(el) or inherited["bbox"], "native_m": _pixel(el) or inherited["native_m"],
            "attribution": _text(_child(el, "Attribution"), "Title") or inherited["attribution"]}
    name = _text(el, "Name")
    if name:
        out.append({"name": name, "title": _text(el, "Title") or name, **here})
    for child in el:
        if _local(child.tag) == "Layer":
            _walk(child, here, out)


def _local(tag) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _child(el, name):
    return next((c for c in el if _local(c.tag) == name), None) if el is not None else None


def _text(el, name) -> str:
    c = _child(el, name)
    return (c.text or "").strip() if c is not None else ""


def _bbox(layer) -> list[float] | None:
    """A layer's lon/lat bounding box (1.3.0 EX_GeographicBoundingBox, 1.1.1 LatLonBoundingBox)."""
    try:
        g = _child(layer, "EX_GeographicBoundingBox")
        if g is not None:
            box = [float(_text(g, k)) for k in ("westBoundLongitude", "southBoundLatitude", "eastBoundLongitude", "northBoundLatitude")]
        else:
            ll = _child(layer, "LatLonBoundingBox")
            if ll is None:
                return None
            box = [float(ll.get(k)) for k in ("minx", "miny", "maxx", "maxy")]
    except (TypeError, ValueError):
        return None
    ok = all(math.isfinite(v) for v in box) and -180 <= box[0] < box[2] <= 180 and -90 <= box[1] < box[3] <= 90
    return box if ok else None


def _pixel(layer) -> float | None:
    """Ground metres per pixel at the layer's finest scale: MinScaleDenominator x 0.28 mm (the WMS standard pixel),
    or 1.1.1's ScaleHint min (a pixel's diagonal on the ground). None when absent or implausible."""
    try:
        t = _text(layer, "MinScaleDenominator")
        hint = _child(layer, "ScaleHint")
        m = float(t) * 0.00028 if t else float(hint.get("min", 0)) / math.sqrt(2) if hint is not None else 0
    except (TypeError, ValueError):
        return None
    return m if math.isfinite(m) and m >= FINEST_PLAUSIBLE_M else None


def wms_params(src: dict, x0: float, y0: float, x1: float, y1: float, width: int, height: int) -> dict:
    """GetMap parameters. WMS 1.3.0 with EPSG:4326 wants lat/lon axis order; 1.1.1 always lon/lat; EPSG:3857 x/y."""
    version = src.get("version") or "1.3.0"
    crs = src.get("crs") or "EPSG:3857"
    if crs == "EPSG:4326":
        (lon0, lat0), (lon1, lat1) = from_merc(x0, y0), from_merc(x1, y1)
        bbox = f"{lat0},{lon0},{lat1},{lon1}" if version == "1.3.0" else f"{lon0},{lat0},{lon1},{lat1}"
    else:
        bbox = f"{x0},{y0},{x1},{y1}"
    return {"SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": version, "LAYERS": src["layers"],
            "STYLES": src.get("styles") or "", ("CRS" if version == "1.3.0" else "SRS"): crs, "BBOX": bbox,
            "WIDTH": width, "HEIGHT": height, "FORMAT": src.get("format") or "image/jpeg", "TRANSPARENT": "FALSE"}
