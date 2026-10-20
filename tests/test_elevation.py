"""The 3D ground's maths (app/elevation.py) on grids whose answers are known: planes, gaps, edges."""
import io
import math

import numpy as np
from PIL import Image

from app import elevation as el

BOX = [-76.5010, 39.0000, -76.4990, 39.0010]          # ~173 m x 111 m


def plane(rise_per_m=0.1, shape=(100, 120)):
    dx, dy = el.cell_m(BOX, shape)
    cols = np.arange(shape[1]) * dx
    return (270 + rise_per_m * cols[None, :] + np.zeros(shape)).astype(np.float32)


def test_the_box_covers_the_map_with_a_margin_and_the_grid_stays_bounded():
    bounds = [-76.5005, 39.0003, -76.4995, 39.0007]
    box = el.box_around(bounds)
    assert el.covers(box, bounds) and not el.covers(bounds, box)
    w, h = el.grid_size(box)
    assert 8 <= w <= el.MAX_PX and 8 <= h <= el.MAX_PX
    assert abs(el.cell_m(box, (h, w))[0] - el.RES_M) < 0.05


def test_a_float_tiff_parses_with_its_nodata_as_gaps():
    z = np.full((6, 8), 275.5, dtype=np.float32)
    z[2, 3] = el.NODATA
    buf = io.BytesIO()
    Image.fromarray(z).save(buf, "TIFF")
    out = el.parse_tiff(buf.getvalue())
    assert out.shape == (6, 8) and np.isnan(out[2, 3]) and out[0, 0] == 275.5


def test_the_robots_heights_make_a_surface_with_the_right_slope():
    rng = np.random.default_rng(1)
    lon = BOX[0] + rng.random(4000) * (BOX[2] - BOX[0])
    lat = BOX[1] + rng.random(4000) * (BOX[3] - BOX[1])
    x = (lon - BOX[0]) * el.m_per_lon((BOX[1] + BOX[3]) / 2)
    h = 280 + 0.05 * x + rng.normal(0, 0.02, 4000)                 # a 1-in-20 rise to the east, 2 cm of RTK noise
    z, res = el.measured_surface(np.c_[lon, lat, h], BOX, area_m2=18000)
    assert 0.5 <= res <= 4.0 and float(np.nanmin(z)) == 0.0
    gy, gx = np.gradient(z, res, res)
    s = np.degrees(np.arctan(np.hypot(gx, gy)))
    assert abs(float(np.nanmedian(s)) - math.degrees(math.atan(0.05))) < 0.6


def test_a_few_points_give_big_cells_and_gaps_stay_gaps():
    pts = np.array([[BOX[0] + 0.0001, BOX[1] + 0.0001, 280.0], [BOX[0] + 0.00012, BOX[1] + 0.0001, 280.2]] * 20)
    z, res = el.measured_surface(pts, BOX, area_m2=2000)
    assert res == 4.0 and np.isfinite(z).sum() < z.size / 4      # far from the points there is nothing


def test_too_few_heights_say_so(tmp_path):
    import asyncio
    import pytest
    e = el.Elevation(str(tmp_path))
    with pytest.raises(LookupError, match="next mow"):
        asyncio.run(e.ensure_robot([(BOX[0], BOX[1], 280.0)] * 5, [BOX[0], BOX[1], BOX[2], BOX[3]], 2000, 1))


def test_heights_come_from_telemetry_and_for_older_runs_from_the_raw_snapshots(tmp_path):
    from app.store import Store
    st = Store(str(tmp_path))
    old = st.start_run(1000.0, 5, None)                           # before hgt was recorded: raw snapshots only
    st.db.execute("UPDATE runs SET ended = 1100 WHERE id = ?", (old,))
    st.add_raw(1010.0, {"RTKMSG": {"status": "4", "hgt": 279.1, "lan": 39.0005, "lon": -76.5005}})
    st.add_raw(1020.0, {"RTKMSG": {"status": "1", "hgt": 279.9, "lan": 39.0005, "lon": -76.5005}})   # no fix: left out
    new = st.start_run(2000.0, 5, None)
    st.add_telemetry({"ts": 2001.0, "run_id": new, "lon": -76.5004, "lat": 39.0004, "rtk_status": 4, "hgt": 278.5})
    st.add_telemetry({"ts": 2002.0, "run_id": new, "lon": -76.5004, "lat": 39.0004, "rtk_status": 5, "hgt": 278.0})
    st.add_telemetry({"ts": 2003.0, "run_id": None, "lon": -76.5004, "lat": 39.0004, "rtk_status": 4, "hgt": 278.0})
    pts, runs = st.height_points()
    assert sorted(p[2] for p in pts) == [278.5, 279.1] and runs == 2


def test_cells_outside_the_areas_are_cut():
    ring = [[BOX[0] + 0.0004, BOX[1] + 0.0003], [BOX[0] + 0.0010, BOX[1] + 0.0003],
            [BOX[0] + 0.0010, BOX[1] + 0.0007], [BOX[0] + 0.0004, BOX[1] + 0.0007], [BOX[0] + 0.0004, BOX[1] + 0.0003]]
    m = el.inside_rings(BOX, (40, 60), [ring])
    assert m.any() and not m.all()
    rows, cols = np.nonzero(m)
    assert cols.min() > 5 and cols.max() < 45                       # the ring spans ~30-50 % of the box east-west
    wider = el.inside_rings(BOX, (40, 60), [ring], margin_m=3.0)
    assert wider.sum() > m.sum()


# -- the 3D view's ground (2026-10-10) ---------------------------------------------------------------------------------
def tile_of(lon, lat, z):
    n = 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    return int(x), int(y)


def decode_terrarium(png: bytes) -> np.ndarray:
    a = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), dtype=np.float64)
    return a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768


def test_terrarium_tiles_round_trip_heights_to_a_256th_of_a_metre():
    hm = np.linspace(-2.5, 7.3, el.TILE * el.TILE).reshape(el.TILE, el.TILE).astype(np.float32)
    back = decode_terrarium(el.terrarium_png(hm))
    assert np.abs(back - hm).max() <= 1 / 256 + 1e-6


def test_a_tile_inside_the_grid_follows_a_plane_rising_east():
    z = plane(0.05) - 270                                # 0 m at the west edge, ~8 m at the east
    tx, ty = tile_of(-76.5000, 39.0005, 19)              # a z19 tile (~55 m) in the middle of the box
    t = el.dem_tile(z, BOX, 19, tx, ty)
    assert t.shape == (el.TILE, el.TILE)
    rows = t.mean(axis=0)
    assert np.all(np.diff(rows) > 0)                     # rises west to east
    assert np.ptp(t[:, 128]) < 0.02                      # flat north-south
    lon_mid = (tx + 0.5) / 2 ** 19 * 360 - 180           # the tile's centre column sits at this longitude
    expect = 0.05 * (lon_mid - BOX[0]) * el.m_per_lon(39.0005)
    assert abs(float(t[:, 128].mean()) - expect) < 0.15


def test_past_the_grid_the_edge_carries_on_then_levels_out():
    z = np.full((40, 40), 3.0, dtype=np.float32)
    tx, ty = tile_of(BOX[2], 39.0005, 18)                # straddles the east edge
    t = el.dem_tile(z, BOX, 18, tx, ty, flat_m=150, taper_m=20)   # the tile reaches ~100 m past the edge
    assert float(t.min()) > 2.99 and float(t.max()) < 3.01   # inside and just past the edge: the edge's height
    far = el.dem_tile(z, BOX, 18, tx + 3, ty, flat_m=50, taper_m=20)   # ~300 m east: levelled out
    assert float(far.max()) == 0.0


def test_blend_keeps_the_robot_where_it_mowed_and_shifts_the_lidar_to_meet_it():
    shape = (60, 80)
    lidar = plane(0.02, shape) - 270                      # the old ground
    robot = lidar + 0.86                                  # the robot's datum sits 0.86 m higher...
    robot[20:40, 30:50] += 0.5                            # ...and the yard was raised 0.5 m here since
    robot[:15, :] = np.nan                                # where it never drove
    robot[:, :10] = np.nan
    z, box, share = el.blend(lidar, BOX, robot, BOX)
    assert box == BOX and z.shape == shape
    edge = np.concatenate([z[0], z[-1], z[1:-1, 0], z[1:-1, -1]])
    assert abs(float(np.median(edge))) < 1e-4            # heights sit around the border's typical level
    assert 0.6 < share < 0.75                             # the robot's cells over the lidar's
    inside = z[25:35, 35:45] - z[25:35, 55:65]            # the raised patch vs the plain lawn east of it
    plane_step = (lidar[25:35, 35:45] - lidar[25:35, 55:65])
    assert abs(float(np.median(inside - plane_step)) - 0.5) < 0.06
    # the lidar-only edge lines up with the robot's surface: no step where the robot's cells stop
    seam = np.abs(z[16, 20:70] - z[14, 20:70]) - np.abs(lidar[16, 20:70] - lidar[14, 20:70])
    assert float(np.abs(seam).max()) < 0.05


def test_blend_with_one_source_uses_that_source():
    lidar = plane(0.02) - 270
    z, box, share = el.blend(lidar, BOX, None, None)
    assert share == 0.0 and box == BOX and abs(float(np.ptp(z)) - float(np.ptp(lidar))) < 0.05
    z2, box2, share2 = el.blend(None, None, lidar, BOX)
    assert share2 == 1.0 and z2.shape == lidar.shape


def test_resample_onto_the_same_grid_is_the_grid():
    z = np.arange(12 * 10, dtype=np.float64).reshape(12, 10)
    assert np.array_equal(el.resample(z, BOX, BOX, z.shape), z)
