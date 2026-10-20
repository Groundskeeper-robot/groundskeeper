"""An area's mowing settings (app/areasettings.py): what a save carries, what is checked, and what happens when the
robot keeps something else. The fake robot stores settings per area, answers reads with id 0 for an area that never
had its settings saved (as this robot does), and re-links pathways on every area save."""
import asyncio
import copy
import tempfile

import pytest

from app import areasettings as aset, mapwrite as mw
from tests.test_area_writes import AreaRobot, writer
from tests.test_mapstudio import REF, sample_map

READ = {"auto_step_angle": 90, "avoidance_level": 1, "buffer_dis": 0.0, "clean_times": 1, "clean_times_dis": 0.0001,
        "contract_dis": 0.0254, "current_blade_height": 0, "current_index": 0,
        "double_clean_params": {"blade_height": 0, "blade_speed": 80, "land_push_pod_place": 50, "plan_speed": 0.5,
                                "route_angle": 90, "route_dis": 0.0, "turn_type": 1},
        "edge_area_first": True, "edge_bypass_sw": True, "edge_circles": 4, "edge_direction_mode": 0, "en_trimmer": True,
        "enable_clean_times_dis": False, "enable_explore": False,
        "first_clean_params": {"blade_height": 25, "blade_speed": 80, "land_push_pod_place": 50, "plan_speed": 0.7,
                               "route_angle": 30, "route_dis": 0.0, "turn_type": 1},
        "gap": 0.3, "id": 1, "m1pro_sensor_switch": 139, "move2area_max_offset": 2.0, "move2area_offset_count": {},
        "move2area_offset_enable": True, "move2area_step_offset": 0.1, "mower_ngz_edge_sw": True,
        "mower_target_height_count": 5, "ngz_expand_dis": 0.102, "parallel_angle_max": 90, "parallel_angle_min": -90,
        "parallel_offset_mode": 2, "route_order": 0, "sensor_switch": 8, "side_enable": True}


class SettingsRobot(AreaRobot):
    def __init__(self, raw, defaults=False, ignore=(), extra_key=None, **kw):
        super().__init__(raw, **kw)
        self.params = {} if defaults else {1: copy.deepcopy(READ)}
        self.ignore, self.extra_key = set(ignore), extra_key

    def _apply(self, cmd, payload):
        if cmd == aset.SAVE:
            assert payload["mapping_module_type"] is None and "clean_times_dis" not in payload
            rec = copy.deepcopy(payload)
            rec.pop("mapping_module_type")
            old = self.params.get(rec["id"], {**READ, "id": 0})
            for k in self.ignore:                    # the robot keeps its old value for these
                aset._put(rec, k, aset.get(old, k))
            self.params[rec["id"]] = {**rec, "clean_times_dis": 0.0001, "current_blade_height": 0, "move2area_offset_count": {}}
            return {"state": 0, "msg": "", "data": ""}
        return super()._apply(cmd, payload)

    async def read(self, aid):
        out = copy.deepcopy(self.params.get(aid) or {**READ, "id": 0})
        if self.extra_key:
            out[self.extra_key] = 1
        return out


def run(robot, tmp_path, changes, **kw):
    store, w = writer(robot, tmp_path)
    return store, asyncio.run(aset.write(w, 1, changes, robot.read, REF, **kw))


def test_a_change_goes_out_as_the_full_record_then_the_unchanged_area_as_the_app_does(tmp_path):
    robot = SettingsRobot(sample_map())
    store, r = run(robot, tmp_path, {"edge_circles": 3, "first_clean_params.plan_speed": 0.5, "contract_dis": 0.0508})
    assert r["ok"], r
    cmds = [c for c, _ in robot.sent]
    assert cmds == [aset.SAVE, mw.AREA_SAVE, "upload_cloud_map_backup"]
    save = robot.sent[0][1]
    assert save["id"] == 1 and save["edge_circles"] == 3 and isinstance(save["edge_circles"], int)
    assert save["first_clean_params"]["plan_speed"] == 0.5 and save["first_clean_params"]["blade_height"] == 25
    assert save["contract_dis"] == 0.0508 and save["gap"] == 0.3                  # untouched fields go back as read
    assert set(robot.sent[1][1]) <= set(mw.AREA_KEYS) and robot.sent[1][1]["id"] == 1
    kept = sorted(store.version(v["id"])["json"]["settings"]["edge_circles"] for v in store.versions_like("area_params:1"))
    assert kept == [3, 4]                                                          # before and after, both kept


def test_an_area_on_its_defaults_gets_its_own_id(tmp_path):
    robot = SettingsRobot(sample_map(), defaults=True)
    _, r = run(robot, tmp_path, {"edge_circles": 3})
    assert r["ok"] and r["before"]["id"] == 0 and r["after"]["id"] == 1


def test_read_only_and_out_of_range_changes_are_refused_before_anything_is_sent(tmp_path):
    for bad in ({"first_clean_params.blade_height": 30}, {"gap": 0.2}, {"edge_circles": 2.5},
                {"first_clean_params.plan_speed": 0.9}, {"en_trimmer": 1}, {"contract_dis": -0.1}):
        robot = SettingsRobot(sample_map())
        with pytest.raises(ValueError):
            run(robot, tmp_path, bad)
        assert robot.sent == [], bad


def test_a_read_with_fields_this_panel_does_not_know_is_not_saved_back(tmp_path):
    robot = SettingsRobot(sample_map(), extra_key="new_firmware_switch")
    with pytest.raises(ValueError, match="does not know"):
        run(robot, tmp_path, {"edge_circles": 3})
    assert robot.sent == []


def test_a_value_the_robot_did_not_keep_puts_the_old_settings_back(tmp_path):
    robot = SettingsRobot(sample_map(), ignore={"edge_circles"})
    store, r = run(robot, tmp_path, {"edge_circles": 2, "en_trimmer": False})
    assert not r["ok"] and r["rollback_ok"] and "edge_circles" in r["error"], r
    assert robot.params[1]["en_trimmer"] is True                                   # the trimmer change went back too
    assert [e for e in store.events(5) if e["kind"] == "area_settings_failed"]


def test_nothing_is_sent_when_nothing_changes(tmp_path):
    robot = SettingsRobot(sample_map())
    _, r = run(robot, tmp_path, {"edge_circles": 4})
    assert r["ok"] and r["unchanged"] and robot.sent == []


def test_the_gate_is_checked_inside_the_lock(tmp_path):
    robot = SettingsRobot(sample_map())
    with pytest.raises(mw.WriteRefused, match="switched off"):
        run(robot, tmp_path, {"edge_circles": 3}, check=lambda: "map writing is switched off")
    assert robot.sent == []


def test_every_field_is_in_the_record_and_every_editable_one_has_a_range_or_choices():
    for f in aset.AREA_FIELDS:
        if f["control"] == "pending":
            assert f["key"] is None
            continue
        assert aset.get(READ, f["key"]) is not None, f["key"]
        if f.get("editable") and f["type"] != "bool":
            assert f.get("strict") or f["min"] < f["max"], f["key"]


def test_speed_levels_are_inside_the_editable_range_and_the_sections_follow_the_app():
    speed = next(f for f in aset.AREA_FIELDS if f["key"] == "first_clean_params.plan_speed")
    assert all(speed["min"] <= v <= speed["max"] for v, _ in speed["levels"])
    assert [n for _, n in speed["levels"]] == ["Slowest", "Slow", "Medium", "Fast", "Fastest"]
    order = ["Area settings", "Advanced settings", "Not in the app"]
    secs = [order.index(f["section"]) for f in aset.AREA_FIELDS]
    assert secs == sorted(secs)


def test_a_choice_takes_only_the_apps_values():
    robot = SettingsRobot(sample_map())
    with pytest.raises(ValueError, match="one of"):
        run(robot, tempfile.mkdtemp(), {"gap": 0.35})
    _, r = run(robot, tempfile.mkdtemp(), {"gap": 0.4, "move2area_step_offset": 0.2})
    assert r["ok"] and robot.params[1]["gap"] == 0.4 and robot.params[1]["move2area_step_offset"] == 0.2


def test_a_recording_lists_what_each_app_save_changed():
    before = {11: {**READ, "id": 11}}
    save1 = aset.params_payload({**READ, "id": 11}, 11)
    save1["first_clean_params"]["turn_type"] = 0
    save2 = copy.deepcopy(save1)
    save2["gap"] = 0.5
    entries = [{"ts": 1, "key": "app/save_mower_area_params", "data": save1}, {"ts": 2, "key": "app/save_clean_area", "data": {}},
               {"ts": 3, "key": "app/save_mower_area_params", "data": save2}]
    out = aset.settings_changes(entries, before, {11: "All Yard"})
    assert [c["key"] for c in out[0]["changes"]] == ["first_clean_params.turn_type"]
    assert out[0]["changes"][0]["from"] == 1 and out[0]["changes"][0]["to"] == 0 and out[0]["name"] == "All Yard"
    assert [(c["key"], c["to"]) for c in out[1]["changes"]] == [("gap", 0.5)]


def test_a_speed_between_the_levels_and_a_blade_speed_are_accepted(tmp_path):
    robot = SettingsRobot(sample_map())
    _, r = run(robot, tmp_path, {"first_clean_params.plan_speed": 0.55, "first_clean_params.blade_speed": 90})
    assert r["ok"] and robot.params[1]["first_clean_params"]["plan_speed"] == 0.55
    assert robot.params[1]["first_clean_params"]["blade_speed"] == 90


def test_the_preview_payload_is_the_apps_and_the_route_comes_back_as_a_line():
    record = {"algorithm_type": 0, "id": 11, "leaf_piles": [], "push_snow_dir": 9999.0, "slope_degrees": 9999.0,
              "snowPiles": [], "range": [{"x": 0, "y": 0, "phi": 0}], "ref": {"latitude": REF[0], "longitude": REF[1]},
              "area": 400.0, "name": "All Yard"}
    p = aset.preview_payload(record)
    assert set(p) == set(aset.PREVIEW_KEYS) and p["id"] == 11
    assert aset.preview_payload(record, [{"x": 1, "y": 1, "phi": 0}])["range"] == [{"x": 1, "y": 1, "phi": 0}]
    out = aset.route_summary({"path": [{"x": 0, "y": 0, "phi": 0}, {"x": 3, "y": 4, "phi": 0}, {"x": 3, "y": 10, "phi": 0}],
                              "total_time": 123.4}, record["ref"])
    assert out["length_m"] == 11.0 and out["points"] == 3 and out["total_time_s"] == 123.4
    assert out["route"]["geometry"]["type"] == "LineString" and len(out["route"]["geometry"]["coordinates"]) == 3
