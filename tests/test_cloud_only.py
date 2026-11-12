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
    c.username = "user@example.com"
    assert c.refresh_session() == "expired"
    assert c.state == "needs login" and c.error


def test_the_worker_stops_with_a_reason_when_the_session_is_gone(tmp_path):
    c = cloud(tmp_path, None, sn=SN)
    c.username = "user@example.com"
    c._run(threading.Event())                   # returns at once: nothing to connect with
    assert c.status()["state"] == "needs login" and c.status()["error"]


def test_a_sign_out_racing_the_worker_stays_signed_out(tmp_path):
    c = cloud(tmp_path, None, sn=SN)            # logout() cleared the session and the username
    c.state = "connecting"                       # the worker got past its stop check first
    assert c.refresh_session() == "expired"
    assert c.state == "signed out" and c.error == ""


def test_a_session_the_sdk_refreshes_during_the_lookup_is_saved(tmp_path):
    import json

    class Rotating(FakeSDK):
        def get_devices(self):                   # a 401: the SDK refreshes (and rotates) by itself, then lists
            self.token, self.refresh_token = "jwt.new", "refresh.new"
            return super().get_devices()

    c = cloud(tmp_path, Rotating([SN]))
    assert c._find_sn() == SN
    assert json.loads(c.path.read_text())["refresh_token"] == "refresh.new"


def test_an_expired_session_during_the_lookup_says_needs_login(tmp_path):
    from yarbo_robot_sdk.exceptions import TokenExpiredError
    c = cloud(tmp_path, FakeSDK(fail=TokenExpiredError("refresh token expired")))
    c._run(CountingStop(3))
    assert c.state == "needs login" and "expired" in c.error


def test_a_fresh_sign_in_starts_a_new_worker_without_waiting_for_the_old_one(tmp_path, monkeypatch):
    c = cloud(tmp_path, None, sn=SN)
    # The old worker is slow to notice the stop (as when it sits in _connect_once for up to 20 s).
    old = threading.Thread(target=lambda: time.sleep(3), daemon=True)
    old.start()
    c._thread = old
    started = threading.Event()
    monkeypatch.setattr(c, "_run", lambda stop: (started.set(), stop.wait(5)))

    def sdk():
        if c._client is None:
            c._client = FakeSDK()
        return c._client
    monkeypatch.setattr(c, "_sdk", sdk)
    t0 = time.time()
    c.login("user@example.com", "synthetic-password")
    assert c._thread is not old and started.wait(2) and time.time() - t0 < 2
    c.stop()


def test_a_running_worker_is_not_doubled(tmp_path, monkeypatch):
    c = cloud(tmp_path, None, sn=SN)
    monkeypatch.setattr(c, "_run", lambda stop: stop.wait(5))
    c.start()
    first = c._thread
    c.start()
    assert c._thread is first
    c.stop()


def test_a_stopped_worker_leaves_the_status_to_the_next_one(tmp_path, monkeypatch):
    c = cloud(tmp_path, FakeSDK(), sn=SN)
    stop = threading.Event()

    def connect(s):                              # a sign-in or restart lands while this worker is connecting
        c.state, c.error = "connected", ""       # what the new worker reports
        s.set()
        raise ConnectionError("stopped while connecting")
    monkeypatch.setattr(c, "_connect_once", connect)
    c._run(stop)
    assert c.state == "connected" and c.error == ""


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


def _kept(store):
    from app.runtime import found_sn
    return found_sn(store)


def _hears(monkeypatch, by_host):
    monkeypatch.setattr(recmod, "discover_sn", lambda host, port, timeout: by_host[host])
    monkeypatch.setattr(recmod, "LocalTransport", QuietTransport)


def test_a_serial_from_the_heart_beat_survives_a_restart(tmp_path, monkeypatch):
    o, env, store = _ops(tmp_path, monkeypatch)
    o.cfg.set("robot", {"host": "192.0.2.50"})
    _hears(monkeypatch, {"192.0.2.50": SN})
    run(o.rec.apply_robot())
    assert o.rec.sn == SN and o.cloud.sn == SN
    assert _kept(store) == {"sn": SN, "from": "heart_beat"}       # a restart reads it back (app/main.py)


def test_a_serial_from_the_account_is_kept_and_followed(tmp_path, monkeypatch):
    o, env, store = _ops(tmp_path, monkeypatch)

    async def go():
        o._loop = asyncio.get_running_loop()
        o.cloud.sn = SN                          # the worker sets its own serial, then tells Ops
        o._cloud_found_sn(SN)
        await asyncio.sleep(0)
    run(go())
    assert o.rec.sn == SN and _kept(store) == {"sn": SN, "from": "account"}


def test_the_heart_beat_wins_over_the_account(tmp_path, monkeypatch):
    o, env, store = _ops(tmp_path, monkeypatch)
    assert o.rec.use_found_sn(SN2)               # the account spoke first
    o.cfg.set("robot", {"host": "192.0.2.50"})
    _hears(monkeypatch, {"192.0.2.50": SN})
    run(o.rec.apply_robot())
    assert o.rec.sn == SN and o.cloud.sn == SN and _kept(store) == {"sn": SN, "from": "heart_beat"}
    assert not o.rec.use_found_sn(SN2)           # and the account never takes it back
    assert o.rec.sn == SN and _kept(store)["sn"] == SN


def test_another_robot_at_a_new_address_is_found_again(tmp_path, monkeypatch):
    o, env, store = _ops(tmp_path, monkeypatch)
    _hears(monkeypatch, {"192.0.2.50": SN, "192.0.2.60": SN2})
    o.cfg.set("robot", {"host": "192.0.2.50"})
    run(o.rec.apply_robot())
    o.cfg.set("robot", {"host": "192.0.2.60"})
    run(o.rec.apply_robot())
    assert o.rec.sn == SN2 and o.rec.transport.sn == SN2 and _kept(store)["sn"] == SN2


def test_a_closed_local_broker_still_gives_the_cloud_the_kept_serial(tmp_path, monkeypatch):
    from app.runtime import FOUND_SN
    o, env, store = _ops(tmp_path, monkeypatch)
    store.set_setting(FOUND_SN, {"sn": SN, "from": "heart_beat"})
    o.cfg.set("robot", {"host": "192.0.2.50"})

    def refused(host, port, timeout):
        raise ConnectionRefusedError("refused")
    monkeypatch.setattr(recmod, "discover_sn", refused)
    run(o.rec.apply_robot())
    assert o.rec.transport is None and o.rec.sn == SN and o.cloud.sn == SN


def test_a_serial_once_typed_in_settings_is_moved_and_never_typed_again(tmp_path, monkeypatch):
    import pytest
    from app.runtime import Runtime
    o, env, store = _ops(tmp_path, monkeypatch)
    store.set_setting("integrations", {"robot": {"host": "192.0.2.50", "sn": SN}})
    cfg = Runtime(env, store)
    assert _kept(store) == {"sn": SN, "from": "settings"}
    assert store.setting("integrations") == {"robot": {"host": "192.0.2.50"}} and cfg.yarbo_host == "192.0.2.50"
    assert "sn" not in cfg.describe()["robot"]
    with pytest.raises(ValueError):
        cfg.set("robot", {"sn": SN2})


def test_only_a_robot_the_account_listed_can_be_chosen(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from app import main
    o, env, store = _ops(tmp_path, monkeypatch)
    o.cloud.devices = [{"sn": SN, "name": "", "model": ""}, {"sn": SN2, "name": "", "model": ""}]
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(rec=o.rec, ops=o)))
    try:
        run(main.robot_from_account(req, {"sn": "YB0000DEMO0099"}))
        raise AssertionError("a serial not on the account was taken")
    except HTTPException as exc:
        assert exc.status_code == 400
    assert run(main.robot_from_account(req, {"sn": SN2})) == {"ok": True}
    assert o.rec.sn == SN2 and o.cloud.sn == SN2 and _kept(store) == {"sn": SN2, "from": "account"}


def test_a_lookup_that_ends_after_a_stop_writes_nothing(tmp_path):
    """A stopped worker's late lookup must not overwrite the next worker's status or robot list."""
    stop = threading.Event()
    sdk = FakeSDK([SN, SN2])

    def stopped_meanwhile():
        stop.set()                              # signed out, or a new worker started, while the account answered
        return FakeSDK.get_devices(sdk)
    sdk.get_devices = stopped_meanwhile
    c = cloud(tmp_path, sdk)
    c.error = "before"
    c._run(stop)
    assert c.devices == [] and c.error == "before" and c.status()["devices"] == []


def test_a_rotated_session_that_cannot_be_saved_keeps_the_worker_going(tmp_path, monkeypatch, caplog):
    sdk = FakeSDK([SN, SN2])

    def rotate_then_list():
        sdk.token, sdk.refresh_token = "jwt.rotated", "refresh.rotated"
        return FakeSDK.get_devices(sdk)
    sdk.get_devices = rotate_then_list
    c = cloud(tmp_path, sdk)
    c.username = "user@example.com"

    def disk_full():
        raise OSError("no space left on device")
    monkeypatch.setattr(c, "_save", disk_full)
    stop = CountingStop(3)
    with caplog.at_level(logging.WARNING, logger="app.cloud"):
        c._run(stop)                            # no exception escapes the worker: it goes on waiting
    assert stop.waits == 3 and sdk.refresh_token == "refresh.rotated"
    assert any("could not save the Yarbo session" in r.getMessage() for r in caplog.records)


def test_another_robot_is_not_followed_while_a_run_is_open(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from app import main
    o, env, store = _ops(tmp_path, monkeypatch)
    assert o.rec.use_found_sn(SN)
    o.rec.run_id = 7                            # robot A is mid-run
    o.cloud.devices = [{"sn": SN, "name": "", "model": ""}, {"sn": SN2, "name": "", "model": ""}]
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(rec=o.rec, ops=o)))
    try:
        run(main.robot_from_account(req, {"sn": SN2}))
        raise AssertionError("switched robots during a run")
    except HTTPException as exc:
        assert exc.status_code == 409
    assert not o.rec.use_found_sn(SN2)
    assert o.rec.sn == SN and _kept(store)["sn"] == SN       # B's telemetry can't land in A's run
    o.rec.run_id = None
    assert o.rec.use_found_sn(SN2) and o.rec.sn == SN2      # once the run has ended


def test_a_demo_leaves_a_real_installs_typed_serial_alone(tmp_path, monkeypatch):
    from dataclasses import replace
    from app import config
    from app.runtime import Runtime
    from app.store import Store
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    store = Store(str(tmp_path))
    store.set_setting("integrations", {"robot": {"host": "192.0.2.50", "sn": SN}})
    Runtime(replace(config.load(), demo=True), store)
    assert store.setting("integrations") == {"robot": {"host": "192.0.2.50", "sn": SN}} and _kept(store) == {}
