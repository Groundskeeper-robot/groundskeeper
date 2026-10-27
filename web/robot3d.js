/* The robot as a 3D model on the map's 3D view (2026-10-10).
   A three.js custom layer, built from simple shapes about the robot's size (forward is +Y, up is +Z, in metres). It
   stands on the drawn ground, turns to the robot's compass heading and tilts by its pitch and roll: on run 4 the
   pitch read positive nose-down and the roll positive left side up. On a ground drawn taller than it is, the tilt is
   drawn taller to match (atan(height x tan(angle))). Zoomed out it keeps a minimum size on screen.
   Live, it takes every position sample the robot sends (the stream's pose events, #35: ~1 a second while the phone
   app is open, else the panel's own reads),
   in the robot's own time order: a late one from the backlog after a dropout is dropped instead of drawn as a detour.
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
  glide: null,        // a replay step: {from, to, t0, ms}
  mode: "live",       // live (the robot now) | replay (a selected run's replay point)
  colour: null,
  raf: 0, last: 0,
  follow: { on: false, offset: 45, cam: null, turning: false, hands: false, wheelAt: 0 },   // cam: the heading the camera has turned to
};
const R3D_DEG = Math.PI / 180;
const R3D_LEN = 1.05;            // metres, nose to tail
const R3D_MIN_PX = 34;           // the model is never drawn shorter than this on screen
const R3D_AHEAD_S = 2.5;         // keep moving on the last speed and heading for this long, then wait
const R3D_EASE_S = 0.5;          // how quickly it closes on where it should be
const R3D_JUMP_M = 25;           // further behind than this (a new run, a long gap): put it there at once
const R3D_RESET_S = 30;          // nothing newer by the robot's clock this long: take the next sample anyway
const R3D_FOLLOW_SIDE = 45;      // the camera sits behind the robot and this far round to one side
const R3D_FOLLOW_TURN = 40;      // degrees a second it turns at most to stay behind the robot
const R3D_FOLLOW_LEAD = 1.5;     // metres ahead of the robot the camera looks at

const r3dWanted = () => typeof want3d === "function" && want3d() && state.mapReady;
const r3dMetres = (lat) => [111320 * Math.cos(lat * R3D_DEG), 111320];
const r3dAngle = (a) => ((a % 360) + 540) % 360 - 180;            // -180..180

function r3dBuild(THREE) {
  const g = new THREE.Group();
  const mat = (c) => new THREE.MeshLambertMaterial({ color: c });
  const box = (w, l, h, c, x, y, z) => { const m = new THREE.Mesh(new THREE.BoxGeometry(w, l, h), mat(c)); m.position.set(x, y, z); g.add(m); return m; };
  box(0.18, 0.95, 0.26, MAP.track3d, 0.33, 0, 0.13);                      // tracks
  box(0.18, 0.95, 0.26, MAP.track3d, -0.33, 0, 0.13);
  box(0.50, 0.80, 0.30, MAP.body3d, 0, 0, 0.36);                          // body
  box(0.44, 0.62, 0.08, MAP.track3d, 0, -0.04, 0.55);                     // top cover
  box(0.82, 0.36, 0.14, MAP.track3d, 0, 0.60, 0.10);                      // mower deck in front
  box(0.50, 0.06, 0.06, MAP.trim3d, 0, 0.79, 0.20);                       // its front trim
  const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.015, 0.015, 0.38), mat(MAP.body3d));
  mast.rotation.x = Math.PI / 2;
  mast.position.set(0, -0.28, 0.78);
  g.add(mast);
  const disc = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, 0.025, 16), mat(MAP.onmap));
  disc.rotation.x = Math.PI / 2;
  disc.position.set(0, -0.28, 0.98);
  g.add(disc);
  R3D.lamp = new THREE.Mesh(new THREE.SphereGeometry(0.06, 12, 8), new THREE.MeshBasicMaterial({ color: R3D.colour || MAP.robot }));
  R3D.lamp.position.set(0, 0.12, 0.62);
  g.add(R3D.lamp);
  return g;
}

function r3dLayer(THREE) {
  return {
    id: "robot3d", type: "custom", renderingMode: "3d",
    onAdd(map, gl) {
      this.map = map;
      this.camera = new THREE.Camera();
      this.scene = new THREE.Scene();
      this.scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 1.6));
      const sun = new THREE.DirectionalLight(0xffffff, 1.2);
      sun.position.set(-1, -2, 3);
      this.scene.add(sun);
      this.scene.add(r3dBuild(THREE));
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
      const model = new M().makeTranslation(mc.x, mc.y, mc.z).scale(new THREE.Vector3(s, -s, s))
        .multiply(new M().makeRotationZ(-(p.heading || 0) * R3D_DEG))
        .multiply(new M().makeRotationX(nose))
        .multiply(new M().makeRotationY(left));
      this.camera.projectionMatrix = new M().fromArray(matrix).multiply(model);
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
  if (!map.getLayer("robot3d")) map.addLayer(r3dLayer(R3D.THREE));        // on top of everything
  if (!R3D.bound) {
    R3D.bound = true;
    map.on("dragstart", (e) => { if (e.originalEvent && R3D.follow.on) robot3dFollow(false); });   // a pan lets go
    map.on("rotatestart", (e) => { if (e.originalEvent) R3D.follow.turning = true; });
    map.on("zoomend", (e) => { if (e.originalEvent && R3D.follow.on) r3dKick(); });    // a zoom about the cursor: back on the robot
    // Moving the camera stops any gesture under way, so it holds still while a finger or button is on the map (and
    // just after a wheel zoom): a pan then gets through (and lets go), a right-drag sets a new angle.
    const box = map.getCanvasContainer();
    box.addEventListener("pointerdown", () => { R3D.follow.hands = true; });
    box.addEventListener("wheel", () => { R3D.follow.wheelAt = performance.now(); }, { passive: true });
    window.addEventListener("pointerup", () => { if (!R3D.follow.hands) return; R3D.follow.hands = false; if (R3D.follow.on) r3dKick(); });
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
  R3D.fix = { lon: s.lon, lat: s.lat, heading: s.heading ?? (f ? f.heading : 0), pitch: s.pitch || 0, roll: s.roll || 0,
              speed: s.speed || 0, t: s.t ?? null, at: now };
  if (!state.map || !state.map.getLayer("robot3d") || R3D.mode !== "live") return;      // kept for when 3D comes on
  if (!R3D.pose) R3D.pose = { ...R3D.fix };
  r3dKick();
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

