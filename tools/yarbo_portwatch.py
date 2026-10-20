"""Watch the Yarbo's TCP ports and print every change, with timestamps. Connect-only (no data sent).

    YARBO_HOST=<robot ip> python tools/yarbo_portwatch.py [MINUTES] [INTERVAL_S]

Polls a focused set of likely service/video ports every INTERVAL_S (default 5). A full 1-65535 sweep
runs once at start and every 3 minutes.
"""
import asyncio
import os
import sys
import time
from datetime import datetime

HOST = os.environ.get("YARBO_HOST", "").strip()
if not HOST:
    sys.exit("set YARBO_HOST (the robot's LAN address) first")
MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 else 20
INTERVAL = float(sys.argv[2]) if len(sys.argv) > 2 else 5
FOCUS = sorted(set(
    [22, 80, 443, 554, 1883, 1935, 1984, 3478, 5349, 8443, 8554, 8555, 8883, 18554]
    + list(range(5000, 5011)) + list(range(7000, 7011)) + list(range(8000, 8201))
    + list(range(8800, 8901)) + list(range(9000, 9101)) + list(range(10000, 10011))
))


async def probe(port, sem, out):
    async with sem:
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection(HOST, port), 0.6)
            out.add(port)
            w.close()
        except Exception:
            pass


async def scan(ports, conc):
    sem, out = asyncio.Semaphore(conc), set()
    await asyncio.gather(*(probe(p, sem, out) for p in ports))
    return out


async def main():
    end = time.time() + MINUTES * 60
    known = await scan(range(1, 65536), 600)
    print(f"{datetime.now():%H:%M:%S} baseline (full sweep): {sorted(known)}", flush=True)
    last_full = time.time()
    while time.time() < end:
        await asyncio.sleep(INTERVAL)
        full = time.time() - last_full > 180
        now = await scan(range(1, 65536) if full else FOCUS, 600 if full else 200)
        if full:
            last_full = time.time()
        opened = sorted(now - known)
        closed = sorted((known - now) if full else (known & set(FOCUS)) - now)
        if opened or closed:
            print(f"{datetime.now():%H:%M:%S} {'FULL ' if full else ''}OPENED {opened}  CLOSED {closed}", flush=True)
        known = (known - set(closed)) | set(opened)
    print(f"{datetime.now():%H:%M:%S} done; final open set {sorted(known)}", flush=True)


asyncio.run(main())
