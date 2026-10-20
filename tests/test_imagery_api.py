"""The imagery endpoints and the upkeep task, driven through a fake app: a real Store and Imagery, the recording HTTP
fake, no robot. Covers automatic mode, failures, fallbacks and the lock that keeps a user's choice from being lost."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import imagery as im
from app import main
from app.imagery.catalog import custom_entry
from app.store import Store

from .fakehttp import FakeHTTP, FakeResponse, jpeg, noise, size_of

ANNAPOLIS = (-76.50, 38.97)                  # the fake yard, 1.3 km from the Maryland State House


def fake_app(tmp_path, source=None, keys=None, answer=None, delay=None):
    store = Store(str(tmp_path))
    if source is not None:
        store.set_setting("imagery_source", source)
    if keys:
        store.set_setting("imagery_keys", keys)
    imgry = im.Imagery(str(tmp_path))
    imgry.http = imgry.http_strict = FakeHTTP(answer, delay)
    imgry.configure(store.setting("imagery_source"), store.setting("imagery_keys"), {})
    imgry.bbox = im.square(*ANNAPOLIS, 40)
    r = SimpleNamespace(yard=None, map_geojson=None, store=store, gps_ref=None)
    app = SimpleNamespace(state=SimpleNamespace(imagery=imgry, store=store, rec=r))
    return app, SimpleNamespace(app=app)


def hosts(app):
    return {c["url"].split("/")[2] for c in app.state.imagery.http.calls}


def big_exports_fail(url, params, headers):
    """Samples (512 px at most) work; a whole-yard export (over 1000 px at 7.6 cm) gets a server error."""
    w, h = size_of(params)
    return FakeResponse(500, b"busy", "text/plain") if max(w, h) > 600 else FakeResponse(200, jpeg(noise(w, h)))


def test_upkeep_on_a_fresh_install_picks_the_sharpest_and_then_rests(tmp_path):
    app, _ = fake_app(tmp_path)
    store, imgry = app.state.store, app.state.imagery
    asyncio.run(main._upkeep_once(app))
    assert imgry.source_id == "md_imap_threeinch" and store.setting("imagery_source") == {"provider": "md_imap_threeinch", "mode": "auto"}
    assert hosts(app) == {"imagery.nationalmap.gov", "mdgeodata.md.gov"}                 # only the sources covering the yard
    assert any("Automatic" in e["detail"] for e in store.events())
    calls = len(imgry.http.calls)
    asyncio.run(main._upkeep_once(app))
    assert len(imgry.http.calls) == calls                                               # nothing stale: no requests


def test_a_failed_automatic_switch_is_recorded_and_retried_a_day_later_not_hourly(tmp_path):
    app, _ = fake_app(tmp_path, answer=big_exports_fail)
    store, imgry = app.state.store, app.state.imagery
    asyncio.run(main._upkeep_once(app))
    auto = store.setting("imagery_auto")
    assert auto["failed"] == "md_imap_threeinch" and "could not use" in auto["error"] and imgry.source_id == "usgs_naip"
    calls = len(imgry.http.calls)
    asyncio.run(main._upkeep_once(app))
    assert len(imgry.http.calls) == calls                                               # an hour later: left alone
    store.set_setting("imagery_auto", {**auto, "at": auto["at"] - 2 * 86400})
    asyncio.run(main._upkeep_once(app))
    assert len(imgry.http.calls) == calls + 1                                           # a day later: one more try


def test_a_choice_of_the_users_own_is_never_overridden(tmp_path):
    app, _ = fake_app(tmp_path, source={"provider": "usgs_naip"})
    asyncio.run(main._upkeep_once(app))
    imgry = app.state.imagery
    assert imgry.source_id == "usgs_naip" and imgry.mode == "manual"
    assert all(max(size_of(c["params"])) <= 512 for c in imgry.http.calls)               # samples only, no yard fetch


def test_a_passing_error_keeps_what_was_known_and_is_retried_within_the_hour(tmp_path):
    failing = {"on": False}

    def answer(url, params, headers):
        if failing["on"] and "ThreeInch" in url:
            return FakeResponse(503, b"down", "text/plain")
        return FakeResponse(200, jpeg(noise(*size_of(params))))

    app, _ = fake_app(tmp_path, answer=answer)
    imgry = app.state.imagery
    probe = asyncio.run(main._check(app))
    failing["on"] = True
    probe = asyncio.run(main._check(app, ["md_imap_threeinch"]))
    r = probe["md_imap_threeinch"]
    assert r["ok"] is True and r["error"] and imgry.auto_choice(probe, *ANNAPOLIS) == "md_imap_threeinch"
    assert "md_imap_threeinch" not in imgry.stale(probe, *ANNAPOLIS)
    probe["md_imap_threeinch"]["error_at"] -= 3700
    assert "md_imap_threeinch" in imgry.stale(probe, *ANNAPOLIS)


def test_an_edited_custom_source_is_sampled_again(tmp_path):
    app, _ = fake_app(tmp_path)
    imgry = app.state.imagery
    imgry.save_local(custom_entry({"kind": "tiles", "url": "https://a.test/{z}/{x}/{y}.jpg", "native_m": 0.05}))
    probe = asyncio.run(main._check(app))
    assert imgry.auto_choice(probe, *ANNAPOLIS) == "custom" and "custom" not in imgry.stale(probe, *ANNAPOLIS)
    imgry.save_local(custom_entry({"kind": "tiles", "url": "https://b.test/{z}/{x}/{y}.jpg", "native_m": 0.05}))
    assert "custom" in imgry.stale(probe, *ANNAPOLIS) and imgry.auto_choice(probe, *ANNAPOLIS) == "md_imap_threeinch"


def test_removing_the_key_of_the_source_in_use_falls_back_and_forgets_its_images(tmp_path):
    app, req = fake_app(tmp_path, source={"provider": "mapbox_satellite", "mode": "manual"}, keys={"mapbox_satellite": "k"})
    imgry = app.state.imagery
    asyncio.run(main._check(app))
    app.state.rec.yard = asyncio.run(imgry.ensure_yard())
    assert imgry.yard_meta["source"]["id"] == "mapbox_satellite" and imgry.preview_file("mapbox_satellite")
    out = asyncio.run(main.imagery_key(req, {"provider": "mapbox_satellite", "key": ""}))
    assert out["has_key"] is False and imgry.mode == "auto" and imgry.source_id == "md_imap_threeinch"
    assert imgry.yard_meta["source"]["id"] == "md_imap_threeinch" and not imgry.preview_file("mapbox_satellite")
    assert "mapbox_satellite" not in (app.state.store.setting("imagery_probe") or {})


def test_deleting_the_custom_source_in_use_falls_back_even_when_nothing_can_be_fetched(tmp_path):
    app, req = fake_app(tmp_path, answer=big_exports_fail)
    imgry = app.state.imagery
    imgry.save_local(custom_entry({"kind": "tiles", "url": "https://a.test/{z}/{x}/{y}.jpg", "native_m": 0.5}))
    imgry.configure({"provider": "custom", "mode": "manual"}, {})
    asyncio.run(main._check(app))
    view = asyncio.run(main.imagery_delete_source(req, "custom"))
    assert imgry.source_id == "md_imap_threeinch" and imgry.mode == "auto"
    assert view["warning"] and "could not be fetched yet" in view["warning"]               # a 200 with a warning
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.imagery_delete_source(req, "usgs_naip"))
    assert exc.value.status_code == 400


def test_the_background_upkeep_leaves_unused_keyed_sources_alone(tmp_path):
    app, _ = fake_app(tmp_path, source={"provider": "md_imap_threeinch", "mode": "manual"}, keys={"mapbox_satellite": "k"})
    asyncio.run(main._upkeep_once(app))
    assert "api.mapbox.com" not in hosts(app)
    assert "mapbox_satellite" in app.state.imagery.stale({}, *ANNAPOLIS)                  # "Check again" still covers it


def test_probe_ids_must_be_a_list_of_ids(tmp_path):
    app, req = fake_app(tmp_path)
    for bad in ("usgs_naip", [["usgs_naip"]], {"a": 1}):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(main.imagery_probe(req, {"ids": bad}))
        assert exc.value.status_code == 400
    assert app.state.imagery.http.calls == []


def test_a_choice_made_during_an_automatic_switch_is_kept(tmp_path):
    """The upkeep is switching to the sharpest source (a slow fetch) when the user picks NAIP: the user wins, in
    memory and in the store."""
    slow = lambda url, params: 0.3 if max(size_of(params)) > 600 else 0
    app, req = fake_app(tmp_path, delay=slow)
    store, imgry = app.state.store, app.state.imagery
    asyncio.run(main._check(app))

    async def both():
        auto = asyncio.create_task(main._upkeep_once(app))
        await asyncio.sleep(0.05)                                   # the automatic switch is mid-fetch
        await main.imagery_source(req, {"provider": "usgs_naip"})
        await auto

    store.set_setting("imagery_auto", {"fingerprint": "old"})
    asyncio.run(both())
    assert imgry.source_id == "usgs_naip" and imgry.mode == "manual"
    assert store.setting("imagery_source") == {"provider": "usgs_naip", "mode": "manual"}
    assert app.state.rec.yard["source"]["id"] == "usgs_naip"
