"""The scheduler (spec §8). The panel owns scheduling; the robot keeps schedules only if the Yarbo app saves one, and
then they start without the run rules, so the panel reads them (robot_schedules) and warns, but never writes them.

A window is "plan P may run on these days between HH:MM and HH:MM". Inside a window the gates are evaluated every
minute; when they all pass the scheduler raises a pending start (notify-to-confirm by default). A window that ends
without passing is logged as skipped with the reasons. If the panel is down, nothing runs (the stated trade-off).
"""
import time
import uuid
from datetime import datetime, timedelta

from . import why

PENDING_TTL_S = 30 * 60
DAY_BITS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")    # read_schedules' week_day: bit 0 = Sunday (map-writes.md)


def robot_schedules(reply) -> list[dict] | None:
    """The robot's own schedules from a read_schedules reply (None = no usable answer). The panel never writes them
    (design §8.1): one saved in the Yarbo app starts mows without the run rules, so the Schedule page warns about it.
    Times are the robot's UTC "HH:MM:SS", shown as they are."""
    if not isinstance(reply, dict) or reply.get("state") not in (None, 0):
        return None
    data = reply.get("data")
    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        mask = it.get("week_day") if isinstance(it.get("week_day"), int) else 0
        out.append({"id": it.get("id"), "name": str(it.get("name") or ""), "enabled": bool(it.get("enable")),
                    "plan_id": it.get("plan_id"), "days": [d for i, d in enumerate(DAY_BITS) if mask >> i & 1],
                    "start_utc": it.get("start_time"), "end_utc": it.get("end_time")})
    return out
HEADS = {"mower": {3, 5}, "snow": {1}, "any": None}


def new_window(**kw) -> dict:
    w = {"id": uuid.uuid4().hex[:8], "name": "", "enabled": True, "days": [1, 4], "start": "09:00", "end": "14:00",
         "plan_id": None, "head": "mower", "auto_start": False}
    w.update({k: v for k, v in kw.items() if k in w})
    return w


def validate(w: dict) -> dict:
    out = new_window(**w)
    out["id"] = w.get("id") or out["id"]
    out["days"] = sorted({int(d) for d in out["days"] if 0 <= int(d) <= 6})
    for key in ("start", "end"):
        hh, mm = str(out[key]).split(":")
        if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
            raise ValueError(f"bad {key} time")
        out[key] = f"{int(hh):02d}:{int(mm):02d}"
    if out["start"] >= out["end"]:
        raise ValueError("a window must end after it starts (no overnight windows yet)")
    if out["head"] not in HEADS:
        raise ValueError("head must be mower, snow or any")
    out["plan_id"] = int(out["plan_id"]) if out["plan_id"] is not None else None
    out["enabled"], out["auto_start"] = bool(out["enabled"]), bool(out["auto_start"])
    return out


def active_occurrence(w: dict, now: datetime) -> str | None:
    """The date key (YYYY-MM-DD) of the window occurrence containing `now` (local time), or None."""
    if not w.get("enabled") or now.weekday() not in w["days"]:
        return None
    hhmm = now.strftime("%H:%M")
    return now.strftime("%Y-%m-%d") if w["start"] <= hhmm < w["end"] else None


def next_occurrence(w: dict, now: datetime) -> datetime | None:
    if not w.get("enabled") or not w["days"]:
        return None
    for d in range(0, 8):
        day = now + timedelta(days=d)
        if day.weekday() not in w["days"]:
            continue
        hh, mm = map(int, w["start"].split(":"))
        start = day.replace(hour=hh, minute=mm, second=0, microsecond=0)
        ehh, emm = map(int, w["end"].split(":"))
        end = day.replace(hour=ehh, minute=emm, second=0, microsecond=0)
        if end > now:
            return max(start, now) if start <= now else start
    return None


class Scheduler:
    def __init__(self, store):
        self.store = store
        self.last_eval: dict[str, dict] = {}   # window id -> {"at", "checks", "verdict"}

    def windows(self) -> list[dict]:
        return self.store.setting("schedule_windows", []) or []

    def save_windows(self, windows: list[dict]) -> list[dict]:
        clean = [validate(w) for w in windows]
        self.store.set_setting("schedule_windows", clean)
        return clean

    def pending(self) -> dict | None:
        p = self.store.setting("schedule_pending")
        if p and time.time() - p["created"] > PENDING_TTL_S:
            self.store.log_schedule(p["window_id"], p["date"], "expired", {"note": "not confirmed in 30 min"})
            self.store.set_setting("schedule_pending", None)
            return None
        return p

    def clear_pending(self) -> None:
        self.store.set_setting("schedule_pending", None)

    async def tick(self, now: datetime, head_type, evaluate, raise_pending, auto_start, on_skip=None) -> None:
        """evaluate(window) -> (ok, checks, reason) (async); raise_pending(window, checks) and auto_start(window);
        on_skip(window, reasons) when a window ends without a start (async, optional)."""
        for w in self.windows():
            date = active_occurrence(w, now)
            key = f"{w['id']}:{now.strftime('%Y-%m-%d')}"
            done = self.store.schedule_outcome(w["id"], now.strftime("%Y-%m-%d"))
            if date is None:
                # Window just ended without a decision today: log a skip with the last reasons we saw.
                ev = self.last_eval.pop(key, None)
                if ev and not done and w.get("enabled") and now.weekday() in w["days"] and now.strftime("%H:%M") >= w["end"]:
                    self.store.log_schedule(w["id"], now.strftime("%Y-%m-%d"), "skipped",
                                            {"reason": ev["reason"], "why": ev.get("why") or []})
                    if on_skip:
                        await on_skip(w, ev.get("why") or [], ev.get("reason") or "")
                continue
            if done in ("started", "pending", "confirmed", "dismissed", "expired", "skipped", "failed"):
                continue
            wanted = HEADS[w["head"]]
            if wanted is not None and head_type is not None and int(head_type) not in wanted:
                self.last_eval[key] = {"at": time.time(), "checks": [], "ok": False,
                                       "reason": f"attached head is not a {w['head']} head",
                                       "why": [why.wrong_head(w["head"])]}
                continue
            ok, checks, reason = await evaluate(w)
            self.last_eval[key] = {"at": time.time(), "checks": checks, "ok": ok, "reason": reason,
                                   "why": [] if ok else why.from_window(checks, reason)}
            if not ok or self.pending():
                continue
            if w.get("auto_start"):
                await auto_start(w)
            else:
                await raise_pending(w, checks)

    def upcoming(self, now: datetime) -> list[dict]:
        out = []
        for w in self.windows():
            nxt = next_occurrence(w, now)
            key = f"{w['id']}:{now.strftime('%Y-%m-%d')}"
            out.append({"window": w, "next": nxt.timestamp() if nxt else None,
                        "active": active_occurrence(w, now) is not None,
                        "today": self.store.schedule_outcome(w["id"], now.strftime("%Y-%m-%d")),
                        "last_eval": self.last_eval.get(key)})
        out.sort(key=lambda x: x["next"] or 9e12)
        return out
