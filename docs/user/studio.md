# Map Studio
<!-- order: 30 -->

The robot's map: look at it, compare versions, and edit it in a draft before anything is sent.

## Tabs

- **Yard:** your draft on top once you have edited something (the changes, the checks, and Send), then the selected
  shape with its tools, and every shape in one list. The pencil or F2 renames in place.
- **Plans:** the robot's plans and their areas. **New plan** or **Edit** sets a name and the areas, in mowing order,
  and saves straight to the robot (map writing has to be on and the robot idle); the panel reads the plans back to
  check. **Delete…** in the editor removes a plan (it asks first, and again if a schedule window starts it). **Preview** asks the robot
  for the plan's route, area by area in order, and draws it on the map as a grey line (sky blue dot at the start,
  amber at the end) with the time it expects; driving between areas is not included.
- **History:** this panel's map versions and the robot's own backups. Compare any of them with the map now, or
  start a draft from one. **Save now** makes a robot backup; the pencil renames one.
- **More:** import and export (GeoJSON, KML, raw JSON), captured writes, and **Record an app edit**.

## Editing

Click a shape and its corners are editable: drag a corner, drag an orange midpoint to add one, right-click to remove
one. Delete removes the shape, Ctrl+Z undoes, Esc deselects. The bar under the map tools adds areas, no-go zones,
no-vision zones and pathways, as points, circles or rectangles. Shift snaps to 15° steps; Alt places freely.

**Moving a group of corners a little at a time:** with a shape selected, Shift-drag a box over the corners to move
(Ctrl+Shift-drag adds more, Shift-click one corner to add or drop it). The picked corners turn blue and a panel opens:
its arrows, or the arrow keys, move them north, south, east or west by the step you set (1, 2, 5, 10 or 25 cm, or
type any value from 0.5 to 100 cm; the step is remembered). The panel shows how far they have moved since you picked
them. Moves in quick succession are saved as one draft edit, so one Ctrl+Z takes back a burst. Esc or Done drops the
picks; Pick all corners nudges the whole shape.

**Tidy** (Clean up, Straighten, Round, Smooth, Square) previews each change as a dashed outline first. A no-go zone
only grows and an area only shrinks.

## Sending

Map writing must be on ([Safety](#help/safety)) and the robot docked and idle. No-go zones, pathways and dead ends can be
added, changed and deleted; areas can be reshaped and renamed in place; no-vision zones can be added, deleted,
switched on or off, reshaped and renamed (adding one asks first: the robot ignores its cameras inside it). Adding or
deleting areas stays in the Yarbo app, and so does a no-vision zone's obstacle bypass (shown like an area's obstacle
avoidance mode). Every write is read back; a mismatch
undoes everything sent.

A pathway's **Mow settings** (select it) are read from the robot and saved straight to it: speed, obstacle sensing,
trimmer, **blades on**, and the **transit path offset** with its range and step, shown in inches like an area's.

The draft is shared by every open window. An edit from a window that is out of date is refused and that window
reloads.

## Dead ends

A dead end is a route the robot drives out along and back the same way (to a shed, a side gate). **+ Dead end** draws
one: start inside an area and click outwards. Select one on the robot for its settings: speed, blades on, trimmer
(read back and checked, like a pathway's). Reshaping or renaming sends a new one and deletes the old, and its settings
go with it.
