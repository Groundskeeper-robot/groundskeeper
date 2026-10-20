"""Edge passes vs fill: which parts of a track ran along a boundary.

The robot mows the edge first (read_mower_area_params: edge_area_first, edge_circles 3, gap ~0.3 m) and also edges
around no-go zones (mower_ngz_edge_sw). The robot reports no per-sample phase, so this is geometric: a sample is an
edge pass when it is within EDGE_DIST_M of an area or no-go boundary AND travelling roughly parallel to that
boundary. The parallel test keeps the fill stripes' turns at the boundary (which run across it) out. Short flickers
are smoothed away so a lap reads as one colour.
"""
import math
from collections import Counter

EDGE_DIST_M = 1.0        # three laps 0.3 m apart sit within about 0.9 m of the boundary
PARALLEL_DEG = 30
CELL_M = 2.0
MIN_STEP_M = 0.05        # below this the heading is unknown (standing still)
SMOOTH = 7               # majority window (samples, about 1 s each)
MIN_EDGE_RUN = 12        # an "edge" stretch shorter than this (samples) is a fill stripe brushing the boundary


def _seg_dist(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


class EdgeIndex:
    """Boundary segments of closed rings (areas, no-go zones), bucketed on a grid for fast nearest lookups."""

    def __init__(self, rings: list[list[tuple[float, float]]]):
        self.segs: list[tuple[float, float, float, float, float]] = []   # ax, ay, bx, by, angle (mod pi)
        self.grid: dict[tuple[int, int], list[int]] = {}
        pad = EDGE_DIST_M
        for ring in rings:
            if len(ring) < 3:
                continue
            for i in range(len(ring)):
                (ax, ay), (bx, by) = ring[i], ring[(i + 1) % len(ring)]
                if (ax, ay) == (bx, by):
                    continue
                k = len(self.segs)
                self.segs.append((ax, ay, bx, by, math.atan2(by - ay, bx - ax) % math.pi))
                for gx in range(math.floor((min(ax, bx) - pad) / CELL_M), math.floor((max(ax, bx) + pad) / CELL_M) + 1):
                    for gy in range(math.floor((min(ay, by) - pad) / CELL_M), math.floor((max(ay, by) + pad) / CELL_M) + 1):
                        self.grid.setdefault((gx, gy), []).append(k)

    def nearest(self, x: float, y: float) -> tuple[float, float] | None:
        """(distance, segment angle) of the nearest boundary within EDGE_DIST_M, else None."""
        best = None
        for k in self.grid.get((math.floor(x / CELL_M), math.floor(y / CELL_M)), ()):
            ax, ay, bx, by, ang = self.segs[k]
            d = _seg_dist(x, y, ax, ay, bx, by)
            if d <= EDGE_DIST_M and (best is None or d < best[0]):
                best = (d, ang)
        return best

    def classify(self, pts: list[tuple[float, float] | None], open_end: bool = False) -> list[int | None]:
        """1 = edge pass, 0 = fill, None = no position. Heading comes from the neighbouring samples."""
        n = len(pts)
        raw: list[int | None] = [None] * n
        last = 0
        for i, p in enumerate(pts):
            if p is None:
                continue
            a = next((pts[j] for j in range(i - 1, max(-1, i - 4), -1) if pts[j] is not None), None)
            b = next((pts[j] for j in range(i + 1, min(n, i + 4)) if pts[j] is not None), None)
            u, v = a or p, b or p
            dx, dy = v[0] - u[0], v[1] - u[1]
            near = self.nearest(*p)
            if near is None:
                last = 0
            elif math.hypot(dx, dy) >= MIN_STEP_M:
                diff = abs((math.atan2(dy, dx) % math.pi) - near[1])
                last = 1 if min(diff, math.pi - diff) <= math.radians(PARALLEL_DEG) else 0
            raw[i] = last                       # standing still: keep what it was doing
        return _drop_short(_smooth(raw), open_end)


def _smooth(vals: list[int | None]) -> list[int | None]:
    half = SMOOTH // 2
    out = list(vals)
    for i, v in enumerate(vals):
        if v is None:
            continue
        window = [w for w in vals[max(0, i - half): i + half + 1] if w is not None]
        out[i] = Counter(window).most_common(1)[0][0]
    return out


def _drop_short(vals: list[int | None], open_end: bool = False) -> list[int | None]:
    """Turn edge stretches shorter than MIN_EDGE_RUN into fill. open_end: the track is still growing, so a stretch
    that runs to the end may become long enough; leave it as edge."""
    out, i, n = list(vals), 0, len(vals)
    while i < n:
        if out[i] != 1:
            i += 1
            continue
        j = i
        while j < n and out[j] in (1, None):
            j += 1
        if sum(1 for v in out[i:j] if v == 1) < MIN_EDGE_RUN and not (open_end and j == n):
            for k in range(i, j):
                if out[k] == 1:
                    out[k] = 0
        i = j
    return out
