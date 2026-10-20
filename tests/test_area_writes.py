"""In-place area writes (save_clean_area with the area's id) and robot backup management, 2026-10-07.

The fake robot behaves like the 2026-10-07 captures: save_clean_area with an id replaces that area and replies with
the stored record; without an id it creates a new area; every area save re-links pathway ends to the area they sit
in (0 when none). Area writes keep their safety copy locally; robot backups are only made on request."""
import asyncio
import copy

import pytest

from app import mapstudio as ms, mapwrite as mw
from app.store import Store
from tests.test_mapstudio import REF, lonlat, sample_map

EXTRA = {"algorithm_type": 0, "labels": [3], "leaf_piles": [], "push_snow_dir": 9999.0, "slope_degrees": 9999.0,
         "snowPiles": [], "trimming_edges": [], "area": 400.0, "humps": {}, "mult_grid": 1, "mapping_module_type": 99}


class AreaRobot:
    def __init__(self, raw, backup_ok=True, mangle=None, unlink=False, new_id=False, slots_used=3, silent=False,
                 read_fails=0, read_fail_from=0):
        self.map = copy.deepcopy(raw)
        for a in self.map["areas"]:
            a.update({k: copy.deepcopy(v) for k, v in EXTRA.items() if k not in a})
        self.sent, self.pending = [], {}
        self.backup_ok, self.mangle, self.unlink, self.new_id = backup_ok, mangle, unlink, new_id
        self.slots_used, self.silent, self.read_fails = slots_used, silent, read_fails
        self.read_fail_from, self.reads = read_fail_from, 0

    def expect(self, cmd):
        fut = asyncio.get_running_loop().create_future()
        self.pending.setdefault(cmd, []).append(fut)
        return fut

    def publish(self, cmd, payload):
        self.sent.append((cmd, copy.deepcopy(payload)))
        reply = self._apply(cmd, payload)
        if self.silent and cmd == mw.AREA_SAVE:
            return                                   # applied, reply lost
        for fut in self.pending.pop(cmd, []):
            fut.set_result(reply)

    def _relink(self):
        for p in self.map.get("pathways") or []:
            pts = ms.local(p, REF)
            p["start_id"] = 0 if self.unlink else ms._endpoint_id(self.map, pts[0], REF)
            p["end_id"] = 0 if self.unlink else ms._endpoint_id(self.map, pts[-1], REF)

    def _apply(self, cmd, payload):
        if cmd == "get_all_map_backup":
            if self.slots_used is None:
                return {"state": -1, "msg": "busy", "data": ""}
            return {"state": 0, "msg": "", "data": [{"id": i, "name": "Manually Saved", "is_auto_backup": False}
                                                    for i in range(self.slots_used)]}
        if cmd == mw.BACKUP_CMD:
            return {"state": 0 if self.backup_ok else -1, "msg": "" if self.backup_ok else "backup limit reached", "data": "[]"}
        if cmd == mw.AREA_SAVE:
            rec = copy.deepcopy(payload)
            if self.mangle and self.mangle.pop(0) if self.mangle else False:
                rec["range"] = rec["range"][:3]
            target = None if self.new_id else rec.get("id")
            if target is None:
                rec["id"] = 900
                self.map["areas"].append({**EXTRA, **rec})
            else:
                a = ms.find(self.map, "areas", target)
                a.update(rec)
            self._relink()
            return {"state": 0, "msg": "", "data": {**EXTRA, **rec}}
        if cmd == "upload_cloud_map_backup":
            return {"state": 0, "msg": ""}
        return {"state": -1, "msg": f"unexpected {cmd}"}

    async def read_map(self):
        self.reads += 1
        if self.read_fails and self.reads > self.read_fail_from:
            self.read_fails -= 1
            raise TimeoutError("get_map")
        return copy.deepcopy(self.map)

    async def read_areas(self):
        return {a["id"]: copy.deepcopy(a) for a in self.map["areas"]}

    async def read_params(self, pid):
        return None


def writer(robot, tmp_path):
    store = Store(str(tmp_path))
    return store, mw.MapWriter(store, robot.publish, robot.expect, robot.read_map, robot.read_params,
                               lambda: "me@example.com", robot.read_areas)


def reshaped(base, dx=0.5):
    """'front' (id 1) with its north edge pushed out by dx metres, keeping the pathway end at (20, 5) inside."""
    ring = [lonlat(0, 0), lonlat(20, 0), lonlat(20, 20 + dx), lonlat(0, 20 + dx)]
    draft, _ = ms.apply_edit(base, {"op": "shape", "kind": "areas", "id": 1, "coords": ring}, REF)
    return draft


def test_an_area_is_reshaped_in_place_with_a_local_safety_copy(tmp_path):
    base = sample_map()
    robot = AreaRobot(base)
    store, w = writer(robot, tmp_path)
    current = asyncio.run(robot.read_map())
    r = asyncio.run(w.write(current, reshaped(current), REF))
    assert r["ok"] and r["verified"], r
    cmds = [c for c, _ in robot.sent]
    assert cmds == [mw.AREA_SAVE, "upload_cloud_map_backup"]                        # no robot backup slot used
    snap = store.latest_version("prewrite")
    assert snap["area_records"]["1"]["labels"] == [3] and snap["map"]["areas"]
    payload = robot.sent[0][1]
    assert payload["id"] == 1 and set(payload) == set(mw.AREA_KEYS)                # app keys only, same id
    assert len(robot.map["areas"]) == 2 and ms.find(robot.map, "areas", 1)["labels"] == [3]
    assert "latitude" not in str(store.control_log())


def test_a_rename_goes_out_with_the_full_record(tmp_path):
    robot = AreaRobot(sample_map())
    _, w = writer(robot, tmp_path)
    current = asyncio.run(robot.read_map())
    draft, _ = ms.apply_edit(current, {"op": "rename", "kind": "areas", "id": 1, "name": "front lawn"}, REF)
    r = asyncio.run(w.write(current, draft, REF))
    save = next(p for c, p in robot.sent if c == mw.AREA_SAVE)
    assert r["ok"] and save["name"] == "front lawn" and len(save["range"]) == 4


def test_a_range_the_robot_did_not_keep_is_put_back(tmp_path):
    robot = AreaRobot(sample_map(), mangle=[True])
    _, w = writer(robot, tmp_path)
    current = asyncio.run(robot.read_map())
    original = copy.deepcopy(ms.find(current, "areas", 1)["range"])
    r = asyncio.run(w.write(current, reshaped(current), REF))
    assert not r["ok"] and r["rollback_ok"], r
    assert ms.find(robot.map, "areas", 1)["range"] == original
    assert robot.sent[-1][0] == mw.AREA_SAVE and robot.sent[-1][1]["id"] == 1


def test_a_pathway_that_comes_loose_undoes_the_area_write(tmp_path):
    robot = AreaRobot(sample_map())
    robot._relink()
    _, w = writer(robot, tmp_path)
    current = asyncio.run(robot.read_map())
    robot.unlink = True                                       # the robot drops the pathway links on save
    r = asyncio.run(w.write(current, reshaped(current), REF))
    assert not r["ok"] and any("link changed" in p for p in r["problems"])


def test_an_edit_that_would_strand_a_pathway_end_is_blocked():
    base = sample_map()
    ring = [lonlat(0, 0), lonlat(15, 0), lonlat(15, 20), lonlat(0, 20)]      # the pathway starts at (20, 5)
    draft, _ = ms.apply_edit(base, {"op": "shape", "kind": "areas", "id": 1, "coords": ring}, REF)
    p = mw.plan(base, draft, REF)
    assert not p["steps"] and "outside the area" in p["blocked"][0]["why"]


def test_a_save_that_lands_under_another_id_is_reported_and_never_resent(tmp_path):
    robot = AreaRobot(sample_map(), new_id=True)
    _, w = writer(robot, tmp_path)
    current = asyncio.run(robot.read_map())
    r = asyncio.run(w.write(current, reshaped(current), REF))
    assert not r["ok"] and "created a new area 900" in r["error"] and "delete area 900 in the app" in r["error"]
    assert sum(1 for c, _ in robot.sent if c == mw.AREA_SAVE) == 1                    # no second copy
    assert "NOT undone automatically" in r["rollback"][0] and r["rollback_ok"] is False


def test_a_reference_point_mismatch_is_refused_not_converted():
    rec = {**EXTRA, "id": 1, "name": "front", "ref": {"latitude": REF[0] + 0.0001, "longitude": REF[1]},
           "range": [{"x": 0, "y": 0, "phi": 0}] * 3}
    feature = {"id": 1, "name": "front", "ref": {"latitude": REF[0], "longitude": REF[1]},
               "range": [{"x": 0.0, "y": 0.0, "phi": 0}, {"x": 1.0, "y": 0.0, "phi": 0}, {"x": 1.0, "y": 1.0, "phi": 0}]}
    with pytest.raises(mw.WriteRefused, match="reference point"):
        mw.area_payload(rec, feature, REF)
    with pytest.raises(mw.WriteRefused):
        mw.area_payload(rec, {**feature, "ref": None}, REF)


def test_a_save_whose_reply_is_lost_is_still_undone_when_it_does_not_check_out(tmp_path, monkeypatch):
    monkeypatch.setattr(mw, "REPLY_TIMEOUT_S", 0.05)
    robot = AreaRobot(sample_map(), silent=True, mangle=[True])
    _, w = writer(robot, tmp_path)
    current = asyncio.run(robot.read_map())
    original = copy.deepcopy(ms.find(current, "areas", 1)["range"])
    r = asyncio.run(w.write(current, reshaped(current), REF))
    assert not r["ok"] and r["rollback"] and ms.find(robot.map, "areas", 1)["range"] == original


def test_a_failed_read_back_still_undoes_and_says_so(tmp_path):
    robot = AreaRobot(sample_map(), read_fails=2, read_fail_from=1)   # the pre-write check reads fine
    _, w = writer(robot, tmp_path)
    current = copy.deepcopy(robot.map)
    r = asyncio.run(w.write(current, reshaped(current), REF))
    assert not r["ok"] and "could not be read back" in r["error"] and r["rollback"]


def test_a_big_size_change_needs_an_explicit_ok(tmp_path):
    robot = AreaRobot(sample_map())
    _, w = writer(robot, tmp_path)
    current = asyncio.run(robot.read_map())
    shrunk = reshaped(current, dx=-10)                         # 400 -> 200 m²
    assert mw.plan(current, shrunk, REF)["steps"][0]["big"]
    with pytest.raises(mw.WriteRefused, match="explicit OK"):
        asyncio.run(w.write(current, shrunk, REF))
    assert robot.sent == []
    assert asyncio.run(w.write(current, shrunk, REF, allow_big=True))["ok"]


def test_moving_an_area_over_another_is_blocked():
    base = sample_map()
    ring = [lonlat(0, 0), lonlat(35, 0), lonlat(35, 20), lonlat(0, 20)]   # over "back" (30..40)
    draft, _ = ms.apply_edit(base, {"op": "shape", "kind": "areas", "id": 1, "coords": ring}, REF)
    p = mw.plan(base, draft, REF)
    assert not p["steps"] and "overlap" in p["blocked"][0]["why"]


def test_a_link_moving_to_another_area_is_a_problem():
    before = sample_map()
    after = copy.deepcopy(before)
    after["pathways"][0]["start_id"] = 11                      # its start sits in area 1
    assert mw.link_problems(before, after, before, REF)


def test_an_existing_overlap_does_not_block_a_rename_or_a_small_edit():
    base = sample_map()
    base["areas"][1]["range"] = [{"x": x, "y": y, "phi": 0} for x, y in ((18, 0), (40, 0), (40, 10), (18, 10))]  # 20 m² shared
    draft, _ = ms.apply_edit(base, {"op": "rename", "kind": "areas", "id": 1, "name": "front lawn"}, REF)
    assert [s["op"] for s in mw.plan(base, draft, REF)["steps"]] == ["update"]
    assert [s["op"] for s in mw.plan(base, reshaped(base), REF)["steps"]] == ["update"]


def test_full_robot_backup_slots_do_not_block_an_area_write(tmp_path):
    robot = AreaRobot(sample_map(), slots_used=10)
    _, w = writer(robot, tmp_path)
    current = asyncio.run(robot.read_map())
    assert asyncio.run(w.write(current, reshaped(current), REF))["ok"]
    assert not any(c in (mw.BACKUP_CMD, "get_all_map_backup") for c, _ in robot.sent)


# -- robot backup management (app/backups.py) ------------------------------------------------------------------
class BackupRobot(AreaRobot):
    def __init__(self, raw, **kw):
        super().__init__(raw, **kw)
        self.backups = [{"id": 100 + i, "name": f"Manually Saved {i}", "timestamp": 1000 + i, "is_auto_backup": False}
                        for i in range(kw.get("slots_used", 3) or 0)]
        self.plans = [{"id": 1, "name": "Mow All", "areaIds": [1, 11]}]
        self.backup_maps = {}
        self.params = {a["id"]: {"id": a["id"], "edge_circles": 4, "gap": 0.3, "contract_dis": 0.0254,
                                 "ngz_expand_dis": 0.102, "first_clean_params": {"blade_height": 25},
                                 "double_clean_params": {"blade_height": 0}, "clean_times_dis": 0.0001,
                                 "current_blade_height": 0, "move2area_offset_count": {}} for a in self.map["areas"]}

    def _apply(self, cmd, payload):
        if cmd == "get_all_map_backup":
            return {"state": 0, "msg": "", "data": copy.deepcopy(self.backups)}
        if cmd == mw.BACKUP_CMD:
            self.backups.insert(0, {"id": 999, "name": "Manually Saved 99", "timestamp": 5000, "is_auto_backup": False})
            return {"state": 0, "msg": "", "data": copy.deepcopy(self.backups)}
        if cmd == "del_map_backup":
            self.backups = [b for b in self.backups if b["id"] not in payload["ids"]]
            return {"state": 0, "msg": "Map backup has been successfully deleted."}
        if cmd == "map_recovery":            # the robot wipes plans and resets settings on a restore
            if payload["id"] in self.backup_maps:
                self.map = copy.deepcopy(self.backup_maps[payload["id"]])
                for a in self.map["areas"]:
                    a.update({k: copy.deepcopy(v) for k, v in EXTRA.items() if k not in a})
            self.plans = []
            self.params = {k: {"id": 0, "edge_circles": 2, "gap": 0.4, "contract_dis": 0.1, "ngz_expand_dis": 0.3,
                               "first_clean_params": {"blade_height": 0}, "double_clean_params": {"blade_height": 0}}
                           for k in self.params}
            return {"state": 0, "msg": "The map has been successfully restored."}
        if cmd == "save_plan":
            self.plans = [x for x in self.plans if x["id"] != payload["id"]] + [copy.deepcopy(payload)]
            return {"state": 0, "msg": "", "data": ""}
        if cmd == "save_mower_area_params":
            assert "clean_times_dis" not in payload and payload["mapping_module_type"] is None
            self.params[payload["id"]] = {k: v for k, v in payload.items() if k != "mapping_module_type"}
            return {"state": 0, "msg": "", "data": ""}
        return super()._apply(cmd, payload)

    async def read(self, cmd, payload):
        if cmd == "read_all_plan":
            return {"state": 0, "data": {"data": copy.deepcopy(self.plans)}}
        if cmd == "read_mower_area_params":
            return {"state": 0, "data": copy.deepcopy(self.params.get(payload["id"]))}
        if cmd == "read_all_clean_area":
            return {"state": 0, "data": {"data": [copy.deepcopy(a) for a in self.map["areas"]]}}
        raise AssertionError(cmd)


def _backups(robot, tmp_path):
    from app.backups import Backups
    store, w = writer(robot, tmp_path)
    return store, Backups(store, w, robot.read, robot.read_map, robot.read_areas)


def test_save_now_refuses_when_the_slots_are_full(tmp_path):
    robot = BackupRobot(sample_map(), slots_used=10)
    _, b = _backups(robot, tmp_path)
    with pytest.raises(mw.WriteRefused, match="slots"):
        asyncio.run(b.save())
    robot2 = BackupRobot(sample_map(), slots_used=3)
    _, b2 = _backups(robot2, tmp_path / "2")
    assert asyncio.run(b2.save())["backup"]["id"] == 999


def test_delete_is_checked_against_the_list(tmp_path):
    robot = BackupRobot(sample_map())
    store, b = _backups(robot, tmp_path)
    r = asyncio.run(b.delete(101))
    assert r["ok"] and all(x["id"] != 101 for x in robot.backups)
    with pytest.raises(mw.WriteRefused, match="no backup 555"):
        asyncio.run(b.delete(555))


def test_a_restore_keeps_the_map_locally_puts_settings_back_and_makes_the_plans_again(tmp_path):
    robot = BackupRobot(sample_map())
    store, b = _backups(robot, tmp_path)
    r = asyncio.run(b.restore(100, copy.deepcopy(robot.map), REF))
    assert r["ok"] and r["map_matches_backup"] and store.setting("map_last_restore")["ok"]
    assert not any(c == mw.BACKUP_CMD for c, _ in robot.sent)                        # no robot slot used
    assert store.latest_version("prerestore")["plans"][0]["name"] == "Mow All"
    assert all(x["ok"] for x in r["settings"]) and robot.params[1]["edge_circles"] == 4
    assert r["plans_lost"] == [] and r["plans_made"] == [{"id": 1, "name": "Mow All"}]
    assert ("save_plan", {"id": 1, "areaIds": [1, 11], "name": "Mow All", "enable_self_order": True}) in robot.sent
    assert robot.plans == [{"id": 1, "areaIds": [1, 11], "name": "Mow All", "enable_self_order": True}]


def test_a_restore_can_leave_the_plans_alone(tmp_path):
    robot = BackupRobot(sample_map())
    _, b = _backups(robot, tmp_path)
    r = asyncio.run(b.restore(100, copy.deepcopy(robot.map), REF, keep_plans=False))
    assert r["plans_made"] == [] and r["plans_lost"][0]["why"] == "not asked to"
    assert not any(c == "save_plan" for c, _ in robot.sent)


def test_a_restore_refuses_when_what_it_wipes_cannot_be_read(tmp_path):
    robot = BackupRobot(sample_map())
    robot.params[11] = {"id": 1, "edge_circles": 9}             # a reply for the wrong area
    _, b = _backups(robot, tmp_path)
    with pytest.raises(mw.WriteRefused, match="nothing was sent"):
        asyncio.run(b.restore(100, copy.deepcopy(robot.map), REF))
    assert not any(c == "map_recovery" for c, _ in robot.sent)


def test_a_restore_puts_settings_back_only_on_areas_the_backup_keeps(tmp_path):
    robot = BackupRobot(sample_map())
    older = copy.deepcopy(sample_map())
    older["areas"] = [a for a in older["areas"] if a["id"] == 1]           # the backup has no area 11
    robot.backup_maps[100] = older
    _, b = _backups(robot, tmp_path)
    r = asyncio.run(b.restore(100, copy.deepcopy(older), REF))
    assert r["ok"] and r["map_matches_backup"]
    assert [x["id"] for x in r["settings"]] == [1] and r["settings"][0]["ok"]
    assert [x["id"] for x in r["settings_not_applied"]] == [11]
    assert r["plans_made"] == [] and r["plans_lost"][0]["why"] == "the backup does not have all of its areas"
    assert not any(c == "save_plan" for c, _ in robot.sent)


def test_after_the_restore_nothing_raises_and_the_result_says_what_happened(tmp_path, monkeypatch):
    import app.backups as bk
    monkeypatch.setattr(bk, "POLL_S", 0)
    robot = BackupRobot(sample_map(), read_fails=50, read_fail_from=1)   # the map cannot be read after the restore
    _, b = _backups(robot, tmp_path)
    r = asyncio.run(b.restore(100, copy.deepcopy(robot.map), REF))
    assert r["ok"] and r["map_matches_backup"] is not True and r["problems"]
    assert r["settings"] == [] and r["plans_lost"][0]["name"] == "Mow All" and r["plans_made"] == []
    assert not any(c == "save_plan" for c, _ in robot.sent)                # the map was never confirmed


def test_the_robots_repeated_pathway_copies_in_a_backup_do_not_block_the_check(tmp_path):
    from app.backups import dedupe
    robot = BackupRobot(sample_map())
    backup = copy.deepcopy(robot.map)
    for p in list(backup.get("pathways") or []):           # as in backups 4322 / 4354: same shape, new id
        backup["pathways"].append(dict(copy.deepcopy(p), id=p["id"] + 700, start_id=0, end_id=0))
    assert len(dedupe(backup)["pathways"]) == len(robot.map["pathways"])
    _, b = _backups(robot, tmp_path)
    r = asyncio.run(b.restore(100, backup, REF))
    assert r["ok"] and r["map_matches_backup"] and all(x["ok"] for x in r["settings"])


def test_a_restore_accepts_an_area_on_its_defaults_and_leaves_it_there(tmp_path):
    robot = BackupRobot(sample_map())
    robot.params[11] = {**robot.params[11], "id": 0}                     # never saved: the robot answers with id 0
    _, b = _backups(robot, tmp_path)
    r = asyncio.run(b.restore(100, copy.deepcopy(robot.map), REF))
    assert r["ok"] and r["map_matches_backup"]
    eleven = next(x for x in r["settings"] if x["id"] == 11)
    assert eleven["ok"] and eleven.get("defaults")
    assert not any(c == "save_mower_area_params" and p["id"] == 11 for c, p in robot.sent)
