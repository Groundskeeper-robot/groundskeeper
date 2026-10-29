"""RTK correction gaps (app/corrections.py), with made-up samples shaped like the robot's."""
from app import corrections


def sample(age, rtk=4, halow=-49, sats=20):
    return {"rtcm_age": age, "rtk_status": rtk, "halow": halow, "sats": sats}


def feed(samples, start=1000.0, step=10.0):
    tr, out = corrections.GapTracker(), []
    for i, s in enumerate(samples):
        done = tr.feed(start + i * step, s)
        if done:
            out.append(done)
    return tr, out


def test_a_gap_runs_from_the_last_correction_to_the_first_fresh_one():
    # fresh, then late for ~100 s with the fix at DGPS, then fresh again (like 2026-10-28 15:53)
    samples = [sample(1), sample(1), sample(13), sample(25, rtk=2), sample(37, rtk=2), sample(98, rtk=2),
               sample(1, rtk=5), sample(1)]
    tr, out = feed(samples)
    assert len(out) == 1 and tr.gap is None
    g = out[0]
    assert g["start"] == 1020 - 13 and g["end"] == 1060 - 1          # from the ages, not the samples
    assert g["seconds"] == 52 and g["fixes"] == ["RTK fixed", "DGPS"]
    assert g["halow"] == -49 and g["sats"] == 20


def test_blips_and_none_received_are_not_gaps():
    _, out = feed([sample(1), sample(12), sample(1)], step=5)           # late for one sample: 16 s, under 20
    assert out == []
    _, out = feed([sample(-1, rtk=1)] * 30)                             # none at all: normal on the dock
    assert out == []
    _, out = feed([sample(None)] * 5 + [sample("x")])
    assert out == []


def test_a_gap_is_not_bridged_across_a_stretch_with_no_data():
    tr = corrections.GapTracker()
    assert tr.feed(1000, sample(15)) is None
    assert tr.feed(1030, sample(45, rtk=2)) is None
    done = tr.feed(1300, sample(1))                                     # 270 s with nothing from the robot
    assert done["end"] == 1030 and done["seconds"] == 1030 - 985


def test_idle_samples_a_minute_apart_are_one_gap():
    """Idle, the panel reads the robot once a minute: the climbing age carries the gap from sample to sample."""
    samples = [sample(1)] + [sample(15 + 61 * i, rtk=2) for i in range(10)] + [sample(2)]
    _, out = feed(samples, step=61)
    assert len(out) == 1
    g = out[0]
    assert g["start"] == 1061 - 15 and g["end"] == 1000 + 11 * 61 - 2


def test_corrections_coming_back_between_two_late_samples_split_the_gap():
    tr = corrections.GapTracker()
    assert tr.feed(1000, sample(30)) is None                             # last correction at 970
    assert tr.feed(1100, sample(40)) is not None or tr.gap["start"] == 1060   # a new last correction: a new gap
    assert tr.gap["start"] == 1060


def test_none_received_ends_a_gap_at_the_last_late_sample():
    tr = corrections.GapTracker()
    tr.feed(1000, sample(15))
    tr.feed(1030, sample(45))
    done = tr.feed(1040, sample(-1, rtk=1))
    assert done["end"] == 1030 and tr.gap is None


def test_the_event_says_where_to_look():
    strong = {"start": 0, "end": 158, "seconds": 158, "fixes": ["RTK fixed", "DGPS"], "halow": -49, "sats": 19}
    text = corrections.describe(strong)
    assert text.startswith("RTK corrections stopped for 2 min 38 s")
    assert "DGPS" in text and "HaLow -49 dBm" in text and "base station" in text
    weak = {**strong, "seconds": 40, "fixes": ["RTK float"], "halow": -80}
    text = corrections.describe(weak)
    assert "40 s" in text and "HaLow down to -80 dBm" in text and "weak" in text and "base station" not in text
    held = {**strong, "fixes": ["RTK fixed"], "halow": None, "sats": None}
    assert "the fix held" in corrections.describe(held)


def test_history_lists_closed_gaps_and_one_still_going():
    rows = [(1000 + 10 * i, age, 4 if age < 10 else 2, -50, 21)
            for i, age in enumerate([1, 15, 25, 35, 1, 1, 12, 22, 32, 42])]
    out = corrections.gaps(rows, now=1100)
    assert [g["end"] is None for g in out] == [False, True]
    assert out[0]["seconds"] == 1039 - 995 and out[1]["start"] == 1060 - 12
    stale = corrections.gaps(rows, now=1090 + corrections.NO_DATA_S + 1)    # its last sample is long ago: ended there
    assert stale[-1]["end"] == 1090


def test_the_stored_rows_are_only_the_late_ones_and_the_one_after(tmp_path):
    from app.store import Store
    store = Store(str(tmp_path))
    ages = [1, 1, 15, 25, 1, 1, -1, 1, 30, -1, 1]
    for i, age in enumerate(ages):
        store.db.execute("INSERT INTO telemetry (ts, rtcm_age, rtk_status) VALUES (?, ?, 4)", (1000 + 10 * i, age))
    rows = store.correction_rows(0)
    assert [r[1] for r in rows] == [15, 25, 1, 30, -1]
    assert [(g["start"], g["end"]) for g in corrections.gaps(rows, now=2000)] == [(1005, 1039), (1050, 1080)]


def test_the_health_api_lists_gaps_newest_first_with_a_count(tmp_path):
    from app import main
    from app.store import Store
    store = Store(str(tmp_path))
    for i, age in enumerate([1, 15, 25, 35, 1, 1, 15, 25, 35, 45, 1]):
        store.db.execute("INSERT INTO telemetry (ts, rtcm_age, rtk_status) VALUES (?, ?, 4)", (1000 + 10 * i, age))
    out = main._correction_gaps(store, 0)
    assert out["correction_gap_count"] == 2
    assert [g["start"] for g in out["correction_gaps"]] == [1045, 995]


def test_the_recorder_logs_a_gap_when_corrections_come_back(tmp_path):
    from tests.test_poses import _recorder
    r = _recorder(tmp_path)
    for i, age in enumerate([1, 15, 25, 35, 1]):
        r._ingest({"rtcm_age": age, "RTKMSG": {"status": 2 if age > 10 else 4}}, 1000 + 10 * i, full=True)
        r._ingest({"BatteryMSG": {"capacity": 80}}, 1005 + 10 * i, full=False)   # keeps the old age: not fed
    ev = [e for e in r.store.events() if e["kind"] == "rtk_corrections_gap"]
    assert len(ev) == 1 and ev[0]["detail"].startswith("RTK corrections stopped for 44 s")


def test_the_recorder_logs_one_event_for_a_gap_while_idle(tmp_path):
    from tests.test_poses import _recorder
    r = _recorder(tmp_path)
    for i, age in enumerate([1] + [15 + 61 * k for k in range(8)] + [1]):
        r._ingest({"rtcm_age": age, "RTKMSG": {"status": 2 if age > 10 else 4}}, 1000 + 61 * i, full=True)
    assert len([e for e in r.store.events() if e["kind"] == "rtk_corrections_gap"]) == 1
