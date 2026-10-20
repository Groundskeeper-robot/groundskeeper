# Robot states, codes and problem thresholds

What the robot's state fields mean, which situations are real problems for it, and the thresholds the panel uses
for the Problem areas layer, the stuck alert and "why it stopped". The code tables live in `app/telemetry.py`; the
problem-area thresholds are constants at the top of `app/problems.py`.

Sources: Yarbo's wiki (wiki.yarbo.com, the most useful), the official Home Assistant integration
(`YarboInc/YarboHA`, its code tables), the community `yarbo-local/yarbo-local` field reference, and owner reports on
forum.yarbo.com (anecdotal; volunteer "Product Pioneers" and staff accounts noted where it matters). Observations
from recorded runs are marked as such.

## 1. Code tables (`StateMSG`)

**`on_going_planning`:** 0 not started, 1 working, 2 calculating route, 3 heading to area, 5 completed.
Errors: −2 WP002, −10 WP003, −11 WP004, −12 WP005 (route; usually pathway/dock topology), −20 WP006 outside map,
−21 WP007, −22 WP008, −23/−88 in a no-go zone, −24 low battery, −26 WP012, −30 WP013, −31 WP014, −40 obstacle mark
failed, −42 out of boundary, −43 WP016 obstacle, −44 WP017 exceeded boundary, −47 out of boundary by more than
1.5 m, −92 WP025.

**`on_going_recharging`:** 1 returning on a path, 2 returning in an area, 3 repositioning, 4 charging, 99 verifying.
Errors: −2 server, −3 direction uninitialised, −4 dock uninitialised, −5 REC005, −6 failed to park, −8 docking
connection failed, −9 stuck, −20 outside map.

**`planning_paused`:** 0 none, 1 manual, 2 low-battery recharge, 3 power restart, 4 emergency stop, 5 bumper,
6 stuck, 7 fault, 9 rain.

**`error_code`:** 902 = tilted or flipped.

**`StateMSG.stuck` is unreliable.** In a recorded mow it stayed 0 through a stuck spell of about 12 minutes and
fired only twice, for a second each, at other times. Never trust it alone: the panel's own stall rule (mowing,
between areas or heading home, but within 1 m for 3 minutes) catches what it misses.

## 2. Problem types, ranked

| # | Problem | Typical triggers | Signals | Place-bound? |
|---|---|---|---|---|
| 1 | **Positioning degrades** (RTK float/loss, multipath, dead reckoning): the most common | trees, eaves, walls; one antenna shaded near a wall | `RTKMSG.status` ≠ 4; `heading_dop` ≥ 1.5 warn, ≥ 2 the robot falls back to IMU/odometry; `combined_odom_confidence` < 0.5 = dead reckoning; shared satellites (`heading_multi`, probably the app's "L2") < 10 | yes |
| 2 | **Boundary / out-of-map pauses** (some false) | position jumps; pinched or jagged boundaries, overlapping areas; routes hugging no-go corners | plan codes −42/−44/−47/−92/−20 with the pose at the time; healthy RTK at the time ⇒ a geometry or software cause | yes |
| 3 | **Correction link failures** (Data Center, HaLow, RTCM) | base antenna obstructed, network flapping; HaLow ≤ −82 dBm switches to 4G, back at ≥ −77 dBm | `rtcm_age` (−1 none; > 10–15 s interrupted), `halow_status.strength`, `abnormal_msg.rtcm_state` | only where the signal is weak |
| 4 | **Stuck / slip / high-centring**: high severity, needs a person | slopes (reversing uphill, wheelies), loose or wet soil, roots, ruts, walkway drops, ditches | `planning_paused` 6, `on_going_recharging` −9, `StateMSG.stuck`; **wheels turning without progress**; wheel current up, left/right asymmetry. The app's own alert is coarse (1 m in 10 min) | yes |
| 5 | **Obstacle detection** (false positives, phantom obstacles, WP016) | dirty lens, low light, shadows (a firmware 3.13.15 bug) | `StateMSG.obstacle`, plan −43 (WP016), −40 | partly |
| 6 | **Tilt / rollover alarms** (often false on slopes) | steep ground | `error_code` 902, pitch/roll; adaptive slope engages above 5° | yes |
| 7 | **Docking failures** | dock tilt > 1°, debris, metal near the sensor, weak GPS at the dock, battery < 20 % | recharge codes −2…−20 (REC005 = −5) | at the dock |
| 8 | Rain pauses | rain sensor (lower = wetter) | `planning_paused` 9 | no |
| 9 | Software pauses (WP003 bug, WP005 after map edits) | firmware, map topology | plan codes | no: excluded from the map |

## 3. Official and community thresholds

- Slope rating 35°. Adaptive slope engages above 5°.
- Heading DOP < 2 (volunteer advice: < 1.5).
- ≥ 20 satellites, shared > 10 (ideally 15).
- RTCM latency 1–2 s normal; > 15 s unusable.
- Position confidence > 0.9 high; < 0.5 = dead reckoning.
- Dead-reckoning travel limits (`read_low_cfd_config`): 50 m inside an area, 10 m on boundaries and pathways.
- Dock: tilt < 1°, at least 120° of clear sky.
- Turns tighter than one robot length force a zero-turn (the robot is about 128 × 69 cm).
- HaLow: ≤ −82 dBm switches to 4G; back at ≥ −77 dBm.
- Wiki rule of thumb: if half an area has poor positioning quality, split or avoid it.
- No official slip threshold or minimum passage width exists.

Thin evidence: WP013 has no explanation anywhere; the "L2" ↔ `heading_multi` and "Quality" ↔
`combined_odom_confidence` mappings are inferred; `rtcm_age` units are ambiguous; frequencies come from single-owner
reports; several issues are firmware-specific (the panel records firmware with events).

## 4. Observations from recorded runs

- A real stuck spot was the slowest 3 m cell of the yard: 79 % of its samples steeper than 12°, 19 % without an RTK
  fix. Slope and a weak fix together. The robot's `stuck` flag never fired; the panel's stall rule would have within
  about 2.5 minutes.
- "Spinning without progress" (wheels travelling ≥ 10 m in 60 s while the robot moves < 1.5 m) marks traction loss.
- Reversing hot spots (cells where 20–25 % of samples have both wheels backwards) are tight turns or obstacle
  bypasses: worth watching, not necessarily a problem.
- Samples on the charger (RTK single, corrections late) look like a weak-positioning spot; the panel skips docked and
  charging samples.
- Link and position: RTK was not fixed in 2.8 % of samples at HaLow ≤ −82 dBm, 0.9 % at −82…−77 and 0.6 % above
  −77. Corrections were almost always 0–3 s old. Most gaps of more than 3 s in the robot's data start at
  HaLow ≤ −82 dBm, the 4G-fallback level.
- A low-battery dock mid-plan is a charging break: the plan stays open (`planning_paused` 2) and the robot drives
  back out by itself once charged, including after dark. A `cmd_recharge` sent mid-plan instead docks the robot and
  closes the plan.
- Telemetry stamped on arrival bunches up after a link gap, which looks like position jumps; most "jumps" faster
  than 1.5 m/s while the wheels report ~0.5 m/s fall within 10 samples after a gap.

## 5. The Problem areas layer

Each 2 m cell is scored from the runs of the last N days, per cause; the dominant cause sets the colour and the
tooltip carries the evidence ("stuck here 12 min · 79 % steeper than 12° · 19 % without RTK fix").

1. **Stuck / no progress** (highest weight): the robot's stuck signals, the panel's stall episodes and "spinning
   without progress" windows, placed at the episode position.
2. **Steep ground:** share of samples with |pitch| or |roll| > 12°, and the maximum; ≥ 20° flagged, ≥ 25° severe.
3. **Weak positioning:** share of samples with RTK not fixed, heading DOP ≥ 2 (where the robot itself falls back to
   IMU/odometry), or confidence < 0.5. The wiki's "≥ 50 % poor" rule marks a cell red. The evidence names a weak
   link where HaLow ≤ −77 dBm or corrections late or missing (`rtcm_age` −1 or > 10 s) in ≥ 5 % of the cell's
   samples.
4. **Hard going:** reversing share and high wheel current at low speed, only where clearly above the yard's normal.
5. **Errors at a place:** plan and recharge error codes and 902 tilt events, pinned where they happened, with the
   WP/REC label.

Weak HaLow alone is not a place problem; it counts only where it coincides with a position problem. Docked and
charging samples are not counted.

Fields recorded for this layer: `combined_odom_confidence`, `heading_obs`, `heading_multi`.
