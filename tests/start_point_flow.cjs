// The start confirmation's "Start at" (leftOffAt, startPoints, startChoice, pickStartPoint and previewConfirm in
// web/ops.js): which start points are offered, and that the checks on screen are the ones for what Send sends.
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
const noLeftOff = src.match(/^const NO_LEFT_OFF = .*$/m);
if (!noLeftOff) throw new Error("NO_LEFT_OFF not found in web/ops.js");

const asked = [];          // every preview asked for: {args, answer(preview)}
const sandbox = {
  ops: { confirm: null }, draws: 0,
  drawConfirm() { sandbox.draws++; },
  postJSON: (url, body) => new Promise((resolve) => asked.push({ args: body.args, answer: resolve })),
};
vm.createContext(sandbox);
vm.runInContext([noLeftOff[0], cut("function leftOffAt("), cut("function startPoints("), cut("function startChoice("),
  cut("function pickStartPoint("), cut("async function previewConfirm(")].join("\n")
  + "\nObject.assign(this, { leftOffAt, startPoints, startChoice, pickStartPoint, previewConfirm });", sandbox);

const run = { started: 10000, ended: 20000, end_reason: "returned to dock", plan_session: "1:9950",
              plan_progress_pct: 42.7 };
const sent = (ts, args, ok = 1) => ({ cmd: "start_plan", ts, sent: ok, args: JSON.stringify(args) });
const at = (r, log = []) => sandbox.leftOffAt(r, 1, log);
const out = {
  early: at(run),                                              // rounded down: overlap, never a skipped strip
  noRun: at(null),
  finished: at({ ...run, end_reason: "plan finished", plan_progress_pct: 100 }),
  panelRestarted: at({ ...run, end_reason: "recorder restarted" }),
  neverResumed: at({ ...run, end_reason: "paused, not resumed" }),
  stillOpen: at({ ...run, ended: null }),
  otherPlan: at({ ...run, plan_session: "11:9950" }),          // plan 11 is not plan 1
  noSession: at({ ...run, plan_session: null }),
  noProgress: at({ ...run, plan_progress_pct: null }),
  barelyBegun: at({ ...run, plan_progress_pct: 0.4 }),
  allButDone: at({ ...run, plan_progress_pct: 99.9 }),
  // The run itself began part-way (this panel's start, a minute before the run opened): no hint.
  beganPartWay: at(run, [sent(9940, { plan_id: 1, start_percent: 30 })]),
  beganAtTheStart: at(run, [sent(9940, { plan_id: 1 })]),
  refusedPartWay: at(run, [sent(9940, { plan_id: 1, start_percent: 30 }, 0), sent(9930, { plan_id: 1 })]),
  olderPartWay: at(run, [sent(9940, { plan_id: 1 }), sent(2000, { plan_id: 1, start_percent: 30 })]),
  points: sandbox.startPoints(42, 0),
  pointsPlain: sandbox.startPoints(null, 0),
  pointsPicked: sandbox.startPoints(null, 42),
  choice: sandbox.startChoice("Start Mow All", 1, 40),
  choiceBeginning: sandbox.startChoice("Start Mow All", 1, 0),
};

(async () => {
  // The dialog is open on a start from the beginning; 40% is picked, then 45% before the first answer is back.
  const c = sandbox.ops.confirm = { cmd: "start_plan", label: "Start Mow All", args: { plan_id: 1 }, override: true,
    preview: { would_send: true }, startAt: { base: "Start Mow All", planId: 1, pct: 0, leftOff: null } };
  const first = sandbox.pickStartPoint(c, 40);
  out.afterPick = { label: c.label, args: c.args, preview: c.preview, override: c.override };
  const second = sandbox.pickStartPoint(c, 45);
  asked[1].answer({ for: 45 });                                // the later answer first
  await second;
  asked[0].answer({ for: 40 });                                // the stale one last: it must not replace it
  await first;
  out.afterRace = { label: c.label, args: c.args, preview: c.preview, asked: asked.map((a) => a.args) };
  // Back to the beginning: no start point is sent at all.
  const third = sandbox.pickStartPoint(c, 0);
  asked[2].answer({ for: 0 });
  await third;
  out.backToStart = { label: c.label, args: c.args, preview: c.preview };
  // An answer that lands after the dialog closed changes nothing.
  const fourth = sandbox.pickStartPoint(c, 50);
  sandbox.ops.confirm = null;
  asked[3].answer({ for: 50 });
  await fourth;
  out.afterClose = c.preview;
  // Once Send is pressed, another start point is not taken up and no checks are asked for it.
  const d = sandbox.ops.confirm = { cmd: "start_plan", label: "Start Mow All from 40%", sending: true,
    args: { plan_id: 1, start_percent: 40 }, preview: { would_send: true }, override: false,
    startAt: { base: "Start Mow All", planId: 1, pct: 40, leftOff: null } };
  await sandbox.pickStartPoint(d, 45);
  out.whileSending = { label: d.label, args: d.args, preview: d.preview, pct: d.startAt.pct, asked: asked.length };
  console.log(JSON.stringify(out));
})();
