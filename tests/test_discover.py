"""Adding a source: discovery per kind (against faked servers), the try step, saving, and export for sharing with
the guards that keep the user's yard out of a public file."""
import asyncio
import json
import math

import pytest

from app import imagery as im
from app import main
from app.imagery import kinds, share
from app.imagery.catalog import CATALOG_DIR, bundled_problems, prepare, schema_doc
from app.imagery.discover import discover, suggest_id
from app.imagery.kinds.tiles import Tiles

from .fakehttp import FakeHTTP, FakeResponse, jpeg, noise, size_of
from .test_imagery_api import ANNAPOLIS, fake_app


def as_json(obj) -> FakeResponse:
    return FakeResponse(200, json.dumps(obj).encode(), "application/json")


def run(coro):
    return asyncio.run(coro)


# -- discovery ------------------------------------------------------------------------------------------------------------
STATE_PLANE = {"wkid": 6488}                                   # Maryland, US feet
BASE = "https://gis.county.test/arcgis/rest/services/Imagery/Ortho_2024/ImageServer"


def county_imageserver(url, params, headers):
    if url == BASE and params.get("f") == "json":
        return as_json({"name": "Imagery/Ortho_2024", "pixelSizeX": 0.5, "maxImageWidth": 15000, "maxImageHeight": 4100,
                        "copyrightText": "Example County Land Information Office",
                        "extent": {"xmin": 2_000_000, "ymin": 400_000, "xmax": 2_150_000, "ymax": 520_000,
                                   "spatialReference": STATE_PLANE}})
    if url == BASE + "/exportImage" and params.get("f") == "json" and params.get("imageSR") == 4326:
        return as_json({"extent": {"xmin": -77.2, "ymin": 38.6, "xmax": -76.0, "ymax": 39.4}})
    if url == BASE + "/exportImage" and params.get("f") == "json":
        x0, y0, x1, y1 = (float(v) for v in params["bbox"].split(","))
        half_ground = (x1 - x0) / 2 * 0.3048                    # feet to metres on the ground
        cx, cy = im.to_merc(-76.6, 39.0)
        half_merc = half_ground / math.cos(math.radians(39.0))
        return as_json({"extent": {"xmin": cx - half_merc, "ymin": cy - half_merc, "xmax": cx + half_merc,
                                   "ymax": cy + half_merc, "spatialReference": {"wkid": 102100}}, "width": 64, "height": 64})
    return FakeResponse(404, b"", "text/plain")


def test_an_arcgis_imageserver_in_feet_is_read_in_centimetres():
    http = FakeHTTP(county_imageserver)
    found = run(discover(http, BASE + "/exportImage?bbox=1,2,3,4&f=image"))
    e = found["entry"]
    assert e["kind"] == "arcgis_image" and e["url"] == BASE
    assert e["native_m"] == pytest.approx(0.1524, abs=0.001)                  # 0.5 ft
    assert any("feet" in n for n in found["notes"]) and "native_m" not in found["guessed"]
    assert e["max_px"] == 4096 and e["attribution"] == "Imagery: Example County Land Information Office"
    assert e["name"] == "Ortho 2024" and e["vintage"] == "2024" and e["coverage"] == {"type": "World"}
    w, s, ee, n = found["extent"]
    assert w < -76.6 < ee and s < 39.0 < n
    assert {c["url"].split("/")[2] for c in http.calls} == {"gis.county.test"}  # only the pasted address


def test_a_tiled_mapserver_in_web_mercator_takes_its_finest_level():
    base = "https://maps.test/arcgis/rest/services/Aerial/MapServer"
    x0, y0 = im.to_merc(-78.0, 38.0)
    x1, y1 = im.to_merc(-76.0, 40.0)

    def answer(url, params, headers):
        return as_json({"mapName": "Aerial 2023", "units": "esriMeters",
                        "tileInfo": {"lods": [{"level": 0, "resolution": 156543.03}, {"level": 20, "resolution": 0.1493}]},
                        "fullExtent": {"xmin": x0, "ymin": y0, "xmax": x1, "ymax": y1, "spatialReference": {"wkid": 102100, "latestWkid": 3857}}})

    found = run(discover(FakeHTTP(answer), base))
    assert found["entry"]["kind"] == "arcgis_map"
    assert found["entry"]["native_m"] == pytest.approx(0.1493 * math.cos(math.radians(39)), abs=0.002)


CAPS = b"""<?xml version="1.0" encoding="UTF-8"?>
<WMS_Capabilities version="1.3.0" xmlns="http://www.opengis.net/wms">
  <Service><Title>Province orthophotos</Title><AccessConstraints>Open data, CC BY 4.0</AccessConstraints>
    <MaxWidth>4000</MaxWidth><MaxHeight>4000</MaxHeight></Service>
  <Capability><Layer><Title>root</Title><CRS>EPSG:4326</CRS><CRS>EPSG:3857</CRS>
    <Layer><Name>roads</Name><Title>Roads</Title></Layer>
    <Layer><Name>ortho_2024</Name><Title>Orthophoto 2024</Title><MinScaleDenominator>500</MinScaleDenominator>
      <EX_GeographicBoundingBox><westBoundLongitude>4.0</westBoundLongitude><eastBoundLongitude>6.0</eastBoundLongitude>
        <southBoundLatitude>51.0</southBoundLatitude><northBoundLatitude>52.0</northBoundLatitude></EX_GeographicBoundingBox>
      <Attribution><Title>Province of Example</Title></Attribution></Layer>
  </Layer></Capability>
</WMS_Capabilities>"""


def test_a_wms_is_read_from_its_capabilities():
    http = FakeHTTP(lambda url, params, headers: FakeResponse(200, CAPS, "text/xml"))
    found = run(discover(http, "https://geo.test/ows/wms?map=/srv/ortho.map&SERVICE=WMS&REQUEST=GetMap&LAYERS=x"))
    e = found["entry"]
    assert e["kind"] == "wms" and e["url"] == "https://geo.test/ows/wms?map=/srv/ortho.map"
    assert e["layers"] == "ortho_2024" and e["crs"] == "EPSG:3857" and e["version"] == "1.3.0" and e["max_px"] == 4000
    assert e["native_m"] == pytest.approx(0.14) and e["attribution"] == "Imagery: Province of Example"
    assert e["license"]["id"] == "CC-BY-4.0" and e["vintage"] == "2024"
    assert [l["name"] for l in found["choices"]["layers"]] == ["roads", "ortho_2024"]
    assert found["extent"] == [4.0, 51.0, 6.0, 52.0]
    assert http.calls[0]["url"] == "https://geo.test/ows/wms"
    assert http.calls[0]["params"] == {"map": "/srv/ortho.map", "SERVICE": "WMS", "REQUEST": "GetCapabilities"}


@pytest.mark.parametrize("pasted,template,how", [
    ("https://t.test/{z}/{x}/{y}.png", "https://t.test/{z}/{x}/{y}.png", "template"),
    ("https://w.test/wmts?SERVICE=WMTS&REQUEST=GetTile&TileMatrix=EPSG:3857:17&TileRow=48000&TileCol=33000&Format=image/jpeg",
     "https://w.test/wmts?SERVICE=WMTS&REQUEST=GetTile&TileMatrix=EPSG:3857:{z}&TileRow={y}&TileCol={x}&Format=image/jpeg", "wmts"),
    ("https://a.test/arcgis/rest/services/X/MapServer/tile/18/96000/66000", "https://a.test/arcgis/rest/services/X/MapServer/tile/{z}/{y}/{x}", "arcgis cache"),
    ("https://n.test/ortho/18/66000/96000.jpg?v=2", "https://n.test/ortho/{z}/{x}/{y}.jpg?v=2", "numbers"),
    ("https://n.test/about", None, ""),
])
def test_tile_addresses_become_templates(pasted, template, how):
    assert Tiles.template(pasted) == (template, how)


def test_a_tile_address_is_checked_with_a_world_tile_only():
    http = FakeHTTP(lambda url, params, headers: FakeResponse(200, jpeg(noise(512, 512))))
    found = run(discover(http, "https://t.test/sat/{z}/{x}/{y}@2x.jpg"))
    assert found["entry"]["kind"] == "tiles" and found["entry"]["tile_size"] == 512
    assert [c["url"] for c in http.calls] == ["https://t.test/sat/0/0/0@2x.jpg"]          # the whole world: no location
    with pytest.raises(im.ImageryError, match="sub-domains"):
        run(discover(FakeHTTP(), "https://{s}.t.test/{z}/{x}/{y}.png"))


def test_an_address_nobody_recognises_says_so():
    http = FakeHTTP(lambda url, params, headers: FakeResponse(200, b"<html>hello</html>", "text/html"))
    with pytest.raises(im.ImageryError, match="could not tell"):
        run(discover(http, "https://example.test/maps"))
    with pytest.raises(im.ImageryError, match="http"):
        run(discover(http, "ftp://example.test/maps"))


# -- try and save ---------------------------------------------------------------------------------------------------------
def test_trying_a_tile_source_finds_its_deepest_zoom_at_the_yard(tmp_path):
    def answer(url, params, headers):
        z = int(url.split("/")[3])
        return FakeResponse(404, b"", "text/plain") if z > 19 else FakeResponse(200, jpeg(noise(256, 256)))

    app, _ = fake_app(tmp_path, answer=answer)
    out = run(app.state.imagery.try_entry({"kind": "tiles", "url": "https://t.test/{z}/{x}/{y}.jpg", "name": "T",
                                            "native_m": 0.1, "max_zoom": 21, "attribution": "x"}, *ANNAPOLIS))
    assert out["ok"] and out["found"]["max_zoom"] == 19 and out["image"].startswith("data:image/jpeg;base64,")
    assert out["found"]["native_m"] == pytest.approx(2 * im.HALF * math.cos(math.radians(ANNAPOLIS[1])) / (256 * 2 ** 19), abs=1e-3)
    blank = lambda url, params, headers: FakeResponse(200, jpeg(noise(1, 1).resize(size_of(params))))
    app, _ = fake_app(tmp_path / "b", answer=blank)
    out = run(app.state.imagery.try_entry({"kind": "wms", "url": "https://w.test/wms", "layers": "x", "name": "W",
                                            "native_m": 0.2, "attribution": "x"}, *ANNAPOLIS))
    assert not out["ok"] and "no imagery" in out["detail"]


def test_saving_gives_an_id_and_refuses_to_overwrite_unasked(tmp_path):
    app, req = fake_app(tmp_path)
    entry = {"kind": "tiles", "url": "https://t.test/{z}/{x}/{y}.jpg", "name": "County 2024 aerial", "native_m": 0.1,
             "max_zoom": 19, "attribution": "Imagery: county"}
    view = run(main.imagery_add_source(req, {"entry": entry, "use": True}))
    imgry = app.state.imagery
    assert view["id"] == "county_2024_aerial" and imgry.source_id == "county_2024_aerial" and imgry.mode == "manual"
    assert imgry.preview_file("county_2024_aerial")                                  # sampled for its card
    with pytest.raises(main.HTTPException) as exc:
        run(main.imagery_add_source(req, {"entry": {**entry, "id": "county_2024_aerial"}}))
    assert exc.value.status_code == 409
    assert suggest_id("County 2024 aerial", {"county_2024_aerial"}) == "county_2024_aerial_2"
    assert suggest_id("3D", set()) == "src_3d"


# -- export for sharing ---------------------------------------------------------------------------------------------------
BALTIMORE_CITY_HALL = (-76.6107, 39.2908)


def saved(tmp_path, **over):
    app, req = fake_app(tmp_path)
    entry = {"kind": "arcgis_image", "url": BASE + "?token=SECRET123", "name": "Ortho 2024", "native_m": 0.1524,
             "attribution": "Imagery: Example County", "vintage": "2024", **over}
    src = app.state.imagery.add_local(entry)
    return app, req, src


def test_export_makes_a_catalog_file_that_passes_the_catalog_checks(tmp_path):
    app, req, src = saved(tmp_path)
    out = run(main.imagery_export(req, src["id"], {"country": "us", "region": "Example County, Maryland",
                                                   "place": "Baltimore City Hall", "landmark": list(BALTIMORE_CITY_HALL),
                                                   "coverage": {"ref": "us_md"}}))
    (f,) = out["files"]
    assert f["path"] == "catalog/sources/us/us_ortho_2024.json" and out["id"] == "us_ortho_2024"
    data = json.loads(f["text"])
    assert "SECRET123" not in f["text"] and data["url"].endswith("?token={key}") and data["auth"]["label"]
    assert data["coverage"] == {"ref": "us_md"} and data["verified"]["place"] == "Baltimore City Hall"
    assert not {"origin", "file", "coverage_ref"} & set(data) and data["$schema"] == "../../schema.json"
    assert bundled_problems(prepare(data, [CATALOG_DIR / "coverage"])) == []
    assert any("not stated" in n for n in out["notes"]) and any("{key}" in n for n in out["notes"])
    jsonschema = pytest.importorskip("jsonschema")
    assert list(jsonschema.Draft202012Validator(schema_doc()).iter_errors(data)) == []


def test_export_with_the_services_own_extent_writes_an_outline(tmp_path):
    app, req, src = saved(tmp_path)
    out = run(main.imagery_export(req, src["id"], {"country": "US", "region": "Example County", "place": "Baltimore City Hall",
                                                   "landmark": list(BALTIMORE_CITY_HALL), "coverage": {"extent": [-77.0, 39.1, -76.2, 39.7]}}))
    paths = [f["path"] for f in out["files"]]
    assert paths == ["catalog/sources/us/us_ortho_2024.json", "catalog/coverage/us_ortho_2024.geojson"]
    assert json.loads(out["files"][0]["text"])["coverage"] == {"ref": "us_ortho_2024"}


@pytest.mark.parametrize("change,message", [
    ({"landmark": [-76.4911, 38.9789]}, "from your yard"),                         # the State House, 1.3 km from the yard
    ({"coverage": {"extent": [-76.65, 39.25, -76.55, 39.33]}}, "at least 20 by 10 km"),  # an outline the size of a suburb
    ({"coverage": {"world": True}}, "whole world"),
    ({"coverage": {"ref": "nl"}}, "outside the outline"),
    ({"coverage": {"ref": "../secrets"}}, "no shared outline"),
    ({"country": "USA"}, "two-letter"),
    ({"place": ""}, "landmark"),
])
def test_export_refuses_what_would_put_the_yard_or_nonsense_in_a_public_file(tmp_path, change, message):
    app, req, src = saved(tmp_path)
    body = {"country": "US", "region": "Example County", "place": "Baltimore City Hall",
            "landmark": list(BALTIMORE_CITY_HALL), "coverage": {"ref": "us_md"}, **change}
    with pytest.raises(main.HTTPException) as exc:
        run(main.imagery_export(req, src["id"], body))
    assert exc.value.status_code == 400 and message in exc.value.detail


def test_only_the_users_own_sources_can_be_exported(tmp_path):
    app, req = fake_app(tmp_path)
    with pytest.raises(main.HTTPException) as exc:
        run(main.imagery_export(req, "usgs_naip", {}))
    assert exc.value.status_code == 400


def test_keys_are_stripped_from_any_common_parameter():
    for url in ("https://a.test/x?token=abc&f=1", "https://a.test/x?f=1&api_key=abc", "https://a.test/x?access_token=abc",
                "https://a.test/{z}/{x}/{y}.png?api=abc", "https://a.test/x?apikey=abc&key=def"):
        out, had = share.strip_keys(url)
        assert had and "abc" not in out and "def" not in out
    assert share.strip_keys("https://a.test/{z}/{x}/{y}.png?api={key}") == ("https://a.test/{z}/{x}/{y}.png?api={key}", False)


def test_a_key_in_a_saved_address_never_goes_back_to_the_browser(tmp_path):
    app, req, src = saved(tmp_path)
    listed = next(c for c in app.state.imagery.catalog(*ANNAPOLIS) if c["id"] == src["id"])
    assert "SECRET123" not in json.dumps(listed) and listed["url"].endswith("token={key}")


def test_an_arcgis_services_ogc_endpoints_are_left_to_the_ogc_kinds():
    wms = "https://a.test/arcgis/services/Ortho/ImageServer/WMSServer"
    assert not kinds.get("arcgis_image").claims(wms) and kinds.get("wms").claims(wms)
    http = FakeHTTP(lambda url, params, headers: FakeResponse(200, CAPS, "text/xml"))
    assert run(discover(http, wms))["entry"]["kind"] == "wms"
    assert all(c["url"] == wms for c in http.calls)


# -- from the review of the add-a-source flow ------------------------------------------------------------------------
from types import SimpleNamespace

from app.imagery import discover as disc
from app.imagery.catalog import ID_RE, validate
from app.imagery.common import OVERZOOMED, added_detail


def export_body(**over):
    return {"country": "US", "region": "Example County", "place": "Baltimore City Hall",
            "landmark": list(BALTIMORE_CITY_HALL), "coverage": {"ref": "us_md"}, **over}


def box_ring(w, s, e, n):
    return [[w, s], [e, s], [e, n], [w, n], [w, s]]


@pytest.mark.parametrize("geojson,message", [
    ({"type": "MultiPolygon", "coordinates": [[box_ring(-77.0, 39.1, -76.2, 39.7)],                       # a county...
                                              [box_ring(-76.5007, 38.9695, -76.4993, 38.9705)]]},          # ...and the yard
     "each part must be at least"),
    ({"type": "Polygon", "coordinates": [[[-77.0, 38.6], [-76.2, 38.6], [-76.2, 39.7], [-76.50, 38.975], [-77.0, 39.7], [-77.0, 38.6]]]},
     "within 2 km of your yard"),
    ({"type": "Polygon", "coordinates": [[[-77.0, 39.1], [float("nan"), 39.1], [-76.2, 39.7], [-77.0, 39.7], [-77.0, 39.1]]]},
     "longitude, latitude"),
])
def test_an_outline_can_never_carry_the_yard(tmp_path, geojson, message):
    app, req, src = saved(tmp_path)
    with pytest.raises(main.HTTPException) as exc:
        run(main.imagery_export(req, src["id"], export_body(coverage={"geojson": geojson})))
    assert message in exc.value.detail


def test_an_outline_is_rebuilt_from_outer_rings_and_pairs(tmp_path):
    app, req, src = saved(tmp_path)
    outer = [[p[0], p[1], 123.0] for p in box_ring(-77.0, 39.1, -76.2, 39.7)]
    hole = box_ring(-76.7, 39.3, -76.6, 39.4)
    out = run(main.imagery_export(req, src["id"], export_body(coverage={"geojson": {"type": "Polygon", "coordinates": [outer, hole]}})))
    geom = json.loads(out["files"][1]["text"])["geometry"]
    assert geom["type"] == "Polygon" and len(geom["coordinates"]) == 1 and all(len(p) == 2 for p in geom["coordinates"][0])


@pytest.mark.parametrize("change", [
    {"coverage": {"world": True}, "worldwide": True},                     # worldwide coverage, a country's code
    {"country": "XX"},                                                    # the worldwide code, a regional outline
])
def test_worldwide_goes_with_xx_and_only_with_it(tmp_path, change):
    app, req, src = saved(tmp_path)
    with pytest.raises(main.HTTPException) as exc:
        run(main.imagery_export(req, src["id"], export_body(**change)))
    assert "worldwide service has country XX" in exc.value.detail


def test_user_names_and_passwords_never_travel(tmp_path):
    with pytest.raises(im.ImageryError, match="user name and password"):
        run(discover(FakeHTTP(), "https://bob:pw@gis.county.test/arcgis/rest/services/O/ImageServer"))
    out, had = share.strip_keys("https://bob:pw@gis.county.test/x?authkey=s3cret&sig=abc")
    assert had and out == "https://gis.county.test/x?authkey={key}&sig={key}"
    app, req = fake_app(tmp_path)
    with pytest.raises(im.ImageryError, match="user name and password"):
        app.state.imagery.add_local({"kind": "arcgis_image", "url": "https://bob:pw@gis.county.test/x/ImageServer",
                                     "name": "X", "native_m": 0.1, "attribution": "x"})
    src = app.state.imagery.save_local({"id": "legacy", "kind": "arcgis_image", "name": "Old", "native_m": 0.15,
                                        "url": "https://bob:pw@gis.county.test/arcgis/rest/services/O/ImageServer",
                                        "attribution": "Imagery: bob:pw@gis.county.test", "coverage": {"type": "World"}})
    listed = next(c for c in app.state.imagery.catalog(*ANNAPOLIS) if c["id"] == src["id"])
    assert "pw@" not in json.dumps(listed)
    text = run(main.imagery_export(req, src["id"], export_body()))["files"][0]["text"]
    assert "bob" not in text and "pw@" not in text


def test_a_token_in_an_arcgis_address_survives_discovery_and_use():
    seen = []

    def answer(url, params, headers):
        seen.append(params.get("token"))
        return county_imageserver(url, {k: v for k, v in params.items() if k != "token"}, headers)

    found = run(discover(FakeHTTP(answer), BASE + "?f=pjson&token=abc"))
    assert found["entry"]["url"] == BASE + "?token=abc" and set(seen) == {"abc"}
    url, params, _ = kinds.get("arcgis_image").request(found["entry"], "", (0, 0, 1, 1), 10, 10)
    assert url == BASE + "/exportImage" and params["token"] == "abc"


def test_exported_ids_fit_and_never_collide_with_the_catalog(tmp_path):
    app, req, src = saved(tmp_path, name="x" * 45)
    out = run(main.imagery_export(req, src["id"], export_body(coverage={"extent": [-77.0, 39.1, -76.2, 39.7]})))
    assert len(out["id"]) <= 40 and out["files"][1]["path"].endswith(f"/{out['id']}.geojson")
    app, req, src = saved(tmp_path / "b", name="PDOK hr")
    nl = run(main.imagery_export(req, src["id"], export_body(country="NL", region="Netherlands", place="Dam",
                                                             landmark=[4.8913, 52.3731], coverage={"ref": "nl"})))
    assert nl["id"] == "nl_pdok_hr_2"                                       # nl_pdok_hr is a bundled source


def test_discovery_stops_at_its_byte_cap(monkeypatch):
    monkeypatch.setattr(disc, "MAX_BYTES", 1000)
    http = FakeHTTP(lambda url, params, headers: FakeResponse(200, b"x" * 5000, "text/xml"))
    with pytest.raises(im.ImageryError, match="more than"):
        run(discover(http, "https://geo.test/wms"))


def test_a_malformed_answer_is_an_error_not_a_crash():
    with pytest.raises(im.ImageryError, match="nope"):
        run(discover(FakeHTTP(lambda u, p, h: as_json({"error": "nope"})), BASE))
    odd = run(discover(FakeHTTP(lambda u, p, h: as_json({"name": "Odd", "extent": [1, 2], "tileInfo": [3]})), BASE))
    assert odd["entry"]["native_m"] == 0.15 and "native_m" in odd["guessed"]


def test_licences_are_only_read_from_what_the_words_say():
    from app.imagery.discover import guess_license
    assert guess_license("Province council open data, CC BY 4.0")["id"] == "CC-BY-4.0"
    assert guess_license("This imagery is not in the public domain")["id"] == "unknown"
    assert guess_license("Public domain")["id"] == "public-domain"


def test_degrees_use_the_north_south_pixel_size():
    answer = lambda u, p, h: as_json({"name": "Geo", "pixelSizeX": 0.00001, "pixelSizeY": 0.000008,
                                      "extent": {"xmin": -79.5, "ymin": 37.9, "xmax": -75.0, "ymax": 39.8, "spatialReference": {"wkid": 4326}}})
    found = run(discover(FakeHTTP(answer), BASE))
    assert found["entry"]["native_m"] == pytest.approx(0.000008 * 110540, abs=0.001)


def test_feet_are_recognised_on_a_rotated_grid():
    """A grid rotated against north inflates the measured scale by a few per cent; it still snaps to feet."""
    def answer(url, params, headers):
        r = county_imageserver(url, params, headers)
        if params.get("imageSR") == 3857:
            data = json.loads(r.content)
            e = data["extent"]
            cx, cy, half = (e["xmin"] + e["xmax"]) / 2, (e["ymin"] + e["ymax"]) / 2, (e["xmax"] - e["xmin"]) / 2 * 1.04
            data["extent"].update(xmin=cx - half, xmax=cx + half, ymin=cy - half, ymax=cy + half)
            return as_json(data)
        return r

    found = run(discover(FakeHTTP(answer), BASE))
    assert found["entry"]["native_m"] == pytest.approx(0.1524, abs=0.0005)


INHERITED = b"""<WMS_Capabilities version="1.3.0" xmlns="http://www.opengis.net/wms"><Service><Title>T</Title></Service>
  <Capability><Layer><Title>root</Title><CRS>EPSG:3857</CRS>
    <EX_GeographicBoundingBox><westBoundLongitude>4</westBoundLongitude><eastBoundLongitude>6</eastBoundLongitude>
      <southBoundLatitude>51</southBoundLatitude><northBoundLatitude>52</northBoundLatitude></EX_GeographicBoundingBox>
    <Layer><Name>ortho</Name><Title>Ortho</Title><MinScaleDenominator>1</MinScaleDenominator></Layer>
  </Layer></Capability></WMS_Capabilities>"""


def test_a_wms_layer_inherits_from_its_parent_and_ignores_silly_scales():
    found = run(discover(FakeHTTP(lambda u, p, h: FakeResponse(200, INHERITED, "text/xml")), "https://geo.test/wms"))
    assert found["entry"]["crs"] == "EPSG:3857" and found["extent"] == [4.0, 51.0, 6.0, 52.0]
    assert "native_m" in found["guessed"]                                   # MinScaleDenominator 1 is not 0.3 mm


def test_place_parameters_never_leave_with_discovery():
    http = FakeHTTP(lambda url, params, headers: FakeResponse(200, CAPS, "text/xml"))
    run(discover(http, "https://geo.test/ows/wms?map=/srv/o.map&center=-76.50,38.97&zoom=18&ll=38.97;-76.50"))
    sent = json.dumps(http.calls)
    assert "38.97" not in sent and "zoom" not in sent and "/srv/o.map" in sent


@pytest.mark.parametrize("pasted,template", [
    ("https://x.test/wmts/1.0.0/ortho/default/GoogleMapsCompatible/18/96000/66000.jpg",
     "https://x.test/wmts/1.0.0/ortho/default/GoogleMapsCompatible/{z}/{y}/{x}.jpg"),
    ("https://t.test/sat/18/66000/96000@2x.jpg90", "https://t.test/sat/{z}/{x}/{y}@2x.jpg90"),
    ("https://t.test/ortho/18/66000/96000", "https://t.test/ortho/{z}/{x}/{y}"),
])
def test_more_tile_addresses_are_read_so_their_numbers_are_never_sent(pasted, template):
    assert Tiles.template(pasted)[0] == template


def test_the_zoom_walk_skips_enlarged_and_placeholder_tiles(tmp_path):
    from PIL import Image
    placeholder = jpeg(noise(256, 256))
    real = {}

    def tile(z):
        return real.setdefault(z, noise(256, 256))

    def answer(url, params, headers):
        z, x, y = (int(v) for v in url.split("/")[3:6] if v.split(".")[0].isdigit() for v in [v.split(".")[0]])
        if z >= 21:
            return FakeResponse(200, placeholder)                                         # the same picture
        if z == 20:                                                                       # its parent's quarter, enlarged
            q = tile(19).crop(((x % 2) * 128, (y % 2) * 128, (x % 2) * 128 + 128, (y % 2) * 128 + 128))
            return FakeResponse(200, jpeg(q.resize((256, 256), Image.BILINEAR)))
        return FakeResponse(200, jpeg(tile(z)))

    app, _ = fake_app(tmp_path, answer=answer)
    out = run(app.state.imagery.try_entry({"kind": "tiles", "url": "https://t.test/{z}/{x}/{y}.jpg", "name": "T",
                                            "native_m": 0.1, "max_zoom": 22, "attribution": "x"}, *ANNAPOLIS))
    assert out["ok"] and out["found"]["max_zoom"] == 19
    parent = noise(256, 256)
    assert added_detail(parent.crop((0, 0, 128, 128)).resize((256, 256), Image.BILINEAR), parent, 0, 0) < OVERZOOMED
    assert added_detail(noise(256, 256), parent, 0, 0) > OVERZOOMED


class _Url:
    def __init__(self, host):
        self.host = host

    def __str__(self):
        return f"https://{self.host}/x"


def test_the_flow_refuses_redirects_to_another_host():
    def response(location):
        return SimpleNamespace(has_redirect_location=True, headers={"location": location},
                               request=SimpleNamespace(url=_Url("gis.county.test")))

    run(disc.same_host_only(response("/elsewhere")))                                  # same host: fine
    with pytest.raises(im.ImageryError, match="redirected to evil.test"):
        run(disc.same_host_only(response("https://evil.test/collect")))


def test_ids_with_a_trailing_newline_are_refused():
    assert not ID_RE.fullmatch("abc\n") and validate({"id": "abc\n"})


# -- from the second-pass review --------------------------------------------------------------------------------------
def test_an_exported_resolution_does_not_say_where_the_yard_is(tmp_path):
    app, req, src = saved(tmp_path, native_m=0.2181)                      # worked out at the yard's latitude
    data = json.loads(run(main.imagery_export(req, src["id"], export_body()))["files"][0]["text"])
    assert data["native_m"] == 0.22


def test_geoserver_tms_addresses_keep_their_colons_and_ats():
    url = "https://geo.test/geoserver/gwc/service/tms/1.0.0/ortho:2023@EPSG:900913@jpeg/{z}/{x}/{-y}.jpeg"
    assert share.strip_keys(url) == (url, False)
    assert share.strip_keys("https://bob:pw@geo.test/a@b/c") == ("https://geo.test/a@b/c", True)
    assert share.USERINFO.sub("", "Imagery: ortho:2023@EPSG:900913, by bob:pw@gis.county.test") == \
        "Imagery: ortho:2023@EPSG:900913, by gis.county.test"


def test_an_outline_edge_through_the_yard_is_refused(tmp_path):
    app, req, src = saved(tmp_path)
    lon, lat = ANNAPOLIS                                                    # the fake yard; an edge runs along its latitude
    ring = [[lon - 0.3, lat], [lon + 0.1, lat], [lon + 0.1, lat + 0.2], [lon - 0.3, lat + 0.2], [lon - 0.3, lat]]
    body = export_body(landmark=[lon - 0.1, lat + 0.1], place="A courthouse", coverage={"geojson": {"type": "Polygon", "coordinates": [ring]}})
    with pytest.raises(main.HTTPException) as exc:
        run(main.imagery_export(req, src["id"], body))
    assert "within 2 km of your yard" in exc.value.detail


def test_a_number_too_big_for_a_float_is_a_problem_not_a_crash():
    from app.imagery import coverage
    huge = int("1" + "0" * 400)
    assert coverage.problems({"type": "Polygon", "coordinates": [[[huge, 0], [1, 0], [1, 1], [0, 0]]]})


def test_keys_with_awkward_characters_stay_one_value():
    key = "ab+c/d=&x"
    url, params, _ = kinds.get("arcgis_image").request({"url": BASE + "?token={key}", "name": "x"}, key, (0, 0, 1, 1), 1, 1)
    assert params["token"] == key and "x" not in params and list(params).count("token") == 1
    url, params, _ = kinds.get("wms").request({"url": "https://w.test/wms?api_key={key}", "layers": "l"}, key, (0, 0, 1, 1), 1, 1)
    assert params["api_key"] == key and "x" not in params
    tile = kinds.get("tiles").request({"url": "https://t.test/{z}/{x}/{y}.png?k={key}"}, key, 1, 0, 0)[0]
    assert tile == "https://t.test/1/0/0.png?k=ab%2Bc%2Fd%3D%26x"


def test_a_key_under_another_name_is_not_sent_twice():
    url, params, _ = kinds.get("arcgis_image").request({"url": BASE + "?apikey={key}", "name": "x",
                                                         "auth": {"label": "k"}}, "K", (0, 0, 1, 1), 1, 1)
    assert params["apikey"] == "K" and "token" not in params


def test_tile_discovery_drops_place_parameters_but_keeps_placeholders():
    http = FakeHTTP(lambda url, params, headers: FakeResponse(404, b"", "text/plain"))
    found = run(discover(http, "https://t.test/vt?lyrs=s&x={x}&y={y}&z={z}&center=38.97,-76.50"))
    assert found["entry"]["url"] == "https://t.test/vt?lyrs=s&x={x}&y={y}&z={z}"
    assert "38.97" not in json.dumps(http.calls)


def test_a_saved_sources_extent_comes_from_its_own_address_and_layer(tmp_path):
    app, req = fake_app(tmp_path)
    app.state.imagery.http_strict = FakeHTTP(lambda url, params, headers: FakeResponse(200, CAPS, "text/xml"))
    src = app.state.imagery.add_local({"kind": "wms", "url": "https://geo.test/ows/wms", "layers": "roads",
                                       "name": "W", "native_m": 0.2, "attribution": "x"})
    assert run(main.imagery_source_extent(req, src["id"]))["extent"] is None          # "roads" has no box of its own
    app.state.imagery.add_local({"id": src["id"], "kind": "wms", "url": "https://geo.test/ows/wms", "layers": "ortho_2024",
                                 "name": "W", "native_m": 0.2, "attribution": "x"}, replace=True)
    assert run(main.imagery_source_extent(req, src["id"]))["extent"] == [4.0, 51.0, 6.0, 52.0]


def test_the_users_own_sources_never_follow_a_redirect_elsewhere(tmp_path):
    app, _ = fake_app(tmp_path)
    imgry = app.state.imagery
    imgry.http = FakeHTTP(lambda url, params, headers: (_ for _ in ()).throw(AssertionError("used the permissive client")))
    src = imgry.add_local({"kind": "tiles", "url": "https://t.test/{z}/{x}/{y}.jpg", "name": "Mine", "native_m": 0.3,
                           "max_zoom": 19, "attribution": "x"})
    assert run(imgry.check([src["id"]], *ANNAPOLIS))[src["id"]]["ok"] is True          # went through http_strict
