"""A start part-way through a plan (start_plan's start_percent): the payload is the SDK's, only a whole number in range
becomes a start point, and the start confirmation offers where the last run got to and shows the checks for exactly
what it sends."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app import control, why
from tests.test_phase2 import good_ctx, make_controller, run

ROOT = Path(__file__).resolve().parent.parent
needs_node = pytest.mark.node      # tests/conftest.py: skipped without node, fails in CI


def test_a_start_point_goes_out_as_the_sdks_percent(tmp_path):
    store, cloud, c = make_controller(tmp_path, enabled=True)
    r = run(c.send("start_plan", {"plan_id": 1, "start_percent": 40}))
    assert r["sent"] and cloud.sent == [("start_plan", {"id": 1, "percent": 40})]
    assert [x["detail"] for x in r["checks"] if x["name"] == "Start point"] == ["from 40% of the plan"]


@pytest.mark.parametrize("pct", [1, 99])
def test_the_first_and_last_start_points_go_out(tmp_path, pct):
    store, cloud, c = make_controller(tmp_path, enabled=True)
    assert run(c.send("start_plan", {"plan_id": 1, "start_percent": pct}))["sent"]
    assert cloud.sent == [("start_plan", {"id": 1, "percent": pct})]


@pytest.mark.parametrize("args", [{"plan_id": 1}, {"plan_id": 1, "start_percent": None},
                                  {"plan_id": 1, "start_percent": 0}])
def test_a_start_from_the_beginning_sends_no_percent(tmp_path, args):
    store, cloud, c = make_controller(tmp_path, enabled=True)
    assert run(c.send("start_plan", args))["sent"]
    assert cloud.sent == [("start_plan", {"id": 1})]


def test_the_start_point_is_listed_only_when_one_was_asked_for():
    def names(args):
        return [x["name"] for x in control.check("start_plan", args, good_ctx(), control.GATE_DEFAULTS, "user")]
    assert "Start point" not in names({"plan_id": 1})
    assert "Start point" in names({"plan_id": 1, "start_percent": 0})
    assert "Start point" in names({"plan_id": 1, "start_percent": 99})


@pytest.mark.parametrize("bad", ["40", 40.0, 40.5, True, False, -1, 100, 250, [40], {"pct": 40}, ""])
@pytest.mark.parametrize("actor", ["user", "token", "scheduler"])
def test_anything_but_a_whole_number_in_range_is_refused_not_rounded(tmp_path, bad, actor):
    store, cloud, c = make_controller(tmp_path, enabled=True)
    args = {"plan_id": 1, "start_percent": bad}
    r = run(c.send("start_plan", args, actor=actor, override=True, wait=False))
    assert not r["sent"] and cloud.sent == [] and "Start point" in r["refused"]
    assert run(c.preview("start_plan", args, actor=actor))["would_send"] is False


@pytest.mark.parametrize("bad", ["40", True, 100, 40.0])
def test_a_bad_start_point_builds_no_payload_at_all(bad):
    """Should a caller ever skip the check: nothing is built, rather than a start from the beginning nobody chose."""
    with pytest.raises(control.Refused):
        control.payload_for("start_plan", {"plan_id": 1, "start_percent": bad})


def test_a_refused_start_point_says_what_to_pick():
    checks = control.check("start_plan", {"plan_id": 1, "start_percent": 100}, good_ctx(), control.GATE_DEFAULTS,
                           "user")
    r = {x["code"]: x for x in why.from_checks(checks)}["start_point"]
    assert r["severity"] == "block" and "0 to 99" in r["fix"]


def test_a_start_point_does_not_relax_the_other_start_checks(tmp_path):
    store, cloud, c = make_controller(tmp_path, ctx=good_ctx(running=True), enabled=True)
    r = run(c.send("start_plan", {"plan_id": 1, "start_percent": 40}))
    assert not r["sent"] and "Not already running" in r["refused"] and cloud.sent == []


def test_resume_takes_no_start_point(tmp_path):
    store, cloud, c = make_controller(tmp_path, ctx=good_ctx(running=True), enabled=True)
    assert run(c.send("resume", {"start_percent": 40}))["sent"]
    assert cloud.sent == [("resume", {})]


def start_points() -> dict:
    out = subprocess.run([shutil.which("node"), str(ROOT / "tests" / "start_point_flow.cjs")],
                         capture_output=True, encoding="utf-8", timeout=60, check=True).stdout
    return json.loads(out)


@needs_node
def test_the_confirmation_offers_where_the_last_run_got_to():
    r = start_points()
    assert r["early"] == {"pct": 42, "ended": 20000}                   # rounded down, so a start there overlaps
    assert r["allButDone"]["pct"] == 99
    assert r["beganAtTheStart"]["pct"] == 42 and r["refusedPartWay"]["pct"] == 42 and r["olderPartWay"]["pct"] == 42
    assert r["points"] == sorted({0, 42, *range(5, 100, 5)})


@needs_node
@pytest.mark.parametrize("case", ["noRun", "finished", "panelRestarted", "neverResumed", "stillOpen", "otherPlan",
                                  "noSession", "noProgress", "barelyBegun", "beganPartWay"])
def test_no_left_off_point_is_offered_when_the_last_run_cannot_say(case):
    assert start_points()[case] is None


@needs_node
def test_the_start_points_are_the_beginning_and_every_five_percent():
    r = start_points()
    assert r["pointsPlain"] == [0, *range(5, 100, 5)]
    assert r["pointsPicked"] == sorted({0, 42, *range(5, 100, 5)})


@needs_node
def test_the_confirmation_names_the_start_point_it_sends():
    r = start_points()
    assert r["choice"] == {"label": "Start Mow All from 40%", "args": {"plan_id": 1, "start_percent": 40}}
    assert r["choiceBeginning"] == {"label": "Start Mow All", "args": {"plan_id": 1}}
    # Picking one: Send is off until the checks for it are back, and an earlier override does not carry over.
    assert r["afterPick"] == {"label": "Start Mow All from 40%", "args": {"plan_id": 1, "start_percent": 40},
                              "preview": None, "override": False}
    assert r["backToStart"] == {"label": "Start Mow All", "args": {"plan_id": 1}, "preview": {"for": 0}}


@needs_node
def test_a_late_answer_for_an_earlier_start_point_is_dropped():
    r = start_points()
    assert r["afterRace"]["asked"] == [{"plan_id": 1, "start_percent": 40}, {"plan_id": 1, "start_percent": 45}]
    assert r["afterRace"]["args"] == {"plan_id": 1, "start_percent": 45}
    assert r["afterRace"]["preview"] == {"for": 45}                    # not the 40% answer that came back last
    assert r["afterClose"] is None                                     # an answer after closing is not taken up


@needs_node
def test_a_start_point_picked_after_send_was_pressed_changes_nothing():
    r = start_points()["whileSending"]
    assert r == {"label": "Start Mow All from 40%", "args": {"plan_id": 1, "start_percent": 40},
                 "preview": {"would_send": True}, "pct": 40, "asked": 4}       # no fifth preview was asked for


@needs_node
def test_send_stays_off_once_it_was_pressed():
    """The real dialog, drawn into a plain tree: Send is pressed, its request left pending, the dialog drawn again."""
    out = subprocess.run([shutil.which("node"), str(ROOT / "tests" / "start_send_flow.cjs")],
                         capture_output=True, encoding="utf-8", timeout=60, check=True).stdout
    r = json.loads(out)
    assert r["before"] == {"sendOff": False, "listOff": False}
    assert r["pressed"] == {"sendOff": True, "listOff": True, "sends": 1}
    assert r["redrawn"] == {"sendOff": True, "listOff": True}          # a redraw does not bring Send back
    assert r["pickedLate"] == {"args": {"plan_id": 1, "start_percent": 40}, "sendOff": True, "previews": 0}
    assert r["after"]["sends"] == 1 and r["after"]["closed"] == 1
    assert r["after"]["sent"] == {"cmd": "start_plan", "args": {"plan_id": 1, "start_percent": 40}, "confirm": True,
                                  "override": False}
    assert r["refused"] == {"sendOff": True}


@needs_node
def test_the_confirmation_names_the_plan_it_sends_when_start_is_clicked_before_the_redraw():
    """Live's Control card, drawn into a plain tree: another plan is picked and Start is clicked on the old card."""
    out = subprocess.run([shutil.which("node"), str(ROOT / "tests" / "start_plan_flow.cjs")],
                         capture_output=True, encoding="utf-8", timeout=60, check=True).stdout
    r = json.loads(out)
    assert r["sameDraw"] == ["start_plan", "Start Mow All", {"plan_id": 1}]
    assert r["afterPick"] == ["start_plan", "Start Mow Side", {"plan_id": 2}]     # not "Start Mow All" with plan 2
    assert r["planGone"] == ["start_plan", "Start plan", {"plan_id": 2}]          # unnamed; the server refuses it
