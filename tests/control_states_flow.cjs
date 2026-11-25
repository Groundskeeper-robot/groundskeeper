// Live's Control card (controlCard in web/ops.js) in each state of the robot: which buttons it offers.
// Run by tests/test_control_card.py (only when node is installed). Synthetic values only.
"use strict";
const fs = require("fs");
const vm = require("vm");

const src = fs.readFileSync(require("path").join(__dirname, "..", "web", "ops.js"), "utf8");
const at = src.indexOf("function controlCard("), end = src.indexOf("\n}\n", at) + 3;
if (at < 0 || end < 3) throw new Error("controlCard not found in web/ops.js");
const NOT = src.match(/^const NOT_PAUSABLE = .*\n^const NOT_DOCKABLE = .*$/m);
if (!NOT) throw new Error("NOT_PAUSABLE / NOT_DOCKABLE not found in web/ops.js");

const sandbox = {
  ops: { planId: 1 }, emergency: false,
  state: { plansCache: [{ id: 1, name: "Mow All" }] },
  h: (tag, attrs, ...kids) => ({ tag, attrs: attrs || {}, kids: kids.flat(Infinity).filter((k) => k) }),
  HELD: new Set(["paused", "charging_break"]),
  routeOk: () => true, noRouteWhy: () => "", planAreas: () => "Front lawn", readinessView: () => null,
  openConfirm() {}, closeConfirm() {}, refreshLive() {},
  patchUI: { view: null }, patchBlock: () => null, stateColor: () => "g", num: (v) => String(v), dur: (v) => `${v}s`,
};
sandbox.emergencyShown = () => sandbox.emergency;
vm.createContext(sandbox);
vm.runInContext(`${NOT[0]}\n${src.slice(at, end)}\nthis.controlCard = controlCard;`, sandbox);

const find = (n, test) => (n && test(n) ? [n] : []).concat(...((n && n.kids) || []).map((k) => find(k, test)));
const keys = (card) => find(card, (n) => n.attrs && /^ctl-/.test(n.attrs["data-key"] || "")).map((n) => n.attrs["data-key"]);
const base = { ops: { control_enabled: true }, lost_contact: false };
const card = (extra) => sandbox.controlCard({ ...base, ...extra });
const run = { plan: { plan_id: 1, progress_pct: 42, left_s: 600 } };

const out = {
  docked: keys(card({ activity: "docked", state: "docked", run: null })),
  mowing: keys(card({ activity: "mowing", state: "mowing", run })),
  held: keys(card({ activity: "paused", state: "paused", run })),
  returning: keys(card({ activity: "returning", state: "returning", run })),
  unknown: keys(card({ activity: null, state: "waiting for data", run: null })),
};
sandbox.emergency = true;                        // the emergency card holds the run's buttons
out.mowingWithEmergency = keys(card({ activity: "mowing", state: "mowing", run }));
out.dockedWithEmergency = keys(card({ activity: "docked", state: "docked", run: null }));
const stop = find(card({ activity: "unknown", state: "waiting for data", run: null }),
  (n) => n.attrs && n.attrs["data-key"] === "ctl-stop")[0];
sandbox.emergency = false;
const stopMowing = find(card({ activity: "mowing", state: "mowing", run }), (n) => n.attrs && n.attrs["data-key"] === "ctl-stop")[0];
out.stopClass = stopMowing.attrs.class;
out.unknownWithEmergencyStop = !!stop;
console.log(JSON.stringify(out));
