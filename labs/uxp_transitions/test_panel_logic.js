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
  getComponentChain: async () => chainOf(name),
});
// a clip's effects: Opacity and Motion, plus whatever the probe appends
const chains = {};
const fakeComponent = (label) => ({
  getDisplayName: async () => label, getMatchName: async () => `AE.${label}`, getParamCount: () => 1,
  getParam: () => ({ displayName: label === "Impact Move FX" ? "Direction" : "Amount", getStartValue: async () => ({ value: 2 }), areKeyframesSupported: async () => true }),
});
function chainOf(name) {
  if (!chains[name]) {
    const list = [fakeComponent("Opacity"), fakeComponent("Motion")];
    chains[name] = {
      list,
      getComponentCount: () => list.length,
      getComponentAtIndex: (i) => list[i],
      createAppendComponentAction: (c) => ({ kind: "append", on: name, apply: () => list.push(fakeComponent(c.label)) }),
    };
  }
  return chains[name];
}
// V1: A [0,100) B [100,200) C [250,300): A|B touch, B|C have a gap and must not count as a cut
const items = [clip("B", 100, 200), clip("A", 0, 100), clip("C", 250, 300)];
const track = { getTrackItems: async (type) => (type === "TRANSITION" ? calls.added : items) };
const seq = { name: "test seq", getTimebase: async () => String(TPF), getVideoTrack: async () => track };
let markerList = [];
seq.guid = "seq-1";
const project = {
  lockedAccess: (cb) => cb(),
  executeTransaction: (cb, undo) => {
    cb({ addAction: (a) => { if (a.kind === "rename") a.target.name = a.name; else { if (a.apply) a.apply(); calls.added.push(a); } } });
    calls.undo = undo;
    return true;
  },
  getActiveSequence: async () => seq,
  getSequences: async () => [seq],
};
const hooks = {};
const fire = (name) => (hooks[name] || []).forEach((fn) => fn());
class AddTransitionOptions {
  constructor() { this.p = {}; }
  setDuration(t) { this.p.duration = t; return this; }
  setApplyToStart(v) { this.p.applyToStart = v; return this; }
  setForceSingleSided(v) { this.p.single = v; return this; }
  setTransitionAlignment(v) { this.p.alignment = v; return this; }
}
const ppro = {
  Constants: { TrackItemType: { CLIP: "CLIP", TRANSITION: "TRANSITION" }, ProjectEvent: { OPENED: "p.opened", ACTIVATED: "p.activated", DIRTY: "p.dirty" }, SequenceEvent: { ACTIVATED: "s.activated" }, OperationCompleteEvent: { IMPORT_MEDIA_COMPLETE: "o.import" } },
  EventManager: { addGlobalEventListener: (name, fn) => { (hooks[name] = hooks[name] || []).push(fn); } },
  Markers: { getMarkers: async () => ({ getMarkers: async () => markerList }) },
  VideoFilterFactory: {
    getMatchNames: async () => ["AE.Impact Move FX", "AE.ADBE Mosaic"],
    getDisplayNames: async () => ["Impact Move FX", "Mosaic"],
    createComponent: async (n) => { calls.components = (calls.components || []).concat(n); return { label: n.replace("AE.", "") }; },
  },
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
global.document.getElementById("auto").checked = true;
require(path.join(__dirname, "index.js"));
const log = () => els.log.value;
const tick = () => new Promise((r) => setTimeout(r, 20));

(async () => {
  await handlers.list(); await tick();
  assert(log().includes("4 video transition match names"), "counts all names");
  assert(log().includes("2 contain \"impact\""), "filters by text");
  const names = calls.saved.filter((s) => s[0] === "transition_match_names.txt");
  assert(names.length === 1 && names[0][1].split("\n").length === 4, "saves the full list to a file");
  assert(calls.saved.some((s) => s[0] === "last_run_log.txt" && s[1].includes("4 video transition match names")), "saves the whole panel log after each click");

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

  // a pasted name with a trailing full stop is cleaned; a name that does not exist is refused before Premiere is asked
  calls.created.length = 0; calls.added.length = 0;
  set("name", "AE.Impact Push."); els.log.value = "";
  await handlers.first(); await tick();
  assert.strictEqual(calls.created[0], "AE.Impact Push", "trailing full stop cleaned: " + log());
  assert(log().includes("cleaned the name"));
  calls.created.length = 0; set("name", "AE.Not A Real One"); els.log.value = "";
  await handlers.first(); await tick();
  assert.strictEqual(calls.created.length, 0, "an unknown name never reaches Premiere");
  assert(log().includes("Premiere has no transition named"), log());

  set("filter", "impact"); set("name", ""); els.log.value = "";                    // a partial word in the filter is not a name
  await handlers.first(); await tick();
  assert(log().includes("ERROR") && log().includes("match name"), "an empty name is reported, not thrown");
  // ---- automatic mode: markers in the sequence are the plan ----
  const mark = (frame, name, comment) => {
    const m = { name, comment, frame, getName: async () => m.name, getComments: async () => m.comment, getStart: async () => tt(frame * TPF) };
    m.createSetNameAction = (n) => ({ kind: "rename", target: m, name: n });
    return m;
  };
  const good = mark(100, "PH transition", "AE.Impact Push|6|0.5");
  const noClip = mark(50, "PH transition", "AE.Impact Push|6|0.5");
  const badName = mark(250, "PH transition", "AE.Not Real|6|0.5");
  const other = mark(10, "Comment", "just a marker");
  markerList = [good, noClip, badName, other];
  calls.created.length = 0; calls.added.length = 0; els.log.value = "";
  assert(hooks["p.opened"] && hooks["s.activated"] && hooks["o.import"], "listens for project open, sequence activated and import");
  fire("s.activated");                                                                 // Premiere says a sequence was activated: the panel looks at every sequence after a short wait
  await new Promise((r) => setTimeout(r, 1300));
  assert.deepStrictEqual(calls.created, ["AE.Impact Push"], "one transition: only the good marker is acted on: " + els.log.value);
  assert.strictEqual(calls.added[0].on, "B", "added on the clip that starts at the marker (frame 100)");
  assert.strictEqual(calls.added[0].opts.p.duration.ticks, String(6 * TPF));
  assert.strictEqual(good.name, "PH transition done", "the marker is renamed in the same step");
  assert.strictEqual(noClip.name, "PH transition FAILED", "no clip starts at frame 50: marked failed, not retried");
  assert.strictEqual(badName.name, "PH transition FAILED", "an unknown transition name is marked failed");
  assert.strictEqual(other.name, "Comment", "an unrelated marker is left alone");
  assert(els.log.value.includes("Premiere has no transition named \"AE.Not Real\"") && els.log.value.includes("no clip on V1 starts at frame 50"), "the reasons are logged");
  const n = calls.added.length;
  fire("p.opened"); fire("s.activated");
  await new Promise((r) => setTimeout(r, 1300));
  assert.strictEqual(calls.added.length, n, "nothing is applied twice");
  const again = mark(100, "PH transition", "AE.Impact Push|6|0.5");
  markerList = [again];
  global.document.getElementById("auto").checked = false;                                // the box off: nothing happens
  fire("p.opened");
  await new Promise((r) => setTimeout(r, 1300));
  assert.strictEqual(again.name, "PH transition", "automatic mode off leaves the markers alone");

  // ---- effect probe: Impact Move FX added to both clips at a cut, its settings written out ----
  global.document.getElementById("auto").checked = true;
  calls.added.length = 0; calls.saved.length = 0; els.log.value = "";
  const probe = mark(100, "PH effect probe", "Impact Move");
  markerList = [probe];
  fire("p.opened");
  await new Promise((r) => setTimeout(r, 1300));
  assert.deepStrictEqual(calls.components, ["AE.Impact Move FX", "AE.Impact Move FX"], "the matching effect is created for each clip: " + els.log.value);
  assert.deepStrictEqual(calls.added.filter((a) => a.kind === "append").map((a) => a.on).sort(), ["A", "B"], "appended to the clip ending at the cut (A) and the one starting there (B)");
  assert.strictEqual(probe.name, "PH effect probe done", "the probe marker is renamed so it runs once");
  const params = calls.saved.find((x) => x[0] === "effect_params.txt");
  assert(params && params[1].includes("\"Direction\" = 2 (number), keyframes true"), "the added effect's settings are written out: " + (params && params[1]));
  assert(calls.saved.some((x) => x[0] === "effect_match_names.txt" && x[1].includes("AE.Impact Move FX\tImpact Move FX")), "all effect names are saved");
  const nAdded = calls.added.length;
  fire("p.opened");
  await new Promise((r) => setTimeout(r, 1300));
  assert.strictEqual(calls.added.length, nAdded, "the probe does not run twice");
  console.log("panel logic: all checks passed");
  process.exit(0);
})().catch((e) => { console.error("FAILED:", e.message, "\nlog:\n" + els.log.value); process.exit(1); });
