// Runs index.js against a stand-in for Premiere's UXP modules and checks what it asks Premiere to do.
// It cannot prove Premiere accepts the calls (that needs Premiere); it proves the panel's own logic: names are listed and saved,
// cuts are found between touching clips only, the first-cut button adds exactly one transition, the every-cut button one per cut,
// the length is converted from frames to ticks, and errors are shown instead of thrown.
//   node labs/uxp_transitions/test_panel_logic.js
const assert = require("assert");
const fs = require("fs");
const Module = require("module");
const path = require("path");

const TPF = 8475667200; // ticks per frame at 29.97
const calls = { created: [], added: [], saved: [] };
const tt = (ticks) => ({ ticksNumber: ticks, ticks: String(ticks) });
const clip = (name, startF, endF) => ({
  name,
  getName: async () => name,
  getStartTime: async () => tt(startF * TPF),
  getEndTime: async () => tt(endF * TPF),
  createAddVideoTransitionAction: (transition, opts) => ({ kind: "add", on: name, transition, opts }),
});
// V1: A [0,100) B [100,200) C [250,300): A|B touch, B|C have a gap and must not count as a cut
const items = [clip("B", 100, 200), clip("A", 0, 100), clip("C", 250, 300)];
const track = { getTrackItems: async (type) => (type === "TRANSITION" ? calls.added : items) };
const seq = { name: "test seq", getTimebase: async () => String(TPF), getVideoTrack: async () => track };
const project = {
  lockedAccess: (cb) => cb(),
  executeTransaction: (cb, undo) => { cb({ addAction: (a) => calls.added.push(a) }); calls.undo = undo; return true; },
  getActiveSequence: async () => seq,
};
class AddTransitionOptions {
  constructor() { this.p = {}; }
  setDuration(t) { this.p.duration = t; return this; }
  setApplyToStart(v) { this.p.applyToStart = v; return this; }
  setForceSingleSided(v) { this.p.single = v; return this; }
  setTransitionAlignment(v) { this.p.alignment = v; return this; }
}
const ppro = {
  Constants: { TrackItemType: { CLIP: "CLIP", TRANSITION: "TRANSITION" } },
  Project: { getActiveProject: async () => project },
  TickTime: { createWithTicks: (s) => ({ ticks: s }) },
  TransitionFactory: {
    getVideoTransitionMatchNames: async () => ["ADBE Cross Dissolve New", "AE.Impact Push", "AE.Impact Blur Dissolve", "Other"],
    createVideoTransition: (n) => { calls.created.push(n); return { match: n }; },
  },
  AddTransitionOptions,
};
const uxp = { storage: { localFileSystem: { getDataFolder: async () => ({ createFile: async (n) => ({ nativePath: "/fake/" + n, write: async (t) => calls.saved.push([n, t]) }) }) } } };

const orig = Module._load;
Module._load = (req, ...rest) => (req === "premierepro" ? ppro : req === "uxp" ? uxp : orig(req, ...rest));
const els = {};
const handlers = {};
global.document = {
  getElementById: (id) => (els[id] = els[id] || { value: "", scrollTop: 0, scrollHeight: 0, addEventListener: (ev, fn) => (handlers[id] = fn) }),
};
const set = (id, v) => (global.document.getElementById(id).value = v);
set("filter", "impact"); set("name", "AE.Impact Push"); set("track", "1"); set("frames", "6"); set("align", "0.5"); set("log", "");
require(path.join(__dirname, "index.js"));
const log = () => els.log.value;
const tick = () => new Promise((r) => setTimeout(r, 20));

(async () => {
  await handlers.list(); await tick();
  assert(log().includes("4 video transition match names"), "counts all names");
  assert(log().includes("2 contain \"impact\""), "filters by text");
  assert(calls.saved.length === 1 && calls.saved[0][1].split("\n").length === 4, "saves the full list to a file");

  await handlers.first(); await tick();
  assert(log().includes("1 cuts between touching clips"), "only the touching pair is a cut: " + log());
  assert.strictEqual(calls.created.length, 1, "first-cut button creates one transition");
  const a = calls.added[0];
  assert.strictEqual(a.on, "B", "the transition goes on the clip after the cut");
  assert.strictEqual(a.opts.p.applyToStart, true);
  assert.strictEqual(a.opts.p.single, false);
  assert.strictEqual(a.opts.p.alignment, 0.5);
  assert.strictEqual(a.opts.p.duration.ticks, String(6 * TPF), "6 frames converted to ticks");
  assert.strictEqual(calls.created[0], "AE.Impact Push");

  calls.created.length = 0; calls.added.length = 0;
  await handlers.all(); await tick();
  assert.strictEqual(calls.added.length, 1, "every-cut button: one per touching cut");

  // a name pasted into the filter box instead of the name box is used when it is exactly one of the listed names
  calls.created.length = 0; calls.added.length = 0;
  set("name", ""); set("filter", "AE.Impact Push"); els.log.value = "";
  await handlers.first(); await tick();
  assert.strictEqual(calls.created[0], "AE.Impact Push", "uses the exact name found in the filter box");
  assert(log().includes("using \"AE.Impact Push\" from the filter box"));

  set("filter", "impact"); set("name", ""); els.log.value = "";                    // a partial word in the filter is not a name
  await handlers.first(); await tick();
  assert(log().includes("ERROR") && log().includes("match name"), "an empty name is reported, not thrown");
  console.log("panel logic: all checks passed");
})().catch((e) => { console.error("FAILED:", e.message, "\nlog:\n" + els.log.value); process.exit(1); });
