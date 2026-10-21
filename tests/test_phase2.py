import asyncio
from datetime import datetime

import pytest

from app import control, maintenance, scheduler
from tests.fakelink import FakeLink
from app import geo
from app.ops import point_in_ring
from app.store import Store


class FakeCloud:
    def __init__(self, state="connected"):
        self.state, self.sent = state, []

    def publish(self, cmd, payload):
        self.sent.append((cmd, payload))


def good_ctx(**over):
    ctx = {"snapshot_age_s": 5, "route_state": "connected", "plans": [{"id": 1, "name": "Mow All"}], "error_code": 0,
           "battery": 90, "running": False, "yard": {"ok": True, "detail": "clear", "events": []},
           "phone_app_active": False, "conditions": "Dry", "presence": True}
    ctx.update(over)
    return ctx


def make_controller(tmp_path, ctx=None, enabled=False, reply=None):
    store = Store(str(tmp_path))
    if enabled:
        store.set_setting("control_enabled", True)
    cloud = FakeCloud((ctx or good_ctx())["route_state"])

    async def context(args):
        return ctx or good_ctx()

    def expect(cmd):
        fut = asyncio.get_running_loop().create_future()
        fut.set_result(reply if reply is not None else {"topic": cmd, "state": 0, "msg": "ok"})
        return fut

    return store, cloud, control.Controller(store, FakeLink(cloud), context, expect)


def run(coro):
    return asyncio.run(coro)


# -- control: master switch, catalogue, audit ------------------------------------------------------------------------
def test_control_is_off_by_default_and_refusals_are_audited(tmp_path):
    store, cloud, c = make_controller(tmp_path)
    r = run(c.send("cmd_recharge"))
    assert not r["sent"] and "switched off" in r["refused"]
    assert cloud.sent == []
    log = store.control_log()
    assert log[0]["cmd"] == "cmd_recharge" and log[0]["sent"] == 0


@pytest.mark.parametrize("cmd", ["cmd_vel", "get_controller", "save_global_params", "light_ctrl", "set_working_state"])
def test_only_catalogue_commands_ever_go_out(tmp_path, cmd):
    store, cloud, c = make_controller(tmp_path, enabled=True)
    r = run(c.send(cmd, {"vel": 1.0}))
    assert not r["sent"] and cloud.sent == []


def test_payloads_match_the_official_sdk(tmp_path):
    store, cloud, c = make_controller(tmp_path, enabled=True)
    assert run(c.send("start_plan", {"plan_id": 1}))["sent"]
    assert run(c.send("cmd_recharge"))["sent"]
    assert run(c.send("pause"))["sent"]
    assert cloud.sent == [("start_plan", {"id": 1}), ("cmd_recharge", {"cmd": 2}), ("pause", {})]
    assert store.control_log()[0]["sent"] == 1


def test_start_plan_needs_its_argument_and_an_existing_plan(tmp_path):
    store, cloud, c = make_controller(tmp_path, enabled=True)
    assert "missing plan_id" in run(c.send("start_plan"))["refused"]
    assert "Plan exists" in run(c.send("start_plan", {"plan_id": 99}))["refused"]
    assert cloud.sent == []


@pytest.mark.parametrize("bad", [None, "", "abc", True, 0])
def test_bad_plan_ids_never_become_a_plan(tmp_path, bad):
    store, cloud, c = make_controller(tmp_path, enabled=True)
    r = run(c.send("start_plan", {"plan_id": bad}))
    assert not r["sent"] and cloud.sent == []
    assert run(c.preview("start_plan", {"plan_id": bad}))["would_send"] is False


# -- interlocks ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("over,needle", [
    ({"battery": 10}, "Battery"),
    ({"running": True}, "Not already running"),
    ({"error_code": 7}, "No active error"),
    ({"snapshot_age_s": 900}, "Robot online"),
    ({"route_state": "needs login"}, "Route to the robot"),
    ({"yard": {"ok": False, "detail": "person on back_yard", "events": [1]}}, "Yard clear"),
    ({"phone_app_active": True}, "Phone app"),
])
def test_hard_interlocks_refuse_even_with_override(tmp_path, over, needle):
    store, cloud, c = make_controller(tmp_path, good_ctx(**over), enabled=True)
    r = run(c.send("start_plan", {"plan_id": 1}, override=True))
    assert not r["sent"] and needle in r["refused"]
    assert cloud.sent == []


def test_wet_lawn_is_soft_for_a_person_and_hard_for_the_scheduler(tmp_path):
    store, cloud, c = make_controller(tmp_path, good_ctx(conditions="Wet"), enabled=True)
    assert not run(c.send("start_plan", {"plan_id": 1}))["sent"]
    assert run(c.send("start_plan", {"plan_id": 1}, override=True))["sent"]
    r = run(c.send("start_plan", {"plan_id": 1}, actor="scheduler", override=True))
    assert not r["sent"] and "Lawn dry" in r["refused"]


def test_unknown_presence_blocks_only_the_scheduler(tmp_path):
    store, cloud, c = make_controller(tmp_path, good_ctx(presence=None), enabled=True)
    assert run(c.send("start_plan", {"plan_id": 1}))["sent"]
    assert not run(c.send("start_plan", {"plan_id": 1}, actor="scheduler"))["sent"]


def test_stop_and_dock_only_need_the_link(tmp_path):
    ctx = good_ctx(battery=5, running=True, conditions="Wet", yard={"ok": False, "detail": "dog", "events": [1]})
    store, cloud, c = make_controller(tmp_path, ctx, enabled=True)
    assert run(c.send("stop"))["sent"] and run(c.send("cmd_recharge"))["sent"]


def test_unknown_frigate_does_not_block(tmp_path):
    store, cloud, c = make_controller(tmp_path, good_ctx(yard={"ok": None, "detail": "Frigate not set up", "events": []}),
                                      enabled=True)
    assert run(c.send("start_plan", {"plan_id": 1}))["sent"]


# -- scheduler -------------------------------------------------------------------------------------------------------
def test_window_validation_and_occurrence():
    w = scheduler.validate({"days": [1, 4, 9], "start": "9:00", "end": "14:00", "plan_id": "1"})
    assert w["days"] == [1, 4] and w["start"] == "09:00" and w["plan_id"] == 1
    tue_10 = datetime(2026, 10, 6, 10, 0)   # a Tuesday
    assert scheduler.active_occurrence(w, tue_10) == "2026-10-06"
    assert scheduler.active_occurrence(w, tue_10.replace(hour=15)) is None
    assert scheduler.active_occurrence(w, datetime(2026, 10, 7, 10, 0)) is None  # Wednesday
    with pytest.raises(ValueError):
        scheduler.validate({"start": "14:00", "end": "09:00"})


def test_tick_raises_one_pending_then_stops(tmp_path):
    store = Store(str(tmp_path))
    s = scheduler.Scheduler(store)
    s.save_windows([{"id": "w1", "days": [1], "start": "09:00", "end": "14:00", "plan_id": 1, "head": "mower"}])
    raised, started = [], []

    async def evaluate(w):
        return True, [], "ok"

    async def raise_pending(w, checks):
        raised.append(w["id"])
        store.set_setting("schedule_pending", {"window_id": w["id"], "date": "2026-10-06", "plan_id": 1,
                                               "created": 9e12})
        store.log_schedule(w["id"], "2026-10-06", "pending", {})

    async def auto(w):
        started.append(w)

    now = datetime(2026, 10, 6, 10, 0)
    for minute in range(3):
        run(s.tick(now.replace(minute=minute), 5, evaluate, raise_pending, auto))
    assert raised == ["w1"] and started == []


def test_window_that_never_passes_is_logged_as_skipped(tmp_path):
    store = Store(str(tmp_path))
    s = scheduler.Scheduler(store)
    s.save_windows([{"id": "w1", "days": [1], "start": "09:00", "end": "10:00", "plan_id": 1}])

    async def evaluate(w):
        return False, [], "Lawn dry: Wet"

    async def noop(*a):
        raise AssertionError("must not run")

    run(s.tick(datetime(2026, 10, 6, 9, 30), 5, evaluate, noop, noop))
    run(s.tick(datetime(2026, 10, 6, 10, 1), 5, evaluate, noop, noop))
    assert store.schedule_outcome("w1", "2026-10-06") == "skipped"
    assert store.schedule_log()[0]["detail"]["reason"] == "Lawn dry: Wet"


def test_snow_window_skips_while_the_mower_head_is_on(tmp_path):
    store = Store(str(tmp_path))
    s = scheduler.Scheduler(store)
    s.save_windows([{"id": "s1", "days": [1], "start": "05:00", "end": "07:00", "plan_id": 1, "head": "snow"}])

    async def evaluate(w):
        raise AssertionError("gates are not evaluated for the wrong head")

    run(s.tick(datetime(2026, 10, 6, 6, 0), 5, evaluate, evaluate, evaluate))
    assert "head" in s.last_eval["s1:2026-10-06"]["reason"]


# -- maintenance and geometry ------------------------------------------------------------------------------------------
def test_maintenance_progress():
    items = [{"id": "a", "name": "Blades", "unit": "blade_hours", "interval": 10, "baseline": 2, "done_at": 0},
             {"id": "b", "name": "Lens", "unit": "days", "interval": None, "baseline": 0, "done_at": 0},
             {"id": "c", "name": "Season", "unit": "head_change", "interval": None, "baseline": 0, "done_at": 0}]
    st = {i["id"]: i for i in maintenance.status(items, {"blade_hours": 11, "days": 5, "head_change": 1})}
    assert st["a"]["pct"] == 90 and st["a"]["due"] == "due soon"
    assert st["b"]["due"] == "set an interval"
    assert st["c"]["due"] == "due"


def test_blade_hours_count_only_spinning_samples(tmp_path):
    store = Store(str(tmp_path))
    run_id = store.start_run(0, 5, None)
    for i in range(3600):
        store.add_telemetry({"ts": float(i), "run_id": run_id, "blade_rpm_l": 3000 if i < 1800 else 0, "blade_rpm_r": 0})
    c = maintenance.counters_from(store, 0)
    assert c["blade_hours"] == pytest.approx(0.5, abs=0.01)
    assert c["mows"] == 1


def test_point_in_ring():
    ring = [[0, 0], [10, 0], [10, 10], [0, 10]]
    assert point_in_ring(5, 5, ring) and not point_in_ring(15, 5, ring)


def test_ring_area_and_double_click_cleanup():
    # ~10 m x ~10 m square at mid latitudes
    sq = [[-76.5, 39.0], [-76.5 + 10 / 86512, 39.0], [-76.5 + 10 / 86512, 39.0 + 10 / 111111],
          [-76.5, 39.0 + 10 / 111111]]
    assert geo.ring_area_m2(sq) == pytest.approx(100, rel=0.02)
    # what a double-click used to save: two corners and a repeat of the second
    flat = [[-76.5, 39.0], [-76.5001, 39.00005], [-76.5001, 39.00005]]
    cleaned = geo.clean_ring(flat)
    assert len(cleaned) == 2 and geo.ring_area_m2(cleaned) == 0
    assert geo.clean_ring(sq + [sq[0]]) == sq
