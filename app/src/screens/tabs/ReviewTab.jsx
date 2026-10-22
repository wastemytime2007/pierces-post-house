import { useCallback, useEffect, useRef, useState } from "react";
import Icon from "../../components/Icon.jsx";
import { useThumb, requestFrames, cutFrameItem } from "../../thumbStore.js";
import { open as openDialog } from "@tauri-apps/plugin-dialog";

import { sendCommand } from "../../App.jsx";

/**
 * 02 · Review. The review page itself (labs/review_loop: the video, every edit decision on a timeline, timecoded notes, drawing on the frame) shown in the app,
 * with the loop around it:
 *
 *   the cut opens  ->  the AI editor reviews it, submits the fixes it can make, reviews the new version, and repeats until nothing fixable is left (creator_tools.auto_edit);
 *   every version and every AI note is visible here while it works, and Stop is always there  ->  you review the version it names (leave your own notes and "Submit changes" any
 *   time; the editor then carries on from there)  ->  "Export XML" (checked by the export check, then opened in Premiere for final touches).
 *
 * The page is served by the backend on 127.0.0.1 (creator_tools.serve_review) and framed here; the page and this tab talk by postMessage.
 */
const STATUS_LABEL = { "VERIFIED": "Verified", "APPLIED-UNMEASURED": "Applied, look at it", "NOT DONE": "Not done", "FAILED": "Failed" };
const STATUS_CLASS = { "VERIFIED": "ok", "APPLIED-UNMEASURED": "warn", "NOT DONE": "warn", "FAILED": "bad" };
// Creative stage (Ryan, 2026-10-21): the XML that comes in is already the cut he trimmed in Premiere. This tab adds to it (graphics, captions, music, bleep, transitions and sound) and sends it back
// to Premiere. It never reviews the story, never fixes or rebuilds the cut, never starts the AI editor. The editing machinery below stays in the file, switched off by this one flag.
const CREATIVE_STAGE = true;
const fmtDur = (s) => { const m = Math.round(s / 60); return m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min` : `${m} min`; };
const isAutoSave = (bin) => /auto-?save/i.test(bin || "");
const fileName = (p) => (p || "").split("/").pop();
const dirName = (p) => (p || "").split("/").slice(0, -1).join("/");

// The frame that stands for a cut: from its finished version when it has one, so the graphics show. A color tile with a film icon until it arrives.
function CutThumb({ xml, className = "" }) {
  const u = useThumb(`cut:${xml}`);
  return <span className={`cut-thumb ${className} ${u ? "" : "empty"}`} style={u ? { backgroundImage: `url(${u})` } : undefined} aria-hidden="true">{!u && <Icon name="film" size={14} />}</span>;
}

// File names read as words: underscores become spaces and a split contraction is put back (someone_s -> someone's)
const pretty = (n) => String(n).replace(/_/g, " ").replace(/(\w) (s|t|re|ve|ll|d)(?= |$)/g, "$1'$2");

export default function ReviewTab({ subscribe, onStatus }) {
  const frameRef = useRef(null);
  const versionsRef = useRef([]);
  const busyRef = useRef("");
  const buildRef = useRef(null);
  const openReviewRef = useRef(null);
  const makingRef = useRef("");
  const [making, setMaking] = useState(""); // the cut is being made from the ideas picked on the Ideas tab ("Review this cut"): what it is doing now
  // One XML that holds many sequences (a Premiere SEQS bin exported in one go): listed here, each one opened is cut out into its own XML and reviewed like any single cut.
  const [graphicCount, setGraphicCount] = useState(0);              // graphics moved or scaled on the page and not yet made into a version
  const [reading, setReading] = useState("");                      // the XML being read for its sequences
  const [creative, setCreative] = useState(null);                // Get Creative's batch: { running, state: { state, started, items: [{ xml, name, status, step, message, final_xml }] } }; the truth is on disk (get_creative.json), this is what the tab last heard
  const [redoOpt, setRedoOpt] = useState(false);                  // start these over from the original (not remembered between runs)
  const [creativeHidden, setCreativeHidden] = useState("");         // the started-stamp of a finished batch the person hid
  const [picker, setPicker] = useState(null);                      // { xml, rows, selected: [ids], filter, hideAuto }
  const [seqSet, setSeqSet] = useState(() => {                   // the sequences opened from one XML survive leaving the tab and coming back
    try { return JSON.parse(localStorage.getItem("review.seqSet") || "null"); } catch (e) { return null; }
  });
                     // { source, items: [{ id, name, xml }] }: the sequences opened from one XML, to switch between
  const [incoming, setIncoming] = useState(""); // an export made while a review is open: offered, never swapped in under the notes being written
  const [exportsList, setExportsList] = useState([]);
  const [versions, setVersions] = useState([]); // [{label, xml, folder, url, qa}]
  const [cur, setCur] = useState(0);
  const [noteCount, setNoteCount] = useState(0);
  const [busy, setBusy] = useState(""); // "" | building | applying
  const [stage, setStage] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [exportResult, setExportResult] = useState(null);
  const [showLedger, setShowLedger] = useState(true);
  // The AI review of each version (keyed by its label): { status: running | done | failed, stage, result }. Its findings are posted onto the page as notes once the page is ready.
  const [ai, setAi] = useState({});
  const [showAi, setShowAi] = useState(true);
  const aiPending = useRef({});
  const readyRef = useRef(false);
  const curRef = useRef(0);
  const flushRef = useRef(null);
  const submitRef = useRef(null);
  const aiStartRef = useRef(null);
  const aiOnlyRef = useRef(null);
  const openedRef = useRef(false); // the last session was reopened (or there was none) once for this mount
  const autoOnRef = useRef(true);
  const [autoOn, setAutoOn] = useState(() => {
    if (CREATIVE_STAGE) return false;
    try { return localStorage.getItem("review.autoEdit") !== "off"; } catch (e) { return true; }
  });
  // The AI editor's own loop: { status: idle | running | done, round, of, label, fixing, summary, best, left, versions }
  const [auto, setAuto] = useState({ status: "idle" });
  const [showLeft, setShowLeft] = useState(true);
  // What the AI editor is doing right now, for the status strip: which of its four steps, since when, and a feed of what it has done.
  const [step, setStep] = useState(0); // 0 review, 1 submit fixes, 2 check, 3 new version
  const [stepSince, setStepSince] = useState(Date.now());
  const [now, setNow] = useState(Date.now());
  const [activity, setActivity] = useState([]); // [{t, text, kind}]
  const [showFeed, setShowFeed] = useState(true);
  // Which Premiere to open XMLs in: every install is listed by the backend (stable and Beta); with more than one the first click asks which, and a remembered choice is used after that.
  const [premiereApps, setPremiereApps] = useState([]);
  const [premiereChoice, setPremiereChoice] = useState(() => {
    try { return localStorage.getItem("review.premiere") || ""; } catch (e) { return ""; }
  });
  const [choosing, setChoosing] = useState(false);
  const [finishing, setFinishing] = useState(false);
  useEffect(() => { if (seqSet?.items?.length) requestFrames(seqSet.items.map((sq) => cutFrameItem(sq.xml)), 200); }, [seqSet]);
  useEffect(() => { if (exportsList?.length) requestFrames(exportsList.slice(0, 8).map((x) => cutFrameItem(x.path)), 420); }, [exportsList]);
  const [scopeAll, setScopeAll] = useState(true);                  // Get Creative: every sequence opened, or only this one
  const [menuOpen, setMenuOpen] = useState(false);                 // the More menu
  const keysRef = useRef({});                                      // the latest handlers for the keyboard shortcuts (set below, after they exist)
  useEffect(() => {
    const shown = () => document.querySelector(".review-tab")?.offsetParent !== null;     // the tab stays mounted while another tab is showing
    const onKey = (e) => {
      if (!shown()) return;
      if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return;
      const k = keysRef.current;
      const fire = (fn) => { if (fn) { e.preventDefault(); fn(); } };
      if (e.key === "o") fire(k.open);
      else if (e.key === "e") fire(k.premiere);
      else if (e.key === "g") fire(k.creative);
      else if (e.key === "[") fire(k.older);
      else if (e.key === "]") fire(k.newer);
    };
    const onMenu = (e) => {
      if (!shown()) return;
      const k = keysRef.current;
      ({ open_xml: k.open, open_premiere: k.premiere, get_creative: k.creative, older_version: k.older, newer_version: k.newer }[e.detail] || (() => {}))();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("posthouse-menu", onMenu);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("posthouse-menu", onMenu); };
  }, []);
  const [finishOpts, setFinishOpts] = useState(() => {
    try { return { fillers: true, captions: true, music: true, bleep: true, graphics: true, sfx: true, ...JSON.parse(localStorage.getItem("review.finish") || "{}") }; } catch (e) { return { captions: true, music: true, bleep: true, graphics: true, sfx: true }; }
  });
  const [autoFinish, setAutoFinish] = useState(() => {
    try { return localStorage.getItem("review.autoFinish") !== "off"; } catch (e) { return true; }
  });
  const [direction, setDirection] = useState("");
  const directionRef = useRef("");
  directionRef.current = direction;
  const dirKey = (root) => `review.direction:${root || ""}`;
  const autoFinishRef = useRef(autoFinish);
  autoFinishRef.current = autoFinish;
  const finishOptsRef = useRef(finishOpts);
  finishOptsRef.current = finishOpts;
  const [remember, setRemember] = useState(true);
  const lastStageRef = useRef("");

  const version = versions[cur];
  autoOnRef.current = autoOn;
  versionsRef.current = versions;
  busyRef.current = busy;
  curRef.current = cur;
  makingRef.current = making;

  useEffect(() => { readyRef.current = false; }, [version?.url]); // a new page is loading: wait for it to say it is ready

  useEffect(() => {
    if (!onStatus) return;
    onStatus(
      auto.status === "running" ? "AI editor working…"
      : making ? "making the cut…"
      : busy === "building" ? "building the cut…"
      : busy === "applying" ? "applying notes…"
      : incoming ? "new export ready"
      : versions.length ? `${versions[cur]?.label || "V1"} open`
      : exportsList.length ? "cut ready" : ""
    );
  }, [onStatus, busy, incoming, versions, cur, exportsList, making, auto.status]);

  useEffect(() => {
    sendCommand({ type: "list_exports" }).catch(() => {});
    sendCommand({ type: "premiere_apps" }).catch(() => {});
  }, []);

  useEffect(() => {
    return subscribe((ev) => {
      if (ev.batch) return;                                                  // Get Creative's own events (another sequence's review and versions): not the review on screen; its sessions are on disk
      if (ev.type === "premiere_apps") {
        setPremiereApps(ev.apps || []);
      } else if (ev.type === "exports_listed") {
        setExportsList(ev.exports || []);
        if (!openedRef.current && !versionsRef.current.length && !busyRef.current && !makingRef.current) {
          openedRef.current = true;
          const worked = (ev.exports || []).filter((x) => x.session).sort((a, b) => (b.session.updated || 0) - (a.session.updated || 0))[0];
          if (worked) openReviewRef.current?.(worked.path);               // the cut you were last reviewing, as you left it
        }
      } else if (ev.type === "review_session_loaded") {
        setBusy("");
        try { setDirection(localStorage.getItem(dirKey(ev.root)) || ""); directionRef.current = localStorage.getItem(dirKey(ev.root)) || ""; } catch (e) { /* private window */ }
        const vs = ev.versions.map((v) => ({ label: v.label, xml: v.xml, folder: v.folder, url: v.url, qa: v.qa, applied: v.applied, notes: v.notes }));
        setVersions(vs);
        const aiState = {};
        aiPending.current = {};
        for (const [tag, r] of Object.entries(ev.ai || {})) {
          aiState[tag] = { status: "done", result: { ...r, notes: r.notes || [], checks: r.checks || [] } };
          aiPending.current[tag] = r.notes || [];                       // put the AI's notes back on the page (a new run replaces only AI notes, never yours)
        }
        setAi(aiState);
        const a = ev.auto;
        if (ev.auto_running) setAuto({ status: "running", round: 0, of: 4, label: ev.auto_tag || vs[vs.length - 1].label });
        else if (a) setAuto({ status: "done", summary: a.summary, best: a.best, left: a.left || [], versions: a.versions || [], rounds: a.rounds || [], outcome: a.status });
        else setAuto({ status: "idle" });
        const bestIdx = a && a.best ? vs.findIndex((v) => v.label === a.best) : -1;
        // A version the editor did not make is YOURS (its notes from you): reopening lands on the latest of them, not on the version the editor named before you went on (2026-10-13: the tab reopened on the
        // editor's V5 while V6 and V7 were yours, and "Open in Premiere" opened V5).
        const editorMade = new Set(((a && a.versions) || []).map((v) => v.label));
        const yours = a ? vs.filter((v) => !editorMade.has(v.label)) : [];
        setCur(yours.length ? vs.length - 1 : bestIdx >= 0 ? bestIdx : vs.length - 1);
        setNoteCount(0);
        setExportResult(null);
        setIncoming("");
        setInfo("");
        if (ev.stale && !CREATIVE_STAGE) setInfo("This review was made by an older version of the AI editor, so it does not have what has been fixed since (for example cutting an off-camera voice, or finishing with graphics and sound). Press Start over to run the current editor on this cut.");
        const interrupted = !ev.auto_running && !a && autoOnRef.current && vs.length > 0;          // the AI editor never reported how it ended: the app was closed or restarted while it worked
        setActivity([{ t: Date.now(), kind: "info", text: interrupted
          ? `Reopened your earlier review of this cut (${vs.map((v) => v.label).join(", ")}). The AI editor did not finish last time (the app was closed or restarted while it worked), so it is picking up from ${vs[vs.length - 1].label}.`
          : `Reopened your earlier review of this cut (${vs.map((v) => v.label).join(", ")}). Nothing was rebuilt or reviewed again.` }]);
        if (interrupted) {
          const last = vs[vs.length - 1];
          setAuto({ status: "running", round: 0, of: 4, label: last.label });
          aiStartRef.current?.(last.xml, last.folder, last.label, vs[0].xml);
        }
      } else if (ev.type === "review_cut_started") {
        setError("");
        setMaking(`Making the cut${ev.of > 1 ? ` (${ev.n} of ${ev.of})` : ""} from the idea…`);
      } else if (makingRef.current && ev.type === "export_matching") {
        setMaking("Matching the idea to your footage…");
      } else if (makingRef.current && ev.type === "export_sync_started") {
        setMaking("Syncing the voice recorders to the footage…");
      } else if (makingRef.current && ev.type === "export_writing") {
        setMaking("Writing the cut…");
      } else if (makingRef.current && ev.type === "export_error") {
        setMaking("");
        setError(ev.message || "The cut could not be made.");
      } else if (ev.type === "export_complete" && ev.xml_path) {
        setMaking("");
        // The app just wrote an XML. Review it without being asked, unless a review is already open or building.
        const row = { path: ev.xml_path, name: fileName(ev.xml_path), mtime: Date.now() / 1000 };
        setExportsList((l) => [row, ...l.filter((x) => x.path !== row.path)]);
        if (!versionsRef.current.length && !busyRef.current) buildRef.current?.(ev.xml_path);
        else setIncoming(ev.xml_path);
      } else if (ev.type === "review_started") {
        setBusy("building");
        setError("");
        setInfo("");
      } else if (ev.type === "ai_review_started") {
        setAi((a) => ({ ...a, [ev.tag]: { status: "running", stage: "Starting…" } }));
      } else if (ev.type === "ai_review_stage") {
        setAi((a) => ({ ...a, [ev.tag]: { ...(a[ev.tag] || {}), status: "running", stage: ev.stage } }));
      } else if (ev.type === "ai_review_done") {
        setAi((a) => ({ ...a, [ev.tag]: { status: "done", result: ev } }));
        aiPending.current[ev.tag] = ev.notes || [];
        flushRef.current?.();
      } else if (ev.type === "ai_review_failed") {
        setAi((a) => ({ ...a, [ev.tag]: { status: "failed", message: ev.message } }));
      } else if (ev.type === "agent_edit_started") {
        setAuto({ status: "running", round: 1, of: 1, label: ev.tag, fixing: ev.notes, agent: true });
      } else if (ev.type === "auto_edit_started") {
        setAuto({ status: "running", round: 0, of: ev.max_rounds, label: ev.tag });
      } else if (ev.type === "auto_edit_round") {
        setAuto((a) => ({ ...a, status: "running", round: ev.round, of: ev.of, label: ev.label, fixing: ev.fixing }));
      } else if (ev.type === "auto_edit_done") {
        setAuto({ status: "done", summary: ev.summary, best: ev.best, left: ev.left || [], versions: ev.versions || [], rounds: ev.rounds || [], outcome: ev.status });
        const idx = parseInt(String(ev.best || "V1").slice(1), 10) - 1;               // open the version the editor names as the one to review
        if (idx >= 0 && idx < versionsRef.current.length) setCur(idx);
      } else if (ev.type === "review_built") {
        setBusy("");
        try { setDirection(localStorage.getItem(dirKey(ev.xml)) || ""); directionRef.current = localStorage.getItem(dirKey(ev.xml)) || ""; } catch (e) { /* private window */ }
        aiPending.current = {};
        setAi({});
        setAuto(autoOnRef.current ? { status: "running", round: 0, of: 4, label: "V1" } : { status: "idle" });      // the editor starts the moment the cut opens: do not flash "your turn" before its first event
        aiStartRef.current?.(ev.xml, ev.folder, "V1", ev.xml);
        setVersions([{ label: "V1", xml: ev.xml, folder: ev.folder, url: ev.url, qa: null }]);
        setCur(0);
        setNoteCount(0);
        setExportResult(null);
      } else if (ev.type === "review_failed") {
        setBusy("");
        setError(ev.message || "The review page could not be built.");
      } else if (ev.type === "notes_started") {
        setBusy("applying");
        setStage("Starting…");
        setError("");
        setInfo("");
      } else if (ev.type === "notes_stage") {
        setStage(ev.stage || "");
      } else if (ev.type === "notes_applied") {
        setBusy("");
        if (!ev.xml) {
          setInfo(ev.message || "Nothing in the notes could be applied to the timeline.");
          return;
        }
        if (!ev.auto) {                                                                                  // YOUR revision is the end of the run: made as you asked, and the AI editor does not start cutting again (it did, on 2026-10-12: it dropped two more clips after a caption fix)
          setAuto({ status: "done", summary: "Your notes were made on this version. The AI editor has left it alone: press \"Let the AI editor continue\" on the version if you want it to review and edit this one too.", left: [], versions: [], rounds: [] });
        }
        setVersions((vs) => {
          const next = [...vs, { label: `V${vs.length + 1}`, xml: ev.xml, folder: ev.folder, url: ev.url, qa: ev.qa, applied: ev.applied, notes: ev.notes }];
          setCur(next.length - 1);
          return next;
        });
        setNoteCount(0);
        setGraphicCount(0);
        setExportResult(null);
        setShowLedger(true);
        if (ev.message) setInfo(ev.message);                                                              // e.g. the layers that were put back, or why they could not be
      } else if (ev.type === "notes_failed") {
        setBusy("");
        setError(ev.message || "The notes could not be applied.");
      } else if (ev.type === "xml_exported") {
        setExportResult(ev);
      } else if (ev.type === "xml_export_failed") {
        setError(ev.message || "The export failed.");
      } else if (ev.type === "sequences_listed") {
        setReading("");
        if (ev.sequences.length <= 1) {                                      // one cut (or none the reader recognises): opens exactly as it always did
          buildRef.current?.(ev.xml);
        } else {
          setPicker({ xml: ev.xml, rows: ev.sequences, selected: [], filter: "", hideAuto: true });
        }
      } else if (ev.type === "sequences_extracted") {
        setPicker(null);
        setSeqSet({ source: ev.xml, items: ev.sequences.map((s) => ({ id: s.id, name: s.name, xml: s.xml })) });
        if (ev.sequences[0]) openReviewRef.current?.(ev.sequences[0].xml);
      } else if (ev.type === "creative_started") {
        setCreative({ running: true, state: { state: "running", items: ev.items } });
      } else if (ev.type === "creative_progress") {
        setCreative((c) => c && { ...c, running: true, state: { ...c.state, items: c.state.items.map((i) => (i.xml === ev.xml ? { ...i, status: "running", step: ev.step } : i)) } });
      } else if (ev.type === "creative_item") {
        setCreative((c) => c && { ...c, state: { ...c.state, items: c.state.items.map((i) => (i.xml === ev.xml ? { ...i, status: ev.status, step: "", message: ev.message, final_xml: ev.final_xml } : i)) } });
        if (ev.status === "done" && versionsRef.current[0]?.xml === ev.xml) openReviewRef.current?.(ev.xml);          // the sequence on screen has new versions: show them
      } else if (ev.type === "creative_batch_done") {
        setCreative({ running: false, state: { state: ev.state, items: ev.items } });
        setSeqSet((s) => (s && ev.items.some((i) => i.xml === s.items?.[0]?.xml)) || ev.items.length < 2 ? s : { source: ev.xml, items: ev.items.map((i) => ({ id: i.xml, name: i.name, xml: i.xml })) });
      } else if (ev.type === "creative_failed") {
        setCreative((c) => c && { ...c, running: false });
        setError(ev.message || "Get Creative could not start.");
      } else if (ev.type === "creative_status") {
        setCreative(ev.state ? { running: !!ev.running, state: ev.state } : null);
      } else if (ev.type === "sequences_failed") {
        setReading("");
        setError(ev.message || "The sequences could not be read.");
      }
    });
  }, [subscribe]);

  // The status strip's feed: every step the editor takes is written down with the time, so it is always clear whether the app is working or waiting for you.
  useEffect(() => {
    const say = (text, kind = "info") => setActivity((a) => [...a.slice(-39), { t: Date.now(), text, kind }]);
    const go = (i) => { setStep(i); setStepSince(Date.now()); };
    return subscribe((ev) => {
      if (ev.batch) return;                                                  // Get Creative's own events (another sequence's review and versions): not the review on screen; its sessions are on disk
      if (ev.type === "review_built") {
        setActivity([]);
        say(`The cut is open as V1 (${ev.clips} clips, ${ev.duration} s).`);
      } else if (ev.type === "agent_edit_started") {
        say(`A Claude agent started on the ${ev.notes} note${ev.notes === 1 ? "" : "s"} the editor could not make (${ev.tag}). It reads the cut, tries changes through the same checks and reads what is refused; this takes several minutes.`, "ai");
      } else if (ev.type === "auto_edit_started") {
        say(`The AI editor started on ${ev.tag}.`, "ai");
      } else if (ev.type === "ai_review_started") {
        go(0);
        say(`Reviewing ${ev.tag}: cut edges, voice recorder, story.`, "ai");
      } else if (ev.type === "ai_review_done") {
        const fixable = (ev.notes || []).filter((n) => n.suggested_op).length;
        say(`Reviewed ${ev.tag}: ${ev.notes.length} note${ev.notes.length === 1 ? "" : "s"}, ${fixable} the editor can try to fix. They are on the page.`, "ai");
      } else if (ev.type === "ai_review_failed") {
        say(`The review of ${ev.tag} could not run: ${String(ev.message || "").split("\n")[0].slice(0, 160)}`, "warn");
      } else if (ev.type === "auto_edit_round") {
        go(1);
        say(`Round ${ev.round} of ${ev.of}: submitting ${ev.fixing} fix${ev.fixing === 1 ? "" : "es"} to ${ev.label}.`, "ai");
      } else if (ev.type === "notes_started" && !ev.auto) {
        go(1);
        say("You submitted your notes. The editor is making the changes.", "you");
      } else if (ev.type === "notes_stage" && ev.stage && ev.stage !== lastStageRef.current) {
        lastStageRef.current = ev.stage;
        if (/Checking every note/i.test(ev.stage)) go(2);
        else go(1);
        say(ev.stage.replace(/\s*\(.*$/, ""), "ai");
      } else if (ev.type === "notes_applied") {
        lastStageRef.current = "";
        go(3);
        const next = ev.xml ? `Built the next version (${ev.applied} of ${ev.notes} change${ev.notes === 1 ? "" : "s"} applied).` : (ev.message || "None of the changes could be applied.");
        say(`The editor finished: ${next}`, ev.xml ? "ai" : "warn");
      } else if (ev.type === "notes_failed") {
        lastStageRef.current = "";
        say(`A change was refused: ${String(ev.message || "").split("\n").find((l) => /REFUSING|FAIL/.test(l)) || String(ev.message || "").split("\n")[0]}`.slice(0, 220), "warn");
      } else if (ev.type === "auto_edit_done") {
        say(ev.summary || "The AI editor finished.", ev.status === "clean" || ev.status === "left" || ev.status === "limit" ? "ai" : "warn");
      }
    });
  }, [subscribe]);

  const working = auto.status === "running" || busy === "applying" || busy === "building" || !!making;
  useEffect(() => {
    if (!working) return undefined;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [working]);

  // The page tells us how many notes it holds.
  useEffect(() => {
    const onMsg = (e) => {
      if (frameRef.current && e.source === frameRef.current.contentWindow && e.data?.type === "review:submit") {
        submitRef.current?.();
        return;
      }
      if (frameRef.current && e.source === frameRef.current.contentWindow && e.data?.type === "review:graphics") {
        setGraphicCount(e.data.n || 0);
        return;
      }
      if (frameRef.current && e.source === frameRef.current.contentWindow && e.data?.type === "review:count") {
        setNoteCount(e.data.n || 0);
        readyRef.current = true;
        flushRef.current?.();
      }
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, []);

  const build = useCallback(async (xml) => {
    if (!xml) return;
    setBusy("building");
    setError("");
    try {
      await sendCommand({ type: "build_review", xml });
    } catch (e) {
      setBusy("");
      setError(String(e));
    }
  }, []);

  buildRef.current = build;

  // Open a cut the way it was left: the backend returns its saved session (every version, the AI reviews, the editor's conclusion) or, if there is none, builds it as a new review.
  const openReview = useCallback(async (xml, fresh = false) => {
    if (!xml) return;
    setBusy("building");
    setError("");
    try {
      await sendCommand({ type: "open_review", xml, ...(fresh ? { fresh: true } : {}) });
    } catch (e) {
      setBusy("");
      setError(String(e));
    }
  }, []);
  openReviewRef.current = openReview;

  // Post the AI review's notes onto the page for the version being shown, once the page has said it is ready.
  flushRef.current = () => {
    const v = versionsRef.current[curRef.current];
    const notes = v && aiPending.current[v.label];
    if (!notes || !readyRef.current || !frameRef.current?.contentWindow) return;
    delete aiPending.current[v.label];
    frameRef.current.contentWindow.postMessage({ type: "review:add-notes", notes }, "*");
  };

  aiOnlyRef.current = (xml, folder, tag, root) => {
    if (CREATIVE_STAGE) return;
    sendCommand({ type: "ai_review", xml, folder, tag, root: root || versionsRef.current[0]?.xml || xml, direction: directionRef.current }).catch((e) => setAi((a) => ({ ...a, [tag]: { status: "failed", message: String(e) } })));
  };

  // A new version (the cut just built, or one made from your notes): the AI editor takes it from here, unless it is switched off, then only the review runs.
  aiStartRef.current = (xml, folder, tag, root) => {
    if (CREATIVE_STAGE) return;
    if (autoOnRef.current) {
      sendCommand({ type: "auto_edit", xml, folder, tag, root: root || versionsRef.current[0]?.xml || xml, finish: autoFinishRef.current ? finishOptsRef.current : null, direction: directionRef.current }).catch((e) => setAuto({ status: "done", summary: `The AI editor could not start: ${e}`, left: [], versions: [], rounds: [] }));
    } else {
      aiOnlyRef.current?.(xml, folder, tag, root);
    }
  };

  const browse = useCallback(async () => {
    const p = await openDialog({ multiple: false, filters: [{ name: "Premiere XML", extensions: ["xml"] }] });
    if (typeof p !== "string") return;
    setError("");
    setReading(p);
    try {
      await sendCommand({ type: "list_sequences", xml: p });                 // one cut opens at once; many open the picker
    } catch (e) {
      setReading("");
      setError(String(e.message || e));
    }
  }, []);

  useEffect(() => {
    try { if (seqSet) localStorage.setItem("review.seqSet", JSON.stringify(seqSet)); } catch (e) { /* private window */ }
  }, [seqSet]);
  const creativeKey = seqSet?.items?.[0]?.xml || versions[0]?.xml || "";
  useEffect(() => {
    if (creativeKey) sendCommand({ type: "creative_status", xml: creativeKey }).catch(() => {});      // a batch left running, or finished while the tab was away
  }, [creativeKey]);

  // Graphics moved or scaled on the page (drag the box, scale it) become the cut's next version: Basic Motion on each graphic's own clip, nothing redrawn.
  const applyGraphics = async () => {
    if (!version) return;
    setError("");
    setInfo("");
    try {
      const payload = await askNotes();
      const edits = payload?.graphic_edits || [];
      if (!edits.length) { setInfo("No graphic has been moved or scaled on the page."); return; }
      await sendCommand({ type: "edit_graphics", xml: version.xml, edits, root: versionsRef.current[0].xml, label: `V${versionsRef.current.length + 1}` });
    } catch (e) {
      setBusy("");
      setError(String(e.message || e));
    }
  };

  const startCreative = async (xmls) => {
    if (!xmls.length) return;
    try { localStorage.setItem("review.finish", JSON.stringify(finishOpts)); } catch (e) { /* private window */ }
    setFinishing(false);
    setError("");
    setInfo("");
    try {
      await sendCommand({ type: "get_creative", xmls, opts: { ...finishOpts, redo: redoOpt } });
      setRedoOpt(false);
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  function CreativePanel() {
    const st = creative?.state;
    if (!st || !st.items?.length || (!creative.running && creativeHidden === String(st.started))) return null;
    const items = st.items;
    const n = (k) => items.filter((i) => i.status === k).length;
    const current = items.find((i) => i.status === "running");
    const left = items.filter((i) => i.status === "waiting" || i.status === "running" || i.status === "failed");
    const title = creative.running ? `Get Creative: ${n("done") + n("skipped") + n("failed") + (current ? 1 : 0)} of ${items.length}${current ? `, ${current.name}` : ""}`
      : st.state === "done" ? `Get Creative finished: ${n("done")} done${n("skipped") ? `, ${n("skipped")} left alone` : ""}${n("failed") ? `, ${n("failed")} failed` : ""}`
      : `Get Creative ${st.state === "stopped" ? "stopped" : "was interrupted"}: ${n("done")} of ${items.length} done`;
    const mark = { done: "✓", skipped: "–", failed: "✗", running: "…", waiting: "·" };
    const premiereFor = () => (chosenApp ? chosenApp.path : premiereApps.length === 1 ? premiereApps[0].path : null);
    const toPremiere = async (i) => {
      const p = premiereFor();
      if (!p) { setError(premiereApps.length ? "More than one Premiere is installed: choose one with 'Premiere version…' on any open cut first, then try again." : "Adobe Premiere Pro was not found in your Applications folder."); return; }
      setError("");
      setExportResult(null);
      await sendCommand({ type: "export_xml", xml: i.final_xml || i.xml, open: true, app: p });
    };
    return (
      <div className={n("failed") && !creative.running ? "pm-tab-warnings" : "run-pipeline-section"} role="status" aria-live="polite">
        <div className="pm-tab-row" style={{ alignItems: "center" }}>
          <div className="run-pipeline-section-label" style={{ margin: 0 }}>{title}</div>
          <span style={{ flex: 1 }} />
          {creative.running
            ? <button className="btn btn-ghost" onClick={() => sendCommand({ type: "creative_stop" })} title="Stops after the sequence being worked on. What is finished stays finished.">Stop after this one</button>
            : (
              <>
                {left.length > 0 && <button className="btn btn-primary" onClick={() => startCreative(left.map((i) => i.xml))}>Continue with the {left.length} left</button>}
                <button className="btn btn-ghost" onClick={() => setCreativeHidden(String(st.started))}>Hide</button>
              </>
            )}
        </div>
        {creative.running && <div className="sync-section-hint">You can leave this tab or the app in the background. Each sequence's new versions are saved as they are made, so you can come back to all of them. {current?.step ? `Now: ${current.step}.` : ""}</div>}
        <div className="transcripts-list">
          {items.map((i) => (
            <div className="transcript-row" key={i.xml}>
              <div className="transcript-row-main">
                <div className="transcript-row-name">{i.status === "done" && /not added/.test(i.message || "") ? "⚠" : mark[i.status] || "·"} {i.name}</div>
                <div className="transcript-row-folder">{i.status === "running" ? (i.step || "starting") : i.message || (i.status === "waiting" ? "waiting" : "")}</div>
              </div>
              {(i.status === "done" || i.status === "skipped") && (
                <div className="pm-tab-row">
                  <button className="btn btn-ghost" disabled={!!busy} onClick={() => openReview(i.xml)} title="Open this sequence's review: its versions, with the finished one showing">Review</button>
                  <button className="btn btn-primary" disabled={!!busy} onClick={() => toPremiere(i)} title="Check the finished version, then open it in Premiere">Open in Premiere</button>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    );
  }

  const openPicked = async () => {
    if (!picker || picker.selected.length === 0) return;
    setError("");
    try {
      await sendCommand({ type: "extract_sequences", xml: picker.xml, ids: picker.selected });
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  function SequencePicker() {
    if (reading) return <div className="sync-section-hint" role="status">Reading the sequences in {fileName(reading)}… (a large project takes a few seconds)</div>;
    if (!picker) return null;
    const q = picker.filter.trim().toLowerCase();
    const shown = picker.rows.filter((r) => (!picker.hideAuto || !isAutoSave(r.bin)) && (!q || r.name.toLowerCase().includes(q) || r.bin.toLowerCase().includes(q)));
    const set = (patch) => setPicker((x) => ({ ...x, ...patch }));
    const toggle = (id) => set({ selected: picker.selected.includes(id) ? picker.selected.filter((x) => x !== id) : [...picker.selected, id] });
    return (
      <div className="run-pipeline-section" role="dialog" aria-label="Choose sequences">
        <div className="run-pipeline-section-label">{picker.rows.length} sequences in {fileName(picker.xml)}</div>
        <div className="sync-section-hint">Each one you open is cut out into its own XML in a folder named "{fileName(picker.xml).replace(/\.xml$/i, "")} - seqs" next to this file. The original is not changed. Open as many as you like and switch between them.</div>
        <div className="pm-tab-row" style={{ alignItems: "center", flexWrap: "wrap" }}>
          <input value={picker.filter} placeholder="Filter by name or bin" onChange={(e) => set({ filter: e.target.value })}
            style={{ font: "inherit", background: "transparent", color: "inherit", border: "1px solid rgba(255,255,255,0.18)", borderRadius: 6, padding: "4px 8px", minWidth: 220 }} />
          <label className="sync-section-hint" style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <input type="checkbox" checked={picker.hideAuto} onChange={(e) => set({ hideAuto: e.target.checked })} />
            Hide Premiere auto-saves
          </label>
          <button className="btn btn-ghost" onClick={() => set({ selected: [...new Set([...picker.selected, ...shown.filter((r) => !r.problem).map((r) => r.id)])] })}>Select all shown ({shown.filter((r) => !r.problem).length})</button>
          <button className="btn btn-ghost" onClick={() => set({ selected: [] })}>Clear</button>
        </div>
        <div className="transcripts-list" style={{ maxHeight: 340, overflowY: "auto" }}>
          {shown.map((r) => (
            <label className="transcript-row" key={r.id} style={{ cursor: r.problem ? "not-allowed" : "pointer", opacity: r.problem ? 0.55 : 1 }} title={r.problem || ""}>
              <input type="checkbox" disabled={!!r.problem} checked={picker.selected.includes(r.id)} onChange={() => toggle(r.id)} style={{ marginRight: 10 }} />
              <div className="transcript-row-main">
                <div className="transcript-row-name">{r.name}</div>
                <div className="transcript-row-folder">{r.bin || "(no bin)"} · {r.clips} clip{r.clips === 1 ? "" : "s"} · {fmtDur(r.seconds)}{r.problem ? " · cannot be opened: it uses media the XML does not define (offline in Premiere)" : ""}</div>
              </div>
            </label>
          ))}
          {shown.length === 0 && <div className="sync-section-hint">Nothing matches.</div>}
        </div>
        <div className="pm-tab-row">
          <button className="btn btn-primary" disabled={picker.selected.length === 0} onClick={openPicked}>
            {picker.selected.length === 0 ? "Choose sequences to open" : `Open ${picker.selected.length} sequence${picker.selected.length === 1 ? "" : "s"}`}
          </button>
          <button className="btn btn-ghost" onClick={() => setPicker(null)}>Cancel</button>
        </div>
      </div>
    );
  }

  function SeqSwitcher() {
    if (!seqSet || seqSet.items.length < 2) return null;
    return (
      <div className="pm-tab-row" style={{ flexWrap: "wrap", alignItems: "center" }} aria-label="Sequences opened from one XML">
        <span className="sync-section-hint">From {fileName(seqSet.source)}:</span>
        {seqSet.items.map((s) => (
          <button key={s.xml} className={`btn ${versionsRef.current[0]?.xml === s.xml ? "btn-primary" : "btn-ghost"}`} disabled={!!busy || auto.status === "running"}
            title={s.xml} onClick={() => openReview(s.xml)}>{{ done: "✓ ", skipped: "– ", failed: "✗ ", running: "… " }[creative?.state?.items?.find((i) => i.xml === s.xml)?.status] || ""}{s.name.length > 38 ? `${s.name.slice(0, 36)}…` : s.name}</button>
        ))}
      </div>
    );
  }

  const askNotes = useCallback(
    () =>
      new Promise((resolve, reject) => {
        const id = String(Math.random());
        const done = (fn, v) => {
          clearTimeout(t);
          window.removeEventListener("message", h);
          fn(v);
        };
        const h = (e) => {
          if (e.data?.type === "review:notes" && e.data.id === id) done(resolve, e.data.payload);
        };
        const t = setTimeout(() => done(reject, new Error("The review page did not answer. Reload this tab and try again.")), 4000);
        window.addEventListener("message", h);
        frameRef.current?.contentWindow?.postMessage({ type: "review:get-notes", id }, "*");
      }),
    []
  );

  const apply = useCallback(async () => {
    if (!version) return;
    if (CREATIVE_STAGE) {
      setInfo("This stage does not re-edit your cut. Notes stay on the page for you; add graphics, captions, music and transitions, then Open in Premiere.");
      return;
    }
    setError("");
    setInfo("");
    try {
      const payload = await askNotes();
      if (!payload.notes?.length) {
        setInfo("Leave at least one note on the page first (the + Note button, or press n).");
        return;
      }
      await sendCommand({ type: "apply_notes", xml: version.xml, notes_payload: payload, review_folder: version.folder, root: versionsRef.current[0].xml, label: `V${versionsRef.current.length + 1}` });
    } catch (e) {
      setBusy("");
      setError(String(e.message || e));
    }
  }, [version, askNotes]);

  submitRef.current = () => {
    if (busyRef.current) {
      setInfo("The editor is already working on the last submission. Wait for the next version to open.");
      return;
    }
    apply();
  };

  const openInPremiere = useCallback(async (appPath) => {
    if (!version) return;
    setError("");
    setExportResult(null);
    setChoosing(false);
    // The cuts clicked on the page (Add transition > Film Impact Push > a direction) go with the export. If the page cannot answer, the export still goes, without them.
    let transitions = [];
    try { transitions = (await askNotes())?.transitions || []; } catch (e) { /* export without transitions */ }
    await sendCommand({ type: "export_xml", xml: version.xml, open: true, ...(transitions.length ? { transitions } : {}), ...(appPath ? { app: appPath } : {}) });
  }, [version, askNotes]);

  const chosenApp = premiereApps.find((a) => a.path === premiereChoice);
  const shortName = (a) => a.name.replace(/^Adobe /, "");
  const finishCut = async () => {
    if (!version) return;
    try { localStorage.setItem("review.finish", JSON.stringify(finishOpts)); } catch (e) { /* private window */ }
    setFinishing(false);
    setError("");
    setInfo("");
    try {
      await sendCommand({ type: "finish_cut", xml: version.xml, ...finishOpts, root: versionsRef.current[0].xml, label: `V${versionsRef.current.length + 1}` });
    } catch (e) {
      setBusy("");
      setError(String(e.message || e));
    }
  };

  const onOpenInPremiere = () => {
    if (premiereApps.length === 0) {
      setError("Adobe Premiere Pro was not found in your Applications folder. Use Show in Finder and open the XML yourself.");
    } else if (premiereApps.length === 1) {
      openInPremiere(premiereApps[0].path);
    } else if (chosenApp) {
      openInPremiere(chosenApp.path);
    } else {
      setChoosing(true);                                                   // more than one installed and none chosen yet: ask
    }
  };
  const pickPremiere = (a) => {
    if (remember) {
      setPremiereChoice(a.path);
      try { localStorage.setItem("review.premiere", a.path); } catch (e) { /* private window */ }
    }
    openInPremiere(a.path);
  };

  const STEPS = ["Review", "Submit fixes", "Check", "New version"];
  const secs = Math.max(0, Math.round((now - stepSince) / 1000));
  const outcomeBad = auto.outcome === "failed" || auto.outcome === "worse" || auto.outcome === "short";

  // Who has the ball. Working: the AI editor is doing something right now (animated, with the step and a timer). Your turn: nothing is running and the next move is yours (amber, with the reason).
  function StatusStrip() {
    if (!version && !working) return null;
    const what = making ? making
      : busy === "building" ? "Building the review page from the cut."
      : auto.status === "running" ? (auto.round ? `Round ${auto.round} of ${auto.of} on ${auto.label}: ${STEPS[step].toLowerCase()}.` : `Reviewing ${auto.label}.`)
      : busy === "applying" ? (CREATIVE_STAGE ? (stage || "Adding the layers you chose: title card and name tags, captions, music, the bleep. Several minutes; the next version opens here when it is done.") : (stage || "The editor is making the changes you submitted."))
      : "";
    if (working) {
      return (
        <div className="ai-strip working" role="status" aria-live="polite">
          <span className="ai-dot" />
          <div className="ai-strip-main">
            <div className="ai-strip-title">{CREATIVE_STAGE ? (busy === "applying" ? "Working on the cut" : "Working") : auto.status === "running" || busy === "applying" ? "The AI editor is working" : "Working"} <span className="ai-time">{secs}s on this step</span></div>
            <div className="ai-strip-sub">{what}</div>
            {!CREATIVE_STAGE && (auto.status === "running" || busy === "applying") && (
              <div className="ai-steps">
                {STEPS.map((n, i) => <span key={n} className={`ai-step ${i === step ? "on" : i < step ? "done" : ""}`}>{i < step ? "✓ " : ""}{n}</span>)}
                <span className="ai-time">Nothing is needed from you.</span>
              </div>
            )}
          </div>
          {auto.status === "running" && <button className="btn btn-ghost" onClick={() => sendCommand({ type: "auto_edit_stop" })}>Stop</button>}
          <div className="ai-bar" />
        </div>
      );
    }
    if (CREATIVE_STAGE) return null;
    const yours = auto.status === "done" ? auto.summary : autoOn ? "The AI editor is idle." : "The AI editor is switched off.";
    return (
      <div className={`ai-strip yours ${outcomeBad ? "bad" : ""}`} role="status" aria-live="polite">
        <span className="ai-dot" />
        <div className="ai-strip-main">
          <div className="ai-strip-title">Your turn</div>
          <div className="ai-strip-sub">{yours}</div>
        </div>
      </div>
    );
  }

  // The sequences opened from one XML, one row each, with where each stands. Progress and Stop sit at the foot while Get Creative runs.
  function SeqSidebar() {
    if (!seqSet || seqSet.items.length < 2) return null;
    const st = creative?.state;
    const byXml = Object.fromEntries((st?.items || []).map((i) => [i.xml, i]));
    const total = st?.items?.length || 0;
    const finished = (st?.items || []).filter((i) => i.status === "done" || i.status === "skipped" || i.status === "failed").length;
    const left = (st?.items || []).filter((i) => i.status === "waiting" || i.status === "running" || i.status === "failed");
    const glyph = { done: "✓", skipped: "–", failed: "!", running: "…", waiting: "·" };
    const current = (st?.items || []).find((i) => i.status === "running");
    const busyNow = !!busy || auto.status === "running";
    return (
      <nav className="rv-side" aria-label="Sequences">
        <div className="rv-side-head">Sequences <span className="rv-count">{seqSet.items.length}</span></div>
        <ul className="rv-side-list">
          {seqSet.items.map((sq) => {
            const it = byXml[sq.xml];
            const on = versionsRef.current[0]?.xml === sq.xml;
            return (
              <li key={sq.xml}>
                <button className={`rv-seq ${on ? "on" : ""} ${it?.status || ""}`} disabled={busyNow} title={it?.message || sq.xml} aria-current={on ? "true" : undefined} onClick={() => openReview(sq.xml)}>
                  <CutThumb xml={sq.xml} />
                  <span className="rv-seq-name">{pretty(sq.name)}</span>
                  <span className={`rv-glyph ${it?.status || ""}`} aria-hidden="true">{glyph[it?.status] || ""}</span>
                  <span className="rv-sr">{it?.status ? `, ${it.status}` : ""}</span>
                </button>
              </li>
            );
          })}
        </ul>
        {st && total > 0 && (!(!creative.running && creativeHidden === String(st.started))) && (
          <div className="rv-side-foot" role="status" aria-live="polite">
            <div className="rv-side-foot-title">
              {creative.running ? `Getting creative, ${finished} of ${total}` : st.state === "done" ? "Get Creative finished" : st.state === "stopped" ? "Get Creative stopped" : "Get Creative was interrupted"}
            </div>
            <div className="rv-progress" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={finished}><span style={{ width: `${Math.round((100 * finished) / total)}%` }} /></div>
            {creative.running && current?.step && <div className="rv-side-foot-sub">{current.name}: {current.step}</div>}
            <div className="rv-side-foot-row">
              {creative.running
                ? <button className="btn btn-ghost btn-sm" onClick={() => sendCommand({ type: "creative_stop" })} title="Stops after the sequence being worked on. What is finished stays finished.">Stop</button>
                : (<>
                    {left.length > 0 && <button className="btn btn-ghost btn-sm" onClick={() => startCreative(left.map((i) => i.xml))}>Continue with {left.length}</button>}
                    <button className="btn btn-ghost btn-sm" onClick={() => setCreativeHidden(String(st.started))}>Dismiss</button>
                  </>)}
            </div>
          </div>
        )}
      </nav>
    );
  }

  // Get Creative: a sheet over the page, one short decision, Return to go.
  function CreativeSheet() {
    if (!finishing) return null;
    const nothing = !finishOpts.fillers && !finishOpts.captions && !finishOpts.music && !finishOpts.bleep && !finishOpts.graphics;
    const all = seqSet && seqSet.items.length > 1 ? seqSet.items.map((sq) => sq.xml) : null;
    const go = (e) => { e?.preventDefault(); if (nothing) return; startCreative(all && scopeAll ? all : [versionsRef.current[0].xml]); };
    const opt = (k, icon, label, hint) => (
      <label className={`rv-opt ${finishOpts[k] ? "on" : ""}`} key={k}>
        <span className="rv-opt-icon"><Icon name={icon} size={18} /></span>
        <span className="rv-opt-text"><b>{label}</b><span>{hint}</span></span>
        <input type="checkbox" className="rv-switch" checked={!!finishOpts[k]} onChange={(e) => setFinishOpts((o) => ({ ...o, [k]: e.target.checked }))} />
      </label>
    );
    return (
      <div className="rv-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) setFinishing(false); }}>
        <form className="rv-sheet" role="dialog" aria-modal="true" aria-labelledby="rv-gc-title" onSubmit={go} onKeyDown={(e) => { if (e.key === "Escape") setFinishing(false); }}>
          <h2 id="rv-gc-title">Get Creative</h2>
          <p className="rv-sheet-sub">Choose what to add. It runs in the background, one sequence after another, and your trimmed cut is never changed: each sequence gets new versions. Allow a few minutes per sequence.</p>
          <fieldset>
            <legend>Cut</legend>
            {opt("fillers", "cut", "Ums and uhs", "Cut from the sound and the picture. The video gets shorter.")}
          </fieldset>
          <fieldset>
            <legend>Add</legend>
            {opt("captions", "captions", "Captions", "What is said, as text on the screen.")}
            {opt("graphics", "graphics", "Graphics", "A title card with the topic, and a name tag the first time each person speaks.")}
            {opt("music", "music", "Music", "An upbeat bed under the voice, with a beat on every cut.")}
            {opt("sfx", "sfx", "Sound effects", "A sound as each graphic comes on.")}
            {opt("bleep", "bleep", "Bleep", "Covers every word on your list.")}
          </fieldset>
          {all && (
            <div className="rv-seg" role="radiogroup" aria-label="Which sequences">
              <button type="button" role="radio" aria-checked={scopeAll} className={scopeAll ? "on" : ""} onClick={() => setScopeAll(true)}>All {all.length} sequences</button>
              <button type="button" role="radio" aria-checked={!scopeAll} className={!scopeAll ? "on" : ""} onClick={() => setScopeAll(false)}>This sequence</button>
            </div>
          )}
          <details className="rv-more">
            <summary>Already done with an older version?</summary>
            <label className="rv-check">
              <input type="checkbox" checked={redoOpt} onChange={(e) => setRedoOpt(e.target.checked)} />
              <span>Start over from the original<span className="rv-hint">the earlier versions move to a folder called “older versions”, not deleted</span></span>
            </label>
          </details>
          <div className="rv-sheet-actions">
            <button type="button" className="btn btn-ghost" onClick={() => setFinishing(false)}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={nothing} autoFocus>Get Creative</button>
          </div>
        </form>
      </div>
    );
  }

  // ---- nothing chosen yet
  if (!version) {
    keysRef.current = { open: () => { if (!busy) browse(); } };
    return (
      <div className="review-tab rv-empty">
        <div className="rv-empty-card">
          <h1>Finish</h1>
          <p>Bring back the cut you trimmed in Premiere. Post House leaves your edit as it is and adds the finishing touches.</p>
          <ol className="rv-steps">
            <li><b>Trim in Premiere</b><span>Export the sequences as one XML.</span></li>
            <li><b>Choose the XML here</b><span>Each sequence opens on its own frame.</span></li>
            <li><b>Get Creative</b><span>Captions, graphics, music and sound, then open it back in Premiere.</span></li>
          </ol>
          <div className="rv-empty-actions">
            <button className="btn btn-primary" disabled={!!busy} onClick={browse}><Icon name="folderOpen" size={15} /> Choose XML…</button>
            <span className="rv-hint">or press ⌘O</span>
          </div>
          {(error || making || busy) && (
            <div className="rv-notices" style={{ marginTop: 16 }}>
              {StatusStrip()}
              {error && <div className="rv-notice bad" role="alert"><span style={{ whiteSpace: "pre-wrap" }}>{error}</span><button className="btn btn-ghost btn-sm" onClick={() => setError("")}>Dismiss</button></div>}
            </div>
          )}
          {SequencePicker()}
          {CreativePanel()}
          {exportsList.length > 0 && (
            <>
              <h2 className="rv-recent-head">Recent cuts</h2>
              <ul className="rv-posters">
                {exportsList.slice(0, 8).map((x) => (
                  <li key={x.path}>
                    <button className="rv-poster" disabled={!!busy} onClick={() => openReview(x.path)}>
                      <CutThumb xml={x.path} className="big" />
                      <span className="rv-poster-name">{pretty(x.name.replace(/\.xml$/i, ""))}</span>
                      <span className="rv-poster-meta">{new Date(x.mtime * 1000).toLocaleDateString([], { month: "short", day: "numeric" })}{x.session ? ` · ${x.session.versions} version${x.session.versions === 1 ? "" : "s"}` : ""}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
    );
  }

  // ---- a version is open
  const qa = version.qa;
  const aiState = ai[version.label];
  const aiRes = aiState?.result;
  const counts = qa ? qa.notes.reduce((a, n) => ({ ...a, [n.status]: (a[n.status] || 0) + 1 }), {}) : {};
  const wholeBad = qa ? qa.whole_cut.filter((w) => w.ok === false) : [];
  const failedChecks = exportResult && !exportResult.verified ? exportResult.rows.filter((r) => r.ok === false) : [];
  // YOUR notes the editor could not act on matter most: they are never buried among the AI's own. (The AI's notes carry the prefix "AI: ".)
  const mineLeft = qa ? qa.notes.filter((n) => (n.status === "NOT DONE" || n.status === "FAILED") && !/^AI:/.test(n.text || "")) : [];

  const hasSide = !!(seqSet && seqSet.items.length > 1);
  const seqName = (seqSet?.items.find((sq) => sq.xml === versionsRef.current[0]?.xml)?.name) || fileName(versionsRef.current[0]?.xml || version.xml).replace(/\.xml$/i, "");
  const toolsBusy = !!busy || auto.status === "running" || !!creative?.running;
  const premiereLabel = premiereApps.length > 1 && chosenApp ? `Open in ${shortName(chosenApp)}` : "Open in Premiere";
  const goVersion = (i) => { if (i < 0 || i >= versions.length) return; setCur(i); setNoteCount(0); setExportResult(null); };
  keysRef.current = {
    open: () => { if (!busy) browse(); },
    premiere: () => { if (!busy) onOpenInPremiere(); },
    creative: () => { if (!toolsBusy) setFinishing(true); },
    older: () => goVersion(cur - 1),
    newer: () => goVersion(cur + 1),
  };
  const closeMenu = (fn) => () => { setMenuOpen(false); fn(); };

  return (
    <div className="review-tab rv">
      {SeqSidebar()}
      <div className="rv-main">
        <header className="rv-toolbar">
          <div className="rv-title">
            <h1 title={version.xml}>{pretty(seqName)}</h1>
            {versions.length > 1 ? (
              <label className="rv-version">
                <span className="rv-sr">Version</span>
                <select value={cur} onChange={(e) => goVersion(Number(e.target.value))} disabled={!!busy}>
                  {versions.map((v, i) => <option key={v.label} value={i}>{v.label}{i === versions.length - 1 ? " (latest)" : ""}</option>)}
                </select>
              </label>
            ) : (
              <span className="rv-version-static">{version.label}</span>
            )}
          </div>
          <div className="rv-actions">
            {graphicCount > 0 && (
              <button className="btn btn-tinted" onClick={applyGraphics} disabled={toolsBusy}
                title="Make the next version with the graphics where you put them. Nothing is redrawn; each graphic is moved or scaled on its own clip, and stays adjustable in Premiere.">
                Apply {graphicCount} graphic change{graphicCount === 1 ? "" : "s"}
              </button>
            )}
            <button className="btn" onClick={() => setFinishing(true)} disabled={toolsBusy}
              title="Cut the ums and uhs, then add captions, graphics, music and the bleep, as new versions (⌘G)">
              <Icon name="sparkles" size={15} /> Get Creative…
            </button>
            <button className="btn btn-primary" onClick={onOpenInPremiere} disabled={!!busy} title="Check the XML, then open it in Premiere (⌘E)">
              {premiereLabel} <Icon name="arrowUpRight" size={14} />
            </button>
            <div className="rv-menu">
              <button className="btn btn-icon" aria-haspopup="menu" aria-expanded={menuOpen} aria-label="More" title="More" onClick={() => setMenuOpen((x) => !x)}>⋯</button>
              {menuOpen && (
                <>
                  <div className="rv-menu-scrim" onMouseDown={() => setMenuOpen(false)} />
                  <div className="rv-menu-list" role="menu" onKeyDown={(e) => { if (e.key === "Escape") setMenuOpen(false); }}>
                    <button role="menuitem" onClick={closeMenu(() => sendCommand({ type: "open_path", path: dirName(version.xml) }))} disabled={!!busy}>Show in Finder</button>
                    {premiereApps.length > 1 && <button role="menuitem" onClick={closeMenu(() => setChoosing(true))} disabled={!!busy}>Premiere version…</button>}
                    <button role="menuitem" onClick={closeMenu(browse)} disabled={!!busy}><span>Choose another XML…</span><kbd>⌘O</kbd></button>
                    <hr />
                    <button role="menuitem" className="danger" disabled={!!busy || auto.status === "running"}
                      onClick={closeMenu(() => { if (window.confirm("Start this cut's review over? The versions made so far will be replaced.")) openReview(versionsRef.current[0].xml, true); })}>
                      Start over…
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </header>

        {(error || info || incoming || exportResult || working) && (
          <div className="rv-notices">
            {StatusStrip()}
            {incoming && (
              <div className="rv-notice" role="status">
                <span>A new export was just made: {fileName(incoming)}.</span>
                <button className="btn btn-sm" disabled={!!busy} onClick={() => { const x = incoming; setIncoming(""); build(x); }}>Review it instead</button>
                <button className="btn btn-ghost btn-sm" onClick={() => setIncoming("")}>Keep this one</button>
              </div>
            )}
            {error && <div className="rv-notice bad" role="alert"><span style={{ whiteSpace: "pre-wrap" }}>{error}</span><button className="btn btn-ghost btn-sm" onClick={() => setError("")}>Dismiss</button></div>}
            {info && <div className="rv-notice" role="status"><span>{info}</span><button className="btn btn-ghost btn-sm" onClick={() => setInfo("")}>Dismiss</button></div>}
            {exportResult && (
              <div className={`rv-notice ${exportResult.verified ? "ok" : "bad"}`} role="status">
                <span>
                  {exportResult.verified
                    ? exportResult.opened
                      ? `Checked, and opened in ${exportResult.app || "the default app"}: ${fileName(exportResult.xml)}` + (exportResult.transitions ? `. Transitions: ${exportResult.transitions.cuts} Film Impact Push${exportResult.transitions.cuts === 1 ? "" : "es"} with your whoosh (the Premiere plugin adds the push when the XML opens)` + (exportResult.transitions.skipped?.length ? `; no sound possible at ${exportResult.transitions.skipped.join(", ")} s (too near the start or end)` : "") : "") + (exportResult.inherited?.length ? `. Already in the original export, not made by the editing: ${exportResult.inherited.join(", ")} (footage in the leftover bin overlaps; your cut is not affected).` : "")
                      : `Checked: ${fileName(exportResult.xml)} (not opened${exportResult.app ? "" : "; Premiere was not found"}).`
                    : `Not opened: the export check failed (${exportResult.failed.join(", ")}). ${failedChecks.map((r) => r.detail).join(" ")}`}
                </span>
                <button className="btn btn-ghost btn-sm" onClick={() => setExportResult(null)}>Dismiss</button>
              </div>
            )}
          </div>
        )}

        {!hasSide && CreativePanel()}
        {SequencePicker()}

        {mineLeft.length > 0 && (
          <div className="rv-notice bad" role="alert">
            <span>
              <strong>{mineLeft.length === 1 ? "One of your notes was" : `${mineLeft.length} of your notes were`} not done.</strong>{" "}
              {mineLeft.map((n) => `“${n.text}”: ${(n.rows[0] && n.rows[0].detail) || "no operation exists for this yet"}`).join(" ")}
            </span>
          </div>
        )}

        <div className="rv-frame">
          {working && (auto.status === "running" || busy === "applying") && (
            <div className="ai-badge"><span className="ai-dot" /> {CREATIVE_STAGE ? "Getting creative" : `AI editor working: ${STEPS[step].toLowerCase()}`}</div>
          )}
          <iframe
            key={version.url}
            ref={frameRef}
            title={`Review ${version.label}`}
            src={version.url}
            allow="autoplay; clipboard-write"
            onLoad={() => { if (CREATIVE_STAGE) frameRef.current?.contentWindow?.postMessage({ type: "review:mode", submit: false }, "*"); }}
          />
        </div>

      {CreativeSheet()}
      {choosing && (
        <div className="rv-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) setChoosing(false); }}>
          <div className="rv-sheet" role="dialog" aria-modal="true" aria-labelledby="rv-pp-title" onKeyDown={(e) => { if (e.key === "Escape") setChoosing(false); }}>
            <h2 id="rv-pp-title">Which Premiere should open it?</h2>
            <p className="rv-sheet-sub">More than one version is installed.</p>
            <div className="rv-list">
              {premiereApps.map((a) => (
                <button key={a.path} className={`btn ${a.path === premiereChoice ? "btn-tinted" : ""}`} onClick={() => pickPremiere(a)}>{shortName(a)}</button>
              ))}
            </div>
            <label className="rv-check"><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /><span>Use this one from now on<span className="rv-hint">change it later in the ⋯ menu</span></span></label>
            <div className="rv-sheet-actions"><button className="btn btn-ghost" onClick={() => setChoosing(false)}>Cancel</button></div>
          </div>
        </div>
      )}

      {!CREATIVE_STAGE && auto.status === "done" && (
        <div className={auto.outcome === "failed" || auto.outcome === "worse" || auto.outcome === "short" ? "pm-tab-warnings" : "run-pipeline-section"} role="status">
          <div className="pm-tab-row" style={{ alignItems: "center" }}>
            <div className="run-pipeline-section-label" style={{ margin: 0 }}>{auto.left?.length ? `What the AI editor could not fix by itself (${auto.left.length})` : "Nothing left for the AI editor"}</div>
            <span style={{ flex: 1 }} />
            {auto.left?.length > 0 && auto.status === "done" && (
              <button className="btn btn-primary" title="A Claude agent reads the cut and these notes, chooses other changes where the editor's rules refused, and applies them through the same checks"
                onClick={() => {
                  const v = versionsRef.current.find((x) => x.label === auto.best) || versionsRef.current[versionsRef.current.length - 1];
                  if (!v) return;
                  setAuto({ status: "running", round: 1, of: 1, label: v.label, fixing: auto.left.length, agent: true });
                  sendCommand({ type: "agent_edit", xml: v.xml, folder: v.folder, tag: v.label, root: versionsRef.current[0]?.xml || v.xml, left: auto.left, direction: directionRef.current })
                    .catch((e) => setAuto({ status: "done", summary: `The agent could not start: ${e}`, left: auto.left, versions: [], rounds: [] }));
                }}>Have a Claude agent try these</button>
            )}
            {auto.left?.length > 0 && <button className="btn btn-ghost" onClick={() => setShowLeft((x) => !x)}>{showLeft ? "Hide" : "Show"}</button>}
          </div>
          {auto.left?.length > 0 && showLeft && (
            <div className="transcripts-list">
              {auto.left.map((l, i) => (
                <div className="transcript-row" key={i}>
                  <div className="transcript-row-main">
                    <div className="transcript-row-name">{l.text}</div>
                    <div className="transcript-row-folder">{l.reason}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {!CREATIVE_STAGE && !aiState && version && auto.status !== "running" && (
        <div className="run-pipeline-section">
          <div className="pm-tab-row" style={{ alignItems: "center" }}>
            <div className="run-pipeline-section-label" style={{ margin: 0 }}>{version.label}: made from your notes, and the AI editor has not touched it</div>
            <span style={{ flex: 1 }} />
            <button className="btn btn-ghost" disabled={!!busy} onClick={() => aiOnlyRef.current?.(version.xml, version.folder, version.label)}>Review it</button>
            <button className="btn btn-ghost" disabled={!!busy} title="The AI editor reviews this version and makes the fixes it can, round after round, as it did on the first cut"
              onClick={() => { setAuto({ status: "running", round: 0, of: 4, label: version.label }); aiStartRef.current?.(version.xml, version.folder, version.label); }}>Let the AI editor continue</button>
          </div>
        </div>
      )}

      {!CREATIVE_STAGE && aiState && (
        <div className="run-pipeline-section">
          <div className="pm-tab-row" style={{ alignItems: "center" }}>
            <div className="run-pipeline-section-label" style={{ margin: 0 }}>
              AI review of {version.label}
              {aiState.status === "running" ? `: ${aiState.stage}` : ""}
              {aiState.status === "done" ? `: ${aiRes.notes.length} note${aiRes.notes.length === 1 ? "" : "s"} added to the page` : ""}
              {aiState.status === "failed" ? ": could not run" : ""}
            </div>
            <span style={{ flex: 1 }} />
            {aiState.status === "done" && <button className="btn btn-ghost" onClick={() => setShowAi((x) => !x)}>{showAi ? "Hide" : "Show"} details</button>}
            {aiState.status !== "running" && (
              <button className="btn btn-ghost" disabled={!!busy} onClick={() => aiOnlyRef.current?.(version.xml, version.folder, version.label)}>Review again</button>
            )}
            {aiState.status !== "running" && auto.status !== "running" && (
              <button className="btn btn-ghost" disabled={!!busy} title="The AI editor reviews this version and makes the fixes it can, round after round, as it did on the first cut"
                onClick={() => { setAuto({ status: "running", round: 0, of: 4, label: version.label }); aiStartRef.current?.(version.xml, version.folder, version.label); }}>Let the AI editor continue</button>
            )}
          </div>
          {aiState.status === "failed" && <div className="pm-tab-warnings" role="alert" style={{ whiteSpace: "pre-wrap" }}>{aiState.message}</div>}
          {aiState.status === "done" && showAi && (
            <div className="transcripts-list">
              {aiRes.summary && <div className="sync-section-hint">What the cut says: {aiRes.summary}</div>}
              {aiRes.checks.filter((c) => c.name !== "STORY").map((c) => (
                <div className="transcript-row" key={c.name}>
                  <div className="transcript-row-main">
                    <div className={`transcript-row-name review-status-${c.ok === true ? "ok" : c.ok === false ? "bad" : "warn"}`}>
                      {c.name.replace("-", " ").toLowerCase()} · {c.ok === true ? "ok" : c.ok === false ? "needs a look" : "not judged"}
                    </div>
                    <div className="transcript-row-folder">{c.detail}</div>
                  </div>
                </div>
              ))}
              {aiRes.unverified_quotes_dropped > 0 && (
                <div className="sync-section-hint">{aiRes.unverified_quotes_dropped} finding(s) quoted words that are not in the cut and were left out.</div>
              )}
              <div className="sync-section-hint">Not covered: the picture (a cropped head, a wrong shot).</div>
            </div>
          )}
        </div>
      )}

      {qa && (
        <div className="run-pipeline-section">
          <div className="pm-tab-row" style={{ alignItems: "center" }}>
            <div className="run-pipeline-section-label" style={{ margin: 0 }}>
              {version.label}, checked: {version.applied} of {version.notes} notes applied · {counts["VERIFIED"] || 0} verified · {counts["APPLIED-UNMEASURED"] || 0} look at it · {counts["NOT DONE"] || 0} not done · {counts["FAILED"] || 0} failed
              {wholeBad.length ? ` · ${wholeBad.length} whole-cut check(s) failed` : ""}
              {qa.unrequested.length ? ` · ${qa.unrequested.length} change(s) nobody asked for` : ""}
            </div>
            <span style={{ flex: 1 }} />
            <button className="btn btn-ghost" onClick={() => setShowLedger((s) => !s)}>{showLedger ? "Hide" : "Show"} details</button>
            <button className="btn btn-ghost" onClick={() => sendCommand({ type: "open_path", path: qa.report })}>QA report</button>
          </div>
          {showLedger && (
            <div className="transcripts-list">
              {qa.notes.map((n) => (
                <div className="transcript-row" key={n.note}>
                  <div className="transcript-row-main">
                    <div className={`transcript-row-name review-status-${STATUS_CLASS[n.status] || "warn"}`}>Note {n.note} at {n.time}s · {STATUS_LABEL[n.status] || n.status}</div>
                    <div className="transcript-row-folder">{n.text}</div>
                    {n.rows.map((r, i) => <div className="sync-section-hint" key={i}>{r.detail}</div>)}
                  </div>
                </div>
              ))}
              {qa.unrequested.map((u, i) => <div className="pm-tab-warnings" key={i}>{u}</div>)}
            </div>
          )}
        </div>
      )}

      {!CREATIVE_STAGE && activity.length > 0 && (
        <div className="run-pipeline-section">
          <div className="pm-tab-row" style={{ alignItems: "center" }}>
            <div className="run-pipeline-section-label" style={{ margin: 0 }}>What has happened</div>
            <span style={{ flex: 1 }} />
            <button className="btn btn-ghost" onClick={() => setShowFeed((x) => !x)}>{showFeed ? "Hide" : "Show"}</button>
          </div>
          {showFeed && (
            <div className="ai-feed">
              {[...activity].reverse().slice(0, 8).map((a, i) => (
                <div className={`ai-feed-row ${a.kind}`} key={`${a.t}-${i}`}>
                  <span className="ai-feed-time">{new Date(a.t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
                  <span>{a.text}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      </div>
    </div>
  );
}
