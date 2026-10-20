"""No-vision zones written in place, and a pathway's blades and transit path offset (app capture 2026-10-10)."""
import asyncio
import copy

import pytest

from app import mapstudio as ms, mapwrite as mw
from tests.test_mapstudio import R, REF, lonlat, pts, sample_map
from tests.test_mapwrite import FakeRobot, writer
from tests.test_pathway_params import P52, ParamRobot, _writer

# Vision Zone 1 as get_map returned it after an edit in the app (shape made up; the keys are the robot's).
ZONE = {"id": 749, "name": "Vision Zone 1", "ref": R, "enable": True, "by_pass_level": 1, "area_id": -1,
        "range": pts((4, 6), (6, 3), (1, 1), (0, 5))}


def with_zone():
    m = sample_map()
    m["novisionzones"] = [copy.deepcopy(ZONE)]
    return m


class NovisRobot(FakeRobot):
    """save_novisionzone as in the capture: the zone saved under the id it was sent with, echoed back in the reply."""

    def __init__(self, raw, new_id=None, keep_enable=False, quiet_add=False, **kw):
        super().__init__(raw, **kw)
        self.new_id, self.keep_enable, self.quiet_add = new_id, keep_enable, quiet_add

    def _apply(self, cmd, payload):
        if cmd == "del_novisionzone":
            if cmd in self.fail:
                self.fail.discard(cmd)
                return {"state": -1, "msg": "refused"}
            self.map["novisionzones"] = [z for z in self.map.get("novisionzones") or [] if z["id"] != payload["id"]]
            return {"state": 0, "msg": ""}
        if cmd != "save_novisionzone":
            return super()._apply(cmd, payload)
        if payload["id"] == -1:                          # a new zone: the robot picks the id (801 in the capture)
            rec = {**copy.deepcopy(payload), "id": 801 + len(self.map.get("novisionzones") or []), "area_id": -1}
            self.map.setdefault("novisionzones", []).append(rec)
            return {"state": 0, "msg": "", "data": "" if self.quiet_add else rec}
        if cmd in self.fail:
            self.fail.discard(cmd)
            return {"state": -1, "msg": "refused"}
        zones = self.map.setdefault("novisionzones", [])
        old = next((z for z in zones if z["id"] == payload["id"]), None)
        rec = {**copy.deepcopy(payload), "area_id": -1}
        if self.keep_enable and old is not None:
            rec["enable"] = old["enable"]               # a robot that ignores the switch
            self.keep_enable = False
        if self.new_id is not None:
            rec["id"] = self.new_id
            zones.append(rec)
        else:
            zones[zones.index(old)] = rec
        return {"state": 0, "msg": "", "data": rec}


def off(m):
    d, _ = ms.apply_edit(m, {"op": "enable", "kind": "novisionzones", "id": 749, "enable": False}, REF)
    return d


def test_switching_a_zone_off_is_one_save_shaped_like_the_apps(tmp_path):
    base = with_zone()
    p = mw.plan(base, off(base), REF)
    assert [(s["op"], s["cmd"]) for s in p["steps"]] == [("update", "save_novisionzone")] and not p["blocked"]
    pay = p["steps"][0]["payload"]
    assert set(pay) == {"range", "ref", "name", "id", "enable", "by_pass_level"}          # as the app sent it
    assert pay["id"] == 749 and pay["enable"] is False and pay["by_pass_level"] == 1     # bypass kept as it is
    robot = NovisRobot(base)
    store, w = writer(robot, tmp_path)
    r = asyncio.run(w.write(base, off(base), REF))
    assert r["ok"] and r["verified"], r
    assert [c for c, _ in robot.sent] == ["save_novisionzone", "upload_cloud_map_backup"]
    assert ms.find(robot.map, "novisionzones", 749)["enable"] is False
    assert "latitude" not in str(store.control_log())          # the audit keeps no location


def test_reshape_and_rename_are_sent_in_place(tmp_path):
    base = with_zone()
    d, _ = ms.apply_edit(base, {"op": "rename", "kind": "novisionzones", "id": 749, "name": "porch"}, REF)
    d, _ = ms.apply_edit(d, {"op": "shape", "kind": "novisionzones", "id": 749,
                             "coords": [lonlat(4, 6), lonlat(7, 3), lonlat(1, 1), lonlat(0, 5)]}, REF)
    step = mw.plan(base, d, REF)["steps"][0]
    assert step["big"] and "new ground" in step["label"]              # wider: more ground without the cameras
    robot = NovisRobot(base)
    _, w = writer(robot, tmp_path)
    with pytest.raises(mw.WriteRefused, match="explicit OK"):
        asyncio.run(w.write(base, d, REF))
    r = asyncio.run(w.write(base, d, REF, allow_big=True))
    z = ms.find(robot.map, "novisionzones", 749)
    assert r["ok"] and r["verified"] and z["name"] == "porch" and len(robot.map["novisionzones"]) == 1, r


def test_shrinking_or_switching_a_zone_off_needs_no_extra_ok_but_switching_on_does():
    base = with_zone()
    d, _ = ms.apply_edit(base, {"op": "shape", "kind": "novisionzones", "id": 749,
                                "coords": [lonlat(4, 6), lonlat(5, 3.5), lonlat(1, 1), lonlat(0, 5)]}, REF)
    assert not mw.plan(base, d, REF)["steps"][0]["big"]
    assert not mw.plan(base, off(base), REF)["steps"][0]["big"]
    was_off = off(base)
    on, _ = ms.apply_edit(was_off, {"op": "enable", "kind": "novisionzones", "id": 749, "enable": True}, REF)
    assert mw.plan(was_off, on, REF)["steps"][0]["big"]
    moved, _ = ms.apply_edit(base, {"op": "shape", "kind": "novisionzones", "id": 749,                # 3 m east
                                    "coords": [lonlat(1, 6), lonlat(3, 3), lonlat(-2, 1), lonlat(-3, 5)]}, REF)
    assert mw.plan(base, moved, REF)["steps"][0]["big"]


@pytest.mark.parametrize("missing", ["enable", "by_pass_level"])
def test_a_zone_record_without_its_switch_or_bypass_is_not_sent(missing):
    base = with_zone()
    del base["novisionzones"][0][missing]
    d, _ = ms.apply_edit(base, {"op": "rename", "kind": "novisionzones", "id": 749, "name": "porch"}, REF)
    p = mw.plan(base, d, REF)
    assert not p["steps"] and "lacks" in p["blocked"][0]["why"]


def test_fields_that_are_not_sent_are_not_dropped_silently():
    base = with_zone()
    d = copy.deepcopy(base)
    d["novisionzones"][0]["area_id"] = 11
    p = mw.plan(base, d, REF)
    assert not p["steps"] and "area_id" in p["blocked"][0]["why"]


def test_a_failed_undo_of_a_zone_is_not_reported_as_ok(tmp_path):
    base = with_zone()
    d, _ = ms.apply_edit(off(base), {"op": "add", "kind": "nogozones", "name": "tree",
                                     "coords": [lonlat(10, 10), lonlat(12, 10), lonlat(12, 12)]}, REF)
    robot = NovisRobot(base, fail={"save_nogozone"})

    def refuse_undo(cmd, payload, _apply=robot._apply):
        if cmd == "save_novisionzone" and payload["enable"] is True:      # the undo is refused
            return {"state": -1, "msg": "busy"}
        return _apply(cmd, payload)
    robot._apply = refuse_undo
    _, w = writer(robot, tmp_path)
    r = asyncio.run(w.write(base, d, REF))
    assert not r["ok"] and r["rollback_ok"] is False
    assert any("enable" in x for x in r["after_rollback"]), r


def _add_and_delete(base):
    d, _ = ms.apply_edit(base, {"op": "add", "kind": "novisionzones", "name": "bed",
                                "coords": [lonlat(10, 10), lonlat(12, 10), lonlat(12, 12)]}, REF)
    d, _ = ms.apply_edit(d, {"op": "delete", "kind": "novisionzones", "id": 749}, REF)
    return d


def test_adding_and_deleting_a_zone_are_sent_like_the_app():
    base = with_zone()
    d = _add_and_delete(base)
    p = mw.plan(base, d, REF)
    assert not p["blocked"] and [s["cmd"] for s in p["steps"]] == ["save_novisionzone", "del_novisionzone"]
    add, delete = p["steps"]
    assert add["payload"]["id"] == -1 and add["payload"]["enable"] is True and add["payload"]["by_pass_level"] == 0
    assert set(add["payload"]) == {"range", "ref", "name", "id", "enable", "by_pass_level"}      # captures 221-235
    assert add["big"] and delete["payload"] == {"id": 749}


@pytest.mark.parametrize("quiet", [False, True])
def test_a_new_zone_is_written_and_verified(tmp_path, quiet):
    base = with_zone()
    robot = NovisRobot(base, quiet_add=quiet)          # quiet: the reply does not name the new zone
    _, w = writer(robot, tmp_path)
    d = _add_and_delete(base)
    with pytest.raises(mw.WriteRefused, match="explicit OK"):
        asyncio.run(w.write(base, d, REF))
    r = asyncio.run(w.write(base, d, REF, allow_big=True))
    assert r["ok"] and r["verified"], r
    assert [z["name"] for z in robot.map["novisionzones"]] == ["bed"]


def test_a_failed_add_is_undone_with_the_zones_own_delete(tmp_path):
    base = with_zone()
    robot = NovisRobot(base, fail={"del_novisionzone"})
    _, w = writer(robot, tmp_path)
    r = asyncio.run(w.write(base, _add_and_delete(base), REF, allow_big=True))
    assert not r["ok"] and ("del_novisionzone", {"id": 802}) in robot.sent


def test_a_robot_that_ignores_the_switch_is_put_back(tmp_path):
    base = with_zone()
    robot = NovisRobot(base, keep_enable=True)
    _, w = writer(robot, tmp_path)
    r = asyncio.run(w.write(base, off(base), REF))
    assert not r["ok"] and any("saved on" in x for x in r["problems"]), r
    assert r["rollback_ok"] and robot.sent[-1][0] == "save_novisionzone" and robot.sent[-1][1]["enable"] is True
    assert "upload_cloud_map_backup" not in [c for c, _ in robot.sent]


def test_a_new_zone_instead_of_a_change_stops_and_says_so(tmp_path):
    base = with_zone()
    robot = NovisRobot(base, new_id=777)
    _, w = writer(robot, tmp_path)
    r = asyncio.run(w.write(base, off(base), REF))
    assert not r["ok"] and "created a new no-vision zone 777" in r["error"]
    assert any("NOT undone" in n for n in r["rollback"])
    assert [c for c, _ in robot.sent] == ["save_novisionzone"]                     # nothing more was sent


def test_a_zone_changed_in_the_app_since_studio_read_it_is_not_overwritten(tmp_path):
    base = with_zone()
    robot = NovisRobot(base)
    ms.find(robot.map, "novisionzones", 749)["by_pass_level"] = 0                   # changed in the app meanwhile
    _, w = writer(robot, tmp_path)
    with pytest.raises(mw.WriteRefused, match="changed on the robot"):
        asyncio.run(w.write(base, off(base), REF))
    assert robot.sent == []


def test_obstacle_bypass_is_not_changed_from_the_panel():
    base = with_zone()
    d = copy.deepcopy(base)
    d["novisionzones"][0]["by_pass_level"] = 0
    p = mw.plan(base, d, REF)
    assert not p["steps"] and "in the app" in p["blocked"][0]["why"]


# -- pathway: blades and the transit path offset -------------------------------------------------------------
def test_blades_and_the_offset_are_sent_with_everything_else_kept():
    p = mw.pathway_params_payload(P52, {"en_blade": True, "offset_enable": True, "offset_range.1": 0.7, "offset_step": 0.2}, 52)
    assert p["en_blade"] is True and p["offset_enable"] is True
    assert p["offset_range"] == [-0.5, 0.7] and p["offset_step"] == 0.2           # the app's values on Pathway 9
    assert P52["offset_range"] == [-0.5, 0.5]                                       # the read itself is untouched
    assert p["sensor_switch"] == 11 and p["plan_speed"] == 0.5


@pytest.mark.parametrize("changes", [{"offset_range.1": 1.1}, {"offset_range.0": 0.1}, {"offset_range.0": -1.1},
                                     {"offset_step": 0.04}, {"offset_range.2": 0.1}, {"offset_range": [0, 1]},
                                     {"en_blade": "yes"}])
def test_offsets_outside_what_the_app_allows_are_refused(changes):
    with pytest.raises(ValueError):
        mw.pathway_params_payload(P52, changes, 52)


def test_an_offset_change_is_written_and_read_back(tmp_path):
    robot = ParamRobot(P52)
    _, w = _writer(robot, tmp_path)
    r = asyncio.run(mw.write_pathway_params(w, 52, {"offset_range.1": 0.6, "en_blade": True}))
    assert r["ok"] and robot.params[52]["offset_range"] == [-0.5, 0.6] and robot.params[52]["en_blade"] is True, r
    assert mw.get_path(r["before"], "offset_range.1") == 0.5


def test_slider_inches_land_on_the_apps_tenth_of_a_metre():
    inch = 0.0254
    p = mw.pathway_params_payload(P52, {"offset_range.0": round(-27.6 * inch, 4), "offset_range.1": round(19.726 * inch, 4),
                                        "offset_step": round(7.837 * inch, 4)}, 52)
    assert p["offset_range"] == [-0.7, 0.5] and p["offset_step"] == 0.2
    with pytest.raises(ValueError):
        mw.pathway_params_payload(P52, {"offset_range.0": 0.0, "offset_range.1": 0.003}, 52)   # snaps to [0, 0]
    assert mw.pathway_params_payload(P52, {"offset_step": 0.15}, 52)["offset_step"] == 0.15     # off-grid: kept as is
    assert mw.pathway_params_payload(P52, {"offset_range.0": -0.004}, 52)["offset_range"][0] == 0.0
    assert str(mw.pathway_params_payload(P52, {"offset_range.0": -0.004}, 52)["offset_range"][0]) == "0.0"   # not -0.0


def test_the_offset_goes_as_wide_as_the_app_allows_and_no_further():
    # The app's Range Offset slider runs -39.4 to 39.4 in (1.0 m each side; the app's screenshot, 2026-10-11).
    p = mw.pathway_params_payload(P52, {"offset_range.0": -1.0, "offset_range.1": 1.0}, 52)
    assert p["offset_range"] == [-1.0, 1.0]
    with pytest.raises(ValueError, match="Range offset"):
        mw.pathway_params_payload(P52, {"offset_range.1": 1.1}, 52)


def test_the_whole_offset_is_checked_whenever_any_part_of_it_changes():
    wide = {**P52, "offset_range": [-1.5, 1.5], "offset_step": 0.9}
    with pytest.raises(ValueError, match="Range offset"):
        mw.pathway_params_payload(wide, {"offset_enable": True}, 52)                   # switching on a wide range
    with pytest.raises(ValueError, match="Range offset"):
        mw.pathway_params_payload({**P52, "offset_range": [-0.5, 2.0]}, {"offset_range.0": -0.7}, 52)
    with pytest.raises(ValueError, match="not a pair"):
        mw.pathway_params_payload({**P52, "offset_range": [0.5]}, {"offset_step": 0.2}, 52)
    assert mw.pathway_params_payload(wide, {"plan_speed": 0.6}, 52)["offset_range"] == [-1.5, 1.5]   # untouched


def axis_zone(w, h, x0=10.0, y0=10.0):
    return [lonlat(x0, y0), lonlat(x0 + w, y0), lonlat(x0 + w, y0 + h), lonlat(x0, y0 + h)]


@pytest.mark.parametrize("w,h", [(1.124, 10.124), (2.374, 30.124), (0.12, 20)])
def test_axis_aligned_zones_switched_off_or_renamed_are_not_wider(w, h):
    base = with_zone()
    base, _ = ms.apply_edit(base, {"op": "shape", "kind": "novisionzones", "id": 749, "coords": axis_zone(w, h)}, REF)
    assert not mw.plan(base, off(base), REF)["steps"][0]["big"]
    d, _ = ms.apply_edit(base, {"op": "rename", "kind": "novisionzones", "id": 749, "name": "x"}, REF)
    assert not mw.plan(base, d, REF)["steps"][0]["big"]


def test_new_ground_is_counted_exactly_enough_both_ways():
    base = with_zone()
    base, _ = ms.apply_edit(base, {"op": "shape", "kind": "novisionzones", "id": 749, "coords": axis_zone(1.5, 10)}, REF)
    shrink, _ = ms.apply_edit(base, {"op": "shape", "kind": "novisionzones", "id": 749, "coords": axis_zone(1.124, 10)}, REF)
    assert not mw.plan(base, shrink, REF)["steps"][0]["big"]
    wide = with_zone()
    wide, _ = ms.apply_edit(wide, {"op": "shape", "kind": "novisionzones", "id": 749, "coords": axis_zone(1.876, 20)}, REF)
    grown, _ = ms.apply_edit(wide, {"op": "shape", "kind": "novisionzones", "id": 749, "coords": axis_zone(1.996, 20)}, REF)
    assert mw.plan(wide, grown, REF)["steps"][0]["big"]                              # 2.4 m² of new ground
