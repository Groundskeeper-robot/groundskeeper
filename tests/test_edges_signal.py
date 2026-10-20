"""Edge-pass classification, the signal grid and background command confirmation (2026-10-06)."""
import asyncio

from app import control, coverage
from app.edges import EdgeIndex
from app.store import Store

SQUARE = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)]


def lap(inset, step=0.4):
    """One lap `inset` metres inside the square, sampled every `step` m."""
    a, b = inset, 20 - inset
    pts, x, y = [], a, a
    for (tx, ty) in ((b, a), (b, b), (a, b), (a, a)):
        while abs(x - tx) + abs(y - ty) > 1e-9:
            dx, dy = tx - x, ty - y
            d = (dx * dx + dy * dy) ** 0.5
            k = min(step, d) / d
            x, y = x + dx * k, y + dy * k
            pts.append((x, y))
    return pts


def stripes():
    """Fill stripes at 45 degrees across the square, turning near the boundary."""
    pts = []
    for c in range(4, 36, 1):
        line = [(x / 2, c - x / 2) for x in range(0, 41) if 1.2 <= x / 2 <= 18.8 and 1.2 <= c - x / 2 <= 18.8]
        pts += line if c % 2 else line[::-1]
    return pts


def test_laps_along_the_boundary_are_edge_and_stripes_are_fill():
    idx = EdgeIndex([SQUARE])
    laps = lap(0.15) + lap(0.45) + lap(0.75)
    labels = idx.classify(laps + stripes())
    edge, fill = labels[:len(laps)], labels[len(laps):]
    assert sum(edge) / len(edge) > 0.9
    assert sum(fill) / len(fill) < 0.1


def test_a_short_brush_along_the_boundary_is_not_an_edge_pass():
    idx = EdgeIndex([SQUARE])
    brush = [(5 + i * 0.4, 0.5) for i in range(6)]              # 2.4 m alongside the edge
    across = [(10, 5 + i * 0.4) for i in range(30)]
    assert not any(idx.classify(across + brush + across[::-1]))


def test_an_open_end_is_left_as_edge_until_it_is_long_enough():
    idx = EdgeIndex([SQUARE])
    start = [(5 + i * 0.4, 0.5) for i in range(5)]
    assert idx.classify(start, open_end=True)[-1] == 1
    assert idx.classify(start, open_end=False)[-1] == 0


def test_signal_grid_averages_per_cell():
    pts = [{"x": 0.5, "y": 0.5, "halow": -60, "rtk_status": 4, "sats": 20}] * 5 +           [{"x": 0.6, "y": 0.4, "halow": -80, "rtk_status": 5, "sats": 10}] * 5 +           [{"x": 9.0, "y": 9.0, "halow": -50, "rtk_status": 4, "sats": 20}] * 2     # too few samples
    cells = coverage.signal_grid(pts, "halow")
    assert len(cells) == 1 and cells[0]["value"] == -70 and cells[0]["n"] == 10
    assert coverage.signal_grid(pts, "rtk")[0]["value"] == 50


class FakeCloud:
    state = "connected"

    def publish(self, cmd, payload):
        pass


def test_panel_commands_confirm_in_the_background(tmp_path):
    store = Store(str(tmp_path))
    store.set_setting("control_enabled", True)
    t = {"planning": 1, "planning_paused": 0}

    async def ctx(args):
        return {"snapshot_age_s": 1, "cloud_state": "connected"}

    def expect(cmd):
        return asyncio.get_running_loop().create_future()

    c = control.Controller(store, FakeCloud(), ctx, expect, lambda: t)
    c.poll_s = 0.01

    async def go():
        r = await c.send("pause", wait="background")
        assert r["sent"] and r["confirmed"] is None and c.last["confirmed"] is None
        t["planning_paused"] = 1
        await asyncio.sleep(0.1)
        return r

    asyncio.run(go())
    assert c.last["confirmed"] is True and "paused" in c.last["confirm"]
    assert any(e["kind"] == "command_confirmed" for e in store.events(10))


def stripes_at(deg, spacing=0.5, step=0.4):
    """Fill stripes at `deg` across the square, so their ends brush the boundary at a shallow angle."""
    import math
    a = math.radians(deg)
    ux, uy, nx, ny = math.cos(a), math.sin(a), -math.sin(a), math.cos(a)
    pts, k = [], 0
    for off in [i * spacing for i in range(-60, 61)]:
        line = []
        for t in [j * step for j in range(-100, 101)]:
            x, y = 10 + nx * off + ux * t, 10 + ny * off + uy * t
            if 1.0 <= x <= 19.0 and 1.0 <= y <= 19.0:
                line.append((x, y))
        if line:
            pts += line if k % 2 else line[::-1]
            k += 1
    return pts


def test_live_batches_end_up_with_the_same_labels_as_the_whole_track():
    """Review: provisional labels at each batch end were frozen on the live map. The server re-sends the last
    EDGE_RELABEL context labels and the client patches them; the result must match classifying everything at once."""
    from app import main
    idx = EdgeIndex([SQUARE])
    track = lap(0.15) + lap(0.45) + stripes_at(20) + lap(0.75)
    whole = idx.classify(track, open_end=True)
    client: list[list] = []                       # [ts, edge]
    for start in range(0, len(track), 3):          # the live map asks about every 3 s, ~3 rows
        batch = list(range(start, min(start + 3, len(track))))
        context = list(range(max(0, start - main.EDGE_CONTEXT), start))
        labels = idx.classify([track[i] for i in context + batch], open_end=True)
        fixes = dict(list(zip(context, labels[:len(context)]))[-main.EDGE_RELABEL:])
        for row in client[-64:]:
            if row[0] in fixes:
                row[1] = fixes[row[0]]
        client += [[i, lab] for i, lab in zip(batch, labels[len(context):])]
    got = [lab for _, lab in client]
    assert sum(1 for a, b in zip(got, whole) if a != b) == 0
