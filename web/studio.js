/* Map Studio (spec §7): inspect, measure, compare versions and robot backups, import/export, and edit.
   Editing is direct: pick a tool in the bar on the map, or click a shape and drag its corners. The first edit starts
   a draft on the server; the robot's map is never changed (the server refuses to write a draft).
   Loaded after app.js and ops.js; uses their helpers (h, fill, kv, card, colored, getJSON, putJSON, setSource,
   checkList, toast, state…). */
"use strict";

const KIND_COLOR = { areas: MAP.area, nogozones: MAP.nogo, novisionzones: MAP.novis, elec_fence: MAP.fence,
                     pathways: "#F2F5F3", sidewalks: "#C9D4CE", deadends: MUTED };
const KIND_ORDER = ["areas", "nogozones", "novisionzones", "elec_fence", "pathways", "sidewalks", "deadends"];
const KIND_NAME = { areas: "Areas", nogozones: "No-go zones", novisionzones: "No-vision zones", elec_fence: "Fences",
                    pathways: "Pathways", sidewalks: "Sidewalks", deadends: "Dead ends" };
const ADD_TOOLS = {
  areas: { label: "Area", noun: "area", closed: true,
           help: "Click around the edge of the area. Yarbo normally records areas by driving them, so a drawn one stays a draft." },
  nogozones: { label: "No-go", noun: "no-go zone", closed: true, help: "Click around the part the robot must stay out of." },
  novisionzones: { label: "No-vision", noun: "no-vision zone", closed: true, help: "Click around where the robot should ignore its cameras." },
  pathways: { label: "Pathway", noun: "pathway", closed: false,
              help: "Click along the route, starting inside one area and ending inside another." },
  deadends: { label: "Dead end", noun: "dead end", closed: false,
              help: "Click along the route, starting inside an area: the robot drives out along it and back the same way." },
};
const MOVABLE = new Set(["nogozones", "novisionzones"]);
const ROUTE_GREY = "#C3C9CC";
const STUDIO_LAYERS = ["studio-overlay-fill", "studio-overlay-line", "studio-diff-fill", "studio-diff-old", "studio-diff-new",
                       "studio-sel-fill", "studio-sel-line", "studio-ghost-fill", "studio-ghost-line", "studio-work-fill",
                       "studio-work-line", "studio-work-pts", "studio-handles", "studio-hover-fill", "studio-hover-line",
                       "studio-route-line", "studio-route-ends"];
// Tidy (app/tidy.py). Areas only shrink and no-go zones only grow when tidied; see BIAS_NOTE.
const TIDY_KINDS = new Set(["areas", "nogozones", "novisionzones", "pathways", "sidewalks", "deadends"]);
const LINE_KINDS = new Set(["pathways", "sidewalks", "deadends"]);
const TIDY_TOOLS = [
  ["cleanup", "Clean up", "Drop duplicate points and points that sit on a straight line"],
  ["straighten", "Straighten", "Turn wobbly edges into straight lines; real corners stay"],
  ["round", "Round", "Make a near-circle a true circle"],
  ["smooth", "Smooth", "Even out gentle curves; sharp corners stay"],
  ["square", "Square", "Make a four-cornered shape a true rectangle"],
];
const TOOL_LABEL = Object.fromEntries(TIDY_TOOLS.map(([id, label]) => [id, label]));
const BIAS_NOTE = {
  nogozones: "Tidying only ever grows a no-go zone, so it never gets closer to what it protects.",
  areas: "Tidying only ever shrinks an area, so the robot never mows past where it was taught.",
  novisionzones: "Tidying only ever shrinks a no-vision zone.",
  pathways: "A pathway keeps its ends within the tolerance, so it still joins the same areas.",
  sidewalks: "Sidewalks cannot be sent to the robot yet, so the result stays in the draft.",
  deadends: "A dead end keeps its start within the tolerance, so it still leaves the same area.",
};
const capital = (s) => (s ? s[0].toUpperCase() + s.slice(1) : "");
function loadPref(key, fallback, ok) {
  try { const v = JSON.parse(localStorage.getItem(`yarbo.studio.${key}`)); return ok(v) ? v : fallback; } catch { return fallback; }
}
function savePref(key, v) { try { localStorage.setItem(`yarbo.studio.${key}`, JSON.stringify(v)); } catch { /* per-browser nicety only */ } }

const studio = {
  data: null,        // GET /api/studio
  current: null,     // the robot's map as GeoJSON
  draft: null,       // GET /api/studio/draft, or null
  compare: null,     // GET /api/studio/diff
  sel: null,         // { kind, id }
  edit: null,        // { kind, id, closed, coords } of the selected shape, while its handles are shown
  mode: "select",    // "select" | "measure" | "add:<kind>"
  work: null,        // state of the measure/add tool
  busy: false,       // an edit is on its way to the server
  overlay: null,
  overlayOn: true,
  tol: loadPref("tol", 10, (v) => Number.isFinite(v) && v >= 2 && v <= 30),   // tidy tolerance, cm
  tidy: null,        // { key, tool, loading, result } while a tidy preview is shown
  review: null,      // { tol, rows, picked } of the whole-map review
  shapeMode: "points",                                                    // add tools: points | circle | rectangle
  tidyOnFinish: loadPref("tidyOnFinish", false, (v) => typeof v === "boolean"),
  pw: null,          // { id, loading, data, error, edits, saving } for the selected pathway's mow settings
  pwUndo: {},        // "kind:id" -> the values from before the last settings save there
  advanced: loadPref("advanced", false, (v) => typeof v === "boolean"),   // mow settings: Fine control (finer steps, every value)
  appAdvOpen: loadPref("appAdvOpen", true, (v) => typeof v === "boolean"), // the app's "Advanced settings" section unfolded
  route: null,       // { id, source, route, points, length_m, total_time_s } of a route preview on the map
  routeBusy: false,
  pts: new Set(),    // corners of the selected shape picked for nudging (indexes into edit.coords)
  nudgeCm: loadPref("nudgeCm", 5, (v) => Number.isFinite(v) && v >= 0.5 && v <= 100),   // one nudge, cm
  pickFrom: null,    // [lon, lat] of the first picked corner when it was picked: "Moved" is measured from it
  nudgeTimer: null,  // nudges are sent as one draft edit once they pause
};
function setTol(cm) { studio.tol = Math.max(2, Math.min(30, Math.round(cm))); savePref("tol", studio.tol); }

async function sendJSON(method, url, body) {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await r.text();
  let data = null;
  try { data = JSON.parse(text); } catch { /* not json */ }
  if (!r.ok) throw Object.assign(new Error((data && (data.detail || data.refused)) || `${r.status} ${text}`), { status: r.status, data });
  return data;
}

const fmtM2 = (v) => (v == null ? "—" : `${Math.round(v).toLocaleString()} m²`);
const fmtM = (v) => (v == null ? "—" : `${v < 10 ? v.toFixed(2) : Math.round(v).toLocaleString()} m`);
const featName = (f) => f.name || `${(f.label || f.kind).toLowerCase()} ${f.id}`;
const keyOf = (o) => `${o.kind}:${o.id}`;
const changeCount = () => (studio.draft ? studio.draft.diff.added.length + studio.draft.diff.removed.length + studio.draft.diff.changed.length : 0);

/* ------------------------------------------------------------- geometry */
function lonlatToM(ring) {   // [[lon, lat]] -> metres around the first point
  if (!ring.length) return [];
  const [lon0, lat0] = ring[0], kx = 111320 * Math.cos(lat0 * Math.PI / 180), ky = 110540;
  return ring.map(([lon, lat]) => [(lon - lon0) * kx, (lat - lat0) * ky]);
}
function lengthM(coords, closed) {
  const p = lonlatToM(coords);
  let d = 0;
  for (let i = 1; i < p.length + (closed ? 1 : 0); i++) {
    const a = p[i - 1], b = p[i % p.length];
    d += Math.hypot(b[0] - a[0], b[1] - a[1]);
  }
  return d;
}
function areaM2(coords) {
  const p = lonlatToM(coords);
  let a = 0;
  for (let i = 0; i < p.length; i++) { const [x1, y1] = p[i], [x2, y2] = p[(i + 1) % p.length]; a += x1 * y2 - x2 * y1; }
  return Math.abs(a) / 2;
}
function inRing([x, y], ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < xi + (y - yi) * (xj - xi) / (yj - yi)) inside = !inside;
  }
  return inside;
}
function pxToSegment(p, a, b) {
  const dx = b.x - a.x, dy = b.y - a.y, len = dx * dx + dy * dy;
  const t = len ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len)) : 0;
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}
function coordsOf(f) {    // a feature's outline without the closing point
  const g = f.geometry;
  if (g.type === "Polygon") return g.coordinates[0].slice(0, -1);
  if (g.type === "LineString") return g.coordinates.slice();
  return [g.coordinates];
}
function studioFit(coords) {
  if (!coords.length) return;
  const b = coords.reduce((acc, [x, y]) => [Math.min(acc[0], x), Math.min(acc[1], y), Math.max(acc[2], x), Math.max(acc[3], y)],
                          [Infinity, Infinity, -Infinity, -Infinity]);
  state.map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 90, maxZoom: 21, duration: 400 });
}

/* --------------------------------------------------------------- map setup */
function studioMapInit() {
  const map = state.map;
  for (const id of ["studio-overlay", "studio-diff", "studio-sel", "studio-ghost", "studio-work", "studio-handles", "studio-hover"]) map.addSource(id, { type: "geojson", data: EMPTY });
  const poly = ["==", ["geometry-type"], "Polygon"];
  map.addLayer({ id: "studio-overlay-fill", type: "fill", source: "studio-overlay", filter: poly, paint: { "fill-color": "#F2F5F3", "fill-opacity": 0.06 } });
  map.addLayer({ id: "studio-overlay-line", type: "line", source: "studio-overlay", paint: { "line-color": "#F2F5F3", "line-width": 1.5, "line-dasharray": [1, 1.5] } });
  map.addLayer({ id: "studio-diff-fill", type: "fill", source: "studio-diff", filter: ["all", poly, ["in", ["get", "status"], ["literal", ["added", "new", "removed"]]]],
                 paint: { "fill-color": ["match", ["get", "status"], "removed", CORAL, "added", GREEN, AMBER], "fill-opacity": 0.18 } });
  map.addLayer({ id: "studio-diff-old", type: "line", source: "studio-diff", filter: ["in", ["get", "status"], ["literal", ["removed", "old"]]],
                 paint: { "line-color": CORAL, "line-width": 2.5, "line-dasharray": [2, 1.5] } });
  map.addLayer({ id: "studio-diff-new", type: "line", source: "studio-diff", filter: ["in", ["get", "status"], ["literal", ["added", "new"]]],
                 paint: { "line-color": ["match", ["get", "status"], "added", GREEN, AMBER], "line-width": 3 } });
  map.addLayer({ id: "studio-hover-fill", type: "fill", source: "studio-hover", filter: poly, paint: { "fill-color": SKY, "fill-opacity": 0.18 } });
  map.addLayer({ id: "studio-hover-line", type: "line", source: "studio-hover", layout: { "line-join": "round", "line-cap": "round" },
                 paint: { "line-color": SKY, "line-width": 7, "line-opacity": 0.75 } });
  map.addLayer({ id: "studio-sel-fill", type: "fill", source: "studio-sel", filter: poly, paint: { "fill-color": SKY, "fill-opacity": 0.14 } });
  map.addLayer({ id: "studio-sel-line", type: "line", source: "studio-sel", layout: { "line-join": "round" },
                 paint: { "line-color": SKY, "line-width": 3.5 } });
  // The route preview (an area's or a whole plan's): a solid grey line over the shapes, like the app's, with a sky blue
  // dot where it starts and an amber one where it ends.
  map.addSource("studio-route", { type: "geojson", data: EMPTY });
  map.addSource("studio-route-ends", { type: "geojson", data: EMPTY });
  map.addLayer({ id: "studio-route-line", type: "line", source: "studio-route", layout: { "line-join": "round", "line-cap": "round" },
                 paint: { "line-color": ROUTE_GREY, "line-opacity": 0.95,
                          "line-width": ["interpolate", ["linear"], ["zoom"], 15, 1, 18, 1.6, 20, 2.5, 22, 4] } });
  map.addLayer({ id: "studio-route-ends", type: "circle", source: "studio-route-ends",
                 paint: { "circle-radius": 6, "circle-color": ["match", ["get", "end"], "start", SKY, AMBER],
                          "circle-stroke-color": "#0E1311", "circle-stroke-width": 2 } });
  map.addLayer({ id: "studio-ghost-fill", type: "fill", source: "studio-ghost", filter: poly, paint: { "fill-color": SKY, "fill-opacity": 0.12 } });
  map.addLayer({ id: "studio-ghost-line", type: "line", source: "studio-ghost", layout: { "line-join": "round" },
                 paint: { "line-color": SKY, "line-width": 2.5, "line-dasharray": [2, 1.2] } });
  map.addLayer({ id: "studio-work-fill", type: "fill", source: "studio-work", filter: poly, paint: { "fill-color": SKY, "fill-opacity": 0.2 } });
  map.addLayer({ id: "studio-work-line", type: "line", source: "studio-work", filter: ["!=", ["geometry-type"], "Point"], paint: { "line-color": SKY, "line-width": 2.5 } });
  map.addLayer({ id: "studio-work-pts", type: "circle", source: "studio-work", filter: ["==", ["geometry-type"], "Point"],
                 paint: { "circle-radius": ["case", ["boolean", ["get", "first"], false], 8, ["boolean", ["get", "snap"], false], 6, 5],
                          "circle-color": ["case", ["boolean", ["get", "snap"], false], SKY, "#F2F5F3"],
                          "circle-stroke-color": ["case", ["boolean", ["get", "snap"], false], "#F2F5F3", SKY], "circle-stroke-width": 2 } });
  map.addLayer({ id: "studio-handles", type: "circle", source: "studio-handles",
                 layout: { "circle-sort-key": ["case", ["boolean", ["get", "picked"], false], 2, ["==", ["get", "role"], "vertex"], 1, 0] },   // picked corners on top, midpoints under
                 paint: { "circle-radius": ["case", ["boolean", ["get", "picked"], false], 7.5, ["==", ["get", "role"], "vertex"], 6, 4.5],
                          "circle-color": ["case", ["boolean", ["get", "picked"], false], SKY, ["==", ["get", "role"], "vertex"], "#F2F5F3", AMBER],
                          "circle-opacity": ["case", ["==", ["get", "role"], "vertex"], 1, 0.8],
                          "circle-stroke-color": "#0E1311", "circle-stroke-width": ["case", ["==", ["get", "role"], "vertex"], 2, 1] } });

  if (state.view === "studio") map.boxZoom.disable();   // a page opened on Studio: Shift-drag picks corners, not zooms
  map.on("click", onStudioClick);
  map.on("mousedown", onStudioMouseDown);
  map.on("contextmenu", "studio-handles", onHandleRightClick);
  map.on("mousemove", onStudioHover);
  document.addEventListener("keydown", onStudioKey);
  setStudioLayers(state.view === "studio");
  syncStudioMap();  // the panel may have loaded its data before the map finished loading
  const shown = studio.route?.route ? [studio.route.route] : studio.plans?.route?.route.features;   // and a route preview
  if (shown) showRoute(shown, false);
}

function setStudioLayers(on) {
  const map = state.map;
  if (!map) return;
  for (const id of STUDIO_LAYERS) if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
  if (on && !studio.overlayOn) for (const id of ["studio-overlay-fill", "studio-overlay-line"]) map.setLayoutProperty(id, "visibility", "none");
}

function studioViewChanged(name) {
  const bar = $(".studiobar");
  if (state.map) state.map.boxZoom[name === "studio" ? "disable" : "enable"]();   // Shift-drag picks corners in Studio
  if (name === "studio") {
    setStudioLayers(true);
    applyLayerVisibility();          // camera coverage off unless the Cameras tool asks; the run layers step aside
    if (!bar && $(".mapwrap")) $(".mapwrap").append(h("div", { class: "studiobar", role: "toolbar", "aria-label": "Map editing" }));
    renderStudio();
  } else {
    flushNudge();
    setMode("select");
    selectFeature(null);
    setStudioLayers(false);
    applyLayerVisibility();
    bar?.remove();
    $(".studiopanel")?.remove();
  }
}

/* ----------------------------------------------------------- data + map sync */
async function loadStudio() {
  const [d, cur, ov] = await Promise.all([getJSON("/api/studio"), getJSON("/api/studio/geojson?ref=current").catch(() => EMPTY),
                                          getJSON("/api/studio/overlay").catch(() => EMPTY)]);
  studio.data = d;
  studio.current = cur;
  studio.overlay = ov;
  studio.draft = d.draft ? await getJSON("/api/studio/draft").catch(() => null) : null;
  studio.appEdit = (await getJSON("/api/studio/app-edit").catch(() => ({}))).session || null;
  pollAppEdit();
}

const activeGeo = () => (studio.draft ? studio.draft.geojson : studio.current) || EMPTY;
const activeFeatures = () => (studio.draft ? studio.draft.features : studio.data?.features) || [];
const findGeo = (sel) => sel && activeGeo().features.find((f) => f.properties.kind === sel.kind && f.properties.id === sel.id);
const findRow = (sel) => sel && activeFeatures().find((f) => f.kind === sel.kind && f.id === sel.id);
const editable = (sel) => !!sel && !!KIND_COLOR[sel.kind] && sel.kind !== "elec_fence";

// Hovering (or tabbing to) a row in the shape list lights that shape up on the map.
function hoverShape(f) {
  const g = f && findGeo(f);
  setSource("studio-hover", g ? { type: "FeatureCollection", features: [g] } : EMPTY);
}

// What a pathway joins, by area name. The robot links each end to the area it sits in; an end in no area is the dock
// when a charging point is within 3 m of it, otherwise a pathway's shows amber (a route through it can fail, e.g.
// WP005). A walkway ending outside the areas is normal.
function pathJoins(row) {
  const names = Object.fromEntries(activeFeatures().filter((f) => f.kind === "areas").map((f) => [f.id, f.name || `area ${f.id}`]));
  const g = findGeo(row), cs = g ? coordsOf(g) : [];
  const docks = ((state.mapData && state.mapData.geojson.features) || [])
    .filter((f) => f.properties.kind === "charging" && f.geometry.type === "Point").map((f) => f.geometry.coordinates);
  const end = (id, p) => (id > 0 ? names[id] || `area ${id}`
    : p && docks.some((d) => metres(p, d) < 3) ? "dock" : row.kind === "pathways" ? colored(AMBER, "no area") : "outside the areas");
  return [end(row.start_id, cs[0]), " → ", end(row.end_id, cs[cs.length - 1])];
}

function syncStudioMap() {
  if (!state.map || !state.map.getSource("studio-diff")) return;
  setSource("studio-overlay", studio.overlay || EMPTY);
  setSource("studio-diff", studio.compare ? studio.compare.overlay : studio.draft ? studio.draft.overlay : EMPTY);
  drawSelection();
}

// The selected shape with its handles: white corners to drag, orange midpoints to add a corner.
function drawSelection() {
  if (!state.map || !state.map.getSource("studio-sel")) return;
  const e = studio.edit;
  if (!e) {
    setSource("studio-sel", EMPTY);
    setSource("studio-handles", EMPTY);
    drawNudgePanel();
    return;
  }
  const c = e.coords, n = c.length;
  const outline = e.closed ? { type: "Polygon", coordinates: [[...c, c[0]]] } : { type: "LineString", coordinates: c };
  setSource("studio-sel", { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: outline }] });
  const feats = c.map((p, i) => ({ type: "Feature", properties: { role: "vertex", idx: i, picked: studio.pts.has(i) }, geometry: { type: "Point", coordinates: p } }));
  for (let i = 0; i < n - (e.closed ? 0 : 1); i++) {
    const a = c[i], b = c[(i + 1) % n];
    feats.push({ type: "Feature", properties: { role: "mid", idx: i + 1 }, geometry: { type: "Point", coordinates: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] } });
  }
  setSource("studio-handles", { type: "FeatureCollection", features: e.locked ? [] : feats });
  drawNudgePanel();
}

// quiet: highlight on the map only, leaving the panel as it is (typing in it must not be interrupted).
function selectFeature(sel, zoom, quiet = false) {
  if (studio.tidy && (!sel || keyOf(sel) !== studio.tidy.key)) clearTidy();
  if (!sel || !studio.sel || keyOf(sel) !== keyOf(studio.sel)) { flushNudge(); clearPicks(); }
  studio.sel = sel;
  const f = findGeo(sel);
  studio.edit = f && editable(sel)
    ? { kind: sel.kind, id: sel.id, closed: f.geometry.type === "Polygon", coords: coordsOf(f), locked: !!studio.compare }
    : null;
  drawSelection();
  if (!quiet) { renderStudioPanel(); renderBar(); }
  if (f && zoom) studioFit(coordsOf(f));
}

function refreshSelection() {   // after the draft changed: re-read the selected shape from it
  if (!studio.sel) return;
  if (!findGeo(studio.sel)) { selectFeature(null); return; }
  const f = findGeo(studio.sel);
  // Nudges still waiting to be sent are newer than the draft: keep them on screen.
  if (studio.edit && !studio.nudgeTimer) {
    const next = coordsOf(f);
    if (next.length !== studio.edit.coords.length) clearPicks();   // corners added or removed: the picks no longer match
    studio.edit.coords = next;
  }
  drawSelection();
}

function pickFeature(lngLat) {
  const map = state.map, p = map.project(lngLat), pt = [lngLat.lng, lngLat.lat];
  let best = null;
  for (const f of activeGeo().features) {
    const g = f.geometry, kind = f.properties.kind;
    if (!KIND_COLOR[kind]) continue;
    if (g.type === "LineString") {
      const pts = g.coordinates.map((c) => map.project(c));
      const d = Math.min(...pts.slice(1).map((b, i) => pxToSegment(p, pts[i], b)));
      if (d <= 8 && (!best || best.rank > 0 || d < best.d)) best = { f, rank: 0, d };
    } else if (g.type === "Polygon" && inRing(pt, g.coordinates[0])) {
      const area = f.properties.area_m2 ?? Infinity;
      if (!best || (best.rank === 1 && area < best.area)) best = { f, rank: 1, area };
    }
  }
  return best && { kind: best.f.properties.kind, id: best.f.properties.id };
}

const handlesAt = (point) => (state.map.getLayer("studio-handles") ? state.map.queryRenderedFeatures(point, { layers: ["studio-handles"] }) : []);

function onStudioClick(e) {
  if (state.view !== "studio" || studio.mode !== "select") return;
  if (handlesAt(e.point).length) return;   // corner and midpoint presses are handled on mousedown
  const picked = pickFeature(e.lngLat);
  if (picked) setStudioTab("yard", true);
  selectFeature(picked, false);
}

function onStudioHover(e) {
  if (state.view !== "studio" || studio.mode !== "select" || studio.dragging) return;
  const canvas = state.map.getCanvas();
  if (handlesAt(e.point).length) canvas.style.cursor = "grab";
  else if (studio.edit && MOVABLE.has(studio.edit.kind) && studio.edit.closed && inRing([e.lngLat.lng, e.lngLat.lat], studio.edit.coords)) canvas.style.cursor = "move";
  else canvas.style.cursor = pickFeature(e.lngLat) ? "pointer" : "";
}

/* ----------------------------------------------------- dragging: corners, midpoints, whole zones */
function onStudioMouseDown(e) {
  if (state.view !== "studio" || studio.mode !== "select" || !studio.edit || studio.edit.locked || studio.busy) return;
  if (e.originalEvent && e.originalEvent.button !== 0) return;
  if (e.originalEvent && e.originalEvent.shiftKey) { pickCornersBox(e); return; }
  const w = studio.edit, hits = handlesAt(e.point);
  const v = hits.find((x) => x.properties.role === "vertex"), m = hits.find((x) => x.properties.role === "mid");
  const inside = MOVABLE.has(w.kind) && w.closed && inRing([e.lngLat.lng, e.lngLat.lat], w.coords);
  if (!v && !m && !inside) return;          // anywhere else the map pans as usual
  e.preventDefault();
  const map = state.map, start = [e.lngLat.lng, e.lngLat.lat], orig = w.coords.map((c) => c.slice());
  const startPt = e.point;
  let move;
  let changed = false;
  // A click that wobbles a few pixels is a click, not a drag: nothing changes until the pointer really moves.
  const wobble = (ev) => !changed && Math.hypot(ev.point.x - startPt.x, ev.point.y - startPt.y) < 5;
  if (v || m) {
    let idx;
    if (v) idx = v.properties.idx;
    else {
      idx = m.properties.idx;
      const a = w.coords[idx - 1], b = w.coords[idx % w.coords.length];
      w.coords.splice(idx, 0, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]);
      changed = true;
      drawSelection();
    }
    move = (ev) => { if (!m && wobble(ev)) return; changed = true; w.coords[idx] = [ev.lngLat.lng, ev.lngLat.lat]; drawSelection(); };
  } else {
    move = (ev) => {
      if (wobble(ev)) return;
      changed = true;
      const dx = ev.lngLat.lng - start[0], dy = ev.lngLat.lat - start[1];
      w.coords = orig.map(([x, y]) => [x + dx, y + dy]);
      drawSelection();
    };
  }
  studio.dragging = true;
  map.dragPan.disable();
  map.getCanvas().style.cursor = "grabbing";
  map.on("mousemove", move);
  map.once("mouseup", () => {
    map.off("mousemove", move);
    map.dragPan.enable();
    studio.dragging = false;
    map.getCanvas().style.cursor = "";
    if (!changed) return;
    if (m) clearPicks();          // a new corner shifts the indexes after it
    flushNudge();
    draftEdit({ op: "shape", kind: w.kind, id: w.id, coords: w.coords });
  });
}

function onHandleRightClick(e) {
  const f = e.features?.find((x) => x.properties.role === "vertex");
  const w = studio.edit;
  if (state.view !== "studio" || studio.mode !== "select" || !w || !f) return;
  e.preventDefault();
  if (w.coords.length <= (w.closed ? 3 : 2)) { toast(`A ${w.closed ? "shape" : "path"} needs at least ${w.closed ? 3 : 2} corners.`); return; }
  flushNudge();
  clearPicks();
  w.coords.splice(f.properties.idx, 1);
  drawSelection();
  draftEdit({ op: "shape", kind: w.kind, id: w.id, coords: w.coords });
}

/* ------------------------------------------------- picking corners and nudging them
   With a shape selected, Shift-drag a box to pick the corners inside it (Ctrl+Shift adds to the picks), or
   Shift-click a corner to add or drop it. The picked corners then move together by a set step (0.5 cm and up) with
   the arrow keys or the panel's arrows, north / south / east / west. Nudges that follow each other quickly are sent
   as one draft edit, so one Undo takes back a burst of them. */
const ARROWS = { ArrowUp: [0, 1], ArrowDown: [0, -1], ArrowLeft: [-1, 0], ArrowRight: [1, 0] };
const NUDGE_STEPS = [1, 2, 5, 10, 25];

function clearPicks() {
  studio.pts = new Set();
  studio.pickFrom = null;
}

// Start measuring "Moved" again from where the picked corners are now.
function markPickOrigin() {
  const w = studio.edit, i = Math.min(...studio.pts);
  studio.pickFrom = w && Number.isFinite(i) && w.coords[i] ? { i, at: w.coords[i].slice() } : null;
}

function pickCornersBox(e) {
  const w = studio.edit;
  if (!w || w.locked) return;
  e.preventDefault();
  const map = state.map, wrap = $(".mapwrap"), a = e.point, add = e.originalEvent.ctrlKey || e.originalEvent.metaKey;
  const box = h("div", { class: "pickbox" });
  wrap.append(box);
  let b = a;
  const draw = () => Object.assign(box.style, { left: `${Math.min(a.x, b.x)}px`, top: `${Math.min(a.y, b.y)}px`,
                                               width: `${Math.abs(b.x - a.x)}px`, height: `${Math.abs(b.y - a.y)}px` });
  draw();
  const move = (ev) => { b = ev.point; draw(); };
  studio.dragging = true;
  map.dragPan.disable();
  map.on("mousemove", move);
  map.once("mouseup", () => {
    map.off("mousemove", move);
    map.dragPan.enable();
    studio.dragging = false;
    box.remove();
    flushNudge();
    if (Math.hypot(b.x - a.x, b.y - a.y) < 5) {   // a Shift-click: add or drop the corner under it
      const v = handlesAt(a).find((x) => x.properties.role === "vertex");
      if (!v) return;
      const i = v.properties.idx;
      if (studio.pts.has(i)) studio.pts.delete(i); else studio.pts.add(i);
    } else {
      const [x0, x1] = [Math.min(a.x, b.x), Math.max(a.x, b.x)], [y0, y1] = [Math.min(a.y, b.y), Math.max(a.y, b.y)];
      const inside = w.coords.map((c, i) => [map.project(c), i])
        .filter(([p]) => p.x >= x0 && p.x <= x1 && p.y >= y0 && p.y <= y1).map(([, i]) => i);
      if (!add) studio.pts = new Set();
      for (const i of inside) studio.pts.add(i);
      if (!inside.length && !add) toast("No corners of the selected shape in that box.");
    }
    markPickOrigin();
    drawSelection();
  });
}

// Move the picked corners by one step: x east (+) / west (-), y north (+) / south (-).
function nudgePicked(x, y) {
  const w = studio.edit;
  if (!w || w.locked || !studio.pts.size) return;
  const m = studio.nudgeCm / 100, lat = w.coords[0][1];
  const dLon = (x * m) / (M_LAT * Math.cos((lat * Math.PI) / 180)), dLat = (y * m) / M_LAT;
  for (const i of studio.pts) if (w.coords[i]) w.coords[i] = [w.coords[i][0] + dLon, w.coords[i][1] + dLat];
  drawSelection();
  clearTimeout(studio.nudgeTimer);
  studio.nudgeTimer = setTimeout(flushNudge, 700);
}

// Send nudges still waiting (after a pause, before another edit, on leaving the shape or Studio).
function flushNudge() {
  if (!studio.nudgeTimer) return;
  clearTimeout(studio.nudgeTimer);
  studio.nudgeTimer = null;
  const w = studio.edit;
  if (w) draftEdit({ op: "shape", kind: w.kind, id: w.id, coords: w.coords.map((c) => c.slice()) });
}

function setNudgeCm(v) {
  if (!Number.isFinite(v)) return;
  studio.nudgeCm = Math.max(0.5, Math.min(100, Math.round(v * 2) / 2));
  savePref("nudgeCm", studio.nudgeCm);
  drawNudgePanel(true);
}

const fmtCm = (m) => `${Math.round(Math.abs(m) * 1000) / 10} cm`;
function movedText() {
  const o = studio.pickFrom, w = studio.edit, now = o && w && w.coords[o.i], parts = [];
  if (!now) return "Not moved yet";
  const n = (now[1] - o.at[1]) * M_LAT, e = (now[0] - o.at[0]) * M_LAT * Math.cos((o.at[1] * Math.PI) / 180);
  if (Math.abs(n) >= 0.0005) parts.push(`${fmtCm(n)} ${n > 0 ? "north" : "south"}`);
  if (Math.abs(e) >= 0.0005) parts.push(`${fmtCm(e)} ${e > 0 ? "east" : "west"}`);
  return parts.length ? `Moved ${parts.join(", ")}` : "Not moved yet";
}

/* The panel while corners are picked: how many, the arrows, the step, and how far they have moved. Rebuilt only when
   one of those changes, so a drag elsewhere does not redraw it on every frame. The map's own arrow-key panning is off
   while it is up, so the arrow keys move corners instead. */
function drawNudgePanel(force = false) {
  const w = studio.edit, on = !!(w && !w.locked && studio.pts.size && studio.mode === "select" && state.view === "studio");
  const kb = state.map && state.map.keyboard;
  if (kb && state.view === "studio") kb[on ? "disable" : "enable"]();
  const cur = $(".nudgepanel");
  if (!on) { cur?.remove(); studio.nudgeSig = null; return; }
  const sig = `${keyOf(w)}|${studio.pts.size}|${studio.nudgeCm}|${movedText()}|${w.coords.length}`;
  if (cur && !force && studio.nudgeSig === sig) return;
  studio.nudgeSig = sig;
  const row = findRow(studio.sel), n = studio.pts.size, step = studio.nudgeCm;
  const arrow = (x, y, label, glyph) => h("button", { type: "button", class: "btn nudgebtn", title: `Move ${label} ${step} cm`,
    "aria-label": `Move ${label} ${step} cm`, onclick: () => nudgePicked(x, y) }, glyph);
  const el = h("div", { class: "alignpanel studiopanel nudgepanel", role: "dialog", "aria-label": "Move picked corners" },
    h("p", { class: "label" }, `${n} corner${n === 1 ? "" : "s"} picked`, row ? h("span", { class: "muted" }, ` · ${featName(row)}`) : null),
    h("div", { class: "nudgepad" },
      h("span"), arrow(0, 1, "north", "↑"), h("span"),
      arrow(-1, 0, "west", "←"), h("span", { class: "nudgestep mono" }, `${step} cm`), arrow(1, 0, "east", "→"),
      h("span"), arrow(0, -1, "south", "↓"), h("span")),
    h("div", { class: "row gap nudgesteps" },
      h("span", { class: "muted small" }, "Step"),
      NUDGE_STEPS.map((c) => h("button", { type: "button", class: `btn slim${c === step ? " on" : ""}`, onclick: () => setNudgeCm(c) }, `${c}`)),
      h("input", { class: "field nudgecm", type: "number", min: 0.5, max: 100, step: 0.5, value: step, "aria-label": "Step in centimetres",
                   onchange: (e) => setNudgeCm(parseFloat(e.target.value)) }),
      h("span", { class: "muted small" }, "cm")),
    h("p", { class: "small" }, movedText()),
    h("div", { class: "row gap" },
      h("button", { type: "button", class: "btn slim", onclick: () => { flushNudge(); w.coords.forEach((_, i) => studio.pts.add(i)); markPickOrigin(); drawSelection(); } }, "Pick all corners"),
      h("button", { type: "button", class: "btn slim", onclick: () => { flushNudge(); clearPicks(); drawSelection(); } }, "Done")),
    h("p", { class: "muted small" }, "Arrow keys move them too. Shift-drag a box to pick corners, Ctrl+Shift-drag to add more, Shift-click one to add or drop it. Undo takes back a burst of moves."));
  if (cur) cur.replaceWith(el); else { $(".studiopanel")?.remove(); $(".mapwrap").append(el); }
}

/* ------------------------------------------------------------------ modes */
function setMode(mode) {
  const map = state.map;
  flushNudge();
  if (studio.work?.off) studio.work.off();
  studio.work = null;
  studio.mode = mode;
  clearTidy();
  $(".studiopanel")?.remove();
  if (map && map.getSource("studio-work")) {
    setSource("studio-work", EMPTY);
    map.getCanvas().style.cursor = "";
    map.doubleClickZoom.enable();
  }
  if (mode !== "select") selectFeature(null);
  if (mode === "measure") startMeasure();
  else if (mode.startsWith("add:")) startAdd(mode.slice(4));
  renderBar();
}

function onStudioKey(e) {
  if (e.defaultPrevented || state.view !== "studio" || e.target.closest?.("input, textarea, select")) return;
  const undoKey = e.key === "z" && (e.ctrlKey || e.metaKey);
  if (studio.work?.pts && (e.key === "Backspace" || undoKey)) {
    e.preventDefault();
    studio.work.pts.pop();
    studio.work.redraw();
  } else if (e.key === "Enter" && studio.work?.finish) studio.work.finish();
  else if (ARROWS[e.key] && studio.mode === "select" && studio.pts.size && studio.edit && !studio.edit.locked) {
    e.preventDefault();
    const [x, y] = ARROWS[e.key];
    nudgePicked(x, y);
  } else if (e.key === "Escape") {
    if (studio.mode !== "select") setMode("select");
    else if (studio.pts.size) { flushNudge(); clearPicks(); drawSelection(); }
    else selectFeature(null);
  } else if (undoKey && studio.mode === "select") { e.preventDefault(); undoEdit(); }
  else if (e.key === "F2" && studio.mode === "select" && studio.sel && editable(studio.sel)) { e.preventDefault(); setStudioTab("yard", true); startRename(findRow(studio.sel)); }
  else if ((e.key === "Delete" || e.key === "Backspace") && studio.mode === "select" && studio.sel && editable(studio.sel)) {
    e.preventDefault();
    deleteSelected();
  }
}

function floatPanel(...children) {
  $(".studiopanel")?.remove();
  const el = h("div", { class: "alignpanel studiopanel", role: "dialog" }, ...children);
  $(".mapwrap").append(el);
  return el;
}

/* Drawing aids. While placing points: a point within 10 px of an existing corner lands on it, within 8 px of an
   edge lands on the edge; Shift turns the new segment in 15° steps from the previous one (or from east for the
   first); Alt places it exactly where clicked. */
const M_LAT = 111320;                         // as app/geo.py and the SDK
const mPerLon = (lat) => 111320 * Math.cos(lat * Math.PI / 180);

function angleSnap(p, pts) {
  const last = pts[pts.length - 1], kx = mPerLon(last[1]);
  const dx = (p[0] - last[0]) * kx, dy = (p[1] - last[1]) * M_LAT, len = Math.hypot(dx, dy);
  if (!len) return p;
  let ref = 0;
  if (pts.length >= 2) { const prev = pts[pts.length - 2]; ref = Math.atan2((last[1] - prev[1]) * M_LAT, (last[0] - prev[0]) * kx); }
  const step = Math.PI / 12, ang = ref + Math.round((Math.atan2(dy, dx) - ref) / step) * step;
  return [last[0] + len * Math.cos(ang) / kx, last[1] + len * Math.sin(ang) / M_LAT];
}

function snapLngLat(lngLat, ev, w) {
  const map = state.map, oe = ev && ev.originalEvent, raw = [lngLat.lng, lngLat.lat];
  if (oe && oe.shiftKey && w && w.pts.length) return { p: angleSnap(raw, w.pts), how: "angle" };
  if (oe && oe.altKey) return { p: raw, how: null };
  const px = map.project(lngLat);
  const shapes = activeGeo().features.filter((f) => KIND_COLOR[f.properties.kind]);
  let best = null;
  for (const f of shapes) {
    for (const c of coordsOf(f)) {
      const q = map.project(c), d = Math.hypot(q.x - px.x, q.y - px.y);
      if (d <= 10 && (!best || d < best.d)) best = { d, p: c, how: "corner" };
    }
  }
  if (best) return best;
  for (const f of shapes) {
    const cs = coordsOf(f), closedF = f.geometry.type === "Polygon";
    for (let i = 0; i < cs.length - (closedF ? 0 : 1); i++) {
      const a = map.project(cs[i]), b = map.project(cs[(i + 1) % cs.length]);
      const dx = b.x - a.x, dy = b.y - a.y, L = dx * dx + dy * dy;
      if (!L) continue;
      const t = Math.max(0, Math.min(1, ((px.x - a.x) * dx + (px.y - a.y) * dy) / L));
      const qx = a.x + t * dx, qy = a.y + t * dy, d = Math.hypot(px.x - qx, px.y - qy);
      if (d <= 8 && (!best || d < best.d)) { const ll = map.unproject([qx, qy]); best = { d, p: [ll.lng, ll.lat], how: "edge" }; }
    }
  }
  return best || { p: raw, how: null };
}

// Click-to-place points for Measure and the Add tools. A click on the last point is ignored (double-clicks), except
// that it finishes a pathway; a click on the first point closes a shape.
function pointTool({ closed, onRedraw, onFinish }) {
  const map = state.map;
  const w = { pts: [], hover: null, snap: null };
  studio.work = w;
  map.doubleClickZoom.disable();
  map.boxZoom.disable();                         // Shift is the angle snap while drawing, not box zoom
  map.getCanvas().style.cursor = "crosshair";
  const near = (a, b, tol) => { const pa = map.project(a), pb = map.project(b); return Math.hypot(pa.x - pb.x, pa.y - pb.y) <= tol; };
  w.redraw = () => {
    const ring = w.hover && w.pts.length ? [...w.pts, w.hover] : w.pts;
    const feats = w.pts.map((p, i) => ({ type: "Feature", properties: { first: closed && i === 0 && w.pts.length >= 3 }, geometry: { type: "Point", coordinates: p } }));
    if (closed && ring.length >= 3) feats.push({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [[...ring, ring[0]]] } });
    else if (ring.length >= 2) feats.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: ring } });
    if (w.hover && w.snap) feats.push({ type: "Feature", properties: { snap: true }, geometry: { type: "Point", coordinates: w.hover } });
    setSource("studio-work", { type: "FeatureCollection", features: feats });
    onRedraw(w);
  };
  const click = (e) => {
    const raw = [e.lngLat.lng, e.lngLat.lat];
    if (closed && w.pts.length >= 3 && near(raw, w.pts[0], 12)) { w.finish?.(); return; }
    if (w.pts.length && near(raw, w.pts[w.pts.length - 1], 8)) {
      if (!closed && w.pts.length >= 2) w.finish?.();   // a path ends with a second click on its last point
      return;
    }
    w.pts.push(snapLngLat(e.lngLat, e, w).p);
    w.redraw();
  };
  const move = (e) => { const s = snapLngLat(e.lngLat, e, w); w.hover = s.p; w.snap = s.how; w.redraw(); };
  const out = () => { w.hover = null; w.snap = null; w.redraw(); };
  map.on("click", click);
  map.on("mousemove", move);
  map.on("mouseout", out);
  w.off = () => { map.off("click", click); map.off("mousemove", move); map.off("mouseout", out); };   // box zoom stays off in Studio (Shift-drag picks corners)
  if (onFinish) w.finish = () => onFinish(w);
  w.redraw();
  return w;
}

// Circle and rectangle: a fixed number of clicks (2 or 3), the shape follows the cursor until the last one.
function shapeTool({ need, build, describe, onFinish }) {
  const map = state.map;
  const w = { pts: [], hover: null, snap: null };
  studio.work = w;
  map.doubleClickZoom.disable();
  map.boxZoom.disable();
  map.getCanvas().style.cursor = "crosshair";
  w.redraw = () => {
    const ring = build(w.hover ? [...w.pts, w.hover] : w.pts);
    const feats = w.pts.map((p) => ({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: p } }));
    if (ring && ring.length >= 3) feats.push({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [[...ring, ring[0]]] } });
    else if (w.pts.length && w.hover) feats.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: [...w.pts, w.hover] } });
    if (w.hover && w.snap) feats.push({ type: "Feature", properties: { snap: true }, geometry: { type: "Point", coordinates: w.hover } });
    setSource("studio-work", { type: "FeatureCollection", features: feats });
    describe(w, ring);
  };
  const click = (e) => {
    w.pts.push(snapLngLat(e.lngLat, e, w).p);
    if (w.pts.length >= need) { w.hover = null; w.finish(); return; }
    w.redraw();
  };
  const move = (e) => { const s = snapLngLat(e.lngLat, e, w); w.hover = s.p; w.snap = s.how; w.redraw(); };
  map.on("click", click);
  map.on("mousemove", move);
  w.off = () => { map.off("click", click); map.off("mousemove", move); };
  w.finish = () => {
    const ring = build(w.pts.length >= need ? w.pts.slice(0, need) : [...w.pts, ...(w.hover ? [w.hover] : [])]);
    if (!ring || ring.length < 3) { w.pts = w.pts.slice(0, need - 1); toast("Not finished yet: keep clicking."); w.redraw(); return; }
    onFinish(ring);
  };
  w.redraw();
  return w;
}

// n points around c through e (in metres): about every 40 cm, 16 to 48 of them; `outside` puts the polygon around
// the circle so nothing inside the drawn circle is left out (used for no-go zones).
function circleRing(c, e, outside) {
  if (!c || !e) return null;
  const kx = mPerLon(c[1]), r = Math.hypot((e[0] - c[0]) * kx, (e[1] - c[1]) * M_LAT);
  if (r < 0.2) return null;
  const n = Math.max(16, Math.min(48, Math.ceil(2 * Math.PI * r / 0.4)));
  const R = outside ? r / Math.cos(Math.PI / n) : r;
  return Array.from({ length: n }, (_, i) => [c[0] + R * Math.cos(2 * Math.PI * i / n) / kx, c[1] + R * Math.sin(2 * Math.PI * i / n) / M_LAT]);
}

// A rectangle from one side (a → b) and how far the third click is from that side.
function rectRing(a, b, c) {
  if (!a || !b || !c) return null;
  const kx = mPerLon(a[1]);
  const toM = (p) => [(p[0] - a[0]) * kx, (p[1] - a[1]) * M_LAT], toLL = (m) => [a[0] + m[0] / kx, a[1] + m[1] / M_LAT];
  const B = toM(b), C = toM(c), L = Math.hypot(B[0], B[1]);
  if (L < 0.2) return null;
  const nrm = [-B[1] / L, B[0] / L], d = C[0] * nrm[0] + C[1] * nrm[1];
  if (Math.abs(d) < 0.2) return null;
  return [[0, 0], B, [B[0] + nrm[0] * d, B[1] + nrm[1] * d], [nrm[0] * d, nrm[1] * d]].map(toLL);
}
const metres = (a, b) => Math.hypot((b[0] - a[0]) * mPerLon(a[1]), (b[1] - a[1]) * M_LAT);

function startMeasure() {
  const read = h("p", { class: "small" });
  floatPanel(h("p", { class: "label" }, "Measure"),
    h("p", { class: "muted small" }, "Click points on the map. Backspace removes the last one; Esc ends."), read,
    h("div", { class: "row gap" },
      h("button", { type: "button", class: "btn", onclick: () => { studio.work.pts = []; studio.work.redraw(); } }, "Clear"),
      h("button", { type: "button", class: "btn", onclick: () => setMode("select") }, "Done")));
  pointTool({ closed: true, onRedraw: (w) => {
    const pts = w.hover && w.pts.length ? [...w.pts, w.hover] : w.pts;
    if (pts.length < 2) { read.textContent = "Distance —"; return; }
    read.textContent = `Distance ${fmtM(lengthM(pts, false))}` + (pts.length >= 3 ? ` · closed shape ${fmtM2(areaM2(pts))}, perimeter ${fmtM(lengthM(pts, true))}` : "");
  } });
}

async function finishAdd(kind, coords, tidyAfter) {
  setMode("select");
  if (!(await draftEdit({ op: "add", kind, coords }))) return;
  const row = studio.draft.diff.added.filter((r) => r.kind === kind).pop();     // what this edit added
  const added = row && activeFeatures().find((f) => f.kind === kind && f.id === row.id);
  if (!added) return;
  if (tidyAfter) {
    // Only when it would change something, so a clean drawing does not get an empty step or an error toast.
    let res = null;
    try { res = (await sendJSON("POST", "/api/studio/tidy/preview", { kind, id: added.id, tol: 0.05, tools: ["straighten"] })).tools.straighten; }
    catch { /* leave it as drawn */ }
    if (res && res.ok) await draftEdit({ op: "tidy", kind, id: added.id, tool: "straighten", tol: 0.05 });
  }
  setStudioTab("yard", true);
  selectFeature({ kind, id: added.id }, false);
  toast(`Added ${featName(added)} to the draft. F2 renames it.`);
}

function startAdd(kind) {
  const t = ADD_TOOLS[kind];
  const mode = t.closed ? studio.shapeMode : "points";
  const status = h("p", { class: "small" });
  const finish = h("button", { type: "button", class: "btn pri", onclick: () => studio.work?.finish() }, `Add ${t.noun}`);
  const seg = t.closed ? h("div", { class: "seg", role: "group", "aria-label": "How to draw it" },
    [["points", "Points"], ["circle", "Circle"], ["rectangle", "Rectangle"]].map(([id, label]) =>
      h("button", { type: "button", class: id === mode ? "on" : null, "aria-pressed": String(id === mode),
                    onclick: () => { if (id !== mode) { studio.shapeMode = id; setMode(`add:${kind}`); } } }, label))) : null;
  const howTo = {
    points: [t.help, t.closed ? " Click the first point again, or press Enter, to finish." : " Click the last point again, or press Enter, to finish."],
    circle: ["Click the centre, then click again at the edge.", kind === "nogozones" ? " The zone is drawn just outside the circle, so all of it stays out of bounds." : ""],
    rectangle: ["Click two corners along one side, then click to set how deep it is."],
  }[mode];
  const tidyBox = mode === "points" ? h("label", { class: "row gap small" },
    h("input", { type: "checkbox", checked: studio.tidyOnFinish || null,
                 onchange: (e) => { studio.tidyOnFinish = e.target.checked; savePref("tidyOnFinish", e.target.checked); } }),
    "Straighten when finished (within 5 cm)") : null;
  floatPanel(h("p", { class: "label" }, `New ${t.noun}`), seg,
    h("p", { class: "muted small" }, ...howTo),
    h("p", { class: "muted tiny" }, "New points land on nearby corners and edges; hold Shift for 15° steps, Alt to place freely. Backspace removes the last click; Esc cancels."),
    tidyBox, status, h("div", { class: "row gap" }, finish, h("button", { type: "button", class: "btn", onclick: () => setMode("select") }, "Cancel")));
  if (mode === "circle") {
    shapeTool({ need: 2, build: (p) => circleRing(p[0], p[1], kind === "nogozones"),
      describe: (w, ring) => {
        status.textContent = ring ? `Radius ${fmtM(metres(w.pts[0] || w.hover, w.hover || w.pts[1]))} · ${fmtM2(areaM2(ring))}` : (w.pts.length ? "Now click at the edge." : "Click the centre.");
        finish.disabled = !(w.pts.length >= 1 && ring);
      },
      onFinish: (ring) => finishAdd(kind, ring, false) });
    return;
  }
  if (mode === "rectangle") {
    shapeTool({ need: 3, build: (p) => rectRing(p[0], p[1], p[2]),
      describe: (w, ring) => {
        const a = w.pts[0], b = w.pts[1] || w.hover;
        status.textContent = ring ? `${fmtM(metres(ring[0], ring[1]))} × ${fmtM(metres(ring[1], ring[2]))} · ${fmtM2(areaM2(ring))}`
          : w.pts.length === 1 && b ? `Side ${fmtM(metres(a, b))}` : w.pts.length ? "Click the second corner." : "Click the first corner.";
        finish.disabled = !(w.pts.length >= 2 && ring);
      },
      onFinish: (ring) => finishAdd(kind, ring, false) });
    return;
  }
  pointTool({
    closed: t.closed,
    onRedraw: (w) => {
      const need = t.closed ? 3 : 2;
      if (w.pts.length < need) status.textContent = `${w.pts.length} point${w.pts.length === 1 ? "" : "s"}; at least ${need} needed.`;
      else status.textContent = t.closed ? `${w.pts.length} corners · ${fmtM2(areaM2(w.pts))}` : `${w.pts.length} points · ${fmtM(lengthM(w.pts, false))}`;
      finish.disabled = w.pts.length < need || (t.closed && areaM2(w.pts) < 0.5);
    },
    onFinish: async (w) => {
      const need = t.closed ? 3 : 2;
      if (w.pts.length < need) { toast(`Add at least ${need} points first.`); return; }
      await finishAdd(kind, w.pts.slice(), studio.tidyOnFinish);
    },
  });
}

/* ------------------------------------------------------------- draft calls */
const draftBase = () => (studio.draft ? studio.draft.meta.updated : null);

// Another window changed the draft: show its version rather than overwrite it.
async function onConflict(e) {
  toast(e.message);
  if (e.status === 409) await refreshStudio();
}

// Edits go to the server one after another, each on top of the draft the previous one produced (tabbing through
// the Names list can fire two renames within a moment). Resolves to whether this edit landed.
let editChain = Promise.resolve();
function draftEdit(edit) {
  const run = editChain.then(() => sendDraftEdit(edit));
  editChain = run.catch(() => false);
  return run;
}

async function sendDraftEdit(edit) {
  studio.busy = true;
  let ok = false;
  try {
    studio.draft = await sendJSON("POST", "/api/studio/draft/edit", { ...edit, base: draftBase() });
    studio.compare = null;
    ok = true;
  } catch (e) { await onConflict(e); }
  studio.busy = false;
  if (ok) forgetPreviews();                    // shapes changed: previews no longer describe them
  syncStudioMap();
  refreshSelection();
  renderStudioPanel();
  renderBar();
  return ok;
}

async function draftAction(method, url, body, msg) {
  try {
    const r = await sendJSON(method, url, body);
    if (msg) toast(msg);
    return r;
  } catch (e) { toast(e.message); return null; }
}

async function undoEdit() {
  if (studio.busy) return;
  if (!studio.draft?.meta.undo) { toast("Nothing to undo."); return; }
  let v;
  try { v = await sendJSON("POST", "/api/studio/draft/undo", { base: draftBase() }); } catch (e) { await onConflict(e); return; }
  studio.draft = v;
  forgetPreviews();
  syncStudioMap();
  refreshSelection();
  renderStudioPanel();
  renderBar();
}

function deleteSelected() {
  const row = findRow(studio.sel);
  if (!row || !confirm(`Delete ${featName(row)} from the draft?`)) return;
  const sel = studio.sel;
  selectFeature(null);
  draftEdit({ op: "delete", kind: sel.kind, id: sel.id });
}

async function startDraft(from) {
  if (changeCount() && !confirm("Replace the current draft? Its edits will be lost.")) return;
  const v = await draftAction("POST", "/api/studio/draft", { from }, "Draft started.");
  if (!v) return;
  studio.draft = v;
  studio.compare = null;
  await refreshStudio();
}

async function discardDraft() {
  if (changeCount() && !confirm("Discard the draft and all its edits?")) return;
  setMode("select");
  try {
    await sendJSON("DELETE", `/api/studio/draft${studio.draft ? `?base=${studio.draft.meta.updated}` : ""}`);
    toast("Draft discarded.");
  } catch (e) { await onConflict(e); return; }
  studio.draft = null;
  await refreshStudio();
}

async function compareWith(a, b = "current") {
  try { studio.compare = await getJSON(`/api/studio/diff?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`); }
  catch (e) { toast(e.message); return; }
  setMode("select");
  selectFeature(null);
  syncStudioMap();
  const all = studio.compare.overlay.features.flatMap(coordsOf);
  if (all.length) studioFit(all);
}

async function fetchBackup(id, button) {
  if (button) { button.disabled = true; button.textContent = "Fetching…"; }
  try {
    const r = await sendJSON("POST", `/api/studio/backups/${id}/fetch`);
    toast(`Backup ${id} fetched: ${Object.entries(r.counts).map(([k, n]) => `${n} ${k}`).join(", ")}.`);
  } catch (e) { toast(e.message); }
  await refreshStudio();
}

async function saveBackup(button) {
  if (button) button.disabled = true;
  try { const r = await sendJSON("POST", "/api/studio/backups/save"); toast(`Saved “${r.backup.name}”.`); }
  catch (e) { toast(e.message); }
  await refreshStudio();
}

async function deleteBackup(b) {
  if (!confirm(`Delete the robot's backup “${b.name || b.id}” (id ${b.id})? This cannot be undone.`)) return;
  try {
    const r = await sendJSON("POST", `/api/studio/backups/${b.id}/delete`, { confirm: true });
    toast(r.ok ? `Deleted “${b.name || b.id}”.` : `Not deleted: ${r.error}`);
  } catch (e) { toast(e.message); }
  await refreshStudio();
}

async function renameBackup(b) {
  const name = prompt(`New name for the robot's backup “${b.name || b.id}” (id ${b.id}):`, b.name || "");
  if (name == null || !name.trim() || name.trim() === b.name) return;
  try {
    const r = await sendJSON("POST", `/api/studio/backups/${b.id}/rename`, { name: name.trim() });
    toast(r.ok ? `Renamed to “${name.trim()}” (read back from the robot).` : `Not renamed: ${r.error}`);
  } catch (e) { toast(e.message); }
  await refreshStudio();
}

const backupDate = (ts) => (ts ? new Date(ts * (ts > 1e11 ? 1 : 1000)).toLocaleString() : "—");

async function restoreBackup(b) {
  let pv;
  try { pv = await sendJSON("GET", `/api/studio/backups/${b.id}/preview`); } catch (e) { toast(e.message); return; }
  if (pv.blocked) { toast(`Cannot restore now: ${pv.blocked}.`); return; }
  const p = pv.preview, names = (list) => list.map((a) => `“${a.name}”`).join(", ");
  const lines = [
    `Replace the robot's map with the backup “${b.name || b.id}” from ${backupDate(b.timestamp)}?`, "",
    p.plans_deleted.some((x) => x.remade) ? `• The robot deletes every plan; the panel makes these again afterwards (same name and areas): ${p.plans_deleted.filter((x) => x.remade).map((x) => x.name).join(", ")}.` : null,
    p.plans_deleted.some((x) => !x.remade) ? `• These plans are LOST (the backup lacks some of their areas): ${p.plans_deleted.filter((x) => !x.remade).map((x) => x.name).join(", ")}.` : null,
    p.plans_deleted.length ? null : "• There are no plans to lose.",
    p.areas_removed.length ? `• These areas are not in the backup and will be gone, with their settings: ${names(p.areas_removed)}.` : null,
    p.areas_added.length ? `• These areas come back from the backup with default settings: ${names(p.areas_added)}.` : null,
    p.areas_kept.length ? `• Mow settings are put back for: ${names(p.areas_kept)} (once the restored map is confirmed).` : null,
    ...p.areas_kept.filter((a) => a.was !== a.name).map((a) => `• “${a.was}” goes back to its name in the backup, “${a.name}”.`),
    "• The map as it is now is kept in History for reference; areas the backup lacks and their settings cannot be put back automatically. For a full undo, Save now (a robot backup) first.",
    "", "The robot must stay docked and idle; this can take a minute."].filter((x) => x !== null);
  if (!confirm(lines.join("\n"))) return;
  studio.busy = true;
  renderStudioPanel();
  toast("Restoring on the robot…");
  try {
    const r = await sendJSON("POST", `/api/studio/backups/${b.id}/restore`, { confirm: true, keep_settings: true });
    toast(restoreSummary(r));
  } catch (e) { toast(e.message); }
  studio.busy = false;
  await refreshStudio();
}

function restoreSummary(r) {
  if (!r.ok) return `Not restored: ${r.error}`;
  const plans = ((r.plans_made || []).length ? ` Plans made again: ${r.plans_made.map((p) => p.name).join(", ")}.` : "") +
    (r.plans_lost.length ? ` Make these plans again (Plans tab): ${r.plans_lost.map((p) => (p.why ? `${p.name} (${p.why})` : p.name)).join(", ")}.` : "");
  const bad = [...r.settings.filter((x) => !x.ok).map((x) => x.name), ...r.settings_not_applied.map((x) => x.name)];
  return `Restored “${r.restored_from.name}”.` + (r.map_matches_backup ? "" : " The map could not be confirmed — check it in the app.") +
         (bad.length ? ` Settings not put back: ${bad.join(", ")}.` : "") + plans +
         ((r.problems || []).length ? ` Problems: ${r.problems.join("; ")}.` : "") +
         (r.cloud_backup === false ? " The cloud copy was not updated." : "");
}

async function refreshBackups(button) {
  if (button) { button.disabled = true; button.textContent = "Asking the robot…"; }
  await draftAction("POST", "/api/studio/backups/refresh", undefined, "Backup list refreshed.");
  await refreshStudio();
}

/* ----------------------------------------------------------- import overlay */
function parseKML(text) {
  const doc = new DOMParser().parseFromString(text, "application/xml");
  const coords = (el) => el.textContent.trim().split(/\s+/).map((t) => t.split(",").slice(0, 2).map(Number)).filter((c) => c.length === 2 && c.every(Number.isFinite));
  const feats = [];
  for (const pm of doc.getElementsByTagName("Placemark")) {
    const name = pm.getElementsByTagName("name")[0]?.textContent || "";
    for (const poly of pm.getElementsByTagName("Polygon")) {
      const ring = poly.getElementsByTagName("coordinates")[0];
      if (ring) feats.push({ type: "Feature", properties: { name }, geometry: { type: "Polygon", coordinates: [coords(ring)] } });
    }
    for (const ls of pm.getElementsByTagName("LineString")) {
      const c = ls.getElementsByTagName("coordinates")[0];
      if (c) feats.push({ type: "Feature", properties: { name }, geometry: { type: "LineString", coordinates: coords(c) } });
    }
    for (const pt of pm.getElementsByTagName("Point")) {
      const c = pt.getElementsByTagName("coordinates")[0];
      if (c) feats.push({ type: "Feature", properties: { name }, geometry: { type: "Point", coordinates: coords(c)[0] } });
    }
  }
  return feats;
}

function parseGeoJSON(text) {
  const j = JSON.parse(text);
  if (j.type === "FeatureCollection") return j.features;
  if (j.type === "Feature") return [j];
  if (j.type && j.coordinates) return [{ type: "Feature", properties: {}, geometry: j }];
  throw new Error("not GeoJSON");
}

async function importOverlay(file) {
  try {
    const text = await file.text();
    const feats = /\.kml$/i.test(file.name) || text.trimStart().startsWith("<") ? parseKML(text) : parseGeoJSON(text);
    if (!feats.length) throw new Error("no shapes found in the file");
    await putJSON("/api/studio/overlay", { type: "FeatureCollection", name: file.name, features: feats });
    studio.overlay = await getJSON("/api/studio/overlay");
    studio.overlayOn = true;
    setStudioLayers(true);
    syncStudioMap();
    studioFit(feats.flatMap((f) => (f.geometry.type === "Point" ? [f.geometry.coordinates] : coordsOf(f))));
    toast(`Imported ${feats.length} shape${feats.length === 1 ? "" : "s"} from ${file.name}.`);
  } catch (e) { toast(`Import failed: ${e.message}`); }
  renderStudioPanel();
}

/* --------------------------------------------------------------- the map bar */
function renderBar() {
  const bar = $(".studiobar");
  if (!bar || !studio.data) return;
  const tool = (mode, label, title) => h("button", { type: "button", class: `tool${studio.mode === mode ? " on" : ""}`, title,
    "aria-pressed": String(studio.mode === mode), onclick: () => setMode(studio.mode === mode ? "select" : mode) }, label);
  const n = changeCount(), errors = studio.draft ? studio.draft.issues.filter((i) => i.level === "error").length : 0;
  fill(bar,
    tool("select", "Select", "Click a shape to select it, then drag its corners"),
    h("span", { class: "sep" }),
    h("span", { class: "barlabel" }, "Add"),
    Object.entries(ADD_TOOLS).map(([kind, t]) => tool(`add:${kind}`, `+ ${t.label}`, `Draw a new ${t.noun}`)),
    h("span", { class: "sep" }),
    tool("measure", "Measure", "Measure a distance or an area"),
    h("button", { type: "button", class: "tool", title: "Undo the last edit (Ctrl+Z)", disabled: !studio.draft?.meta.undo || null, onclick: undoEdit }, "Undo"),
    h("span", { class: "sep" }),
    studio.draft
      ? h("span", { class: "barstatus" },
          h("button", { type: "button", class: "tool on", title: "Review and send the draft", onclick: showDraft },
            `Draft · ${n} change${n === 1 ? "" : "s"}`, errors ? colored(CORAL, ` · ${errors} error${errors === 1 ? "" : "s"}`) : null),
          h("button", { type: "button", class: "tool", onclick: discardDraft }, "Discard"))
      : h("span", { class: "barstatus muted small" }, "No edits yet"));
}

/* ------------------------------------------------------------------- panel */
async function refreshStudio() {
  forgetPreviews();
  try { await loadStudio(); } catch (e) { fill($("#p-studio"), h("p", { class: "muted pad" }, e.message)); return; }
  syncStudioMap();
  refreshSelection();
  renderStudioPanel();
  renderBar();
}

async function renderStudio() {
  if (!studio.data) fill($("#p-studio"), h("p", { class: "muted pad" }, "Loading the map…"));
  await refreshStudio();
}

/* ------------------------------------------------------------------ panel
   Tabs (Yard · Plans · History · More). Yard is the draft (when there is one) on top, then the selected shape and
   the list of shapes. Explanations live in tooltips: the panel shows states, numbers and
   one-line errors, and warns in words only before something that cannot be taken back. */
const STUDIO_TABS = [["yard", "Yard"], ["plans", "Plans"], ["history", "History"], ["more", "More"]];
const DRAFT_ONLY = new Set(["sidewalks", "elec_fence"]);
Object.assign(studio, {
  tab: loadPref("tab", "yard", (v) => STUDIO_TABS.some(([id]) => id === v)),     // "shapes" / "draft" (before Yard) fall back
  renaming: null,     // { key, where: "sel" | "list" } while a name is edited in place (one editor at a time)
  tolOpen: null,      // which tolerance chip is open ("sel" | "list")
  showAll: {},        // per long list: shown in full
});

const stacked = () => window.matchMedia("(max-width: 900px)").matches;    // the panel sits under the map

// `quiet`: switch without redrawing (the caller is about to redraw anyway).
function setStudioTab(id, quiet = false) {
  const panel = $("#p-studio");
  if (studio.tab !== id) {
    studio.tab = id;
    savePref("tab", id);
    if (panel) panel.scrollTop = 0;
    if (!quiet) renderStudioPanel();
  }
  if (panel && stacked() && !quiet) panel.scrollIntoView({ block: "start", behavior: "smooth" });
}

const ICONS = {
  pencil: "M4 20h4L19 9l-4-4L4 16v4zM14 6l4 4",
  target: "M12 2v4M12 18v4M2 12h4M18 12h4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
  trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  x: "M6 6l12 12M18 6L6 18",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  eyeoff: "M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6 0 10 7 10 7a17 17 0 0 1-3.2 3.9M6.6 6.6C3.8 8.4 2 12 2 12s4 7 10 7c1.6 0 3-.4 4.3-1",
  help: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14M12 17.5v.01",
  info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v6M12 7.5v.01",
};
const icon = (name) => svg("svg", { viewBox: "0 0 24 24", class: "sico", "aria-hidden": "true" }, svg("path", { d: ICONS[name] }));
const ibtn = (name, title, onclick, extra = {}) =>
  h("button", { type: "button", class: "ibtn", title, "aria-label": title, onclick, ...extra }, icon(name));
// An explanation, kept out of the way: hover (or focus) the ⓘ to read it.
const tip = (text, name = "info") => h("span", { class: "tip", title: text, tabindex: "0", role: "note", "aria-label": text }, icon(name));
const btn = (label, onclick, cls = "btn slim", extra = {}) => h("button", { type: "button", class: cls, onclick, ...extra }, label);
const swatch = (kind) => h("span", { class: "swatch", style: `background:${KIND_COLOR[kind] || MUTED}` });

/* ----------------------------------------------------- renaming in place */
function startRename(f, where = "sel") {
  if (!f || studio.compare || !editable(f)) return;
  studio.renaming = { key: keyOf(f), where, value: f.name || "", fresh: true };
  renderStudioPanel();
}
let panelRendering = false;      // true while renderStudioPanel swaps the DOM: blurs then are ours, not the user's
const isRenaming = (f, where) => !!studio.renaming && studio.renaming.key === keyOf(f) && studio.renaming.where === where;

function nameEditor(f) {
  const r = studio.renaming;
  const keepCaret = (e) => { if (studio.renaming === r) r.caret = [e.target.selectionStart, e.target.selectionEnd]; };
  const commit = (v) => { if (v && v !== (f.name || "")) draftEdit({ op: "rename", kind: f.kind, id: f.id, name: v }); };
  const input = h("input", { class: "field slim namefield", value: r.value, maxlength: 40, autocomplete: "off", spellcheck: "false",
    "data-key": "rename", "aria-label": `New name for ${featName(f)}`, title: "Enter saves, Esc cancels",
    oninput: (e) => { if (studio.renaming === r) { r.value = e.target.value; keepCaret(e); } },
    onkeyup: (e) => keepCaret(e), onmouseup: (e) => keepCaret(e), onselect: (e) => keepCaret(e),
    onkeydown: (e) => {
      if (e.key !== "Enter" && e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      if (studio.renaming !== r) return;
      studio.renaming = null;
      if (e.key === "Enter" && e.target.value.trim() !== (f.name || "")) commit(e.target.value.trim());
      else renderStudioPanel();
    },
    // Leaving the field keeps what was typed. Our own redraw also blurs it (the panel rebuilds the field): ignore that.
    // No full redraw here, or the control the user just pressed would be replaced before its click lands.
    onblur: (e) => {
      if (panelRendering || studio.renaming !== r) return;
      studio.renaming = null;
      const v = e.target.value.trim();
      e.target.replaceWith(h("span", { class: f === findRow(studio.sel) ? "selname" : "ellipsis" }, v || featName(f)));
      if (v !== (f.name || "")) setTimeout(() => commit(v), 150);
    },
    onclick: (e) => e.stopPropagation() });
  input.restore = () => {            // after a redraw: same text, same caret
    if (!input.isConnected || studio.renaming !== r) return;
    if (document.activeElement !== input) input.focus({ preventScroll: true });
    if (r.fresh) { input.select(); r.fresh = false; r.caret = [0, input.value.length]; }
    else if (r.caret) input.setSelectionRange(r.caret[0], r.caret[1]);
  };
  requestAnimationFrame(input.restore);
  return input;
}

/* ------------------------------------------------------------ shapes tab */
function emptyHint() {
  const keys = ["Click a shape to select it. Drag a white corner to move it, an orange dot to add a corner; right-click a corner to remove it.",
    "No-go and no-vision zones can be dragged whole.",
    "+ Area, + No-go, + No-vision, + Pathway and + Dead end on the map draw new shapes; Shift snaps to 15°, Alt places freely.",
    "Del deletes · Ctrl+Z undoes · Esc deselects · F2 renames.",
    "Edits go into a draft; nothing reaches the robot until you send it."].join("\n");
  return h("div", { class: "card" },
    h("details", { class: "hintfold" }, h("summary", { class: "muted small" }, "Click a shape on the map or in the list. ", icon("help")),
      h("ul", { class: "small keys" }, keys.split("\n").map((k) => h("li", null, k)))));
}

function tolChip(id, onSet) {
  const rule = `Tidy tolerance: no part of the outline moves further than ${studio.tol} cm.`;
  if (studio.tolOpen !== id) {
    return h("button", { type: "button", class: "chipbtn", title: rule, "aria-label": rule, "data-key": `chip:${id}`,
      onclick: () => { studio.tolOpen = id; renderStudioPanel(); } }, `${studio.tol} cm ▾`);
  }
  return h("span", { class: "tolpop row gap" }, h("span", { style: "flex:1" }, tolSlider(() => { studio.tolOpen = null; onSet(); })),
    ibtn("x", "Close", () => { studio.tolOpen = null; renderStudioPanel(); }, { "data-key": `chip:${id}` }));
}

function selectedCard() {
  const row = findRow(studio.sel);
  if (!row) return null;
  const max = row.kind === "areas" ? (studio.data.max_points_areas || studio.data.max_points) : studio.data.max_points;
  const canEdit = editable(studio.sel) && !studio.compare;
  const renaming = isRenaming(row, "sel");
  const stat = (text, title, color) => h("span", { class: "stat2", title, style: color ? `color:${color}` : null }, text);
  const cfd = row.low_cfd && Object.keys(row.low_cfd).length ? row.low_cfd : null;
  return h("div", { class: "card selcard" },
    h("div", { class: "selhead" },
      swatch(row.kind),
      renaming ? nameEditor(row) : h("span", { class: "selname", title: featName(row) }, featName(row)),
      !renaming && canEdit ? ibtn("pencil", "Rename (F2)", () => startRename(row), { "data-key": "pen:sel" }) : null,
      h("span", { class: "selactions" },
        ibtn("target", "Zoom to it", () => { const f = findGeo(studio.sel); if (f) studioFit(coordsOf(f)); }, { "data-key": "zoom" }),
        row.enable != null && canEdit
          ? ibtn(row.enable ? "eye" : "eyeoff", row.enable ? "On: click to switch it off" : "Off: click to switch it on",
                 () => draftEdit({ op: "enable", kind: row.kind, id: row.id, enable: !row.enable }), { "aria-pressed": String(!!row.enable), "data-key": "eye" })
          : null,
        canEdit ? ibtn("trash", "Delete (Del)", deleteSelected) : null,
        ibtn("x", "Deselect (Esc)", () => selectFeature(null)))),
    h("p", { class: "muted tiny selsub" }, `${row.label} · id ${row.id}`, row.enable === false ? colored(AMBER, " · off") : null),
    h("div", { class: "stats2" },
      stat(`${row.points} / ${max} pts`, row.points > max ? `Over the ${max} points the robot accepts` : `Points; the robot accepts up to ${max}`, row.points > max ? CORAL : null),
      row.area_m2 != null ? stat(row.robot_area_m2 != null && Math.abs(row.robot_area_m2 - row.area_m2) >= 1 ? `${fmtM2(row.area_m2)} (robot ${fmtM2(row.robot_area_m2)})` : fmtM2(row.area_m2),
                               "Area; the robot measures a little inside the drawn edge") : null,
      stat(fmtM(row.length_m), row.area_m2 != null ? "Perimeter" : "Length"),
      row.slope_deg != null ? stat(`${row.slope_deg}°`, "Slope") : null,
      cfd ? stat("low-cfd", `Low-confidence limits: area ${cfd.area_max_low_cfd_dis ?? "—"} · path ${cfd.path_max_low_cfd_dis ?? "—"}`) : null),
    row.start_id !== undefined ? h("p", { class: "small", style: "margin:8px 0 0" }, h("span", { class: "muted" }, "Joins "), pathJoins(row)) : null,
    canEdit && !studio.pts.size ? h("p", { class: "muted small", style: "margin:8px 0 0" },
      h("b", null, "Tip: "), "hold Shift and drag a box over the corners to pick many at once (Ctrl+Shift adds more); then drag one of them, or use the arrow keys, to move them all.") : null,
    canEdit && row.points > max ? h("div", { style: "margin-top:8px" },
      btn(`Simplify to ${max} points`, () => draftEdit({ op: "simplify", kind: row.kind, id: row.id, target: max }), "btn slim pri")) : null,
    row.kind === "areas" && onRobot("areas", row.id) ? h("p", { class: "muted small", style: "margin:8px 0 0" }, "Reshaping and renaming are sent in place ",
      tip("The area keeps its id, so plans, pathway links and its mow settings stay attached. The map and area records are kept here first (History), without using the robot's backup slots; afterwards the panel reads the map back and sends the old area again if the shape, the pathway links or anything else does not match. Big size changes ask first. Adding or deleting areas is done in the app.")) : null,
    row.kind === "novisionzones" && onRobot("novisionzones", row.id) ? h("p", { class: "muted small", style: "margin:8px 0 0" },
      "On/off, reshaping and renaming are sent in place ",
      tip("The zone keeps its id. Afterwards the panel reads the map back and sends the old zone again if anything does not match. New zones (+ No-vision) and deletes are sent too; a new zone asks first, since the robot ignores its cameras there. Obstacle bypass is changed in the app for now."),
      row.bypass ? h("span", null, h("br"), h("span", { title: "As for areas' obstacle avoidance mode; changed in the app for now" }, `Obstacle bypass: ${row.bypass}`)) : null) : null,
    row.kind === "novisionzones" && !onRobot("novisionzones", row.id) ? h("p", { class: "small", style: `margin:8px 0 0;color:${AMBER}` },
      "A new no-vision zone stays in the draft: adding one has not been captured yet.") : null,
    DRAFT_ONLY.has(row.kind) ? h("p", { class: "small", style: `margin:8px 0 0;color:${AMBER}` },
      "Changes to these stay in the draft: the panel cannot send them yet. ",
      h("a", { href: "#", onclick: (e) => { e.preventDefault(); setStudioTab("more"); } }, "Record an app edit")) : null,
    canEdit ? tidySection(row) : null,
    (row.kind === "pathways" || row.kind === "deadends") && onRobot(row.kind, row.id)
      || (row.kind === "areas" && onRobot("areas", row.id)) ? mowSettings(row) : null,
    studio.compare ? h("p", { class: "muted tiny", style: "margin:8px 0 0" }, "Stop comparing to edit.") : null);
}

/* ------------------------------------------------------- pathway mow settings */
// Read straight from the robot when a pathway is selected; a change goes to the robot on Save (not into the draft),
// is read back and checked, and the previous settings are put back if the robot kept something else.
const onRobot = (kind, id) => (studio.current?.features || []).some((f) => f.properties.kind === kind && f.properties.id === id);

/* Mow settings: a pathway's (save_mower_pathway_params) or an area's (save_mower_area_params). The server says which
   fields exist and which may change; keys can be nested ("first_clean_params.plan_speed"). A field with `show` is
   stored in metres and shown in inches, as the app does. */
const SETTINGS_URL = { pathways: (id) => `/api/pathways/${id}/settings`, areas: (id) => `/api/areas/${id}/settings`,
                       deadends: (id) => `/api/deadends/${id}/settings` };
const getPath = (o, key) => key.split(".").reduce((x, k) => (x && typeof x === "object" ? x[k] : undefined), o);

async function loadSettings(kind, id, keepEdits = null) {
  const pw = { kind, id, loading: true, data: null, error: null, edits: keepEdits || {}, saving: false };
  studio.pw = pw;
  try {
    pw.data = await sendJSON("GET", SETTINGS_URL[kind](id));
  } catch (e) {
    pw.error = e.status === 404 ? "not on the robot yet" : e.message;
  }
  pw.loading = false;
  if (studio.pw === pw) renderStudioPanel();
}

async function saveSettings(kind, id, changes, label = "Saved") {
  const pw = studio.pw, key = `${kind}:${id}`;
  pw.saving = true;
  renderStudioPanel();
  let failed = false;
  try {
    const r = await sendJSON("PUT", SETTINGS_URL[kind](id), { changes });
    if (r.ok && !r.unchanged) {
      studio.pwUndo[key] = label === "Undone" ? null : Object.fromEntries(Object.keys(changes).map((k) => [k, getPath(r.before, k)]));
      const links = (r.problems || []).length ? ` But: ${r.problems.join("; ")}.` : "";
      toast(`${label}: the robot has the new settings (read back and checked).${links}`);
    } else if (r.ok) {
      studio.pwUndo[key] = null;
      toast("Nothing to change: the robot already has these settings.");
    } else {
      failed = !r.unverified;
      toast(`Not saved: ${r.error}.${r.rollback_ok === false ? " The previous settings could NOT be put back; check in the app." : ""}`);
    }
  } catch (e) {
    failed = true;
    toast(`Not saved: ${e.message}`);
  }
  pw.saving = false;
  if (studio.pw === pw) await loadSettings(kind, id, failed ? pw.edits : null);   // keep the edits after a failure
}

/* The route preview: the robot plans the mowing route for the area (its saved settings, the robot's or the draft's
   outline) and answers with the path and its time. Nothing on the robot changes. */
// Draw a route preview: `features` are LineStrings in driving order (one per area).
function showRoute(features, fit) {
  const lines = features.filter((f) => f.geometry.coordinates.length > 1);
  setSource("studio-route", { type: "FeatureCollection", features: lines });
  const pt = (c, end) => ({ type: "Feature", properties: { end }, geometry: { type: "Point", coordinates: c } });
  setSource("studio-route-ends", lines.length ? { type: "FeatureCollection", features: [
    pt(lines[0].geometry.coordinates[0], "start"), pt(lines.at(-1).geometry.coordinates.at(-1), "end")] } : EMPTY);
  if (fit && lines.length) studioFit(lines.flatMap((f) => f.geometry.coordinates));
}

async function previewRoute(aid, source = "robot") {
  studio.routeBusy = true;
  renderStudioPanel();
  try {
    const r = await sendJSON("POST", `/api/areas/${aid}/preview`, { source });
    studio.route = r;
    showRoute([r.route], false);
  } catch (e) { toast(`No preview: ${e.message}`); }
  studio.routeBusy = false;
  renderStudioPanel();
}

function hideRoute() {
  studio.route = null;
  if (studio.plans) studio.plans.route = null;
  showRoute([], false);
  renderStudioPanel();
}

async function previewPlan(p) {
  const pl = studio.plans;
  pl.routeBusy = p.id;
  renderStudioPanel();
  try {
    const r = await sendJSON("POST", `/api/plans/${p.id}/preview`);
    studio.route = null;
    pl.route = r;
    showRoute(r.route.features, true);
    if (r.missing.length) toast(`No route for: ${r.missing.map((m) => `${m.name || `area ${m.id}`} (${m.why})`).join(", ")}.`);
  } catch (e) { toast(`No preview: ${e.message}`); }
  pl.routeBusy = null;
  renderStudioPanel();
}

function planRouteLine(pl, p) {
  const r = pl.route && pl.route.id === p.id ? pl.route : null;
  if (!r) return null;
  return h("p", { class: "small", style: "margin:4px 0 0" },
    h("b", null, r.total_time_s ? `About ${dur(r.total_time_s)}` : "Route"), ` · ${num(r.length_m / 1000, 1)} km of mowing `,
    tip("The robot's route for each area in the plan's order, with each area's saved mow settings. Driving between the areas is not included."),
    h("span", { class: "muted tiny", style: "display:block" },
      r.areas.map((a) => `${a.name}: ${a.total_time_s ? dur(a.total_time_s) : "?"}`).join(" → "), " · sky blue is the start, amber the end"),
    btn("Hide", hideRoute, "btn slim", { style: "margin-top:4px" }));
}

function routeSection(row, edited) {
  const r = studio.route && studio.route.id === row.id ? studio.route : null;
  const draftChanged = !!studio.draft && studio.draft.diff.changed.some((c) => c.kind === "areas" && c.id === row.id);
  return h("div", { class: "routebox" },
    h("div", { class: "row gap wrap" },
      btn(studio.routeBusy ? "Planning…" : "Preview the route", () => previewRoute(row.id, "robot"), "btn slim",
          { disabled: studio.routeBusy || null, "data-key": "route:robot", title: "Ask the robot to plan this area's mowing route (about 10 s). It only computes; nothing changes." }),
      draftChanged ? btn("Preview the draft's outline", () => previewRoute(row.id, "draft"), "btn slim",
          { disabled: studio.routeBusy || null, "data-key": "route:draft", title: "The same, for the outline in your draft (before it is sent)" }) : null,
      r ? btn("Hide", hideRoute, "btn slim", { "data-key": "route:hide" }) : null),
    r ? h("p", { class: "small", style: "margin:6px 0 0" },
      h("b", null, r.total_time_s ? `About ${dur(r.total_time_s)}` : "Route"), ` · ${num(r.length_m / 1000, 1)} km of driving · `,
      h("span", { class: "muted" }, r.source === "draft" ? "the draft's outline, saved settings" : "as on the robot"),
      h("span", { class: "muted tiny", style: "display:block" }, "Sky blue is where it starts, amber where it ends.")) : null,
    edited ? h("p", { class: "muted tiny", style: "margin:4px 0 0" }, "The preview uses the settings saved on the robot: save first to see your changes in it.") : null);
}

// One setting row, drawn the way the app draws it (levels, choices, sliders, -/+). `fine` (Fine control) swaps in finer
// steps and plain number boxes; a choice the robot value of which is not matched yet cannot be picked.
function settingRow(f, ctx) {
  const { cur, pw, fine, locked, locks, setEdit, same } = ctx;
  const control = f.control || (f.type === "bool" ? "bool" : f.levels ? "levels" : f.editable ? "number" : "value");
  if (control === "pending") {
    return h("div", { class: "pwrow ro", title: f.help },
      h("span", { class: "small" }, f.label),
      h("span", { class: "muted small" }, f.options ? f.options.map((o) => o[1]).join(" · ") : "", " ", h("span", { class: "pendtag" }, "not matched yet")));
  }
  const v = f.key in pw.edits ? pw.edits[f.key] : getPath(cur, f.key);
  const show = f.show, unit = show ? show.unit : f.unit || "";
  const shown = (x) => (show && typeof x === "number" ? Number((x / show.per).toFixed(show.digits)) : x);
  // f.round: the robot's own grid (pathway offsets: 0.1 m); a slider's inch step lands ~1 mm off it, so snap back
  const snap = (x) => (f.round !== undefined && Math.abs(x - Number(x.toFixed(f.round))) <= 0.005 ? Number(x.toFixed(f.round)) : x);
  const stored = (n) => (show ? snap(Number((n * show.per).toFixed(4))) : n);
  const tidy = (x) => (typeof x === "number" && !Number.isInteger(x) ? Number(x.toFixed(3)) : x);
  const ro = !f.editable || !!locks[f.key];
  const off = locked || ro || null;
  const optLabel = (x) => (f.options || []).find((o) => o[0] !== null && same(o[0], x))?.[1];
  const known = (x) => (f.known && f.known[String(x)] ? ` (${f.known[String(x)]})` : "");
  const text = (x) => (f.type === "bool" ? (x ? "on" : "off")
    : optLabel(x) || `${tidy(shown(x)) ?? "—"}${unit ? ` ${unit}` : ""}${known(x)}${f.levels?.find((l) => same(l[0], x)) ? ` · ${f.levels.find((l) => same(l[0], x))[1]}` : ""}`);
  const step = show ? (fine ? show.fine ?? show.adv_step ?? show.step : show.step) : (fine ? f.fine ?? f.step : f.step);
  const numberBox = () => {
    const lo = shown(f.min), hi = shown(f.max);
    return h("span", { class: "row gap", style: "align-items:center" },
      h("input", { class: "field pwnum", type: "number", min: lo, max: hi, step, value: tidy(shown(v)), "data-key": `pw:${f.key}`,
                   "aria-label": f.label, title: f.help, disabled: off,
                   onchange: (e) => {
                     const raw = e.target.value.trim(), n = Number(raw);
                     if (raw === "" || !Number.isFinite(n) || n < lo || n > hi || (f.integer && !Number.isInteger(n))) {
                       if (raw !== "") toast(`${f.label}: ${lo} to ${hi} ${unit}${f.integer ? ", whole numbers" : ""}`.trim());
                       renderStudioPanel();
                       return;
                     }
                     setEdit(f.key, stored(n));
                   } }),
      h("span", { class: "muted small" }, unit));
  };
  let ctl, stack = false;
  if (control === "value" || (ro && ["number", "slider"].includes(control))) {
    ctl = h("span", { class: "muted small mono", title: locks[f.key] ? `Read-only: ${locks[f.key]}` : f.help }, text(v));
  } else if (control === "bool") {
    ctl = h("label", { class: "switch", title: f.help },
      h("input", { type: "checkbox", role: "switch", "aria-label": f.label, "data-key": `pw:${f.key}`, checked: v || null,
                   disabled: off, onchange: (e) => setEdit(f.key, e.target.checked) }),
      h("span", { class: "track", "aria-hidden": "true" }));
  } else if ((control === "levels" || (control === "choice" && f.min !== undefined)) && fine && !ro) {
    ctl = numberBox();
  } else if (control === "levels" || control === "choice") {
    const opts = control === "levels" ? f.levels : f.options;
    const on = opts.find((o) => o[0] !== null && same(o[0], v));
    stack = true;
    ctl = h("span", { class: "levels" },
      h("span", { class: "seg slimseg", role: "group", "aria-label": f.label }, opts.map(([x, name]) => h("button", {
        type: "button", class: on && x !== null && same(on[0], x) ? "on" : "", "aria-pressed": String(!!on && x !== null && same(on[0], x)),
        title: x === null ? `${name}: not matched to the robot yet` : control === "levels" ? `${name}: ${x} ${unit}` : name,
        "data-key": `pw:${f.key}:${x}`, disabled: off || x === null || null, onclick: () => setEdit(f.key, x) }, name))),
      on ? (control === "levels" ? h("span", { class: "muted tiny" }, `${v} ${unit}`) : null)
         : h("span", { class: "muted tiny" }, `stored ${tidy(shown(v))}${unit ? ` ${unit}` : ""}${known(v)}${control === "levels" ? " (set with Fine control)" : ""}`));
  } else if (control === "slider") {
    const lo = show ? f.min / show.per : f.min, hi = show ? f.max / show.per : f.max;   // unrounded: the steps start on the grid
    const label = (x) => `${Number(x).toFixed(show ? show.digits : 0)}${unit ? ` ${unit}` : ""}`;
    const out = h("span", { class: "sliderval mono small" }, label(shown(v)));
    ctl = h("span", { class: "sliderctl" },
      h("input", { type: "range", min: lo, max: hi, step, value: shown(v), "aria-label": f.label, title: f.help,
                   "data-key": `pw:${f.key}`, disabled: off,
                   oninput: (e) => { out.textContent = label(e.target.value); },
                   onchange: (e) => setEdit(f.key, stored(Number(e.target.value))) }),
      out);
  } else if (control === "stepper") {
    const move = (d) => {
      const n = Math.min(f.max, Math.max(f.min, Number(((typeof v === "number" ? v : 0) + d).toFixed(4))));
      setEdit(f.key, f.integer ? Math.round(n) : n);
    };
    ctl = ro ? h("span", { class: "row gap", style: "align-items:center" },
                h("button", { type: "button", class: "ibtn stepbtn", disabled: true, title: f.help, "aria-label": `Lower ${f.label}` }, "−"),
                h("span", { class: "mono small", title: f.help }, text(v)),
                h("button", { type: "button", class: "ibtn stepbtn", disabled: true, title: f.help, "aria-label": `Raise ${f.label}` }, "+"))
      : fine ? numberBox()
      : h("span", { class: "row gap", style: "align-items:center" },
          h("button", { type: "button", class: "ibtn stepbtn", disabled: off || v <= f.min || null, "aria-label": `Lower ${f.label}`,
                        "data-key": `pw:${f.key}:-`, onclick: () => move(-f.step) }, "−"),
          h("span", { class: "mono small stepval" }, text(v)),
          h("button", { type: "button", class: "ibtn stepbtn", disabled: off || v >= f.max || null, "aria-label": `Raise ${f.label}`,
                        "data-key": `pw:${f.key}:+`, onclick: () => move(f.step) }, "+"));
  } else {
    ctl = numberBox();
  }
  return h("div", { class: `pwrow${ro ? " ro" : ""}${f.key in pw.edits ? " changed" : ""}${stack ? " stack" : ""}`,
                    title: ro ? `${f.help} ${locks[f.key] ? `Read-only: ${locks[f.key]}.` : "Read-only for now."}` : f.help },
    h("span", { class: "small" }, f.label), ctl);
}

function mowSettings(row) {
  const kind = row.kind;
  if (!onRobot(kind, row.id)) {
    return h("div", { class: "tidybox" }, h("p", { class: "label", style: "margin:0" }, "Mow settings"),
      h("p", { class: "muted small", style: "margin:0" }, "Send the draft first: a new pathway gets its settings once it is on the robot."));
  }
  const pw = studio.pw, fine = studio.advanced;
  if (!pw || pw.id !== row.id || pw.kind !== kind) { loadSettings(kind, row.id); return h("div", { class: "tidybox" }, h("p", { class: "muted small" }, "Reading the settings…")); }
  const head = h("div", { class: "row gap", style: "justify-content:space-between;align-items:center" },
    h("p", { class: "label", style: "margin:0" }, "Mow settings ", tip("Read from the robot just now, laid out as in the Yarbo app. Saving writes to the robot straight away (not to the draft) and reads it back to check; a mismatch puts the old settings back. Fine control adds finer steps and the robot's other values.")),
    h("span", { class: "row gap", style: "align-items:center" },
      h("label", { class: "switch small", title: "Finer steps than the app, and the robot's values the app does not show" },
        h("input", { type: "checkbox", role: "switch", "aria-label": "Fine control", "data-key": "pw:advanced", checked: fine || null,
                     onchange: (e) => { studio.advanced = e.target.checked; savePref("advanced", studio.advanced); renderStudioPanel(); } }),
        h("span", { class: "track", "aria-hidden": "true" }), h("span", { class: "muted small" }, "Fine control")),
      btn("Reload", () => loadSettings(kind, row.id), "btn slim", { disabled: pw.loading || pw.saving || null, title: "Read the settings from the robot again" })));
  if (pw.loading) return h("div", { class: "tidybox" }, head, h("p", { class: "muted small", style: "margin:0" }, "Reading the settings…"));
  if (pw.error) return h("div", { class: "tidybox" }, head, h("p", { class: "small", style: `margin:0;color:${AMBER}` }, `Could not read them: ${pw.error}`));
  const { settings: cur, blocked, locks = {} } = pw.data;
  const same = (a, b) => (typeof a === "number" && typeof b === "number" ? Math.abs(a - b) < 1e-4 : a === b);
  const ctx = { cur, pw, fine, locks, same, locked: !!blocked || pw.saving,
    setEdit: (k, v) => { if (same(v, getPath(cur, k))) delete pw.edits[k]; else pw.edits[k] = v; renderStudioPanel(); } };
  const fields = pw.data.fields.filter((f) => f.simple !== false || fine);       // pathways: their raw extras with Fine control
  const sections = [];
  for (const f of fields) {
    const name = f.section || "";
    if (name === "Not in the app" && !fine) continue;
    if (!sections.length || sections[sections.length - 1].name !== name) sections.push({ name, fields: [] });
    sections[sections.length - 1].fields.push(f);
  }
  const body = sections.map((sec) => {
    const rows = sec.fields.map((f) => settingRow(f, ctx));
    if (sec.name === "Area settings" && pw.data.labels) {
      rows.push(h("div", { class: "pwrow ro", title: "The area's labels in the app (the area's own record)" },
        h("span", { class: "small" }, "Labels"), h("span", { class: "muted small" }, pw.data.labels.join(", ") || "none")));
    }
    if (sec.name === "Advanced settings") {
      return h("details", { class: "pwsection", open: studio.appAdvOpen || null,
                            ontoggle: (e) => { studio.appAdvOpen = e.target.open; savePref("appAdvOpen", e.target.open); } },
        h("summary", { class: "pwgroup" }, "Advanced settings"), rows);
    }
    return [sec.name ? h("p", { class: "muted tiny pwgroup" }, sec.name) : null, rows];
  });
  const n = Object.keys(pw.edits).length, undo = studio.pwUndo[`${kind}:${row.id}`];
  return h("div", { class: "tidybox" }, head,
    pw.data.defaults ? h("p", { class: "muted small", style: "margin:0" }, `On the robot's defaults: these were never set for this ${pw.kind === "deadends" ? "dead end" : "area"}.`) : null,
    body,
    blocked ? h("p", { class: "small", style: `margin:0;color:${AMBER}` }, `Read-only now: ${blocked}.`) : null,
    h("div", { class: "row gap wrap" },
      btn(pw.saving ? "Saving…" : n ? `Save ${n} change${n > 1 ? "s" : ""} to the robot` : "Save to the robot",
          () => saveSettings(kind, row.id, { ...pw.edits }), "btn slim pri",
          { disabled: !n || ctx.locked || null, title: blocked || "Writes to the robot now, then reads it back to check", "data-key": "pw:save" }),
      n ? btn("Reset", () => { pw.edits = {}; renderStudioPanel(); }, "btn slim", { disabled: pw.saving || null, "data-key": "pw:reset" }) : null,
      undo && !n ? btn("Undo last save", () => saveSettings(kind, row.id, undo, "Undone"), "btn slim",
                       { disabled: ctx.locked || null, title: "Put back the values from before your last save", "data-key": "pw:undo" }) : null),
    kind === "areas" ? routeSection(row, n > 0) : null);
}

function tidySection(row) {
  if (!TIDY_KINDS.has(row.kind)) return null;
  const t = studio.tidy && studio.tidy.key === keyOf(row) ? studio.tidy : null;
  const tools = TIDY_TOOLS.filter(([id]) => !LINE_KINDS.has(row.kind) || (id !== "round" && id !== "square"));
  const res = t && t.result, rule = BIAS_NOTE[row.kind];
  let body = null;
  if (t && t.loading) body = h("p", { class: "muted small", style: "margin:0" }, "Working it out…");
  else if (res && res.ok) {
    body = h("div", { class: "tidyres row gap wrap" },
      h("span", { class: "small", style: "flex:1", title: "The dashed blue outline on the map is the result" },
        `${TOOL_LABEL[t.tool]}: ${res.before} → ${res.after} pts · moves ≤ ${Math.round(res.max_move * 100)} cm`),
      btn("Apply", () => draftEdit({ op: "tidy", kind: row.kind, id: row.id, tool: t.tool, tol: t.tol }), "btn slim pri",
          { disabled: studio.busy || null, title: "Put the result in the draft" }),
      btn("Cancel", () => { clearTidy(); renderStudioPanel(); }));
  } else if (res) {
    const need = res.needs_tol ? Math.ceil(res.needs_tol * 100) : null;
    body = h("div", { class: "tidyres row gap wrap" },
      h("span", { class: "small", style: `flex:1;color:${AMBER}`, title: need ? capital(res.why) : null },
        need ? `${TOOL_LABEL[t.tool]} needs ${need} cm` : `${TOOL_LABEL[t.tool]}: ${res.why}.`),
      need ? btn(`Use ${need} cm`, () => { setTol(need); previewTidy(row, t.tool); }) : null);
  }
  return h("div", { class: "tidybox" },
    h("div", { class: "row gap wrap" },
      h("span", { class: "label", style: "margin:0", title: rule }, "Tidy"),
      tools.map(([id, label, title]) => h("button", { type: "button", class: `btn slim${t && t.tool === id ? " on" : ""}`,
        title: `${title}. ${rule}`, disabled: studio.busy || null, onclick: () => previewTidy(row, id) }, label)),
      tolChip("sel", () => (t ? previewTidy(row, t.tool) : renderStudioPanel()))),
    body);
}

function issuesByShape() {
  const list = studio.draft ? studio.draft.issues : (studio.data.issues || []);
  const out = new Map();
  for (const i of list) {
    if (i.id == null) continue;
    const k = `${i.kind}:${i.id}`;
    if (!out.has(k)) out.set(k, []);
    out.get(k).push(i);
  }
  return out;
}

function shapesCard() {
  const feats = activeFeatures();
  if (!feats.length) return card("Shapes", h("p", { class: "muted small" }, "The map has not been read yet."));
  const groups = KIND_ORDER.map((k) => [k, feats.filter((f) => f.kind === k)]).filter(([, l]) => l.length);
  const sel = studio.sel && keyOf(studio.sel), locked = !!studio.compare;
  const rv = studio.review && studio.review.rows ? studio.review : null;
  const sugg = rv ? new Map(rv.rows.map((x) => [keyOf(x), x])) : null;
  const issues = issuesByShape();

  const rowEl = (f) => {
    const k = keyOf(f), renaming = isRenaming(f, "list"), iss = issues.get(k) || [], s = sugg && sugg.get(k);
    const issueText = iss.map((i) => `${i.msg}${!studio.draft ? " (already on the robot)" : ""}`).join("\n");
    const dot = iss.length ? h("span", { class: "idot", tabindex: "0", role: "img", title: issueText, "aria-label": issueText,
      style: `background:${iss.some((i) => i.level === "error") ? CORAL : AMBER}` }) : null;
    let right;
    if (s && s.ok) right = h("span", { class: "muted tiny", title: `Moves at most ${Math.round(s.max_move * 100)} cm` }, `${TOOL_LABEL[s.tool]} · ${s.before} → ${s.after}`);
    else if (s) right = btn(`${TOOL_LABEL[s.tool]}: use ${Math.ceil(s.needs_tol * 100)} cm`, (e) => { e.stopPropagation(); setTol(Math.ceil(s.needs_tol * 100)); findCleanups(); },
                            "btn slim", { title: `${TOOL_LABEL[s.tool]} needs a tolerance of ${Math.ceil(s.needs_tol * 100)} cm`, "data-key": `use:${k}` });
    else right = h("span", { class: "muted tiny" }, f.area_m2 != null ? fmtM2(f.area_m2) : fmtM(f.length_m));
    const pick = () => selectFeature({ kind: f.kind, id: f.id }, true);
    return h("div", { class: `shaperow${k === sel ? " on" : ""}`, onclick: (e) => { if (!renaming && e.target === e.currentTarget) pick(); },
        onmouseenter: () => hoverShape(f), onmouseleave: () => hoverShape(null), onfocusin: () => hoverShape(f), onfocusout: () => hoverShape(null),
        onkeydown: (e) => {
          if (e.key !== "F2" || e.target.closest("input")) return;
          e.preventDefault();
          e.stopPropagation();                   // the document's F2 would rename the selected shape instead
          startRename(f, "list");
        } },
      s && s.ok ? h("input", { type: "checkbox", checked: rv.picked.has(k) || null, "aria-label": `Tidy ${featName(f)}`, "data-key": `tick:${k}`,
        onchange: (e) => { if (e.target.checked) rv.picked.add(k); else rv.picked.delete(k); showReviewGhosts(); renderStudioPanel(); } }) : null,
      renaming ? nameEditor(f)
        : h("button", { type: "button", class: "shapebtn", "data-key": `row:${k}`, "aria-current": k === sel ? "true" : null, onclick: pick },
            f.start_id !== undefined
              ? h("span", { class: "shapetext" }, h("span", { class: "ellipsis" }, featName(f)), h("span", { class: "muted tiny ellipsis" }, pathJoins(f)))
              : h("span", { class: "ellipsis" }, featName(f)),
            f.enable === false ? h("span", { class: "badge", title: "Switched off" }, "off") : null),
      renaming ? null : dot,
      renaming ? null : right,
      !renaming && editable(f) && !locked ? ibtn("pencil", `Rename ${featName(f)} (F2)`, (e) => { e.stopPropagation(); startRename(f, "list"); },
                                                 { class: "ibtn rowedit", "data-key": `pen:${k}` }) : null);
  };

  let reviewBar = null;
  if (studio.review && studio.review.loading) reviewBar = h("p", { class: "muted small", style: "margin:0 0 6px" }, "Checking every shape…");
  else if (rv) {
    const okRows = rv.rows.filter((x) => x.ok), picked = okRows.filter((x) => rv.picked.has(keyOf(x)));
    const more = rv.rows.length - okRows.length;
    reviewBar = h("div", { class: "reviewbar" },
      h("span", { class: "small", style: "flex:1" }, rv.rows.length ? `${okRows.length} to tidy${more ? ` · ${more} need more tolerance` : ""}` : `Nothing to tidy at ${Math.round(rv.tol * 100)} cm`),
      okRows.length ? btn(`Apply ${picked.length}`, () => draftEdit({ op: "tidy_many", tol: rv.tol, items: picked.map((x) => ({ kind: x.kind, id: x.id, tool: x.tool })) }),
        "btn slim pri", { disabled: locked || studio.busy || !picked.length || null, title: "Put the ticked cleanups in the draft as one edit (one Undo)" }) : null,
      ibtn("x", "Close the suggestions", () => { studio.review = null; clearTidy(); renderStudioPanel(); }));
  }
  return h("div", { class: "card" },
    h("div", { class: "cardhead" },
      h("p", { class: "label", style: "margin:0" }, `Shapes · ${feats.length}`),
      h("span", { class: "row gap" },
        tolChip("list", () => (rv ? findCleanups() : renderStudioPanel())),
        rv || (studio.review && studio.review.loading) ? null
          : btn("Tidy all…", findCleanups, "btn slim", { disabled: locked || null,
              title: "Suggest one cleanup per shape (shown dashed on the map); tick the ones you want and apply them as one draft edit" }))),
    reviewBar,
    groups.map(([k, list]) => h("div", { class: "fgroup" },
      h("p", { class: "small fhead" }, h("span", { class: "row gap" }, swatch(k), `${KIND_NAME[k]} · ${list.length}`,
        DRAFT_ONLY.has(k) ? h("span", { class: "badge", title: "Changes to these stay in the draft: they cannot be sent to the robot yet" }, "draft only") : null)),
      list.map(rowEl))),
    !studio.draft && (studio.data.issues || []).length ? h("details", { class: "issuefold" },
      h("summary", { class: "muted small" }, `${studio.data.issues.length} issue${studio.data.issues.length === 1 ? "" : "s"} on the robot's map`),
      h("div", { class: "linklist" }, studio.data.issues.map(issueRow))) : null);
}

/* ------------------------------------------------------------ tidy state */
function showGhost(geoms) {
  setSource("studio-ghost", { type: "FeatureCollection", features: geoms.filter(Boolean).map((g) => ({ type: "Feature", properties: {}, geometry: g })) });
}
let previewSeq = 0;
function forgetPreviews() {
  previewSeq++;
  studio.review = null;
  studio.tidy = null;
  setSource("studio-ghost", EMPTY);
}
function clearTidy() {
  studio.tidy = null;
  if (studio.review && studio.review.rows) showReviewGhosts(); else setSource("studio-ghost", EMPTY);
}
function tolSlider(onSet) {
  const label = h("span", { class: "muted small", style: "min-width:44px;text-align:right" }, `${studio.tol} cm`);
  return h("div", { class: "row gap" },
    h("input", { type: "range", min: 2, max: 30, step: 1, value: studio.tol, "aria-label": "Tidy tolerance in centimetres",
                 style: "flex:1;accent-color:#79B8F0", title: "No part of the outline moves further than this",
                 oninput: (e) => { label.textContent = `${e.target.value} cm`; },
                 onchange: (e) => { setTol(Number(e.target.value)); onSet(); } }),
    label);
}

async function previewTidy(row, tool) {
  const key = keyOf(row), tol = studio.tol / 100, seq = ++previewSeq;
  studio.tidy = { key, tool, tol, loading: true };
  renderStudioPanel();
  let r;
  try { r = await sendJSON("POST", "/api/studio/tidy/preview", { kind: row.kind, id: row.id, tol, tools: [tool] }); }
  catch (e) { if (seq === previewSeq) { toast(e.message); clearTidy(); renderStudioPanel(); } return; }
  if (seq !== previewSeq) return;                                  // superseded meanwhile
  const res = r.tools[tool];
  studio.tidy = { key, tool, tol, result: res };
  showGhost(res && res.ok ? [res.geometry] : []);
  renderStudioPanel();
}

function showReviewGhosts() {
  const rv = studio.review;
  showGhost(rv && rv.rows ? rv.rows.filter((x) => x.ok && rv.picked.has(keyOf(x))).map((x) => x.geometry) : []);
}

async function findCleanups() {
  const seq = ++previewSeq;
  studio.tidy = null;
  studio.review = { loading: true };
  renderStudioPanel();
  try {
    const r = await sendJSON("POST", "/api/studio/tidy/review", { tol: studio.tol / 100 });
    if (seq !== previewSeq) return;                                // the map changed meanwhile
    studio.review = { tol: r.tol, rows: r.rows, picked: new Set(r.rows.filter((x) => x.ok).map(keyOf)) };
  } catch (e) { if (seq !== previewSeq) return; toast(e.message); studio.review = null; }
  showReviewGhosts();
  renderStudioPanel();
}

/* ------------------------------------------------------------- draft tab */
function changeList(d, onPick) {
  const line = (r, color, text) => h("button", { type: "button", class: "linkrow", onclick: () => onPick(r) },
    h("span", { class: "row gap" }, swatch(r.kind), h("span", null, r.name || `${r.label.toLowerCase()} ${r.id}`)), colored(color, text));
  const rows = [
    ...d.added.map((r) => line(r, GREEN, "added")),
    ...d.removed.map((r) => line(r, CORAL, "removed")),
    ...d.changed.map((r) => line(r, AMBER, r.changes.join(" · "))),
  ];
  return rows.length ? h("div", { class: "linklist" }, rows) : h("p", { class: "muted small" }, "No differences.");
}

// Select a changed shape; a removed one is not in the draft, so show where it was.
function focusChange(r, overlay) {
  if (findRow({ kind: r.kind, id: r.id })) { selectFeature({ kind: r.kind, id: r.id }, true); return; }
  const f = (overlay.features || []).find((x) => x.properties.kind === r.kind && x.properties.id === r.id);
  if (f) studioFit(coordsOf(f));
}

const issueRow = (i) => h("button", { type: "button", class: "linkrow", onclick: () => i.id != null && selectFeature({ kind: i.kind, id: i.id }, true) },
  h("span", null, i.name || `${i.kind} ${i.id ?? ""}`), colored(i.level === "error" ? CORAL : i.level === "warning" ? AMBER : MUTED, i.msg));

// The bar's "Draft · N changes": bring the draft card (top of Yard) into view.
function showDraft() {
  setStudioTab("yard");
  $("#draftcard")?.scrollIntoView({ block: "start", behavior: "smooth" });
}

function draftSection() {
  const d = studio.draft;
  if (!d) return [lastWriteCard()];
  const m = d.meta, n = changeCount();
  const fresh = d.issues.filter((i) => i.level === "error" || i.level === "warning"), existing = d.issues.filter((i) => i.level === "existing");
  const exp = (fmt) => h("a", { class: "btn slim", href: `/api/studio/export?ref=draft&fmt=${fmt}` }, fmt.toUpperCase());
  return [lastWriteCard(), h("div", { class: "card attention", id: "draftcard" },
    h("div", { class: "cardhead" }, h("p", { class: "label", style: "margin:0" }, `Draft · ${n} change${n === 1 ? "" : "s"}`),
      h("span", { class: "row gap" }, btn("Undo", undoEdit, "btn slim", { disabled: !m.undo || null, title: "Undo the last edit (Ctrl+Z)" }), btn("Discard", discardDraft, "btn slim", { title: "Throw the draft away" }))),
    h("p", { class: "muted tiny", style: "margin:0 0 6px" }, `From ${m.from_label} · last edit ${when(m.updated)}`),
    m.stale ? h("p", { class: "small", style: `color:${AMBER}` }, "The robot's map has changed since this draft was started.") : null,
    changeList(d.diff, (r) => focusChange(r, d.overlay)),
    sendSection(d, fresh),
    existing.length ? h("details", null, h("summary", { class: "muted small" }, `${existing.length} issue${existing.length === 1 ? " was" : "s were"} already on the robot`),
      h("div", { class: "linklist" }, existing.map(issueRow))) : null,
    h("details", null, h("summary", { class: "muted small" }, "Edit log and export"),
      h("ol", { class: "small log" }, m.log.map((l) => h("li", null, l))),
      h("div", { class: "row gap wrap", style: "margin-top:6px" }, exp("geojson"), exp("kml"), exp("json"))))];
}

function sendSection(d, fresh) {
  const send = d.send, ready = d.checks.every((c) => c.ok);
  return h("div", null,
    h("p", { class: "label", style: "margin:12px 0 6px" }, "Send to the robot ", tip(studio.data.write.reason)),
    h("p", { class: "whyline muted tiny" }, studio.data.write.reason),
    send.blocked.length ? h("div", { class: "blockedlist" },
      h("p", { class: "small", style: `color:${AMBER};margin:0 0 4px` }, "Can't be sent yet. ", h("span", { class: "muted" }, "These stay in the draft; everything else can still be sent.")),
      send.blocked.map((b) => h("div", { class: "vrow" },
        h("span", null, h("span", { class: "row gap" }, h("b", null, b.name || `${b.label} ${b.id}`), tip(capital(b.why))),
          h("span", { class: "whyline muted tiny" }, capital(b.why))),
        btn("Revert", () => draftEdit({ op: "revert", kind: b.kind, id: b.id }), "btn slim", { title: "Put it back as it is on the robot" })))) : null,
    send.steps.length ? h("ol", { class: "small log", title: "Sent in this order" }, send.steps.map((st) => h("li", { title: st.cmd }, st.label))) : null,
    checkList(d.checks),
    fresh.length ? h("div", { class: "linklist" }, fresh.map(issueRow)) : null,
    h("div", { class: "row gap wrap", style: "margin-top:8px" },
      btn("Send to robot", sendDraft, "btn pri", { disabled: !ready || studio.busy || null, title: "Asks to confirm first; then reads the map back and undoes anything that does not match" }),
      send.enabled ? btn("Map writing off", () => setMapWriting(false), "btn slim", { title: "Stop the panel from changing the robot's map" })
                   : btn("Map writing on…", () => setMapWriting(true), "btn slim", { title: "Let the panel change the robot's map (asks you to type ENABLE)" })));
}

async function setMapWriting(on) {
  if (on && prompt("Type ENABLE to let the panel change the robot's map (no-go zones, pathways, dead ends, area and no-vision zone edits, pathway and dead-end settings, plans).") !== "ENABLE") return;
  try { await putJSON("/api/settings/map_write_enabled", on); toast(on ? "Map writing is on." : "Map writing is off."); }
  catch (e) { toast(e.message); }
  await refreshStudio();
}

async function sendDraft() {
  const d = studio.draft;
  const lines = d.send.steps.map((st, i) => `${i + 1}. ${st.label}`).join("\n");
  const areas = d.send.steps.some((st) => st.cmd === "save_clean_area");
  if (!confirm(`Send ${d.send.steps.length} command${d.send.steps.length === 1 ? "" : "s"} to the robot?\n\n${lines}\n\n` +
               (areas ? "The map and area records are kept here first (Map Studio → History). " : "") +
               "Afterwards the map is read back and compared with the draft; anything that does not match is undone.")) return;
  const big = d.send.steps.filter((st) => st.big);
  if (big.length && !confirm(`Please confirm these changes (an area's size changing by over a quarter, a no-vision zone added, switched on, grown or moved, or shapes put back from an older version):\n\n${big.map((st) => st.label).join("\n")}\n\nSend them?`)) return;
  studio.busy = true;
  setMode("select");
  selectFeature(null);
  toast("Sending to the robot…");
  try {
    const r = await sendJSON("POST", "/api/studio/draft/apply", { confirm: true, base: draftBase(), allow_big: big.length > 0 });
    toast(`Written and verified: ${r.steps.length} command${r.steps.length === 1 ? "" : "s"}.` +
          (r.kept_in_draft?.length ? ` Kept in the draft (can't be sent yet): ${r.kept_in_draft.join(", ")}.` : ""));
  } catch (e) {
    const r = e.data || {};
    if (e.status === 502) toast(`Not written: ${r.error || (r.problems || []).join("; ")}. ${r.rollback_ok ? "Everything was undone." : "Undo did not fully succeed: check the map."}`);
    else await onConflict(e);
  }
  studio.busy = false;
  await refreshStudio();
}

function lastWriteCard() {
  const w = studio.data?.write?.last;
  if (!w || Date.now() / 1000 - w.at > 6 * 3600) return null;
  const good = w.ok;
  return h("div", { class: `card${good ? "" : " attention"}` },
    h("p", { class: "label" }, good ? "Last write" : "Last write failed"),
    h("p", { class: "small", style: `color:${good ? GREEN : CORAL}` },
      good ? `Written and verified ${when(w.at)}: ${w.steps.length} command${w.steps.length === 1 ? "" : "s"}${w.cloud_backup ? "; cloud backup queued" : ""}.`
           : `${when(w.at)}: ${w.error || (w.problems || []).join("; ")}`),
    w.steps.length ? h("ol", { class: "small log" }, w.steps.map((st) => h("li", null, st.label, st.new_id != null ? h("span", { class: "muted tiny" }, ` · new id ${st.new_id}`) : null))) : null,
    !good ? h("p", { class: "small", style: `color:${w.rollback_ok ? GREEN : CORAL}` },
      w.rollback_ok ? "Everything that was sent has been undone; the robot's map is as it was." : "The undo did not fully succeed. Check the map, and restore from History if needed.") : null,
    !good && w.rollback?.length ? h("details", null, h("summary", { class: "muted small" }, "Undo steps"), h("ol", { class: "small log" }, w.rollback.map((n) => h("li", null, n)))) : null);
}

function compareCard() {
  const c = studio.compare;
  if (!c) return null;
  const d = c.diff;
  return h("div", { class: "card attention" },
    h("div", { class: "cardhead" }, h("p", { class: "label", style: "margin:0" }, "Comparing ", tip("Dashed red = before · orange = after · green = new")),
      ibtn("x", "Stop comparing", () => { studio.compare = null; syncStudioMap(); selectFeature(studio.sel); })),
    h("p", { class: "small", style: "margin:0 0 4px" }, `${c.a_label} → ${c.b_label}`),
    h("p", { class: "muted tiny", style: "margin:0 0 6px" }, `${d.added.length} added · ${d.removed.length} removed · ${d.changed.length} changed · ${d.unchanged} unchanged`),
    changeList(d, (r) => { const f = (c.overlay.features || []).find((x) => x.properties.kind === r.kind && x.properties.id === r.id); if (f) studioFit(coordsOf(f)); }),
    h("div", { class: "row gap", style: "margin-top:8px" }, btn("Restore as a draft", () => startDraft(c.a), "btn slim pri", { title: "Start a draft from this version" })));
}

/* ---------------------------------------------------- history and more */
// The newest few of a long list, with a toggle for the rest.
function shortList(id, rows, n = 5) {
  if (rows.length <= n) return rows;
  const all = !!studio.showAll[id];
  return [...(all ? rows : rows.slice(0, n)),
    h("button", { type: "button", class: "linkbtn morebtn", "data-key": `more:${id}`, onclick: () => { studio.showAll[id] = !all; renderStudioPanel(); } },
      all ? "Show fewer" : `Show all ${rows.length}`)];
}

function historyTab() {
  const s = studio.data;
  const dated = (ts) => (ts ? new Date(ts * (ts > 1e11 ? 1 : 1000)).toLocaleString([], { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");
  const ours = s.versions.map((v, i) => h("div", { class: "vrow" },
    h("span", null, i === 0 ? h("b", null, "Current") : dated(v.fetched), h("span", { class: "muted tiny" }, ` · ${v.sha.slice(0, 7)}`)),
    i === 0 ? h("span", { class: "muted tiny" }, `read ${dated(v.fetched)}`)
      : h("span", { class: "row gap" }, btn("Compare", () => compareWith(v.ref), "btn slim", { title: "Show what changed since this version" }),
          btn("Draft", () => startDraft(v.ref), "btn slim", { title: "Start a draft from this version (nothing reaches the robot until you send it)" }))));
  const manual = s.backups.filter((b) => !b.auto).length, slots = s.backup_slots || 10, lr = s.last_restore;
  const backups = s.backups.map((b) => h("div", { class: "brow" },
    h("div", { class: "bmeta", title: `Backup id ${b.id}` },
      h("span", { class: "bname" }, b.name || `#${b.id}`),
      h("span", { class: "muted tiny" }, `${dated(b.timestamp)}${b.auto ? " · automatic" : ""}`)),
    h("div", { class: "bact" },
      b.fetched
        ? [btn("Compare", () => compareWith(`backup:${b.id}`)), btn("Draft", () => startDraft(`backup:${b.id}`), "btn slim", { title: "Start a draft from this backup (nothing reaches the robot until you send it)" })]
        : btn("Fetch", (e) => fetchBackup(b.id, e.target), "btn slim", { title: "Read this backup from the robot (a read; it changes nothing)" }),
      btn("Restore", () => restoreBackup(b), "btn slim", { title: "Replace the robot's map with this backup (shows what changes first; plans have to be made again)", disabled: studio.busy || null }),
      ibtn("pencil", "Rename this backup on the robot", () => renameBackup(b), { disabled: studio.busy || null }),
      ibtn("trash", "Delete this backup from the robot", () => deleteBackup(b), { disabled: studio.busy || null }))));
  return [
    h("div", { class: "card" },
      h("p", { class: "label" }, "This panel's versions ", tip("A new version is recorded each time the robot's map changes, and before every change the panel sends; there is no limit. Draft starts a draft from one: sending it puts those shapes back on the robot (areas in place, after asking).")),
      ours.length ? shortList("versions", ours) : h("p", { class: "muted small" }, "None yet.")),
    h("div", { class: "card" },
      h("div", { class: "cardhead" }, h("p", { class: "label", style: "margin:0" }, "Robot backups ", tip("The robot's own map backups. Fetching one is a read; Save, Rename, Delete and Restore change the robot and need map writing on.")),
        h("span", { class: "row gap" }, btn("Save now", (e) => saveBackup(e.target), "btn slim", { title: "Make a backup of the robot's map as it is now (the app's Save Current Map)", disabled: manual >= slots || studio.busy || null }),
          btn("Refresh", (e) => refreshBackups(e.target), "btn slim", { title: "Ask the robot for its backup list" }),
          btn("Read map now", async (e) => { e.target.disabled = true; await draftAction("POST", "/api/studio/reload", undefined, "Map read from the robot."); await refreshStudio(); },
              "btn slim", { title: "Read the robot's current map again" }))),
      s.backups.length ? h("p", { class: "small", style: `margin:0 0 6px;color:${manual >= slots ? AMBER : "inherit"}` },
        `${manual} of ${slots} backup slots used`, manual >= slots ? " · full: delete one to save another" : "") : null,
      lr ? h("p", { class: "small", style: `margin:0 0 6px;color:${lr.ok && lr.map_matches_backup ? "inherit" : AMBER}` },
        h("b", null, "Last restore: "), `${backupDate(lr.at)} · `, restoreSummary(lr)) : null,
      backups.length ? shortList("backups", backups) : h("p", { class: "muted small" }, "No backup list yet."))];
}

function toolsCard() {
  const ov = studio.overlay;
  const hasOv = ov && ov.features && ov.features.length;
  const exp = (fmt) => h("a", { class: "btn slim", href: `/api/studio/export?ref=current&fmt=${fmt}` }, fmt.toUpperCase());
  const file = h("input", { type: "file", accept: ".geojson,.json,.kml", hidden: true, onchange: (e) => e.target.files[0] && importOverlay(e.target.files[0]) });
  return card("Import and export",
    h("div", { class: "row gap wrap" }, h("span", { class: "muted small" }, "The robot's map:"), exp("geojson"), exp("kml"), exp("json")),
    h("div", { class: "row gap wrap", style: "margin-top:8px" },
      file, btn(hasOv ? "Replace reference" : "Import reference…", () => file.click(), "btn slim",
        { title: "Show a GeoJSON or KML file (a survey, an old export) under the map for tracing. Display only; it never reaches the robot." }),
      hasOv ? btn(studio.overlayOn ? "Hide" : "Show", () => { studio.overlayOn = !studio.overlayOn; setStudioLayers(true); renderStudioPanel(); }) : null,
      hasOv ? btn("Remove", async () => { await draftAction("DELETE", "/api/studio/overlay"); studio.overlay = EMPTY; syncStudioMap(); renderStudioPanel(); }) : null),
    hasOv ? h("p", { class: "muted tiny", style: "margin:6px 0 0" }, `${ov.name || "imported"} · ${ov.features.length} shapes, white dotted`) : null);
}

/* Plans: make one or change its name and areas (save_plan, captured 2026-10-10). Deleting stays in the app. */
async function loadPlans(fresh = true) {
  const pl = studio.plans = { ...(studio.plans || {}), loading: true, error: null };
  try { Object.assign(pl, await getJSON(`/api/plans${fresh ? "?fresh=true" : ""}`)); }
  catch (e) {
    pl.error = e.message;
    if (fresh) try { Object.assign(pl, await getJSON("/api/plans")); } catch { /* keep the error */ }
  }
  pl.loading = false;
  if (studio.plans === pl) renderStudioPanel();
}

function editPlan(p) {
  studio.plans.edit = p ? { id: p.id, name: p.name || "", areaIds: [...(p.areaIds || [])] } : { id: null, name: "", areaIds: [] };
  renderStudioPanel();
  $('[data-key="plan:name"]')?.focus();
}

async function savePlan() {
  const pl = studio.plans, e = pl.edit;
  pl.saving = true;
  renderStudioPanel();
  try {
    const body = { name: e.name.trim(), areaIds: e.areaIds };
    const r = await sendJSON(e.id == null ? "POST" : "PUT", e.id == null ? "/api/plans" : `/api/plans/${e.id}`, body);
    if (r.ok) {
      toast(r.unchanged ? "Nothing to change: the robot already has this plan." : `Saved “${r.plan.name}” (read back from the robot).`);
      pl.edit = null;
    } else {
      toast(`Not saved: ${r.error}.${r.rollback_ok === false ? " The previous plan could NOT be put back; check in the app." : ""}`);
    }
    if (r.plans) pl.plans = r.plans;
  } catch (err) {
    toast(`Not saved: ${err.message}`);
  }
  pl.saving = false;
  renderStudioPanel();
}

async function deletePlan() {
  const pl = studio.plans, e = pl.edit, p = (pl.plans || []).find((x) => x.id === e.id);
  if (!p || !confirm(`Delete “${p.name}” from the robot? This cannot be undone from the panel; you can make it again with New plan.`)) return;
  pl.saving = true;
  renderStudioPanel();
  const del = (force) => sendJSON("DELETE", `/api/plans/${p.id}${force ? "?force=true" : ""}`);
  try {
    let r;
    try { r = await del(false); }
    catch (err) {
      if (err.status !== 409 || !/schedule window/.test(err.message) || !confirm(`${capital(err.message)}.\n\nDelete it anyway?`)) throw err;
      r = await del(true);
    }
    if (r.ok) { toast(`Deleted “${p.name}” (read back: gone from the robot).`); pl.edit = null; }
    else toast(`Not deleted: ${r.error}.`);
    if (r.plans) pl.plans = r.plans;
  } catch (err) {
    toast(`Not deleted: ${err.message}`);
  }
  pl.saving = false;
  renderStudioPanel();
}

function planEditor(pl) {
  const e = pl.edit, names = new Map(pl.areas.map((a) => [a.id, a.name]));
  const toggle = (id, on) => { e.areaIds = on ? [...e.areaIds, id] : e.areaIds.filter((x) => x !== id); renderStudioPanel(); };
  const ok = e.areaIds.length && !pl.blocked && !pl.saving;     // an empty name is refused by the server, with a message
  return h("div", { class: "card" },
    h("p", { class: "label" }, e.id == null ? "New plan" : `Edit plan ${e.id}`),
    h("input", { type: "text", class: "field slim", style: "width:100%", maxlength: "40", autocomplete: "off", placeholder: "Plan name", value: e.name, "data-key": "plan:name",
                 "aria-label": "Plan name", oninput: (ev) => { e.name = ev.target.value; } }),
    h("p", { class: "muted small", style: "margin:10px 0 4px" }, "Areas, mowed in the order picked ",
      tip("The robot may reorder them itself (the app's “self order”, kept on as the app sends it).")),
    pl.areas.map((a) => {
      const i = e.areaIds.indexOf(a.id);
      return h("label", { class: "vrow", onmouseenter: () => hoverShape({ kind: "areas", id: a.id }), onmouseleave: () => hoverShape(null) },
        h("span", { class: "row gap" }, h("input", { type: "checkbox", checked: i >= 0 || null, "data-key": `plan:area:${a.id}`,
                                                     onchange: (ev) => toggle(a.id, ev.target.checked) }), a.name),
        i >= 0 ? h("span", { class: "muted tiny" }, `#${i + 1}`) : null);
    }),
    e.areaIds.some((id) => !names.has(id)) ? h("p", { class: "small", style: `color:${AMBER}` }, "This plan names an area that is no longer on the map.") : null,
    pl.blocked ? h("p", { class: "muted small", style: "margin:8px 0 0" }, `Saving is off: ${pl.blocked}.`) : null,
    h("div", { class: "row gap wrap", style: "margin-top:8px" },
      btn(pl.saving ? "Saving…" : "Save to robot", savePlan, "btn pri", { disabled: !ok || null,
        title: "Sends save_plan, then reads the plans back; a changed plan that does not match is put back" }),
      btn("Cancel", () => { pl.edit = null; renderStudioPanel(); }, "btn slim", { disabled: pl.saving || null }),
      e.id != null ? btn("Delete…", deletePlan, "btn slim", { style: "margin-left:auto", disabled: pl.saving || !!pl.blocked || null,
                                                             title: "Delete this plan from the robot (asks first)" }) : null));
}

function plansTab() {
  const pl = studio.plans;
  if (!pl || (pl.loading && !pl.plans)) { if (!pl) loadPlans(); return h("p", { class: "muted pad" }, "Reading the plans from the robot…"); }
  const names = new Map((pl.areas || []).map((a) => [a.id, a.name]));
  const list = h("div", { class: "card" },
    h("div", { class: "row gap", style: "justify-content:space-between" },
      h("p", { class: "label", style: "margin:0" }, "Plans on the robot ",
        tip("A plan is a list of areas to mow in one go. Live's control card and Schedule windows start plans. Edit → Delete removes one.")),
      btn(pl.loading ? "Reading…" : "Reload", () => loadPlans(), "btn slim", { disabled: pl.loading || null })),
    pl.error ? h("p", { class: "small", style: `color:${AMBER}` }, `Could not read them fresh: ${pl.error}. Showing the last ones read.`) : null,
    (pl.plans || []).length ? pl.plans.map((p) => h("div", { class: "vrow", style: "flex-wrap:wrap" },
      h("span", { style: "flex:1;min-width:0" }, h("b", null, p.name || `Plan ${p.id}`),
        h("span", { class: "muted tiny", style: "display:block" }, (p.areaIds || []).map((id) => names.get(id) || `area ${id} (not on the map)`).join(" → ")),
        planRouteLine(pl, p)),
      h("span", { class: "row gap" },
        btn(pl.routeBusy === p.id ? "Planning…" : "Preview", () => previewPlan(p), "btn slim",
            { disabled: pl.routeBusy != null || null, title: "Ask the robot to plan this plan's mowing route and show it on the map (about 10 s per area). It only computes; nothing moves." }),
        btn("Edit", () => editPlan(p), "btn slim", { disabled: pl.saving || null }))))
      : h("p", { class: "muted small" }, "No plans on the robot."),
    h("div", { class: "row gap wrap", style: "margin-top:8px" },
      btn("New plan", () => editPlan(null), "btn slim", { disabled: pl.saving || !(pl.areas || []).length || null }),
      pl.blocked && studio.data?.write && !studio.data.write.enabled ? btn("Map writing on…", () => setMapWriting(true), "btn slim") : null),
    pl.blocked && !pl.edit ? h("p", { class: "muted small", style: "margin:8px 0 0" }, `Saving is off: ${pl.blocked}.`) : null);
  return [pl.edit ? planEditor(pl) : null, list];
}

/* Record an app edit: the way to learn a command the panel has never seen (e.g. the app's area edit). */
async function appEdit(action) {
  try {
    const r = await sendJSON("POST", "/api/studio/app-edit", { action, minutes: 30 });
    studio.appEdit = r.session;
    if (action === "stop") await refreshStudio();
  } catch (e) { toast(e.message); }
  renderStudioPanel();
  pollAppEdit();
}

function pollAppEdit() {
  clearTimeout(studio.appEditTimer);
  const s = studio.appEdit;
  if (!s || s.ended) return;
  studio.appEditTimer = setTimeout(async () => {
    try { studio.appEdit = (await getJSON("/api/studio/app-edit")).session; } catch { /* keep */ }
    if (state.view === "studio" && studio.tab === "more") renderStudioPanel();
    pollAppEdit();
  }, 3000);
}

function appEditCard() {
  const s = studio.appEdit;
  const head = h("p", { class: "label" }, "Record an app edit ",
    tip("To teach the panel a command it has never seen (like the app's area edit): start, do the edit in the Yarbo app, then stop. Everything the app sends and the robot answers is kept, and the map is compared before and after. The panel sends nothing but reads."));
  if (!s || s.ended) {
    const changes = s && s.map_changes;
    return h("div", { class: "card" }, head,
      s ? h("div", { class: "small" },
        h("p", { style: "margin:0 0 4px" }, `Last recording: ${Object.values(s.kept).reduce((a, b) => a + b, 0)} messages kept`),
        Object.keys(s.kept).length ? h("p", { class: "muted tiny", style: "margin:0 0 4px" }, Object.entries(s.kept).map(([k, n]) => `${k} ×${n}`).join(" · ")) : null,
        changes ? h("p", { style: "margin:0 0 6px" }, changes.length
          ? ["Map changes: ", changes.map((c) => `${c.what} ${c.kind.replace(/s$/, "")} “${c.name || c.id}”${c.changes ? ` (${c.changes.join(", ")})` : ""}`).join("; ")]
          : "The map did not change.") : null,
        settingsSaves(s),
        s.map_error ? h("p", { style: `color:${AMBER};margin:0` }, `Could not read the map afterwards: ${s.map_error}`) : null) : null,
      btn("Start recording (30 min)", () => appEdit("start"), "btn slim pri", { "data-key": "appedit:start" }));
  }
  const left = Math.max(0, Math.round((s.until - Date.now() / 1000) / 60));
  const kept = Object.entries(s.kept);
  return h("div", { class: "card attention" }, head,
    h("p", { class: "small", style: "margin:0 0 6px" }, h("b", null, "Recording"), ` · ${left} min left. Do the edit in the Yarbo app now, then stop.`),
    settingsSaves(s),
    kept.length ? h("p", { class: "muted tiny", style: "margin:0 0 6px" }, kept.map(([k, n]) => `${k} ×${n}`).join(" · "))
                : h("p", { class: "muted tiny", style: "margin:0 0 6px" }, "Nothing from the app yet."),
    btn("Stop and compare the map", () => appEdit("stop"), "btn slim pri", { "data-key": "appedit:stop" }));
}

// Each area-settings save the app made during a recording, as the fields it changed (one change per save makes each
// app option's robot value plain).
function settingsSaves(s) {
  const saves = (s && s.settings_changes) || [];
  if (!saves.length) return null;
  return h("div", { class: "small", style: "margin:0 0 6px" }, h("b", null, "Settings saved in the app"),
    h("ul", { class: "log" }, saves.map((c) => h("li", null,
      h("span", { class: "muted" }, `${clock(c.ts)} ${c.name || `area ${c.id}`}: `),
      !c.baseline ? "saved (no earlier copy to compare with)"
        : c.changes.length ? c.changes.map((x) => `${x.label || x.key} ${JSON.stringify(x.from)} → ${JSON.stringify(x.to)}`).join("; ")
        : "saved, nothing changed"))));
}

function capturesCard() {
  const caps = studio.data.captures;
  return h("div", { class: "card" },
    h("p", { class: "label" }, "Captured writes ", tip("Commands other apps send that change the robot (reads are not kept). To learn a command, do it once in the Yarbo app while this panel runs.")),
    caps.length ? shortList("captures", caps.map((c) => h("div", { class: "vrow" },
      h("span", null, c.name, h("span", { class: "muted tiny" }, ` · ${when(c.ts)} · ${c.bytes.toLocaleString()} B`)),
      h("a", { class: "btn slim", href: `/api/studio/captures/${c.id}`, target: "_blank", rel: "noopener" }, "View"))))
      : h("p", { class: "muted small" }, "None yet."));
}

/* -------------------------------------------------------------- assembly */
function tabsBar() {
  const n = changeCount(), errs = studio.draft ? studio.draft.issues.filter((i) => i.level === "error").length : 0;
  return h("div", { class: "stabs", role: "tablist", "aria-label": "Map Studio" },
    STUDIO_TABS.map(([id, label]) => h("button", { type: "button", role: "tab", class: `stab${studio.tab === id ? " on" : ""}`,
        "aria-selected": String(studio.tab === id), "data-key": `tab:${id}`, onclick: () => setStudioTab(id) },
      label,
      id === "yard" && studio.draft && (n || errs) ? h("span", { class: `tbadge${errs ? " err" : ""}`, title: errs ? `${errs} error${errs === 1 ? "" : "s"}` : `${n} change${n === 1 ? "" : "s"}` }, String(n)) : null)));
}

function renderStudioPanel() {
  const panel = $("#p-studio");
  if (!panel || !studio.data || state.view !== "studio") return;
  const top = panel.scrollTop, focused = document.activeElement?.dataset?.key || null;
  if (studio.renaming && !findRow(Object.fromEntries([["kind", studio.renaming.key.split(":")[0]], ["id", Number(studio.renaming.key.split(":")[1])]]))) studio.renaming = null;
  const body = studio.tab === "history" ? historyTab()
    : studio.tab === "plans" ? plansTab()
    : studio.tab === "more" ? [appEditCard(), toolsCard(), capturesCard()]
    : [draftSection(), selectedCard() || emptyHint(), shapesCard()];
  hoverShape(null);
  panelRendering = true;
  try { fill(panel, tabsBar(), compareCard(), body); } finally { panelRendering = false; }
  panel.scrollTop = top;
  if (focused && focused !== "rename") $(`[data-key="${focused}"]`, panel)?.focus({ preventScroll: true });
  else if (studio.renaming) $('[data-key="rename"]', panel)?.restore?.();
}
