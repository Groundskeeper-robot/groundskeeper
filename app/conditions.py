"""Conditions v1: is the lawn dry? Four rules, every one visible in the UI (spec §9).

1. Raining now                         -> Wet
2. Rain in the last N hours            -> Wet, decaying to Damp, then Dry (weighted by amount, time constant tau)
3. Dew overnight (temp - dew point < spread) -> Damp until sunrise + X
4. Robot rain sensor reads wet         -> Wet (veto), only once a threshold has been calibrated
5. Soil moisture (HA sensors)          -> Wet / Damp above set percentages, regardless of how long ago it rained

A drying credit from sun and wind, and self-calibration, are deliberately deferred (experimental, spec §9).
"""
import calendar
import math
import time
from dataclasses import asdict, dataclass

from .tempest import dew_point_c

SEVERITY = {"Dry": 0, "Damp": 1, "Wet": 2}


@dataclass
class ConditionsConfig:
    lookback_h: float = 6.0
    tau_h: float = 2.0             # rain wetness halves roughly every 1.4 h
    wet_mm: float = 0.5            # decayed rain above this -> Wet
    damp_mm: float = 0.1           # decayed rain above this -> Damp
    dew_spread_c: float = 2.0      # temperature minus dew point below this -> dew risk
    dry_after_sunrise_h: float = 2.0
    raining_window_s: float = 600  # rain in the last 10 minutes counts as "raining now"
    robot_rain_wet_below: float | None = None  # robot rain sensor raw value meaning wet; None = not calibrated
    soil_wet_pct: float = 45.0     # soil moisture at or above this -> Wet
    soil_damp_pct: float = 35.0    # ... at or above this -> Damp
    soil_max_age_h: float = 2.0    # a soil reading older than this is ignored

    @classmethod
    def from_dict(cls, d: dict | None) -> "ConditionsConfig":
        known = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def to_dict(self) -> dict:
        return asdict(self)


# -- sun ---------------------------------------------------------------------------------------------------------
def _sun_ut_hours(day_of_year: int, lat: float, lon: float, rising: bool, zenith: float = 90.833) -> float | None:
    """Sunrise/sunset in UT hours (the classic Almanac for Computers algorithm). None if the sun never crosses."""
    lng_hour = lon / 15
    t = day_of_year + ((6 if rising else 18) - lng_hour) / 24
    m = 0.9856 * t - 3.289
    rad = math.radians
    l_ = (m + 1.916 * math.sin(rad(m)) + 0.020 * math.sin(rad(2 * m)) + 282.634) % 360
    ra = math.degrees(math.atan(0.91764 * math.tan(rad(l_)))) % 360
    ra = (ra + (math.floor(l_ / 90) * 90 - math.floor(ra / 90) * 90)) / 15
    sin_dec = 0.39782 * math.sin(rad(l_))
    cos_dec = math.cos(math.asin(sin_dec))
    cos_h = (math.cos(rad(zenith)) - sin_dec * math.sin(rad(lat))) / (cos_dec * math.cos(rad(lat)))
    if not -1 <= cos_h <= 1:
        return None
    h = (360 - math.degrees(math.acos(cos_h))) if rising else math.degrees(math.acos(cos_h))
    t_local = h / 15 + ra - 0.06571 * t - 6.622
    return (t_local - lng_hour) % 24


def _sun_on(day_ts: float, lat: float, lon: float, rising: bool) -> float | None:
    day = time.gmtime(day_ts)
    midnight = calendar.timegm((day.tm_year, day.tm_mon, day.tm_mday, 0, 0, 0))
    ut = _sun_ut_hours(day.tm_yday, lat, lon, rising=rising)
    return None if ut is None else midnight + ut * 3600


def _sunrise_on(day_ts: float, lat: float, lon: float) -> float | None:
    return _sun_on(day_ts, lat, lon, True)


def is_dark(now: float, lat: float, lon: float) -> bool | None:
    """Between sunset and sunrise at the yard: the latest sun event before `now` is a sunset. The algorithm's events
    for a UT date can fall on the next UT date (an evening sunset west of Greenwich), so the events of three days are
    compared rather than one day's. None where the sun neither rose nor set in that time (polar day or night)."""
    events = [(ts, rising) for back in (-1, 0, 1) for rising in (True, False)
              if (ts := _sun_on(now + back * 86400, lat, lon, rising)) is not None and ts <= now]
    if not events:
        return None
    return not max(events)[1]


def sunrise_ts(now: float, lat: float, lon: float) -> float | None:
    """The most recent sunrise at or before `now`."""
    for back in (0, 1, 2):
        ts = _sunrise_on(now - back * 86400, lat, lon)
        if ts is not None and ts <= now:
            return ts
    return None


def next_sunrise_ts(now: float, lat: float, lon: float) -> float | None:
    for ahead in (0, 1, 2):
        ts = _sunrise_on(now + ahead * 86400, lat, lon)
        if ts is not None and ts > now:
            return ts
    return None


# -- words -------------------------------------------------------------------------------------------------------
def mm(v: float, units: str = "metric", digits: int = 1) -> str:
    return f"{v / 25.4:.{digits + 1}f} in" if units == "us" else f"{v:.{digits}f} mm"


def deg(v: float, units: str = "metric", spread: bool = False) -> str:
    """A temperature, or a difference of two (spread: no 32 offset)."""
    return f"{v * 9 / 5 + (0 if spread else 32):.1f} °F" if units == "us" else f"{v:.1f} °C"


# -- rules -------------------------------------------------------------------------------------------------------
def evaluate(now: float, weather: list[dict], last_rain_start: float | None, robot_rain: float | None,
             lat: float | None, lon: float | None, cfg: ConditionsConfig, units: str = "metric") -> dict:
    """weather: weather rows (oldest first; Tempest and/or HA) covering at least the lookback window."""
    rules = []
    recent = [w for w in weather if now - w["ts"] <= cfg.lookback_h * 3600]

    def newest(field: str, max_age_s: float = 1800) -> dict | None:
        """The newest row carrying `field` (rows from two sources each carry only their own inputs)."""
        return next((w for w in reversed(weather) if w.get(field) is not None and now - w["ts"] <= max_age_s), None)

    def dew_of(w: dict) -> float | None:
        return w.get("dew_c") if w.get("dew_c") is not None else dew_point_c(w.get("temp_c"), w.get("rh"))

    t_row, h_row, p_row = newest("temp_c"), newest("rh"), newest("precip_type", 180)
    temp_now, rh_now = (t_row or {}).get("temp_c"), (h_row or {}).get("rh")
    dew_now = t_row["dew_c"] if t_row and t_row.get("dew_c") is not None else dew_point_c(temp_now, rh_now)
    latest = newest("temp_c") or newest("rain_mm") or (weather[-1] if weather else None)

    # 1. raining now
    rain_10m = sum((w.get("rain_mm") or 0) for w in weather if now - w["ts"] <= cfg.raining_window_s)
    precip_now = bool(p_row and (p_row.get("precip_type") or 0) > 0)
    started = bool(last_rain_start and now - last_rain_start <= cfg.raining_window_s)
    raining = rain_10m > 0 or precip_now or started
    rules.append({"rule": "Raining now", "state": "Wet" if raining else "Dry",
                  "detail": f"{mm(rain_10m, units, 2)} in the last 10 min" + (", rain-start event" if started else "")})

    # 2. decayed rain
    wetness = sum((w.get("rain_mm") or 0) * math.exp(-((now - w["ts"]) / 3600) / cfg.tau_h) for w in recent)
    rain_total = sum((w.get("rain_mm") or 0) for w in recent)
    rain_state = "Wet" if wetness >= cfg.wet_mm else "Damp" if wetness >= cfg.damp_mm else "Dry"
    rain_dry_by = None
    if wetness >= cfg.damp_mm and not raining:
        rain_dry_by = now + cfg.tau_h * math.log(wetness / cfg.damp_mm) * 3600
    rules.append({"rule": f"Rain in the last {cfg.lookback_h:g} h", "state": rain_state,
                  "detail": f"{mm(rain_total, units)} total, {mm(wetness, units, 2)} after decay"})

    # 3. dew
    dew_state, dew_detail, dew_dry_by = "Dry", "no dew risk", None
    if lat is not None and lon is not None and weather:
        rise = sunrise_ts(now, lat, lon)
        overnight = [w for w in weather if rise and rise - 10 * 3600 <= w["ts"] <= rise + 3600]
        spreads = [w["temp_c"] - dew_of(w) for w in overnight if w.get("temp_c") is not None and dew_of(w) is not None]
        dew_night = bool(spreads) and min(spreads) < cfg.dew_spread_c
        spread_now = (temp_now - dew_now) if temp_now is not None and dew_now is not None else None
        if rise and dew_night and now < rise + cfg.dry_after_sunrise_h * 3600:
            dew_state, dew_dry_by = "Damp", rise + cfg.dry_after_sunrise_h * 3600
            dew_detail = f"dew overnight (spread {deg(min(spreads), units, True)}), dry about {cfg.dry_after_sunrise_h:g} h after sunrise"
        elif spread_now is not None and spread_now < cfg.dew_spread_c:
            nxt = next_sunrise_ts(now, lat, lon)
            dew_state = "Damp"
            dew_dry_by = (nxt + cfg.dry_after_sunrise_h * 3600) if nxt else None
            dew_detail = f"dew forming now (spread {deg(spread_now, units, True)})"
        elif spread_now is not None:
            dew_detail = f"spread {deg(spread_now, units, True)}"
    rules.append({"rule": "Dew", "state": dew_state, "detail": dew_detail})

    # 4. robot rain sensor
    if cfg.robot_rain_wet_below is None:
        rules.append({"rule": "Robot rain sensor", "state": "Dry",
                      "detail": f"raw {robot_rain if robot_rain is not None else '—'}; not calibrated, so no veto yet"})
    else:
        wet = robot_rain is not None and robot_rain < cfg.robot_rain_wet_below
        rules.append({"rule": "Robot rain sensor", "state": "Wet" if wet else "Dry",
                      "detail": f"raw {robot_rain}, wet below {cfg.robot_rain_wet_below:g}"})

    # 5. soil moisture
    soil = next((w for w in reversed(weather) if w.get("soil_pct") is not None), None)
    if soil is None or now - soil["ts"] > cfg.soil_max_age_h * 3600:
        rules.append({"rule": "Soil moisture", "state": "Dry",
                      "detail": "no soil sensor" if soil is None else "no recent reading"})
    else:
        pct = soil["soil_pct"]
        rules.append({"rule": "Soil moisture",
                      "state": "Wet" if pct >= cfg.soil_wet_pct else "Damp" if pct >= cfg.soil_damp_pct else "Dry",
                      "detail": f"{pct:.0f} % (damp from {cfg.soil_damp_pct:g} %, wet from {cfg.soil_wet_pct:g} %)"})

    state = max((r["state"] for r in rules), key=SEVERITY.get)
    dry_by = None
    if state != "Dry" and not raining:
        candidates = [t for t in (rain_dry_by, dew_dry_by) if t]
        dry_by = max(candidates) if candidates else None
    return {
        "state": state,
        "dry_by": dry_by,
        "raining": raining,
        "rules": rules,
        "inputs": {
            "temp_c": temp_now,
            "rh": rh_now,
            "dew_c": dew_now,
            "solar": latest.get("solar") if latest else None,
            "wind_avg": latest.get("wind_avg") if latest else None,
            "rain_last_h_mm": round(sum((w.get("rain_mm") or 0) for w in weather if now - w["ts"] <= 3600), 2),
            "rain_lookback_mm": round(rain_total, 2),
            "robot_rain_raw": robot_rain,
            "obs_age_s": round(now - latest["ts"], 1) if latest else None,
            "soil_pct": soil["soil_pct"] if soil else None,
        },
        "config": cfg.to_dict(),
        "units": units,
        "evaluated": now,
    }
