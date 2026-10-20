"""The yard's ground for the map's 3D view, from the USGS 3DEP lidar and the robot's own RTK heights.

USGS 3DEP's ImageServer (no key) returns a float32 GeoTIFF in WGS84 for a box: the best DEM it has there (1 m bare
earth from lidar in most of the US), heights in metres (NAVD88). It is fetched once for the robot's map plus a margin
and kept in DATA_DIR/elevation/. Nothing here reaches the robot.

How far to trust it: the lidar can be years old. Checked against a robot's RTK-fixed heights on one yard, after the
constant offset (antenna height and datum) the ground differed by 0.35 m median, up to 1.5 m, with no horizontal shift
fixing it (regrading since the survey); the robot's slope matched its tilt sensor (r 0.70) where the lidar's did not.

Source "robot": a surface made from the robot's RTK-fixed heights (store.height_points: the telemetry column hgt,
the one-a-minute raw snapshots before it), median per cell, small gaps filled, lightly smoothed. Its cells follow the
data: a few metres from sparse history, about a metre once a mow has logged a height every second.

The 3D view reads one ground made from both (`blend`): the robot's heights where it has mowed, the lidar around them
shifted to agree, served as Terrarium-encoded web-mercator tiles (`dem_tile`, `terrarium_png`) that carry the edge
heights on past the grid and only then level out, so there is no cliff where the data stops."""
import asyncio
import hashlib
import io
import json
import math
import time
from collections import OrderedDict
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

EXPORT = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
SOURCE = "USGS 3DEP lidar terrain (1 m where available)"
NOTE = "The yard may have changed since the lidar was flown: read it as the lay of the land, not exact heights."
M_LAT = 111320.0
MARGIN_M = 15.0          # around the robot's map
RES_M = 0.5              # the fetched grid (the DEM is 1 m; half-metre pixels give smoother tiles)
MAX_PX = 900             # per side
NODATA = -9999.0
# The 3D view's ground
TILE = 256
FLAT_M = 50.0            # past the grid's edge its (smoothed) edge heights carry straight on this far...
TAPER_M = 100.0          # ...then ease to the border's typical level (0) over this far, out of the usual view
EDGE_SMOOTH_M = 10.0     # what carries on is the ground smoothed over this far, so its bumps do not streak outward
SEAM_M = 3.0             # the step between the robot's surface and the lidar is eased over this far
DEM_ZOOMS = (14, 18)     # tiles are served for these zooms (z18 is ~0.44 m a pixel here); MapLibre overzooms past it
TILE_CACHE = 400


def m_per_lon(lat: float) -> float:
    return M_LAT * math.cos(math.radians(lat))


def box_around(bounds, margin_m: float = MARGIN_M) -> list[float]:
    """[west, south, east, north] of the map's bounds plus a margin in metres."""
    w, s, e, n = bounds
    lat = (s + n) / 2
    dx, dy = margin_m / m_per_lon(lat), margin_m / M_LAT
    return [w - dx, s - dy, e + dx, n + dy]


def covers(box, bounds) -> bool:
    return box[0] <= bounds[0] and box[1] <= bounds[1] and box[2] >= bounds[2] and box[3] >= bounds[3]


def grid_size(box, res_m: float = RES_M) -> tuple[int, int]:
    lat = (box[1] + box[3]) / 2
    w = (box[2] - box[0]) * m_per_lon(lat) / res_m
    h = (box[3] - box[1]) * M_LAT / res_m
    k = max(1.0, max(w, h) / MAX_PX)
    return max(8, round(w / k)), max(8, round(h / k))


def cell_m(box, shape) -> tuple[float, float]:
    """(dx, dy): a pixel's width and height in metres."""
    h, w = shape
    lat = (box[1] + box[3]) / 2
    return (box[2] - box[0]) * m_per_lon(lat) / w, (box[3] - box[1]) * M_LAT / h


def parse_tiff(data: bytes) -> np.ndarray:
    z = np.array(Image.open(io.BytesIO(data)), dtype=np.float32)       # a copy: asarray gives a read-only view
    z[(z <= NODATA + 1) | ~np.isfinite(z)] = np.nan
    return z


def measured_surface(pts: np.ndarray, box, area_m2: float, per_cell: float = 3.0, reach: int = 2) -> tuple[np.ndarray, float]:
    """A height grid from the robot's own heights (rows of lon, lat, metres): about `per_cell` points per cell over the
    mowed area, the median per cell, empty cells within `reach` cells filled by inverse distance, a 3x3 smoothing;
    NaN where the robot has not been. Returns (grid relative to its lowest cell, cell size in metres)."""
    lat = (box[1] + box[3]) / 2
    kx = m_per_lon(lat)
    res = float(np.clip(math.sqrt(per_cell * max(area_m2, 1.0) / max(len(pts), 1)), 0.5, 4.0))
    w = max(2, math.ceil((box[2] - box[0]) * kx / res))
    h = max(2, math.ceil((box[3] - box[1]) * M_LAT / res))
    col = np.clip(((pts[:, 0] - box[0]) * kx / res).astype(int), 0, w - 1)
    row = np.clip(((box[3] - pts[:, 1]) * M_LAT / res).astype(int), 0, h - 1)
    cells: dict = {}
    for i, v in zip((row * w + col).tolist(), pts[:, 2].tolist()):
        cells.setdefault(i, []).append(v)
    z = np.full(h * w, np.nan, dtype=np.float64)
    for i, vals in cells.items():
        z[i] = float(np.median(vals))
    z = z.reshape(h, w)

    def shifted(a, dy, dx):
        out = np.full_like(a, np.nan)
        ys, yd = (slice(dy, None), slice(None, -dy or None)) if dy >= 0 else (slice(None, dy), slice(-dy, None))
        xs, xd = (slice(dx, None), slice(None, -dx or None)) if dx >= 0 else (slice(None, dx), slice(-dx, None))
        out[ys, xs] = a[yd, xd]
        return out

    acc, wt = np.zeros_like(z), np.zeros_like(z)                      # fill the gaps from the cells around them
    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            if dy == dx == 0:
                continue
            n = shifted(z, dy, dx)
            ok = np.isfinite(n)
            wgt = 1.0 / (dy * dy + dx * dx)
            acc[ok] += n[ok] * wgt
            wt[ok] += wgt
    filled = np.where(np.isfinite(z), z, np.where(wt > 0, acc / np.where(wt > 0, wt, 1), np.nan))
    acc, wt = np.zeros_like(z), np.zeros_like(z)                      # a light smoothing over 3x3
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            n = shifted(filled, dy, dx)
            ok = np.isfinite(n)
            acc[ok] += n[ok]
            wt[ok] += 1
    smooth = np.where(np.isfinite(filled), acc / np.where(wt > 0, wt, 1), np.nan)
    if not np.isfinite(smooth).any():
        return smooth, res
    return (smooth - np.nanmin(smooth)).astype(np.float32), res


def _box_mean(a: np.ndarray, k: int) -> np.ndarray:
    """Mean over a (2k+1) square around each cell (edges use the cells that exist); NaN counts as missing."""
    if k < 1:
        return a.astype(np.float64)
    ok = np.isfinite(a)
    vals = np.where(ok, a, 0.0).astype(np.float64)

    def window_sum(x):
        p = np.pad(x, ((k + 1, k), (k + 1, k)))
        c = p.cumsum(0).cumsum(1)
        n = 2 * k + 1
        return c[n:, n:] - c[:-n, n:] - c[n:, :-n] + c[:-n, :-n]

    s, n = window_sum(vals), window_sum(ok.astype(np.float64))
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(n > 0, s / n, np.nan)


def resample(z: np.ndarray, box, to_box, to_shape) -> np.ndarray:
    """`z` (over `box`) at the cell centres of a grid over `to_box` (nearest cell; NaN outside `box`)."""
    h, w = to_shape
    lon = to_box[0] + (np.arange(w) + 0.5) * (to_box[2] - to_box[0]) / w
    lat = to_box[3] - (np.arange(h) + 0.5) * (to_box[3] - to_box[1]) / h
    col = np.floor((lon - box[0]) / (box[2] - box[0]) * z.shape[1]).astype(int)
    row = np.floor((box[3] - lat) / (box[3] - box[1]) * z.shape[0]).astype(int)
    out = np.full(to_shape, np.nan, dtype=np.float64)
    cv, rv = (col >= 0) & (col < z.shape[1]), (row >= 0) & (row < z.shape[0])
    out[np.ix_(rv, cv)] = z[np.ix_(row[rv], col[cv])]
    return out


def blend(lidar: np.ndarray | None, lidar_box, robot: np.ndarray | None, robot_box) -> tuple[np.ndarray, list, float]:
    """One ground for the 3D view: the robot's own heights where it has mowed, the lidar around them shifted by the
    median difference where both exist, and the step left at the seam eased out over SEAM_M (2026-10-10).
    Heights are relative to the median of the grid's border, so where `dem_tile` finally levels the ground out (to 0)
    it only takes away the edge's own ups and downs.
    Returns (grid in metres, its box, the share of cells that came from the robot)."""
    if lidar is None and robot is None:
        raise LookupError("no terrain yet")
    if lidar is None:
        z, box, share = robot.astype(np.float64), robot_box, 1.0
    else:
        box = lidar_box
        L = lidar.astype(np.float64)
        if robot is None:
            z, share = L, 0.0
        else:
            r = resample(robot, robot_box, box, L.shape)
            mask = np.isfinite(r) & np.isfinite(L)
            if not mask.any():
                z, share = L, 0.0
            else:
                L2 = L + float(np.median(r[mask] - L[mask]))
                k = max(1, round(SEAM_M / max(cell_m(box, L.shape)[0], 0.1)))
                near = _box_mean(np.where(mask, r - L2, np.nan), k)            # the robot's step, from the cells near by
                reach = _box_mean(mask.astype(np.float64), k)                    # ~0.5 at the seam, 0 a SEAM_M out
                eased = L2 + np.nan_to_num(near) * np.clip(2 * np.nan_to_num(reach), 0, 1)
                z = np.where(mask, r, eased)
                share = float(mask.sum() / np.isfinite(L).sum())
    if not np.isfinite(z).any():
        raise LookupError("no terrain yet")
    z = _box_mean(z, 1)                                               # soften the robot's coarser cells
    edge = np.concatenate([z[0], z[-1], z[1:-1, 0], z[1:-1, -1]])
    z = z - float(np.nanmedian(edge))
    return np.nan_to_num(z, nan=0.0).astype(np.float32), list(box), share


def dem_tile(z: np.ndarray, box, tz: int, tx: int, ty: int, flat_m: float = FLAT_M, taper_m: float = TAPER_M,
             smooth: np.ndarray | None = None) -> np.ndarray:
    """The heights (m) at the pixel centres of one 256-px web-mercator tile, bilinear from the grid (row 0 north). Past
    the grid's edge the nearest edge height (from `smooth`, the grid smoothed, when given; blended in over the first
    EDGE_SMOOTH_M) carries on for `flat_m`, then eases to 0 over `taper_m`."""
    n = 2 ** tz
    px = (np.arange(TILE) + 0.5) / TILE
    lon = (tx + px) / n * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (ty + px) / n))))
    h, w = z.shape
    C, R = np.meshgrid((lon - box[0]) / (box[2] - box[0]) * w - 0.5, (box[3] - lat) / (box[3] - box[1]) * h - 0.5)
    Cc, Rc = np.clip(C, 0, w - 1), np.clip(R, 0, h - 1)
    c0, r0 = np.minimum(np.floor(Cc).astype(int), w - 2 if w > 1 else 0), np.minimum(np.floor(Rc).astype(int), h - 2 if h > 1 else 0)
    c1, r1 = np.minimum(c0 + 1, w - 1), np.minimum(r0 + 1, h - 1)
    fc, fr = Cc - c0, Rc - r0
    bil = lambda g: (g[r0, c0] * (1 - fc) + g[r0, c1] * fc) * (1 - fr) + (g[r1, c0] * (1 - fc) + g[r1, c1] * fc) * fr
    v = bil(z)
    dx, dy = cell_m(box, z.shape)
    out_m = np.hypot((C - Cc) * dx, (R - Rc) * dy)
    if smooth is not None and (out_m > 0).any():
        w = np.clip(out_m / EDGE_SMOOTH_M, 0, 1)
        v = v * (1 - w) + bil(smooth) * w
    return (v * np.clip(1 - (out_m - flat_m) / taper_m, 0, 1)).astype(np.float32)


def terrarium_png(hm: np.ndarray) -> bytes:
    """Heights as a Terrarium PNG (MapLibre raster-dem, encoding "terrarium"): h = R*256 + G + B/256 - 32768."""
    v = np.clip(hm.astype(np.float64), -32768, 32767) + 32768.0
    r = np.floor(v / 256)
    g = np.floor(v - r * 256)
    b = np.floor((v - np.floor(v)) * 256)
    buf = io.BytesIO()
    Image.fromarray(np.dstack([r, g, b]).astype(np.uint8)).save(buf, "PNG")          # RGB from the three channels
    return buf.getvalue()


def inside_rings(box, shape, rings: list, margin_m: float = 0.0) -> np.ndarray:
    """Which cells of a grid over `box` lie inside any of the rings ([lon, lat] outlines), or within `margin_m` of one
    (cell centres; even-odd rule)."""
    h, w = shape
    lat0 = (box[1] + box[3]) / 2
    kx = m_per_lon(lat0)
    cx = (np.arange(w) + 0.5) * (box[2] - box[0]) / w * kx                    # metres east of the box's west edge
    cy = (box[3] - box[1]) * M_LAT - (np.arange(h) + 0.5) * (box[3] - box[1]) / h * M_LAT   # north of its south edge
    X, Y = np.meshgrid(cx, cy)
    inside = np.zeros(shape, dtype=bool)
    near = np.zeros(shape, dtype=bool)
    for ring in rings:
        r = np.asarray(ring, dtype=float)
        if len(r) < 3:
            continue
        px, py = (r[:, 0] - box[0]) * kx, (r[:, 1] - box[1]) * M_LAT
        x1, y1, x2, y2 = px, py, np.roll(px, -1), np.roll(py, -1)
        hit = np.zeros(shape, dtype=bool)
        for a, b, c, d in zip(x1, y1, x2, y2):
            if b == d:
                continue
            cross = ((b > Y) != (d > Y)) & (X < a + (Y - b) * (c - a) / (d - b))
            hit ^= cross
        inside |= hit
        if margin_m > 0:
            for a, b, c, d in zip(x1, y1, x2, y2):
                L = max((c - a) ** 2 + (d - b) ** 2, 1e-9)
                t = np.clip(((X - a) * (c - a) + (Y - b) * (d - b)) / L, 0, 1)
                near |= np.hypot(X - (a + t * (c - a)), Y - (b + t * (d - b))) <= margin_m
    return inside | near


class Elevation:
    """The yard's terrain from two sources: "lidar" (USGS, fetched once and kept) and "robot" (the robot's own RTK
    heights, worked out again when new ones arrive). Each keeps its grid, meta and rendered products."""

    def __init__(self, data_dir: str):
        self.dir = Path(data_dir) / "elevation"
        self.src: dict = {"lidar": {"z": None, "meta": None}, "robot": {"z": None, "meta": None, "key": None}}
        self.g: dict = {"key": None, "z": None, "box": None, "meta": None, "tiles": OrderedDict()}   # the 3D ground
        self._lock = asyncio.Lock()
        self._load()

    def _load(self) -> None:
        try:
            self.src["lidar"]["meta"] = json.loads((self.dir / "dem.json").read_text())
            self.src["lidar"]["z"] = np.load(self.dir / "dem.npy")
        except (OSError, ValueError):
            self.src["lidar"]["z"] = self.src["lidar"]["meta"] = None

    async def ensure(self, bounds, force: bool = False) -> dict:
        """The lidar terrain for the map's bounds: the kept grid if it covers them, else a fresh fetch."""
        L = self.src["lidar"]
        async with self._lock:
            if not force and L["z"] is not None and L["meta"] and covers(L["meta"]["box"], bounds):
                return L["meta"]
            box = box_around(bounds)
            w, h = grid_size(box)
            async with httpx.AsyncClient(timeout=60) as http:
                r = await http.get(EXPORT, params={
                    "bbox": ",".join(f"{v:.7f}" for v in box), "bboxSR": 4326, "imageSR": 4326, "size": f"{w},{h}",
                    "format": "tiff", "pixelType": "F32", "noData": NODATA, "interpolation": "RSP_BilinearInterpolation",
                    "f": "image"})
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
                raise ValueError(f"USGS answered {r.status_code}: {r.text[:160]}")
            z = await asyncio.to_thread(parse_tiff, r.content)
            if not np.isfinite(z).any():
                raise ValueError("USGS has no elevation for this place")
            meta = {"box": box, "w": int(z.shape[1]), "h": int(z.shape[0]), "res_m": round(cell_m(box, z.shape)[0], 3),
                    "fetched": int(time.time()), "source": SOURCE, "note": NOTE, "relative": False,
                    "min": round(float(np.nanmin(z)), 2), "max": round(float(np.nanmax(z)), 2)}
            self.dir.mkdir(parents=True, exist_ok=True)
            np.save(self.dir / "dem.npy", z)
            (self.dir / "dem.json").write_text(json.dumps(meta))
            L.update(z=z, meta=meta)
            return meta

    async def ensure_robot(self, points: list, bounds, area_m2: float, runs: int, rings: list | None = None) -> dict:
        """The surface from the robot's heights, cut to the mowing areas (`rings`: their [lon, lat] outlines) plus about
        a metre; worked out again only when the heights or the areas changed."""
        R = self.src["robot"]
        if len(points) < 30:
            raise LookupError(f"only {len(points)} RTK-fixed heights recorded so far; the next mow adds one a second")
        key = (len(points), round(points[-1][2], 3), tuple(round(b, 6) for b in bounds), len(rings or []),
               sum(len(r) for r in rings or []))
        if R["key"] == key and R["z"] is not None:
            return R["meta"]
        box = box_around(bounds)
        z, res = await asyncio.to_thread(measured_surface, np.asarray(points, dtype=np.float64), box, area_m2)
        if rings:
            z = np.where(inside_rings(box, z.shape, rings, margin_m=1.0), z, np.nan).astype(np.float32)
        if not np.isfinite(z).any():
            raise LookupError("the recorded heights do not fall inside the map")
        meta = {"box": box, "w": int(z.shape[1]), "h": int(z.shape[0]), "res_m": round(res, 2), "fetched": int(time.time()),
                "source": "The robot's own RTK heights", "relative": True, "points": len(points), "runs": runs,
                "note": f"{len(points):,} RTK-fixed heights from {runs} run{'s' if runs != 1 else ''}, in {res:.1f} m cells; "
                        "heights are above the lowest point the robot reached.",
                "min": 0.0, "max": round(float(np.nanmax(z)), 2)}
        R.update(z=z, meta=meta, key=key)
        return meta

    async def ground(self, use_lidar: bool, use_robot: bool) -> dict:
        """The 3D view's ground from whichever sources are ready (see `blend`), worked out again when one changed.
        Its version goes into the tile URLs, so a browser never mixes tiles of two grounds."""
        L, R = self.src["lidar"], self.src["robot"]
        lz = L["z"] if use_lidar and L["z"] is not None else None
        rz = R["z"] if use_robot and R["z"] is not None else None
        key = ((L["meta"] or {}).get("fetched") if lz is not None else None, R["key"] if rz is not None else None)
        if self.g["key"] == key and self.g["z"] is not None:
            return self.g["meta"]
        z, box, share = await asyncio.to_thread(blend, lz, (L["meta"] or {}).get("box"), rz, (R["meta"] or {}).get("box"))
        lat = (box[1] + box[3]) / 2
        dx, dy = (FLAT_M + TAPER_M) / m_per_lon(lat), (FLAT_M + TAPER_M) / M_LAT
        sources = [s for s, used in (("robot", rz is not None), ("lidar", lz is not None)) if used]
        meta = {"version": hashlib.sha1(repr(key).encode()).hexdigest()[:10], "sources": sources,
                "bounds": [round(box[0] - dx, 7), round(box[1] - dy, 7), round(box[2] + dx, 7), round(box[3] + dy, 7)],
                "minzoom": DEM_ZOOMS[0], "maxzoom": DEM_ZOOMS[1], "relief_m": round(float(z.max() - z.min()), 2),
                "robot_share": round(share, 3), "encoding": "terrarium"}
        k = max(1, round(EDGE_SMOOTH_M / max(cell_m(box, z.shape)[0], 0.1)))
        smooth = (await asyncio.to_thread(_box_mean, z, k)).astype(np.float32)
        self.g.update(key=key, z=z, smooth=smooth, box=box, meta=meta, tiles=OrderedDict())
        return meta

    async def ground_tile(self, tz: int, tx: int, ty: int) -> bytes:
        """One Terrarium tile of the 3D ground (call `ground` first); kept for the most recent TILE_CACHE tiles."""
        g = self.g
        k = (tz, tx, ty)
        if k in g["tiles"]:
            g["tiles"].move_to_end(k)
            return g["tiles"][k]
        data = await asyncio.to_thread(lambda: terrarium_png(dem_tile(g["z"], g["box"], tz, tx, ty, smooth=g.get("smooth"))))
        g["tiles"][k] = data
        while len(g["tiles"]) > TILE_CACHE:
            g["tiles"].popitem(last=False)
        return data
