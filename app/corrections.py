"""Gaps in the RTK corrections.

The robot reports `rtcm_age`: seconds since its last RTK correction from the base station (-1 = none received, which
is normal on the dock). Without corrections the fix falls to DGPS or single point however many satellites it sees,
so a gap says where to look: at the base station's side when the robot's own HaLow link is strong (2026-10-28: three
gaps of 1.5-3.5 min with HaLow at -49 dBm and 20 satellites, the fix at DGPS).

A gap opens when the age passes problems.RTCM_LATE_S and closes at the first fresh age. It starts at the robot's
last correction (the sample's time minus its age), not at the sample that first showed it late.
"""
from __future__ import annotations

from .problems import HALOW_WEAK_DBM, RTCM_LATE_S
from . import telemetry

MIN_GAP_S = 20          # shorter ones are blips and not reported
NO_DATA_S = 150         # samples further apart than this, with nothing to bridge them: the panel lost the robot
SAME_S = 5              # a late sample whose last correction is within this of the gap's start continues that gap


def _age(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def late(age) -> bool:
    a = _age(age)
    return a is not None and a > RTCM_LATE_S


class GapTracker:
    """Fed every telemetry sample; returns a finished gap (dict) once, when corrections come back.

    The age carries the gap across sparse samples: idle, the panel reads the robot once a minute, and a late sample
    whose last correction is the gap's own start is the same gap however long ago the previous sample was."""

    def __init__(self):
        self.gap: dict | None = None

    def feed(self, ts: float, t: dict) -> dict | None:
        age = _age(t.get("rtcm_age"))
        g = self.gap
        if late(age):
            if g is not None and ts - age <= g["start"] + SAME_S:
                g["last"] = ts                                     # the same gap, still going
                self._note(g, t)
                return None
            done = self._close(g, g["last"]) if g is not None else None   # corrections came in between
            self._open(ts, age, t)
            return done
        if g is None or age is None:
            return None
        self.gap = None
        if age < 0 or ts - g["last"] > NO_DATA_S:
            return self._close(g, g["last"])        # none received, or no data for a while: end at what we saw
        return self._close(g, ts - age)

    def _open(self, ts: float, age: float, t: dict) -> None:
        self.gap = {"start": ts - age, "last": ts, "fixes": [], "halow": None, "sats": None}
        self._note(self.gap, t)

    @staticmethod
    def _note(g: dict, t: dict) -> None:
        label = telemetry.rtk_label(t.get("rtk_status")) if t.get("rtk_status") is not None else None
        if label and label not in g["fixes"]:
            g["fixes"].append(label)
        h = t.get("halow")
        if isinstance(h, (int, float)) and h < 0:
            g["halow"] = h if g["halow"] is None else min(g["halow"], h)
        s = t.get("sats")
        if isinstance(s, (int, float)):
            g["sats"] = s if g["sats"] is None else min(g["sats"], s)

    @staticmethod
    def _close(g: dict, end: float) -> dict | None:
        seconds = end - g["start"]
        if seconds < MIN_GAP_S:
            return None
        return {"start": g["start"], "end": end, "seconds": round(seconds),
                "fixes": g["fixes"], "halow": g["halow"], "sats": g["sats"]}


def describe(gap: dict) -> str:
    """The event text: how long, what the fix did, and whether the robot's own link explains it."""
    s = gap["seconds"]
    length = f"{s} s" if s < 90 else f"{s // 60} min {s % 60} s"
    fell = [f for f in gap["fixes"] if f != "RTK fixed"]
    parts = [f"fix: {', '.join(fell)}" if fell else "the fix held"]
    if gap["sats"] is not None:
        parts.append(f"{gap['sats']:.0f}+ satellites")
    h = gap["halow"]
    if h is None:
        cause = ""
    elif h > HALOW_WEAK_DBM:
        parts.append(f"HaLow {h:.0f} dBm")
        cause = "; the robot's link was fine, so look at the base station"
    else:
        parts.append(f"HaLow down to {h:.0f} dBm")
        cause = "; HaLow was weak, which may be why"
    return f"RTK corrections stopped for {length} ({', '.join(parts)}){cause}"


def gaps(rows: list, now: float) -> list[dict]:
    """Gaps in stored telemetry rows (ts, rtcm_age, rtk_status, halow, sats), oldest first. One still open at `now`
    comes last with end None; one whose last sample is older than NO_DATA_S ends there."""
    tr, out = GapTracker(), []
    for ts, age, rtk, halow, sats in rows:
        done = tr.feed(ts, {"rtcm_age": age, "rtk_status": rtk, "halow": halow, "sats": sats})
        if done:
            out.append(done)
    g = tr.gap
    if g is not None:
        done = GapTracker._close(g, g["last"])
        if done:
            out.append({**done, "end": None} if now - g["last"] <= NO_DATA_S else done)
    return out
