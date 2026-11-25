"""Live's Control card offers only what the robot can do in its state (web/ops.js controlCard)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def card_keys() -> dict:
    out = subprocess.run([shutil.which("node"), str(ROOT / "tests" / "control_states_flow.cjs")],
                         capture_output=True, encoding="utf-8", timeout=60, check=True).stdout
    return json.loads(out)


@needs_node
def test_docked_offers_the_plan_and_start_and_nothing_for_a_run():
    assert card_keys()["docked"] == ["ctl-plan", "ctl-start_plan"]


@needs_node
def test_a_run_offers_pause_return_and_stop_and_no_start():
    r = card_keys()
    assert r["mowing"] == ["ctl-pause", "ctl-cmd_recharge", "ctl-stop"]
    assert r["held"] == ["ctl-resume", "ctl-cmd_recharge", "ctl-stop"]          # Resume first, as the main button
    assert r["returning"] == ["ctl-stop"]           # already on its way: pausing a return is not offered (NOT_PAUSABLE)


@needs_node
def test_stop_is_on_its_own_row_in_the_refusal_colour():
    assert card_keys()["stopClass"] == "btn stopbtn wide"


@needs_node
def test_an_unknown_state_offers_everything_and_the_server_decides():
    assert card_keys()["unknown"] == ["ctl-plan", "ctl-start_plan", "ctl-pause", "ctl-cmd_recharge", "ctl-resume",
                                      "ctl-stop"]


@needs_node
def test_the_emergency_card_holds_the_run_buttons_when_it_is_on_screen():
    r = card_keys()
    assert r["mowingWithEmergency"] == []
    assert r["dockedWithEmergency"] == ["ctl-plan", "ctl-start_plan"]
    assert r["unknownWithEmergencyStop"] is False
