import asyncio
import copy

import pytest

from app import mapstudio as ms, mapwrite as mw
from app.store import Store
from tests.test_mapstudio import REF, lonlat, pts, sample_map


# A complete read_mower_pathway_params reply (as the robot sends it, 2026-10-06).
P_FULL = {"blade_height": 0, "blade_speed": 80, "current_offset": -1.0, "en_blade": False, "en_trimmer": False,
          "land_push_pod_place": 50, "offset_enable": False, "offset_range": [-0.5, 0.5], "offset_step": 0.1,
          "pathway_obs_perception_sw": True, "plan_speed": 0.5, "sensor_switch": 11, "turn_type": 0}


class FakeRobot:
    """Answers like the robot did in capture C2: ids assigned on save, the saved feature in the reply."""

    def __init__(self, raw, fail=(), mangle=None):
        self.map = copy.deepcopy(raw)
        self.next_id = 900
        self.sent = []
        self.fail = set(fail)
        self.mangle = mangle
        self.pending = {}

    def expect(self, cmd):
        fut = asyncio.get_running_loop().create_future()
        self.pending.setdefault(cmd, []).append(fut)
        return fut

    def publish(self, cmd, payload):
        self.sent.append((cmd, copy.deepcopy(payload)))
        reply = self._apply(cmd, payload)
        for fut in self.pending.pop(cmd, []):
            fut.set_result(reply)

    def _apply(self, cmd, payload):
        if cmd in self.fail:
            self.fail.discard(cmd)
            return {"state": -1, "msg": "refused"}
        kind = "nogozones" if "nogozone" in cmd else "pathways"
        if cmd in ("save_nogozone", "save_pathway"):
            f = {**copy.deepcopy(payload), "id": self.next_id}
            self.next_id += 1
            if self.mangle:
                f = self.mangle(f)
            self.map.setdefault(kind, []).append(f)
            return {"state": 0, "msg": "", "data": f}
        if cmd in ("del_nogozone", "del_pathway"):
            self.map[kind] = [f for f in self.map[kind] if f["id"] != payload["id"]]
            return {"state": 0, "msg": "deleted successfully."}
        return {"state": 0, "msg": "", "data": ""}

    async def read_map(self):
        return copy.deepcopy(self.map)

    async def read_params(self, pid):
        return {**P_FULL, "blade_speed": 55, "en_blade": True, "current_offset": 0.2, "id": pid}


def writer(robot, tmp_path):
    store = Store(str(tmp_path))
    return store, mw.MapWriter(store, robot.publish, robot.expect, robot.read_map, robot.read_params, lambda: "me@example.com")


def a_draft():
    base = sample_map()
    d, _ = ms.apply_edit(base, {"op": "add", "kind": "nogozones", "name": "tree",
                                "coords": [lonlat(10, 10), lonlat(12, 10), lonlat(12, 12), lonlat(10, 12)]}, REF)
    ring = [lonlat(p["x"], p["y"]) for p in base["pathways"][0]["range"]]
    ring[1] = lonlat(30, 7)
    d, _ = ms.apply_edit(d, {"op": "shape", "kind": "pathways", "id": 52, "coords": ring}, REF)
    d, _ = ms.apply_edit(d, {"op": "delete", "kind": "nogozones", "id": 45}, REF)
    return base, d


def test_payloads_are_shaped_like_the_app():
    base, draft = a_draft()
    p = mw.plan(base, draft, REF)
    save = next(s for s in p["steps"] if s["cmd"] == "save_nogozone")
    assert set(save["payload"]) == {"range", "ref", "name", "type", "enable", "trimming_edges"}
    path = next(s for s in p["steps"] if s["cmd"] == "save_pathway")
    assert set(path["payload"]) == {"range", "ref", "name", "connectids", "snowPiles", "leaf_piles", "trimming_edges"}
    assert [s["op"] for s in p["steps"]] == ["add", "add", "delete", "delete"]   # adds first, deletes last


def test_a_draft_is_written_verified_and_backed_up(tmp_path):
    base, draft = a_draft()
    robot = FakeRobot(base)
    store, w = writer(robot, tmp_path)
    r = asyncio.run(w.write(base, draft, REF))
    assert r["ok"] and r["verified"] and r["cloud_backup"], r
    cmds = [c for c, _ in robot.sent]
    assert cmds == ["save_nogozone", "save_pathway", "save_mower_pathway_params", "del_pathway", "del_nogozone",
                    "upload_cloud_map_backup"]
    params = dict(robot.sent[2][1])
    assert params["blade_speed"] == 55 and params["en_blade"] is True and "current_offset" not in params  # carried over
    assert params["id"] == 901
    assert [f["name"] for f in robot.map["nogozones"]] == ["tree"]   # "bed" deleted, "tree" added
    assert [f["id"] for f in robot.map["pathways"]] == [901]
    log = store.control_log()
    assert len(log) == 6 and all(row["actor"] == "map" for row in log)
    assert "me@example.com" not in str(log) and "latitude" not in str(log)   # no account or location in the audit


def test_deleting_an_area_is_refused_before_anything_is_sent(tmp_path):
    base = sample_map()
    draft, _ = ms.apply_edit(base, {"op": "delete", "kind": "areas", "id": 1}, REF)
    robot = FakeRobot(base)
    _, w = writer(robot, tmp_path)
    assert "plans that use it" in mw.plan(base, draft, REF)["blocked"][0]["why"]
    with pytest.raises(mw.WriteRefused, match="nothing to write"):
        asyncio.run(w.write(base, draft, REF))
    assert robot.sent == []


def test_unsendable_changes_do_not_block_the_rest_and_stay_in_the_draft(tmp_path):
    """2026-10-06: an unsendable change used to block a whole draft. Now the sendable part goes out, the rest is
    laid back on top of the robot's new map (here: deleting an area, which the panel never sends)."""
    base, draft = a_draft()
    draft, _ = ms.apply_edit(draft, {"op": "delete", "kind": "areas", "id": 1}, REF)
    robot = FakeRobot(base)
    _, w = writer(robot, tmp_path)
    r = asyncio.run(w.write(base, draft, REF))
    assert r["ok"] and r["verified"], r
    assert ms.find(robot.map, "areas", 1) is not None                               # the area was not deleted
    blocked = mw.plan(base, draft, REF)["blocked"]
    kept = mw.carry_unsent(robot.map, draft, blocked)
    assert ms.find(kept, "areas", 1) is None
    assert [f["name"] for f in kept["nogozones"]] == ["tree"]                        # the sent part is the robot's
    left = mw.plan(robot.map, kept, REF)
    assert not left["steps"] and [(b["kind"], b["id"]) for b in left["blocked"]] == [("areas", 1)]


def test_an_area_rename_is_an_in_place_update(tmp_path):
    base = sample_map()
    draft, _ = ms.apply_edit(base, {"op": "rename", "kind": "areas", "id": 1, "name": "front lawn"}, REF)
    p = mw.plan(base, draft, REF)
    assert [(s["op"], s["cmd"]) for s in p["steps"]] == [("update", "save_clean_area")] and not p["blocked"]
    # a no-go zone rename is a change like any other: a new copy, then the old one deleted
    draft, _ = ms.apply_edit(base, {"op": "rename", "kind": "nogozones", "id": 45, "name": "flower bed"}, REF)
    p = mw.plan(base, draft, REF)
    assert [s["op"] for s in p["steps"]] == ["add", "delete"] and p["steps"][0]["payload"]["name"] == "flower bed"


def test_a_failed_step_rolls_back(tmp_path):
    base, draft = a_draft()
    robot = FakeRobot(base, fail={"del_nogozone"})
    _, w = writer(robot, tmp_path)
    r = asyncio.run(w.write(base, draft, REF))
    assert not r["ok"] and "refused" in r["error"]
    assert r["rollback_ok"], r
    assert mw._same_shapes(base, robot.map, REF)
    assert "upload_cloud_map_backup" not in [c for c, _ in robot.sent]


def test_a_robot_copy_that_does_not_match_rolls_back(tmp_path):
    base, draft = a_draft()

    def shift(f):                       # the robot stores the new zone 1 m east of where it was sent
        if f.get("name") == "tree":
            f["range"] = [{**p, "x": p["x"] - 1} for p in f["range"]]
        return f

    robot = FakeRobot(base, mangle=shift)
    _, w = writer(robot, tmp_path)
    r = asyncio.run(w.write(base, draft, REF))
    assert not r["ok"] and not r["verified"] and any("off" in p for p in r["problems"])
    assert r["rollback_ok"] and mw._same_shapes(base, robot.map, REF)


def test_only_map_write_commands_go_out(tmp_path):
    robot = FakeRobot(sample_map())
    _, w = writer(robot, tmp_path)
    with pytest.raises(mw.WriteRefused):
        asyncio.run(w._send("cmd_vel", {"vel": 1}))
    assert robot.sent == []
