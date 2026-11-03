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

Each has the view, what is wrong, and a fix sized as one small PR. **Fixed here** marks the ones fixed with the
review (#72); later fixes name their PR.

### 1. Top bar: the connections pill is out of sight (all views) — fixed here

Cloud · Broker · HaLow · RTK sat at the end of the scrolling chip row, behind the robot pill: at 375 px the row
was 181 px wide holding 605 px of chips, so the connections (and on the demo, the Control switch) were never seen
without a sideways swipe nobody knows to make. On pages without the Live layout switch the chips also took the
title row, leaving the robot pill alone on the second row, so the bar looked different on every page.

Fix: on a phone the connections get their own row (`#conn`), spread across it; a line break after the title row
keeps the chips and the robot pill together on the second row on every page. `renderChips` moves the pill there
only while `(max-width: 700px)` matches, and back when it stops; above 700 px nothing changes.

Cost: the sticky bar is now three rows (about 143 px instead of 91 at 375 px). See 10.

### 2. Live: Pause and Return to dock are below the fold in the Map layout — fixed in #73

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
  only in `title`, and a disabled button gets no tap. Fixed in #74: one line under the grid names why.
- **Top-bar chips**: *Cloud* shows a grey dot but *signed out* is in the tooltip (`.more` is hidden below 1280 px).
  Fixed: a link that is not fine shows its detail. Every detail at once does not fit at 375 px (440 px in 351), so
  fine links stay short and the row wraps to a second line when several are down.
- Map tools (*Problem areas*, *Signal*, *3D*) and Map Studio's drawing tools explain themselves only in `title`.
  The labels carry enough to try them; Help covers the rest. No change proposed.

### 5. Inputs zoom the page on iOS — fixed here (not verified on an iPhone)

Fields were 12–14 px. Safari zooms into any field under 16 px when it is focused and leaves the page zoomed,
which then scrolls sideways. Fix: 16 px text in inputs, selects and text areas on a phone.

### 6. Tap targets under 32 px — fixed

Fixed (phone only): top-bar chips and switches 28 → 32 px; Map / Camera and Health / Maintenance / Events tabs
29 → 32; the **?** help link keeps its 22 px look with a 34 px hit area; Settings section items 30 → 32; Map Studio
shape rows 30 → 32 and their rename buttons 28 → 32; the map's zoom buttons 29 → 36.

Fixed after the review: the small `summary` folds (*Every check*, *History (5)*, *Plan and recharge status*,
*Advanced: port and serial*), the track colour select, the replay scrubber and Map Studio's tidy tolerance chip;
*check again* gets a bigger hit area and keeps its look, since it sits in a sentence. MapLibre's own (i) button stays
24 px.

### 7. Map Studio: the tool bar covered the zoom-out button — fixed here

Below 900 px the drawing bar started at 10 px from the left, over the bottom of the − button, so a tap there hit
the bar. It now starts at 52 px, like the map tools row above it.

### 8. Landscape (844 × 390) and the 701–900 px band — fixed

This band uses the tablet layout: nav row at the top (not sticky), a one-row top bar where the connections pill
still scrolls out of sight (627 px of chips in 383 px), and a map with a 360 px minimum height on a 390 px screen,
so the map fills the view and a drag on it pans the map instead of scrolling the page.

Fixed: a phone on its side (`(orientation: landscape) and (max-height: 500px) and (pointer: coarse)`) gets the
stacked layout at any width, so the largest phones (932 px) no longer get the desktop sidebar, and its map is 70 % of
the height. In that case and the 701–900 px band the connections show as *Links ok* in the one-row bar while all is
well, and take a row of their own when one is not. Live's emergency card leads the panel there too, under the map;
the map-tools fold and the Settings fold stay portrait-only.

### 9. Settings: the section list pushes the section down — fixed in #76

On a phone all 13 sections are shown wrapped (deliberately), which takes about 170 px under the top bar; the
section itself starts near the middle of the screen. Fix (small PR): on a phone, a "Sections" fold that is open
on `#settings` and closed on `#settings/<section>`.

### 10. Top bar height on a phone — fixed in #77

Three rows (143 px, sticky) is 19 % of a 752 px screen above the nav bar. Fix (small PR): while every connection
is fine, show the connections as one chip (*Links ok*, linking to Settings → Robot) and spell them out only when
one is not; the third row then holds one short chip and can join the second when there is room.

### 11. Map Studio on a phone — copy fixed

The map takes 62 vh, so with the top bar only the panel's tab row and one line are in view; picking a shape
scrolls the panel up (`studio.js`), which works. The hint now reads *Pick a shape on the map or in the list*.

Still open: removing a corner is right-click only (`contextmenu` on the handles). A long press may raise that event
on Android, but Safari on iOS does not fire it, so a corner may not be removable there. Needs a real phone.

### 12. Live: map tools scroll sideways — fixed in #75

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

### 15. Map attribution over the legend while imagery loads — fixed

The compact attribution was folded 1.5 s after the map loaded; MapLibre opens it once, when the first source with a
credit arrives, so a slow one left it covering the legend. It now folds on the map's `sourcedata` event when that
happens.

### 16. Demo chip — fixed

*Demo data · synthetic robot* was 200 px of a 181 px chip row on a phone. It is *Demo* there now, the rest in its
tooltip.

## Checked and fine

- Bottom nav: six items, 52 px tall, always in view; the toast sits above it.
- Live camera layout: Pause (76 px) and Stop / Return to dock (52 px) above the fold at 375 and 390 wide.
- Runs list and totals, run page charts and coverage, Schedule, Robot (Health, Maintenance, Events), Help: no
  sideways scroll, no cut-off text, no text truncated by an ellipsis.
- Light theme: the same layout; chips, legend and map tools keep their contrast.
