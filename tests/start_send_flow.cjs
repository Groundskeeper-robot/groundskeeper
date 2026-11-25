// The start confirmation after Send is pressed (drawConfirm, startAtRow and pickStartPoint in web/ops.js): the real
// dialog is drawn into a plain tree, Send is pressed with its request left pending, and the dialog is drawn again.
// Run by tests/test_start_point.py (only when node is installed). Synthetic values only.
"use strict";
const fs = require("fs");
const vm = require("vm");

const src = fs.readFileSync(require("path").join(__dirname, "..", "web", "ops.js"), "utf8");
const cut = (head) => {
  const at = src.indexOf(head), end = src.indexOf("\n}\n", at) + 3;
  if (at < 0 || end < 3) throw new Error(`${head} not found in web/ops.js`);
  return src.slice(at, end);
};

const posts = [];          // every request: {url, body, answer(reply)}
let closed = 0;
const sandbox = {
  ops: { confirm: null },
  GREEN: "g", AMBER: "a", CORAL: "c", dot: () => ({ tag: "i", attrs: {}, kids: [] }),
  document: { activeElement: {} },
  // A plain tree of tags, attributes and children, as tests/emerg_flow.cjs draws the emergency card.
  h: (tag, attrs, ...kids) => ({ tag, attrs: attrs || {}, kids: kids.flat(Infinity).filter((k) => k) }),
  fill: (el, ...kids) => { el.kids = kids.flat(Infinity).filter((k) => k); },
  checkList: () => ({ tag: "ul", attrs: {}, kids: [] }),
  postJSON: (url, body) => new Promise((resolve) => posts.push({ url, body, answer: resolve })),
  closeConfirm() { closed++; sandbox.ops.confirm = null; },
  reportSend() {}, toast() {}, refreshLive() {},
};
vm.createContext(sandbox);
vm.runInContext([cut("function startPct("), cut("function startChoice("), cut("function pickStartPoint("),
  cut("async function previewConfirm("), cut("function drawConfirm("), cut("function checksBlock("),
  cut("function startAtRow(")].join("\n")
  + "\nObject.assign(this, { drawConfirm, pickStartPoint });", sandbox);

const find = (n, test) => (n && test(n) ? [n] : []).concat(...((n && n.kids) || []).map((k) => find(k, test)));
const box = { kids: [], contains: () => true, querySelector: () => null };
const send = () => find(box, (n) => n.attrs && n.attrs["data-key"] === "confirm-send")[0];
const list = () => find(box, (n) => n.attrs && n.attrs["data-key"] === "confirm-from")[0];
const off = (n) => !!n.attrs.disabled;
const sends = () => posts.filter((p) => p.url === "/api/control/send");

(async () => {
  const out = {};
  // A start that would go: the checks are back and every one passes.
  const c = sandbox.ops.confirm = { cmd: "start_plan", label: "Start Mow All from 40%", previewOnly: false, box,
    args: { plan_id: 1, start_percent: 40 }, override: false,
    preview: { enabled: true, would_send: true, reason: "ok", checks: [] },
    startAt: { base: "Start Mow All", planId: 1, pct: 40, leftOff: null, mode: "part", typed: "40", invalid: false } };
  sandbox.drawConfirm();
  out.before = { sendOff: off(send()), listOff: off(list()), label: send().kids.join("") };
  const pressed = send().attrs.onclick();                      // Send is pressed; its request stays pending
  out.pressed = { sendOff: off(send()), listOff: off(list()), sends: sends().length };
  sandbox.drawConfirm();                                       // a redraw, as the override box or late checks make
  out.redrawn = { sendOff: off(send()), listOff: off(list()) };
  await sandbox.pickStartPoint(c, 45);                         // another start point now: not taken up, no redraw
  out.pickedLate = { args: c.args, sendOff: off(send()), previews: posts.length - sends().length };
  sends()[0].answer({ sent: true, confirmed: null });
  await pressed;
  out.after = { sends: sends().length, sent: sends()[0].body, closed };
  // A start the checks refuse never has Send on, pressed or not.
  sandbox.ops.confirm = { ...c, sending: false, preview: { enabled: true, would_send: false,
    reason: "Battery", checks: [{ name: "Battery", ok: false, hard: true }] } };
  sandbox.drawConfirm();
  out.refused = { sendOff: off(send()) };
  // A start point that is not a whole number from 1 to 99: Send goes off and no checks are asked for it.
  const asked = posts.length;
  sandbox.ops.confirm = { ...c, sending: false, startAt: { ...c.startAt }, preview: { enabled: true,
    would_send: true, reason: "ok", checks: [] } };
  sandbox.drawConfirm();
  list().attrs.oninput({ target: { value: "4.5" } });
  out.typedBad = { sendOff: off(send()), previews: posts.length - asked };
  console.log(JSON.stringify(out));
})();
