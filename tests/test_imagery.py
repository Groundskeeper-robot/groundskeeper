"""The imagery service: the yard image, probes, context tiles, and above all the privacy gates."""
import asyncio
import json
import math

import pytest
from PIL import Image

from app import imagery as im
from app.imagery import kinds
from app.imagery.catalog import custom_entry

from .fakehttp import FakeHTTP, FakeResponse, jpeg, no_network, noise, size_of, zxy

LON, LAT = -76.5, 39.0
PLACES = {"annapolis_md": (-76.50, 38.97), "raleigh": (-78.64, 35.78), "denver": (-104.99, 39.74),
          "toronto": (-79.38, 43.65), "paris": (2.35, 48.86), "amsterdam": (4.90, 52.37)}


def box(half_m=40, lon=LON, lat=LAT):
    return im.square(lon, lat, half_m)


def merc(b):
    return (*im.to_merc(b[0], b[1]), *im.to_merc(b[2], b[3]))


def offline(tmp_path, **kw) -> im.Imagery:
    """An Imagery whose HTTP client is a recorder that refuses to fetch unless told how to answer."""
    imgry = im.Imagery(str(tmp_path), **kw)
    imgry.http = imgry.http_strict = FakeHTTP(no_network)
    return imgry


def test_mercator_round_trip():
    x, y = im.to_merc(LON, LAT)
    lon, lat = im.from_merc(x, y)
    assert lon == pytest.approx(LON, abs=1e-10) and lat == pytest.approx(LAT, abs=1e-10)


def test_plan_size_uses_native_resolution_and_caps_it():
    mpp, w, h = im.plan_size(box(40), 0.0762)          # an 80 m square at 7.6 cm
    assert mpp == pytest.approx(0.0762) and w == pytest.approx(80 / 0.0762, rel=0.02) and h == pytest.approx(w, rel=0.02)
    mpp, w, h = im.plan_size(box(400), 0.0762)         # 800 m would be 10,500 px: capped
    assert max(w, h) <= im.MAX_PX and mpp > 0.0762


def test_tile_zoom_and_range():
    z = im.tile_zoom(0.3, LAT, 256, 22)
    m_at_z = 2 * im.HALF * math.cos(math.radians(LAT)) / (256 * 2 ** z)
    assert m_at_z <= 0.3 < m_at_z * 2                    # the first zoom at least as sharp as asked
    assert im.tile_zoom(0.01, LAT, 256, 19) == 19        # capped at the source's max zoom
    tx0, ty0, tx1, ty1 = im.tile_range(*merc(box()), 19)
    assert tx0 <= tx1 and ty0 <= ty1 and (tx1 - tx0 + 1) * (ty1 - ty0 + 1) < 30


def test_stitching_puts_tiles_where_they_belong():
    """Each fake tile is one flat colour named after its x, y: a pixel's colour tells which tile it came from."""
    def answer(url, params, headers):
        z, x, y = zxy(url)
        return FakeResponse(200, jpeg(Image.new("RGB", (256, 256), (x % 256, y % 256, 7))))

    http = FakeHTTP(answer)
    src = {"id": "t", "name": "test", "kind": "tiles", "url": "https://t.test/{z}/{x}/{y}.png", "max_zoom": 19, "native_m": 0.3}
    kind = kinds.get("tiles")
    b = merc(box(60))                                   # 120 m: at least two tile centres each way
    mpp, w, h = im.plan_size(box(60), 0.3)
    out = asyncio.run(kind.fetch_box(http, src, "", b, w, h))
    assert out.size == (w, h)
    z = kind.zoom_for(src, b, w)
    x0, y0, x1, y1 = b
    span = 2 * im.HALF / 2 ** z
    tx0, ty0, tx1, ty1 = im.tile_range(*b, z)
    assert len(http.calls) == (tx1 - tx0 + 1) * (ty1 - ty0 + 1)
    checked = 0
    for tx in range(tx0, tx1 + 1):            # the centre of every tile inside the box lands on that tile's colour
        for ty in range(ty0, ty1 + 1):
            mx, my = (tx + 0.5) * span - im.HALF, im.HALF - (ty + 0.5) * span
            if not (x0 < mx < x1 and y0 < my < y1):
                continue
            px = out.getpixel((int((mx - x0) / (x1 - x0) * w), int((y1 - my) / (y1 - y0) * h)))
            assert abs(px[0] - tx % 256) <= 3 and abs(px[1] - ty % 256) <= 3    # JPEG rounds a little
            checked += 1
    assert checked >= 2


def test_blank_detection():
    assert im.is_blank(Image.new("RGB", (64, 64), (0, 0, 0)))
    assert im.is_blank(Image.new("RGB", (64, 64), (255, 255, 255)))
    assert not im.is_blank(noise(64, 64))


def test_keyed_sources_need_their_key(tmp_path):
    imgry = offline(tmp_path)
    r = asyncio.run(imgry.probe("esri_world", LON, LAT))
    assert r["ok"] is None and "API key" in r["detail"]
    with pytest.raises(im.ImageryError, match="http"):
        imgry.save_local(custom_entry({"kind": "xyz", "url": "ftp://nope"}))
    assert imgry.http.calls == []


def test_a_failed_fetch_keeps_the_last_image(tmp_path, monkeypatch):
    imgry = offline(tmp_path)

    async def ok(self, src, bbox, native_m=None):
        return noise(128, 128), 0.6

    async def fail(self, src, bbox, native_m=None):
        raise im.ImageryError("down")

    monkeypatch.setattr(im.Imagery, "render", ok)
    first = asyncio.run(imgry.ensure_yard(box()))
    assert first["source"]["id"] == "usgs_naip"
    monkeypatch.setattr(im.Imagery, "render", fail)
    assert asyncio.run(imgry.ensure_yard(box(), force=False)) == first          # cached, not refetched
    with pytest.raises(im.ImageryError):
        asyncio.run(imgry.ensure_yard(box(), force=True))                      # an explicit refetch reports it
    assert imgry.yard_meta == first


def test_the_yard_image_is_fetched_at_the_source_resolution(tmp_path):
    imgry = offline(tmp_path)
    imgry.http = imgry.http_strict = FakeHTTP()
    imgry.configure({"provider": "md_imap_threeinch"}, {})
    meta = asyncio.run(imgry.ensure_yard(box(40, *PLACES["annapolis_md"])))
    (call,) = imgry.http.calls
    assert call["url"].endswith("/ImageServer/exportImage")
    w, h = size_of(call["params"])
    assert w == pytest.approx(80 / 0.0762, rel=0.02) and meta["source"]["m_per_px"] == pytest.approx(0.0762)


# -- catalog, coverage and privacy --------------------------------------------------------------------------------------
@pytest.mark.parametrize("place,naip,maryland", [
    ("annapolis_md", True, True), ("raleigh", True, False), ("denver", True, False),
    ("toronto", False, False), ("paris", False, False)])
def test_coverage_outlines(place, naip, maryland):
    cat = im.load_catalog().sources
    lon, lat = PLACES[place]
    assert im.covers(cat["usgs_naip"], lon, lat) is naip
    assert im.covers(cat["md_imap_sixinch"], lon, lat) is maryland
    assert im.covers(cat["esri_world"], lon, lat)


def test_only_sources_covering_the_yard_are_ever_contacted(tmp_path):
    imgry = offline(tmp_path)
    imgry.configure({}, {"mapbox_satellite": "k"})
    assert set(imgry.candidates(*PLACES["annapolis_md"])) == {"usgs_naip", "md_imap_threeinch", "md_imap_sixinch", "mapbox_satellite"}
    assert set(imgry.candidates(*PLACES["paris"])) == {"fr_ign_ortho", "mapbox_satellite"}   # France's own + the keyed one
    imgry.configure({}, {})
    assert imgry.candidates(*PLACES["paris"]) == ["fr_ign_ortho"]
    assert imgry.candidates(-40.0, 30.0) == []                                                 # mid-Atlantic: nobody
    r = asyncio.run(imgry.probe("md_imap_threeinch", *PLACES["paris"]))
    assert r["ok"] is None and "does not cover" in r["detail"]
    imgry.configure({"provider": "md_imap_threeinch"}, {})
    with pytest.raises(im.ImageryError, match="does not cover"):
        asyncio.run(imgry.ensure_yard(box(40, *PLACES["paris"]), force=True))
    assert imgry.http.calls == []


def test_a_local_source_adds_and_a_local_id_replaces(tmp_path):
    src_dir = tmp_path / "imagery" / "sources"
    src_dir.mkdir(parents=True)
    (src_dir / "nl_example.json").write_text(json.dumps(
        {"id": "nl_example", "name": "NL example", "kind": "tiles", "url": "https://example.test/{z}/{x}/{y}.jpeg",
         "native_m": 0.08, "attribution": "x", "coverage": {"ref": "nl"}}), encoding="utf-8")
    (src_dir / "usgs_naip.json").write_text(json.dumps(
        {"id": "usgs_naip", "name": "NAIP override", "kind": "arcgis_image", "url": "https://example.test/is",
         "native_m": 0.6, "attribution": "x", "coverage": {"type": "World"}}), encoding="utf-8")
    imgry = offline(tmp_path)
    ids = {c["id"]: c for c in imgry.sources}
    assert ids["usgs_naip"]["name"] == "NAIP override" and ids["usgs_naip"]["origin"] == "override"
    assert ids["nl_example"]["origin"] == "local" and ids["nl_example"]["coverage"]["type"] in ("Polygon", "MultiPolygon")   # bundled outline
    assert "nl_example" in imgry.candidates(*PLACES["amsterdam"])


def test_the_custom_source_is_a_local_entry_that_can_be_saved_and_removed(tmp_path):
    imgry = offline(tmp_path)
    src = imgry.save_local(custom_entry({"kind": "wmts", "url": "https://county.test/{z}/{x}/{y}.jpg", "native_m": 0.1,
                                         "name": "County"}))
    assert src["kind"] == "tiles" and src["max_zoom"] == 20 and src["coverage"]["type"] == "World"
    assert imgry.local_entry("custom")["name"] == "County"
    assert "custom" in imgry.candidates(*PLACES["annapolis_md"])          # the user's own source is asked for their yard
    imgry.delete_local("custom")
    with pytest.raises(im.ImageryError, match="no custom source"):
        imgry.source("custom")


def test_wms_parameters_follow_the_axis_rules():
    x0, y0 = im.to_merc(-76.5, 39.0)
    x1, y1 = im.to_merc(-76.49, 39.01)
    p = im.wms_params({"layers": "ortho"}, x0, y0, x1, y1, 100, 100)
    assert p["VERSION"] == "1.3.0" and p["CRS"] == "EPSG:3857" and p["BBOX"] == f"{x0},{y0},{x1},{y1}"
    p = im.wms_params({"layers": "ortho", "crs": "EPSG:4326"}, x0, y0, x1, y1, 100, 100)
    assert float(p["BBOX"].split(",")[0]) == pytest.approx(39.0)          # 1.3.0 + EPSG:4326: latitude first
    p = im.wms_params({"layers": "ortho", "crs": "EPSG:4326", "version": "1.1.1"}, x0, y0, x1, y1, 100, 100)
    assert "SRS" in p and "CRS" not in p and float(p["BBOX"].split(",")[0]) == pytest.approx(-76.5)


def test_big_exports_are_split_and_reassembled():
    pieces = im.chunks(5000, 3000, 2048)
    assert sum(w * h for _, _, w, h in pieces) == 5000 * 3000 and all(w <= 2048 and h <= 2048 for _, _, w, h in pieces)
    shade = []

    def answer(url, params, headers):
        shade.append(len(shade) + 1)
        return FakeResponse(200, jpeg(Image.new("RGB", size_of(params), (shade[-1] * 20, 0, 0))))

    http = FakeHTTP(answer)
    src = {"id": "w", "name": "w", "kind": "wms", "max_px": 1000, "layers": "l", "url": "https://example.test/wms"}
    out = asyncio.run(kinds.get("wms").fetch_box(http, src, "", (0.0, 0.0, 2500.0, 1200.0), 2500, 1200))
    assert out.size == (2500, 1200) and len(http.calls) == 6
    assert abs(out.getpixel((0, 0))[0] - 20) <= 3 and abs(out.getpixel((2499, 1199))[0] - 120) <= 3   # first top-left, last bottom-right
    boxes = [[float(v) for v in c["params"]["BBOX"].split(",")] for c in http.calls]
    assert min(b[0] for b in boxes) == 0 and max(b[2] for b in boxes) == 2500                         # pieces span the whole box
    assert min(b[1] for b in boxes) == 0 and max(b[3] for b in boxes) == 1200


# -- context tiles: the map around the yard ---------------------------------------------------------------------------
def context_imagery(tmp_path, source="usgs_naip", keys=None, blank=False):
    imgry = im.Imagery(str(tmp_path))
    imgry.configure({"provider": source}, keys or {}, {})
    imgry.bbox = box(40, *PLACES["annapolis_md"])

    def answer(url, params, headers):
        w, h = size_of(params)
        return FakeResponse(200, jpeg(Image.new("RGB", (w, h), (90, 90, 90)) if blank else noise(w, h)))

    imgry.http = imgry.http_strict = FakeHTTP(answer)
    return imgry, imgry.http.calls


def test_the_chosen_source_draws_the_yard_tiles(tmp_path):
    imgry, calls = context_imagery(tmp_path)
    z = 16
    x, y = im.tile_at(*PLACES["annapolis_md"], z)
    data = asyncio.run(imgry.source_tile(z, x, y))
    assert data and data[:3] == b"\xff\xd8\xff"
    (call,) = calls
    assert call["url"].endswith("USGSNAIPImagery/ImageServer/exportImage") and call["params"]["size"] == "256,256"
    assert [float(v) for v in call["params"]["bbox"].split(",")] == pytest.approx(im.tile_bounds(z, x, y))
    asyncio.run(imgry.source_tile(z, x, y))
    assert len(calls) == 1                                       # cached on disk


@pytest.mark.parametrize("case", ["far", "too_low", "too_deep"])
def test_source_tiles_are_refused_without_any_request(tmp_path, case):
    imgry, calls = context_imagery(tmp_path)
    lon, lat = PLACES["annapolis_md"]
    z = {"far": 15, "too_low": 11, "too_deep": 21}[case]
    if case == "far":
        lon += 10000 / (111320 * math.cos(math.radians(lat)))   # 10 km east: outside the 5 km radius
    assert asyncio.run(imgry.source_tile(z, *im.tile_at(lon, lat, z))) is None
    assert calls == []


def test_source_tiles_stay_inside_the_source_coverage(tmp_path):
    lon, lat = PLACES["annapolis_md"]
    d = 0.001
    (tmp_path / "imagery" / "sources").mkdir(parents=True)
    (tmp_path / "imagery" / "sources" / "tiny.json").write_text(json.dumps(
        {"id": "tiny", "name": "tiny", "kind": "tiles", "url": "https://example.test/{z}/{x}/{y}.jpg", "max_zoom": 19,
         "native_m": 0.1, "attribution": "x", "coverage": {"type": "Polygon", "coordinates": [[
             [lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d]]]}}),
        encoding="utf-8")
    imgry, calls = context_imagery(tmp_path, source="tiny")
    assert asyncio.run(imgry.source_tile(17, *im.tile_at(lon, lat, 17)))                     # the yard: yes
    east = lon + 2000 / (111320 * math.cos(math.radians(lat)))                               # 2 km away: in range,
    assert asyncio.run(imgry.source_tile(17, *im.tile_at(east, lat, 17))) is None           # but not covered
    assert [c["url"].startswith("https://example.test/17/") for c in calls] == [True]


def test_keyed_sources_draw_no_tiles_without_a_key(tmp_path):
    imgry, calls = context_imagery(tmp_path, source="mapbox_satellite")
    assert asyncio.run(imgry.source_tile(17, *im.tile_at(*PLACES["annapolis_md"], 17))) is None and calls == []
    imgry.configure({"provider": "mapbox_satellite"}, {"mapbox_satellite": "k"}, {})
    assert asyncio.run(imgry.source_tile(17, *im.tile_at(*PLACES["annapolis_md"], 17)))
    assert calls[0]["url"].endswith("access_token=k")


def test_blank_tiles_are_remembered(tmp_path):
    imgry, calls = context_imagery(tmp_path, blank=True)
    xy = im.tile_at(*PLACES["annapolis_md"], 16)
    assert asyncio.run(imgry.source_tile(16, *xy)) is None
    assert asyncio.run(imgry.source_tile(16, *xy)) is None
    assert len(calls) == 1


def test_the_world_layer_only_serves_zoomed_out_tiles(tmp_path):
    imgry, calls = context_imagery(tmp_path)
    assert imgry.world and imgry.world["max_zoom"] == 12
    assert asyncio.run(imgry.world_tile(10, *im.tile_at(*PLACES["annapolis_md"], 10)))
    assert asyncio.run(imgry.world_tile(13, *im.tile_at(*PLACES["annapolis_md"], 13))) is None   # closer in: never asked
    assert len(calls) == 1 and "eox.at" in calls[0]["url"]
    imgry.configure({"provider": "usgs_naip"}, {}, {"world": False})
    assert asyncio.run(imgry.world_tile(9, *im.tile_at(*PLACES["annapolis_md"], 9))) is None
    assert imgry.context_info()["world"] is None
    info = imgry.context_info()["source"]
    assert info["min_zoom"] == 12 and info["max_zoom"] >= 17 and info["radius_m"] == 5000


# -- from the review -------------------------------------------------------------------------------------------------------
def test_source_tiles_need_the_yard_itself_inside_the_outline(tmp_path):
    """A tile near the yard that overlaps a source's outline is still refused when the yard is outside it (a fresh
    install just across a border, or bounds that moved after the source was chosen)."""
    lon, lat = PLACES["annapolis_md"]
    east = lambda m: lon + m / (111320 * math.cos(math.radians(lat)))
    (tmp_path / "imagery" / "sources").mkdir(parents=True)
    (tmp_path / "imagery" / "sources" / "next_door.json").write_text(json.dumps(
        {"id": "next_door", "name": "next door", "kind": "tiles", "url": "https://example.test/{z}/{x}/{y}.jpg",
         "max_zoom": 19, "native_m": 0.1, "attribution": "x", "coverage": {"type": "Polygon", "coordinates": [[
             [east(1000), lat - 0.02], [east(4000), lat - 0.02], [east(4000), lat + 0.02], [east(1000), lat + 0.02],
             [east(1000), lat - 0.02]]]}}), encoding="utf-8")
    imgry, calls = context_imagery(tmp_path, source="next_door")
    assert asyncio.run(imgry.source_tile(16, *im.tile_at(east(2500), lat, 16))) is None    # inside its outline, 2.5 km off
    assert calls == [] and imgry.context_info()["source"] is None


def test_the_custom_source_can_be_put_back_byte_for_byte(tmp_path):
    imgry = offline(tmp_path)
    (tmp_path / "imagery" / "sources").mkdir(parents=True, exist_ok=True)
    legacy = b'{"id": "custom", "kind": "xyz", "url": "https://old.test/{z}/{x}.png"}'   # not valid today, still restorable
    (tmp_path / "imagery" / "sources" / "custom.json").write_bytes(legacy)
    snap = imgry.snapshot_local("custom")
    imgry.save_local(custom_entry({"kind": "tiles", "url": "https://new.test/{z}/{x}/{y}.png"}))
    imgry.restore_local("custom", snap)
    assert (tmp_path / "imagery" / "sources" / "custom.json").read_bytes() == legacy
    imgry.restore_local("custom", None)
    assert not (tmp_path / "imagery" / "sources" / "custom.json").exists()
    with pytest.raises(im.ImageryError, match="native_m must be a number"):
        custom_entry({"kind": "tiles", "url": "https://x.test/{z}/{x}/{y}", "native_m": "abc"})


# -- the picker: samples, automatic mode ----------------------------------------------------------------------------------
def test_the_check_keeps_a_sample_at_the_source_resolution(tmp_path):
    imgry = offline(tmp_path)
    imgry.http = imgry.http_strict = FakeHTTP()
    lon, lat = PLACES["annapolis_md"]
    out = asyncio.run(imgry.check(["md_imap_threeinch", "usgs_naip"], lon, lat))
    assert out["md_imap_threeinch"]["size"] == [512, 512]                      # 7.6 cm: capped at 512 px for 40 m
    assert out["usgs_naip"]["size"][0] == pytest.approx(40 / 0.6, abs=1)     # 60 cm: its own resolution, 67 px
    assert imgry.preview_file("md_imap_threeinch") and imgry.preview_file("usgs_naip")
    assert out["usgs_naip"]["where"] == [round(lon, 6), round(lat, 6)]


def test_the_check_only_ever_contacts_sources_that_cover_the_yard(tmp_path):
    imgry = offline(tmp_path)
    imgry.http = imgry.http_strict = FakeHTTP()
    out = asyncio.run(imgry.check(["fr_ign_ortho", "esri_world", "usgs_naip"], *PLACES["annapolis_md"]))
    assert list(out) == ["usgs_naip"] and all("nationalmap.gov" in c["url"] for c in imgry.http.calls)


def test_samples_go_stale_when_missing_old_or_taken_elsewhere(tmp_path):
    imgry = offline(tmp_path)
    imgry.http = imgry.http_strict = FakeHTTP()
    lon, lat = PLACES["annapolis_md"]
    probe = asyncio.run(imgry.check(imgry.candidates(lon, lat), lon, lat))
    assert imgry.stale(probe, lon, lat) == []
    assert set(imgry.stale(probe, lon + 0.001, lat)) == set(imgry.candidates(lon, lat))    # about 80 m away
    probe["usgs_naip"]["at"] -= 91 * 86400
    (tmp_path / "imagery" / "previews" / "md_imap_sixinch.jpg").unlink()
    assert set(imgry.stale(probe, lon, lat)) == {"usgs_naip", "md_imap_sixinch"}


def test_automatic_picks_the_sharpest_free_source_with_imagery(tmp_path):
    imgry = offline(tmp_path)
    imgry.configure({}, {"mapbox_satellite": "k"})
    lon, lat = PLACES["annapolis_md"]
    ok = lambda *ids: {i: {"ok": True, "ident": imgry.ident(imgry.source(i))} for i in ids}
    assert imgry.auto_choice(ok("usgs_naip", "md_imap_threeinch", "md_imap_sixinch", "mapbox_satellite"), lon, lat) == "md_imap_threeinch"
    assert imgry.auto_choice({**ok("usgs_naip", "md_imap_sixinch"), "md_imap_threeinch": {"ok": False}}, lon, lat) == "md_imap_sixinch"
    assert imgry.auto_choice(ok("mapbox_satellite"), lon, lat) is None             # a keyed source is never automatic
    assert imgry.auto_choice(ok("fr_ign_ortho"), lon, lat) is None                 # nor one that does not cover the yard


def test_a_saved_choice_from_before_automatic_mode_stays_the_users(tmp_path):
    imgry = offline(tmp_path)
    imgry.configure(None, {})
    assert imgry.mode == "auto"
    imgry.configure({"provider": "md_imap_threeinch"}, {})
    assert imgry.mode == "manual"
    imgry.configure({"provider": "md_imap_threeinch", "mode": "auto"}, {})
    assert imgry.mode == "auto"


def test_the_fingerprint_changes_when_a_free_source_here_does(tmp_path):
    imgry = offline(tmp_path)
    lon, lat = PLACES["annapolis_md"]
    before = imgry.fingerprint(lon, lat)
    imgry.save_local({"id": "county", "name": "County", "kind": "tiles", "url": "https://c.test/{z}/{x}/{y}.jpg",
                      "native_m": 0.05, "attribution": "x", "coverage": {"ref": "us_md"}})
    assert imgry.fingerprint(lon, lat) != before
    assert imgry.fingerprint(*PLACES["paris"]) == offline(tmp_path / "other").fingerprint(*PLACES["paris"])


def test_octet_stream_tiles_count_as_images_by_their_bytes():
    import httpx
    from app.imagery.common import is_image
    jpeg = bytes.fromhex("ffd8ffe000104a464946")
    resp = lambda ctype, body: httpx.Response(200, headers={"content-type": ctype}, content=body)  # noqa: E731
    assert is_image(resp("application/octet-stream", jpeg))          # MassGIS labels its JPEG tiles like this
    assert is_image(resp("image/png", b""))
    assert not is_image(resp("application/octet-stream", b"<html>error</html>"))
    assert not is_image(resp("text/html", jpeg))                      # an error page that happens to start oddly
