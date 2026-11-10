/* The robot as a 3D model on the map's 3D view (2026-10-10).
   A three.js custom layer, built from simple shapes about the robot's size (forward is +Y, up is +Z, in metres). It
   stands on the drawn ground, turns to the robot's compass heading and tilts by its pitch and roll: on run 4 the
   pitch read positive nose-down and the roll positive left side up. On a ground drawn taller than it is, the tilt is
   drawn taller to match (atan(height x tan(angle))). Zoomed out it keeps a minimum size on screen. It is lit from
   the north-north-west whichever way it faces, as the ground's shading is, and stands on a soft shadow.
   Live, it takes every position sample the robot sends (the stream's pose events, #35: ~1 a second while the phone
   app is open, else the panel's own reads),
   in the robot's own time order: a late one from the backlog after a dropout is dropped instead of drawn as a detour.
   A single sample further away than the robot could have driven is held until the next one confirms or drops it.
   Between samples it carries on at the reported speed and heading for up to 2.5 s, then catches up with the next
   real position, faster the further behind it is but never in a jump. In a replay it follows the replay point.
   three.js is loaded the first time the 3D view is switched on.
   Follow (2026-10-11): the camera stays with the model, behind it and R3D_FOLLOW_SIDE° to one side, and turns with
   its heading at most R3D_FOLLOW_TURN°/s, so the U-turn at the end of each stripe swings it round instead of
   spinning it. Right-drag (two fingers) moves the camera round the robot and it keeps that angle; zoom and tilt
   stay as set; a pan or Fit yard lets go. */
const R3D = {
  THREE: null, loading: null, layer: null,
  pose: null,         // what is drawn: {lon, lat, heading, pitch, roll}
  fix: null,          // the last live position: {lon, lat, heading, pitch, roll, speed, t (robot time), at}
  held: null,         // a far sample waiting for the next one to confirm it (the speed gate): {s, at}
  pace: 0,            // metres a second between recent samples, smoothed (the speed gate)
  glide: null,        // a replay step: {from, to, t0, ms}
  mode: "live",       // live (the robot now) | replay (a selected run's replay point)
  colour: null,
  raf: 0, last: 0,
  follow: { on: false, offset: 45, cam: null, turning: false, hands: false, wheelAt: 0 },   // cam: the heading the camera has turned to
};
const R3D_DEG = Math.PI / 180;
const R3D_LEN = 1.35;            // metres, the mower deck's bumper to the back of the tracks
const R3D_MIN_PX = 34;           // the model is never drawn shorter than this on screen
const R3D_AHEAD_S = 2.5;         // keep moving on the last speed and heading for this long, then wait
const R3D_EASE_S = 0.5;          // how quickly it closes on where it should be
const R3D_JUMP_M = 25;           // further behind than this (a new run, a long gap): put it there at once
const R3D_RESET_S = 30;          // nothing newer by the robot's clock this long: take the next sample anyway
const R3D_GATE_MPS = 2;          // faster than this between two samples is a stray reading, not the robot (it does ~1 m/s)
const R3D_GATE_PACE = 2.5;       // ...or than this many times the pace it has been keeping (a sped-up demo is faster)
const R3D_GATE_M = 3;            // ...beyond this much room for noise, whatever the time between them
const R3D_GATE_HOLD_S = 6;       // a far sample nothing confirms is dropped; one this much later is taken anyway
const R3D_FOLLOW_SIDE = 45;      // the camera sits behind the robot and this far round to one side
const R3D_FOLLOW_TURN = 40;      // degrees a second it turns at most to stay behind the robot
const R3D_FOLLOW_LEAD = 1.5;     // metres ahead of the robot the camera looks at

const r3dWanted = () => typeof want3d === "function" && want3d() && state.mapReady;
const r3dMetres = (lat) => [111320 * Math.cos(lat * R3D_DEG), 111320];
const r3dAngle = (a) => ((a % 360) + 540) % 360 - 180;            // -180..180

function r3dBuild(THREE) {
  const g = new THREE.Group();
  const mat = (c) => new THREE.MeshLambertMaterial({ color: c });
  // A soft shadow on the ground under it, so it stands on the lawn instead of floating over it: three see-through
  // ellipses, darker toward the middle. They are drawn through the ground (the map's ground hides anything level
  // with it) but before the robot, so it covers them: blended in the opaque pass, which keeps renderOrder.
  // Both sides drawn, so the way they face never hides them. (A faded texture or per-vertex alpha did not show here.)
  [[0.75, 0.9], [0.62, 0.76], [0.5, 0.62]].forEach(([rx, ry], i) => {
    const m = new THREE.Mesh(new THREE.CircleGeometry(1, 40), new THREE.MeshBasicMaterial({
      color: 0x000000, opacity: 0.28, transparent: false, blending: THREE.CustomBlending,
      blendSrc: THREE.SrcAlphaFactor, blendDst: THREE.OneMinusSrcAlphaFactor,
      depthTest: false, depthWrite: false, side: THREE.DoubleSide }));
    m.scale.set(rx, ry, 1);
    m.position.set(0, 0.06, 0.01);
    m.renderOrder = -1 - i;
    g.add(m);
  });
  // A Yarbo core with the mower head, about life size: rounded tracks, the body with its top cover and carry handle,
  // the two GNSS antennas on top, a light bar in the status colour, and the deck in front. Simple solids, so it
  // reads at 34 px as well as close up.
  const dark = mat(MAP.track3d), body = mat(MAP.body3d), trim = mat(MAP.trim3d);
  const add = (geo, m, x, y, z) => { const o = new THREE.Mesh(geo, m); o.position.set(x, y, z); g.add(o); return o; };
  // A rounded rectangle, w across and l long, centred on 0,0.
  const rounded = (w, l, r) => {
    const s = new THREE.Shape(), x = w / 2, y = l / 2;
    s.moveTo(-x + r, -y);
    s.lineTo(x - r, -y); s.quadraticCurveTo(x, -y, x, -y + r);
    s.lineTo(x, y - r); s.quadraticCurveTo(x, y, x - r, y);
    s.lineTo(-x + r, y); s.quadraticCurveTo(-x, y, -x, y - r);
    s.lineTo(-x, -y + r); s.quadraticCurveTo(-x, -y, -x + r, -y);
    return s;
  };
  // A slab of that outline, `h` tall from its base, with softened edges.
  const slab = (w, l, h, r, bevel = 0.015) => {
    const geo = new THREE.ExtrudeGeometry(rounded(w - 2 * bevel, l - 2 * bevel, r), {
      depth: h - 2 * bevel, bevelEnabled: true, bevelSize: bevel, bevelThickness: bevel, bevelSegments: 2, curveSegments: 8 });
    geo.translate(0, 0, bevel);
    return geo;
  };
  // Tracks: a stadium seen from the side (0.95 m long, 0.26 m tall), 0.17 m wide, with a hub at each end.
  const track = new THREE.ExtrudeGeometry(rounded(0.95, 0.26, 0.13), {
    depth: 0.15, bevelEnabled: true, bevelSize: 0.01, bevelThickness: 0.01, bevelSegments: 2, curveSegments: 12 });
  track.rotateY(Math.PI / 2);                              // the outline's across -> along, its depth -> across
  track.rotateX(Math.PI / 2);                              // its height -> up
  track.translate(-0.075, 0, 0.13);
  const hub = new THREE.CylinderGeometry(0.07, 0.07, 0.02, 20);
  hub.rotateZ(Math.PI / 2);
  for (const side of [1, -1]) {
    add(track, dark, side * 0.33, 0, 0);
    for (const y of [0.34, -0.34]) add(hub, body, side * 0.42, y, 0.13);
  }
  add(slab(0.50, 0.82, 0.30, 0.08), body, 0, -0.02, 0.21);                  // body
  add(slab(0.44, 0.60, 0.05, 0.06), dark, 0, -0.06, 0.50);                  // top cover
  const handle = new THREE.TorusGeometry(0.17, 0.018, 8, 24, Math.PI);       // carry handle across the back
  handle.rotateX(Math.PI / 2);
  add(handle, dark, 0, -0.38, 0.50);
  const puck = new THREE.CylinderGeometry(0.065, 0.075, 0.035, 20);          // the two GNSS antennas
  puck.rotateX(Math.PI / 2);
  for (const y of [0.16, -0.24]) add(puck, mat(MAP.onmap), 0, y, 0.57);
  R3D.lamp = add(new THREE.BoxGeometry(0.34, 0.03, 0.04), new THREE.MeshBasicMaterial({ color: R3D.colour || MAP.robot }),
                 0, 0.39, 0.44);                                             // light bar across the front, status colour
  add(new THREE.BoxGeometry(0.30, 0.16, 0.08), dark, 0, 0.47, 0.16);        // the arm the head hangs on
  const deck = new THREE.Shape();                                            // the mower deck: square back, round front
  deck.moveTo(-0.41, -0.18);
  deck.lineTo(0.41, -0.18);
  deck.lineTo(0.41, 0.04);
  deck.quadraticCurveTo(0.41, 0.20, 0.20, 0.20);
  deck.lineTo(-0.20, 0.20);
  deck.quadraticCurveTo(-0.41, 0.20, -0.41, 0.04);
  deck.lineTo(-0.41, -0.18);
  const deckGeo = new THREE.ExtrudeGeometry(deck, {
    depth: 0.10, bevelEnabled: true, bevelSize: 0.015, bevelThickness: 0.015, bevelSegments: 2, curveSegments: 12 });
  add(deckGeo, dark, 0, 0.66, 0.035);
  add(slab(0.44, 0.035, 0.035, 0.012, 0.008), trim, 0, 0.875, 0.08);        // its bumper strip
  return g;
}

function r3dLayer(THREE) {
  return {
    id: "robot3d", type: "custom", renderingMode: "3d",
    // The layer comes off and goes back on with the 3D view; the scene and renderer are made once and kept, so
    // switching does not leave a renderer and its buffers behind each time.
    onAdd(map, gl) {
      this.map = map;
      if (this.renderer) return;
      this.camera = new THREE.Camera();
      this.scene = new THREE.Scene();                 // x east, y north, z up, in metres: the light stays put as the robot turns
      const sky = new THREE.HemisphereLight(0xffffff, 0x444444, 1.6);
      sky.position.set(0, 0, 1);                      // the sky is up
      this.scene.add(sky);
      const sun = new THREE.DirectionalLight(0xffffff, 1.2);
      sun.position.set(-1, 2, 3);                    // from 333°, about where the ground's shading is lit from (335°)
      this.scene.add(sun);
      this.robot = r3dBuild(THREE);
      this.scene.add(this.robot);
      this.renderer = new THREE.WebGLRenderer({ canvas: map.getCanvas(), context: gl, antialias: true });
      this.renderer.autoClear = false;
    },
    render(gl, matrix) {
      const p = R3D.pose, map = this.map;
      if (!p) return;
      const at = [p.lon, p.lat];
      const mc = maplibregl.MercatorCoordinate.fromLngLat(at, map.queryTerrainElevation(at) || 0);  // includes the height x
      const mpp = (40075016.686 * Math.cos(p.lat * R3D_DEG)) / (512 * 2 ** map.getZoom());
      const s = mc.meterInMercatorCoordinateUnits() * Math.max(1, (R3D_MIN_PX * mpp) / R3D_LEN);
      const x = (map.getTerrain() && map.getTerrain().exaggeration) || 1;
      const nose = Math.atan(x * Math.tan(-(p.pitch || 0) * R3D_DEG));
      const left = Math.atan(x * Math.tan((p.roll || 0) * R3D_DEG));
      const M = THREE.Matrix4;
      this.robot.rotation.set(nose, left, -(p.heading || 0) * R3D_DEG, "ZXY");      // heading, then pitch, then roll
      const place = new M().makeTranslation(mc.x, mc.y, mc.z).scale(new THREE.Vector3(s, -s, s));
      this.camera.projectionMatrix = new M().fromArray(matrix).multiply(place);
      this.renderer.resetState();
      this.renderer.render(this.scene, this.camera);
    },
  };
}

// The dots the model stands in for: the live robot's, or the replay point's.
function r3dDots(hideLive, hideReplay) {
  paint("robot", "circle-opacity", hideLive ? 0 : 1);
  paint("robot", "circle-stroke-opacity", hideLive ? 0 : 1);
  paint("robot-heading", "icon-opacity", hideLive ? 0 : 1);
  paint("robot-halo", "circle-opacity", hideLive ? 0 : 0.25);
  paint("replay", "circle-opacity", hideReplay ? 0 : 1);
  paint("replay", "circle-stroke-opacity", hideReplay ? 0 : 1);
}

// Show or hide the model to match the map (3D on, view), and point it at the live robot or the replay.
async function robot3dSync() {
  const map = state.map;
  if (!map) return;
  const want = r3dWanted();
  const mode = state.run ? "replay" : "live";
  if (mode !== R3D.mode) { R3D.mode = mode; R3D.pose = null; R3D.glide = null; }
  if (!want) {
    robot3dFollow(false);
    if (map.getLayer("robot3d")) map.removeLayer("robot3d");
    r3dDots(false, false);
    return;
  }
  if (!R3D.THREE) {
    try {
      R3D.loading ||= import("/vendor/three.module.min.js");
      R3D.THREE = await R3D.loading;
    } catch (e) {
      R3D.loading = null;
      toast(`3D robot unavailable: ${e.message}`);
      return;
    }
    if (!r3dWanted()) return;
  }
  if (!map.getLayer("robot3d")) map.addLayer(R3D.layer ||= r3dLayer(R3D.THREE));        // on top of everything
  if (!R3D.bound) {
    R3D.bound = true;
    map.on("dragstart", (e) => { if (e.originalEvent && R3D.follow.on) robot3dFollow(false); });   // a pan lets go
    map.on("rotatestart", (e) => { if (e.originalEvent) R3D.follow.turning = true; });
    map.on("zoomend", (e) => { if (e.originalEvent && R3D.follow.on) r3dKick(); });    // a zoom about the cursor: back on the robot
    // Moving the camera stops any gesture under way, so it holds still while a finger or button is on the map (and
    // just after a wheel zoom): a pan then gets through (and lets go), a right-drag sets a new angle. Every finger
    // is counted, so lifting one of two does not move the camera under the other, and a touch the browser takes
    // over (pointercancel) lets go like a lifted one.
    const box = map.getCanvasContainer(), down = new Set();
    const free = () => { if (!R3D.follow.hands) return; R3D.follow.hands = false; if (R3D.follow.on) r3dKick(); };
    const lift = (e) => { down.delete(e.pointerId); if (!down.size) free(); };
    box.addEventListener("pointerdown", (e) => { down.add(e.pointerId); R3D.follow.hands = true; });
    box.addEventListener("wheel", () => { R3D.follow.wheelAt = performance.now(); }, { passive: true });
    window.addEventListener("pointerup", lift);
    window.addEventListener("pointercancel", lift);
    window.addEventListener("blur", () => { down.clear(); free(); });        // a lift the page never hears of
    map.on("rotateend", (e) => {
      if (!R3D.follow.turning) return;
      R3D.follow.turning = false;
      if (R3D.follow.on && R3D.follow.cam !== null) R3D.follow.offset = r3dAngle(map.getBearing() - R3D.follow.cam);
    });
  }
  r3dDots(mode === "live", mode === "replay");
  if (mode === "replay" && state.run) robot3dReplay(state.run.rows[Math.min(state.replayIndex, state.run.rows.length - 1)]);
  else if (R3D.fix && !R3D.pose) R3D.pose = { ...R3D.fix };
  r3dKick();
}

// A live status: where the robot is now. Called with status.robot.
function robot3dLive(robot) {
  const p = robot && robot.position;
  if (!p) return;
  const sp = (robot.speed || []).filter((v) => typeof v === "number");
  r3dTake({ ...p, pitch: robot.pitch, roll: robot.roll, speed: sp.length ? sp.reduce((a, b) => a + b, 0) / sp.length : 0 });
}

// The pose stream's samples (/api/stream "pose", ~1 a second while it moves), oldest first.
function robot3dPoses(list) {
  for (const s of list || []) r3dTake(s);
}

// One position sample: {lon, lat, heading, pitch, roll, speed, t} with t the robot's own time for it. The same time
// again is the same sample (the status repeats the last one every 1.5 s, also through a dropout) and is dropped. An
// older one is late (the backlog after a dropout) and dropped too, unless nothing newer has come for R3D_RESET_S
// (the robot's clock was set back).
function r3dTake(s) {
  if (!s || s.lon === null || s.lon === undefined) return;
  const now = performance.now(), f = R3D.fix;
  if (f && f.t != null && s.t != null && (s.t === f.t || (s.t < f.t && now - f.at < R3D_RESET_S * 1000))) return;
  if (f && s.t == null && s.lon === f.lon && s.lat === f.lat) return;     // no robot time: the same sample again
  if (!r3dGate(s, now)) return;
  R3D.fix = { lon: s.lon, lat: s.lat, heading: s.heading ?? (f ? f.heading : 0), pitch: s.pitch || 0, roll: s.roll || 0,
              speed: s.speed || 0, t: s.t ?? null, at: now };
  if (!state.map || !state.map.getLayer("robot3d") || R3D.mode !== "live") return;      // kept for when 3D comes on
  if (!R3D.pose) R3D.pose = { ...R3D.fix };
  r3dKick();
}

// The speed gate: whether to take a sample. One that is further from the last than the robot could have driven in
// the time between them is held, not drawn: a single stray position would otherwise send the model off and back.
// The next sample decides. Near the held one, the robot really moved (it was carried, or a long gap ended) and the
// jump is taken; near the last one, the held one was a stray and is dropped. A held sample that nothing has
// confirmed for R3D_GATE_HOLD_S is dropped, and after that long with nothing taken the next sample is taken as it is.
// The pace it allows is the larger of R3D_GATE_MPS and R3D_GATE_PACE times the pace kept between recent samples.
function r3dGate(s, now) {
  const f = R3D.fix;
  if (!f || now - f.at > R3D_GATE_HOLD_S * 1000) { R3D.held = null; R3D.pace = 0; return true; }
  const dist = (a, b) => { const [kx, ky] = r3dMetres(b.lat); return Math.hypot((a.lon - b.lon) * kx, (a.lat - b.lat) * ky); };
  const gap = (a, b, aAt, bAt) => Math.max(0, a.t != null && b.t != null ? a.t - b.t : (aAt - bAt) / 1000);
  const fits = (d, secs) => d <= R3D_GATE_M + Math.max(R3D_GATE_MPS, R3D_GATE_PACE * (R3D.pace || 0)) * secs;
  const take = (d, secs) => {          // a sample taken: the pace between it and the one before, smoothed
    if (secs > 0.2 && secs <= 3) R3D.pace = 0.7 * (R3D.pace || 0) + 0.3 * Math.min(10, d / secs);
    R3D.held = null;
    return true;
  };
  const df = dist(s, f), gf = gap(s, f, now, f.at);
  if (fits(df, gf)) return take(df, gf);
  const h = R3D.held;
  if (h && now - h.at <= R3D_GATE_HOLD_S * 1000) {
    const dh = dist(s, h.s), gh = gap(s, h.s, now, h.at);
    if (fits(dh, gh)) return take(dh, gh);
  }
  R3D.held = { s, at: now };           // too far from the last: wait for the next one to confirm it
  return false;
}

// A replay step: glide from where the model is to the replay row over the next timer tick.
function robot3dReplay(row) {
  if (!row || !state.run || R3D.mode !== "replay" || !state.map || !state.map.getLayer("robot3d")) return;
  const f = Object.fromEntries(state.run.fields.map((k, i) => [k, i]));
  const to = { lon: row[f.lon], lat: row[f.lat], heading: row[f.heading] ?? (R3D.pose ? R3D.pose.heading : 0),
               pitch: row[f.pitch] || 0, roll: row[f.roll] || 0 };
  if (!R3D.pose) { R3D.pose = to; r3dKick(); return; }
  const [kx, ky] = r3dMetres(to.lat);
  const far = Math.hypot((to.lon - R3D.pose.lon) * kx, (to.lat - R3D.pose.lat) * ky) > R3D_JUMP_M * 4;
  R3D.glide = far ? null : { from: { ...R3D.pose }, to, t0: performance.now(), ms: state.replayTimer ? 60 : 120 };
  if (far) R3D.pose = to;
  r3dKick();
}

function robot3dColour(c) {
  R3D.colour = c;
  if (R3D.lamp) R3D.lamp.material.color.set(c);
  if (state.map && state.map.getLayer("robot3d")) state.map.triggerRepaint();
}

function r3dKick() {
  if (!R3D.raf) { R3D.last = performance.now(); R3D.raf = requestAnimationFrame(r3dTick); }
}

// One frame: move the drawn pose toward where the robot should be; stop asking for frames once it is there.
function r3dTick(now) {
  R3D.raf = 0;
  const map = state.map;
  if (!map || !map.getLayer("robot3d") || !R3D.pose) return;
  const dt = Math.max(0, Math.min(0.25, (now - R3D.last) / 1000));   // a frame's time can be a little before the request
  R3D.last = now;
  let busy = false;
  const p = R3D.pose;
  if (R3D.mode === "replay" && R3D.glide) {
    const g = R3D.glide, t = Math.min(1, (now - g.t0) / g.ms);
    p.lon = g.from.lon + (g.to.lon - g.from.lon) * t;
    p.lat = g.from.lat + (g.to.lat - g.from.lat) * t;
    p.heading = (g.from.heading + r3dAngle(g.to.heading - g.from.heading) * t + 360) % 360;
    p.pitch = g.from.pitch + (g.to.pitch - g.from.pitch) * t;
    p.roll = g.from.roll + (g.to.roll - g.from.roll) * t;
    if (t < 1) busy = true; else R3D.glide = null;
  } else if (R3D.mode === "live" && R3D.fix) {
    const f = R3D.fix, [kx, ky] = r3dMetres(f.lat);
    const ahead = Math.min(R3D_AHEAD_S, (now - f.at) / 1000) * (Math.abs(f.speed) > 0.03 ? f.speed : 0);
    const tx = f.lon + (Math.sin(f.heading * R3D_DEG) * ahead) / kx, ty = f.lat + (Math.cos(f.heading * R3D_DEG) * ahead) / ky;
    const ex = (tx - p.lon) * kx, ey = (ty - p.lat) * ky, err = Math.hypot(ex, ey);
    if (err > R3D_JUMP_M) { p.lon = tx; p.lat = ty; }
    else {
      const k = 1 - Math.exp(-dt / R3D_EASE_S), cap = (0.9 + err * 0.6) * dt;
      let sx = ex * k, sy = ey * k;
      const m = Math.hypot(sx, sy);
      if (m > cap && m > 1e-9) { sx *= cap / m; sy *= cap / m; }
      p.lon += sx / kx;
      p.lat += sy / ky;
    }
    const turn = r3dAngle(f.heading - p.heading), e = 1 - Math.exp(-dt * 4);
    p.heading += turn * e;
    p.pitch += (f.pitch - p.pitch) * e;
    p.roll += (f.roll - p.roll) * e;
    busy = err > 0.01 || Math.abs(turn) > 0.3 || ((now - f.at) / 1000 < R3D_AHEAD_S && Math.abs(f.speed) > 0.03);
  }
  if (R3D.follow.on && r3dFollowFrame(dt)) busy = true;
  map.triggerRepaint();
  if (busy) R3D.raf = requestAnimationFrame(r3dTick);
}

// Follow on or off. On: fly behind the robot (and to one side), then stay with it; off: leave the camera where it is.
function robot3dFollow(on) {
  const map = state.map, f = R3D.follow;
  on = !!on && !!map && !!map.getLayer("robot3d") && !!R3D.pose;
  if (on === f.on) return syncFollowButton();
  f.on = on;
  syncFollowButton();
  if (!on) return;
  const p = R3D.pose;
  f.cam = p.heading;
  f.offset = R3D_FOLLOW_SIDE;
  map.easeTo({ center: r3dAhead(p), bearing: p.heading + f.offset, zoom: Math.max(map.getZoom(), 21.5), pitch: 62, duration: 900 });
  setTimeout(r3dKick, 950);
}

function syncFollowButton() {
  const b = typeof $ === "function" && $("#follow3d");
  if (!b) return;
  b.classList.toggle("on", R3D.follow.on);
  b.setAttribute("aria-pressed", String(R3D.follow.on));
}

const r3dAhead = (p) => {
  const [kx, ky] = r3dMetres(p.lat);
  return [p.lon + (Math.sin(p.heading * R3D_DEG) * R3D_FOLLOW_LEAD) / kx, p.lat + (Math.cos(p.heading * R3D_DEG) * R3D_FOLLOW_LEAD) / ky];
};

// One frame of following: turn toward the robot's heading (at most R3D_FOLLOW_TURN°/s) and keep it in view. True
// while the camera is still turning.
function r3dFollowFrame(dt) {
  const map = state.map, f = R3D.follow, p = R3D.pose;
  if (!map || !p || map.isEasing() || f.hands || performance.now() - f.wheelAt < 400) return true;
  if (f.cam === null) f.cam = p.heading;
  const d = r3dAngle(p.heading - f.cam), cap = R3D_FOLLOW_TURN * dt;
  f.cam += Math.max(-cap, Math.min(cap, d * (1 - Math.exp(-dt * 2.5))));
  map.jumpTo(f.turning ? { center: r3dAhead(p) } : { center: r3dAhead(p), bearing: f.cam + f.offset });
  return Math.abs(d) > 0.5;
}

