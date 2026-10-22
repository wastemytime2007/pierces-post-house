import { useCallback, useEffect, useRef, useState } from "react";
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

export default function ReviewTab({ subscribe, onStatus }) {
  const frameRef = useRef(null);
  const versionsRef = useRef([]);
  const busyRef = useRef("");
  const buildRef = useRef(null);
  const openReviewRef = useRef(null);
  const makingRef = useRef("");
  const [making, setMaking] = useState(""); // the cut is being made from the ideas picked on the Ideas tab ("Review this cut"): what it is doing now
  // One XML that holds many sequences (a Premiere SEQS bin exported in one go): listed here, each one opened is cut out into its own XML and reviewed like any single cut.
  const [reading, setReading] = useState("");                      // the XML being read for its sequences
  const [creative, setCreative] = useState(null);                // Get Creative's batch: { running, state: { state, started, items: [{ xml, name, status, step, message, final_xml }] } }; the truth is on disk (get_creative.json), this is what the tab last heard
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
      : exportsList.length ? "cut ready" : "notes on a cut"
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

  const startCreative = async (xmls) => {
    if (!xmls.length) return;
    try { localStorage.setItem("review.finish", JSON.stringify(finishOpts)); } catch (e) { /* private window */ }
    setFinishing(false);
    setError("");
    setInfo("");
    try {
      await sendCommand({ type: "get_creative", xmls, opts: finishOpts });
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
            <div className="ai-strip-title">{CREATIVE_STAGE ? (busy === "applying" ? "Getting creative" : "Working") : auto.status === "running" || busy === "applying" ? "The AI editor is working" : "Working"} <span className="ai-time">{secs}s on this step</span></div>
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
    const yours = CREATIVE_STAGE ? "Your cut is open exactly as you trimmed it. Nothing here edits it. Get Creative adds ums-and-uhs cuts, graphics, captions and music; click a cut to add a transition; then Open in Premiere." : auto.status === "done" ? auto.summary : autoOn ? "The AI editor is idle." : "The AI editor is switched off.";
    return (
      <div className={`ai-strip yours ${outcomeBad ? "bad" : ""}`} role="status" aria-live="polite">
        <span className="ai-dot" />
        <div className="ai-strip-main">
          <div className="ai-strip-title">Your turn <span className="ai-time">{CREATIVE_STAGE ? "your cut is not being edited" : auto.status === "done" ? (auto.outcome === "clean" ? "nothing left for the editor" : "the editor has done what it can") : "the editor is waiting on you"}</span></div>
          <div className="ai-strip-sub">{yours}</div>
        </div>
      </div>
    );
  }

  // ---- nothing chosen yet
  if (!version) {
    return (
      <div className="review-tab">
        <div className="run-pipeline-section">
          <div className="run-pipeline-section-label">Choose the cut to review</div>
          <p className="pm-tab-sub">
            {CREATIVE_STAGE
              ? "Open the XML you trimmed in Premiere. It shows here exactly as you cut it: the video and every clip on a timeline. Nothing in this stage edits the cut. Add the ums-and-uhs cut, graphics, captions, music and the bleep with Get Creative, click any cut to add a transition and its sound, then Open in Premiere."
              : "Pick ideas on the Ideas tab and press Review: the cut is made in your project folder and opens here by itself, with nothing to save or download: the video, every edit decision on a timeline, and a place for timecoded notes and drawing on the frame. Leave notes, apply them to get the next version, then export the XML and open it in Premiere."}
          </p>
          {StatusStrip()}
      {SequencePicker()}
      {CreativePanel()}
          {!making && !busy && exportsList.length === 0 && <div className="sync-section-hint">{CREATIVE_STAGE ? "No XML yet. Press Browse and choose the XML you exported from Premiere." : "No cut from this project yet. Pick ideas on the Ideas tab and press Review."}</div>}
          {exportsList.length > 0 && (
            <div className="transcripts-list">
              {exportsList.slice(0, 8).map((x) => (
                <div className="transcript-row" key={x.path}>
                  <div className="transcript-row-main">
                    <div className="transcript-row-name">{x.name}</div>
                    <div className="transcript-row-folder">
                      {new Date(x.mtime * 1000).toLocaleString()}
                      {x.session ? ` · reviewed: ${x.session.versions} version${x.session.versions === 1 ? "" : "s"}, ${x.session.best} is the one to review${x.session.left ? `, ${x.session.left} left` : ""}` : " · not reviewed yet"}
                    </div>
                  </div>
                  <button className="btn btn-primary" disabled={!!busy} onClick={() => openReview(x.path)}>
                    {x.session ? "Open review" : "Review"}
                  </button>
                  {x.session && (
                    <button className="btn btn-ghost" disabled={!!busy} title="Build this cut's review again from the start. The versions made so far are replaced."
                      onClick={() => { if (window.confirm("Start this cut's review over? The versions made so far will be replaced.")) openReview(x.path, true); }}>
                      Start over
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
          <div className="pm-tab-row">
            <button className="btn btn-ghost" disabled={!!busy} onClick={browse}>
              {exportsList.length ? "A different XML…" : "Choose an XML yourself…"}
            </button>
          </div>
        </div>
        {error && <div className="pm-tab-warnings" role="alert" style={{ whiteSpace: "pre-wrap" }}>{error}</div>}
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

  return (
    <div className="review-tab" style={{ display: "flex", flexDirection: "column", gap: 8, height: "100%" }}>
      <div className="pm-tab-row" style={{ flexWrap: "wrap", alignItems: "center", gap: 8 }}>
        {versions.map((v, i) => (
          <button key={v.label} className={`btn ${i === cur ? "btn-primary" : "btn-ghost"}`} onClick={() => { setCur(i); setNoteCount(0); setExportResult(null); }} disabled={!!busy}>
            {v.label}
          </button>
        ))}
        <span className="transcript-row-name" title={version.xml}>{fileName(version.xml)}</span>
        <span style={{ flex: 1 }} />
        {!CREATIVE_STAGE && (<>
        <label className="sync-section-hint" style={{ display: "flex", alignItems: "center", gap: 6 }} title="When on, the AI editor reviews each version, submits the fixes it can make and repeats until nothing fixable is left. You can stop it any time.">
          <input type="checkbox" checked={autoOn} onChange={(e) => { setAutoOn(e.target.checked); try { localStorage.setItem("review.autoEdit", e.target.checked ? "on" : "off"); } catch (err) { /* private window */ } }} />
          AI editor works on its own
        </label>
        <label className="sync-section-hint" style={{ display: "flex", alignItems: "center", gap: 6 }} title="When on, the AI editor ends by finishing the best version it made: captions, a title card and name tags, music, an effect on each graphic, the bleep. The choices are the ones in Finish the cut.">
          <input type="checkbox" checked={autoFinish} onChange={(e) => { setAutoFinish(e.target.checked); try { localStorage.setItem("review.autoFinish", e.target.checked ? "on" : "off"); } catch (err) { /* private window */ } }} />
          …and finishes the cut
        </label>
        {auto.status === "running" && (
          <button className="btn btn-ghost" onClick={() => sendCommand({ type: "auto_edit_stop" })} title="The editor finishes the step it is on and stops">Stop the AI editor</button>
        )}
        <button className="btn btn-primary" onClick={apply} disabled={!!busy || auto.status === "running"} title="Send the notes on the page to the editor. It makes the changes and opens the next version here.">
          {busy === "applying"
            ? "Editor is working…"
            : noteCount
              ? `Submit changes (${noteCount}) → V${versions.length + 1}`
              : `Submit changes → V${versions.length + 1}`}
        </button>
        </>)}
        <button className="btn btn-ghost" onClick={() => setFinishing((x) => !x)}
          title="Cut the ums and uhs, then add captions, a title card and name tag, music and the bleep, as new versions. Can run on every sequence you opened, in the background." disabled={!!busy || auto.status === "running" || !!creative?.running}>
          Get Creative…
        </button>
        <button className="btn btn-primary" onClick={onOpenInPremiere} disabled={!!busy} title="Check the XML, then open it in Premiere. Nothing to download.">
          {(premiereApps.length > 1 && chosenApp ? `Open in ${shortName(chosenApp)}` : "Open in Premiere") + (version ? `: ${version.label}` : "")}
        </button>
        {premiereApps.length > 1 && (
          <button className="btn btn-ghost" onClick={() => setChoosing((x) => !x)} disabled={!!busy} title="Choose which version of Premiere opens it">
            Premiere version…
          </button>
        )}
        <button className="btn btn-ghost" onClick={() => sendCommand({ type: "open_path", path: dirName(version.xml) })} disabled={!!busy}>
          Show in Finder
        </button>
        <button className="btn btn-ghost" onClick={() => { setVersions([]); setCur(0); setExportResult(null); setError(""); setInfo(""); openedRef.current = true; sendCommand({ type: "list_exports" }).catch(() => {}); }} disabled={!!busy}>
          Other cut…
        </button>
        <button className="btn btn-ghost" title="Build this cut's review again from the start. The versions made so far are replaced." disabled={!!busy || auto.status === "running"}
          onClick={() => { if (window.confirm("Start this cut's review over? The versions made so far will be replaced.")) openReview(versionsRef.current[0].xml, true); }}>
          Start over
        </button>
      </div>

      {SeqSwitcher()}

      {!CREATIVE_STAGE && versions.length > 0 && (
        <label className="sync-section-hint" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          Your own direction, after the AI editor has done its pass (optional; it already follows the idea you chose, and this is added on top of that)
          <textarea rows={2} value={direction} placeholder="For example: open on Bob's quick check, then Mitch's plan. Then press Start over to run the editor again with this added."
            onChange={(e) => { setDirection(e.target.value); try { localStorage.setItem(dirKey(versionsRef.current[0]?.xml), e.target.value); } catch (err) { /* private window */ } }}
            style={{ width: "100%", font: "inherit", background: "transparent", color: "inherit", border: "1px solid rgba(255,255,255,0.18)", borderRadius: 6, padding: 8 }} />
        </label>
      )}

      {finishing && (
        <div className="run-pipeline-section" role="dialog" aria-label="Get Creative">
          <div className="run-pipeline-section-label">Get Creative</div>
          <div className="sync-section-hint">Runs in the background, one sequence after another, so you can leave and come back. Each sequence gets new versions: the ums and uhs cut out first, then the layers below on top. Several minutes per sequence.</div>
          {[["fillers", "Ums and uhs: cut out of the sound and the picture (the cut gets shorter)"], ["captions", "Captions: what is said, as text on screen"], ["graphics", "Graphics: a title card with the topic, and a name tag the first time each person talks"], ["music", "Music: generated, an upbeat bed under the voice, a beat on every cut"], ["sfx", "Sound effects: a library effect as each graphic comes on (placed with the music)"], ["bleep", "Bleep: every listed word"]].map(([k, label]) => (
            <label key={k} className="sync-section-hint" style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <input type="checkbox" checked={!!finishOpts[k]} onChange={(e) => setFinishOpts((o) => ({ ...o, [k]: e.target.checked }))} />
              {label}
            </label>
          ))}
          <div className="pm-tab-row">
            {(() => {
              const nothing = !finishOpts.fillers && !finishOpts.captions && !finishOpts.music && !finishOpts.bleep && !finishOpts.graphics;
              const all = seqSet && seqSet.items.length > 1 ? seqSet.items.map((s) => s.xml) : null;
              return (
                <>
                  {all && <button className="btn btn-primary" onClick={() => startCreative(all)} disabled={nothing}>Get Creative on all {all.length} sequences</button>}
                  <button className={`btn ${all ? "btn-ghost" : "btn-primary"}`} onClick={() => startCreative([versionsRef.current[0].xml])} disabled={nothing}>{all ? "Just this sequence" : "Get Creative"}</button>
                </>
              );
            })()}
            <button className="btn btn-ghost" onClick={() => setFinishing(false)}>Cancel</button>
          </div>
        </div>
      )}

      {choosing && (
        <div className="run-pipeline-section" role="dialog" aria-label="Choose a version of Premiere">
          <div className="run-pipeline-section-label">Which Premiere should open it?</div>
          <div className="sync-section-hint">More than one version is installed.</div>
          <div className="pm-tab-row" style={{ flexWrap: "wrap" }}>
            {premiereApps.map((a) => (
              <button key={a.path} className={`btn ${a.path === premiereChoice ? "btn-primary" : "btn-ghost"}`} onClick={() => pickPremiere(a)}>
                {shortName(a)}
              </button>
            ))}
            <button className="btn btn-ghost" onClick={() => setChoosing(false)}>Cancel</button>
          </div>
          <label className="sync-section-hint" style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> Use this one from now on (you can change it with "Premiere version…")
          </label>
        </div>
      )}

      {incoming && (
        <div className="pm-tab-warnings" role="status">
          <div className="pm-tab-row" style={{ alignItems: "center" }}>
            <span>A new export was just made: {fileName(incoming)}.</span>
            <button className="btn btn-primary" disabled={!!busy} onClick={() => { const x = incoming; setIncoming(""); build(x); }}>Review it instead</button>
            <button className="btn btn-ghost" onClick={() => setIncoming("")}>Keep this one</button>
          </div>
        </div>
      )}
      {error && <div className="pm-tab-warnings" role="alert" style={{ whiteSpace: "pre-wrap" }}>{error}</div>}
      {info && <div className="pm-tab-warnings" role="status">{info}</div>}

      {exportResult && (
        <div className={exportResult.verified ? "sync-section-hint" : "pm-tab-warnings"} role="status">
          {exportResult.verified
            ? exportResult.opened
              ? `Checked, and opened in ${exportResult.app || "the default app"}: ${fileName(exportResult.xml)}` + (exportResult.transitions ? `. Transitions: ${exportResult.transitions.cuts} Film Impact Push${exportResult.transitions.cuts === 1 ? "" : "es"} with your whoosh (the Premiere plugin adds the push when the XML opens)` + (exportResult.transitions.skipped?.length ? `; no sound possible at ${exportResult.transitions.skipped.join(", ")} s (too near the start or end)` : "") : "") + (exportResult.inherited?.length ? `. Already in the original export, not made by the editing: ${exportResult.inherited.join(", ")} (footage in the leftover bin overlaps; your cut is not affected).` : "")
              : `Checked: ${fileName(exportResult.xml)} (not opened${exportResult.app ? "" : "; Premiere was not found"}).`
            : `Not opened: the export check failed (${exportResult.failed.join(", ")}). ${failedChecks.map((r) => r.detail).join(" ")}`}
        </div>
      )}

      {StatusStrip()}
      {SequencePicker()}
      {CreativePanel()}

      {mineLeft.length > 0 && (
        <div className="pm-tab-warnings" role="alert">
          <div style={{ fontWeight: 600 }}>
            {mineLeft.length === 1 ? "One of your notes was" : `${mineLeft.length} of your notes were`} not done: the AI editor cannot make {mineLeft.length === 1 ? "this change" : "these changes"}, so {mineLeft.length === 1 ? "it needs" : "they need"} you or another tool.
          </div>
          {mineLeft.map((n) => (
            <div key={n.note} style={{ marginTop: 6 }}>
              <div>“{n.text}”</div>
              <div className="sync-section-hint">{(n.rows[0] && n.rows[0].detail) || "no operation exists for this yet"}</div>
            </div>
          ))}
        </div>
      )}

      <div style={{ position: "relative", flex: 1, display: "flex", minHeight: 640 }}>
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
          style={{ flex: 1, minHeight: 640, width: "100%", border: `1px solid ${working ? "var(--accent, #00e0e0)" : "var(--border, #333)"}`, borderRadius: 8, background: "#000" }}
        />
      </div>

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

      {activity.length > 0 && (
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
  );
}
