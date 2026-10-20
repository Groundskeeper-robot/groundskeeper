"""Each kind's requests, exactly. A new kind needs a case here: test_every_kind_has_a_request_shape_case fails until
it has one."""
import asyncio

import pytest

from app.imagery import ImageryError, kinds, square, tile_bounds, to_merc

BOX = (*to_merc(-76.5, 39.0), *to_merc(-76.49, 39.01))
CASES = {}


def case(name):
    def register(fn):
        CASES[name] = fn
        return fn
    return register


@case("tiles")
def tiles_requests():
    k = kinds.get("tiles")
    src = {"id": "t", "name": "t", "kind": "tiles", "url": "https://t.test/{z}/{x}/{y}.jpg?api={key}", "native_m": 0.1}
    assert k.request(src, "K", 17, 33000, 47000) == ("https://t.test/17/33000/47000.jpg?api=K", {})
    tms = {**src, "scheme": "tms"}
    assert k.request(tms, "", 3, 1, 2)[0] == "https://t.test/3/1/5.jpg?api="           # row counted from the bottom
    flipped = {**src, "url": "https://t.test/{z}/{x}/{-y}.png"}
    assert k.request(flipped, "", 3, 1, 2)[0] == "https://t.test/3/1/5.png"
    header = {**src, "url": "https://t.test/{z}/{x}/{y}.jpg", "auth": {"label": "k", "header": {"name": "Authorization", "format": "Bearer {key}"}}}
    assert k.request(header, "K", 1, 0, 0) == ("https://t.test/1/0/0.jpg", {"Authorization": "Bearer K"})
    retina = {**src, "tile_size": 512}
    assert k.zoom_for(retina, BOX, 400) == k.zoom_for(src, BOX, 400) - 1               # 512 px tiles: one zoom less


@case("arcgis_image")
def arcgis_image_requests():
    k = kinds.get("arcgis_image")
    src = {"id": "a", "name": "a", "kind": "arcgis_image", "url": "https://a.test/rest/services/O/ImageServer/", "native_m": 0.1}
    url, params, headers = k.request(src, "", BOX, 300, 200)
    assert url == "https://a.test/rest/services/O/ImageServer/exportImage" and headers == {}
    assert params == {"bbox": ",".join(map(str, BOX)), "bboxSR": 3857, "imageSR": 3857, "size": "300,200",
                      "format": "jpg", "f": "image", "compressionQuality": 88}
    assert k.request({**src, "auth": {"label": "k"}}, "K", BOX, 1, 1)[1]["token"] == "K"
    url, params, _ = k.request({**src, "url": src["url"] + "?token=abc"}, "", BOX, 1, 1)
    assert url.endswith("/ImageServer/exportImage") and params["token"] == "abc"          # a token in the address survives
    url, params, _ = k.request({**src, "url": src["url"] + "?token={key}", "auth": {"label": "k"}}, "K", BOX, 1, 1)
    assert params["token"] == "K"
    hdr = {**src, "auth": {"label": "k", "header": {"name": "X-Esri-Authorization", "format": "Bearer {key}"}}}
    url, params, headers = k.request(hdr, "K", BOX, 1, 1)
    assert "token" not in params and headers == {"X-Esri-Authorization": "Bearer K"}


@case("arcgis_map")
def arcgis_map_requests():
    k = kinds.get("arcgis_map")
    src = {"id": "m", "name": "m", "kind": "arcgis_map", "url": "https://a.test/rest/services/O/MapServer", "native_m": 0.1}
    url, params, _ = k.request(src, "", BOX, 256, 256)
    assert url.endswith("/MapServer/export") and params["transparent"] == "false" and "compressionQuality" not in params


@case("wms")
def wms_requests():
    k = kinds.get("wms")
    src = {"id": "w", "name": "w", "kind": "wms", "url": "https://w.test/wms?key={key}", "layers": "ortho", "native_m": 0.1}
    url, params, _ = k.request(src, "K", BOX, 300, 200)
    assert url == "https://w.test/wms" and params["key"] == "K"            # the address's own query, kept as params
    mapfile = k.request({**src, "url": "https://w.test/cgi?map=/srv/o.map"}, "", BOX, 1, 1)
    assert mapfile[0] == "https://w.test/cgi" and mapfile[1]["map"] == "/srv/o.map"
    assert params["REQUEST"] == "GetMap" and params["LAYERS"] == "ortho" and params["CRS"] == "EPSG:3857"
    assert (params["WIDTH"], params["HEIGHT"]) == (300, 200) and params["BBOX"] == ",".join(map(str, BOX))


def test_every_kind_has_a_request_shape_case():
    assert set(CASES) == set(kinds.REGISTRY)


@pytest.mark.parametrize("name", sorted(CASES))
def test_request_shape(name):
    CASES[name]()


def test_fields_are_checked_by_the_kind():
    wms = kinds.get("wms")
    assert wms.validate({"layers": ""}) == ["wms needs layers"]
    assert wms.validate({"layers": "a", "version": "2.0"}) == ["version must be one of 1.3.0, 1.1.1"]
    assert kinds.get("tiles").validate({"url": "https://t/{z}/{x}/{y}", "max_zoom": "19"}) == ["max_zoom must be a whole number"]
    assert kinds.get("tiles").validate({"url": "https://t/{z}/{x}/{y}", "max_zoom": 30}) == ["max_zoom must be at most 24"]


def test_kinds_describe_themselves_for_the_source_form():
    d = {k["name"]: k for k in (kind.describe() for kind in kinds.REGISTRY.values())}
    assert d["wms"]["fields"]["layers"]["required"] is True and d["tiles"]["fields"]["scheme"]["choices"] == ["xyz", "tms"]
    assert all(k["label"] and k["url_hint"] for k in d.values())


def test_old_kind_names_still_resolve():
    assert kinds.get("xyz") is kinds.get("wmts") is kinds.get("tiles")
    with pytest.raises(ImageryError):
        kinds.get("quadkey")


def test_export_kinds_render_a_tile_as_its_box():
    seen = []

    class Rec(kinds.ArcGISImage):
        async def fetch_one(self, http, src, key, box, w, h):
            seen.append((box, w, h))
            return "img"

    asyncio.run(Rec().fetch_tile(None, {"name": "x"}, "", 16, 100, 200))
    assert seen == [(tile_bounds(16, 100, 200), 256, 256)]
