"""Robots the panel can only reach through the Yarbo cloud (the local broker refuses connections): the serial comes
from the account's robot list, a found serial is kept across restarts, the wait is said plainly, and the cloud
worker survives a fresh sign-in. No network, no robot: the SDK client is a fake."""
import asyncio
import logging
import threading
import time
from types import SimpleNamespace

from app import recorder as recmod
from app.cloud import CloudTransport

SN = "YB0000DEMO0001"
SN2 = "YB0000DEMO0002"


def run(coro):
    return asyncio.run(coro)


class FakeSDK:
    """The slice of YarboClient the cloud transport uses for a sign-in and the account's robot list."""

    def __init__(self, robots=(), fail=None):
        self.robots, self.fail = list(robots), fail
        self.token, self.refresh_token = "jwt.synthetic", "refresh.synthetic"
        self.listed = 0
        self._auth = SimpleNamespace(refresh=lambda: None)

    def login(self, email, password):
        pass

    def get_devices(self):
        self.listed += 1
        if self.fail:
            raise self.fail
        return [SimpleNamespace(sn=sn, name=f"Robot {i}", model="YB-DEMO", online=True)
                for i, sn in enumerate(self.robots, 1)]


class CountingStop(threading.Event):
    """A stop event whose waits return at once and that sets itself after `n` waits, so _run's loop is driven
    without sleeping."""

    def __init__(self, n):
        super().__init__()
        self.n, self.waits = n, 0

    def wait(self, timeout=None):
        self.waits += 1
        if self.waits >= self.n:
            self.set()
        return self.is_set()


def cloud(tmp_path, sdk=None, sn=None, on_sn=None):
    c = CloudTransport(str(tmp_path), sn, lambda *a: None, on_sn=on_sn)
    c._client = sdk
    return c


# -- the serial from the account ------------------------------------------------------------------------------------
def test_the_accounts_only_robot_gives_the_serial(tmp_path):
    found = []
    stop = threading.Event()
    c = cloud(tmp_path, FakeSDK([SN]), on_sn=lambda sn: (found.append(sn), stop.set()))
    c._run(stop)
    assert c.sn == SN and found == [SN] and c.status()["devices"] == []


def test_several_robots_on_the_account_are_offered_not_guessed(tmp_path):
    sdk = FakeSDK([SN, SN2])
    c = cloud(tmp_path, sdk, on_sn=lambda sn: (_ for _ in ()).throw(AssertionError("must not pick one")))
    c._run(CountingStop(3))
    assert c.sn is None and sdk.listed == 1                      # asked once, not on every wait
    assert [d["sn"] for d in c.status()["devices"]] == [SN, SN2]
    c.sn = SN                                                    # chosen in Settings → Robot
    assert c.status()["devices"] == []


def test_a_failed_robot_list_is_asked_again_after_a_minute(tmp_path):
    sdk = FakeSDK(fail=ConnectionError("offline"))
    c = cloud(tmp_path, sdk)
    assert c._find_sn() is None and sdk.listed == 1
    assert c._find_sn() is None and sdk.listed == 1              # not again straight away
    c._devices_at -= 61
    c._find_sn()
    assert sdk.listed == 2


def test_no_robot_list_without_a_session(tmp_path):
    c = cloud(tmp_path, None)
    c._sdk = lambda: (_ for _ in ()).throw(AssertionError("must not build the SDK client"))
    assert c._find_sn() is None


def test_waiting_for_the_serial_is_said_in_the_status_and_once_in_the_log(tmp_path, caplog):
    c = cloud(tmp_path, FakeSDK([]))
    with caplog.at_level(logging.INFO, logger="app.cloud"):
        c._run(CountingStop(4))
    assert c.status()["error"] == "waiting for the robot's serial (Settings → Robot)"
    said = [r for r in caplog.records if "waiting for the robot's serial" in r.getMessage()]
    assert len(said) == 1


# -- cloud.py bugs ---------------------------------------------------------------------------------------------------
def test_a_refresh_with_no_session_says_needs_login(tmp_path):
    c = cloud(tmp_path, None, sn=SN)
    assert c.refresh_session() == "expired"
    assert c.state == "needs login" and c.error


def test_the_worker_stops_with_a_reason_when_the_session_is_gone(tmp_path):
    c = cloud(tmp_path, None, sn=SN)
    c._run(threading.Event())                   # returns at once: nothing to connect with
    assert c.status()["state"] == "needs login" and c.status()["error"]


def test_a_fresh_sign_in_waits_for_the_old_worker_and_starts_a_new_one(tmp_path, monkeypatch):
    c = cloud(tmp_path, None, sn=SN)
    # The old worker takes a moment to notice the stop (as when it sits in _connect_once).
    old = threading.Thread(target=lambda: time.sleep(0.5), daemon=True)
    old.start()
    c._thread = old
    started = threading.Event()
    monkeypatch.setattr(c, "_run", lambda stop: (started.set(), stop.wait(5)))

    def sdk():
        if c._client is None:
            c._client = FakeSDK()
        return c._client
    monkeypatch.setattr(c, "_sdk", sdk)
    c.login("user@example.com", "synthetic-password")
    assert c._thread is not old and started.wait(2)
    c.stop()


# -- keeping a found serial ------------------------------------------------------------------------------------------
def _ops(tmp_path, monkeypatch):
    from app import config
    from app.imagery import Imagery
    from app.ops import Ops
    from app.recorder import Recorder
    from app.runtime import Runtime
    from app.store import Store

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("YARBO_SN", raising=False)
    monkeypatch.delenv("YARBO_HOST", raising=False)
    env = config.load()
    store = Store(env.data_dir)
    cfg = Runtime(env, store)
    return Ops(cfg, Recorder(cfg, store, Imagery(env.data_dir), None)), env, store


class QuietTransport:
    def __init__(self, host, port, sn, on_message):
        self.host, self.port, self.sn, self.connected = host, port, sn, False
        self.last_message_at, self.received, self.published, self.last_error = 0.0, 0, 0, ""

    def start(self):
        pass

    def stop(self):
        pass


def test_a_serial_from_the_heart_beat_survives_a_restart(tmp_path, monkeypatch):
    from app.runtime import Runtime
    o, env, store = _ops(tmp_path, monkeypatch)
    o.cfg.set("robot", {"host": "192.0.2.50"})
    monkeypatch.setattr(recmod, "discover_sn", lambda host, port, timeout: SN)
    monkeypatch.setattr(recmod, "LocalTransport", QuietTransport)
    run(o.rec.apply_robot())
    assert o.rec.sn == SN and o.cloud.sn == SN
    assert Runtime(env, store).yarbo_sn == SN                    # a restart reads it back


def test_a_serial_from_the_account_is_kept_and_followed(tmp_path, monkeypatch):
    from app.runtime import Runtime
    o, env, store = _ops(tmp_path, monkeypatch)

    async def go():
        o._loop = asyncio.get_running_loop()
        o.cloud.sn = SN                          # the worker sets its own serial, then tells Ops
        o._cloud_found_sn(SN)
        await asyncio.sleep(0)
    run(go())
    assert o.rec.sn == SN and Runtime(env, store).yarbo_sn == SN


def test_a_serial_already_set_is_left_alone(tmp_path, monkeypatch):
    o, env, store = _ops(tmp_path, monkeypatch)
    o.cfg.set("robot", {"sn": SN2})
    o.rec.keep_sn(SN)
    assert o.cfg.yarbo_sn == SN2
    o.cfg.set("robot", {"sn": False})            # switched off in Settings
    o.rec.keep_sn(SN)
    assert o.cfg.stored("robot", "sn") is False
