import { useCallback, useEffect, useRef, useState } from "react";
import { open as openDialog } from "@tauri-apps/plugin-dialog";

import { sendCommand } from "../../App.jsx";

/**
 * 02 · Review. The review page itself (labs/review_loop: the video, every edit decision on a timeline, timecoded notes, drawing on the frame) shown in the app,
 * with the loop around it:
 *
 *   choose the cut  ->  leave notes on the page  ->  "Apply notes" (the page hands its notes to the app, the cut is revised, the next version opens here,
 *   every note is re-checked on it)  ->  "Export XML" (checked by the export check, then opened in Premiere for final touches).
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

  const version = versions[cur];
  versionsRef.current = versions;
  busyRef.current = busy;

  useEffect(() => {
    if (!onStatus) return;
    onStatus(
      busy === "building" ? "building the cut…"
      : busy === "applying" ? "applying notes…"
      : incoming ? "new export ready"
      : versions.length ? `${versions[cur]?.label || "V1"} open`
      : exportsList.length ? "cut ready" : "notes on a cut"
    );
  }, [onStatus, busy, incoming, versions, cur, exportsList]);

  useEffect(() => {
    sendCommand({ type: "list_exports" }).catch(() => {});
  }, []);

  useEffect(() => {
    return subscribe((ev) => {
      if (ev.type === "exports_listed") {
        setExportsList(ev.exports || []);
      } else if (ev.type === "export_complete" && ev.xml_path) {
        // The app just wrote an XML. Review it without being asked, unless a review is already open or building.
        const row = { path: ev.xml_path, name: fileName(ev.xml_path), mtime: Date.now() / 1000 };
        setExportsList((l) => [row, ...l.filter((x) => x.path !== row.path)]);
        if (!versionsRef.current.length && !busyRef.current) buildRef.current?.(ev.xml_path);
        else setIncoming(ev.xml_path);
      } else if (ev.type === "review_started") {
        setBusy("building");
        setError("");
        setInfo("");
      } else if (ev.type === "review_built") {
        setBusy("");
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
      if (frameRef.current && e.source === frameRef.current.contentWindow && e.data?.type === "review:count") setNoteCount(e.data.n || 0);
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
            Export a cut from the Ideas tab and it opens here by itself: the video, every edit decision on a timeline, and a place for timecoded notes and drawing
            on the frame. Leave notes, apply them to get the next version, then export the XML and open it in Premiere.
          </p>
          {busy === "building" && <div className="sync-section-hint">Building the review page from the export (about half a minute)…</div>}
          {!busy && exportsList.length === 0 && <div className="sync-section-hint">No export from this project yet.</div>}
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
        <button className="btn btn-primary" onClick={apply} disabled={!!busy} title="Revise the cut from the notes on the page and open the next version here">
          {busy === "applying"
            ? "Applying…"
            : noteCount
              ? `Apply ${noteCount} note${noteCount === 1 ? "" : "s"} → V${versions.length + 1}`
              : `Apply notes → V${versions.length + 1}`}
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
