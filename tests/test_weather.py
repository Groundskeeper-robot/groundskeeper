"""Weather inputs for Lawn conditions: Tempest and/or Home Assistant sensors, per input; one station out of several;
units for the words; soil moisture as a fifth rule. No network: HA states are faked."""
import asyncio
import time

import pytest

from app import weather
from app.conditions import ConditionsConfig, evaluate
from app.tempest import TempestListener


def run(coro):
    return asyncio.run(coro)


def cfg(**over):
    raw = {"ha": {"rain": "sensor.rain_rate", "temp": "sensor.out_temp", "humidity": "sensor.out_rh",
                  "soil": ["sensor.soil_a", "sensor.soil_b"]}}
    raw.update(over)
    return weather.config(raw)


def test_settings_are_validated():
    assert weather.validate({})["use"] == {"rain": "auto", "temp": "auto", "humidity": "auto"}
    for bad in ({"units": "kelvin"}, {"use": {"rain": "both"}}, {"ha": {"rain": "not an entity"}},
                {"ha": {"soil": ["sensor.a"] * 9}}, {"station": "../x"}, []):
        with pytest.raises(ValueError):
            weather.validate(bad)


def test_either_takes_the_tempest_while_it_reports():
    c = cfg()
    assert weather.source_for(c, "rain", 30) == "tempest"
    assert weather.source_for(c, "rain", 3600) == "ha"           # the Tempest went quiet
    assert weather.source_for(c, "rain", None) == "ha"
    assert weather.source_for(weather.config({}), "rain", None) == "tempest"   # nothing in HA: the Tempest or nothing
    assert weather.source_for(cfg(use={"rain": "ha"}), "rain", 30) == "ha"
    assert weather.source_for(weather.config({"use": {"rain": "ha"}}), "rain", 30) is None


def test_a_tempest_row_loses_what_ha_feeds():
    row = {"ts": 1, "rain_mm": 0.4, "precip_type": 1, "temp_c": 20, "rh": 50, "dew_c": 9, "wind_avg": 2}
    out = weather.filter_tempest(row, cfg(use={"rain": "ha", "humidity": "ha"}), 10)
    assert "rain_mm" not in out and "precip_type" not in out and "rh" not in out and "dew_c" not in out
    assert out["temp_c"] == 20 and out["wind_avg"] == 2


def states(values=None, **kw):
    values = values if values is not None else kw                  # pass a dict to change readings between calls

    async def get(entity):
        v = values.get(entity.split(".")[1])
        if isinstance(v, Exception):
            raise v
        return v
    return get


def st(state, unit=None):
    return {"state": str(state), "attributes": {"unit_of_measurement": unit} if unit else {}}


def test_ha_row_converts_units_and_computes_dew():
    ha = weather.HAWeather(states(out_temp=st(68, "°F"), out_rh=st(50, "%"), rain_rate=st(0.5, "in/h"),
                                  soil_a=st(31, "%"), soil_b=st(44, "%")))
    row = run(ha.read(cfg(), None, now=1000.0))
    assert row["temp_c"] == pytest.approx(20.0) and row["rh"] == 50 and row["dew_c"] == pytest.approx(9.27, abs=0.05)
    assert row["rain_mm"] == pytest.approx(0.5 * 25.4 * 60 / 3600, abs=0.01) and row["precip_type"] == 1
    assert row["soil_pct"] == 44                                  # the wettest sensor
    assert ha.status()["soil"] == [{"entity": "sensor.soil_a", "pct": 31.0}, {"entity": "sensor.soil_b", "pct": 44.0}]


def test_a_running_total_counts_its_increase_and_not_a_reset():
    vals = {"rain_rate": st(10.0, "mm")}
    ha = weather.HAWeather(states(vals))
    c = weather.config({"ha": {"rain": "sensor.rain_rate"}})
    assert run(ha.read(c, None, now=0))["rain_mm"] == 0.0
    vals["rain_rate"] = st(10.6, "mm")
    assert run(ha.read(c, None, now=60))["rain_mm"] == pytest.approx(0.6)
    vals["rain_rate"] = st(0.0, "mm")                              # midnight reset
    assert run(ha.read(c, None, now=120))["rain_mm"] == 0.0


def test_an_on_off_rain_sensor_and_unavailable_entities():
    ha = weather.HAWeather(states(rain_rate={"state": "on"}, out_temp={"state": "unavailable"},
                                  out_rh=RuntimeError("timeout")))
    row = run(ha.read(cfg(ha={"rain": "sensor.rain_rate", "temp": "sensor.out_temp", "humidity": "sensor.out_rh"}), None, 0))
    assert row["precip_type"] == 1 and row["rain_mm"] == pytest.approx(1.0 * 60 / 3600, abs=1e-3)   # on = light rain
    assert row["temp_c"] is None
    assert ha.status()["errors"] == {"sensor.out_temp": "unavailable", "sensor.out_rh": "timeout"}


def test_soil_moisture_holds_the_lawn_wet_or_damp():
    now = time.time()
    c = ConditionsConfig()
    def state(pct, age_h=0.1):
        return evaluate(now, [{"ts": now - age_h * 3600, "soil_pct": pct}], None, None, None, None, c)
    assert state(50)["state"] == "Wet" and state(38)["state"] == "Damp" and state(20)["state"] == "Dry"
    assert state(50, age_h=5)["state"] == "Dry"                   # an old reading does not count
    rule = next(r for r in state(38)["rules"] if r["rule"] == "Soil moisture")
    assert "38 %" in rule["detail"]


def test_us_units_in_the_words():
    now = time.time()
    rows = [{"ts": now - 60, "rain_mm": 2.54, "temp_c": 10, "dew_c": 9}]
    out = evaluate(now, rows, None, None, None, None, ConditionsConfig(), "us")
    text = " ".join(r["detail"] for r in out["rules"])
    assert " in " in text and "mm" not in text


def test_one_station_out_of_two():
    seen = []
    t = TempestListener(50222, seen.append, lambda *a: None, station="ST-2")
    obs = lambda serial: {"type": "obs_st", "serial_number": serial, "obs": [[int(time.time()), 0, 1, 2, 3, 3, 1000, 20, 50]]}  # noqa: E731
    t.handle(obs("ST-1"))
    t.handle(obs("ST-2"))
    assert [r["serial"] for r in seen] == ["ST-2"]
    assert [s["serial"] for s in t.status()["stations"]] == ["ST-1", "ST-2"]
    t.station = None
    t.handle(obs("ST-1"))
    assert len(seen) == 2


# -- from the review: the two sources sharing one table ---------------------------------------------------------------
def test_rows_from_both_sources_in_one_second_merge(tmp_path):
    from app.store import Store
    s = Store(str(tmp_path))
    s.add_weather({"ts": 100, "temp_c": 20.0, "rh": 50.0, "rain_mm": 0.2})
    s.add_weather({"ts": 100, "soil_pct": 40.0, "rain_mm": 0.1})
    row = s.weather_since(0)[0]
    assert row["temp_c"] == 20.0 and row["soil_pct"] == 40.0 and row["rain_mm"] == pytest.approx(0.3)


def test_a_running_total_never_counts_the_tempests_rain_again():
    vals = {"rain_rate": st(5.0, "mm")}
    ha = weather.HAWeather(states(vals))
    c = weather.config({"ha": {"rain": "sensor.rain_rate"}})
    assert run(ha.read(c, None, now=0))["rain_mm"] == 0.0            # HA feeds rain (no Tempest yet)
    vals["rain_rate"] = st(9.0, "mm")                               # 4 mm while the Tempest was the source
    assert "rain_mm" not in (run(ha.read(c, 30, now=60)) or {})
    vals["rain_rate"] = st(9.2, "mm")                               # the Tempest went quiet: HA again
    assert run(ha.read(c, 3600, now=120))["rain_mm"] == pytest.approx(0.2)
    c2 = weather.config({"ha": {"rain": "sensor.other_total"}})
    ha.get = states({"other_total": st(800.0, "mm")})
    assert run(ha.read(c2, None, now=180))["rain_mm"] == 0.0          # a new entity starts afresh


def test_unknown_rain_units_and_stale_sensors_are_not_read():
    old = "2000-01-01T00:00:00+00:00"
    ha = weather.HAWeather(states(rain_rate=st(30, "%"), soil_a={**st(60, "%"), "last_updated": old}))
    row = run(ha.read(cfg(ha={"rain": "sensor.rain_rate", "soil": ["sensor.soil_a"]}), None, now=time.time()))
    assert row is None or ("rain_mm" not in row and "soil_pct" not in row)
    assert "unit" in ha.status()["errors"]["sensor.rain_rate"] and "2 h" in ha.status()["errors"]["sensor.soil_a"]


def test_ha_only_needs_its_entity():
    with pytest.raises(ValueError):
        weather.validate({"use": {"rain": "ha"}})


def test_a_soil_only_row_does_not_hide_the_temperature():
    now = time.time()
    rows = [{"ts": now - 90, "temp_c": 12.0, "rh": 95.0, "precip_type": 0}, {"ts": now - 30, "soil_pct": 20.0}]
    out = evaluate(now, rows, None, None, 40.0, -100.0, ConditionsConfig())
    assert out["inputs"]["temp_c"] == 12.0 and out["inputs"]["dew_c"] is not None
    assert next(r for r in out["rules"] if r["rule"] == "Dew")["detail"] != "no dew risk"


def ent(entity_id, state, unit=None, dc=None, name=None, **attrs):
    a = {**attrs, **({"unit_of_measurement": unit} if unit else {}), **({"device_class": dc} if dc else {}),
         **({"friendly_name": name} if name else {})}
    return {"entity_id": entity_id, "state": str(state), "attributes": a}


def test_candidates_match_each_input_by_device_class():
    out = weather.candidates([
        ent("sensor.tempest_rain_rate", 0.2, "mm/h", "precipitation_intensity", "Tempest rain rate"),
        ent("sensor.daily_rain", 3.1, "in", "precipitation"),
        ent("sensor.rain_chance", 40, "%"),                                  # a probability is never rain
        ent("sensor.kitchen_temperature", 21, "°C", "temperature"),
        ent("sensor.outdoor_temperature", 60, "°F", "temperature"),
        ent("sensor.outdoor_humidity", 70, "%", "humidity"),
        ent("sensor.lawn_soil_moisture", 33, "%", "moisture"),
        ent("binary_sensor.rain_detector", "off", dc="moisture"),
        ent("weather.home", "rainy", temperature=55, temperature_unit="°F", humidity=90),
        ent("sensor.gone", "unavailable", "°C", "temperature"),
        ent("light.porch", "on"),
    ])
    assert [x["entity"] for x in out["rain"]] == ["sensor.tempest_rain_rate", "sensor.daily_rain",
                                                  "binary_sensor.rain_detector", "weather.home"]
    assert [x["entity"] for x in out["temp"]] == ["sensor.outdoor_temperature", "sensor.kitchen_temperature", "weather.home"]
    assert out["temp"][0]["likely"] and not out["temp"][1]["likely"] and out["temp"][2]["value"] == "55 °F"
    assert [x["entity"] for x in out["humidity"]] == ["sensor.outdoor_humidity", "weather.home"]
    assert [x["entity"] for x in out["soil"]] == ["sensor.lawn_soil_moisture", "binary_sensor.rain_detector"]
    every = [x["entity"] for x in out["all"]]
    assert "sensor.rain_chance" in every and "light.porch" not in every and "weather.home" not in every and "sensor.gone" not in every


def test_a_weather_entity_and_binary_moisture_are_read():
    home = ent("weather.home", "pouring", temperature=50, temperature_unit="°F", humidity=80)
    vals = {"home": home, "soil_bin": ent("binary_sensor.soil_bin", "on", dc="moisture")}
    ha = weather.HAWeather(states(vals))
    c = weather.config({"ha": {"rain": "weather.home", "temp": "weather.home", "humidity": "weather.home",
                               "soil": ["binary_sensor.soil_bin"]}})
    row = run(ha.read(c, None, now=time.time()))
    assert row["temp_c"] == 10.0 and row["rh"] == 80 and row["precip_type"] == 1 and row["rain_mm"] > 0
    assert row["soil_pct"] == 100.0
    vals["home"] = {**home, "state": "cloudy"}
    assert run(ha.read(c, None, now=time.time() + 60))["precip_type"] == 0


def test_daily_rates_and_cm_totals_and_an_old_off_sensor():
    assert weather.rain_reading(st(24, "mm/d")) == ("rate", 1.0)
    assert weather.rain_reading(st(1, "cm")) == ("total", 10.0)
    old = {**st("off"), "last_updated": "2000-01-01T00:00:00+00:00"}           # unchanged for ages is still off
    ha = weather.HAWeather(states(rain_rate=old))
    row = run(ha.read(cfg(ha={"rain": "sensor.rain_rate"}, use={"rain": "ha"}), None, now=time.time()))
    assert row["precip_type"] == 0 and not ha.status()["errors"]
    fresh = {**st(0.0, "mm/h"), "last_updated": "2000-01-01T00:00:00+00:00", "last_reported": "2999-01-01T00:00:00+00:00"}
    assert weather.age_s(fresh, time.time()) < 0                               # last_reported wins when newer


def group(eid, state, members):
    g = ent(eid, state)
    g["attributes"]["entity_id"] = members
    return g


def test_presence_from_each_kind_of_entity():
    from app.ha import presence_candidates, presence_from
    assert presence_from(ent("alarm_control_panel.home", "armed_away")) is False
    assert presence_from(ent("alarm_control_panel.home", "armed_home")) is True
    assert presence_from(ent("zone.home", "0")) is False and presence_from(ent("zone.home", "2")) is True
    assert presence_from(ent("person.sam", "not_home")) is False and presence_from(ent("person.sam", "home")) is True
    assert presence_from(ent("person.sam", "unavailable")) is None and presence_from(ent("group.odd", "open")) is None
    out = presence_candidates([ent("person.sam", "home"), ent("zone.work", "1"), ent("zone.home", "1"),
                               ent("alarm_control_panel.home", "disarmed"), group("group.lights", "on", ["light.a"]),
                               group("group.family", "home", ["person.sam"]), ent("sensor.x", "1")])
    assert [x["entity"] for x in out] == ["alarm_control_panel.home", "zone.home", "group.family", "person.sam"]


def test_connection_errors_in_words():
    import httpx
    from app.ha import net_error
    msg = net_error(OSError("[Errno -2] Name or service not known"), "http://homeassistant.local:8123")
    assert "homeassistant.local" in msg and "Docker" in msg and ":8123" in msg
    assert "no answer" in net_error(httpx.ConnectTimeout(""), "http://192.0.2.1:8123")
    assert net_error(RuntimeError(""), None) == "RuntimeError"
