// Post House Transition Lab: a Premiere Pro UXP panel (Premiere 25.6 or newer).
// Question it answers: can a script inside Premiere list and add the transitions an XML cannot carry (Film Impact, now part of Premiere)?
//  1. List every video transition match name Premiere offers (TransitionFactory.getVideoTransitionMatchNames), written to a file as well.
//  2. Add a chosen one at the first cut, or every cut, of the active sequence (createAddVideoTransitionAction inside one undoable transaction).
// Every step is logged in the panel; any error shows its message and stack instead of failing silently.
const ppro = require("premierepro");
const uxp = require("uxp");

const $ = (id) => document.getElementById(id);
const TICKS_PER_SECOND = 254016000000;

function log(...parts) {
  const el = $("log");
  el.value = (el.value || "") +parts.map((p) => (typeof p === "string" ? p : JSON.stringify(p))).join(" ") + "\n";
  el.scrollTop = el.scrollHeight;
}

async function guard(label, fn) {
  log(`--- ${label}`);
  try {
    await fn();
  } catch (e) {
    log("ERROR:", (e && (e.stack || e.message)) || String(e));
  }
  try {
    await saveText("last_run_log.txt", $("log").value, true); // the whole panel log, so it can be read without a screenshot
  } catch (e) { /* the log file is a convenience */ }
}

async function saveText(fileName, text, quiet) {
  const folder = await uxp.storage.localFileSystem.getDataFolder();
  const file = await folder.createFile(fileName, { overwrite: true });
  await file.write(text);
  if (!quiet) log("saved:", file.nativePath);
}

async function listTransitions() {
  const names = await ppro.TransitionFactory.getVideoTransitionMatchNames();
  log(`${names.length} video transition match names in total`);
  await saveText("transition_match_names.txt", names.join("\n"));
  const f = $("filter").value.trim().toLowerCase();
  const shown = f ? names.filter((n) => n.toLowerCase().includes(f)) : names;
  log(f ? `${shown.length} contain "${f}":` : "all of them:");
  log(shown.join("\n") || "(none)");
}

async function activeSequence() {
  const project = await ppro.Project.getActiveProject();
  if (!project) throw new Error("no active project");
  const seq = await project.getActiveSequence();
  if (!seq) throw new Error("no active sequence: open a sequence first");
  return { project, seq };
}

async function ticksPerFrame(seq) {
  const tb = await seq.getTimebase();
  const n = Number(tb);
  log("sequence timebase reported as:", String(tb));
  return n > 1000 ? n : TICKS_PER_SECOND / n; // ticks per frame, or a frame rate if it came back small
}

async function cutsOnTrack(seq, trackNumber) {
  const track = await seq.getVideoTrack(trackNumber - 1);
  const items = await track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false);
  const rows = [];
  for (const it of items) {
    const s = await it.getStartTime();
    const e = await it.getEndTime();
    rows.push({ it, start: s.ticksNumber, end: e.ticksNumber, name: await it.getName() });
  }
  rows.sort((a, b) => a.start - b.start);
  log(`V${trackNumber}: ${rows.length} clips`);
  const tpf = await ticksPerFrame(seq);
  rows.forEach((r, i) => log(`  clip ${i + 1}: "${r.name}" frames ${Math.round(r.start / tpf)} to ${Math.round(r.end / tpf)}`));
  const cuts = [];
  for (let i = 0; i + 1 < rows.length; i++) {
    if (Math.abs(rows[i].end - rows[i + 1].start) < 1000) cuts.push({ before: rows[i], after: rows[i + 1] });
  }
  log(`${cuts.length} cuts between touching clips`);
  return cuts;
}

function addAt(project, clip, matchName, durationTicks, alignment) {
  let accepted;
  project.lockedAccess(() => {
    accepted = project.executeTransaction((compound) => {
      const transition = ppro.TransitionFactory.createVideoTransition(matchName);
      let opts = new ppro.AddTransitionOptions();
      opts = opts.setDuration(ppro.TickTime.createWithTicks(String(Math.round(durationTicks)))) || opts;
      opts = opts.setApplyToStart(true) || opts;
      opts = opts.setForceSingleSided(false) || opts;
      opts = opts.setTransitionAlignment(alignment) || opts;
      compound.addAction(clip.createAddVideoTransitionAction(transition, opts));
    }, `Add transition ${matchName}`);
  });
  return accepted;
}

function cleanName(raw) {
  return String(raw || "").trim().replace(/^[`'"\s]+/, "").replace(/[`'".,;:\s]+$/, ""); // a pasted name often carries a trailing full stop or quote
}

async function resolveName(raw) {
  const names = await ppro.TransitionFactory.getVideoTransitionMatchNames();
  let typed = cleanName(raw);
  if (!typed) {                                       // a name pasted into the filter box by mistake: use it if it is one of Premiere's names
    const f = cleanName($("filter").value);
    if (f && names.includes(f)) {
      log(`the name box was empty; using "${f}" from the filter box`);
      typed = f;
    }
  }
  if (!typed) throw new Error("type or paste a transition match name into the SECOND box (\"Transition match name\"); list them with button 1");
  if (typed !== String(raw || "").trim()) log(`cleaned the name: "${String(raw).trim()}" -> "${typed}"`);
  if (names.includes(typed)) return typed;
  const ci = names.find((n) => n.toLowerCase() === typed.toLowerCase());
  if (ci) return ci;
  const near = names.filter((n) => n.toLowerCase().includes(typed.toLowerCase().slice(0, Math.max(6, typed.length - 3)))).slice(0, 8);
  throw new Error(`Premiere has no transition named "${typed}".` + (near.length ? ` Close names: ${near.join(", ")}` : " Use button 1 to see the names."));
}

async function apply(onlyFirst) {
  const matchName = await resolveName($("name").value);
  const trackNumber = parseInt($("track").value, 10) || 1;
  const frames = parseFloat($("frames").value) || 6;
  const alignment = parseFloat($("align").value);
  const { project, seq } = await activeSequence();
  log("sequence:", seq.name);
  const tpf = await ticksPerFrame(seq);
  const cuts = await cutsOnTrack(seq, trackNumber);
  if (!cuts.length) throw new Error("no cuts found on that track");
  const todo = onlyFirst ? cuts.slice(0, 1) : cuts;
  for (const c of todo) {
    log(`adding "${matchName}" at the cut into "${c.after.name}", ${frames} frames, alignment ${alignment}`);
    const accepted = addAt(project, c.after.it, matchName, tpf * frames, Number.isNaN(alignment) ? 0.5 : alignment);
    log(`  Premiere ${accepted ? "accepted" : "did NOT accept"} the transaction`);
  }
  const track = await seq.getVideoTrack(trackNumber - 1);
  const trans = await track.getTrackItems(ppro.Constants.TrackItemType.TRANSITION, false);
  log(`done. V${trackNumber} now has ${trans.length} transition item(s). Look at the timeline, then Cmd+Z to undo if you want.`);
}

// ---- automatic mode -----------------------------------------------------------------------------------------------------------------------------
// The XML carries the plan as sequence markers (written by place_sfx.py): one marker per cut, named "PH transition", at the cut's frame, with the comment
// "<match name>|<frames>|<alignment>" (and optionally "|<video track>"). While this panel is loaded it looks at EVERY sequence in the project (not only the open one) whenever
// the project opens, a sequence is activated, media is imported or the project changes, and every few seconds as a fallback. A sequence with pending markers gets each transition added at the clip that starts
// on its marker, and the marker is renamed "PH transition done" in the same undoable step, so nothing is applied twice. A marker it cannot act on becomes "PH transition FAILED" (with the reason in the log).
const PENDING = "PH transition";
const DONE = "PH transition done";
const FAILED = "PH transition FAILED";
let scanning = false;
const handled = new Set(); // sequence guid + marker frame, so a marker that could not be renamed is never retried in a loop

async function markFailed(project, marker) {
  try {
    project.lockedAccess(() => project.executeTransaction((compound) => compound.addAction(marker.createSetNameAction(FAILED)), "Post House: mark transition failed"));
  } catch (e) { log("could not rename the marker:", (e && e.message) || String(e)); }
}

async function runPlan(project, seq, pending, reason) {
  log(`--- automatic (${reason}): "${seq.name}" has ${pending.length} pending transition marker(s)`);
  const names = await ppro.TransitionFactory.getVideoTransitionMatchNames();
  const tpf = await ticksPerFrame(seq);
  let applied = 0;
  for (const m of pending) {
    const start = (await m.getStart()).ticksNumber;
    const key = `${seq.guid}:${Math.round(start / tpf)}`;
    if (handled.has(key)) continue;
    handled.add(key);
    const [rawName, rawFrames, rawAlign, rawTrack] = String(await m.getComments()).split("|");
    const frame = Math.round(start / tpf);
    try {
      const matchName = cleanName(rawName);
      if (!names.includes(matchName)) throw new Error(`Premiere has no transition named "${matchName}"`);
      const frames = parseFloat(rawFrames) || 6;
      const alignment = Number.isNaN(parseFloat(rawAlign)) ? 0.5 : parseFloat(rawAlign);
      const trackNumber = parseInt(rawTrack, 10) || 1;
      const track = await seq.getVideoTrack(trackNumber - 1);
      const items = await track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false);
      let clip = null;
      for (const it of items) {
        if (Math.abs((await it.getStartTime()).ticksNumber - start) < tpf / 2) { clip = it; break; }
      }
      if (!clip) throw new Error(`no clip on V${trackNumber} starts at frame ${frame}`);
      let accepted;
      project.lockedAccess(() => {
        accepted = project.executeTransaction((compound) => {
          const transition = ppro.TransitionFactory.createVideoTransition(matchName);
          let opts = new ppro.AddTransitionOptions();
          opts = opts.setDuration(ppro.TickTime.createWithTicks(String(Math.round(tpf * frames)))) || opts;
          opts = opts.setApplyToStart(true) || opts;
          opts = opts.setForceSingleSided(false) || opts;
          opts = opts.setTransitionAlignment(alignment) || opts;
          compound.addAction(clip.createAddVideoTransitionAction(transition, opts));
          compound.addAction(m.createSetNameAction(DONE));
        }, `Post House: add ${matchName}`);
      });
      if (!accepted) throw new Error("Premiere did not accept the transaction");
      applied++;
      log(`  frame ${frame}: ${matchName}, ${frames} frames, alignment ${alignment}: added`);
    } catch (e) {
      log(`  frame ${frame}: FAILED: ${(e && e.message) || String(e)}`);
      await markFailed(project, m);
    }
  }
  log(`  done: ${applied} of ${pending.length} transitions added in "${seq.name}"`);
  try { await saveText("last_run_log.txt", $("log").value, true); } catch (e) { /* convenience */ }
}

async function scanProject(reason) {
  if (scanning || !$("auto").checked) return;
  scanning = true;
  try {
    const project = await ppro.Project.getActiveProject();
    if (!project) return;
    for (const seq of await project.getSequences()) {
      const owner = await ppro.Markers.getMarkers(seq);
      const pending = [];
      for (const m of (await owner.getMarkers()) || []) {
        if ((await m.getName()) === PENDING) pending.push(m);
      }
      if (pending.length) await runPlan(project, seq, pending, reason);
    }
  } catch (e) {
    log("automatic mode ERROR:", (e && (e.stack || e.message)) || String(e));
    try { await saveText("last_run_log.txt", $("log").value, true); } catch (e2) { /* convenience */ }
  } finally {
    scanning = false;
  }
}

function startAuto() {
  const hooks = [["ProjectEvent", "OPENED"], ["ProjectEvent", "ACTIVATED"], ["ProjectEvent", "DIRTY"], ["SequenceEvent", "ACTIVATED"], ["OperationCompleteEvent", "IMPORT_MEDIA_COMPLETE"]];
  for (const [group, name] of hooks) {
    try {
      ppro.EventManager.addGlobalEventListener(ppro.Constants[group][name], () => setTimeout(() => scanProject(`${group}.${name}`), 800), false);
      log(`listening for ${group}.${name}`);
    } catch (e) {
      log(`could not listen for ${group}.${name}: ${(e && e.message) || String(e)}`);
    }
  }
  setInterval(() => scanProject("timer"), 3000);
  setTimeout(() => scanProject("panel loaded"), 1500);
}

// ---- probe: what settings does an added transition expose (e.g. the Push's direction)? ---------------------------------------------------------
// For every transition on the video tracks of the open sequence: its name, then each component and parameter with its current value. Written to transition_params.txt.
async function describeTransitions() {
  const { seq } = await activeSequence();
  const lines = [`sequence: ${seq.name}`];
  const nTracks = await seq.getVideoTrackCount();
  for (let t = 0; t < nTracks; t++) {
    const track = await seq.getVideoTrack(t);
    const items = await track.getTrackItems(ppro.Constants.TrackItemType.TRANSITION, false);
    for (const it of items) {
      let head = `V${t + 1} transition`;
      try { head += ` "${await it.getName()}" match ${await it.getMatchName()} at ${(await it.getStartTime()).seconds.toFixed(3)}s`; } catch (e) { head += ` (name: ${e.message})`; }
      lines.push(head);
      try {
        const chain = await it.getComponentChain();
        const n = await chain.getComponentCount();
        for (let c = 0; c < n; c++) {
          const comp = await chain.getComponentAtIndex(c);
          const pc = await comp.getParamCount();
          lines.push(`  component ${c}: "${await comp.getDisplayName()}" (${await comp.getMatchName()}), ${pc} params`);
          for (let p = 0; p < pc; p++) {
            const prm = await comp.getParam(p);
            let val;
            try { const kf = await prm.getStartValue(); val = JSON.stringify(kf && (kf.value !== undefined ? kf.value : kf)); } catch (e) { val = `(value: ${e.message})`; }
            lines.push(`    param ${p}: "${prm.displayName}" = ${val}`);
          }
        }
      } catch (e) {
        lines.push(`  could not read its settings: ${(e && e.message) || String(e)}`);
      }
    }
  }
  if (lines.length === 1) lines.push("(no transitions on the video tracks)");
  log(lines.join("\n"));
  await saveText("transition_params.txt", lines.join("\n"), true);
}

async function noteStartup() {
  try {
    const h = uxp.host || {};
    await saveText("started.txt", `${new Date().toISOString()} loaded in ${h.name || "?"} ${h.version || "?"}\n`, true);
  } catch (e) { /* convenience */ }
}

$("describe").addEventListener("click", () => guard("describe transitions", describeTransitions));
$("list").addEventListener("click", () => guard("list transitions", listTransitions));
$("first").addEventListener("click", () => guard("add at first cut", () => apply(true)));
$("all").addEventListener("click", () => guard("add at every cut", () => apply(false)));
log("Transition Lab v0.6: automatic mode. Sequences with \"PH transition\" markers get their transitions without a click (tick the box to turn it on or off).");
noteStartup();
startAuto();
// the probe runs whenever a sequence is activated or a project opens (at load there is usually no project yet); quietly, only when there are transitions to describe
let describedFor = "";
async function describeQuietly(reason) {
  try {
    const project = await ppro.Project.getActiveProject();
    if (!project) return;
    const seq = await project.getActiveSequence();
    if (!seq) return;
    let n = 0;
    const nTracks = await seq.getVideoTrackCount();
    for (let t = 0; t < nTracks; t++) n += ((await (await seq.getVideoTrack(t)).getTrackItems(ppro.Constants.TrackItemType.TRANSITION, false)) || []).length;
    const key = `${seq.guid}:${n}`;
    if (!n || key === describedFor) return;
    describedFor = key;
    await guard(`describe transitions (${reason})`, describeTransitions);
  } catch (e) { /* the probe is a diagnostic; it never interrupts anything */ }
}
for (const [group, name] of [["SequenceEvent", "ACTIVATED"], ["ProjectEvent", "OPENED"], ["ProjectEvent", "ACTIVATED"]]) {
  try { ppro.EventManager.addGlobalEventListener(ppro.Constants[group][name], () => setTimeout(() => describeQuietly(`${group}.${name}`), 1500), false); } catch (e) { /* logged by startAuto already */ }
}
setInterval(() => describeQuietly("timer"), 10000);
