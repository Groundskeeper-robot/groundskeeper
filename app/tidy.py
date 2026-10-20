"""Map cleanup (Map Studio → Tidy): straighter lines, rounder circles, fewer stray points.

Pure geometry on a feature's own points [(x, y), ...] in metres. Every tool obeys the same rules:

- No point of the outline moves further than the tolerance (Hausdorff distance between old and new outline,
  sampled every 5 cm along both).
- A direction bias keeps the robot on the safe side: "grow" (no-go zones) means every old point ends up inside or on
  the new shape, so a zone never shrinks towards what it protects; "shrink" (areas, no-vision zones) means the new
  shape lies inside the old one, so the robot never mows or ignores its cameras past where it was taught. Paths have
  no bias.
- A shape never comes out crossing itself more than the robot's own copy did.
- A tool that cannot meet the rules says why (and, when more tolerance would do, how much) instead of returning
  something worse.
"""
from __future__ import annotations

import math

import numpy as np

Pt = tuple[float, float]
TOOLS = ("cleanup", "straighten", "round", "smooth", "square")
TOOL_LABELS = {"cleanup": "Clean up", "straighten": "Straighten", "round": "Round", "smooth": "Smooth", "square": "Square"}
BIAS = {"nogozones": "grow", "areas": "shrink", "novisionzones": "shrink"}
DEFAULT_TOL = 0.10
MIN_TOL, MAX_TOL = 0.02, 0.30
DUP_M = 0.05          # points closer than this to the previous one are duplicates
FLAT_M = 0.01         # a point this close to the line through its neighbours adds nothing
CORNER_DEG = 35       # a turn this sharp is a real corner: smoothing keeps it
ROUND_MAX_ERR = 0.30  # Round is offered when no point is further than this from the fitted circle…
ROUND_REL_ERR = 0.20  # …and no further than this fraction of the radius
SQUARE_DEG = 12       # Square needs four corners within this of 90°
BIAS_SLACK = 0.003    # the bias holds to within 3 mm (sampling every 5 cm along an outline)
MAX_POINTS_ROBOT = 252


class TidyError(Exception):
    """Why a tool cannot run; `needs` is the tolerance (m) it would need, when raising it would help."""

    def __init__(self, msg: str, needs: float | None = None):
        super().__init__(msg)
        self.needs = needs


# -- basic geometry --------------------------------------------------------------------------------------------
def _sub(a, b): return (a[0] - b[0], a[1] - b[1])
def _add(a, b): return (a[0] + b[0], a[1] + b[1])
def _mul(a, k): return (a[0] * k, a[1] * k)
def _cross(a, b): return a[0] * b[1] - a[1] * b[0]
def _dot(a, b): return a[0] * b[0] + a[1] * b[1]
def _len(a): return math.hypot(a[0], a[1])


def signed_area(pts: list[Pt]) -> float:
    return sum(_cross(a, b) for a, b in zip(pts, pts[1:] + pts[:1])) / 2


def _seg_dist(p, a, b) -> float:
    d = _sub(b, a)
    L2 = _dot(d, d)
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, _dot(_sub(p, a), d) / L2))
    return _len(_sub(p, _add(a, _mul(d, t))))


def _segs(pts, closed):
    return list(zip(pts, pts[1:] + (pts[:1] if closed else [])))


def _nearest(P: np.ndarray, pts, closed) -> tuple[np.ndarray, np.ndarray]:
    """For each row of P (m×2): the distance to the outline and the index of the nearest segment. Chunked, so a long
    boundary densified every 5 cm stays within a few MB."""
    Q = np.asarray(pts, float)
    if len(P) == 0:
        return np.zeros(0), np.zeros(0, int)
    if len(Q) == 1:
        return np.hypot(*(P - Q[0]).T), np.zeros(len(P), int)
    A = Q if closed else Q[:-1]
    B = np.roll(Q, -1, axis=0) if closed else Q[1:]
    D = B - A
    L2 = (D * D).sum(1)
    L2[L2 == 0] = 1e-18
    dist, idx = np.empty(len(P)), np.empty(len(P), int)
    step = max(1, 200_000 // len(A))
    for s in range(0, len(P), step):
        Pc = P[s:s + step]
        t = np.clip(((Pc[:, None, :] - A[None]) * D[None]).sum(2) / L2[None], 0.0, 1.0)
        d2 = ((Pc[:, None, :] - (A[None] + t[..., None] * D[None])) ** 2).sum(2)
        j = d2.argmin(1)
        idx[s:s + step], dist[s:s + step] = j, np.sqrt(d2[np.arange(len(Pc)), j])
    return dist, idx


def _closest(P: np.ndarray, pts, closed) -> np.ndarray:
    """The nearest point on the outline for each row of P."""
    Q = np.asarray(pts, float)
    _, j = _nearest(P, pts, closed)
    A = Q[j]
    B = Q[(j + 1) % len(Q)] if closed else Q[np.minimum(j + 1, len(Q) - 1)]
    D = B - A
    L2 = (D * D).sum(1)
    L2[L2 == 0] = 1e-18
    t = np.clip(((P - A) * D).sum(1) / L2, 0.0, 1.0)
    return A + t[:, None] * D


def _dists(P: np.ndarray, pts, closed) -> np.ndarray:
    """Distance from each row of P (m×2) to the outline."""
    return _nearest(P, pts, closed)[0]


def dist_to_outline(p, pts, closed) -> float:
    return float(_dists(np.asarray([p], float), pts, closed)[0])


def _inside_many(P: np.ndarray, ring) -> np.ndarray:
    R = np.asarray(ring, float)
    x1, y1 = R[:, 0][None], R[:, 1][None]
    x2, y2 = np.roll(R[:, 0], -1)[None], np.roll(R[:, 1], -1)[None]
    px, py = P[:, :1], P[:, 1:2]
    crosses = (y1 > py) != (y2 > py)
    with np.errstate(divide="ignore", invalid="ignore"):
        xi = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
    return ((crosses & (px < xi)).sum(1) % 2) == 1


def inside(p, ring) -> bool:
    return bool(_inside_many(np.asarray([p], float), ring)[0])


def _densify(pts, closed, step=0.05) -> np.ndarray:
    """Points every `step` metres along the outline, so a Hausdorff check sees edges, not just corners."""
    out = []
    for a, b in _segs(pts, closed):
        a, b = np.asarray(a, float), np.asarray(b, float)
        n = max(1, int(np.hypot(*(b - a)) / step))
        out.append(a[None] + (np.arange(n)[:, None] / n) * (b - a)[None])
    if not closed:
        out.append(np.asarray([pts[-1]], float))
    return np.vstack(out)


def max_move(old, new, closed) -> float:
    """How far the outline moved: symmetric Hausdorff distance, sampled every 5 cm along both outlines."""
    if not old or not new:
        return 0.0
    return float(max(_dists(_densify(old, closed), new, closed).max(), _dists(_densify(new, closed), old, closed).max()))


def turn_deg(a, b, c) -> float:
    v1, v2 = _sub(b, a), _sub(c, b)
    if _len(v1) < 1e-9 or _len(v2) < 1e-9:
        return 0.0
    return abs(math.degrees(math.atan2(_cross(v1, v2), _dot(v1, v2))))


def self_crossings(pts, closed) -> int:
    segs = _segs(pts, closed)
    n, count = len(segs), 0
    for i in range(n):
        (p1, p2) = segs[i]
        for j in range(i + 2, n):
            if closed and i == 0 and j == n - 1:
                continue
            (p3, p4) = segs[j]
            d1, d2 = _cross(_sub(p2, p1), _sub(p3, p1)), _cross(_sub(p2, p1), _sub(p4, p1))
            if d1 * d2 >= 0:
                continue
            d3, d4 = _cross(_sub(p4, p3), _sub(p1, p3)), _cross(_sub(p4, p3), _sub(p2, p3))
            if d3 * d4 < 0:
                count += 1
    return count


# -- building blocks -------------------------------------------------------------------------------------------
def dedupe(pts, closed) -> list[Pt]:
    out = []
    for p in pts:
        if not out or _len(_sub(p, out[-1])) >= DUP_M:
            out.append(p)
    if closed and len(out) > 3 and _len(_sub(out[0], out[-1])) < DUP_M:
        out.pop()
    if not closed and out[-1] != pts[-1]:     # a path keeps its real end
        if len(out) > 1:
            out[-1] = pts[-1]
        else:
            out.append(pts[-1])
    return out


def untwist(pts, closed) -> list[Pt]:
    """Swap back two points recorded in the wrong order: a segment crossing the one two steps on is a bow-tie, not
    shape (the live "All Yard" has three, 9–43 cm long)."""
    pts = list(pts)
    n = len(pts)
    if n < 4:
        return pts
    for _ in range(3):
        fixed = False
        for i in range(n if closed else n - 3):
            p1, p2, p3, p4 = pts[i], pts[(i + 1) % n], pts[(i + 2) % n], pts[(i + 3) % n]
            d1, d2 = _cross(_sub(p2, p1), _sub(p3, p1)), _cross(_sub(p2, p1), _sub(p4, p1))
            d3, d4 = _cross(_sub(p4, p3), _sub(p1, p3)), _cross(_sub(p4, p3), _sub(p2, p3))
            if d1 * d2 < 0 and d3 * d4 < 0:
                pts[(i + 1) % n], pts[(i + 2) % n] = p3, p2
                fixed = True
        if not fixed:
            break
    return pts


def drop_flat(pts, closed, eps=FLAT_M) -> list[Pt]:
    """Remove points that sit on a straight line: a point goes only if every original point it stood for stays within
    eps of the segment that replaces them (so removals cannot add up along a gentle curve)."""
    n = len(pts)
    keep = list(range(n))
    floor = 3 if closed else 2

    def between(a, c):
        return range(a + 1, c) if c > a else [*range(a + 1, n), *range(0, c)]

    changed = True
    while changed and len(keep) > floor:
        changed = False
        i = 0 if closed else 1
        while i < (len(keep) if closed else len(keep) - 1) and len(keep) > floor:
            a, c = keep[i - 1], keep[(i + 1) % len(keep)]
            A, C = pts[a], pts[c]
            if all(_seg_dist(pts[k], A, C) < eps for k in between(a, c)):
                keep.pop(i)
                changed = True
            else:
                i += 1
    return [pts[k] for k in keep]


def douglas_peucker(pts, closed, eps) -> list[int]:
    """Indices kept by Douglas-Peucker. A closed ring is split at two far-apart points first."""
    n = len(pts)
    if n <= (3 if closed else 2):
        return list(range(n))

    def dp(i, j, keep):
        a, b = pts[i % n], pts[j % n]
        best, idx = 0.0, None
        for k in range(i + 1, j):
            d = _seg_dist(pts[k % n], a, b)
            if d > best:
                best, idx = d, k
        if idx is not None and best > eps:
            dp(i, idx, keep)
            keep.add(idx % n)
            dp(idx, j, keep)

    if not closed:
        keep = {0, n - 1}
        dp(0, n - 1, keep)
        return sorted(keep)
    far = max(range(n), key=lambda k: _len(_sub(pts[k], pts[0])))
    start = max(range(n), key=lambda k: _len(_sub(pts[k], pts[far])))
    other = max(range(n), key=lambda k: _len(_sub(pts[k], pts[start])))
    i, j = sorted((start, other))
    keep = {i, j}
    dp(i, j, keep)
    dp(j, i + n, keep)
    out = sorted(keep)
    if len(out) < 3:
        out = sorted({i, j, max(range(n), key=lambda k: _seg_dist(pts[k], pts[i], pts[j]))})
    return out


def _fit_line(run):
    """Least-squares line through points: (centroid, unit direction)."""
    cx = sum(p[0] for p in run) / len(run)
    cy = sum(p[1] for p in run) / len(run)
    sxx = sum((p[0] - cx) ** 2 for p in run)
    syy = sum((p[1] - cy) ** 2 for p in run)
    sxy = sum((p[0] - cx) * (p[1] - cy) for p in run)
    ang = 0.5 * math.atan2(2 * sxy, sxx - syy)
    return (cx, cy), (math.cos(ang), math.sin(ang))


def _intersect(p, d, q, e):
    den = _cross(d, e)
    if abs(den) < math.sin(math.radians(8)):     # nearly parallel: no useful corner
        return None
    t = _cross(_sub(q, p), e) / den
    return _add(p, _mul(d, t))


def _project(p, line):
    c, d = line
    return _add(c, _mul(d, _dot(_sub(p, c), d)))


def fit_runs(pts, keep, closed, limit) -> list[Pt]:
    """Replace each run between kept corners with its best-fit line; corners become the lines' intersections (the old
    corner is kept where the lines are nearly parallel or the intersection lands more than `limit` away)."""
    n, k = len(pts), len(keep)
    runs = []
    for a in range(k if closed else k - 1):
        i, j = keep[a], keep[(a + 1) % k]
        idx = list(range(i, j + 1)) if j > i else list(range(i, n)) + list(range(0, j + 1))
        runs.append(_fit_line([pts[t] for t in idx]))
    out = []
    for a in range(k):
        old = pts[keep[a]]
        if not closed and a == 0:
            out.append(_project(old, runs[0]))
        elif not closed and a == k - 1:
            out.append(_project(old, runs[-1]))
        else:
            l1, l2 = runs[a - 1], runs[a % len(runs)]
            x = _intersect(l1[0], l1[1], l2[0], l2[1])
            out.append(x if x is not None and _len(_sub(x, old)) <= limit else old)
    return out


def bias_gap(old, new, bias) -> float:
    """How far the bias is broken, exactly: grow → the furthest any part of the old outline lies outside the new
    shape; shrink → the furthest any part of the new outline lies outside the old one. 0 when it holds."""
    if bias == "grow":
        O = _densify(old, True)
        out = ~_inside_many(O, new)
        return float(_dists(O[out], new, True).max()) if out.any() else 0.0
    if bias == "shrink":
        N = _densify(new, True)
        out = ~_inside_many(N, old)
        return float(_dists(N[out], old, True).max()) if out.any() else 0.0
    return 0.0


def apply_bias(old, new, bias) -> list[Pt]:
    """Push a closed shape just far enough that the bias holds, edge by edge: each new edge moves out (grow) or in
    (shrink) by the true distance of the worst stretch of old outline on the wrong side of it. Local, so a long
    driven boundary with a few tiny self-crossings is handled like any other. run() checks the result exactly."""
    if bias not in ("grow", "shrink") or len(new) < 3:
        return new
    O = _densify(old, True)
    for k in range(16):
        N = np.asarray(new, float)
        inn = _inside_many(O, new)
        wrong = ~inn if bias == "grow" else inn            # old outline outside a no-go zone / inside an area
        if not wrong.any():
            return new
        dist, j = _nearest(O[wrong], new, True)
        w = np.zeros(len(N))
        np.maximum.at(w, j, dist)
        if w.max() < 0.001:
            return new
        D = np.roll(N, -1, axis=0) - N
        L = np.hypot(D[:, 0], D[:, 1])
        L[L == 0] = 1e-9
        U = D / L[:, None]
        nrm = np.stack([U[:, 1], -U[:, 0]], axis=1)
        # Outward per edge, by looking just off its middle: a driven boundary can have tiny twisted loops where the
        # shape's overall orientation points the wrong way.
        probe = N + D / 2 + nrm * np.minimum(0.01, L / 4)[:, None]
        nrm[_inside_many(probe, new)] *= -1
        # A capped corner leaves a sliver behind; later passes push a little harder so it converges.
        w = np.where(w > 0, w * (1.0 + 0.25 * (k // 3)) + 0.002, 0.0) * (1.0 if bias == "grow" else -1.0)
        lines = [(_add(tuple(N[i]), _mul(tuple(nrm[i]), w[i])), tuple(U[i])) for i in range(len(N))]
        out = []
        for i in range(len(N)):
            (p1, d1), (p2, d2) = lines[i - 1], lines[i]
            x = _intersect(p1, d1, p2, d2)
            big = max(abs(w[i - 1]), abs(w[i]), 0.005)
            # A corner may travel w / sin(half its angle): far at a sharp tip, about w on a nearly straight run. Beyond
            # that the joint slides along an unmoved neighbour and folds a long driven boundary.
            a, c = _sub(tuple(N[i - 1]), tuple(N[i])), _sub(tuple(N[(i + 1) % len(N)]), tuple(N[i]))
            half = 0.5 * math.atan2(abs(_cross(a, c)), _dot(a, c)) if _len(a) and _len(c) else math.pi / 2
            limit = 1.05 * big / max(math.sin(half), 0.05)
            if x is None or _len(_sub(x, tuple(N[i]))) > limit:      # move along the normals that moved instead
                m = _add(_mul(tuple(nrm[i - 1]), 1.0 if w[i - 1] else 0.0), _mul(tuple(nrm[i]), 1.0 if w[i] else 0.0))
                ml = _len(m) or 1.0
                x = _add(tuple(N[i]), _mul(m, (big if (w[i - 1] or w[i]) > 0 else -big) / ml))
            out.append(x)
        if bias == "shrink":
            # A corner that pokes out (a fitted corner can come out a little sharper than the old one) goes back onto
            # the old outline; pushing whole edges hardly moves a thin tip.
            V = np.asarray(out, float)
            poke = ~_inside_many(V, old)
            if poke.any():
                V[poke] = _closest(V[poke], old, True)
                out = [tuple(v) for v in V]
        new = out
    return new


# -- circle and rectangle fits ---------------------------------------------------------------------------------
def fit_circle(pts) -> tuple[Pt, float, float]:
    """Least-squares (Kåsa) circle: (centre, radius, worst distance of a point from it)."""
    n = len(pts)
    mx, my = sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n
    u = [(p[0] - mx, p[1] - my) for p in pts]
    suu = sum(a * a for a, _ in u); svv = sum(b * b for _, b in u); suv = sum(a * b for a, b in u)
    suuu = sum(a ** 3 for a, _ in u); svvv = sum(b ** 3 for _, b in u)
    suvv = sum(a * b * b for a, b in u); svuu = sum(b * a * a for a, b in u)
    det = suu * svv - suv * suv
    if abs(det) < 1e-12:
        raise TidyError("the points lie on a line")
    uc = (0.5 * (suuu + suvv) * svv - 0.5 * (svvv + svuu) * suv) / det
    vc = (0.5 * (svvv + svuu) * suu - 0.5 * (suuu + suvv) * suv) / det
    c = (uc + mx, vc + my)
    r = math.sqrt(uc * uc + vc * vc + (suu + svv) / n)
    err = max(abs(_len(_sub(p, c)) - r) for p in pts)
    return c, r, err


def enclosing_circle(pts) -> tuple[Pt, float]:
    """The smallest circle around all points (brute force over pairs and triples; fine for a zone's few points)."""
    P = [tuple(p) for p in pts]
    best = None

    def covers(c, r):
        return all(_len(_sub(p, c)) <= r + 1e-9 for p in P)

    for i in range(len(P)):
        for j in range(i + 1, len(P)):
            c = ((P[i][0] + P[j][0]) / 2, (P[i][1] + P[j][1]) / 2)
            r = _len(_sub(P[i], c))
            if (best is None or r < best[1]) and covers(c, r):
                best = (c, r)
    for i in range(len(P)):
        for j in range(i + 1, len(P)):
            for k in range(j + 1, len(P)):
                (ax, ay), (bx, by), (cx, cy) = P[i], P[j], P[k]
                d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
                if abs(d) < 1e-12:
                    continue
                ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
                uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
                r = _len(_sub(P[i], (ux, uy)))
                if (best is None or r < best[1]) and covers((ux, uy), r):
                    best = ((ux, uy), r)
    return best


def circle_points(c, r, n, outside: bool, phase: float = 0.0) -> list[Pt]:
    """n points on a circle; outside=True places the polygon around the circle (its edges touch it), else inside."""
    R = r / math.cos(math.pi / n) if outside else r
    return [(c[0] + R * math.cos(phase + 2 * math.pi * i / n), c[1] + R * math.sin(phase + 2 * math.pi * i / n))
            for i in range(n)]


def circle_count(r: float) -> int:
    """Points for a circle of radius r: about every 40 cm, between 16 and 48."""
    return max(16, min(48, math.ceil(2 * math.pi * r / 0.4)))


def rectangle(corners) -> list[Pt]:
    """The rectangle closest to four corners: their centre, the average edge direction, the average side lengths."""
    c = (sum(p[0] for p in corners) / 4, sum(p[1] for p in corners) / 4)
    angs = [math.atan2(b[1] - a[1], b[0] - a[0]) for a, b in _segs(corners, True)]
    sx, sy = sum(math.cos(4 * a) for a in angs), sum(math.sin(4 * a) for a in angs)
    base = math.atan2(sy, sx) / 4                                   # mean direction modulo 90°
    base += round((angs[0] - base) / (math.pi / 2)) * (math.pi / 2)   # aligned with the first edge
    u, v = (math.cos(base), math.sin(base)), (-math.sin(base), math.cos(base))
    w = (_len(_sub(corners[1], corners[0])) + _len(_sub(corners[2], corners[3]))) / 2
    hgt = (_len(_sub(corners[2], corners[1])) + _len(_sub(corners[3], corners[0]))) / 2
    if _cross(_sub(corners[1], corners[0]), _sub(corners[2], corners[1])) < 0:
        v = _mul(v, -1)
    o = _sub(c, _add(_mul(u, w / 2), _mul(v, hgt / 2)))
    return [o, _add(o, _mul(u, w)), _add(o, _add(_mul(u, w), _mul(v, hgt))), _add(o, _mul(v, hgt))]


# -- the tools ------------------------------------------------------------------------------------------------------
def _too_far(what: str, moved: float):
    return TidyError(f"{what} moves the edge up to {math.ceil(moved * 100)} cm; set the tolerance to at least that", moved)


def _cleanup(pts, closed, tol, bias):
    new = drop_flat(dedupe(pts, closed), closed)
    return apply_bias(pts, new, bias) if closed else new


def _straighten(pts, closed, tol, bias):
    base = dedupe(pts, closed)
    crossings = self_crossings(pts, True) if closed else 0
    for eps in (tol / 2, tol / 3, tol / 5):
        keep = douglas_peucker(base, closed, eps)
        if len(keep) < (3 if closed else 2):
            continue
        new = fit_runs(base, keep, closed, limit=tol)
        if closed:
            new = apply_bias(pts, new, bias)
        if max_move(pts, new, closed) <= tol and (not closed or self_crossings(new, True) <= crossings):
            return new
    raise TidyError("could not straighten it within the tolerance; raise the tolerance or use Clean up")


def _round(pts, closed, tol, bias):
    if not closed:
        raise TidyError("only closed shapes can be rounded")
    c, r, err = fit_circle(pts)
    if err > ROUND_MAX_ERR or err > ROUND_REL_ERR * r:
        raise TidyError(f"not round enough: points are up to {err:.2f} m off the nearest circle")
    n = circle_count(r)
    options = []
    if bias == "grow":
        options.append(circle_points(c, max(_len(_sub(p, c)) for p in pts), n, outside=True))
        if len(pts) <= 60:
            ec, er = enclosing_circle(pts)
            options.append(circle_points(ec, er, n, outside=True))
    elif bias == "shrink":
        inner = float(_dists(np.asarray([c], float), pts, True)[0])        # nearest part of the outline
        if inside(c, pts) and inner > 0.05:
            options.append(circle_points(c, inner, n, outside=False))
    else:
        options.append(circle_points(c, r, n, outside=False))
    if not options:
        raise TidyError("its centre is too close to the edge to fit a circle inside it")
    moved, new = min(((max_move(pts, o, True), o) for o in options), key=lambda t: t[0])
    if moved > tol:
        raise _too_far("rounding it", moved)
    return new


def _smooth_pass(base, fixed, closed, cap):
    n, cur = len(base), list(base)
    for _ in range(12):
        nxt = list(cur)
        for i in range(n):
            if i in fixed:
                continue
            a, b = cur[i - 1], cur[(i + 1) % n]
            p = _add(cur[i], _mul(_sub(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), cur[i]), 0.5))
            d = _sub(p, base[i])
            if _len(d) > cap:                                   # never further than `cap` from where it was
                p = _add(base[i], _mul(d, cap / _len(d)))
            nxt[i] = p
        cur = nxt
    return cur


def _smooth(pts, closed, tol, bias):
    base = dedupe(pts, closed)
    n = len(base)
    if n < (4 if closed else 3):
        raise TidyError("too few points to smooth")
    fixed = set() if closed else {0, n - 1}
    for i in range(n):
        if (closed or 0 < i < n - 1) and turn_deg(base[i - 1], base[i], base[(i + 1) % n]) >= CORNER_DEG:
            fixed.add(i)
    crossings = self_crossings(pts, True) if closed else 0
    least = None
    for cap in (tol / 2, tol / 3, tol / 5):                     # gentler passes when the bias needs room
        new = drop_flat(_smooth_pass(base, fixed, closed, cap), closed, eps=min(0.02, tol / 5))
        if closed:
            new = apply_bias(pts, new, bias)
        moved = max_move(pts, new, closed)
        if closed and self_crossings(new, True) > crossings:
            continue
        if moved <= tol:
            return new
        least = moved if least is None else min(least, moved)
    if least is None:
        raise TidyError("smoothing would make the outline cross itself")
    raise _too_far("smoothing it", least)


def _square(pts, closed, tol, bias):
    if not closed:
        raise TidyError("only closed shapes can be squared")
    base = list(pts)
    if len(base) > 4:
        try:
            base = _straighten(pts, closed, tol, None)
        except TidyError:
            pass
    if len(base) != 4:
        raise TidyError(f"Square needs a four-cornered shape; this one has {len(base)} corners")
    turns = [turn_deg(base[i - 1], base[i], base[(i + 1) % 4]) for i in range(4)]
    if any(abs(t - 90) > SQUARE_DEG for t in turns):
        raise TidyError("its corners are not close enough to right angles")
    new = apply_bias(pts, rectangle(base), bias)
    moved = max_move(pts, new, True)
    if moved > tol:
        raise _too_far("squaring it", moved)
    return new


_TOOL_FN = {"cleanup": _cleanup, "straighten": _straighten, "round": _round, "smooth": _smooth, "square": _square}


def run(tool: str, pts: list[Pt], closed: bool, tol: float = DEFAULT_TOL, bias: str | None = None) -> dict:
    """One tool on one outline. {"ok": True, "pts", "max_move", "before", "after"}, or {"ok": False, "why"} plus
    "needs_tol" when a larger tolerance (up to MAX_TOL) would let it run."""
    if tool not in _TOOL_FN:
        raise TidyError(f"unknown tool {tool!r}")
    tol = max(MIN_TOL, min(MAX_TOL, float(tol)))
    pts = [(float(x), float(y)) for x, y in pts]
    if len(pts) < (3 if closed else 2):
        return {"ok": False, "tool": tool, "why": "too few points"}
    ref = untwist(pts, closed)          # the same recorded points in their right order: what the rules protect
    try:
        new = _TOOL_FN[tool](ref, closed, tol, bias)
    except TidyError as exc:
        out = {"ok": False, "tool": tool, "why": str(exc)}
        if exc.needs is not None and exc.needs <= MAX_TOL:
            out["needs_tol"] = math.ceil(exc.needs * 100) / 100
        return out
    moved = max_move(pts, new, closed)
    if len(new) == len(pts) and moved < 0.002:
        return {"ok": False, "tool": tool, "why": "nothing to change"}
    # The tools aim for these; this is the guarantee. Nothing leaves here that breaks either rule.
    if moved > tol + 1e-9:
        out = {"ok": False, "tool": tool, "why": f"it would move the edge up to {math.ceil(moved * 100)} cm, over the tolerance"}
        if moved <= MAX_TOL:
            out["needs_tol"] = math.ceil(moved * 100) / 100
        return out
    if closed and bias_gap(ref, new, bias) > BIAS_SLACK:
        side = "grow beyond" if bias == "shrink" else "leave part of the old zone outside"
        return {"ok": False, "tool": tool, "why": f"it would {side} the old outline; try another tool or a smaller tolerance"}
    if len(new) > MAX_POINTS_ROBOT:
        return {"ok": False, "tool": tool, "why": f"it would leave {len(new)} points, over the robot's {MAX_POINTS_ROBOT}"}
    return {"ok": True, "tool": tool, "pts": new, "max_move": round(moved, 3), "before": len(pts), "after": len(new)}


def recommend(pts: list[Pt], closed: bool, tol: float = DEFAULT_TOL, bias: str | None = None) -> dict | None:
    """The one tool worth suggesting for a shape in the whole-map review (possibly one that needs more tolerance),
    or None."""
    if closed:
        r = run("round", pts, closed, tol, bias)
        if r["ok"] or r.get("needs_tol"):
            return r
        q = run("square", pts, closed, tol, bias)
        if q["ok"]:
            return q
    s = run("straighten", pts, closed, tol, bias)
    if s["ok"] and s["after"] <= 0.8 * s["before"]:
        return s
    c = run("cleanup", pts, closed, tol, bias)
    return c if c["ok"] else None
