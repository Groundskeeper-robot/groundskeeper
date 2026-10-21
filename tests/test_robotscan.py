"""Finding the robot on the network (app/robotscan.py): which network is scanned, and what a scan reports. No sockets:
the connect scan and the heartbeat listener are replaced."""
import asyncio

import pytest

from app import robotscan


def test_the_scan_covers_the_panels_own_slash_24():
    got = robotscan.local_prefix("192.0.2.57", container=False)
    assert got == {"ok": True, "address": "192.0.2.57", "network": "192.0.2.0/24"}


def test_a_container_on_dockers_bridge_is_told_to_type_the_address():
    got = robotscan.local_prefix(str(robotscan.DOCKER_RANGE[2]), container=True)
    assert not got["ok"] and "bridge network" in got["reason"]


def test_a_public_address_is_never_scanned():
    got = robotscan.local_prefix("8.8.8.8", container=False)
    assert not got["ok"]


@pytest.mark.parametrize("bad", ["8.8.8.0/24", "192.0.2.0/16", "fe80::/64", "nonsense"])
def test_a_typed_network_must_be_private_and_small(bad):
    with pytest.raises(ValueError):
        robotscan.checked_network(bad)


def test_a_typed_network_is_normalised():
    assert robotscan.checked_network(" 192.0.2.77/24 ") == "192.0.2.0/24"


def test_scan_reports_the_robot_and_counts_other_brokers(monkeypatch):
    async def fake_open(network, port):
        assert network == "192.0.2.0/24" and port == 1883
        return ["192.0.2.10", "192.0.2.20"]

    def fake_sn(host, port, timeout):
        if host == "192.0.2.20":
            return "YB0000DEMO0001"
        raise RuntimeError("no heart_beat")

    monkeypatch.setattr(robotscan, "open_hosts", fake_open)
    monkeypatch.setattr(robotscan, "discover_sn", fake_sn)
    got = asyncio.run(robotscan.scan(1883, "192.0.2.0/24"))
    assert got["found"] == [{"host": "192.0.2.20", "port": 1883, "sn": "YB0000DEMO0001"}]
    assert got["brokers"] == 2 and got["note"] is None


def test_scan_with_no_robot_says_what_it_saw(monkeypatch):
    async def fake_open(network, port):
        return ["192.0.2.10"]

    monkeypatch.setattr(robotscan, "open_hosts", fake_open)
    monkeypatch.setattr(robotscan, "discover_sn", lambda *a: (_ for _ in ()).throw(RuntimeError("quiet")))
    got = asyncio.run(robotscan.scan(1883, "192.0.2.0/24"))
    assert got["found"] == [] and "no robot answered" in got["note"] and "1 other MQTT broker" in got["note"]


def test_scan_without_a_usable_network_explains(monkeypatch):
    monkeypatch.setattr(robotscan, "local_prefix", lambda: {"ok": False, "reason": "no route"})
    got = asyncio.run(robotscan.scan())
    assert got == {"network": None, "brokers": 0, "found": [], "note": "no route"}


def test_neighbours_reads_the_arp_table(tmp_path):
    arp = tmp_path / "arp"
    arp.write_text("IP address       HW type     Flags       HW address            Mask     Device\n"
                   "192.0.2.10       0x1         0x2         aa:bb:cc:dd:ee:01     *        eth0\n"
                   "192.0.2.11       0x1         0x0         00:00:00:00:00:00     *        eth0\n", encoding="ascii")
    assert robotscan.neighbours(str(arp)) == {"192.0.2.10"}
    assert robotscan.neighbours(str(tmp_path / "missing")) is None


def test_only_quiet_hosts_that_are_really_there_are_retried(monkeypatch):
    tried = []

    async def fake_pass(hosts, port, timeout, parallel):
        tried.append((timeout, list(hosts)))
        if timeout == robotscan.SWEEP[0]:
            return ["192.0.2.5"], ["192.0.2.9", "192.0.2.10"]
        return ["192.0.2.10"], []

    monkeypatch.setattr(robotscan, "_pass", fake_pass)
    monkeypatch.setattr(robotscan, "neighbours", lambda: {"192.0.2.10"})
    got = asyncio.run(robotscan.open_hosts("192.0.2.0/24", 1883))
    assert got == ["192.0.2.5", "192.0.2.10"] and tried[1] == (robotscan.RETRY[0], ["192.0.2.10"])
