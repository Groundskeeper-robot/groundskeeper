"""Web Mercator and tile-grid arithmetic. Pure functions; boxes are (x0, y0, x1, y1) in EPSG:3857 metres."""
import math

R = 6378137.0
HALF = math.pi * R
MAX_PX = 4096                # largest yard image, per side


def to_merc(lon: float, lat: float) -> tuple[float, float]:
    return R * math.radians(lon), R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def from_merc(x: float, y: float) -> tuple[float, float]:
    return math.degrees(x / R), math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """A web map tile's Mercator box: x0, y0, x1, y1."""
    span = 2 * HALF / 2 ** z
    x0, y1 = -HALF + x * span, HALF - y * span
    return x0, y1 - span, x0 + span, y1


def tile_at(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    return int((lon + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)


def metres_to_box(lon: float, lat: float, w: float, s: float, e: float, n: float) -> float:
    """Ground distance from a point to a lon/lat box (0 when inside)."""
    clon, clat = min(max(lon, w), e), min(max(lat, s), n)
    return math.hypot((clon - lon) * 111320 * math.cos(math.radians(lat)), (clat - lat) * 110540)


def square(lon: float, lat: float, half_m: float) -> list[float]:
    """[west, south, east, north] of a square of side 2 * half_m centred on (lon, lat)."""
    d_lat = half_m / 111320
    d_lon = d_lat / math.cos(math.radians(lat))
    return [lon - d_lon, lat - d_lat, lon + d_lon, lat + d_lat]


def plan_size(bbox: list[float], native_m: float, max_px: int = MAX_PX) -> tuple[float, int, int]:
    """(ground metres per pixel, width, height) for bbox at the source's resolution, capped at max_px a side."""
    w, s, e, n = bbox
    x0, y0 = to_merc(w, s)
    x1, y1 = to_merc(e, n)
    k = 1 / math.cos(math.radians((s + n) / 2))          # Mercator metres per ground metre
    gw, gh = (x1 - x0) / k, (y1 - y0) / k
    mpp = max(native_m, max(gw, gh) / max_px)
    return mpp, max(64, round(gw / mpp)), max(64, round(gh / mpp))


def tile_zoom(mpp: float, lat: float, tile_px: int, max_zoom: int) -> int:
    """The smallest zoom whose tiles are at least as sharp as mpp (ground metres per pixel), capped."""
    z = math.ceil(math.log2(2 * HALF * math.cos(math.radians(lat)) / (tile_px * mpp)))
    return max(0, min(max_zoom, z))


def tile_range(x0: float, y0: float, x1: float, y1: float, z: int) -> tuple[int, int, int, int]:
    span = 2 * HALF / 2 ** z
    return (math.floor((x0 + HALF) / span), math.floor((HALF - y1) / span),
            math.floor((x1 + HALF) / span - 1e-9), math.floor((HALF - y0) / span - 1e-9))


def chunks(width: int, height: int, max_px: int) -> list[tuple[int, int, int, int]]:
    """Split a width x height export into pieces no larger than max_px a side: (left, top, w, h) in pixels."""
    cols, rows = math.ceil(width / max_px), math.ceil(height / max_px)
    xs = [round(i * width / cols) for i in range(cols + 1)]
    ys = [round(j * height / rows) for j in range(rows + 1)]
    return [(xs[i], ys[j], xs[i + 1] - xs[i], ys[j + 1] - ys[j]) for j in range(rows) for i in range(cols)]
