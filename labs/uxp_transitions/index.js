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

$("list").addEventListener("click", () => guard("list transitions", listTransitions));
$("first").addEventListener("click", () => guard("add at first cut", () => apply(true)));
$("all").addEventListener("click", () => guard("add at every cut", () => apply(false)));
log("Transition Lab v0.3 (saves last_run_log.txt after every click). Start with: List transitions.");
