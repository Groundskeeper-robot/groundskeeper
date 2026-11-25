# 0013. Mow a patch through a temporary area the panel adds and removes

- **Status:** Proposed
- **Date:** 2026-11-24
- **Links:** 0004, #183; `docs/dev/map-writes.md`

## Context
The robot has no command to mow part of an area, and the app's only answer is a permanent map edit. 0004 keeps
adding and deleting areas out of the panel, because a new area changes ids, pathway links and the plans and
settings tied to them. Both commands are captured. Reshaping the real area and putting it back would stay inside
0004, but a failure mid-run leaves the real area shrunk and its settings and history tied to a shape that changed.

## Decision
The panel may add one area and one plan of its own for a single patch run, and must remove both afterwards. This
is the only case in which it adds or deletes an area; 0004 holds for everything else, and its rules (captured
commands, the map-writing switch, docked with no plan, read back) apply to every step.

- The patch is a **rectangle**: a new area (`save_clean_area` without an id) inside one existing area, with that
  area's `ref`, and a new plan that holds only the patch. No existing area, plan or setting is written. A rectangle
  keeps the long side, and so the route angle, unambiguous; other shapes can come later.
- It starts from a **"Mow a patch" button in the Live page's Control card**, beside the plan picker, not from Map
  Studio. This is the first map write outside Studio: it goes through the same `MapWriter`, needs the same
  map-writing switch and is read back the same way. The user taps two opposite corners and can drag a round
  handle on each; the panel lays the rectangle between them along the last mow's lines and trims back, along the
  lines, any part outside the area or closer than 0.5 m to its edge, saying so. Two taps and two big handles work
  the same with a mouse or a finger. (It first shipped as a fixed rectangle the user panned the map under, sized
  with steppers; that was hard to aim.)
- Its settings are a copy of the surrounding area's, with the boundary extension at 0, the boundary laps off
  (`edge_circles` 0), one pass and its own route angle. The robot measures that angle from the outline's long side and turns
  its lines 90° from one mow to the next, so the panel computes it from the last recorded run over the patch and
  the patch's shape.
- The start is a normal run: the same gates, guards and the user's go, after the robot's own route preview for the
  patch has been shown.
- The pending removal is stored before the first write. Once the robot is docked with no plan, the panel deletes
  the plan, then the area once the plan is read back as gone (a plan left on a freed id would mow whatever area
  gets that id next), and compares areas, plans and pathway links with what it kept before the patch. A
  difference is reported, never repaired by guessing, and keeps the patch tracked (so no new patch starts) until a
  person checks again or accepts the map as it is. The area stays while any plan still uses it, since deleting an
  area drops the plans that use it. A patch is never kept or reused.
- The run keeps the patch's outline, since its area and plan ids are gone after the removal.
- Only a person in the panel can do this: no scheduler, no MCP tool, no Home Assistant, no API token. The
  controller refuses a start of the patch's plan from anywhere but the patch's own Start, which first checks that
  the robot still lists that plan with the patch's name and area.

It is built only if a capture on a docked robot shows that an area inside another is accepted and pathway links
come back unchanged after the removal. Previews already showed that a patch's route keeps out of no-go zones, that
its lines move with the outline, and that `edge_circles` 0 leaves stripes only.

## Consequences
- Between the add and the removal the map holds an extra area and plan, which the app shows too. A panel that
  stays down leaves them there until it is back or the user deletes them in the app.
- The robot reuses freed ids and re-links pathways on every area save, so history must never key on the patch's id,
  and the comparison after removal is part of the feature, not an extra.
- If the capture fails either check, this ADR is marked Rejected and reshaping is looked at again.

## Enforced by
`app/patch.py` (`PatchMower.block`, `_remove_locked`, `compare`); `app/control.py` (`PATCH_START`, the "Started
from Mow a patch" check); `tests/test_patch.py`
`test_nothing_is_sent_unless_docked_idle_and_switched_on`, `test_a_failure_after_the_add_removes_what_was_added`,
`test_an_area_that_reused_the_patch_id_is_never_deleted`, `test_a_set_up_cut_short_by_a_restart_is_cleaned_up`,
`test_a_plan_that_would_not_go_keeps_its_area_and_the_patch_tracked`, `test_the_patch_plan_starts_only_from_its_own_button`,
`test_start_refuses_a_plan_that_is_not_the_patch_any_more`,
`test_only_the_patch_deletes_areas` and `test_the_routes_are_closed_to_api_tokens`; `tests/test_guards.py`
`test_only_the_map_writer_gets_the_write_door`.
