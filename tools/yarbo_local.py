"""Read-only client for the Yarbo's on-board MQTT broker (stdlib only, no paho).

Sends only *request* commands (get_device_msg, get_map, read_gps_ref, read_all_plan) and never
get_controller or any action, so it cannot move the robot or take control from the phone app.

    python yarbo_local.py status            # full device snapshot (flattened)
    python yarbo_local.py map [--raw FILE]  # zones/no-go/pathways summary; --raw saves decoded JSON
    python yarbo_local.py plans
    python yarbo_local.py gpsref            # prints the map's lat/lon origin, so keep the output local
    python yarbo_local.py acl TOPIC...      # SUBACK result per topic (granted / REFUSED)

Set YARBO_HOST (the robot's LAN address) and YARBO_SN (its serial, as in its MQTT topics) first.

Protocol notes and findings: ../docs/dev/protocol.md
"""
import base64
import json
import os
import socket
import sys
import time
import zlib

HOST = os.environ.get("YARBO_HOST", "").strip()
SN = os.environ.get("YARBO_SN", "").strip()


def _varint(n):
    out = bytearray()
    while True:
        b = n % 128
        n //= 128
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _s16(b):
    return len(b).to_bytes(2, "big") + b


def _pkt(t, body):
    return bytes([t]) + _varint(len(body)) + body


class Broker:
    def __init__(self, host=HOST):
        self.s = socket.create_connection((host, 1883), timeout=5)
        cid = f"ro-{int(time.time())}".encode()
        self.s.sendall(_pkt(0x10, _s16(b"MQTT") + bytes([4, 2, 0, 60]) + _s16(cid)))
        _, d = self.read()
        if d[1] != 0:
            raise ConnectionRefusedError(f"CONNACK rc={d[1]}")

    def _rx(self, n):
        d = b""
        while len(d) < n:
            c = self.s.recv(n - len(d))
            if not c:
                raise EOFError("broker closed the connection")
            d += c
        return d

    def read(self):
        h = self._rx(1)[0]
        mult, n = 1, 0
        while True:
            b = self._rx(1)[0]
            n += (b & 127) * mult
            mult *= 128
            if not b & 128:
                break
        return h, self._rx(n)

    def subscribe(self, *topics):
        body = (1).to_bytes(2, "big") + b"".join(_s16(t.encode()) + b"\x00" for t in topics)
        self.s.sendall(_pkt(0x82, body))
        while True:
            h, d = self.read()
            if h >> 4 == 9:
                return list(d[2:])  # 0 = granted, 128 = refused

    def request(self, cmd, payload=None, timeout=10):
        """Publish snowbot/{SN}/app/{cmd} (zlib JSON) and return the matching data_feedback reply."""
        self.subscribe(f"snowbot/{SN}/device/data_feedback")
        self.s.sendall(_pkt(0x30, _s16(f"snowbot/{SN}/app/{cmd}".encode())
                            + zlib.compress(json.dumps(payload or {}).encode())))
        self.s.settimeout(1)
        end = time.time() + timeout
        while time.time() < end:
            try:
                h, d = self.read()
            except socket.timeout:
                continue
            if h >> 4 != 3:
                continue
            tl = int.from_bytes(d[:2], "big")
            p = d[2 + tl + (2 if (h >> 1) & 3 else 0):]
            try:
                p = zlib.decompress(p)
            except zlib.error:
                pass
            try:
                j = json.loads(p)
            except ValueError:
                continue
            if j.get("topic") == cmd:
                return j
        raise TimeoutError(f"no reply to {cmd} in {timeout}s")

    def close(self):
        try:
            self.s.sendall(b"\xe0\x00")
        finally:
            self.s.close()


def decode_map(reply):
    """get_map's data is base64(zlib(JSON)). Points are local x/y metres from each feature's ref lat/lon."""
    return json.loads(zlib.decompress(base64.b64decode(reply["data"])))


def _flat(o, pre=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _flat(v, f"{pre}{k}.")
    else:
        yield pre[:-1], o


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if not HOST or not SN:
        sys.exit("set YARBO_HOST (the robot's LAN address) and YARBO_SN (its serial) first")
    b = Broker()
    try:
        if cmd == "status":
            for k, v in _flat(b.request("get_device_msg")["data"]):
                print(f"{k} = {str(v)[:100]}")
        elif cmd == "map":
            m = decode_map(b.request("get_map"))
            if "--raw" in argv:
                json.dump(m, open(argv[argv.index("--raw") + 1], "w"), indent=1)
            for kind, feats in m.items():
                if isinstance(feats, list):
                    print(f"{kind}: {len(feats)}")
                    for f in feats:
                        pts = len(f["range"]) if isinstance(f.get("range"), list) else "-"
                        print(f"   id={f.get('id')} name={f.get('name')!r} points={pts}")
        elif cmd == "plans":
            for p in b.request("read_all_plan")["data"]["data"]:
                print(f"id={p['id']} name={p['name']!r} areas={p['areaIds']}")
        elif cmd == "gpsref":
            print(json.dumps(b.request("read_gps_ref")["data"], indent=1))
        elif cmd == "acl":
            for t, c in zip(argv[2:], b.subscribe(*argv[2:])):
                print(f"{c:>3} {'REFUSED' if c == 0x80 else 'granted'}  {t}")
        else:
            sys.exit(__doc__)
    finally:
        b.close()


if __name__ == "__main__":
    main(sys.argv)
