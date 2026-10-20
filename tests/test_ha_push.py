"""HA publishing: a null state and one refused entity do not stop the rest (run 4 review, 2026-10-10)."""
import asyncio

import httpx

from app.ha import HAPublisher


def test_null_states_go_as_unknown_and_a_refusal_does_not_stop_the_rest():
    seen = []

    def handler(req: httpx.Request):
        import json
        body = json.loads(req.content)
        seen.append((req.url.path, body["state"]))
        return httpx.Response(400 if req.url.path.endswith("_broken") else 200, json={})

    p = HAPublisher("http://ha.test:8123", "t")
    p._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    asyncio.run(p.push({"sensor.yarbo_panel_battery": {"state": None, "attributes": {}},
                        "sensor.yarbo_panel_broken": {"state": "x", "attributes": {}},
                        "sensor.yarbo_panel_rtk": {"state": "RTK fixed", "attributes": {}}}))
    assert seen == [("/api/states/sensor.yarbo_panel_battery", "unknown"), ("/api/states/sensor.yarbo_panel_broken", "x"),
                    ("/api/states/sensor.yarbo_panel_rtk", "RTK fixed")]
    assert "sensor.yarbo_panel_broken (400)" in p.error


def test_a_good_push_clears_the_error():
    p = HAPublisher("http://ha.test:8123", "t")
    p.error = "old"
    p._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})))
    asyncio.run(p.push({"sensor.yarbo_panel_state": {"state": "docked", "attributes": {}}}))
    assert p.error == ""
