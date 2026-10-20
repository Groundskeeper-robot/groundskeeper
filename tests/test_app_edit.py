"""Record an app edit (Map Studio → More), 2026-10-07."""
import asyncio
import copy
from types import SimpleNamespace

from app.recorder import Recorder
from app.store import Store
from tests.test_mapstudio import REF, sample_map


def _rec(tmp_path, maps):
    r = Recorder.__new__(Recorder)
    r.store, r.cfg, r.gps_ref, r.loop = Store(str(tmp_path)), SimpleNamespace(data_dir=str(tmp_path)), REF, None
    r.session, r._sent = None, {"get_device_msg": 1000.0}
    seq = iter(maps)

    async def reload_map():
        return copy.deepcopy(next(seq))
    r.reload_map = reload_map
    return r


def test_a_recording_keeps_the_apps_commands_and_the_robots_answers_and_diffs_the_map(tmp_path):
    before = sample_map()
    after = copy.deepcopy(before)
    after["areas"][0]["range"].append({"x": 3.0, "y": 9.0, "phi": 0})
    r = _rec(tmp_path, [before, after])
    asyncio.run(r.start_session(30))
    t0 = r.session["started"]
    sn = "snowbot/SN"
    r._session_record("app", "set_auto_mapping_state", {"state": 1, "dir": 1}, t0 + 1)
    r._session_record("app", "cmd_vel", {"vel": 0.3}, t0 + 2)                           # counted only
    r._session_record("device", "DeviceMSG", {"x": 1}, t0 + 2)                          # counted only
    r._session_record("device", "mapping_path", {"pts": [[0, 0], [1, 1]]}, t0 + 3)       # unknown topic: kept
    r._session_record("app", "save_clean_area", {"id": 1, "range": []}, t0 + 4)
    r._session_record("device", "data_feedback", {"topic": "save_clean_area", "state": 0}, t0 + 5)
    r._session_record("device", "data_feedback", {"topic": "get_device_msg", "data": {}}, 1003.0)  # our own read
    v = asyncio.run(r.stop_session())
    assert v["kept"] == {"app/set_auto_mapping_state": 1, "device/mapping_path": 1, "app/save_clean_area": 1,
                         "reply/save_clean_area": 1}
    assert v["counted"]["app/cmd_vel"] == 1 and v["counted"]["device/DeviceMSG"] == 1
    assert v["map_changes"] and v["map_changes"][0]["kind"] == "areas" and v["map_changes"][0]["what"] == "changed"
    assert (tmp_path / "captures").exists() and v["file"].endswith(".json")
