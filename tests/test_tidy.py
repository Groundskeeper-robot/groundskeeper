"""Map cleanup geometry: each tool keeps to the tolerance, the direction bias holds, and refusals say why."""
import math
import random
import time

import numpy as np
import pytest

from app import tidy as t


def noisy_circle(r=1.6, n=14, noise=0.03, seed=1, centre=(5.0, -3.0)):
    rnd = random.Random(seed)
    return [(centre[0] + (r + rnd.uniform(-noise, noise)) * math.cos(2 * math.pi * i / n),
             centre[1] + (r + rnd.uniform(-noise, noise)) * math.sin(2 * math.pi * i / n)) for i in range(n)]


def noisy_rect(w=8.0, h=5.0, step=0.3, noise=0.03, seed=2):
    rnd = random.Random(seed)
    edge = []
    for (x0, y0), (x1, y1) in [((0, 0), (w, 0)), ((w, 0), (w, h)), ((w, h), (0, h)), ((0, h), (0, 0))]:
        n = int(math.hypot(x1 - x0, y1 - y0) / step)
        for i in range(n):
            f = i / n
            edge.append((x0 + f * (x1 - x0) + rnd.uniform(-noise, noise), y0 + f * (y1 - y0) + rnd.uniform(-noise, noise)))
    return edge


def all_inside(points, ring, tol=0.006):
    P = np.asarray(points, float)
    inn = t._inside_many(P, ring)
    return bool(np.all(inn | (t._dists(P, ring, True) <= tol)))


def test_round_makes_a_circle_that_only_grows_for_a_no_go_zone():
    pts = noisy_circle()
    r = t.run("round", pts, True, 0.10, "grow")
    assert r["ok"] and r["max_move"] <= 0.10 and r["after"] >= 16
    c, rad, err = t.fit_circle(r["pts"])
    assert err < 0.03                                              # it is a circle now
    assert all_inside(t._densify(pts, True), r["pts"])            # nothing of the old zone is left outside


def test_round_for_an_area_shrinks_and_paths_cannot_be_rounded():
    pts = noisy_circle(r=6, n=40, noise=0.04)
    r = t.run("round", pts, True, 0.10, "shrink")
    assert r["ok"] and all_inside(t._densify(r["pts"], True), pts)
    assert not t.run("round", pts[:10], False, 0.10, None)["ok"]


def test_round_says_how_much_tolerance_it_needs():
    pts = noisy_circle(noise=0.10, seed=4)
    r = t.run("round", pts, True, 0.05, "grow")
    assert not r["ok"] and 0.05 < r["needs_tol"] <= t.MAX_TOL
    assert t.run("round", pts, True, r["needs_tol"], "grow")["ok"]
    assert "not round enough" in t.run("round", noisy_rect(), True, 0.30, "grow")["why"]


def test_straighten_turns_a_wobbly_rectangle_into_four_sides_within_the_tolerance():
    pts = noisy_rect()
    for bias in (None, "grow", "shrink"):
        r = t.run("straighten", pts, True, 0.10, bias)
        assert r["ok"] and r["max_move"] <= 0.10 and r["after"] <= 8, (bias, r.get("after"))
    grow = t.run("straighten", pts, True, 0.10, "grow")["pts"]
    shrink = t.run("straighten", pts, True, 0.10, "shrink")["pts"]
    assert all_inside(t._densify(pts, True), grow)
    assert all_inside(t._densify(shrink, True), pts)


def test_square_gives_right_angles():
    pts = [(0.02, -0.03), (6.05, 0.04), (5.96, 4.03), (-0.04, 3.97)]
    r = t.run("square", pts, True, 0.10, None)
    assert r["ok"] and r["after"] == 4
    q = r["pts"]
    assert all(abs(t.turn_deg(q[i - 1], q[i], q[(i + 1) % 4]) - 90) < 0.01 for i in range(4))
    assert not t.run("square", noisy_circle(), True, 0.30, None)["ok"]


def test_cleanup_drops_duplicates_and_points_on_a_straight_line():
    pts = [(0, 0), (0.01, 0), (1, 0), (2, 0.001), (3, 0), (3, 2), (0, 2)]
    r = t.run("cleanup", pts, True, 0.10, None)
    assert r["ok"] and r["after"] == 4 and r["max_move"] < 0.01


def test_smooth_keeps_real_corners_and_the_tolerance():
    pts = noisy_rect(noise=0.04)
    r = t.run("smooth", pts, True, 0.10, "shrink")
    assert r["ok"] and r["max_move"] <= 0.10
    corners = [(0, 0), (8, 0), (8, 5), (0, 5)]
    assert all(min(math.hypot(x - cx, y - cy) for x, y in r["pts"]) < 0.25 for cx, cy in corners)


def test_open_paths_keep_their_ends_and_have_no_bias():
    rnd = random.Random(3)
    path = [(i * 0.5, rnd.uniform(-0.03, 0.03)) for i in range(20)]
    r = t.run("straighten", path, False, 0.10, None)
    assert r["ok"] and r["after"] == 2 and r["max_move"] <= 0.10
    assert abs(r["pts"][0][0] - 0) < 0.1 and abs(r["pts"][-1][0] - 9.5) < 0.1


def test_a_shape_never_comes_out_crossing_itself_more_than_before():
    bow = [(0, 0), (4, 0), (4, 3), (2.05, 1.4), (1.95, 1.6), (0, 3)]
    for tool in t.TOOLS:
        r = t.run(tool, bow, True, 0.30, "shrink")
        if r["ok"]:
            assert t.self_crossings(r["pts"], True) <= t.self_crossings(bow, True), tool


def test_nothing_to_change_is_not_a_change():
    square = [(0, 0), (4, 0), (4, 4), (0, 4)]
    assert t.run("cleanup", square, True)["why"] == "nothing to change"
    assert t.recommend(square, True) is None


def test_a_driven_boundary_is_fast_enough():
    rnd = random.Random(5)
    pts = []
    for i in range(250):
        a = 2 * math.pi * i / 250
        rad = 20 + 4 * math.sin(3 * a) + rnd.uniform(-0.04, 0.04)
        pts.append((rad * math.cos(a), rad * math.sin(a)))
    start = time.time()
    for tool in ("cleanup", "straighten", "smooth"):
        t.run(tool, pts, True, 0.10, "shrink")
    assert time.time() - start < 6


@pytest.mark.parametrize("tool", t.TOOLS)
def test_tolerance_is_clamped(tool):
    r = t.run(tool, noisy_circle(), True, 5.0, "grow")
    assert not r["ok"] or r["max_move"] <= t.MAX_TOL


# -- properties: whatever a tool returns keeps both rules (shapes from the code review that broke the first version) --
def wedge(tip_deg, length, noise, n, seed):
    rnd = random.Random(seed)
    half = math.radians(tip_deg) / 2
    a, b = (length * math.cos(half), length * math.sin(half)), (length * math.cos(half), -length * math.sin(half))
    side1 = [(a[0] * f + rnd.uniform(-noise, noise), a[1] * f + rnd.uniform(-noise, noise)) for f in [i / n for i in range(1, n)]]
    side2 = [(b[0] * f + rnd.uniform(-noise, noise), b[1] * f + rnd.uniform(-noise, noise)) for f in [i / n for i in range(n - 1, 0, -1)]]
    return [(0.0, 0.0)] + side1 + [a, b] + side2


def notched(seed):
    rnd = random.Random(seed)
    base = [(0, 0), (6, 0), (6, 4), (3.5, 4), (3.5, 2.5), (2.5, 2.5), (2.5, 4), (0, 4)]
    out = []
    for (x0, y0), (x1, y1) in zip(base, base[1:] + base[:1]):
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) / 0.25))
        out += [(x0 + (x1 - x0) * i / n + rnd.uniform(-0.03, 0.03), y0 + (y1 - y0) * i / n + rnd.uniform(-0.03, 0.03)) for i in range(n)]
    return out


def variants(pts):
    yield pts
    yield pts[::-1]                                   # the other orientation
    yield [(-x, y) for x, y in pts]                   # the robot's mirrored frame (x = west)


SHAPES = [("wedge", wedge(10.6, 5.2, 0.03, 14, s)) for s in range(4)] + \
         [("notched", notched(s)) for s in range(3)] + \
         [("dense circle", [(3 * math.cos(2 * math.pi * i / 250), 3 * math.sin(2 * math.pi * i / 250)) for i in range(250)])] + \
         [("octagon", [(2 * math.cos(2 * math.pi * i / 8), 2 * math.sin(2 * math.pi * i / 8)) for i in range(8)])]


@pytest.mark.parametrize("bias", ["grow", "shrink"])
@pytest.mark.parametrize("name,shape", SHAPES, ids=[f"{n}{i}" for i, (n, _) in enumerate(SHAPES)])
def test_every_accepted_result_keeps_the_tolerance_and_the_bias(name, shape, bias):
    for pts in variants(shape):
        for tol in (0.02, 0.10, 0.20, 0.30):
            for tool in t.TOOLS:
                r = t.run(tool, pts, True, tol, bias)
                if r["ok"]:
                    assert r["max_move"] <= tol + 1e-9, (tool, tol)
                    assert t.bias_gap(pts, r["pts"], bias) <= t.BIAS_SLACK, (name, tool, tol, bias)


def test_cleanup_on_a_dense_curve_stays_within_the_tolerance():
    arc = [(10 * math.cos(i / 200), 10 * math.sin(i / 200)) for i in range(200)]
    r = t.run("cleanup", arc, False, 0.02, None)
    assert not r["ok"] or r["max_move"] <= 0.02


def test_a_path_keeps_its_real_end():
    assert t.dedupe([(0, 0), (1, 0), (2, 0), (3, 0), (3.04, 0)], False)[-1] == (3.04, 0)
