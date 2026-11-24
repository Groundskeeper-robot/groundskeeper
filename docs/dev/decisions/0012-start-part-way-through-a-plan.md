# 0012. A start may begin part-way through the plan

- **Status:** Accepted
- **Date:** 2026-11-24
- **Links:** #182

## Context
Stop and Return to dock end the plan on the robot, so Resume has nothing to act on. The panel could then only start
the plan again from the beginning, mowing the finished part twice. Yarbo's SDK starts a plan part-way through with one
more field on the same command: `start_plan {"id", "percent"}` (`CoreModule.start_plan(plan_id, start_percent)`,
which adds `percent` only when it is above 0), and the official app has been seen to send the same field.

Until now `start_plan` carried a plan id and nothing else, so every start began at the beginning and there was nothing
about where a start begins to get wrong. A start point is a second thing a request can say about a start, and a wrong
one sends the robot to mow from a place nobody chose.

## Decision
- **`start_plan` may carry one optional start point**, `start_percent`, sent to the robot as the SDK sends it:
  `percent`, a whole number, only when it is above 0. No start point, or 0, is the start the panel always sent.
- **A start point is a whole number from 0 to 99, or the start is refused.** The check (**Start point**) is hard, for
  every actor. A string, a fraction, a boolean or a number out of range is never rounded, clamped or dropped into a
  start from the beginning. The payload builder refuses one too, should a caller ever skip the check.
- **It adds a check and relaxes none.** A start part-way through faces every start check a start from the beginning
  faces.
- **A person picks it, in the confirmation, for one start.** The confirmation shows the checks for exactly what Send
  would send, and nothing remembers the choice for the next start. A program with an API token may ask for one
  through the same route, under the strict reading it already gets.
- **Nothing that acts by itself uses it.** The scheduler, the guards and the AI assistant's command tool always start
  at the beginning. Letting any of them choose a start point is a new decision.
- **The left-off figure is a hint, never a default.** It is the robot's last progress reading for the newest run,
  rounded down, and it is held back when that run cannot say where it stopped (this panel started it part-way, or
  the panel stopped watching before it ended). The list always opens on the beginning.

## Consequences
- A stopped mow can be finished without mowing the first part again.
- How the robot measures the percent (area or route length), and what it reports as plan progress after such a
  start, is not known until it is tried on a robot. Until then the hint may overlap or skip a little, and a run
  started part-way may show its skipped first part as missed. Revisit the hint and the run analysis after that test.
- A part-way start made in the Yarbo app is not in the panel's control log, so the hint is not held back after one.
- A resume is not a start: it takes no start point.

## Enforced by
`app/control.py` (`start_percent_of`, `check`, `payload_for`, `START_PERCENT_MAX`), `app/why.py` (the reason shown),
`web/ops.js` (`startChoice`, `pickStartPoint`, `previewConfirm`, `leftOffAt`), `tests/test_start_point.py` and
`tests/start_point_flow.cjs`.
