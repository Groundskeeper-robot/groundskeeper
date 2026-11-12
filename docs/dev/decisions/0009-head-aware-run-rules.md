# 0009. Run rules depend on the attached head

- **Status:** Proposed
- **Date:** 2026-11-11
- **Links:** #126, #127, #128, #129, #130, #134

## Context
The robot carries different heads (mower, snow blower, leaf blower), and every run records the head type. The start
checks and guards were written for mowing: "Lawn dry" refuses a start in falling snow, and the rain guard docks the
robot in sleet, which is when a snow blower is needed. The area settings editor reads the mower's settings record for
any area, including snow areas.

## Decision
- Each run rule says which heads it applies to. Mower-only rules (lawn dry, the rain guard, the mower area settings)
  do not apply while a snow blower is attached; snow heads get rules of their own (snowfall, a temperature floor, a
  minimum battery for the cold).
- Rules that protect people and the robot apply to every head: the yard-clear check and the person guard, presence,
  the phone-app check, battery, no active error, and the strict reading for assistants and API tokens.
- When the head is unknown, every rule applies (fail closed).
- A head-specific editor or write is offered only for the head it was captured with.

## Consequences
- Settings → Run rules has to show which head each rule is for, so a rule switched off for snow is not mistaken for
  off everywhere.
- Each head's rules need their own guard tests.
- Becomes Accepted in the PR that makes the rules head-aware (#126, #127).

## Enforced by
To be added with #126 and #127: `app/control.py` (`check`), `app/ops.py` (guards), `tests/test_guards.py`.
