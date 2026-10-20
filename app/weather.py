"""Where Lawn conditions get their weather: the Tempest hub (app/tempest.py), Home Assistant sensors, or both.

Settings → Lawn conditions keeps `weather_sources`:
  station   Tempest serial to listen to (None = any station heard)
  units     "metric" | "us", for display only (everything is stored metric)
  ha        HA entity ids: rain (a rate, a running total, or an on/off rain sensor), temp, humidity, soil (a list);
            a weather.* entity can stand in for rain (its condition), temp and humidity
  use       per input (rain, temp, humidity): "auto" (the Tempest while it reports, else HA), "tempest" or "ha"

Both sources write rows to the same weather table, each only with the inputs it is the source for at that moment,
so rain is never counted twice. Soil moisture comes from HA only (Zigbee2MQTT sensors arrive there as entities)
and is stored as soil_pct: the wettest sensor's reading.
"""
import logging
import re
import time
from datetime import datetime

from .tempest import dew_point_c

log = logging.getLogger(__name__)

INPUTS = ("rain", "temp", "humidity")
TEMPEST_FRESH_S = 600                    # "auto" uses the Tempest while it has reported within this
RAIN_STALE_S = 900                       # a rain reading HA has not updated in this long counts as no reading
SOIL_STALE_S = 7200                      # ... a soil reading
ON_RAIN_MM_H = 1.0                       # an on/off rain sensor that is on counts as light rain
ENTITY = re.compile(r"[a-z_]+\.[a-z0-9_]+")
DEFAULTS = {"station": None, "units": "metric",
            "ha": {"rain": None, "temp": None, "humidity": None, "soil": []},
            "use": {k: "auto" for k in INPUTS}}
TEMPEST_FIELDS = {"rain": ("rain_mm", "precip_type"), "temp": ("temp_c",), "humidity": ("rh",)}


def config(raw: dict | None) -> dict:
    """The stored setting merged over the defaults (always every key)."""
    raw = raw or {}
    ha = {**DEFAULTS["ha"], **(raw.get("ha") or {})}
    use = {**DEFAULTS["use"], **(raw.get("use") or {})}
    return {"station": raw.get("station"), "units": raw.get("units") or "metric", "ha": ha, "use": use}


def validate(value: dict) -> dict:
    """Clean a PUT body; raises ValueError with a message for the user."""
    if not isinstance(value, dict):
        raise ValueError("expected an object")
    out = config(value)
    if out["units"] not in ("metric", "us"):
        raise ValueError("units: metric or us")
    if out["station"] is not None and not (isinstance(out["station"], str) and re.fullmatch(r"[A-Za-z0-9-]{1,32}", out["station"])):
        raise ValueError("station: a Tempest serial like ST-00012345, or null for any")
    for k in INPUTS:
        if out["use"][k] not in ("auto", "tempest", "ha"):
            raise ValueError(f"use.{k}: auto, tempest or ha")
        if out["use"][k] == "ha" and not out["ha"][k]:
            raise ValueError(f"enter the Home Assistant entity for {k} before choosing HA only")
        e = out["ha"][k]
        if e is not None and not (isinstance(e, str) and ENTITY.fullmatch(e)):
            raise ValueError(f"ha.{k}: an entity id like sensor.backyard_rain_rate, or null")
    soil = out["ha"]["soil"]
    if not isinstance(soil, list) or len(soil) > 8 or not all(isinstance(e, str) and ENTITY.fullmatch(e) for e in soil):
        raise ValueError("ha.soil: up to 8 entity ids")
    return out


def source_for(cfg: dict, key: str, tempest_age_s: float | None) -> str | None:
    """Which source feeds an input right now: "tempest", "ha" or None (nothing configured for it)."""
    want = cfg["use"][key]
    has_ha = bool(cfg["ha"].get(key))
    fresh = tempest_age_s is not None and tempest_age_s <= TEMPEST_FRESH_S
    if want == "tempest":
        return "tempest"
    if want == "ha":
        return "ha" if has_ha else None
    return "tempest" if fresh or not has_ha else "ha"


def filter_tempest(row: dict, cfg: dict, tempest_age_s: float | None) -> dict:
    """A Tempest row without the inputs HA is the source for (dew follows temperature and humidity)."""
    out = dict(row)
    for key, fields in TEMPEST_FIELDS.items():
        if source_for(cfg, key, tempest_age_s) != "tempest":
            for f in fields:
                out.pop(f, None)
    if "temp_c" not in out or "rh" not in out:
        out.pop("dew_c", None)
    return out


# -- reading HA states --------------------------------------------------------------------------------------------
def _number(state: dict | None) -> float | None:
    try:
        v = float((state or {}).get("state"))
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def _unit(state: dict | None) -> str:
    return str(((state or {}).get("attributes") or {}).get("unit_of_measurement") or "").strip().lower()


def _attrs(state: dict | None) -> dict:
    return (state or {}).get("attributes") or {}


def _is_weather(state: dict | None) -> bool:
    return str((state or {}).get("entity_id") or "").startswith("weather.")


def _to_c(v: float, unit: str) -> float:
    unit = unit.strip().lower()
    if unit in ("°f", "f", "fahrenheit"):
        return round((v - 32) * 5 / 9, 2)
    if unit in ("k", "kelvin"):
        return round(v - 273.15, 2)
    return v


def temp_c(state: dict | None) -> float | None:
    if _is_weather(state):                                # a weather.* entity keeps it in its attributes
        v = _number({"state": _attrs(state).get("temperature")})
        return None if v is None else _to_c(v, str(_attrs(state).get("temperature_unit") or ""))
    v = _number(state)
    return None if v is None else _to_c(v, _unit(state))


def humidity(state: dict | None) -> float | None:
    if _is_weather(state):
        return _number({"state": _attrs(state).get("humidity")})
    return _number(state)


def soil_pct(state: dict | None) -> float | None:
    """A moisture % sensor, or an on/off moisture sensor as 100 % (wet) / 0 % (dry)."""
    if (state or {}).get("state") in ("on", "off"):
        return 100.0 if state["state"] == "on" else 0.0
    return _number(state)


def age_s(state: dict | None, now: float) -> float | None:
    """How long ago HA last heard from the sensor (last_updated), or None when it does not say."""
    ages = []
    for key in ("last_reported", "last_updated"):         # last_reported moves on every report, even unchanged
        try:
            ages.append(now - datetime.fromisoformat(str((state or {})[key]).replace("Z", "+00:00")).timestamp())
        except (KeyError, TypeError, ValueError):
            pass
    return min(ages) if ages else None


RAINY = ("rainy", "pouring", "lightning-rainy", "snowy-rainy", "hail")


def rain_reading(state: dict | None) -> tuple[str, float | bool] | None:
    """("on", bool) for an on/off rain sensor, ("rate", mm/h), or ("total", mm) for a running total. Raises
    ValueError for a unit it cannot read (mm/d, cm, %...), so a probability is never taken for millimetres."""
    if not state:
        return None
    if _is_weather(state):
        return "on", state.get("state") in RAINY
    if state.get("state") in ("on", "off"):
        return "on", state["state"] == "on"
    v, unit = _number(state), _unit(state)
    if v is None:
        return None
    if unit in ("mm/h", "mm/hr"):
        return "rate", v
    if unit in ("in/h", "in/hr"):
        return "rate", v * 25.4
    if unit == "mm/d":
        return "rate", v / 24
    if unit == "in/d":
        return "rate", v * 25.4 / 24
    if unit == "in":
        return "total", v * 25.4
    if unit == "mm":
        return "total", v
    if unit == "cm":
        return "total", v * 10
    raise ValueError(f"unit {unit or 'none'}: use mm, in, cm, mm/h, in/h, mm/d or in/d")


class HAWeather:
    """Polls the configured HA entities and turns them into weather rows. `get(entity)` returns HA's state dict
    (or None); it is injected so tests need no HA."""

    def __init__(self, get):
        self.get = get
        self.prev_total: dict[str, float] = {}               # entity -> its running total at the last read
        self.last: dict = {}
        self.errors: dict[str, str] = {}
        self.last_at: float | None = None

    async def _state(self, entity: str) -> dict | None:
        try:
            st = await self.get(entity)
        except Exception as exc:
            self.errors[entity] = str(exc)[:120]
            return None
        if st is None or st.get("state") in ("unavailable", "unknown", None):
            self.errors[entity] = "unavailable"
            return None
        self.errors.pop(entity, None)
        return st

    async def read(self, cfg: dict, tempest_age_s: float | None, now: float | None = None) -> dict | None:
        """One row for the inputs HA is the source for now (plus soil), or None when there is nothing to read."""
        now = time.time() if now is None else now
        ha = cfg["ha"]
        row: dict = {"ts": round(now)}
        if ha.get("temp") and source_for(cfg, "temp", tempest_age_s) == "ha":
            row["temp_c"] = temp_c(await self._state(ha["temp"]))
        if ha.get("humidity") and source_for(cfg, "humidity", tempest_age_s) == "ha":
            row["rh"] = humidity(await self._state(ha["humidity"]))
        if "temp_c" in row and "rh" in row:
            row["dew_c"] = dew_point_c(row["temp_c"], row["rh"])
        if ha.get("rain"):
            # A running total is read every time, whichever source feeds rain now, so the step from the last read
            # is always just the last minute's rain: never the whole time the Tempest was the source.
            ent, feeds = ha["rain"], source_for(cfg, "rain", tempest_age_s) == "ha"
            st = await self._state(ent)
            try:
                reading = rain_reading(st)
            except ValueError as exc:
                self.errors[ent], reading = str(exc), None
            if reading and reading[0] == "rate" and (age_s(st, now) or 0) > RAIN_STALE_S:     # on/off holds its state
                self.errors[ent], reading = "no update from the sensor in 15 min", None
            dt = min(now - self.last_at, 600) if self.last_at else 60
            if reading:
                kind, v = reading
                if kind == "total":
                    prev = self.prev_total.get(ent)
                    self.prev_total = {ent: v}                # only the configured entity; a new one starts afresh
                    mm = max(0.0, v - prev) if prev is not None else 0.0     # a reset to 0 is not rain
                else:
                    rate = (ON_RAIN_MM_H if v else 0.0) if kind == "on" else v
                    mm = rate * dt / 3600
                if feeds:
                    row["rain_mm"] = round(mm, 3)
                    row["precip_type"] = 1 if (v if kind == "on" else mm > 0) else 0
            else:
                self.prev_total.pop(ent, None)
        soil = []
        for e in ha.get("soil") or []:
            st = await self._state(e)
            v = soil_pct(st)
            if v is not None and (st or {}).get("state") not in ("on", "off") and (age_s(st, now) or 0) > SOIL_STALE_S:
                self.errors[e], v = "no update from the sensor in 2 h", None
            if v is not None:
                soil.append({"entity": e, "pct": v})
        if soil:
            row["soil_pct"] = max(s["pct"] for s in soil)
        self.last, self.last_at = {"row": row, "soil": soil}, now
        return row if len(row) > 1 else None

    def status(self) -> dict:
        return {"last_at": self.last_at, "soil": self.last.get("soil", []), "errors": dict(self.errors)}


# -- picking entities ---------------------------------------------------------------------------------------------
# Which HA entities fit each input, by HA's device classes (the sensor and binary_sensor docs). The units are the
# ones the readers above understand, so anything offered here can be read.
RAIN_RATE_UNITS = ("mm/h", "mm/hr", "in/h", "in/hr", "mm/d", "in/d")
RAIN_TOTAL_UNITS = ("mm", "in", "cm")
TEMP_UNITS = ("°c", "°f", "c", "f", "k")
OUTDOOR = ("outdoor", "outside", "exterior", "yard", "garden", "weather", "tempest", "station")
LIKELY = {"rain": ("rain", "precip"), "temp": OUTDOOR, "humidity": OUTDOOR,
          "soil": ("soil", "lawn", "grass", "garden", "yard", "plant")}


def _fits(state: dict) -> dict[str, tuple[int, str]]:
    """{input: (rank, why)} for the inputs an entity can feed; a lower rank is a better kind of source."""
    domain = str(state.get("entity_id") or "").split(".", 1)[0]
    a, unit = _attrs(state), _unit(state)
    dc = str(a.get("device_class") or "").lower()
    out: dict[str, tuple[int, str]] = {}
    if domain == "weather":                               # the last resort for each input it has
        if a.get("temperature") is not None:
            out["temp"] = (3, "weather entity")
        if a.get("humidity") is not None:
            out["humidity"] = (3, "weather entity")
        out["rain"] = (4, "weather entity: rainy or not, no amount")
    elif domain == "binary_sensor" and dc == "moisture":
        out["rain"] = (2, "on/off: wet")
        out["soil"] = (2, "on/off: wet")
    elif domain == "sensor":
        if dc == "precipitation_intensity" and unit in RAIN_RATE_UNITS:
            out["rain"] = (0, "rain rate")
        elif dc == "precipitation" and unit in RAIN_TOTAL_UNITS:
            out["rain"] = (1, "running total")
        elif dc == "temperature" and unit in TEMP_UNITS:
            out["temp"] = (0, "temperature")
        elif dc == "humidity" and unit == "%":
            out["humidity"] = (0, "humidity")
        elif dc == "moisture" and unit == "%":
            out["soil"] = (0, "moisture %")
    return out


def _value(state: dict, key: str) -> str:
    st, a = state.get("state"), _attrs(state)
    if _is_weather(state):
        if key == "temp":
            return f"{a.get('temperature')} {a.get('temperature_unit') or ''}".strip()
        if key == "humidity":
            return f"{a.get('humidity')} %"
        return str(st)
    unit = a.get("unit_of_measurement")
    return f"{st} {unit}" if unit else str(st)


def candidates(states: list[dict]) -> dict:
    """Per input, the entities that fit it, likely ones first (an outdoor / rain / soil name, or a weather
    station), plus `all`: every sensor and binary sensor with a number or an on/off state, for integrations that
    set no device class. Each entry: entity, name, value (as HA shows it), why (what kind of source), likely."""
    picks: dict[str, list] = {k: [] for k in (*INPUTS, "soil")}
    every = []
    for st in states or []:
        ent = str(st.get("entity_id") or "")
        if not ENTITY.fullmatch(ent) or st.get("state") in ("unavailable", "unknown"):
            continue
        name = str(_attrs(st).get("friendly_name") or ent)
        text = f"{ent} {name}".lower()
        for key, (rank, why) in _fits(st).items():
            likely = not _is_weather(st) and any(w in text for w in LIKELY[key])     # a weather entity is the last resort
            picks[key].append({"entity": ent, "name": name, "value": _value(st, key), "why": why, "likely": likely,
                               "_rank": (rank, not likely, name.lower())})
        if ent.split(".", 1)[0] in ("sensor", "binary_sensor") and (st.get("state") in ("on", "off") or _number(st) is not None):
            every.append({"entity": ent, "name": name, "value": _value(st, "")})
    for key in picks:
        picks[key].sort(key=lambda x: x.pop("_rank"))
    every.sort(key=lambda x: x["name"].lower())
    return {**picks, "all": every}
