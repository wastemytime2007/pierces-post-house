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
  const autoOnRef = useRef(true);
  const [autoOn, setAutoOn] = useState(() => {
    try { return localStorage.getItem("review.autoEdit") !== "off"; } catch (e) { return true; }
  });
  // The AI editor's own loop: { status: idle | running | done, round, of, label, fixing, summary, best, left, versions }
  const [auto, setAuto] = useState({ status: "idle" });
  const [showLeft, setShowLeft] = useState(true);

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
  }, []);

  useEffect(() => {
    return subscribe((ev) => {
      if (ev.type === "exports_listed") {
        setExportsList(ev.exports || []);
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
        setAuto({ status: "idle" });
        aiStartRef.current?.(ev.xml, ev.folder, "V1");
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
        if (!ev.auto) aiStartRef.current?.(ev.xml, ev.folder, `V${versionsRef.current.length + 1}`);    // the editor's own revisions are reviewed by its loop; yours start it again
        setVersions((vs) => {
          const next = [...vs, { label: `V${vs.length + 1}`, xml: ev.xml, folder: ev.folder, url: ev.url, qa: ev.qa, applied: ev.applied, notes: ev.notes }];
          setCur(next.length - 1);
          return next;
        });
        setNoteCount(0);
        setExportResult(null);
        setShowLedger(true);
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

  // Post the AI review's notes onto the page for the version being shown, once the page has said it is ready.
  flushRef.current = () => {
    const v = versionsRef.current[curRef.current];
    const notes = v && aiPending.current[v.label];
    if (!notes || !readyRef.current || !frameRef.current?.contentWindow) return;
    delete aiPending.current[v.label];
    frameRef.current.contentWindow.postMessage({ type: "review:add-notes", notes }, "*");
  };

  aiOnlyRef.current = (xml, folder, tag) => {
    sendCommand({ type: "ai_review", xml, folder, tag }).catch((e) => setAi((a) => ({ ...a, [tag]: { status: "failed", message: String(e) } })));
  };

  // A new version (the cut just built, or one made from your notes): the AI editor takes it from here, unless it is switched off, then only the review runs.
  aiStartRef.current = (xml, folder, tag) => {
    if (autoOnRef.current) {
      sendCommand({ type: "auto_edit", xml, folder, tag }).catch((e) => setAuto({ status: "done", summary: `The AI editor could not start: ${e}`, left: [], versions: [], rounds: [] }));
    } else {
      aiOnlyRef.current?.(xml, folder, tag);
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
      await sendCommand({ type: "apply_notes", xml: version.xml, notes_payload: payload, review_folder: version.folder });
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

  const exportXml = useCallback(async () => {
    if (!version) return;
    setError("");
    setExportResult(null);
    await sendCommand({ type: "export_xml", xml: version.xml, open: true });
  }, [version]);

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
          {making && <div className="sync-section-hint">{making}</div>}
          {!making && busy === "building" && <div className="sync-section-hint">Building the review page from the cut (about half a minute)…</div>}
          {!making && !busy && exportsList.length === 0 && <div className="sync-section-hint">No cut from this project yet. Pick ideas on the Ideas tab and press Review.</div>}
          {exportsList.length > 0 && (
            <div className="transcripts-list">
              {exportsList.slice(0, 8).map((x) => (
                <div className="transcript-row" key={x.path}>
                  <div className="transcript-row-main">
                    <div className="transcript-row-name">{x.name}</div>
                    <div className="transcript-row-folder">{new Date(x.mtime * 1000).toLocaleString()}</div>
                  </div>
                  <button className="btn btn-primary" disabled={!!busy} onClick={() => build(x.path)}>
                    Review
                  </button>
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
        <button className="btn btn-ghost" onClick={exportXml} disabled={!!busy} title="Check the XML, then open it in Premiere">
          Export XML → Premiere
        </button>
        <button className="btn btn-ghost" onClick={() => sendCommand({ type: "open_path", path: dirName(version.xml) })} disabled={!!busy}>
          Show in Finder
        </button>
        <button className="btn btn-ghost" onClick={() => { setVersions([]); setCur(0); setExportResult(null); setError(""); setInfo(""); sendCommand({ type: "list_exports" }).catch(() => {}); }} disabled={!!busy}>
          Other cut…
        </button>
      </div>

      {incoming && (
        <div className="pm-tab-warnings" role="status">
          <div className="pm-tab-row" style={{ alignItems: "center" }}>
            <span>A new export was just made: {fileName(incoming)}.</span>
            <button className="btn btn-primary" disabled={!!busy} onClick={() => { const x = incoming; setIncoming(""); build(x); }}>Review it instead</button>
            <button className="btn btn-ghost" onClick={() => setIncoming("")}>Keep this one</button>
          </div>
        </div>
      )}
      {busy === "applying" && <div className="sync-section-hint">{stage}</div>}
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

      {auto.status === "running" && (
        <div className="run-pipeline-section">
          <div className="run-pipeline-section-label" style={{ margin: 0 }}>
            AI editor at work: {auto.round ? `round ${auto.round} of ${auto.of}, fixing ${auto.fixing} note${auto.fixing === 1 ? "" : "s"} on ${auto.label}` : `reviewing ${auto.label}`}
          </div>
          <div className="sync-section-hint">
            It reviews the cut, submits the fixes it can make, and reviews the new version. Each version stays in the tabs above, and its notes are on the page. You can look around, or press Stop.
          </div>
        </div>
      )}
      {auto.status === "done" && (
        <div className={auto.outcome === "failed" || auto.outcome === "worse" || auto.outcome === "short" ? "pm-tab-warnings" : "run-pipeline-section"} role="status">
          <div className="pm-tab-row" style={{ alignItems: "center" }}>
            <div className="run-pipeline-section-label" style={{ margin: 0 }}>{auto.summary}</div>
            <span style={{ flex: 1 }} />
            {auto.left?.length > 0 && <button className="btn btn-ghost" onClick={() => setShowLeft((x) => !x)}>{showLeft ? "Hide" : "Show"} what is left</button>}
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

      <iframe
        key={version.url}
        ref={frameRef}
        title={`Review ${version.label}`}
        src={version.url}
        allow="autoplay; clipboard-write"
        style={{ flex: 1, minHeight: 640, width: "100%", border: "1px solid var(--border, #333)", borderRadius: 8, background: "#000" }}
      />
    </div>
  );
}
