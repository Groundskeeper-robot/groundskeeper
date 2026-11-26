# 0013. Mow a patch through a temporary area the panel adds and removes

- **Status:** Rejected
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

## Outcome
Rejected after it was built and tried on a robot. The robot took the patch's area, settings and plan, and its own
stripe preview of the patch looked right, but its route preview for the patch's plan failed every time with WP005
("make sure the map is drawn according to the guide"): with the patch inside its area, with the area cut back
around it and a pathway in, and with a gap all round and a pathway in. A plan of the existing areas still routed
with the area cut back. Yarbo documents no rule the patch broke, so an area made by command rather than in the app
looks like something the robot will not route to. The code was taken out. Reshaping the real area in place (see
Context) is the option left if this comes back.

## Consequences
- Between the add and the removal the map holds an extra area and plan, which the app shows too. A panel that
  stays down leaves them there until it is back or the user deletes them in the app.
- The robot reuses freed ids and re-links pathways on every area save, so history must never key on the patch's id,
  and the comparison after removal is part of the feature, not an extra.
- If the capture fails either check, this ADR is marked Rejected and reshaping is looked at again.

## Enforced by
Nothing: the code and its tests were removed when this was rejected.
