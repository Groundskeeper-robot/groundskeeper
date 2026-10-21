"""Find the robot on the local network: Settings → Robot → Find it on my network.

Scans the /24 the panel's own address is in for an open MQTT port, then listens on each open broker for a Yarbo
heartbeat (`snowbot/+/device/heart_beat`, the same subscription `transport_local.discover_sn` uses). It only
connects and subscribes: nothing is ever published, so it is as read-only as the rest of the local transport.
Other MQTT brokers on the network (Home Assistant's, Zigbee2MQTT's) are connected to briefly and give no heartbeat.

In a container on Docker's bridge network the panel's own address is the bridge's, not the home network's, so the
scan would look in the wrong place; `local_prefix` says so and the user types the address instead (or runs the
container with host networking, which the bundled compose files do).
"""
import asyncio
import ipaddress
import os
import socket

from .transport_local import discover_sn

SWEEP = (0.6, 48)           # first pass over the whole /24: (connect timeout s, connects at once)
RETRY = (8.0, 32)           # second pass, only for quiet hosts that are really there (see neighbours); a robot
                            # on HaLow can take several seconds to answer a connect
HEARTBEAT_WAIT_S = 8        # the robot sends a heartbeat every few seconds
DOCKER_RANGE = ipaddress.IPv4Network((0xAC100000, 12))   # 172.16/12, where Docker's bridge networks live


def own_address() -> str | None:
    """The address this host uses to reach other networks. A UDP connect sends no packet; it only picks a route."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))           # TEST-NET-1: never contacted, only routed
            return s.getsockname()[0]
        except OSError:
            return None


def in_container() -> bool:
    return os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")


def local_prefix(address: str | None = None, container: bool | None = None) -> dict:
    """The /24 to scan, or why there is none."""
    address = address if address is not None else own_address()
    container = in_container() if container is None else container
    if not address:
        return {"ok": False, "reason": "this host has no network route"}
    ip = ipaddress.ip_address(address)
    if not ip.is_private or ip.is_loopback:
        return {"ok": False, "reason": f"{address} is not a home network address"}
    if container and ip in DOCKER_RANGE:
        return {"ok": False, "address": address,
                "reason": "the panel runs on Docker's bridge network and can't see your home network from there: "
                          "type the robot's address, or run the container with host networking"}
    net = ipaddress.ip_network(f"{address}/24", strict=False)
    return {"ok": True, "address": address, "network": str(net)}


async def _try(host: str, port: int, timeout: float, sem: asyncio.Semaphore) -> str:
    """'open', 'closed' (refused: something is there, no broker) or 'quiet' (no answer in time)."""
    async with sem:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        except asyncio.TimeoutError:
            return "quiet"
        except OSError:
            return "closed"
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return "open"


def neighbours(path: str = "/proc/net/arp") -> set[str] | None:
    """Addresses the host has a link-layer neighbour for (Linux; the container shares it with host networking). None
    when the table can't be read, e.g. on Windows or macOS."""
    try:
        with open(path, encoding="ascii") as f:
            rows = f.read().splitlines()[1:]
    except OSError:
        return None
    return {r.split()[0] for r in rows if len(r.split()) >= 4 and r.split()[2] != "0x0"}


async def open_hosts(network: str, port: int) -> list[str]:
    """Hosts with the port open. A quick sweep, then a slower retry of the hosts that stayed quiet but are really
    there (in the neighbour table): a robot behind a wireless bridge can miss the sweep while dozens of connects and
    their ARP lookups are in flight. Without a neighbour table every quiet host is retried."""
    hosts = [str(h) for h in ipaddress.ip_network(network).hosts()]
    found, quiet = await _pass(hosts, port, *SWEEP)
    near = neighbours()
    retry = [h for h in quiet if near is None or h in near]
    more, _ = await _pass(retry, port, *RETRY)
    return sorted(found + more, key=lambda h: ipaddress.ip_address(h))


async def _pass(hosts: list[str], port: int, timeout: float, parallel: int) -> tuple[list[str], list[str]]:
    sem = asyncio.Semaphore(parallel)
    res = await asyncio.gather(*(_try(h, port, timeout, sem) for h in hosts))
    return [h for h, r in zip(hosts, res) if r == "open"], [h for h, r in zip(hosts, res) if r == "quiet"]


async def identify(host: str, port: int) -> dict | None:
    """The robot's serial if `host` is a Yarbo broadcasting its heartbeat, else None."""
    try:
        sn = await asyncio.to_thread(discover_sn, host, port, HEARTBEAT_WAIT_S)
    except Exception:
        return None
    return {"host": host, "port": port, "sn": sn}


def checked_network(network: str) -> str:
    """A network the user typed: a private one, no bigger than a /24 (a scan never leaves the home network)."""
    net = ipaddress.ip_network(network.strip(), strict=False)
    if net.version != 4 or not net.is_private or net.prefixlen < 24:
        raise ValueError("give a private network no bigger than a /24, like 192.0.2.0/24")
    return str(net)


async def scan(port: int = 1883, network: str | None = None) -> dict:
    where = {"ok": True, "network": checked_network(network)} if network else local_prefix()
    if not where.get("ok"):
        return {"network": None, "brokers": 0, "found": [], "note": where["reason"]}
    brokers = await open_hosts(where["network"], port)
    found = [r for r in await asyncio.gather(*(identify(h, port) for h in brokers)) if r]
    note = None if found else (
        f"no robot answered on {where['network']}" + (f" ({len(brokers)} other MQTT broker{'s' if len(brokers) != 1 else ''} found)"
                                                       if brokers else "")
        + ". Is it powered on and on this network?")
    return {"network": where["network"], "brokers": len(brokers), "found": found, "note": note}
