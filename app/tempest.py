"""WeatherFlow Tempest, read from the hub's local UDP broadcast (port 50222). No account, no cloud, no HA.

Message format: WeatherFlow UDP API. `obs_st` arrives once a minute; `evt_precip` when rain starts;
`rapid_wind` every 3 s. Containers need host networking to receive the broadcast.
"""
import asyncio
import json
import logging
import math
import socket
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

# obs_st field order (WeatherFlow UDP API v171)
OBS_ST = ["ts", "wind_lull", "wind_avg", "wind_gust", "wind_dir", "wind_interval", "pressure", "temp_c", "rh",
          "lux", "uv", "solar", "rain_mm", "precip_type", "lightning_dist", "lightning", "battery_v", "interval_min"]


def dew_point_c(temp_c: float | None, rh: float | None) -> float | None:
    """Magnus formula."""
    if temp_c is None or rh is None or rh <= 0:
        return None
    b, c = 17.62, 243.12
    gamma = math.log(rh / 100.0) + (b * temp_c) / (c + temp_c)
    return round(c * gamma / (b - gamma), 2)


def parse_obs_st(msg: dict) -> list[dict]:
    rows = []
    for obs in msg.get("obs") or []:
        if not isinstance(obs, list):
            continue
        row = {name: obs[i] if i < len(obs) else None for i, name in enumerate(OBS_ST)}
        row["dew_c"] = dew_point_c(row["temp_c"], row["rh"])
        row["serial"] = msg.get("serial_number")
        rows.append(row)
    return rows


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, listener: "TempestListener"):
        self.listener = listener

    def datagram_received(self, data, addr):
        try:
            msg = json.loads(data)
        except ValueError:
            return
        self.listener.handle(msg)


class TempestListener:
    def __init__(self, port: int, on_obs: Callable[[dict], None], on_event: Callable[[str, dict], None],
                 station: str | None = None):
        self.port = port
        self.station = station                  # only this station's observations count (None = any)
        self.stations: dict[str, float] = {}    # every station heard -> when
        self.on_obs, self.on_event = on_obs, on_event
        self.last_obs: dict | None = None
        self.last_obs_at = 0.0
        self.last_rain_start: float | None = None
        self.wind: dict | None = None
        self.hub: dict | None = None
        self.packets = 0
        self.error = ""
        self._transport = None

    def stop(self) -> None:
        if self._transport:
            self._transport.close()
            self._transport = None

    def handle(self, msg: dict) -> None:
        self.packets += 1
        kind = msg.get("type")
        if kind == "obs_st":
            serial = msg.get("serial_number")
            if serial:
                self.stations[serial] = time.time()
            if self.station and serial != self.station:
                return
            for row in parse_obs_st(msg):
                self.last_obs, self.last_obs_at = row, time.time()
                self.on_obs(row)
        elif kind == "evt_precip":
            if self.station and msg.get("serial_number") not in (None, self.station):
                return
            evt = msg.get("evt") or []
            start = evt[0] if evt else time.time()
            if evt and start == self.last_rain_start:
                return                       # the hub sends each event more than once; its timestamp tells them apart
            self.last_rain_start = start
            self.on_event("rain_started", msg)
        elif kind == "rapid_wind":
            ob = msg.get("ob") or []
            if len(ob) >= 3:
                self.wind = {"ts": ob[0], "speed": ob[1], "dir": ob[2]}
        elif kind == "hub_status":
            self.hub = {"serial": msg.get("serial_number"), "firmware": msg.get("firmware_revision"),
                        "uptime": msg.get("uptime"), "rssi": msg.get("rssi")}

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", self.port))
        except OSError as exc:
            self.error = f"cannot listen on UDP {self.port}: {exc}"
            log.warning(self.error)
            sock.close()
            return
        self._transport, _ = await loop.create_datagram_endpoint(lambda: _Protocol(self), sock=sock)
        log.info("listening for Tempest broadcasts on UDP %d", self.port)

    def status(self) -> dict:
        now = time.time()
        return {
            "listening_port": self.port,
            "packets": self.packets,
            "last_obs_age_s": round(now - self.last_obs_at, 1) if self.last_obs_at else None,
            "station": (self.last_obs or {}).get("serial"),
            "station_filter": self.station,
            "stations": [{"serial": s, "age_s": round(now - at, 1)} for s, at in sorted(self.stations.items())],
            "battery_v": (self.last_obs or {}).get("battery_v"),
            "hub": self.hub,
            "error": self.error,
        }
