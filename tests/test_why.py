"""Why the robot cannot run (app/why.py): every source of a refusal, skip, pause or early end turns into the same
reasons with a fix, the controller and scheduler carry them, a skip notifies with exactly those words, and
maintenance starts at Yarbo's own intervals without touching what the user set."""
import asyncio
from datetime import datetime

import pytest

from app import control, maintenance, scheduler, why
from app.store import Store
from tests.test_phase2 import good_ctx, make_controller


def run(coro):
    return asyncio.run(coro)


def checks_for(**over):
    return control.check("start_plan", {"plan_id": 1}, good_ctx(**over), control.GATE_DEFAULTS, "user")


def test_a_clean_start_has_no_reasons():
    assert why.from_checks(checks_for()) == []


def test_each_failed_check_says_what_fixes_it():
    rs = why.from_checks(checks_for(battery=10, conditions="Wet", yard={"ok": None, "detail": "Frigate unreachable"}))
    by = {r["code"]: r for r in rs}
    assert by["battery"]["fix"] == "Let it charge on the dock" and by["battery"]["severity"] == "block"
    assert by["lawn"]["severity"] == "warn"                       # a person may override a wet lawn
    assert "yard" not in by                                       # unknown yard is soft for a person (shown in checks)
    assert all(r["title"] and r["fix"] and r["link"] for r in rs)


def test_unknown_reads_as_cannot_tell():
    c = {"name": "Yard clear (Frigate)", "ok": None, "hard": True, "detail": "Frigate unreachable"}
    r = why.from_check(c)
    assert r["title"].startswith("Cannot tell") and "Frigate" in r["fix"]


def test_no_plans_is_one_reason_not_a_missing_plan():
    checks = control.check("start_plan", {"plan_id": None}, good_ctx(plans=[]), control.GATE_DEFAULTS, "user")
    rs = why.from_checks(checks, enabled=True, plans=[])
    assert [r["code"] for r in rs] == ["no_plans"] and "Yarbo app" in rs[0]["fix"]


def test_control_off_comes_first():
    rs = why.from_checks(checks_for(battery=10), enabled=False)
    assert [r["code"] for r in rs] == ["control_off", "battery"]


def test_preview_and_refusal_carry_the_reasons(tmp_path):
    _, _, c = make_controller(tmp_path, ctx=good_ctx(battery=10), enabled=True)
    p = run(c.preview("start_plan", {"plan_id": 1}))
    assert [r["code"] for r in p["why"]] == ["battery"]
    sent = run(c.send("start_plan", {"plan_id": 1}))
    assert not sent["sent"] and [r["code"] for r in sent["why"]] == ["battery"]
    _, _, off = make_controller(tmp_path / "off", enabled=False)
    refused = run(off.send("pause", {}))
    assert [r["code"] for r in refused["why"]] == ["control_off"]


def test_window_reasons_for_head_and_missing_plan():
    assert why.from_window([], "attached head is not a snow head")[0]["code"] == "wrong_head"
    assert why.from_window([], "no plan chosen for this window")[0]["code"] == "no_plan_chosen"
    assert why.from_window([], "ok") == []


def test_robot_pauses_and_run_ends():
    assert why.from_pause(1) is None and why.from_pause(0) is None and why.from_pause(None) is None
    assert why.from_pause(4)["code"] == "estop" and "stop button" in why.from_pause(4)["fix"]
    assert why.from_pause(2)["severity"] == "info"
    assert why.from_pause(42)["code"] == "paused"
    assert why.from_end("plan finished") is None and why.from_end(None) is None
    assert why.from_end("error 902")["code"] == "run_error"
    assert why.from_end("paused, not resumed")["code"] == "not_resumed"
    assert why.from_end("stopped")["code"] == "stopped"
    assert why.from_error("WP017 Exceeded boundary")["fix"].startswith("Check the boundary")


def test_a_skipped_window_keeps_and_sends_its_reasons(tmp_path):
    store = Store(str(tmp_path))
    s = scheduler.Scheduler(store)
    s.save_windows([{"id": "w1", "days": [1], "start": "09:00", "end": "10:00", "plan_id": 1}])
    checks = checks_for(conditions="Wet")
    skipped = []

    async def evaluate(w):
        ok, reason = control.verdict(checks, override=False)
        return ok, checks, reason

    async def noop(*a):
        raise AssertionError("must not run")

    async def on_skip(w, reasons, text):
        skipped.append(why.lines(reasons))

    run(s.tick(datetime(2026, 10, 6, 9, 30), 5, evaluate, noop, noop, on_skip))
    assert s.last_eval["w1:2026-10-06"]["why"][0]["code"] == "lawn"
    run(s.tick(datetime(2026, 10, 6, 10, 1), 5, evaluate, noop, noop, on_skip))
    logged = store.schedule_log()[0]["detail"]["why"]
    assert skipped == [why.lines(logged)] and skipped[0].startswith("The lawn is not dry.")


# -- maintenance defaults ---------------------------------------------------------------------------------------------
def test_new_installs_start_at_yarbos_intervals():
    items = {i["key"]: i for i in maintenance.default_items({"days": 100.0})}
    assert items["blades"]["unit"] == "days" and items["blades"]["interval"] == 90
    assert items["track_grease"]["interval"] == 14 and items["battery"]["interval"] == 500
    assert items["deck"]["interval"] is None                      # Yarbo gives no number: left to the user
    st = maintenance.status(list(items.values()), {"days": 100.0})
    assert all(i["recommended"]["source"].startswith("https://") for i in st)


def test_an_older_install_is_upgraded_once_without_touching_the_users_values():
    old = [{"id": "a", "name": "Blades: inspect or replace", "unit": "blade_hours", "interval": None, "done_at": 86400 * 10,
            "baseline": 0, "note": ""},
           {"id": "b", "name": "Battery health review", "unit": "charge_cycles", "interval": 300, "done_at": 0,
            "baseline": 5, "note": ""},
           {"id": "c", "name": "My own thing", "unit": "runs", "interval": 7, "done_at": 0, "baseline": 0, "note": ""}]
    up = {i["id"]: i for i in maintenance.upgrade(old, {"charge_cycles": 9, "runs": 3})}
    assert up["a"]["unit"] == "days" and up["a"]["interval"] == 90
    assert up["a"]["done_at"] > 86400 * 10 and up["a"]["baseline"] == pytest.approx(up["a"]["done_at"] / 86400, abs=0.01)
    assert up["b"]["interval"] == 300 and up["b"]["baseline"] == 5 and up["b"]["key"] == "battery"
    assert up["c"] == old[2]
    keys = [i.get("key") for i in up.values()]
    assert keys.count("track_grease") == 1 and keys.count("blades") == 1
    again = maintenance.upgrade(list(up.values()), {})
    assert len(again) == len(up)
