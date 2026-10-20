"""Other clients' secrets are never kept (the app's change_to_wifi carried a Wi-Fi password, 2026-10-10)."""
import json

from app.codec import MASK, redact, redact_text
from app.recorder import Recorder
from app.store import Store

WIFI = {"wifi_name": "HomeNet", "password": "hunter2-not-real"}


def test_secret_fields_are_masked_and_settings_are_not():
    assert redact(WIFI) == {"wifi_name": "HomeNet", "password": MASK}
    nested = {"email": "a@b.c", "data": [{"access_token": "t0k"}], "by_pass_level": 1, "enable_sam_bypass": True}
    assert redact(nested) == {"email": MASK, "data": [{"access_token": MASK}], "by_pass_level": 1,
                              "enable_sam_bypass": True}


def test_cut_short_text_is_masked_too():
    text = json.dumps(WIFI)
    assert "hunter2" not in redact_text(text) and "HomeNet" in redact_text(text)
    assert "hunt" not in redact_text(text[:-6])                     # the sample ends inside the password
    assert redact_text('{"by_pass_level": 1, "name": "x"}') == '{"by_pass_level": 1, "name": "x"}'


def test_a_foreign_command_is_logged_without_its_password(tmp_path):
    st = Store(str(tmp_path))
    r = Recorder.__new__(Recorder)
    r.store, r.session, r._sent, r.foreign_cmd_at, r._wifi_foreign_at, r._robot_at = st, None, {}, 0.0, 0.0, 0.0
    r._handle("snowbot/SN/app/change_to_wifi", WIFI, 1000.0)
    stored = json.dumps([st.events(10), st.captures(10), st.capture(st.captures(1)[0]["id"]),
                         [dict(x) for x in st.db.execute("SELECT * FROM commands_seen")]])
    assert "hunter2" not in stored and "HomeNet" in stored


def test_copies_stored_before_are_masked_on_start(tmp_path):
    st = Store(str(tmp_path))
    text = json.dumps(WIFI)
    st.event("new_command_seen", f"another client sent change_to_wifi {text}")
    st.command_seen("change_to_wifi", 1000.0, text[:30])                 # cut inside the password
    st.add_capture("change_to_wifi", 1000.0, text)
    again = Store(str(tmp_path))
    stored = json.dumps([again.events(10), again.capture(again.captures(1)[0]["id"]),
                         [dict(x) for x in again.db.execute("SELECT * FROM commands_seen")]])
    assert "hunter2" not in stored and "hunt" not in stored and "HomeNet" in stored
    assert again.redact_stored() == 0                                    # nothing left to do


def test_a_reply_kept_as_a_version_is_masked_on_start(tmp_path):
    st = Store(str(tmp_path))
    st.save_version("reply:get_saved_wifi", {"state": 0, "data": {"list": [{"ssid": "HomeNet", "password": "hunter2"}]}})
    st.save_version("map", {"areas": [{"name": "password garden"}]})          # not a reply: left alone
    again = Store(str(tmp_path))
    assert "hunter2" not in json.dumps(again.latest_version("reply:get_saved_wifi"))
    assert again.latest_version("reply:get_saved_wifi")["data"]["list"][0]["ssid"] == "HomeNet"
    assert again.redact_stored() == 0


def test_a_cleaned_reply_keeps_a_matching_hash(tmp_path):
    st = Store(str(tmp_path))
    st.save_version("reply:get_saved_wifi", {"state": 0, "data": {"password": "hunter2", "ssid": "HomeNet"}})
    again = Store(str(tmp_path))                                           # cleaned on start
    from app.codec import redact
    # the recorder now keeps the masked form: the same reply again is not a new version
    assert again.save_version("reply:get_saved_wifi", redact({"state": 0, "data": {"password": "x", "ssid": "HomeNet"}})) is False
