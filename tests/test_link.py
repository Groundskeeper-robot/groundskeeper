"""Local first, the cloud as a fallback (app/link.py): which route reads and commands take, falling back and coming
back, the controller repeating a safe command by the other route, map writes keeping one route, and the phone app
seen on the local broker. All with stand-ins: no broker, no robot."""
import asyncio

import pytest

from app import control, link as link_mod
from app.link import NoRoute, RobotLink
from app.mapwrite import MapWriter
from app.store import Store


class FakeLocal:
    def __init__(self):
        self.connected, self.reads, self.commands = True, [], []

    def request(self, cmd, payload=None):
        self.reads.append(cmd)

    def command(self, cmd, payload):
        self.commands.append(cmd)


class FakeCloud:
    def __init__(self, state="connected"):
        self.state, self.connected, self.sent = state, state == "connected", []

    def publish(self, cmd, payload):
        self.sent.append(cmd)


class FakeRec:
    def __init__(self):
        self.transport = FakeLocal()


def _link(tmp_path, mode=None, cloud="connected"):
    store = Store(str(tmp_path))
    if mode:
        store.set_setting("robot_route", mode)
    rec, cl = FakeRec(), FakeCloud(cloud)
    return RobotLink(rec, cl, store), rec, cl, store


def test_reads_and_commands_go_local_while_it_is_up(tmp_path):
    lk, rec, cl, _ = _link(tmp_path)
    assert lk.read("get_device_msg") == "local" and lk.command("pause", {}) == "local"
    assert rec.transport.reads == ["get_device_msg"] and rec.transport.commands == ["pause"] and cl.sent == []


def test_with_the_local_broker_down_everything_goes_through_the_cloud(tmp_path):
    lk, rec, cl, _ = _link(tmp_path)
    rec.transport.connected = False
    assert lk.read("get_map") == "cloud" and lk.command("cmd_recharge", {"cmd": 2}) == "cloud"
    assert cl.sent == ["get_map", "cmd_recharge"]


def test_unanswered_local_reads_move_reads_to_the_cloud_for_a_while(tmp_path, monkeypatch):
    lk, rec, cl, _ = _link(tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(link_mod.time, "time", lambda: clock[0])
    for _ in range(link_mod.LOCAL_READ_MISSES):
        lk.note_read(lk.read("get_device_msg"), False)
    assert lk.read("get_device_msg") == "cloud"
    assert lk.command("pause", {}) == "local"             # commands still try local while it is connected
    clock[0] += link_mod.LOCAL_READ_BACKOFF_S + 1
    assert lk.read("get_device_msg") == "local"


def test_a_route_change_is_reported_once(tmp_path):
    lk, rec, cl, _ = _link(tmp_path)
    seen = []
    lk.on_change = lambda what, before, after: seen.append((what, before, after))
    lk.read("get_device_msg")
    rec.transport.connected = False
    lk.read("get_device_msg")
    lk.read("get_device_msg")
    assert seen == [("reads", "local", "cloud")]


@pytest.mark.parametrize("mode, local, cloud, want", [
    ("local", True, True, "local"), ("local", False, True, None),
    ("cloud", True, True, "cloud"), ("cloud", True, False, None),
    ("auto", False, False, None),
])
def test_the_route_setting(tmp_path, mode, local, cloud, want):
    lk, rec, cl, _ = _link(tmp_path, mode, "connected" if cloud else "signed out")
    rec.transport.connected = local
    assert lk.command_route() == want
    if want is None:
        with pytest.raises(NoRoute):
            lk.command("pause", {})


def test_the_link_refuses_what_the_robot_must_never_get(tmp_path):
    lk, *_ = _link(tmp_path)
    from app.transport_local import ReadOnlyViolation
    for cmd in ("cmd_vel", "light_ctrl"):
        with pytest.raises(ReadOnlyViolation):
            lk.command(cmd, {})
    with pytest.raises(ReadOnlyViolation):
        lk.read("start_plan")


# -- the controller: a safe command not seen goes once more by the other route ---------------------------------
def _controller(tmp_path, monkeypatch, state):
    lk, rec, cl, store = _link(tmp_path)
    store.set_setting("control_enabled", True)
    monkeypatch.setattr(control, "REROUTE_AFTER_S", 0.05)

    async def context(args):
        return {"snapshot_age_s": 1, "route_state": "connected", "plans": [{"id": 1}], "error_code": 0, "battery": 90,
                "running": False, "yard": {"ok": True, "detail": "clear"}, "phone_app_active": False,
                "conditions": "Dry", "presence": True}

    def expect(cmd):
        return asyncio.get_running_loop().create_future()

    c = control.Controller(store, lk, context, expect, lambda: state)
    c.poll_s = 0.01
    return c, rec, cl, store


def test_a_pause_not_seen_is_sent_again_through_the_cloud(tmp_path, monkeypatch):
    c, rec, cl, store = _controller(tmp_path, monkeypatch, {"planning": 1, "planning_paused": 0})

    async def go():
        r = await c.send("pause", wait=False)
        await asyncio.sleep(0.2)
        return r

    r = asyncio.run(go())
    assert r["route"] == "local" and rec.transport.commands == ["pause"] and cl.sent == ["pause"]
    assert any(e["kind"] == "command_rerouted" for e in store.events(10))
    assert store.control_log()[0]["route"] == "local"


def test_a_pause_that_took_is_not_sent_again(tmp_path, monkeypatch):
    c, rec, cl, store = _controller(tmp_path, monkeypatch, {"planning": 1, "planning_paused": 1})

    async def go():
        await c.send("pause", wait=False)
        await asyncio.sleep(0.2)

    asyncio.run(go())
    assert cl.sent == []


def test_a_start_is_never_sent_again_by_the_other_route(tmp_path, monkeypatch):
    c, rec, cl, store = _controller(tmp_path, monkeypatch, {"planning": 0, "planning_paused": 0})

    async def go():
        await c.send("start_plan", {"plan_id": 1}, wait=False)
        await asyncio.sleep(0.2)

    asyncio.run(go())
    assert rec.transport.commands == ["start_plan"] and cl.sent == []


# -- map writes keep one route ---------------------------------------------------------------------------------
def test_a_map_write_keeps_the_route_it_started_on(tmp_path):
    lk, rec, cl, store = _link(tmp_path)
    routes = []

    def publish(cmd, payload, route=None):
        got = lk.command(cmd, payload, route=route)
        routes.append(got)
        rec.transport.connected = False            # the local broker drops halfway through the write
        return got

    async def go():
        loop = asyncio.get_running_loop()

        def expect(cmd):
            fut = loop.create_future()
            fut.set_result({"state": 0, "topic": cmd})
            return fut

        w = MapWriter(store, publish, expect, None, None, lambda: "")
        async with w.lock:
            first = await w._send("save_nogozone", {"id": 1})
            second = await w._send("save_nogozone", {"id": 2})
        return first, second

    first, second = asyncio.run(go())
    assert first["ok"] and routes == ["local"] and cl.sent == []
    # The local broker dropped mid-write: the rest of the write is refused rather than switched to the cloud.
    assert not second["sent"] and "NoRoute" in second["error"]


# -- the phone app on the local broker ------------------------------------------------------------------------
def test_the_phone_app_is_seen_on_the_local_broker(tmp_path, monkeypatch):
    from app import ops as ops_mod
    o = ops_mod.Ops.__new__(ops_mod.Ops)
    lk, rec, cl, store = _link(tmp_path, cloud="signed out")
    rec.app_stream_at = 0.0
    o.link, o.rec, o.cloud, o.phone_app_at = lk, rec, cl, 0.0
    clock = [5000.0]
    monkeypatch.setattr(ops_mod.time, "time", lambda: clock[0])
    assert o.phone_app_active(60) is False                     # local is up, no stream: the app is closed
    rec.app_stream_at = clock[0] - 2
    assert o.phone_app_active(60) is True                      # the robot streams DeviceMSG: the app is open
    rec.transport.connected = False
    assert o.phone_app_active(60) is None                      # neither route: unknown


def test_writes_prefer_the_cloud_until_local_writes_are_proved(tmp_path, monkeypatch):
    lk, rec, cl, _ = _link(tmp_path)
    monkeypatch.setattr(link_mod, "LOCAL_WRITES_PROVED", False)
    assert lk.write_route() == "cloud"
    cl.state, cl.connected = "signed out", False
    assert lk.write_route() == "local"
    monkeypatch.setattr(link_mod, "LOCAL_WRITES_PROVED", True)
    cl.state, cl.connected = "connected", True
    assert lk.write_route() == "local"
