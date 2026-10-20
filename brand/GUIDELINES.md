# Groundskeeper brand guidelines

How the name, logo, colour, type and voice are used. The files are in this folder (see [README.md](README.md));
the token values are in [tokens.json](tokens.json) and are the same as the CSS custom properties in
`web/style.css`, which is what the panel actually uses. If one changes, change the other in the same commit.

Groundskeeper is a self-hosted recorder, map and guarded control panel for a Yarbo robot mower. It watches the
yard, keeps the record of every run, and only acts on the robot when a person has switched control on and every
check passes. The brand should feel the same way: calm, exact, and on the side of whoever is standing in the yard.

## Name

- Write **Groundskeeper**: one word, capital G only. Never "Grounds Keeper", "GroundsKeeper" or "Yarbo Groundskeeper".
- Where a reader needs to know which robot it works with, write **Groundskeeper for Yarbo**, and use the
  `groundskeeper-lockup-for-yarbo-on-*.svg` lockup. Groundskeeper is not made or endorsed by Yarbo: never put
  Yarbo's logo, colours or name first next to the mark.
- In the panel the name appears once, in the nav. Pages are named by what they show: Live, Runs, Map Studio,
  Schedule, Robot, Settings.

## Voice

Plain, specific, and honest about what the panel can and cannot do. Real copy from the panel:

> Automatic has not found a free source with imagery at your yard yet. Add a key below, or a source of your own.

> Switch control on in Settings → Safety (you type ENABLE), then use the Control card on Live.

> Revoke the token? Connected assistants lose access and assistant commands switch off.

- Say what is true now, then what fixes it. A refusal names the failed check and where to change it.
- Address the reader as **you**; the software is **the panel**; the machine is **the robot**.
- Sentence case everywhere. Uppercase only in card labels (the `label` style).
- No emoji, no exclamation marks, no jokes about blades.
- Give real units: cm per pixel, %, min, m. "7.6 cm per pixel" beats "high resolution".
- Anything that can move the robot says so plainly and asks for a typed ENABLE. Never imply the robot acts on its
  own judgement; say which rule or check let it start.
- Use the panel's words: **control on / off**, **map writing**, **run rules**, **checks**, **held** (paused
  mid-plan), **dock**.

## Logo

The mark is a **GK monogram standing over a strip of lawn**. The monogram is IBM Plex Sans Condensed Bold, the
heavy cut of the wordmark's face. In the grass sits **the robot as a dot**, in the same blue (`robot`) it has on the
Live map: behind it the lawn is mowed short, ahead of it the grass is still long. Everything sits on a `turf` tile.

| Where | File (in `brand/logo/`, `brand/app-icons/`, `brand/social/`) |
|---|---|
| App icon, nav, anything 24 px and up | `groundskeeper-mark.svg` |
| 16 to 23 px, browser tab | `groundskeeper-mark-small.svg` or `favicon.svg` (bigger letters, a few chunky blades) |
| A header with room, on `bg` dark / light | `groundskeeper-lockup-on-dark.svg` / `-on-light.svg` |
| Where the robot must be named | `groundskeeper-lockup-for-yarbo-on-dark.svg` / `-on-light.svg` |
| A square space | `groundskeeper-lockup-stacked-on-dark.svg` / `-on-light.svg` |
| Inside the interface, no tile | `groundskeeper-glyph-on-dark.svg` / `-on-light.svg` (`brand-grass`, `brand-grass-back`, `brand-dot`) |
| One colour: print, engraving, a stamp | `groundskeeper-glyph-mono-dark.svg` (ink `#16201B`) / `-mono-light.svg` (ink `#E6ECE8`) |
| Link previews | `groundskeeper-social-card.png` |

- **Clear space:** a quarter of the mark's height on every side (16 units on its 64-unit grid). Nothing enters it.
- **Lockup gap:** 18 units between tile and name; keep it when you scale.
- **Minimum size:** mark 16 px (the small tile below 24 px); horizontal lockup 120 px wide; stacked lockup 64 px wide.
- **On the aerial map** use the glyph on an `overlay` chip; never the tile straight on the photo.
- **Do not** recolour the monogram, grass or dot beyond the files given, drop the dot or the grass, rotate the
  tile, add outlines, shadows or gradients, or set the monogram or wordmark as live text. The wordmark is IBM Plex Sans Condensed SemiBold and the
  monogram the Bold, both as outlines; use the files.

## Colour

Two layers. The **brand colours** (`turf`, `brand-grass`, `brand-grass-back`, `brand-dot`) belong to the logo
and to covers. On the tile they always take their dark values. Everything else is the **panel's palette**, the same tokens `web/style.css` defines. Dark is the first
theme; light follows the device or Settings → This panel → Theme.

- **Surfaces** step up from `bg` to `side` (nav rail), `card`, then `card2` (wells inside a card). Separate them
  with `line`; use `line2` for control borders and `rule` for rows inside a card.
- **Text:** `text` on `bg`, `card` and `card2`; `text2` for secondary text and nav labels; `muted` for help and
  captions; `label` for uppercase card labels; `link` for links. `dim` is below 4.5:1 on `bg` in the light theme:
  only for text a reader can miss.
- **Status:** `ok` good or on, `warn` needs care, `bad` wrong or refused, `info` information. Each always comes
  with a word, never colour alone. `ok` is also the primary button fill, with `on-accent` text.
- **Charts:** neutral data uses `series-1` to `series-4`; an ordered scale uses `ramp-1` to `ramp-4`. Never draw a
  neutral measurement (satellites, voltage, temperature) in status colours.
- **The map:** `area`, `nogo`, `novis`, `fence`, `road`, the `trk-*` track colours, `gap`, `offplan`, `edge` and
  the `robot*` colours are fixed in both themes because they sit on the aerial photo. One colour, one meaning: a
  shape never shares a colour with a measurement, and Live and Map Studio draw the same thing the same way.
- **Focus:** a solid 2 px `info` outline, offset 2 px (inset 2 px inside tabs and icon buttons).

## Type

- **IBM Plex Sans** (`sans`) sets the interface. `body` is 14 px; help text and chips are `small`; page titles
  `page-title`; one headline number per card `big`.
- **IBM Plex Mono** (`mono`) sets anything measured or raw: `data` for values and the raw-data tree,
  `data-strong` for short values on a picture (resolution badges), `cell` for dense readouts.
- **IBM Plex Sans Condensed** (`display`) is the wordmark's face (SemiBold) and the monogram's (Bold). Outside the wordmark, use it only for display
  lines on covers and the social card (`display`), never inside the panel.
- Uppercase only in `label`, with its 0.6 px tracking.

## Space, radius, borders

- The default gap between siblings is `space-8`. Cards sit `space-12` apart with `space-16` side padding, which is
  also the page gutter.
- Radius by role: cards `radius-12`, notes and toasts `radius-10`, buttons, fields and stat tiles `radius-9`, nav
  items `radius-8`, segmented buttons `radius-7`, badges `radius-6`, chips and switches `radius-pill`.
  `radius-tile` is the logo tile's own corner and is not used on interface elements.
- Borders, not shadows. A selected or attention card adds a 1 px ring in its status colour; the toast is the only
  floating element.

## Iconography

Outline icons on a 24-unit grid: 1.75 stroke, round caps and joins, no fill, `stroke: currentColor`. In the nav an
icon is `text2` at rest and `ok` when its page is current. The files in `brand/icons/` are the panel's own, inked
`#8BD49A` because an `<img>` cannot inherit colour. No icon font and no emoji. The robot icon (a body on two
wheels) was the panel's mark before Groundskeeper; it now stands only for the Robot page.

## Motion

Almost none. The one ambient motion is the ring that pulses around the robot on the map while it works, and it
stops under reduced motion. Interface changes are instant; nothing slides or bounces.
