"""Yarbo cloud diagnostic, built on Yarbo's own SDK (`yarbo-data-sdk`).

    python yarbo_cloud.py login      # prompts for email (or YARBO_EMAIL) + password, saves the session, then diagnose
    python yarbo_cloud.py diagnose   # uses the saved session; never needs the password

The password is typed into this terminal only. It is RSA-encrypted by the SDK before it leaves the
machine and is never stored. Only the session tokens are saved, in ~/.yarbo/session.json (outside
the workspace and outside anything synced).

`diagnose` walks the same path the HA integration takes and reports where it breaks:
  1. /sdk/config: which brokers Yarbo's cloud currently advertises
  2. token refresh + which identity the token carries
  3. /devices: is the robot on this account, and as owner or shared (`user_type`)
  4. migration lookup: does Yarbo route this robot to the NEW or the LEGACY broker
  5. one MQTT CONNECT to EACH broker with the SDK's exact credential scheme (no retry loop)
  6. on any broker that accepts: a read-only get_device_msg / read_all_plan round trip
It never sends get_controller or any action command.
"""
import base64
import getpass
import json
import os
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt
from yarbo_robot_sdk import YarboClient
from yarbo_robot_sdk.codec import decode_mqtt_payload, encode_mqtt_payload
from yarbo_robot_sdk.device_registry import resolve_control_topic

SESSION = Path.home() / ".yarbo" / "session.json"
DEFAULT_EMAIL = os.environ.get("YARBO_EMAIL", "").strip()  # optional: the account's login, else asked for


def save_session(client, username):
    SESSION.parent.mkdir(exist_ok=True)
    SESSION.write_text(json.dumps({
        "username": username,
        "token": client.token,
        "refresh_token": client.refresh_token,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }))


def jwt_claims(token):
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        return {}


def try_broker(label, host, port, tls, client_id, username, password, sn, type_id):
    """One CONNECT, no auto-reconnect. If accepted, do a read-only request round trip."""
    print(f"\n[{label}] {host}:{port} tls={tls}")
    connected = threading.Event()
    result = {}
    replies = {}

    def on_connect(c, userdata, flags, rc, props=None):
        result["rc"] = rc
        connected.set()

    def on_message(c, userdata, msg):
        try:
            data = decode_mqtt_payload(msg.payload)
        except Exception:
            data = {"_raw": msg.payload[:80]}
        key = data.get("topic") or msg.topic.rsplit("/", 1)[-1]
        replies.setdefault(key, data)

    c = mqtt.Client(client_id=client_id, callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    c.username_pw_set(username, password)
    if tls:
        c.tls_set()
    c.on_connect = on_connect
    c.on_message = on_message
    try:
        c.connect(host, port, 60)
    except Exception as exc:
        print(f"  TCP/TLS failed: {exc}")
        return False
    c.loop_start()
    try:
        if not connected.wait(15):
            print("  no CONNACK within 15 s")
            return False
        rc = result["rc"]
        ok = not rc.is_failure if hasattr(rc, "is_failure") else rc == 0
        print(f"  CONNACK: {rc}  ->  {'ACCEPTED' if ok else 'REJECTED'}")
        if not ok:
            return False
        c.subscribe([(f"snowbot/{sn}/device/data_feedback", 0),
                     (f"snowbot/{sn}/device/heart_beat", 0),
                     (f"snowbot/{sn}/device/DeviceMSG", 0)])
        time.sleep(1)
        for cmd in ("get_device_msg", "read_all_plan"):
            try:
                topic = resolve_control_topic(sn, type_id, cmd)
            except Exception:
                topic = f"snowbot/{sn}/app/{cmd}"
            c.publish(topic, encode_mqtt_payload({}))
        end = time.time() + 15
        while time.time() < end and not {"get_device_msg", "read_all_plan"} <= replies.keys():
            time.sleep(0.5)
        dm = replies.get("get_device_msg", {}).get("data", {})
        plans = replies.get("read_all_plan", {}).get("data", {})
        print(f"  get_device_msg: {'REPLY' if dm else 'no reply'}"
              + (f" (battery {dm.get('BatteryMSG', {}).get('capacity')}%, "
                 f"working_state {dm.get('StateMSG', {}).get('working_state')})" if dm else ""))
        print(f"  read_all_plan:  {'REPLY' if plans else 'no reply'}"
              + (f" {[p.get('name') for p in plans.get('data', [])]}" if plans else ""))
        print(f"  heart_beat seen: {'heart_beat' in replies}")
        return True
    finally:
        c.loop_stop()
        c.disconnect()


def diagnose():
    if not SESSION.exists():
        sys.exit(f"No saved session at {SESSION}. Run:  python yarbo_cloud.py login")
    sess = json.loads(SESSION.read_text())
    username = sess["username"].lower()

    client = YarboClient()
    cfg = client._config
    print("== 1. cloud config (/sdk/config)")
    for k in ("mqtt_host", "mqtt_port", "mqtt_use_tls", "new_mqtt_host", "new_mqtt_port", "new_mqtt_use_tls"):
        print(f"  {k} = {cfg.get(k)}")

    print("\n== 2. session")
    client.restore_session(username, sess["token"], sess["refresh_token"])
    try:
        client._auth.refresh()
        print("  token refresh: OK")
    except Exception as exc:
        print(f"  token refresh FAILED: {exc!r}  (run `login` again)")
        return
    save_session(client, username)  # refresh tokens rotate
    claims = jwt_claims(client.token)
    exp = claims.get("exp")
    print(f"  claims present: {sorted(claims)}")
    print(f"  iss={claims.get('iss')}  aud={str(claims.get('aud'))[:60]}")
    if exp:
        print(f"  expires {datetime.fromtimestamp(exp, timezone.utc).isoformat()}")
    for k in ("email", "https://yarbo.com/email", "username"):
        if k in claims:
            print(f"  claim {k} matches login: {str(claims[k]).lower() == username}")

    print("\n== 3. devices on this account")
    devices = client.get_devices()
    for d in devices:
        print(f"  sn={d.sn} name={d.name!r} model={d.model} type_id={d.type_id} "
              f"online={d.online} user_type={d.user_type!r}")
    if not devices:
        print("  NONE: this account has no robots bound to it")
        return
    dev = devices[0]

    print("\n== 4. migration routing")
    try:
        migrated = client._migration.query(dev.sn)
        print(f"  {dev.sn}: migrated={migrated}  ->  SDK would use the {'NEW' if migrated else 'LEGACY'} broker")
    except Exception as exc:
        migrated = None
        print(f"  migration query FAILED: {exc}")

    print("\n== 5/6. broker tests (one CONNECT each, then read-only requests if accepted)")
    rand4 = f"{secrets.randbelow(10000):04d}"
    try_broker("NEW broker, SDK scheme: user=email pass=JWT",
               cfg.get("new_mqtt_host"), int(cfg.get("new_mqtt_port")), bool(cfg.get("new_mqtt_use_tls")),
               f"HA_{username}_{rand4}", username, client.token, dev.sn, dev.type_id)
    if cfg.get("mqtt_host"):
        try_broker("LEGACY broker, SDK scheme: user=JWT pass=''",
                   cfg.get("mqtt_host"), int(cfg.get("mqtt_port")),
                   bool(cfg.get("mqtt_use_tls") if cfg.get("mqtt_use_tls") is not None else True),
                   f"HA_{username}_{int(time.time())}_{rand4}", client.token, "", dev.sn, dev.type_id)
    else:
        print("\n[LEGACY] no mqtt_host advertised by /sdk/config; skipped")


def login():
    prompt = f"Yarbo account email [{DEFAULT_EMAIL}]: " if DEFAULT_EMAIL else "Yarbo account email: "
    email = input(prompt).strip() or DEFAULT_EMAIL
    if not email:
        sys.exit("an account email is needed (type it, or set YARBO_EMAIL)")
    password = getpass.getpass("Yarbo password (not echoed): ")
    client = YarboClient()
    try:
        client.login(email, password)
    except Exception as exc:
        sys.exit(f"LOGIN FAILED: {exc}")
    finally:
        del password
    save_session(client, email.lower())
    print(f"Login OK. Session saved to {SESSION}\n")
    diagnose()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "login":
        login()
    elif cmd == "diagnose":
        diagnose()
    else:
        sys.exit(__doc__)
