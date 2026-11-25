/* Phase 2 UI: control, pending confirmations, follow-cam, schedule, maintenance, camera polygons (Settings is settings.js).
   Loaded after app.js and uses its helpers (h, fill, kv, card, chip, getJSON, putJSON, state, setSource, toast…).
   Commands only go out through POST /api/control/send or the pending confirmation, both behind the server's
   catalogue, master switch and interlocks. */
"use strict";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
// As app/ops.py: the states in which a pause or a return to dock would do nothing.
const NOT_PAUSABLE = new Set(["paused", "charging_break", "returning", "docking", "docked", "idle"]);
const NOT_DOCKABLE = new Set(["charging_break", "returning", "docking", "docked"]);
const ops = { followTimer: null, pinnedCam: null, confirm: null, editing: null, drawing: null, planId: null, watch: null,
              mode: savedLiveMode(), camAway: false, arm: null, sending: null, mini: null, feed: null };

async function postJSON(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
  const text = await r.text();
  let data = null;
  try { data = JSON.parse(text); } catch { /* not json */ }
  if (!r.ok) throw new Error((data && (data.detail || data.error)) || `${r.status} ${text}`);
  return data;
}

// A view opened straight from its URL can render before the first SSE status arrives.
async function currentStatus() {
  if (!state.status) { try { state.status = await getJSON("/api/status"); } catch { /* the stream will fill it */ } }
  return state.status || {};
}

function checkList(checks) {
  return h("ul", { class: "rules" }, (checks || []).map((c) => h("li", null,
    dot(c.ok === true ? GREEN : c.ok === false ? (c.hard ? CORAL : AMBER) : MUTED),
    h("span", null, h("b", null, c.name), " ", h("span", { class: "muted" }, c.detail),
      c.ok === false && !c.hard ? h("span", { class: "muted" }, " (soft)") : null))));
}

// Why it cannot run (app/why.py): what is wrong, what fixes it, and where. The same words on Live, Schedule and runs.
const WHY_COLOR = { block: CORAL, warn: AMBER, info: MUTED };
function whyList(reasons) {
  return h("ul", { class: "rules whylist" }, (reasons || []).map((r) => h("li", null,
    dot(WHY_COLOR[r.severity] || MUTED),
    h("span", null, h("b", null, r.title), r.detail ? h("span", { class: "muted" }, ` · ${r.detail}`) : null,
      r.fix ? h("span", { class: "whyfix" }, r.fix, r.link ? [" ", h("a", { href: r.link }, "→")] : null) : null))));
}
const whyHead = (reasons) => `${reasons[0].title}${reasons.length > 1 ? ` (+${reasons.length - 1} more)` : ""}`;

// Live: the robot paused itself, or the last run ended without finishing its plan.
function whyCard(w) {
  if (!w || (!w.held && !w.last_run)) return null;
  const items = [w.held, w.last_run].filter(Boolean);
  return h("div", { class: `card${w.held && w.held.severity === "block" ? " attention" : ""}` },
    h("p", { class: "label" }, w.held ? "Why it stopped" : "Last run"),
    whyList(items),
    w.last_run ? h("p", { class: "muted tiny", style: "margin:4px 0 0" },
      h("a", { href: `#runs/${w.last_run.run_id}` }, "Open the run"), ` · ended ${when(w.last_run.ended)}`) : null);
}

/* --------------------------------------------------------------- chips */
const GUARD_NAMES = { person: "Person guard", rain: "Rain guard", away: "Everyone-left guard", dark: "After-dark guard" };
// The top bar's alerts from ops (a run waiting for a yes, guards); also where a command's result is announced.
function opsAlerts(s) {
  const o = s.ops;
  if (!o) return [];
  const lc = o.last_command, w = ops.watch;
  if (w && lc && lc.at === w.at && lc.done) {
    toast(lc.confirmed ? `${w.label}: done, ${lc.confirm}` : lc.confirmed === false ? `${w.label}: sent, but ${lc.confirm}`
          : `${w.label}: sent (${lc.confirm})`);
    ops.watch = null;
  }
  return [
    o.pending ? alertChip("#live", AMBER, h("b", null, "Waiting for you"), ` · ${o.pending.plan}`) : null,
    ...(o.guards || []).filter((g) => g.active || g.acted).map((g) =>
      titled(alertChip("#settings/rules", AMBER, h("b", null, GUARD_NAMES[g.key] || "Guard"),
                       ` · ${g.acted === "pause" ? "paused for" : g.acted === "dock" ? "docked for" : "seeing"} ${g.why}`),
             `${GUARD_NAMES[g.key] || "A guard"}: Settings → Run rules → During a run`)),
  ];
}

// The top bar's switches, which it shows as one group.
function opsSwitches(s) {
  const o = s.ops;
  if (!o) return [];
  return [
    switchChip("control_enabled", "Control", o.control_enabled, "let the panel send commands to the robot"),
    state.view === "studio" ? switchChip("map_write_enabled", "Map writing", o.map_write_enabled, "let Map Studio change the robot's map") : null,
  ];
}

// Grouped with the other connection chips (HaLow, Wi-Fi, RTK) in the top bar: "Cloud" while connected, else its state.
function cloudChip(s) {
  const c = s.ops && s.ops.cloud;
  if (!c) return null;
  const ok = c.state === "connected";
  return titled(chipLink("#settings/robot", ok ? GREEN : c.state === "needs login" ? CORAL : MUTED, connLabel("Cloud", ok ? "" : `: ${c.state}`)),
                `Yarbo cloud: ${c.state}`);
}

// A switch in the top bar: on asks for ENABLE (as in Settings → Safety), off is one click. Logged as in Settings.
function switchChip(key, label, on, what) {
  return h("button", { type: "button", class: `chip switchchip${on ? " on" : ""}`, "aria-pressed": on ? "true" : "false",
                       title: on ? `${label} is on: click to switch it off` : `Switch ${label.toLowerCase()} on…`,
    onclick: async () => {
      if (!on && prompt(`Type ENABLE to ${what}.`) !== "ENABLE") return;
      try { await putJSON(`/api/settings/${key}`, !on); toast(`${label} ${on ? "off" : "on"}.`); }
      catch (e) { toast(e.message); return; }
      try { state.status = await getJSON("/api/status"); renderChips(state.status); } catch { /* the stream catches up */ }
      if (state.view === "settings") renderSettings();
    } },
    h("span", { class: "minitrack", "aria-hidden": "true" }), label, h("span", { class: "swstate" }, on ? " ON" : " off"));
}

/* -------------------------------------------------------- live extras */
// Live, top to bottom: what the robot is doing, a run waiting for a yes, Control, the follow-cam. In the camera
// layout the follow-cam takes the map's slot, the emergency buttons lead the panel and the small map comes next.
// When no camera's coverage holds the robot (and none is pinned), the camera layout puts the map back in the big
// slot until one does; the emergency buttons stay at the top of the panel. On a phone the map layout's panel sits under
// the map, so during a run the emergency buttons lead it there too (Control's own buttons are a screen further down).
const PHONE_LIVE = STACKED;
PHONE_LIVE.addEventListener("change", () => { if (state.view === "live") refreshLive(); });
const runGoing = (s) => !!s.run || (!!s.activity && !["docked", "idle"].includes(s.activity));
async function liveExtras(s) {
  const o = s.ops || {};
  // Live can render before the map's plan list arrives; an empty picker would send plan_id 0.
  if (!state.plansCache.length) { try { state.plansCache = (await getJSON("/api/plans")).plans || []; } catch { /* none yet */ } }
  const [fc, robotCams] = await Promise.all([followCamData(), camerasCard(s), patchRefresh()]);   // cameras.js: the robot's own cameras
  const rest = [whyCard(o.why), o.pending ? pendingCard(o.pending, o.control_enabled) : null, controlCard(s),
                robotCams];
  if (ops.mode === "camera") {
    const away = !fc.pinned && (!fc.cam || fc.covered === false);
    if (away !== ops.camAway) { ops.camAway = away; syncLiveMode(); }
    if (away) return [emergencyCard(s), camAwayCard(fc), statusCard(s), ...rest];
    drawCamStage(fc, s);
    return [emergencyCard(s), miniMapCard(), statusCard(s), ...rest];
  }
  return [PHONE_LIVE.matches && runGoing(s) ? emergencyCard(s) : null, statusCard(s), ...rest, followCamCard(fc)];
}

/* ---------------------------------------------------------- live layout
   Map (the yard map big, the follow-cam a card) or Camera (the follow-cam big, the map a small card in the panel).
   One class on .layout; the map itself (#map) is moved between its slot and the card, so there is one map. The
   choice is per browser. */
function savedLiveMode() { try { return localStorage.getItem("yarbo.liveView") === "camera" ? "camera" : "map"; } catch { return "map"; } }

function setLiveMode(mode) {
  ops.mode = mode === "camera" ? "camera" : "map";
  try { localStorage.setItem("yarbo.liveView", ops.mode); } catch { /* no storage: this page only */ }
  ops.arm = null;
  syncLiveMode();
  refreshLive();
}

function syncLiveMode() {
  // Drawing camera coverage, aligning imagery and placing a patch need the big map, so they show the map layout
  // while they last.
  const live = state.view === "live", cam = live && ops.mode === "camera" && !ops.camAway && !ops.drawing
    && !state.align && !ops.patchPlacing;
  const bar = $("#liveview");
  bar.hidden = !live;
  for (const b of $$("[data-mode]", bar)) {
    if (b.dataset.mode === ops.mode) b.setAttribute("aria-current", "true"); else b.removeAttribute("aria-current");
    b.onclick = () => { if (b.dataset.mode !== ops.mode) setLiveMode(b.dataset.mode); };
  }
  $(".layout").classList.toggle("cam", cam);
  $("#camstage").hidden = !cam;
  if (!cam) { clearCamStage(); }
  const el = MAP_EL, home = cam ? miniSlot() : $(".mapwrap");
  if (el.parentElement !== home) {
    home.prepend(el);
    requestAnimationFrame(() => { if (state.map) { state.map.resize(); fitYard(); } });
  }
  lockMiniMap(cam);
}

// The small map is an overview of the whole yard: it cannot be panned or zoomed away from that (Map layout gives the
// full map). Switched only when the layout changes, so it leaves Map Studio's own settings alone.
const MINI_LOCKED = ["dragPan", "scrollZoom", "doubleClickZoom", "touchZoomRotate", "keyboard"];
function lockMiniMap(on) {
  if (!state.map || ops.miniLocked === on) return;
  ops.miniLocked = on;
  for (const k of MINI_LOCKED) state.map[k][on ? "disable" : "enable"]();
  sync3d(false);                                   // the small map is flat; back in the map layout, 3D comes back
  if (on) fitYard();
}

function miniSlot() { return (ops.mini ||= h("div", { class: "minimap" })); }

// Kept as one element across renders: it holds the live map while the camera layout is on.
function miniMapCard() {
  ops.miniCard ||= h("div", { class: "card" },
    h("div", { class: "row gap", style: "justify-content:space-between" },
      h("p", { class: "label", style: "margin:0" }, "Map"),
      h("button", { type: "button", class: "linkbtn small", onclick: () => setLiveMode("map") }, "Map layout")),
    miniSlot());
  return ops.miniCard;
}

// In the camera layout while no camera sees the robot: why the map is big, and the chips to pin a camera anyway.
function camAwayCard(d) {
  return h("div", { class: "card" },
    h("div", { class: "row gap", style: "justify-content:space-between" },
      h("p", { class: "label", style: "margin:0" }, "Follow-cam"),
      d.yard ? yardLine(d.yard) : null),
    h("p", { class: "muted small", style: "margin:4px 0" }, d.cam
      ? "No camera sees the robot, so the map is shown. The camera comes back when the robot drives into view."
      : ["No yard camera is set up, so the map is shown. Pick yard cameras and draw what each covers in ",
         h("a", { href: "#settings/cameras" }, "Settings → Cameras"), "."]),
    camChips(d));
}

function clearCamStage() {
  const st = $("#camstage");
  if (st && st.childElementCount) st.replaceChildren();
}

// The follow-cam big, with the camera chips top left, the yard check top right and a strip of the robot's state.
// The picture element is kept while the camera stays the same, so a status update does not reload it.
function drawCamStage(d, s) {
  const st = $("#camstage");
  if (!st) return;
  if (!d.cam) {
    stopFeed();
    fill(st, h("div", { class: "camempty" }, h("p", null, "No yard camera sees the robot."),
      h("p", { class: "muted small" }, "Pick yard cameras and draw what each covers in ", h("a", { href: "#settings/cameras" }, "Settings → Cameras"), ".")));
    return;
  }
  const r = s.robot || {}, plan = (s.run && s.run.plan) || {};
  fill(st, camFeed(d.cam, 720),
    h("div", { class: "camtl" }, camChips(d), h("span", { class: "camname" }, d.pinned ? `${shortCam(d.cam)} (pinned)` : `${shortCam(d.cam)} · ${d.reason || "auto"}`)),
    d.yard ? h("div", { class: "camtr" }, yardLine(d.yard)) : null,
    h("div", { class: "cambar" },
      h("span", { class: "state", style: `color:${stateColor(s)}` }, dot(stateColor(s)), s.pause_reason ? `paused: ${s.pause_reason}` : s.state),
      h("span", null, h("b", { class: "mono" }, `${num(r.battery)}%`), " battery"),
      r.rtk ? h("span", null, r.rtk.label) : null,
      plan.progress_pct !== null && plan.progress_pct !== undefined ? h("span", null, `${num(plan.progress_pct)}% of the plan`) : null,
      s.stalled ? h("span", { style: `color:${CORAL}` }, `not moving for ${dur(s.stalled_s)}`) : null,
      s.rtk_wait ? h("span", { style: `color:${AMBER}` }, `waiting for an RTK fix for ${dur(s.stalled_s)}`) : null));
}

/* The emergency block: one big button that pauses a running plan or resumes a paused one (the robot's own
   planning_paused decides which, through its activity), with Stop and Return to dock under it. The same command
   path and checks as the Control card; the confirmation is a second tap within 4 s instead of a dialog. */
// The Yarbo cloud carries commands; the reason when it doesn't.
const routeOk = (o) => (o.link ? !!o.link.commands : !!(o.cloud && o.cloud.state === "connected"));
const noRouteWhy = (o) => [`No route to the robot: the Yarbo cloud is ${(o.cloud || {}).state || "not set up"}. `,
  h("a", { href: "#settings/robot" }, "Settings → Robot"), "."];

function emergencyCard(s) {
  const o = s.ops || {}, on = o.control_enabled;
  const act = s.activity, known = !!act && !s.lost_contact && s.state !== "waiting for data";
  const running = !!s.run || !["docked", "idle"].includes(act);
  const held = HELD.has(act);
  const main = held ? ["resume", "Resume", "▶"] : ["pause", "Pause", "❚❚"];
  const can = {
    pause: !known || !NOT_PAUSABLE.has(act), resume: !known || held,
    stop: !known || running, cmd_recharge: !known || !NOT_DOCKABLE.has(act),
  };
  const off = !on || !routeOk(o);
  const arm = ops.arm && Date.now() < ops.arm.until ? ops.arm : null;
  // Armed: the whole button turns amber, says what the second tap does, and a bar drains over the 4 s. Live redraws on
  // every status event, so the bar starts part-way through (a negative delay) rather than over again.
  const button = (cmd, label, icon, cls) => {
    const armed = arm && arm.cmd === cmd, busy = ops.sending === cmd;
    return h("button", { type: "button", class: `btn ${cls}${armed ? " armed" : ""}`, "data-cmd": cmd,
      "data-key": `emerg-${cmd}`,
      "data-nofocus": armed ? "" : null,   // armed: a redraw must not leave focus on it (Space or Enter would send)
      disabled: off || !can[cmd] || !!ops.sending || null, "aria-live": "polite",
      onclick: () => emergencyTap(cmd, label) },
      icon ? h("span", { class: "eicon", "aria-hidden": "true" }, icon) : null,
      busy ? `${label}…` : armed ? h("span", { class: "armtext" }, h("b", null, `Tap again to ${label.toLowerCase()}`),
        h("small", null, "or wait to cancel")) : label,
      armed ? h("span", { class: "armbar", "aria-hidden": "true", style: `animation-delay:-${4000 - (arm.until - Date.now())}ms` }) : null);
  };
  const armedLabel = arm && { pause: "Pause", resume: "Resume", stop: "Stop", cmd_recharge: "Return to dock" }[arm.cmd];
  const why = !on ? ["Control is off. ", h("a", { href: "#settings/safety" }, "Switch it on"), " to use these."]
            : !routeOk(o) ? noRouteWhy(o)
            : known && !can[main[0]] ? `Nothing to ${main[1].toLowerCase()}: the robot is ${s.state}.` : null;
  return h("div", { class: "card ecard" },
    button(main[0], main[1], main[2], held ? "pri huge" : "danger huge"),
    h("div", { class: "ehalf" }, button("stop", "Stop", "■", ""), button("cmd_recharge", "Return to dock", "⌂", "")),
    armedLabel ? h("p", { class: "armhint", role: "status" }, `Armed: tap “${armedLabel}” again within 4 seconds to send it.`)
      : h("p", { class: "muted small", style: "margin:8px 0 0" }, why || "These need two taps: the first arms the button (it turns amber), the second sends."));
}

// The second tap must come at least this long after the first: a bounce, a held key or a habitual double-click (500 ms
// is the usual system double-click time) is not a decision.
const ARM_MIN_GAP_MS = 600;
async function emergencyTap(cmd, label) {
  // Never leave a two-tap button focused, on any tap (an ignored one too): Space or Enter on it would be the next tap.
  const f = document.activeElement;
  if (f && f.closest && f.closest(".ecard")) f.blur();
  const a = ops.arm;
  // A bounce or key repeat, not a second tap: ignore it, and the button stays armed.
  if (a && a.cmd === cmd && Date.now() - a.at < ARM_MIN_GAP_MS) return;
  if (!a || a.cmd !== cmd || Date.now() > a.until) {
    ops.arm = { cmd, at: Date.now(), until: Date.now() + 4000 };
    refreshLive();
    setTimeout(() => { if (ops.arm && ops.arm.cmd === cmd && Date.now() >= ops.arm.until) { ops.arm = null; refreshLive(); } }, 4100);
    return;
  }
  ops.arm = null;
  ops.sending = cmd;
  refreshLive();
  try { reportSend(await postJSON("/api/control/send", { cmd, args: {}, confirm: true, override: false }), label); }
  catch (e) { toast(e.message); }
  ops.sending = null;
  refreshLive();
}

// What a send came back with; a result still on its way arrives on the stream (opsAlerts).
function reportSend(r, label) {
  if (r.sent && r.confirmed === null && r.at) ops.watch = { at: r.at, label };
  toast(!r.sent ? `${label}: not sent: ${r.refused || r.error}`
        : r.confirmed ? `${label}: done, ${r.confirm}`
        : r.confirmed === false ? `${label}: sent, but ${r.confirm}` : `${label}: sent, checking that the robot follows…`);
}

function planAreas(p) {
  const names = {};
  for (const f of (state.mapData && state.mapData.geojson.features) || []) {
    if (f.properties.kind === "areas") names[f.properties.id] = f.properties.name;
  }
  return (p.areaIds || []).map((id) => names[id] || `area ${id}`).join(", ");
}

/* ---------------------------------------------------------- readiness
   What a start of a plan would face right now (POST /api/control/preview: reads only, nothing is sent). One block,
   shown on Live (in Control, folded) and on Schedule; the rules themselves are edited in Settings → Run rules.
   Checked at most every 30 s per plan; every block on screen for that plan is redrawn when the answer comes. */
const ready = { cache: new Map(), busy: new Set(), open: false };

function readinessView(planId, full = false) {
  const key = String(planId), c = ready.cache.get(key);
  const el = h("div", { class: "readiness", "data-plan": key, "data-full": full ? "1" : "" });
  if (c) drawReadiness(el, c, full, planId);
  else fill(el, h("p", { class: "muted small", style: "margin:0" }, "Checking the start rules…"));
  if (!c || Date.now() - c.at > 30000) refreshReadiness(planId);
  return el;
}

async function refreshReadiness(planId) {
  const key = String(planId);
  if (ready.busy.has(key)) return;
  ready.busy.add(key);
  let c;
  try { c = { at: Date.now(), r: await postJSON("/api/control/preview", { cmd: "start_plan", args: { plan_id: planId } }) }; }
  catch (e) { c = { at: Date.now(), error: e.message }; }
  ready.busy.delete(key);
  ready.cache.set(key, c);
  for (const el of $$(`.readiness[data-plan="${key}"]`)) drawReadiness(el, c, el.dataset.full === "1", planId);
}

function drawReadiness(el, c, full, planId) {
  const again = h("button", { type: "button", class: "linkbtn", style: "font-size:inherit;min-height:0", onclick: () => refreshReadiness(planId) }, "check again");
  if (c.error) {
    fill(el, h("p", { class: "small", style: `color:${AMBER};margin:0` }, `Could not check the start rules: ${c.error} · `, again));
    return;
  }
  const r = c.r, hard = r.checks.some((x) => x.ok === false && x.hard);
  // The reasons beyond "control is off" (that one is said on its own when it is the only one).
  const reasons = (r.why || []).filter((x) => x.code !== "control_off");
  const [color, text] = r.would_send ? [GREEN, "Every start check passes"]
    : reasons.length ? [reasons.some((x) => x.severity === "block") ? CORAL : AMBER, `Can't start: ${whyHead(reasons)}`]
    : !r.enabled && r.reason === "ok" ? [MUTED, "Every start check passes (control is off, so nothing would be sent)"]
    : [hard ? CORAL : AMBER, `A start would be refused: ${r.reason}`];
  const head = h("span", { class: "readyhead small" }, dot(color), h("span", null, text));
  const foot = h("p", { class: "muted tiny", style: "margin:2px 0 0" }, `Checked at ${clock(c.at / 1000)} · `, again, " · ",
    h("a", { href: "#settings/rules" }, "the rules"));
  const why = (r.why || []).length && !r.would_send ? whyList(r.why) : null;
  const checks = h("details", { class: "allchecks" },
    h("summary", { class: "muted small", "data-key": "ready-checks" }, "Every check"), checkList(r.checks));
  if (full) fill(el, h("p", { style: "margin:0" }, head), why, why ? checks : checkList(r.checks), foot);
  else fill(el, h("details", { open: ready.open || null, ontoggle: (e) => { ready.open = e.target.open; } },
    h("summary", { "data-key": "ready-summary" }, head), why, why ? checks : checkList(r.checks), foot));
}

function pendingCard(p, controlOn) {
  return h("div", { class: "card attention" },
    h("p", { class: "label" }, "Ready to start"),
    h("div", { class: "big", style: "font-size:22px" }, p.plan),
    h("p", { class: "muted small" }, `Window ${p.window} · raised ${clock(p.created)} · expires 30 min after`),
    p.camera ? h("img", { class: "snap", alt: `Current view from ${p.camera}`, src: `/api/frigate/snapshot/${encodeURIComponent(p.camera)}.jpg?height=360&t=${Date.now()}` }) : null,
    checkList(p.checks),
    controlOn ? null : h("p", { class: "muted small" }, "Control is switched off, so confirming is logged but does not start the robot."),
    h("div", { class: "row gap" },
      h("button", { type: "button", class: "btn pri", "data-key": "pending-start", onclick: async (e) => {
        e.target.disabled = true;
        try {
          const r = await postJSON("/api/schedule/pending/confirm");
          toast(r.sent ? `Started ${p.plan}.` : `Not started: ${r.refused || r.error || r.msg}`);
        } catch (err) { toast(err.message); }
        refreshLive();
      } }, "Start it"),
      h("button", { type: "button", class: "btn", "data-key": "pending-dismiss",
        onclick: async () => { await postJSON("/api/schedule/pending/dismiss"); refreshLive(); } }, "Not now")));
}

// Which camera the follow-cam shows (pinned, or the one whose coverage holds the robot), the yard cameras, the yard check.
async function followCamData() {
  let fc = null, cams = [];
  try { fc = await getJSON("/api/followcam"); } catch { /* frigate off */ }
  try { cams = (await getJSON("/api/frigate/cameras")).yard || []; } catch { /* ignore */ }
  return { cam: ops.pinnedCam || (fc && fc.camera) || null, pinned: !!ops.pinnedCam, reason: fc && fc.reason,
           covered: fc ? fc.covered : null, cams, yard: fc && fc.yard };
}

const yardLine = (yard) => h("span", { class: "small", style: `color:${yard.ok === false ? CORAL : yard.ok ? GREEN : MUTED}` },
  yard.ok === false ? `Yard not clear: ${yard.detail}` : yard.ok ? "Yard clear" : yard.detail);

const camChips = (d) => (d.cams.length > 1 ? h("div", { class: "camchips" },
  h("button", { type: "button", class: `btn slim${ops.pinnedCam ? "" : " on"}`, "data-key": "yardcam-auto",
                onclick: () => { ops.pinnedCam = null; refreshLive(); } }, "Auto"),
  d.cams.map((c) => h("button", { type: "button", class: `btn slim${ops.pinnedCam === c ? " on" : ""}`,
                                 "data-key": `yardcam-cam-${c}`,
                                 onclick: () => { ops.pinnedCam = c; refreshLive(); } }, c.replace(/_frigate$/, "")))) : null);

function followCamCard(d) {
  return h("div", { class: "card" },
    h("div", { class: "row gap", style: "justify-content:space-between" },
      h("p", { class: "label", style: "margin:0" }, "Follow-cam"),
      d.yard ? yardLine(d.yard) : null),
    d.cam ? camFeed(d.cam, 360, "snap")
          : h("p", { class: "muted small" }, "Pick yard cameras in ", h("a", { href: "#settings/cameras" }, "Settings"), " to see the robot here."),
    d.cam ? h("p", { class: "muted small" }, d.pinned ? `${d.cam} (pinned)` : `${d.cam} · ${d.reason}`) : null,
    d.cam ? h("p", { class: "small", style: "margin:4px 0 0" }, h("button", { type: "button", class: "linkbtn", onclick: () => setLiveMode("camera") }, "Camera layout"),
              h("span", { class: "muted" }, ": the picture big, with pause and stop beside it")) : null,
    camChips(d));
}

/* The follow-cam picture: Frigate's live MJPEG through the panel (/api/frigate/stream), one <img> kept across the
   renders Live does on every status event so the stream is not restarted. It runs only while it is on screen (Live,
   tab visible); if the stream fails it falls back to a snapshot every 3 s and tries the stream again every 30 s.
   An MJPEG that ends quietly (Frigate restarted) just freezes, so the stream is also reopened every 5 minutes. */
const FEED_RENEW_MS = 5 * 60000;
const FEED_FPS = 5;  // Frigate sends high-quality JPEGs: 720p at 5 fps is ~10 Mbit/s
function camFeed(cam, height, cls) {
  const key = `${cam}|${height}`;
  if (ops.feed && ops.feed.key !== key) stopFeed();
  if (!ops.feed) {
    const img = h("img", { id: "followimg", alt: `Live view from ${cam}` });
    ops.feed = { key, cam, height, img, mode: null, failedAt: 0 };
    img.onerror = () => {
      const f = ops.feed;
      if (f && f.img === img && f.mode === "stream") { f.mode = "snap"; f.failedAt = Date.now(); feedTick(); }
    };
    playFeed();
  } else if (ops.feed.mode !== "stream") {
    setTimeout(feedTick, 0);  // back on Live: resume once the picture is on the page
  }
  ops.feed.img.className = cls || "";
  return ops.feed.img;
}

function playFeed() {
  const f = ops.feed;
  if (!f) return;
  const c = encodeURIComponent(f.cam);
  if (f.mode !== "snap") {
    f.mode = "stream";
    f.startedAt = Date.now();
    f.img.src = `/api/frigate/stream/${c}.mjpeg?height=${f.height}&fps=${FEED_FPS}&t=${Date.now()}`;
  } else {
    f.img.src = `/api/frigate/snapshot/${c}.jpg?height=${f.height}&t=${Date.now()}`;
  }
}

// Ends the stream's connection (an <img> keeps loading it even off screen).
function pauseFeed() {
  const f = ops.feed;
  if (f && (f.mode === "stream" || f.mode === "snap")) {
    f.img.removeAttribute("src");
    f.mode = f.mode === "snap" ? "snap-paused" : null;
  }
}

function stopFeed() {
  if (!ops.feed) return;
  pauseFeed();
  ops.feed.img.onerror = null;
  ops.feed = null;
}

function feedTick() {
  const f = ops.feed;
  if (!f) return;
  const onScreen = f.img.isConnected && state.view === "live" && !document.hidden;
  if (!onScreen) { pauseFeed(); return; }
  if (f.mode === "snap-paused") f.mode = "snap";
  if (f.mode === "snap" && Date.now() - f.failedAt > 30000) f.mode = null;  // try the stream again
  if (f.mode !== "stream" || Date.now() - f.startedAt > FEED_RENEW_MS) playFeed();
}

function startFollowTimer() {
  clearInterval(ops.followTimer);
  ops.followTimer = setInterval(feedTick, 3000);
  document.addEventListener("visibilitychange", feedTick);
}

function controlCard(s) {
  const o = s.ops || {};
  const on = o.control_enabled;
  const patchPlan = patchUI.view?.patch?.plan_id;
  const plans = (state.plansCache || []).filter((p) => p.id !== patchPlan);     // the patch has its own buttons
  // Live re-renders on every status event, so the picked plan lives in ops.planId, not in the <select>.
  if (!plans.some((p) => p.id === ops.planId)) ops.planId = plans[0]?.id ?? null;
  const plan = plans.find((p) => p.id === ops.planId);
  // The plan is read once, at the click, for both the confirmation's name and what is sent. This draw's `plan` can be
  // a moment old: after another plan is picked, the redraw waits on the cameras, and a click in between used to open
  // "Start <the old plan>" while sending the new one.
  const startPicked = () => {
    const p = (state.plansCache || []).find((x) => x.id === ops.planId);
    openConfirm("start_plan", p ? `Start ${p.name}` : "Start plan", { plan_id: ops.planId });
  };
  const disabled = !on || !routeOk(o);
  const why = !on ? ["Control is switched off. ", h("a", { href: "#settings/safety" }, "Switch it on in Settings"), "."]
            : !routeOk(o) ? noRouteWhy(o) : null;
  // Only what makes sense in the robot's state is offered; when the state is unknown, everything is (the server's
  // interlocks still decide).
  const act = s.activity, known = !!act && !s.lost_contact && s.state !== "waiting for data";
  const running = !!s.run || !["docked", "idle"].includes(act);
  const no = (ok, text) => (known && !ok ? text : null);
  const idle = {
    start_plan: no(!running, "A run is already going."),
    pause: no(!NOT_PAUSABLE.has(act), `Nothing to pause: the robot is ${s.state}.`),
    resume: no(HELD.has(act), `Nothing to resume: the robot is ${s.state}.`),
    stop: no(running, `Nothing to stop: the robot is ${s.state}.`),
    cmd_recharge: no(!NOT_DOCKABLE.has(act), act === "docked" ? "Already on the dock." : `Already on its way: ${s.state}.`),
  };
  // A touch screen never shows a disabled button's title, so the same reasons go in one line under the grid
  // (.whyline). The button's name replaces "Nothing to pause:", so buttons off for the same state share one entry
  // ("Pause, Stop: the robot is docked."). Control off and no plans already say so above the buttons.
  const labels = { start_plan: "Start", pause: "Pause", resume: "Resume", stop: "Stop",
    cmd_recharge: "Return to dock" };
  const offWhy = new Map();
  for (const [cmd, text] of Object.entries(idle)) {
    const t = text?.replace(/^Nothing to \w+: /, "");
    if (t) offWhy.set(t, [...(offWhy.get(t) || []), labels[cmd]]);
  }
  const whyLine = [...offWhy].map(([t, names]) => `${names.join(", ")}: ${t[0].toLowerCase()}${t.slice(1)}`).join(" ");
  const btn = (cmd, label, args) => h("button", { type: "button", class: "btn", "data-key": `ctl-${cmd}`,
    disabled: disabled || !!idle[cmd] || null,
    title: idle[cmd] || null, onclick: () => openConfirm(cmd, label, args) }, label);
  return h("div", { class: "card" },
    h("p", { class: "label" }, "Control"),
    why ? h("p", { class: "muted small" }, why) : null,
    plans.length ? [
      h("label", { for: "ctlplan", class: "muted small" }, "Plan"),
      h("select", { id: "ctlplan", class: "field", "data-key": "ctl-plan",
        onchange: (e) => { ops.planId = Number(e.target.value); closeConfirm(); refreshLive(); } },
        plans.map((p) => h("option", { value: p.id, selected: p.id === ops.planId || null }, p.name))),
      h("p", { class: "muted small", style: "margin:-4px 0 8px" }, `Mows ${planAreas(plan) || "no areas"}`),
      readinessView(plan.id)]
      : h("p", { class: "muted small" }, "No plans on the robot. Make them in the Yarbo app; they appear here (",
          h("a", { href: "#help/not-yet" }, "the panel cannot make plans yet"), ")."),
    h("div", { class: "ctlgrid" },
      h("button", { type: "button", class: "btn pri wide", "data-key": "ctl-start_plan",
        disabled: disabled || !plan || !!idle.start_plan || null, title: idle.start_plan || null,
        onclick: startPicked }, plan ? `Start ${plan.name}…` : "Start plan…"),
      btn("pause", "Pause"), btn("resume", "Resume"), btn("stop", "Stop"), btn("cmd_recharge", "Return to dock")),
    whyLine ? h("p", { class: "whyline muted tiny", style: "margin-top:6px" }, whyLine) : null,
    patchBlock(disabled, idle.start_plan));
}

/* A command's confirmation is a modal over the page (it used to open under the buttons, below the fold on a phone or
   in the camera layout). It lives outside Live, so the redraw on every status event leaves it alone; Esc, Cancel or a
   tap outside closes it. Its head says whether the command would go; Send is offered only when it would (soft
   checks: once the override is ticked), so a refused start is never a button that seems to do nothing. */
// A run that ended one of these ways says nothing about where to pick the plan up: it finished, or the panel stopped
// watching before the robot did.
const NO_LEFT_OFF = new Set(["plan finished", "recorder restarted", "paused, not resumed"]);

// How far the newest run got, when it was this plan and ended before the plan finished: {pct, ended}, else null. pct
// is the robot's last progress reading (area done of the plan's area, asked once a minute), rounded down so a start
// there overlaps rather than skips. Not offered after a run this panel itself started part-way (the control log,
// newest first): what the robot reports as progress after such a start is not known yet.
function leftOffAt(run, planId, log) {
  if (!run || !run.ended || NO_LEFT_OFF.has(run.end_reason)) return null;
  if (!String(run.plan_session || "").startsWith(`${planId}:`)) return null;
  if (typeof run.plan_progress_pct !== "number") return null;
  const startPct = (l) => { try { return JSON.parse(l.args).start_percent; } catch { return null; } };
  // The robot can take a few minutes to act on a start, and the run opens when it does.
  const start = (log || []).find((l) => l.cmd === "start_plan" && l.sent && l.ts <= run.started + 60
                                        && l.ts >= run.started - 600);
  if (start && startPct(start)) return null;
  const pct = Math.floor(run.plan_progress_pct);
  return pct >= 1 && pct <= 99 ? { pct, ended: run.ended } : null;
}

async function lastRunLeftOff(planId) {
  try {
    const [runs, log] = await Promise.all([getJSON("/api/runs?limit=1"), getJSON("/api/control/log?limit=50")]);
    return leftOffAt((runs.runs || [])[0], planId, log.log);
  } catch { return null; }    // no hint; the list still has every 5 %
}

// The start points on offer: the beginning, every 5 %, where the last run got to, and the one picked.
function startPoints(leftOff, picked) {
  const every5 = Array.from({ length: 19 }, (_, i) => (i + 1) * 5);
  return [...new Set([0, ...every5, leftOff, picked])].filter((v) => typeof v === "number").sort((x, y) => x - y);
}

// One place makes both what the confirmation says and what Send sends, so the two cannot differ. A start from the
// beginning carries no start point at all, as before.
function startChoice(base, planId, pct) {
  return { label: pct ? `${base} from ${pct}%` : base,
           args: pct ? { plan_id: planId, start_percent: pct } : { plan_id: planId } };
}

// Another start point picked in the confirmation: the checks are asked again for exactly what Send would now send, and
// Send is off until they are back. An override ticked for the earlier choice does not carry over.
function pickStartPoint(c, pct) {
  if (c.sending) return undefined;      // Send was pressed: what is on its way cannot be changed any more
  c.startAt.pct = pct;
  ({ label: c.label, args: c.args } = startChoice(c.startAt.base, c.startAt.planId, pct));
  c.preview = null; c.error = null; c.override = false;
  drawConfirm();
  return previewConfirm(c);
}

// What the command would face (reads only). An answer for args that were replaced meanwhile is dropped: the checks on
// screen must be the ones for what Send sends.
async function previewConfirm(c) {
  const args = c.args;
  let preview = null, error = null;
  try { preview = await (c.own ? c.own.preview() : postJSON("/api/control/preview", { cmd: c.cmd, args })); }
  catch (e) { error = e.message; }
  if (ops.confirm !== c || c.args !== args) return;
  c.preview = preview; c.error = error;
  drawConfirm();
}

// own: {preview(), send(override)} for a start that has its own routes (the patch's), else
// /api/control/*. Such a start always runs from the beginning: no start point is offered.
async function openConfirm(cmd, label, args = {}, previewOnly = false, own = null) {
  closeConfirm();
  const opener = document.activeElement;
  const box = h("div", { class: "imgdialogbox confirmbox", role: "dialog", "aria-modal": "true", "aria-labelledby": "confirm-title" });
  const overlay = h("div", { class: "imgdialog", onclick: (e) => { if (e.target === overlay) closeConfirm(); } });
  const onKey = (e) => { if (e.key === "Escape") closeConfirm(); };
  // A start a person is about to send can begin part-way through the plan: picked here, for this one start.
  const startAt = cmd === "start_plan" && !previewOnly && !own
    ? { base: label, planId: args.plan_id, pct: 0, leftOff: null } : null;
  const c = ops.confirm = { cmd, label, args, previewOnly, preview: null, override: false, box, startAt, own,
    close: () => {
      overlay.remove();
      document.removeEventListener("keydown", onKey);
      $(".shell")?.removeAttribute("inert");
      if (opener && opener.isConnected && opener.focus) opener.focus();
    } };
  overlay.append(box);
  $(".shell")?.setAttribute("inert", "");
  document.body.append(overlay);
  document.addEventListener("keydown", onKey);
  drawConfirm();
  if (startAt) {
    lastRunLeftOff(startAt.planId).then((v) => {
      if (v && ops.confirm === c) { startAt.leftOff = v; drawConfirm(); }
    });
  }
  await previewConfirm(c);
}

function closeConfirm() {
  const c = ops.confirm;
  ops.confirm = null;
  if (c) c.close();
}

function drawConfirm() {
  const c = ops.confirm, p = c.preview;
  const softOnly = p && !p.would_send && p.checks.every((x) => x.hard ? x.ok === true : true);
  const canSend = p && p.enabled && (p.would_send || (softOnly && c.override && c.cmd === "start_plan"));
  const head = !p ? null
    : p.would_send ? h("p", { class: "small", style: `color:${GREEN};margin:0 0 6px` }, "Every check passes.")
    : softOnly && !c.previewOnly && c.cmd === "start_plan" ? h("p", { class: "small", style: `color:${AMBER};margin:0 0 6px` },
        `Only soft checks fail (${p.reason}). Tick the override below to start anyway.`)
    : h("p", { class: "small", style: `color:${CORAL};margin:0 0 6px` }, h("b", null, "Can't send: "), p.reason);
  fill(c.box,
    h("h3", { id: "confirm-title" }, c.previewOnly ? `Gate check · ${c.label}` : `Send “${c.label}”?`),
    c.error ? h("p", { class: "small", style: `color:${CORAL}` }, c.error) : null,
    c.startAt ? startAtRow(c) : null,
    head,
    p ? checkList(p.checks) : h("p", { class: "muted small" }, "Checking interlocks…"),
    p && !p.enabled ? h("p", { class: "muted small" }, "Control is switched off; nothing will be sent.") : null,
    !c.previewOnly && softOnly && c.cmd === "start_plan" ? h("label", { class: "row gap small" },
      h("input", { type: "checkbox", checked: c.override || null, onchange: (e) => { c.override = e.target.checked; drawConfirm(); } }),
      "Override the soft checks above") : null,
    h("div", { class: "row gap", style: "justify-content:flex-end;margin-top:12px" },
      h("button", { type: "button", class: "btn", onclick: closeConfirm }, c.previewOnly ? "Close" : "Cancel"),
      // c.sending: once Send is pressed nothing in the dialog can send again or change what is sent. The redraw
      // (a start point's checks coming back, the override box) would otherwise rebuild Send as enabled while the
      // first request is still on its way, and a second click would publish a second command.
      c.previewOnly ? null : h("button", { type: "button", class: "btn pri", disabled: !canSend || c.sending || null,
        onclick: async () => {
        c.sending = true;
        drawConfirm();
        try {
          reportSend(await (c.own ? c.own.send(c.override)
            : postJSON("/api/control/send", { cmd: c.cmd, args: c.args, confirm: true, override: c.override })),
          c.label);
        }
        catch (err) { toast(err.message); }
        if (ops.confirm === c) closeConfirm();
        refreshLive();
      } }, "Send")));
  if (!c.box.contains(document.activeElement)) c.box.querySelector("button:not([disabled])")?.focus();  // a redraw drops focus
}

// The confirmation's "Start at" list. It lives in the dialog, not on Live: Live redraws on every status event, which
// would shut an open list, and here the choice is in view when Send is pressed.
function startAtRow(c) {
  const s = c.startAt, left = s.leftOff;
  const day = left ? new Date(left.ended * 1000).toLocaleDateString([], { month: "short", day: "numeric" }) : "";
  const text = (v) => (v === 0 ? "The beginning" : left && v === left.pct ? `${v}% (last run, ${day})` : `${v}%`);
  return h("label", { class: "row gap small", style: "margin:0 0 8px",
    title: "Start part-way through the plan, to finish a mow that was stopped." },
    h("span", { class: "muted", style: "white-space:nowrap" }, "Start at"),
    h("select", { class: "field slim", style: "flex:1;min-width:0", "data-key": "confirm-from",
      disabled: c.sending || null,
      onchange: (e) => pickStartPoint(c, Number(e.target.value)) },
      startPoints(left && left.pct, s.pct).map((v) =>
        h("option", { value: v, selected: v === s.pct || null }, text(v)))));
}

function refreshLive() {
  if (state.status && state.view === "live") renderLive(state.status);
}

/* ------------------------------------------------------------- schedule */
async function renderSchedule() {
  const panel = $("#p-schedule");
  let d;
  try { d = await getJSON("/api/schedule"); } catch (e) { fill(panel, h("p", { class: "muted pad" }, e.message)); return; }
  await currentStatus();
  state.plansCache = d.plans;
  const planName = (id) => (d.plans.find((p) => p.id === id) || {}).name || (id === null ? "no plan" : `plan ${id}`);
  // The start checks for the plan the next window would run (or the robot's first plan).
  const checkPlan = (d.windows.find((w) => w.enabled && w.plan_id !== null) || {}).plan_id ?? d.plans[0]?.id ?? null;
  const save = async (windows) => {
    try { await putJSON("/api/schedule/windows", windows); toast("Schedule saved."); } catch (e) { toast(e.message); }
    ops.editing = null;
    renderSchedule();
  };
  const upcoming = d.upcoming.map((u) => {
    const w = u.window, ev = u.last_eval;
    return h("div", { class: "card" },
      h("div", { class: "row gap", style: "justify-content:space-between;flex-wrap:wrap" },
        h("b", null, w.name || planName(w.plan_id)),
        h("span", { class: "muted small" }, u.active ? "window open now" : u.next ? `next ${when(u.next)}` : "no upcoming window")),
      kv("Plan", planName(w.plan_id)),
      kv("When", `${w.days.map((x) => DAYS[x]).join(", ") || "no days"} · ${w.start}–${w.end}`),
      kv("Head", w.head), kv("Start", w.auto_start ? "automatically (if control is on)" : "ask me first"),
      u.today ? kv("Today", u.today) : null,
      ev ? h("div", null,
        h("p", { class: "muted small" }, `Checked at ${clock(ev.at)}: ${ev.ok ? "every check passes" : (ev.why || []).length ? "it won't start yet, because" : ev.reason}`),
        ev.ok || !(ev.why || []).length ? checkList(ev.checks) : whyList(ev.why)) : null,
      h("div", { class: "row gap" },
        h("button", { type: "button", class: "btn slim", onclick: () => { ops.editing = { ...w }; renderSchedule(); } }, "Edit"),
        h("button", { type: "button", class: "btn slim", onclick: () => save(d.windows.map((x) => (x.id === w.id ? { ...x, enabled: !x.enabled } : x))) }, w.enabled ? "Disable" : "Enable"),
        h("button", { type: "button", class: "btn slim", onclick: () => { if (confirm(`Delete this window?`)) save(d.windows.filter((x) => x.id !== w.id)); } }, "Delete")));
  });
  fill(panel,
    h("div", { class: "healthbar" },
      h("p", { class: "muted small", style: "margin:0" }, "The panel owns scheduling. Inside a window it checks the gates every minute and asks you to confirm once they all pass."),
      h("button", { type: "button", class: "btn pri", onclick: () => { ops.editing = { days: [1, 4], start: "09:00", end: "14:00", head: "mower", plan_id: d.plans[0]?.id ?? null, enabled: true, auto_start: false, name: "" }; renderSchedule(); } }, "Add window")),
    d.pending ? h("div", { class: "pad0" }, pendingCard(d.pending, state.status?.ops?.control_enabled)) : null,
    robotSchedulesCard(d.robot_schedules, planName),
    ops.editing ? windowEditor(ops.editing, d, save) : null,
    h("div", { class: "healthgrid" },
      h("div", { class: "card" },
        h("p", { class: "label" }, checkPlan === null ? "Right now" : `Right now · ${planName(checkPlan)}`),
        readinessView(checkPlan, true)),
      upcoming.length ? upcoming : h("div", { class: "card" }, h("p", { class: "muted" }, "No windows yet."))),
    h("div", { class: "card", style: "margin:12px" }, h("p", { class: "label" }, "History"),
      d.log.length ? d.log.map((l) => h("div", null,
        kv(`${when(l.ts)} · ${l.outcome}`, `${planName((d.windows.find((w) => w.id === l.window_id) || {}).plan_id)}${(l.detail.why || []).length ? "" : ` ${l.detail.reason || l.detail.result || ""}`}`),
        (l.detail.why || []).length ? whyList(l.detail.why) : null))
                   : h("p", { class: "muted small" }, "Nothing yet.")));
}

// Schedules saved in the Yarbo app live on the robot and start mows without any of the panel's run rules (#64). The
// panel never writes them; it says they are there.
function robotSchedulesCard(list, planName) {
  if (!list || !list.length) return null;
  return h("div", { class: "card", style: "margin:12px" },
    h("p", { class: "label" }, h("span", { style: `color:${AMBER}` }, "On the robot itself")),
    h("p", { class: "small", style: "margin:0 0 6px" },
      `${list.length === 1 ? "A schedule was" : `${list.length} schedules were`} saved in the Yarbo app. They start mows without the run rules (dry lawn, yard clear, someone home, daylight). Remove them in the app to let the panel decide.`),
    list.map((s) => kv(s.name || `Schedule ${s.id}`,
      `${s.enabled ? "on" : "off"} · ${planName(s.plan_id)} · ${s.days.join(", ") || "no days"} · ${s.start_utc || "?"}–${s.end_utc || "?"} UTC`)));
}

function windowEditor(w, d, save) {
  const field = (label, input) => h("label", { class: "formrow" }, h("span", { class: "muted small" }, label), input);
  const controlOn = state.status?.ops?.control_enabled;
  return h("div", { class: "card", style: "margin:12px" },
    h("p", { class: "label" }, w.id ? "Edit window" : "New window"),
    h("div", { class: "formgrid" },
      field("Name (optional)", h("input", { class: "field", value: w.name || "", oninput: (e) => { w.name = e.target.value; } })),
      field("Plan", h("select", { class: "field", onchange: (e) => { w.plan_id = Number(e.target.value); } },
        d.plans.map((p) => h("option", { value: p.id, selected: p.id === w.plan_id }, p.name)))),
      field("From", h("input", { class: "field", type: "time", value: w.start, oninput: (e) => { w.start = e.target.value; } })),
      field("To", h("input", { class: "field", type: "time", value: w.end, oninput: (e) => { w.end = e.target.value; } })),
      field("Head", h("select", { class: "field", onchange: (e) => { w.head = e.target.value; } },
        ["mower", "snow", "any"].map((x) => h("option", { value: x, selected: x === w.head }, x))))),
    h("fieldset", { class: "days" }, h("legend", { class: "muted small" }, "Days"),
      DAYS.map((name, i) => h("label", null, h("input", { type: "checkbox", checked: w.days.includes(i) || null,
        onchange: (e) => { w.days = e.target.checked ? [...new Set([...w.days, i])] : w.days.filter((x) => x !== i); } }), name))),
    h("label", { class: "row gap small" }, h("input", { type: "checkbox", checked: w.auto_start || null, onchange: (e) => { w.auto_start = e.target.checked; } }),
      "Start automatically when every gate passes", controlOn ? null : h("span", { class: "muted" }, " (only works once control is on)")),
    h("div", { class: "row gap" },
      h("button", { type: "button", class: "btn pri", onclick: () => {
        // The editor holds a copy; replace (or add) it in the saved list.
        getJSON("/api/schedule").then((cur) => save(w.id ? cur.windows.map((x) => (x.id === w.id ? w : x)) : [...cur.windows, w]));
      } }, "Save"),
      h("button", { type: "button", class: "btn", onclick: () => { ops.editing = null; renderSchedule(); } }, "Cancel")));
}

/* ---------------------------------------------------------- maintenance */
async function renderMaintenance() {
  const panel = $("#p-maintenance");
  let d;
  try { d = await getJSON("/api/maintenance"); } catch (e) { fill(panel, h("p", { class: "muted pad" }, e.message)); return; }
  const saveItems = async (items) => {
    try { await putJSON("/api/maintenance/items", items); } catch (e) { toast(e.message); }
    renderMaintenance();
  };
  const raw = d.items.map(({ id, key, name, unit, interval, done_at, baseline, note }) => ({ id, key, name, unit, interval, done_at, baseline, note }));
  const color = (due) => (due === "overdue" || due === "due" ? CORAL : due === "due soon" ? AMBER : due === "ok" ? GREEN : MUTED);
  const UNIT = { blade_hours: "h", distance_km: "km", mows: "runs", runs: "runs", charge_cycles: "charges", days: "days", head_change: "" };
  const count = (it, v) => `${num(v, ["mows", "runs", "charge_cycles", "head_change"].includes(it.unit) ? 0 : 1)}${UNIT[it.unit] ? ` ${UNIT[it.unit]}` : ""}`;
  fill(panel,
    h("div", { class: "healthbar" },
      h("p", { class: "muted small", style: "margin:0" }, "Counters come from recorded runs. Intervals start at Yarbo's own advice where it gives one; “Done” restarts the count."),
      h("span", { class: "muted small" }, `blade ${num(d.counters.blade_hours, 1)} h · ${num(d.counters.distance_km, 1)} km · ${d.counters.runs} runs · ${d.counters.charge_cycles} charges`)),
    h("div", { class: "healthgrid" }, d.items.map((it) => h("div", { class: "card" },
      h("div", { class: "row gap", style: "justify-content:space-between" }, h("b", null, it.name),
        h("span", { class: "small", style: `color:${color(it.due)}` }, it.due)),
      h("p", { class: "muted small" }, it.unit_label),
      it.recommended ? h("p", { class: "muted tiny", style: "margin:0 0 6px" },
        h("a", { href: it.recommended.source, target: "_blank", rel: "noopener" }, it.recommended.says),
        it.recommended.interval !== null && (it.recommended.interval !== it.interval || it.recommended.unit !== it.unit)
          ? [" · ", h("button", { type: "button", class: "linkbtn", style: "font-size:inherit;min-height:0",
              onclick: () => saveItems(raw.map((x) => (x.id === it.id ? { ...x, interval: it.recommended.interval, unit: it.recommended.unit,
                                                                     baseline: it.recommended.unit === it.unit ? x.baseline : (it.recommended.unit === "days" ? x.done_at / 86400 : d.counters[it.recommended.unit] || 0) } : x))) },
              `use ${count({ unit: it.recommended.unit }, it.recommended.interval)}`)] : null) : null,
      it.pct !== null ? h("div", { class: "bar" }, h("div", { class: "fillbar", style: `width:${Math.min(100, it.pct)}%;background:${color(it.due)}` })) : null,
      kv("Since last done", `${count(it, it.used)}${it.interval ? ` of ${count(it, it.interval)}` : ""} · ${when(it.done_at)}`),
      it.unit !== "head_change" ? h("label", { class: "formrow" }, h("span", { class: "muted small" }, "Interval"),
        h("input", { class: "field", type: "number", min: "0", step: "any", value: it.interval ?? "", placeholder: "your choice",
          onchange: (e) => saveItems(raw.map((x) => (x.id === it.id ? { ...x, interval: e.target.value ? Number(e.target.value) : null } : x))) })) : null,
      h("div", { class: "row gap" },
        h("button", { type: "button", class: "btn slim", onclick: async () => { await postJSON(`/api/maintenance/${it.id}/done`, {}); toast(`${it.name}: marked done.`); renderMaintenance(); } }, "Mark done"),
        h("button", { type: "button", class: "btn slim", onclick: () => { if (confirm(`Remove “${it.name}”?`)) saveItems(raw.filter((x) => x.id !== it.id)); } }, "Remove"))))),
    h("div", { class: "card", style: "margin:12px" }, h("p", { class: "label" }, "Add an item"),
      h("div", { class: "formgrid" },
        h("label", { class: "formrow" }, h("span", { class: "muted small" }, "Name"), h("input", { class: "field", id: "mnew" })),
        h("label", { class: "formrow" }, h("span", { class: "muted small" }, "Counts"),
          h("select", { class: "field", id: "mnewunit" }, Object.entries(d.units).map(([k, v]) => h("option", { value: k }, v)))),
        h("label", { class: "formrow" }, h("span", { class: "muted small" }, "Interval"), h("input", { class: "field", id: "mnewint", type: "number", min: "0", step: "any" }))),
      h("button", { type: "button", class: "btn pri", onclick: () => {
        const name = $("#mnew").value.trim();
        if (!name) { toast("Give it a name."); return; }
        const unit = $("#mnewunit").value;
        saveItems([...raw, { name, unit, interval: $("#mnewint").value ? Number($("#mnewint").value) : null, baseline: d.counters[unit] || 0 }]);
      } }, "Add")));
}

/* ------------------------------------------------- camera polygons on map */
const shortCam = (c) => c.replace(/_frigate$/, "").replace(/_/g, " ");

// Area of a [[lon, lat], ...] ring in m²: shoelace in metres around the first corner (fine at yard scale).
function ringAreaM2(ring) {
  if (!ring || ring.length < 3) return 0;
  const [lon0, lat0] = ring[0], kx = 111320 * Math.cos(lat0 * Math.PI / 180), ky = 110540;
  const pts = ring.map(([lon, lat]) => [(lon - lon0) * kx, (lat - lat0) * ky]);
  let a = 0;
  for (let i = 0; i < pts.length; i++) {
    const [x1, y1] = pts[i], [x2, y2] = pts[(i + 1) % pts.length];
    a += x1 * y2 - x2 * y1;
  }
  return Math.abs(a) / 2;
}

const validPolys = (polys) => Object.fromEntries(Object.entries(polys || {}).filter(([, r]) => ringAreaM2(r) >= 1));

function opsMapInit() {
  const map = state.map;
  ops.camMarkers = [];
  for (const id of ["cameras", "draw"]) map.addSource(id, { type: "geojson", data: EMPTY });
  map.addLayer({ id: "cameras-fill", type: "fill", source: "cameras", paint: { "fill-color": MAP.camera, "fill-opacity": 0.06 } }, "track-line");
  map.addLayer({ id: "cameras-line", type: "line", source: "cameras", paint: { "line-color": MAP.camera, "line-width": 1.5, "line-dasharray": [0.5, 2] } }, "track-line");
  map.addLayer({ id: "draw-fill", type: "fill", source: "draw", filter: ["==", ["geometry-type"], "Polygon"], paint: { "fill-color": SKY, "fill-opacity": 0.25 } });
  map.addLayer({ id: "draw-line", type: "line", source: "draw", filter: ["!=", ["geometry-type"], "Point"], paint: { "line-color": SKY, "line-width": 2.5 } });
  map.addLayer({ id: "draw-pts", type: "circle", source: "draw", filter: ["==", ["geometry-type"], "Point"],
                 paint: { "circle-radius": ["case", ["get", "first"], 8, 5], "circle-color": "#F2F5F3",
                          "circle-stroke-color": SKY, "circle-stroke-width": 2 } });
  map.addSource("patch", { type: "geojson", data: EMPTY });
  map.addLayer({ id: "patch-route", type: "line", source: "patch", filter: ["==", ["geometry-type"], "LineString"],
                 paint: { "line-color": MAP.route, "line-width": 1.5, "line-opacity": 0.9 } });
  map.addLayer({ id: "patch-fill", type: "fill", source: "patch", filter: ["==", ["geometry-type"], "Polygon"],
                 paint: { "fill-color": ["case", ["get", "ok"], SKY, CORAL], "fill-opacity": 0.18 } });
  map.addLayer({ id: "patch-line", type: "line", source: "patch", filter: ["==", ["geometry-type"], "Polygon"],
                 paint: { "line-color": ["case", ["get", "ok"], SKY, CORAL], "line-width": 2.5 } });
  loadCameraPolygons();
}

// Zones load after this file's layers are added and also slot in under the track, which would bury the coverage.
function raiseCameraLayers() {
  const map = state.map;
  if (!map || !map.getLayer("track-line")) return;
  for (const id of ["cameras-fill", "cameras-line"]) if (map.getLayer(id)) map.moveLayer(id, "track-line");
}

function applyCamVisibility() {
  (ops.camMarkers || []).forEach((m) => { m.getElement().style.display = camerasVisible() && state.layers.labels ? "" : "none"; });
}

async function loadCameraPolygons() {
  let d;
  try { d = await getJSON("/api/frigate/cameras"); } catch { return; }
  const polys = validPolys(d.polygons);
  setSource("cameras", { type: "FeatureCollection", features: Object.entries(polys).map(([cam, ring]) => ({
    type: "Feature", properties: { cam }, geometry: { type: "Polygon", coordinates: [[...ring, ring[0]]] } })) });
  raiseCameraLayers();
  (ops.camMarkers || []).forEach((m) => m.remove());
  ops.camMarkers = Object.entries(polys).map(([cam, ring]) => new maplibregl.Marker({ element: h("div", { class: "zlabel cam" }, shortCam(cam)) })
    .setLngLat(centroid([...ring, ring[0]])).addTo(state.map));
  applyCamVisibility();
}

// `back` is where to return once drawing is over (Settings → Cameras); drawing itself happens on the Live map.
function startDraw(camera, back = null) {
  if (ops.drawing) { ops.drawing.back = null; ops.drawing.finish(false); }   // pre-empted: stay on the map
  $(".drawpanel")?.remove();
  if (location.hash !== "#live") location.hash = "#live";
  const map = state.map;
  const d = { camera, pts: [], hover: null, back };
  ops.drawing = d;
  syncLiveMode();
  map.doubleClickZoom.disable();
  map.getCanvas().style.cursor = "crosshair";
  if (!state.layers.cameras) $('.tool[data-layer="cameras"]')?.click();
  requestAnimationFrame(() => { if (!state.fitted) state.fitted = fitYard(); });

  const near = (a, b, tol) => { const pa = map.project(a), pb = map.project(b); return Math.hypot(pa.x - pb.x, pa.y - pb.y) <= tol; };
  const status = h("p", { class: "small" });
  const save = h("button", { type: "button", class: "btn pri", onclick: () => d.finish(true) }, "Save");
  const undo = h("button", { type: "button", class: "btn", onclick: () => { d.pts.pop(); redraw(); } }, "Undo");
  const redraw = () => {
    const pts = d.pts, ring = d.hover && pts.length ? [...pts, d.hover] : pts;
    const feats = pts.map((p, i) => ({ type: "Feature", properties: { first: i === 0 && pts.length >= 3 }, geometry: { type: "Point", coordinates: p } }));
    if (ring.length >= 3) feats.push({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [[...ring, ring[0]]] } });
    else if (ring.length === 2) feats.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: ring } });
    setSource("draw", { type: "FeatureCollection", features: feats });
    const area = ringAreaM2(pts);
    status.textContent = pts.length < 3 ? `${pts.length} corner${pts.length === 1 ? "" : "s"} so far; at least 3 needed.`
                                        : `${pts.length} corners · ${Math.round(area)} m². Click the first corner (or Save) to finish.`;
    save.disabled = !(pts.length >= 3 && area >= 1);
    undo.disabled = !pts.length;
  };
  const onClick = (e) => {
    const p = [e.lngLat.lng, e.lngLat.lat];
    if (d.pts.length >= 3 && near(p, d.pts[0], 12)) { d.finish(true); return; }
    if (d.pts.length && near(p, d.pts[d.pts.length - 1], 8)) return;  // a double-click or a shaky click: same corner
    d.pts.push(p);
    redraw();
  };
  const onMove = (e) => { if (d.pts.length) { d.hover = [e.lngLat.lng, e.lngLat.lat]; redraw(); } };
  const onOut = () => { d.hover = null; redraw(); };
  const onKey = (e) => {
    if (e.target.closest?.("input, textarea, select")) return;
    if (e.key === "Escape") d.finish(false);
    else if (e.key === "Enter") d.finish(true);
    else if (e.key === "Backspace" || (e.key === "z" && (e.ctrlKey || e.metaKey))) { e.preventDefault(); d.pts.pop(); redraw(); }
  };
  const panel = h("div", { class: "alignpanel drawpanel", role: "dialog", "aria-label": `Draw coverage for ${shortCam(camera)}` });
  d.finish = async (keep) => {
    if (ops.drawing !== d) return;
    if (keep && !(d.pts.length >= 3 && ringAreaM2(d.pts) >= 1)) { toast("Add at least 3 separate corners first."); return; }
    map.off("click", onClick);
    map.off("mousemove", onMove);
    map.off("mouseout", onOut);
    document.removeEventListener("keydown", onKey);
    map.doubleClickZoom.enable();
    map.getCanvas().style.cursor = "";
    ops.drawing = null;
    syncLiveMode();
    setSource("draw", EMPTY);
    const leave = () => { panel.remove(); if (d.back) location.hash = d.back; };
    if (!keep) { leave(); return; }
    let cams;
    try {
      cams = await getJSON("/api/frigate/cameras");
      await putJSON("/api/settings/camera_polygons", { ...validPolys(cams.polygons), [camera]: d.pts });
    } catch (e) { toast(e.message); leave(); return; }
    await loadCameraPolygons();
    const left = cams.yard.filter((c) => c !== camera && !(ringAreaM2(cams.polygons[c]) >= 1));
    fill(panel,
      h("p", { class: "label" }, `Saved: ${shortCam(camera)} · ${Math.round(ringAreaM2(d.pts))} m²`),
      h("p", { class: "muted small" }, left.length ? "Yard cameras still without coverage:" : "Every yard camera has its coverage."),
      left.length ? h("div", { class: "camchips" }, left.map((c) => h("button", { type: "button", class: "btn slim", onclick: () => startDraw(c, d.back) }, shortCam(c)))) : null,
      h("div", { class: "row gap" }, h("button", { type: "button", class: "btn", onclick: leave }, "Done")));
  };
  map.on("click", onClick);
  map.on("mousemove", onMove);
  map.on("mouseout", onOut);
  document.addEventListener("keydown", onKey);
  fill(panel,
    h("p", { class: "label" }, `Coverage: ${shortCam(camera)}`),
    h("img", { class: "snap", alt: `What ${shortCam(camera)} sees now`, src: `/api/frigate/snapshot/${encodeURIComponent(camera)}.jpg?height=240&t=${Date.now()}` }),
    h("p", { class: "muted small" }, "Click the corners of the ground this camera can see. Click the first corner again to finish. Backspace undoes a corner; Esc cancels."),
    status,
    h("div", { class: "row gap" }, save, undo, h("button", { type: "button", class: "btn", onclick: () => d.finish(false) }, "Cancel")));
  $(".mapwrap").append(panel);
  redraw();
}

startFollowTimer();

/* ------------------------------------------------------------- mow a patch (ADR 0013) */
/* A rectangle inside one area, mowed once along the last mow's lines; the server adds it to the robot, starts it with
   the usual checks and takes it off again once the robot is back on its charger (app/patch.py). Placing it: the
   rectangle stays in the middle of the map and the map pans under it, with steppers for its size, so nothing is
   dragged by touch. The server lays it along the lines and says whether it fits. */
const patchUI = { view: null, fetchedAt: 0, place: null, timer: null };
const PATCH_STEP = 0.5, PATCH_MIN = 1.5, PATCH_MAX = 30;
const PATCH_SETTLING = new Set(["adding", "aligning", "started", "removing"]);

async function patchRefresh(force = false) {
  const settling = PATCH_SETTLING.has(patchUI.view?.patch?.stage);
  if (!force && Date.now() - patchUI.fetchedAt < (settling ? 5000 : 20000)) return patchUI.view;
  patchUI.fetchedAt = Date.now();
  const seq = patchUI.seq = (patchUI.seq || 0) + 1;
  try {
    const v = await getJSON("/api/patch");
    if (seq === patchUI.seq) patchUI.view = v;              // an older answer arriving late never replaces a newer one
  } catch { /* keep the last answer */ }
  drawPatchMap();
  return patchUI.view;
}

function patchStageLine(p) {
  const size = `${p.length} × ${p.width} m in ${p.parent?.name || "the area"}`;
  const mins = p.time_s ? `, about ${Math.max(1, Math.round(p.time_s / 60))} min` : "";
  return {
    adding: "Setting up the patch on the robot…",
    aligning: "Lining the patch up with the last mow…",
    ready: `Patch ready: ${size}${mins}.`,
    started: p.running_seen
      ? `Mowing the patch (${size}). It comes off the robot once the robot is back on its charger.`
      : `Start sent for the patch (${size}); waiting for the robot to show it running…`,
    removing: "Taking the patch off the robot…",
    stuck: p.differs?.length
      ? `The patch is off the robot, but the map differs from before: ${p.differs.join("; ")}. Check the map in the `
        + "Yarbo app, then check again or accept the map as it is."
      : `The patch could not be taken off the robot (${(p.log || []).slice(-1)[0] || "see Events"}). `
        + "Try again, or delete “Patch (temporary)” in the Yarbo app.",
  }[p.stage] || `Patch: ${p.stage}`;
}

function patchBlock(off, runningWhy) {
  const v = patchUI.view || {}, p = v.patch;
  if (patchUI.place) return h("p", { class: "muted small", style: "margin-top:10px" }, "Placing a patch on the map…");
  if (!p) {
    const why = runningWhy || v.blocked || null;
    const last = v.last && Date.now() / 1000 - v.last.at < 86400 && v.last.problems?.length
      ? h("p", { class: "small", style: `color:${AMBER};margin:6px 0 0` },
          `The map was accepted as it is after the last patch: ${v.last.problems.join("; ")}.`) : null;
    return h("div", { style: "margin-top:10px" },
      h("button", { type: "button", class: "btn wide", "data-key": "ctl-patch", disabled: off || !!why || null,
        title: why ? `Mow a patch: ${why}` : "Mow a small rectangle inside an area, along the last mow's lines",
        onclick: startPlacing }, "Mow a patch…"),
      why && !off ? h("p", { class: "whyline muted tiny" }, `Mow a patch: ${why}.`) : null,
      last);
  }
  const ready = p.stage === "ready", stuck = p.stage === "stuck";
  // Taking it off needs what any map write needs; say what is in the way (map writing off strands it otherwise).
  const held = v.blocked && (ready || stuck || (p.stage === "started" && !/plan is running/.test(v.blocked)))
    ? v.blocked : null;
  return h("div", { class: "patchbox" },
    h("p", { class: "small", style: `margin:0 0 8px;color:${stuck ? CORAL : "inherit"}` }, patchStageLine(p)),
    held ? h("p", { class: "whyline muted tiny", style: "margin:0 0 8px" },
      `It can't come off the robot right now: ${held}.`) : null,
    ready || stuck ? h("div", { class: "row gap" },
      ready ? h("button", { type: "button", class: "btn pri", "data-key": "ctl-patch-start", disabled: off || null,
        onclick: () => openConfirm("start_plan", "Start the patch", { plan_id: p.plan_id }, false, {
          preview: () => postJSON("/api/patch/preview", { plan_id: p.plan_id }),
          send: (override) => postJSON("/api/patch/start", { confirm: true, override, plan_id: p.plan_id })
            .finally(() => patchRefresh(true)),
        })
      }, "Start patch…") : null,
      h("button", { type: "button", class: "btn", "data-key": "ctl-patch-remove", disabled: held || null,
        title: held ? `Remove: ${held}` : null, onclick: () => removePatch(false, !!p.differs?.length) },
        stuck ? (p.differs?.length ? "Check again" : "Try again") : "Remove"),
      stuck && p.differs?.length ? h("button", { type: "button", class: "btn", "data-key": "ctl-patch-accept",
        disabled: held || null, onclick: () => removePatch(true) }, "Accept the map") : null) : null);
}

async function removePatch(accept = false, recheck = false) {
  const ask = accept ? "Accept the robot's map as it is now? The differences stay; the panel stops tracking the patch."
    : recheck ? "Check the robot's map against how it was before the patch again?"
    : "Take the patch off the robot now? Its area and plan are deleted; the rest of the map is checked.";
  if (!confirm(ask)) return;
  try {
    const r = await fetch(accept ? "/api/patch?accept=true" : "/api/patch", { method: "DELETE" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `${r.status}`);
    toast(d.ok ? "The patch is off the robot." : `The patch is off, but: ${(d.problems || [d.error]).join("; ")}`);
  } catch (e) { toast(`Not removed: ${e.message}`); }
  await patchRefresh(true);
  refreshLive();
}

function startPlacing() {
  const map = state.map;
  if (!map || patchUI.place) return;                           // a second tap before the card redraws
  const pl = patchUI.place = { length: 6, width: 3, check: null, center: null, seq: 0, busy: false, error: null };
  // In the camera layout the map is the small locked overview: bring the big map back first, once it has settled.
  const small = MAP_EL.parentElement !== $(".mapwrap");
  ops.patchPlacing = true;
  syncLiveMode();
  if (small) requestAnimationFrame(() => requestAnimationFrame(() => { if (patchUI.place === pl) beginPlacing(); }));
  else beginPlacing();
}

function beginPlacing() {
  const map = state.map;
  $(".mapwrap")?.classList.add("placing");
  if (STACKED.matches) $(".mapwrap")?.scrollIntoView({ behavior: "smooth", block: "start" });
  map.on("move", drawPatchMap);
  map.on("moveend", patchCheckSoon);
  drawPatchBar();
  // The patch sits in the middle of what the bar leaves visible: the bar's height is the map's bottom padding, which
  // moves the map's centre (getCenter) up with it.
  map.resize();
  map.setPadding({ top: 0, left: 0, right: 0, bottom: ($("#patchbar")?.offsetHeight || 0) + 20 });
  if (map.getZoom() < 20) map.easeTo({ zoom: 20, duration: 300 });     // close enough to place a few metres
  patchCheck();
  refreshLive();
}

function stopPlacing() {
  const map = state.map;
  if (map) {
    map.off("move", drawPatchMap);
    map.off("moveend", patchCheckSoon);
    map.setPadding({ top: 0, left: 0, right: 0, bottom: 0 });
  }
  clearTimeout(patchUI.timer);
  patchUI.place = null;
  ops.patchPlacing = false;
  $(".mapwrap")?.classList.remove("placing");
  $("#patchbar")?.remove();
  map?.resize();
  drawPatchMap();
  if (state.view === "live") syncLiveMode();                   // back to the camera layout if that was on
  refreshLive();
}

function patchCheckSoon() {
  clearTimeout(patchUI.timer);
  patchUI.timer = setTimeout(patchCheck, 250);
}

async function patchCheck() {
  const pl = patchUI.place;
  if (!pl || pl.busy || state.view !== "live") return;
  const c = state.map.getCenter(), seq = ++pl.seq;
  pl.pending = true;
  drawPatchBar();
  let r;
  try { r = await postJSON("/api/patch/check", { lon: c.lng, lat: c.lat, length: pl.length, width: pl.width }); }
  catch (e) { r = { ok: false, problems: [e.message] }; }
  if (patchUI.place !== pl || seq !== pl.seq) return;          // a newer check is on its way
  Object.assign(pl, { check: r, center: [c.lng, c.lat], pending: false });
  drawPatchMap();
  drawPatchBar();
}

// While placing, the rectangle follows the map's centre (corners kept as offsets from where they were checked).
function drawPatchMap() {
  const pl = patchUI.place, p = patchUI.view?.patch, feats = [];
  const ring = (cs) => ({ type: "Polygon", coordinates: [[...cs, cs[0]]] });
  if (pl && pl.check?.corners && pl.center && state.map) {
    const c = state.map.getCenter();
    const moved = pl.check.corners.map(([x, y]) => [x - pl.center[0] + c.lng, y - pl.center[1] + c.lat]);
    feats.push({ type: "Feature", properties: { ok: !!pl.check.ok && !pl.pending }, geometry: ring(moved) });
  } else if (!pl && p?.corners && ["ready", "started", "aligning"].includes(p.stage)) {
    feats.push({ type: "Feature", properties: { ok: true }, geometry: ring(p.corners) });
    if (p.route) feats.push({ ...p.route, properties: {} });
  }
  setSource("patch", { type: "FeatureCollection", features: feats });
}

function drawPatchBar() {
  const pl = patchUI.place, wrap = $(".mapwrap");
  if (!pl || !wrap) return;
  let bar = $("#patchbar");
  if (!bar) { bar = h("div", { id: "patchbar", class: "patchbar", role: "group", "aria-label": "Place the patch" }); wrap.append(bar); }
  const r = pl.check;
  const step = (key, d) => () => { pl[key] = Math.min(PATCH_MAX, Math.max(PATCH_MIN, pl[key] + d)); patchCheck(); };
  const size = (key, label) => h("span", { class: "patchsize" },
    h("span", { class: "muted tiny" }, label),
    h("button", { type: "button", class: "btn", "data-key": `patch-${key}-less`, "aria-label": `${label} shorter`,
      disabled: pl.busy || pl[key] <= PATCH_MIN || null, onclick: step(key, -PATCH_STEP) }, "−"),
    h("b", null, `${pl[key]} m`),
    h("button", { type: "button", class: "btn", "data-key": `patch-${key}-more`, "aria-label": `${label} longer`,
      disabled: pl.busy || pl[key] >= PATCH_MAX || null, onclick: step(key, PATCH_STEP) }, "+"));
  const lines = r?.target ? (r.target.source === "run" ? "along the last mow" : "along the area's plan") : null;
  const blocked = patchUI.view?.blocked;
  const status = pl.busy ? h("span", null, "Setting up on the robot… about a minute")
    : pl.error ? h("span", { style: `color:${CORAL}` }, pl.error)
    : pl.pending || !r ? h("span", { class: "muted" }, "Checking…")
    : r.ok && blocked ? h("span", { style: `color:${AMBER}` }, `Fits here, but can't be set up now: ${blocked}`)
    : r.ok ? h("span", null, `${r.area.name || "Area"} · ${r.area_m2} m² · ${lines}`)
    : h("span", { style: `color:${CORAL}` }, (r.problems || []).join("; "));
  fill(bar,
    h("div", { class: "row gap wrap" }, size("length", "Length"), size("width", "Width")),
    h("p", { class: "small", style: "margin:6px 0" }, status),
    h("div", { class: "row gap", style: "justify-content:flex-end" },
      h("button", { type: "button", class: "btn", disabled: pl.busy || null, onclick: stopPlacing }, "Cancel"),
      h("button", { type: "button", class: "btn pri", "data-key": "patch-setup",
        disabled: pl.busy || pl.pending || !r?.ok || !!blocked || null, onclick: setUpPatch }, "Set up patch")));
}

async function setUpPatch() {
  const pl = patchUI.place;
  if (!pl?.check?.ok) return;
  const c = state.map.getCenter();
  if (!pl.center || Math.abs(c.lng - pl.center[0]) > 1e-7 || Math.abs(c.lat - pl.center[1]) > 1e-7) {
    patchCheck();                                              // moved since the last check: check where it is now
    return;
  }
  if (!confirm(`Add a ${pl.length} × ${pl.width} m patch to the robot's map, with its own plan? `
               + "It is taken off again once it has been mowed.")) return;
  Object.assign(pl, { busy: true, error: null });
  drawPatchBar();
  try {
    const r = await postJSON("/api/patch", { confirm: true, lon: pl.center[0], lat: pl.center[1], length: pl.length, width: pl.width });
    if (!r.ok) throw new Error(r.error || "not set up");
    toast("The patch is ready: check its route on the map, then start it.");
    stopPlacing();
  } catch (e) {
    Object.assign(pl, { busy: false, error: `Not set up: ${e.message}` });
    drawPatchBar();
  }
  await patchRefresh(true);
  refreshLive();
}
