// Live's Control card (controlCard in web/ops.js): which plan the start confirmation names and which it sends, when
// Start is clicked between picking another plan and the redraw that follows.
// Run by tests/test_start_point.py (only when node is installed). Synthetic values only.
"use strict";
const fs = require("fs");
const vm = require("vm");

const src = fs.readFileSync(require("path").join(__dirname, "..", "web", "ops.js"), "utf8");
const at = src.indexOf("function controlCard("), end = src.indexOf("\n}\n", at) + 3;
if (at < 0 || end < 3) throw new Error("controlCard not found in web/ops.js");

const opened = [];         // every confirmation opened: [cmd, label, args]
const sandbox = {
  ops: { planId: 1 },
  state: { plansCache: [{ id: 1, name: "Mow All" }, { id: 2, name: "Mow Side" }] },
  // A plain tree of tags, attributes and children, as tests/emerg_flow.cjs draws the emergency card.
  h: (tag, attrs, ...kids) => ({ tag, attrs: attrs || {}, kids: kids.flat(Infinity).filter((k) => k) }),
  HELD: new Set(), NOT_PAUSABLE: new Set(["docked"]), NOT_DOCKABLE: new Set(["docked"]),
  routeOk: () => true, noRouteWhy: () => "", planAreas: () => "Front lawn", readinessView: () => null,
  openConfirm: (cmd, label, args) => { opened.push([cmd, label, args]); },
  closeConfirm() {},
  refreshLive() {},        // the redraw is on its way and has not landed: the card on screen stays the old one
};
vm.createContext(sandbox);
vm.runInContext(src.slice(at, end) + "\nthis.controlCard = controlCard;", sandbox);

const find = (n, test) => (n && test(n) ? [n] : []).concat(...((n && n.kids) || []).map((k) => find(k, test)));
const keyed = (card, key) => find(card, (n) => n.attrs && n.attrs["data-key"] === key)[0];
const docked = { ops: { control_enabled: true }, activity: "docked", state: "docked, charging", run: null };

const out = {};
const card = sandbox.controlCard(docked);
const start = keyed(card, "ctl-start_plan");
out.button = start.kids.join("");
start.attrs.onclick();
out.sameDraw = opened.pop();
// Another plan is picked; the old card is still on screen when Start is clicked.
keyed(card, "ctl-plan").attrs.onchange({ target: { value: "2" } });
start.attrs.onclick();
out.afterPick = opened.pop();
// The picked plan has gone from the robot meanwhile: the confirmation opens unnamed and the server's check refuses.
sandbox.state.plansCache = [{ id: 1, name: "Mow All" }];
start.attrs.onclick();
out.planGone = opened.pop();
console.log(JSON.stringify(out));
