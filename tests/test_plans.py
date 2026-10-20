"""Plan create/edit (save_plan, captured from the app 2026-10-10): payload, validation, verified write and restore."""
import asyncio
import copy

import pytest

from app import mapwrite as mw
from app.store import Store

PLANS = [{"areaIds": [11, 34], "enable_self_order": True, "id": 1, "name": "Mow All"},
         {"areaIds": [34], "enable_self_order": True, "id": 2, "name": "Mow Side"}]
AREAS = [11, 34]


class PlanRobot:
    """Keeps plans; save_plan replies state 0. `mangle` changes the first save's areas; `drop` loses new plans."""

    def __init__(self, plans=PLANS, mangle=False, drop=False):
        self.plans = copy.deepcopy(plans)
        self.mangle, self.drop, self.sent, self.pending = mangle, drop, [], {}

    def expect(self, cmd):
        fut = asyncio.get_running_loop().create_future()
        self.pending.setdefault(cmd, []).append(fut)
        return fut

    def publish(self, cmd, payload):
        self.sent.append((cmd, copy.deepcopy(payload)))
        if cmd == "del_plan":
            if not self.drop:
                self.plans = [p for p in self.plans if p["id"] != payload["id"]]
            for fut in self.pending.pop(cmd, []):
                fut.set_result({"state": 0, "msg": "", "data": ""})
            return
        rec = copy.deepcopy(payload)
        if self.mangle:
            rec["areaIds"], self.mangle = [11], False
        old = next((i for i, p in enumerate(self.plans) if p["id"] == rec["id"]), None)
        if old is not None:
            self.plans[old] = rec
        elif not self.drop:
            self.plans.append(rec)
        for fut in self.pending.pop(cmd, []):
            fut.set_result({"state": 0, "msg": "", "data": ""})

    async def read_plans(self):
        return copy.deepcopy(self.plans)

    async def read_map(self):
        return {}

    async def read_params(self, pid):
        return None


def _writer(robot, tmp_path):
    store = Store(str(tmp_path))
    return store, mw.MapWriter(store, robot.publish, robot.expect, robot.read_map, robot.read_params, lambda: "")


def test_new_plan_takes_the_next_id_like_the_app():
    p = mw.plan_payload(PLANS, AREAS, {"name": " All Yard Only ", "areaIds": [11]})
    assert p == {"id": 3, "areaIds": [11], "name": "All Yard Only", "enable_self_order": True}   # app-edit-1791687834
    assert mw.plan_payload([], AREAS, {"name": "First", "areaIds": [34]})["id"] == 1


def test_edit_keeps_what_was_not_sent():
    p = mw.plan_payload(PLANS, AREAS, {"name": "Everything"}, 1)
    assert p == {"id": 1, "areaIds": [11, 34], "name": "Everything", "enable_self_order": True}


@pytest.mark.parametrize("body,pid,why", [
    ({"name": "", "areaIds": [11]}, None, "name"),
    ({"name": "x" * 41, "areaIds": [11]}, None, "40"),
    ({"name": "mow all", "areaIds": [11]}, None, "already"),
    ({"name": "New", "areaIds": []}, None, "at least one"),
    ({"name": "New", "areaIds": [11, 11]}, None, "distinct"),
    ({"name": "New", "areaIds": [99]}, None, "no area 99"),
    ({"name": "New", "areaIds": [True]}, None, "distinct"),
    ({"name": "New"}, 9, "not on the robot"),
])
def test_bad_input_is_refused(body, pid, why):
    with pytest.raises(ValueError, match=why):
        mw.plan_payload(PLANS, AREAS, body, pid)


def test_renaming_a_plan_to_its_own_name_is_allowed():
    assert mw.plan_payload(PLANS, AREAS, {"name": "Mow All", "areaIds": [34, 11]}, 1)["areaIds"] == [34, 11]


def test_create_is_sent_and_read_back(tmp_path):
    robot = PlanRobot()
    store, w = _writer(robot, tmp_path)
    r = asyncio.run(mw.write_plan(w, robot.read_plans, AREAS, {"name": "All Yard Only", "areaIds": [11]}))
    assert r["ok"] and r["created"] and r["plan"]["id"] == 3
    assert robot.sent == [("save_plan", {"id": 3, "areaIds": [11], "name": "All Yard Only", "enable_self_order": True})]
    assert any(e["kind"] == "plan_saved" for e in store.events(10))


def test_unchanged_sends_nothing(tmp_path):
    robot = PlanRobot()
    _, w = _writer(robot, tmp_path)
    r = asyncio.run(mw.write_plan(w, robot.read_plans, AREAS, {"name": "Mow Side", "areaIds": [34]}, 2))
    assert r["ok"] and r["unchanged"] and robot.sent == []


def test_an_edit_the_robot_got_wrong_is_put_back(tmp_path):
    robot = PlanRobot(mangle=True)
    _, w = _writer(robot, tmp_path)
    r = asyncio.run(mw.write_plan(w, robot.read_plans, AREAS, {"areaIds": [34]}, 1))
    assert not r["ok"] and r["rollback_ok"]
    assert robot.sent[-1] == ("save_plan", PLANS[0]) and robot.plans[0] == PLANS[0]


def test_a_new_plan_the_robot_does_not_list_is_reported(tmp_path):
    robot = PlanRobot(drop=True)
    _, w = _writer(robot, tmp_path)
    r = asyncio.run(mw.write_plan(w, robot.read_plans, AREAS, {"name": "Gone", "areaIds": [11]}))
    assert not r["ok"] and "does not list" in r["error"] and len(robot.sent) == 1


def test_the_gate_is_checked_under_the_lock(tmp_path):
    robot = PlanRobot()
    _, w = _writer(robot, tmp_path)
    with pytest.raises(mw.WriteRefused, match="switched off"):
        asyncio.run(mw.write_plan(w, robot.read_plans, AREAS, {"name": "X", "areaIds": [11]},
                                  check=lambda: "map writing is switched off"))
    assert robot.sent == []


def test_delete_is_sent_as_the_app_does_and_read_back(tmp_path):
    robot = PlanRobot()
    store, w = _writer(robot, tmp_path)
    r = asyncio.run(mw.delete_plan(w, robot.read_plans, 2))
    assert r["ok"] and r["deleted"]["name"] == "Mow Side" and [p["id"] for p in r["plans"]] == [1]
    assert robot.sent == [("del_plan", {"id": 2})]                    # app-edit-1791688547
    assert any(e["kind"] == "plan_deleted" for e in store.events(10))


def test_delete_that_does_not_take_is_reported(tmp_path):
    robot = PlanRobot(drop=True)
    _, w = _writer(robot, tmp_path)
    r = asyncio.run(mw.delete_plan(w, robot.read_plans, 2))
    assert not r["ok"] and "still lists" in r["error"]


def test_delete_of_an_unknown_plan_sends_nothing(tmp_path):
    robot = PlanRobot()
    _, w = _writer(robot, tmp_path)
    with pytest.raises(ValueError, match="not on the robot"):
        asyncio.run(mw.delete_plan(w, robot.read_plans, 9))
    assert robot.sent == []


class BackupRobot(PlanRobot):
    """get_all_map_backup lists; rename_map_backup renames unless `ignore`."""

    def __init__(self, ignore=False):
        self.list = [{"id": 254, "name": "Auto Saved", "timestamp": 1782566833, "is_auto_backup": False}]
        self.ignore, self.sent, self.pending = ignore, [], {}

    def expect(self, cmd):
        fut = asyncio.get_running_loop().create_future()
        self.pending.setdefault(cmd, []).append(fut)
        return fut

    def publish(self, cmd, payload):
        self.sent.append((cmd, copy.deepcopy(payload)))
        if cmd == "rename_map_backup" and not self.ignore:
            next(b for b in self.list if b["id"] == payload["id"])["name"] = payload["name"]
        data = copy.deepcopy(self.list) if cmd == "get_all_map_backup" else "ok"
        for fut in self.pending.pop(cmd, []):
            fut.set_result({"state": 0, "msg": "", "data": data})


def _backups(robot, tmp_path):
    from app.backups import Backups
    store, w = _writer(robot, tmp_path)
    return store, Backups(store, w, None, None, None)


def test_backup_rename_is_sent_as_the_app_does_and_read_back(tmp_path):
    robot = BackupRobot()
    store, b = _backups(robot, tmp_path)
    r = asyncio.run(b.rename(254, " Before the pathway work "))
    assert r["ok"] and ("rename_map_backup", {"id": 254, "name": "Before the pathway work"}) in robot.sent
    assert any(e["kind"] == "backup_renamed" for e in store.events(10))


def test_backup_rename_the_robot_ignores_is_reported(tmp_path):
    robot = BackupRobot(ignore=True)
    _, b = _backups(robot, tmp_path)
    r = asyncio.run(b.rename(254, "New"))
    assert not r["ok"] and "kept another name" in r["error"]


def test_backup_rename_refuses_bad_names_and_unknown_ids(tmp_path):
    robot = BackupRobot()
    _, b = _backups(robot, tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(b.rename(254, "  "))
    with pytest.raises(mw.WriteRefused):
        asyncio.run(b.rename(999, "x"))
    assert [c for c, _ in robot.sent if c == "rename_map_backup"] == []
