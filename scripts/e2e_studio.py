"""Map Studio end-to-end check: headless Edge, real mouse and keyboard events, against a running panel.

    pip install playwright          # uses the installed Microsoft Edge; no browser download
    python scripts/e2e_studio.py [PANEL_URL]      # or PANEL_URL=http://server:8426 python scripts/e2e_studio.py

The target is the first argument, else PANEL_URL, else http://localhost:8426 (a local demo instance).

Point it at the DEMO instance (:8426, the default), not the live panel: the draft is shared, so a test run would
fight with anyone editing. It refuses to start if a draft with edits is open (E2E_FORCE=1 overrides), edits a fresh
one and discards that at the end. It never writes to the robot; it checks
that sending a draft is refused. Screenshots (studio_*.png) go to the current directory. No coordinates are printed.
"""
import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PANEL_URL", "http://localhost:8426")).rstrip("/")
OUT = Path.cwd()
results, errors = [], []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True,
                                args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    page = browser.new_page(viewport={"width": 1500, "height": 950})
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("dialog", lambda d: d.accept())
    open_draft = page.request.get(URL + "/api/studio").json().get("draft")
    if open_draft and len(open_draft["log"]) > 1 and os.environ.get("E2E_FORCE") != "1":
        sys.exit(f"{URL} has a draft with {len(open_draft['log']) - 1} edit(s) open; not touching it (E2E_FORCE=1 overrides)")
    page.request.delete(URL + "/api/studio/draft")
    page.goto(URL + "/#studio")
    page.wait_for_function("() => state.map && state.map.loaded() && state.map.getLayer('studio-handles') && state.map.getLayer('areas-fill') && studio.data && document.querySelector('.studiobar button')", timeout=60000)
    page.wait_for_timeout(2000)
    page.screenshot(path=str(OUT / "studio_1_open.png"))

    js = page.evaluate
    bar = js("() => [...document.querySelectorAll('.studiobar button')].map(b => b.textContent)")
    check("the map bar has the add tools", all(t in bar for t in ("+ Area", "+ No-go", "+ No-vision", "+ Pathway", "Measure")), " | ".join(bar))
    check("the panel opens with editing help", "Click a shape" in js("() => document.querySelector('#p-studio').innerText"))
    check("camera coverage is hidden in the Studio", js("() => state.map.getLayoutProperty('cameras-fill', 'visibility')") == "none")

    def to_px(c):
        return js("""(c) => { const r = document.querySelector('#map').getBoundingClientRect();
                     const p = state.map.project(c); return [r.left + p.x, r.top + p.y]; }""", c)

    def coords(kind, fid):
        return js("([k, i]) => { const f = activeGeo().features.find(f => f.properties.kind === k && f.properties.id === i); return f ? coordsOf(f) : null; }", [kind, fid])

    def change(kind, fid):
        return js("([k, i]) => studio.draft && [...studio.draft.diff.changed, ...studio.draft.diff.added, ...studio.draft.diff.removed].find(r => r.kind === k && r.id === i) || null", [kind, fid])

    def bar_status():
        return js("() => document.querySelector('.studiobar .barstatus')?.innerText || ''")

    # select by clicking inside a no-go zone: handles appear straight away, no draft yet
    # the first no-go zone on the map (ids change when the map is edited in the app)
    NOGO = js("() => activeGeo().features.find(f => f.properties.kind === 'nogozones').properties.id")
    ring = coords("nogozones", NOGO)
    centre = [sum(c[0] for c in ring) / len(ring), sum(c[1] for c in ring) / len(ring)]
    js("(c) => state.map.jumpTo({ center: c, zoom: 21 })", centre)
    page.wait_for_timeout(800)
    page.mouse.click(*to_px(centre))
    page.wait_for_timeout(400)
    n0 = js("() => studio.edit && studio.edit.coords.length")
    handles = js("() => state.map.getSource('studio-handles')._data.features.length")
    check("clicking a shape selects it and shows its handles", js("() => studio.sel") == {"kind": "nogozones", "id": NOGO} and handles == 2 * n0,
          f"{n0} corners, {handles} handles")
    check("selecting alone does not start a draft", js("() => studio.draft") is None and "No edits" in bar_status())

    # drag a corner: the first edit starts the draft
    hx, hy = to_px(js("() => studio.edit.coords[0]"))
    page.mouse.move(hx, hy); page.mouse.down(); page.mouse.move(hx + 40, hy, steps=8); page.mouse.up()
    page.wait_for_timeout(1500)
    ch = change("nogozones", NOGO)
    check("dragging a corner starts a draft and reshapes the zone", ch and ch["max_move_m"] > 0.1, json.dumps(ch and ch["changes"]))
    check("the map did not pan during the drag", js("(c) => { const m = state.map.getCenter(); return Math.abs(m.lng - c[0]) < 1e-7 && Math.abs(m.lat - c[1]) < 1e-7; }", centre))
    check("the bar shows the draft", "Draft · 1 change" in bar_status(), bar_status())

    # drag the whole zone from inside it: every corner moves by the same amount
    js("() => { window.__before = studio.edit.coords.map(c => c.slice()); }")
    cx, cy = to_px(centre)
    page.mouse.move(cx, cy); page.mouse.down(); page.mouse.move(cx, cy + 80, steps=8); page.mouse.up()
    page.wait_for_timeout(1500)
    shift = js("""() => { const a = __before, b = studio.edit.coords, k = 111320 * Math.cos(a[0][1] * Math.PI / 180);
                 if (a.length !== b.length) return null;
                 const d = a.map((p, i) => Math.hypot((b[i][0] - p[0]) * k, (b[i][1] - p[1]) * 110540)); return [Math.min(...d), Math.max(...d)]; }""")
    check("dragging inside a no-go zone moves it whole", shift and shift[0] > 1 and shift[1] - shift[0] < 0.01,
          shift and f"every corner moved {shift[0]:.2f}-{shift[1]:.2f} m")

    # midpoint adds a corner, right-click removes one
    n1 = js("() => studio.edit.coords.length")
    page.mouse.click(*to_px(js("() => { const c = studio.edit.coords; return [(c[1][0] + c[2][0]) / 2, (c[1][1] + c[2][1]) / 2]; }")))
    page.wait_for_timeout(1500)
    n2 = js("() => studio.edit.coords.length")
    check("clicking a midpoint adds a corner", n2 == n1 + 1, f"{n1} -> {n2}")
    page.mouse.click(*to_px(js("() => studio.edit.coords[3]")), button="right")
    page.wait_for_timeout(1500)
    check("right-clicking a corner removes it", js("() => studio.edit.coords.length") == n2 - 1)

    # rename in place: the pencil on the selected shape, Enter saves; the pencil on a list row, Esc cancels; F2
    page.click(".selcard .selhead button[title^='Rename']")
    page.fill(".selcard .namefield", "rose bed")
    page.keyboard.press("Enter")
    page.wait_for_timeout(1500)
    check("renaming with the pencil", js("(i) => studio.draft.features.find(f => f.kind === 'nogozones' && f.id === i)?.name", NOGO) == "rose bed")
    row = page.locator(".shaperow", has_text="rose bed")
    row.hover()
    row.locator("button.rowedit").click()
    page.fill(".shaperow .namefield", "never saved")
    page.keyboard.press("Escape")
    page.wait_for_timeout(800)
    check("Esc cancels a rename in the list", js("(i) => studio.draft.features.find(f => f.kind === 'nogozones' && f.id === i)?.name", NOGO) == "rose bed"
          and js("() => !document.querySelector('.namefield')"))
    page.mouse.move(5, 5)
    js("() => document.activeElement && document.activeElement.blur()")
    page.keyboard.press("F2")
    page.wait_for_timeout(300)
    check("F2 renames the selected shape", js("() => document.activeElement && document.activeElement.classList.contains('namefield')"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    # Delete key, then Ctrl+Z
    page.mouse.click(*to_px(js("() => studio.edit.coords.reduce((a, c) => [a[0] + c[0] / studio.edit.coords.length, a[1] + c[1] / studio.edit.coords.length], [0, 0])")))
    page.wait_for_timeout(300)
    page.keyboard.press("Delete")
    page.wait_for_timeout(1500)
    removed = [r["id"] for r in js("() => studio.draft.diff.removed")]
    check("Delete removes the selected zone", NOGO in removed, json.dumps(removed))
    page.keyboard.press("Control+z")
    page.wait_for_timeout(1500)
    check("Ctrl+Z undoes it", NOGO not in [r["id"] for r in js("() => studio.draft.diff.removed")])
    page.keyboard.press("Escape")

    # an empty spot inside "All Yard", clear of other shapes
    spot = js("""() => { const f = activeGeo().features.find(f => f.properties.kind === 'areas' && f.properties.id === 11);
        const ring = f.geometry.coordinates[0]; const xs = ring.map(p => p[0]), ys = ring.map(p => p[1]);
        const free = (p) => inRing(p, ring) && !activeGeo().features.some(g => g.properties.kind !== 'areas' && g.geometry.type === 'Polygon' && inRing(p, g.geometry.coordinates[0]));
        for (let i = 1; i < 30; i++) for (let j = 1; j < 30; j++) {
          const p = [Math.min(...xs) + (Math.max(...xs) - Math.min(...xs)) * i / 30, Math.min(...ys) + (Math.max(...ys) - Math.min(...ys)) * j / 30];
          if ([[0, 0], [8e-5, 0], [8e-5, 5e-5], [0, 5e-5]].every(([dx, dy]) => free([p[0] + dx, p[1] + dy]))) return p; }
        return null; }""")
    js("(c) => state.map.jumpTo({ center: [c[0] + 4e-5, c[1] + 2.5e-5], zoom: 20.5 })", spot)
    page.wait_for_timeout(800)
    pt = lambda dx, dy: to_px([spot[0] + dx, spot[1] + dy])

    # + No-go: four corners (one double-clicked), close on the first
    page.get_by_role("button", name="+ No-go").click()
    corners = [pt(0, 0), pt(3e-5, 0), pt(3e-5, 2e-5), pt(0, 2e-5)]
    for i, c in enumerate(corners):
        page.mouse.click(*c)
        if i == 1:
            page.mouse.click(*c)
        page.wait_for_timeout(100)
    check("the add panel shows progress", "4 corners" in js("() => document.querySelector('.studiopanel')?.innerText || ''"))
    page.mouse.click(*corners[0])
    page.wait_for_timeout(1800)
    added = js("() => studio.draft.diff.added")
    zone = next((a for a in added if a["kind"] == "nogozones"), None)
    check("+ No-go draws a new zone and selects it", zone and js("() => studio.sel")["id"] == zone["id"],
          json.dumps(zone) + " | toast: " + str(js("() => document.querySelector('#toast')?.hidden ? '' : document.querySelector('#toast')?.innerText")))
    page.screenshot(path=str(OUT / "studio_2_added.png"))

    # + Pathway: two points, finished with a second click on the last
    page.get_by_role("button", name="+ Pathway").click()
    page.mouse.click(*pt(5e-5, 0)); page.mouse.click(*pt(7e-5, 4e-5)); page.wait_for_timeout(150); page.mouse.click(*pt(7e-5, 4e-5))
    page.wait_for_timeout(1800)
    path = next((a for a in js("() => studio.draft.diff.added") if a["kind"] == "pathways"), None)
    row = path and js("(i) => studio.draft.features.find(f => f.kind === 'pathways' && f.id === i)", path["id"])
    check("+ Pathway draws a path joined to the area it is in", row and row["start_id"] == 11 and row["end_id"] == 11, json.dumps(row and [row["start_id"], row["end_id"]]))

    # + Area: a triangle, finished with Enter
    page.get_by_role("button", name="+ Area").click()
    for c in (pt(0, 3e-5), pt(2e-5, 3e-5), pt(1e-5, 5e-5)):
        page.mouse.click(*c)
    page.keyboard.press("Enter")
    page.wait_for_timeout(1800)
    check("+ Area draws an area", any(a["kind"] == "areas" for a in js("() => studio.draft.diff.added")))

    # measure
    page.get_by_role("button", name="Measure").click()
    page.mouse.click(400, 400); page.mouse.click(600, 400)
    page.wait_for_timeout(300)
    check("Measure shows a distance", "Distance" in js("() => document.querySelector('.studiopanel')?.innerText || ''"))
    page.keyboard.press("Escape")

    check("the draft has no errors", not js("() => studio.draft.issues.filter(i => i.level === 'error').map(i => i.msg)"))

    # sending: the drawn area cannot be sent; Revert drops it; the rest is listed command by command
    js("() => setStudioTab('draft')")
    page.wait_for_timeout(300)
    panel = lambda: js("() => document.querySelector('#draftcard')?.innerText || ''")
    blocked = js("() => studio.draft.send.blocked.map(b => b.kind)")
    check("a drawn area is listed as not sendable", "areas" in blocked and "can't be sent" in panel().lower(), json.dumps(blocked))
    page.locator("#draftcard .blockedlist button", has_text="Revert").first.click()
    page.wait_for_timeout(1500)
    check("Revert drops it from the draft", "areas" not in js("() => studio.draft.send.blocked.map(b => b.kind)"))
    cmds = js("() => studio.draft.send.steps.map(s => s.cmd)")
    check("the commands to send are listed", "save_nogozone" in cmds and "save_pathway" in cmds and "add no-go zone" in panel().lower(), json.dumps(cmds))
    send_btn = page.locator("#draftcard button", has_text="Send to robot")
    failing = js("() => studio.draft.checks.filter(c => !c.ok).map(c => c.name)")
    check("Send stays disabled while a check fails", send_btn.is_disabled() and failing, json.dumps(failing))
    status = js("() => fetch('/api/studio/draft/apply', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'}).then(r => r.status)")
    check("the server wants confirm: true", status == 400, str(status))
    status = js("() => fetch('/api/studio/draft/apply', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({confirm: true, base: studio.draft.meta.updated})}).then(r => r.status)")
    check("the server refuses while checks fail", status == 409, str(status))
    page.locator(".studiobar button", has_text="Discard").click()
    page.wait_for_timeout(1500)
    check("Discard in the bar ends the draft", js("() => studio.draft") is None and "No edits" in bar_status())
    browser.close()

real_errors = [e for e in errors if "favicon" not in e and "409" not in e and "400" not in e]   # the expected refusals
check("no console errors", not real_errors, json.dumps(real_errors[:5]))
print(f"\n{sum(ok for _, ok in results)}/{len(results)} passed")
sys.exit(0 if all(ok for _, ok in results) else 1)
