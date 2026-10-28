"""The robot's network links (Robot → Health → Network): how strong each one is, and the Wi-Fi it knows.

Strengths come from what the robot already reports: HaLow in dBm in every device message (`halow_status.strength`),
the 4G module's fields (`net_module_status.lte_*`, shown as reported: their units are not confirmed), and Wi-Fi only
from a scan (`get_wifi_list`, signal 0-100 %, `saved` for networks it has joined) plus `get_connect_wifi_name`. The
latest of those replies is shown whoever asked (the phone app's scans count too); a scan from here is a fresh ~4 s
scan on the robot, so it is asked for, never polled, and not while a plan runs.

Join and forget are the phone app's own commands (captured 2026-10-05 and 2026-10-10, docs/dev/protocol.md → Wi-Fi):
    change_to_wifi {"wifi_name": "...", "password": "..."}
    forget_wifi    {"wifi_name": "..."}
Only a join WITH a password was captured, so a join always carries one (what the app sends to rejoin a saved or an
open network is not known: an empty password might replace a saved one). Their replies were not captured either, so
neither is judged by its reply: a join is confirmed when get_connect_wifi_name names the network, a forget when a fresh
scan no longer marks it saved. Both go through the map writer (its lock, its route and its audit log, with the password
and the network name left out), only while the gate passes (main._network_write_block: map writing on, robot on its
charger, no plan), checked again once the lock is held. Neither can be undone from here: putting a forgotten network
back needs its password. The password is passed through to the robot once and never kept or logged; network names
stay out of events and the control log (an SSID can place a home on public Wi-Fi maps).
"""
import asyncio
import json
import time

from .codec import decode_any
from .mapwrite import WriteRefused
from . import telemetry

JOIN, FORGET = "change_to_wifi", "forget_wifi"
COMMANDS = frozenset({JOIN, FORGET})
SCAN_TIMEOUT_S = 60       # on a weak link the robot can take 30-60 s to answer a scan (protocol.md → Wi-Fi list)
NAME_TIMEOUT_S = 10       # get_connect_wifi_name: no answer at all when no network is connected
VERIFY_S, VERIFY_EVERY_S = 45, 5
# A scan takes the robot about 4 s; an answer sooner than this after asking is a late answer to an earlier ask (another
# client's, or one of ours that timed out), not to this one (protocol.md → Wi-Fi list).
FRESH_SCAN_S = 2.0
NAME_MAX = 32             # an SSID is at most 32 bytes
# HaLow levels from the robot's own behaviour (docs/dev/robot-states.md): it falls back to 4G at -82 dBm and comes
# back at -77; the panel saw robot-data gaps start at the fallback level.
HALOW_WEAK, HALOW_FALLBACK = -77, -82
LTE_FIELDS = ("lte_status", "lte_signal", "lte_rssi", "lte_rsrp", "lte_rsrq", "lte_csq", "lte_netera")
HEX = set("0123456789abcdefABCDEF")


def halow_level(dbm) -> str | None:
    if not isinstance(dbm, (int, float)) or dbm == 0:
        return None
    if dbm <= HALOW_FALLBACK:
        return "fallback"          # at or below the level where the robot switches to 4G
    return "weak" if dbm < HALOW_WEAK else "good"


def parse_list(data) -> list[dict] | None:
    """A get_wifi_list reply's networks, strongest first, one row per name (the strongest of its access points)."""
    body = decode_any(data)
    items = body.get("wifi_list") if isinstance(body, dict) else body
    if not isinstance(items, list):
        return None
    best: dict[str, dict] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        name = it.get("wifi_name")
        if not isinstance(name, str) or not name.strip():
            continue
        try:
            signal = max(0.0, min(100.0, float(it.get("signal"))))
        except (TypeError, ValueError):
            signal = None
        saved = it.get("saved") in (True, 1, "1", "true", "True")
        row = best.get(name)
        if row is None:
            best[name] = {"name": name, "signal": signal, "security": str(it.get("security") or ""), "saved": saved}
        else:
            row["saved"] = row["saved"] or saved
            if signal is not None and (row["signal"] is None or signal > row["signal"]):
                row["signal"] = signal
    return sorted(best.values(), key=lambda r: -(r["signal"] if r["signal"] is not None else -1))


def parse_connected(reply) -> str | None:
    """The network name in a get_connect_wifi_name reply. A plain string is the name as it is (an SSID like "1.0",
    "true" or "MTIz" must not be decoded as JSON or base64); only a JSON object is looked into."""
    if not isinstance(reply, dict) or reply.get("state") not in (None, 0):
        return None
    data = reply.get("data")
    if isinstance(data, str) and data.lstrip().startswith("{"):
        try:
            data = json.loads(data)
        except ValueError:
            pass
    if isinstance(data, dict):
        data = data.get("wifi_name") or data.get("name") or data.get("ssid")
    return data if isinstance(data, str) and data.strip() else None


def check_password(pw: str) -> None:
    """WPA: a passphrase of 8-63 bytes, or a 64-digit hex key."""
    n = len(pw.encode())
    if not (8 <= n <= 63 or (len(pw) == 64 and set(pw) <= HEX)):
        raise ValueError("the password is 8 to 63 characters (or a 64-digit key)")


class Network:
    """I/O comes in as functions (tests stand in for the robot): rec = the recorder (t, snapshot, replies, request,
    run_id, plan_active); writer = the MapWriter (its lock and _send carry the two writes). join and forget take
    gate() -> why a write is not allowed now (or None) and ask it again once they hold the writer's lock."""

    def __init__(self, store, rec, writer):
        self.store, self.rec, self.writer = store, rec, writer
        self._scan_lock = asyncio.Lock()
        self._no_name_at = 0.0     # the last time the robot did not answer get_connect_wifi_name (= no network)

    # -- reading -----------------------------------------------------------------------------------------------------
    def _reply(self, cmd: str) -> tuple[dict | None, float | None]:
        got = (getattr(self.rec, "replies", None) or {}).get(cmd)
        if not got:
            return None, None
        return got.get("data"), got.get("ts")

    def connected(self) -> tuple[str | None, float | None]:
        """The robot's Wi-Fi network and when that was learnt: its last answer, unless a later ask went unanswered."""
        reply, at = self._reply("get_connect_wifi_name")
        if self._no_name_at and (at is None or self._no_name_at > at):
            return None, self._no_name_at
        return (parse_connected(reply) if reply else None), at

    def status(self) -> dict:
        """Everything known now, without asking the robot anything."""
        r, now = self.rec, time.time()
        dbm = (r.t or {}).get("halow")
        at = r.update_at or None
        lte_raw = telemetry.get(r.snapshot or {}, "net_module_status") or {}
        lte = {k: lte_raw.get(k) for k in LTE_FIELDS if k in lte_raw}
        listing, list_at = self._reply("get_wifi_list")
        networks = parse_list((listing or {}).get("data")) if listing and listing.get("state") == 0 else None
        name, name_at = self.connected()
        signal = next((n["signal"] for n in networks or [] if n["name"] == name), None)
        return {
            "halow": {"dbm": dbm if isinstance(dbm, (int, float)) and dbm != 0 else None, "level": halow_level(dbm),
                      "age_s": round(now - at) if at else None, "weak_dbm": HALOW_WEAK, "fallback_dbm": HALOW_FALLBACK},
            "lte": {"fields": lte, "reported": any(v not in (None, 0, "0", "") for v in lte.values())},
            "wifi": {"connected": name, "signal": signal,
                     "connected_age_s": round(now - name_at) if name_at else None,
                     "networks": networks, "scan_age_s": round(now - list_at) if list_at else None},
            "scanning": self._scan_lock.locked(),
        }

    def bars(self) -> dict:
        """For the top bar: is the robot on Wi-Fi (None = not known yet) and the signal of that network, if scanned."""
        name, at = self.connected()
        listing, _ = self._reply("get_wifi_list")
        rows = parse_list((listing or {}).get("data")) if listing and listing.get("state") == 0 else None
        signal = next((n["signal"] for n in rows or [] if n["name"] == name), None) if name else None
        return {"connected": bool(name) if at else None, "signal": signal}

    async def _connected_now(self, timeout: float = NAME_TIMEOUT_S) -> str | None:
        try:
            reply = await self.rec.request("get_connect_wifi_name", timeout=max(1.0, timeout))
        except asyncio.TimeoutError:
            self._no_name_at = time.time()  # the robot does not answer when no network is connected
            return None
        except Exception as exc:            # no route, a refused read: say so instead of reporting "no network"
            raise WriteRefused(f"could not ask which network the robot is on ({exc})")
        return parse_connected(reply)

    async def _fresh_scan(self) -> list[dict]:
        """One fresh scan (under _scan_lock): an answer that comes back too soon is an earlier ask's, so ask again."""
        for _ in range(2):
            asked = time.time()
            try:
                reply = await self.rec.request("get_wifi_list", timeout=SCAN_TIMEOUT_S)
            except asyncio.TimeoutError:
                raise WriteRefused(f"the robot did not answer the scan in {SCAN_TIMEOUT_S} s")
            except Exception as exc:
                raise WriteRefused(f"could not ask the robot to scan ({exc})")
            if time.time() - asked < FRESH_SCAN_S:
                continue
            rows = parse_list(reply.get("data")) if reply.get("state") == 0 else None
            if rows is None:
                raise WriteRefused(f"the scan failed ({reply.get('msg') or 'state ' + str(reply.get('state'))})")
            return rows
        raise WriteRefused("only an earlier scan's answer came back; scan again")

    def _scan_block(self) -> str | None:
        if self.rec._surveying():
            return "the Wi-Fi survey is scanning during this run; its scans show here as they come"
        if self.rec.run_id is not None or self.rec.plan_active() is not False:
            return "not while a plan may be running (a scan can stall the robot's link)"
        if self._scan_lock.locked():
            return "a scan is already running"
        return None

    async def scan(self) -> dict:
        """A fresh scan and, if the robot answers, the connected network's name. One at a time, never during a plan."""
        why = self._scan_block()
        if why:
            raise WriteRefused(why)
        async with self._scan_lock:
            await self._fresh_scan()
            try:
                await self._connected_now()
            except WriteRefused:
                pass                         # the list is in; the name shows the next time it is read
        return self.status()

    # -- writing -----------------------------------------------------------------------------------------------------
    def _known(self, name: str) -> dict | None:
        return next((n for n in self.status()["wifi"]["networks"] or [] if n["name"] == name), None)

    @staticmethod
    def _held_gate(gate) -> None:
        why = gate() if gate else None
        if why:
            raise WriteRefused(why)

    async def join(self, name, password, gate=None) -> dict:
        name = name if isinstance(name, str) else ""
        password = password if isinstance(password, str) else ""
        if not name.strip() or len(name.encode()) > NAME_MAX:
            raise ValueError("choose a network from the scan")
        net = self._known(name)
        if net is None:
            raise ValueError("that network is not in the latest scan; scan again first")
        if not net["security"]:
            raise ValueError("joining an open network is not supported yet (the app's command for it was not captured)")
        check_password(password)
        async with self.writer.lock:
            self._held_gate(gate)
            r = await self.writer._send(JOIN, {"wifi_name": name, "password": password})
            if not r["sent"]:
                raise WriteRefused(f"not sent: {r.get('error')}")
            if r.get("state") not in (None, 0):
                self.store.event("wifi_join_refused", f"the robot refused to join a Wi-Fi network: "
                                                      f"{r.get('msg') or r.get('state')}")
                return {"ok": False, "error": r.get("msg") or f"the robot answered state {r.get('state')}"}
            now = None
            deadline = time.time() + VERIFY_S
            while (left := deadline - time.time()) > 0:
                try:
                    now = await self._connected_now(timeout=min(NAME_TIMEOUT_S, left))
                except WriteRefused:        # the link can drop for a moment while the robot changes network
                    now = None
                if now == name or deadline - time.time() <= VERIFY_EVERY_S:
                    break
                await asyncio.sleep(VERIFY_EVERY_S)
        ok = now == name
        self.store.event("wifi_joined" if ok else "wifi_join_unconfirmed",
                         "the robot joined the chosen Wi-Fi network" if ok else
                         f"asked the robot to join a Wi-Fi network; after {VERIFY_S} s it reports "
                         + ("another network" if now else "no Wi-Fi network"))
        return {"ok": ok, "connected": now, **({} if ok else {"error": f"the robot is on {now or 'no Wi-Fi'}"})}

    async def forget(self, name, gate=None) -> dict:
        name = name if isinstance(name, str) else ""
        net = self._known(name)
        if not name.strip() or not ((net and net["saved"]) or name == self.connected()[0]):
            raise ValueError("choose a saved network from the scan")
        async with self.writer.lock:
            self._held_gate(gate)
            r = await self.writer._send(FORGET, {"wifi_name": name})
            if not r["sent"]:
                raise WriteRefused(f"not sent: {r.get('error')}")
            if r.get("state") not in (None, 0):
                self.store.event("wifi_forget_refused", f"the robot refused to forget a Wi-Fi network: "
                                                        f"{r.get('msg') or r.get('state')}")
                return {"ok": False, "error": r.get("msg") or f"the robot answered state {r.get('state')}"}
            gone = None
            async with self._scan_lock:
                try:
                    after = await self._fresh_scan()
                    row = next((n for n in after if n["name"] == name), None)
                    gone = None if row is None else not row["saved"]
                except WriteRefused:         # no answer or no route: the forget was sent, its result is unknown
                    pass
        what = {True: "forgotten", False: "still saved on the robot",
                None: "sent, but a fresh scan could not show whether it is still saved"}[gone]
        self.store.event("wifi_forgotten" if gone else "wifi_forget_unconfirmed", f"a saved Wi-Fi network: {what}")
        return {"ok": bool(gone), **({} if gone else {"error": what})}
