# UI design: navigation, colours and charts

The rules behind the panel's navigation, top bar, palette and charts. The palette lives as tokens on `:root` in
`web/style.css`; the JavaScript reads the same CSS variables, so the map, legends, chips and charts never hard-code a
colour.

## Navigation

Six items: **Live · Runs · Map Studio · Schedule · Robot · Settings**.

- **Robot** holds Health, Maintenance and Events as tabs. Health and Maintenance are both "the robot itself" (its
  condition now and its upkeep), visited rarely and never together with Live. Events is the log of everything, read
  after something happened, usually from a link (an alert, a refused command, a run).
- **Help** is not a nav item: a **?** beside every page title opens that page's help, and a Help link sits at the
  foot of the side nav (and in the phone menu's overflow).
- Old addresses (`#health`, `#maintenance`) keep working.
- Fewer items matter most on a phone, where a long nav row scrolls sideways and hides its last items.

## Top bar

- **Problems first**, tinted in their colour: a robot error, a guard, a run waiting for the user.
- **Switches, not status chips:** Control (and Map writing while Map Studio is open) are buttons. Off → on asks the
  user to type ENABLE, the same confirmation as Settings → Safety; on → off is one click.
- **Status chips are links** to where the user can act: Cloud and broker → Settings → Robot; battery and RTK →
  Health; lawn → Settings → Lawn conditions.
- Nothing is repeated elsewhere on the page (the robot card does not repeat the Control state).

## Colours

The rule: **three families that never share a hue**. Status colours mean good or bad; map-shape colours name a kind
of feature; track and analysis colours describe a measurement. Neutral data is drawn in neutral colours.

| Family | Token | Colour (dark theme) | Used for |
|---|---|---|---|
| Status | `--ok` | green `#8BD49A` | ok, on, RTK fixed |
| | `--warn` | amber `#F0B955` | caution, held, RTK float |
| | `--bad` | coral `#F08A6E` | error, refused, not moving |
| | `--info` | sky `#79B8F0` | robot working, information |
| Map shapes | `--area` | white `#E8EEEA` outline, 8 % fill | mowing areas |
| | `--nogo` | red `#E5484D`, hatched fill | no-go zones |
| | `--novis` | sky `#79B8F0`, dashed | no-vision zones |
| | `--fence` | yellow `#E8D44D` | electric fence |
| | `--path` | grey road | pathways |
| Track | `--trk-fixed` / `--trk-float` | green / amber | RTK fixed / float |
| | `--trk-single` | red-orange `#F2784B` | single-point track (weak, not an error) |
| Analysis | `--gap` | pink `#FF5FA2`, dashed | gaps |
| | `--offplan` | pink `#FF5FA2` | off-plan stretches (both mean "missed the plan") |
| | `--edge` | violet `#B79CF0` | edge passes |
| Data | `--series-1..4` | sky, slate `#9AA7B4`, violet, teal `#4FD1C5` | neutral chart series |

Problems these rules prevent, all seen in an earlier palette:

1. **Two views disagreeing** on what a colour means (no-go amber on one map and coral on another).
2. **One colour meaning a shape and a measurement on the same map** (a mowing area and an RTK-fixed track both green,
   so the coverage swath merged with the area outline).
3. **The error colour on weak-but-normal data** (a single-point track drawn in the error colour reads as a fault).
4. **Status colours on neutral data** (one blade motor in amber, the other in coral, read as "bad").

The robot marker is sky blue with a pulsing ring while it works and amber when held mid-plan. The Problem areas
overlay keeps its own five colours, because it is shown on its own.

**Light theme:** follows from the tokens (`prefers-color-scheme`, plus a switch in Settings → This panel and a
sun/moon button), with darker variants of the same hues for contrast on white. Text contrast on the dark background
is about 8:1 (muted `#A3B1AA` on `#0E1311`). Low-opacity fills (10–18 %) are hard to see on bright roofs and
concrete in aerial photos; shapes rely on outlines and hatching, not fill alone.

## Charts

One chart component draws everything on Health, Runs and Lawn conditions:

- a y-axis with min, mid and max ticks, and a time axis;
- a hover (or touch) readout of value and time, with a crosshair;
- neutral series in the data colours; status colours only where the value itself is good or bad;
- no two series with different units on one axis;
- long tables (such as firmware history) folded behind "Show history" so they do not push the useful cards down.
