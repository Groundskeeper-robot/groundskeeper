# Live
<!-- order: 10 -->

The robot now: where it is, what it is doing, and the last 24 hours of its track.

**Map / Camera** (beside the title) switches the layout; this browser remembers the choice. **Camera** puts the
follow-cam where the map was, with the camera chips top left, the yard check top right and the robot's state along
the foot; the map becomes a small card in the panel. The panel then starts with the emergency buttons:
a big red **Pause** (it turns into **Resume** while the plan is paused), with **Stop** and **Return to dock** under
it, and the small map right under them. Each needs two taps: the first arms it for 4 seconds, the second sends. They go through the same checks as the
Control card and need control on. Drawing camera coverage and aligning the imagery switch back to the big map while
they last. When no camera's coverage holds the robot (it has driven out of every camera's view, or no position yet),
the big slot shows the map again and the camera comes back when the robot drives into view; both changes wait 10 s so
one stray position does not flip the picture. Pinning a camera with its chip keeps the camera up regardless.

The bar at the top of every page: problems first, tinted in their colour (a robot error, a guard, a run waiting
for you). Then the switches, **Control** (and **Map writing** in Map Studio): click to switch one off; switching it
on asks you to type ENABLE. Then the lawn. At the right, the connections in one pill (Cloud, Broker, RTK; on a
narrower screen just the word and its dot, with the detail when you hover) and, at the far right, the robot: its
state and battery. Each part opens where you can act on it (battery and RTK → Health, lawn → Lawn conditions, cloud
and broker → Settings → Robot).

In the **Camera** layout the small map always shows the whole yard: it refits whenever its card changes size and
cannot be panned or zoomed. **Map layout** gives the full map.

Map colours: mowing areas white, no-go zones red hatched, no-vision zones blue dashed, the electric fence yellow,
pathways grey roads. The track: green RTK fixed, amber float, red-orange single point. Gaps and off-plan stretches
are pink, edge passes violet.

- **Map:** the robot (sky blue and pulsing while it works, amber when held mid-plan), its areas, no-go zones,
  pathways and today's track. **Colour** switches the track between RTK fix quality and edge passes; the legend at
  the foot of the map explains the colours.
- **Map tools** (top right): layers (imagery, zones, labels, track, cameras), Problem areas, Signal, 3D and
  Fit yard. **Align**, next to the imagery credit at the foot of the map, lines the aerial photo up with the
  robot's map (also in [Settings → Map imagery](#settings/imagery)).
- **3D:** tilts the map and raises the ground: the robot's own heights over the lawns (they sharpen with every mow),
  public lidar elevation (USGS 3DEP) around them. The **1× to 3×** menu next to it draws it taller so small slopes show. Right-drag, or two fingers on
  a phone, turns and tilts; the compass resets north. The same view works in Runs, for replay. In 3D the robot is a
  small 3D model: it faces where it is heading, leans with its own tilt, and glides between updates instead of
  jumping. In a replay it drives the run. **Follow** (in 3D) puts the camera behind the robot and a little to one
  side and keeps it there as the robot turns; right-drag moves the camera round it, and a drag or Fit yard lets go.
- **Signal:** HaLow strength, RTK fixed share or satellites across the yard (30 days of runs, 2 m cells), or
  **Wi-Fi (survey)**: one network's signal in 4 m cells, picked from the networks the robot heard. Wi-Fi needs the
  survey on in [Settings → Robot](#settings/robot).
- **Problem areas:** a weak-positioning spot also says when HaLow was weak there (−77 dBm or lower) or corrections
  came late. A good link there points at the sky view instead: trees, walls, the house.
- **Control card:** pick a plan and see its areas. Only the buttons that make sense in the robot's state are on.
  Needs control switched on ([Safety](#help/safety)).
- **Ready to start:** runs every check a start would face and sends nothing. When a start would be refused it says
  why and what fixes it ("Can't start: the lawn is not dry"), with every check one click further.
- **Why it stopped:** when the robot paused itself (bumper, emergency stop, rain, a fault) or the last run ended
  before its plan finished, a card says why and what to do.
- **Pending start:** when a schedule window is ready, confirm or dismiss it here.
- **Follow-cam:** live video (5 frames a second) from the Frigate camera whose coverage contains the robot. It
  plays only while Live is on screen; if the video fails it shows a still every 3 seconds and tries again. "Camera
  layout" under it switches to the camera-first layout.
- **Lawn:** Dry, Damp or Wet, and when it should be dry.
