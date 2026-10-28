# Mobile review: the whole panel on a phone

Issue #52. A pass over every view at 375 × 812 and 390 × 844, dark and light, plus 844 × 390 landscape, against the
demo (`DEMO=1`). Checked in a desktop browser with phone emulation (touch points, `hover: none`, `pointer: coarse`);
measurements come from the page itself (element sizes, scroll widths, `title` attributes).

**What was not checked:** real touch gestures (pinch, drag on the map, Map Studio corner editing), a real iPhone
(focus zoom, notch and home-indicator insets, Safari's bars, the on-screen keyboard over an input), the Wi-Fi join
form and the Restore dialog (both need a robot reply or map writing on), and the Control confirmation with Send
(control stays off; the read-only gate check was opened instead).

**No horizontal page scroll** was found on any view at either width: every overflow is inside a deliberate sideways
scroller (nav on wide phones, chips, map tools, Help's page list).

## Findings, most important first

Each has the view, what is wrong, and a fix sized as one small PR. **Fixed here** marks the ones in this branch.

### 1. Top bar: the connections pill is out of sight (all views) — fixed here

Cloud · Broker · HaLow · RTK sat at the end of the scrolling chip row, behind the robot pill: at 375 px the row
was 181 px wide holding 605 px of chips, so the connections (and on the demo, the Control switch) were never seen
without a sideways swipe nobody knows to make. On pages without the Live layout switch the chips also took the
title row, leaving the robot pill alone on the second row, so the bar looked different on every page.

Fix: on a phone the connections get their own row (`#conn`), spread across it; a line break after the title row
keeps the chips and the robot pill together on the second row on every page. `renderChips` moves the pill there
only while `(max-width: 700px)` matches, and back when it stops; above 700 px nothing changes.

Cost: the sticky bar is now three rows (about 143 px instead of 91 at 375 px). See 10.

### 2. Live: Pause and Return to dock are below the fold in the Map layout

In the Map layout (the default) Live has no emergency card. The Control card's Pause, Stop and Return to dock sit
about 1.3 screens down (under the map and the status card), behind a confirmation dialog. From any page:
**Live, scroll, Pause, Send: three taps and a scroll.** In the Camera layout the emergency card leads the panel:
**Live, Pause, Pause again: three taps, no scroll**, and the button is above the fold at both widths.

Fix (small PR, a choice for a maintainer): on a phone, show the emergency card first in the Map layout too while a
run is going (`liveExtras` in `web/ops.js`, the same card and two-tap path the Camera layout uses). Not done here
because it changes what Live shows.

### 3. Runs: a run link opened cold shows a blank page — fixed here

Opening `#runs/<id>` as the first page (a link from a notification, a bookmark) left Runs empty: `selectRun`
called `state.map.resize()` before `initMap` had created the map (it waits for `/api/map`), threw, and never
rendered the page. Not phone-only, but a phone is where notification links are opened.

Fix: skip the resize and fit when the map is not there yet; the map's `load` handler already draws the run.

### 4. Meaning that lives only in a tooltip (all views)

Touch screens never show `title`. The worst cases:

- **ⓘ tips** (Runs, run page, Map Studio, Schedule): the icon has nothing else, and was 13 × 17 px. **Fixed
  here:** on a touch screen (`hover: none`) a tap on an ⓘ shows its text in the toast, and the tip has an
  invisible 31 px hit area. The toast also uses the full width on a phone (it was squeezed to half the screen).
- **Disabled Control-card buttons** (Live): *A run is already going*, *Nothing to pause: the robot is mowing* are
  only in `title`, and a disabled button gets no tap. Fix (small PR): under the button grid, on touch, one line
  naming why the disabled ones are off, like Map Studio's `.whyline`.
- **Top-bar chips**: *Cloud* shows a grey dot but *signed out* is in the tooltip (`.more` is hidden below 1280 px).
  Now that the connections have their own row on a phone, the detail fits: show `.more` inside `#conn` (one CSS
  line; check the widest case, all four down, before shipping).
- Map tools (*Problem areas*, *Signal*, *3D*) and Map Studio's drawing tools explain themselves only in `title`.
  The labels carry enough to try them; Help covers the rest. No change proposed.

### 5. Inputs zoom the page on iOS — fixed here (not verified on an iPhone)

Fields were 12–14 px. Safari zooms into any field under 16 px when it is focused and leaves the page zoomed,
which then scrolls sideways. Fix: 16 px text in inputs, selects and text areas on a phone.

### 6. Tap targets under 32 px — mostly fixed here

Fixed (phone only): top-bar chips and switches 28 → 32 px; Map / Camera and Health / Maintenance / Events tabs
29 → 32; the **?** help link keeps its 22 px look with a 34 px hit area; Settings section items 30 → 32; Map Studio
shape rows 30 → 32 and their rename buttons 28 → 32; the map's zoom buttons 29 → 36.

Left (small PR): the small `summary` folds (*Every check*, *History (5)*, *Plan and recharge status*,
*Advanced: port and serial*: 18–27 px tall), the *check again* link button (16 px), the track colour select
(30 px), the replay scrubber (16 px tall), Map Studio's tidy tolerance chip (28 px). Padding on `.small` summaries
and `.linkbtn` on a phone covers most of them.

### 7. Map Studio: the tool bar covered the zoom-out button — fixed here

Below 900 px the drawing bar started at 10 px from the left, over the bottom of the − button, so a tap there hit
the bar. It now starts at 52 px, like the map tools row above it.

### 8. Landscape (844 × 390) and the 701–900 px band

This band uses the tablet layout: nav row at the top (not sticky), a one-row top bar where the connections pill
still scrolls out of sight (627 px of chips in 383 px), and a map with a 360 px minimum height on a 390 px screen,
so the map fills the view and a drag on it pans the map instead of scrolling the page.

Fix (one PR, medium): in `(orientation: landscape) and (max-height: 500px)`, cap the map at about 70 % of the
height, and give the connections the same own-row treatment as 1 (or fold them, see 10).

### 9. Settings: the section list pushes the section down

On a phone all 13 sections are shown wrapped (deliberately), which takes about 170 px under the top bar; the
section itself starts near the middle of the screen. Fix (small PR): on a phone, a "Sections" fold that is open
on `#settings` and closed on `#settings/<section>`.

### 10. Top bar height on a phone

Three rows (143 px, sticky) is 19 % of a 752 px screen above the nav bar. Fix (small PR): while every connection
is fine, show the connections as one chip (*Links ok*, linking to Settings → Robot) and spell them out only when
one is not; the third row then holds one short chip and can join the second when there is room.

### 11. Map Studio on a phone

The map takes 62 vh, so with the top bar only the panel's tab row and one line are in view; picking a shape
scrolls the panel up (`studio.js`), which works. The hint says *Click a shape*; on touch it should say *Tap*.
Fix (tiny PR): copy that does not name the input (*Pick a shape on the map or in the list*).

### 12. Live: map tools scroll sideways

*Cameras*, *Problem areas*, *Signal*, *3D* and *Fit yard* are off the right edge of the tools row (a fade hints at
more). Fix (small PR): on a phone, let the row wrap to two lines, or fold the rarely used tools behind *More*.

### 13. Dialogs — partly fixed here

The Control gate check fits at 375 × 812 (527 px tall, Close 40 px). The dialog's limit was `100vh - 32px`, which
on a phone is the height without the browser's bars, so a long dialog's buttons could sit under Safari's toolbar;
it is now `100dvh` on a phone. Restore and Wi-Fi join were not opened (see the top).

### 14. Safe-area insets are inert

`index.html`'s viewport has no `viewport-fit=cover`, so on iOS `env(safe-area-inset-*)` is always 0 and the
bottom nav's padding does nothing; Safari letterboxes instead, which is safe. Fix (small PR, needs a real
iPhone): add `viewport-fit=cover`, then pad the sticky top bar with `env(safe-area-inset-top)` and both bars with
the left and right insets for landscape.

### 15. Map attribution over the legend while imagery loads

The compact attribution is folded 1.5 s after the map loads; when the imagery arrives later, MapLibre opens it
again and it covers the legend for a few seconds. Fix (tiny PR): fold again on the map's `sourcedata` event for
the imagery source, on a phone.

### 16. Demo chip

*Demo data · synthetic robot* is 200 px of a 181 px chip row on a phone. Fix (tiny PR, demo only): *Demo* on a
phone, the rest in its tooltip.

## Checked and fine

- Bottom nav: six items, 52 px tall, always in view; the toast sits above it.
- Live camera layout: Pause (76 px) and Stop / Return to dock (52 px) above the fold at 375 and 390 wide.
- Runs list and totals, run page charts and coverage, Schedule, Robot (Health, Maintenance, Events), Help: no
  sideways scroll, no cut-off text, no text truncated by an ellipsis.
- Light theme: the same layout; chips, legend and map tools keep their contrast.
