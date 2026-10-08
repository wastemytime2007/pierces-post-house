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
    try { return { captions: true, music: true, bleep: true, graphics: true, sfx: true, ...JSON.parse(localStorage.getItem("review.finish") || "{}") }; } catch (e) { return { captions: true, music: true, bleep: true, graphics: true, sfx: true }; }
  });
  const [autoFinish, setAutoFinish] = useState(() => {
    try { return localStorage.getItem("review.autoFinish") !== "off"; } catch (e) { return true; }
  });
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
        setCur(bestIdx >= 0 ? bestIdx : vs.length - 1);
        setNoteCount(0);
        setExportResult(null);
        setIncoming("");
        setInfo("");
        if (ev.stale) setInfo("This review was made by an older version of the AI editor, so it does not have what has been fixed since (for example cutting an off-camera voice, or finishing with graphics and sound). Press Start over to run the current editor on this cut.");
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
        if (!ev.auto) {                                                                                  // the editor's own revisions are reviewed by its loop; yours start it again
          const lab = `V${versionsRef.current.length + 1}`;
          if (autoOnRef.current) setAuto({ status: "running", round: 0, of: 4, label: lab });
          aiStartRef.current?.(ev.xml, ev.folder, lab);
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
      }
    });
  }, [subscribe]);

  // The status strip's feed: every step the editor takes is written down with the time, so it is always clear whether the app is working or waiting for you.
  useEffect(() => {
    const say = (text, kind = "info") => setActivity((a) => [...a.slice(-39), { t: Date.now(), text, kind }]);
    const go = (i) => { setStep(i); setStepSince(Date.now()); };
    return subscribe((ev) => {
      if (ev.type === "review_built") {
        setActivity([]);
        say(`The cut is open as V1 (${ev.clips} clips, ${ev.duration} s).`);
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
    sendCommand({ type: "ai_review", xml, folder, tag, root: root || versionsRef.current[0]?.xml || xml }).catch((e) => setAi((a) => ({ ...a, [tag]: { status: "failed", message: String(e) } })));
  };

  // A new version (the cut just built, or one made from your notes): the AI editor takes it from here, unless it is switched off, then only the review runs.
  aiStartRef.current = (xml, folder, tag, root) => {
    if (autoOnRef.current) {
      sendCommand({ type: "auto_edit", xml, folder, tag, root: root || versionsRef.current[0]?.xml || xml, finish: autoFinishRef.current ? finishOptsRef.current : null }).catch((e) => setAuto({ status: "done", summary: `The AI editor could not start: ${e}`, left: [], versions: [], rounds: [] }));
    } else {
      aiOnlyRef.current?.(xml, folder, tag, root);
    }
  };

  const browse = useCallback(async () => {
    const p = await openDialog({ multiple: false, filters: [{ name: "Premiere XML", extensions: ["xml"] }] });
    if (typeof p === "string") build(p);
  }, [build]);

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
    await sendCommand({ type: "export_xml", xml: version.xml, open: true, ...(appPath ? { app: appPath } : {}) });
  }, [version]);

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
      : busy === "applying" ? (stage || "The editor is making the changes you submitted.")
      : "";
    if (working) {
      return (
        <div className="ai-strip working" role="status" aria-live="polite">
          <span className="ai-dot" />
          <div className="ai-strip-main">
            <div className="ai-strip-title">{auto.status === "running" || busy === "applying" ? "The AI editor is working" : "Working"} <span className="ai-time">{secs}s on this step</span></div>
            <div className="ai-strip-sub">{what}</div>
            {(auto.status === "running" || busy === "applying") && (
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
    const yours = auto.status === "done" ? auto.summary : autoOn ? "The AI editor is idle." : "The AI editor is switched off.";
    return (
      <div className={`ai-strip yours ${outcomeBad ? "bad" : ""}`} role="status" aria-live="polite">
        <span className="ai-dot" />
        <div className="ai-strip-main">
          <div className="ai-strip-title">Your turn <span className="ai-time">{auto.status === "done" ? (auto.outcome === "clean" ? "nothing left for the editor" : "the editor has done what it can") : "the editor is waiting on you"}</span></div>
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
            Pick ideas on the Ideas tab and press Review: the cut is made in your project folder and opens here by itself, with nothing to save or download: the video, every edit decision on a timeline, and a place for timecoded notes and drawing
            on the frame. Leave notes, apply them to get the next version, then export the XML and open it in Premiere.
          </p>
          {StatusStrip()}
          {!making && !busy && exportsList.length === 0 && <div className="sync-section-hint">No cut from this project yet. Pick ideas on the Ideas tab and press Review.</div>}
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
        <button className="btn btn-ghost" onClick={() => setFinishing((x) => !x)} disabled={!!busy || auto.status === "running"}
          title="Add the finishing layers to this version: captions, a music bed, the bleep. Makes the next version; the picture is not touched.">
          Finish the cut…
        </button>
        <button className="btn btn-primary" onClick={onOpenInPremiere} disabled={!!busy} title="Check the XML, then open it in Premiere. Nothing to download.">
          {premiereApps.length > 1 && chosenApp ? `Open in ${shortName(chosenApp)}` : "Open in Premiere"}
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

      {finishing && (
        <div className="run-pipeline-section" role="dialog" aria-label="Finish the cut">
          <div className="run-pipeline-section-label">Finish the cut</div>
          <div className="sync-section-hint">Adds the layers below to this version and opens the result as V{versions.length + 1}. The picture and the length stay exactly as they are. This takes several minutes.</div>
          {[["captions", "Captions: what is said, as text on screen"], ["graphics", "Graphics: a title card with the topic, and a name tag the first time each person talks"], ["music", "Music: generated, an upbeat bed under the voice, a beat on every cut"], ["sfx", "Sound effects: a library effect as each graphic comes on (placed with the music)"], ["bleep", "Bleep: every listed word"]].map(([k, label]) => (
            <label key={k} className="sync-section-hint" style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <input type="checkbox" checked={!!finishOpts[k]} onChange={(e) => setFinishOpts((o) => ({ ...o, [k]: e.target.checked }))} />
              {label}
            </label>
          ))}
          <div className="pm-tab-row">
            <button className="btn btn-primary" onClick={finishCut} disabled={!finishOpts.captions && !finishOpts.music && !finishOpts.bleep && !finishOpts.graphics}>Make V{versions.length + 1}</button>
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
              ? `Checked, and opened in ${exportResult.app || "the default app"}: ${fileName(exportResult.xml)}`
              : `Checked: ${fileName(exportResult.xml)} (not opened${exportResult.app ? "" : "; Premiere was not found"}).`
            : `Not opened: the export check failed (${exportResult.failed.join(", ")}). ${failedChecks.map((r) => r.detail).join(" ")}`}
        </div>
      )}

      {StatusStrip()}

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
          <div className="ai-badge"><span className="ai-dot" /> AI editor working: {STEPS[step].toLowerCase()}</div>
        )}
        <iframe
          key={version.url}
          ref={frameRef}
          title={`Review ${version.label}`}
          src={version.url}
          allow="autoplay; clipboard-write"
          style={{ flex: 1, minHeight: 640, width: "100%", border: `1px solid ${working ? "var(--accent, #00e0e0)" : "var(--border, #333)"}`, borderRadius: 8, background: "#000" }}
        />
      </div>

      {auto.status === "done" && (
        <div className={auto.outcome === "failed" || auto.outcome === "worse" || auto.outcome === "short" ? "pm-tab-warnings" : "run-pipeline-section"} role="status">
          <div className="pm-tab-row" style={{ alignItems: "center" }}>
            <div className="run-pipeline-section-label" style={{ margin: 0 }}>{auto.left?.length ? `What the AI editor could not fix by itself (${auto.left.length})` : "Nothing left for the AI editor"}</div>
            <span style={{ flex: 1 }} />
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

      {aiState && (
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
