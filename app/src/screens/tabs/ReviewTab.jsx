import { useCallback, useEffect, useState } from "react";
import { open as openDialog } from "@tauri-apps/plugin-dialog";

import { sendCommand } from "../../App.jsx";

/**
 * 02 · Review. The creator-workflow review loop (labs/review_loop and labs/qa, bundled into python_backend/labs).
 *
 *  1. An export XML goes in; the backend builds a review page (preview.mp4 + review.html) beside it. Notes and drawings are made on that page.
 *  2. The notes file the page saves (review_notes.json) goes in with the same XML; the backend revises the cut, builds the next version's review
 *     page, and QA re-measures every note on the new version from its files. The ledger below is that QA pass, not the revise step's own claim.
 */
const STATUS_LABEL = {
  "VERIFIED": "Verified",
  "APPLIED-UNMEASURED": "Applied, look at it",
  "NOT DONE": "Not done",
  "FAILED": "Failed",
};
const STATUS_CLASS = { "VERIFIED": "ok", "APPLIED-UNMEASURED": "warn", "NOT DONE": "warn", "FAILED": "bad" };

const fileName = (p) => (p || "").split("/").pop();

export default function ReviewTab({ subscribe }) {
  const [xml, setXml] = useState("");
  const [status, setStatus] = useState("idle"); // idle | building | built | failed
  const [result, setResult] = useState(null);
  const [message, setMessage] = useState("");
  const [rows, setRows] = useState([]);

  const [notesFile, setNotesFile] = useState("");
  const [notesStatus, setNotesStatus] = useState("idle"); // idle | working | done | failed
  const [notesStage, setNotesStage] = useState("");
  const [notesResult, setNotesResult] = useState(null);
  const [notesMessage, setNotesMessage] = useState("");

  useEffect(() => {
    return subscribe((ev) => {
      if (ev.type === "review_started") {
        setStatus("building");
        setRows([]);
        setMessage("");
      } else if (ev.type === "log" && status === "building" && /\[(PASS|FAIL|SKIP)\]/.test(ev.message || "")) {
        setRows((r) => [...r, ev.message.trim()]);
      } else if (ev.type === "review_built") {
        setStatus("built");
        setResult(ev);
      } else if (ev.type === "review_failed") {
        setStatus("failed");
        setMessage(ev.message || "The review page could not be built.");
      } else if (ev.type === "notes_started") {
        setNotesStatus("working");
        setNotesStage("Starting…");
        setNotesMessage("");
        setNotesResult(null);
      } else if (ev.type === "notes_stage") {
        setNotesStage(ev.stage || "");
      } else if (ev.type === "notes_applied") {
        setNotesStatus("done");
        setNotesResult(ev);
      } else if (ev.type === "notes_failed") {
        setNotesStatus("failed");
        setNotesMessage(ev.message || "The notes could not be applied.");
      }
    });
  }, [subscribe, status]);

  const pickXml = useCallback(async () => {
    const p = await openDialog({ multiple: false, filters: [{ name: "Premiere XML", extensions: ["xml"] }] });
    if (typeof p === "string") {
      setXml(p);
      setStatus("idle");
      setResult(null);
      setNotesStatus("idle");
      setNotesResult(null);
    }
  }, []);

  const pickNotes = useCallback(async () => {
    const p = await openDialog({ multiple: false, filters: [{ name: "Review notes", extensions: ["json"] }] });
    if (typeof p === "string") {
      setNotesFile(p);
      setNotesStatus("idle");
      setNotesResult(null);
    }
  }, []);

  const build = useCallback(async () => {
    if (!xml) return;
    setStatus("building");
    try {
      await sendCommand({ type: "build_review", xml });
    } catch (e) {
      setStatus("failed");
      setMessage(String(e));
    }
  }, [xml]);

  const applyNotes = useCallback(async () => {
    if (!xml || !notesFile) return;
    setNotesStatus("working");
    setNotesStage("Starting…");
    try {
      await sendCommand({ type: "apply_notes", xml, notes: notesFile });
    } catch (e) {
      setNotesStatus("failed");
      setNotesMessage(String(e));
    }
  }, [xml, notesFile]);

  const failed = rows.filter((r) => r.includes("[FAIL]"));
  const qa = notesResult?.qa;
  const counts = qa
    ? qa.notes.reduce((acc, n) => ({ ...acc, [n.status]: (acc[n.status] || 0) + 1 }), {})
    : {};
  const wholeBad = qa ? qa.whole_cut.filter((w) => w.ok === false) : [];

  return (
    <div className="review-tab">
      <div className="run-pipeline-section">
        <div className="run-pipeline-section-label">1 · The edit to review</div>
        <p className="pm-tab-sub">
          Pick the XML you exported from Premiere (or from this app). A review page is built beside it: the cut as a video, every edit decision on a
          timeline, and a place to leave timecoded notes and draw on the frame.
        </p>
        <div className="pm-tab-row">
          <button className="btn btn-ghost" onClick={pickXml} disabled={status === "building" || notesStatus === "working"}>
            {xml ? "Choose a different XML" : "Choose an XML…"}
          </button>
          {xml && <span className="transcript-row-name" title={xml}>{fileName(xml)}</span>}
        </div>
        <div className="pm-tab-row">
          <button className="btn btn-primary" onClick={build} disabled={!xml || status === "building"}>
            {status === "building" ? "Building the review page…" : "Build review page"}
          </button>
        </div>
      </div>

      {status === "failed" && (
        <div className="pm-tab-warnings" role="alert">
          {message}
        </div>
      )}

      {status === "built" && result && (
        <div className="run-pipeline-section">
          <div className="run-pipeline-section-label">Review page ready</div>
          <p className="pm-tab-sub">
            {result.sequence}: {result.clips} clips, {result.duration} s. The notes you save on the page come back as a review_notes.json file.
          </p>
          <div className="pm-tab-row">
            <button className="btn btn-primary" onClick={() => sendCommand({ type: "open_path", path: result.page })}>
              Open review page
            </button>
            <button className="btn btn-ghost" onClick={() => sendCommand({ type: "open_path", path: result.folder })}>
              Show folder
            </button>
          </div>
          {rows.length > 0 && (
            <div className="sync-section-hint">
              Layer checks: {rows.length - failed.length} of {rows.length} passed or skipped{failed.length ? `; ${failed.length} failed (see the log)` : ""}.
            </div>
          )}
        </div>
      )}

      <div className="run-pipeline-section">
        <div className="run-pipeline-section-label">2 · Apply the notes</div>
        <p className="pm-tab-sub">
          Pick the review_notes.json the page saved. The notes are turned into edits on this same XML, a new version and its review page are built, and every note is
          then checked again on the new version. Notes it cannot do are listed as not done, with the reason.
        </p>
        <div className="pm-tab-row">
          <button className="btn btn-ghost" onClick={pickNotes} disabled={notesStatus === "working"}>
            {notesFile ? "Choose a different notes file" : "Choose the notes file…"}
          </button>
          {notesFile && <span className="transcript-row-name" title={notesFile}>{fileName(notesFile)}</span>}
        </div>
        <div className="pm-tab-row">
          <button className="btn btn-primary" onClick={applyNotes} disabled={!xml || !notesFile || notesStatus === "working"}>
            {notesStatus === "working" ? "Working…" : "Apply notes and check"}
          </button>
          {!xml && <span className="sync-section-hint">Choose the XML in step 1 first.</span>}
        </div>
        {notesStatus === "working" && <div className="sync-section-hint">{notesStage}</div>}
      </div>

      {notesStatus === "failed" && (
        <div className="pm-tab-warnings" role="alert" style={{ whiteSpace: "pre-wrap" }}>
          {notesMessage}
        </div>
      )}

      {notesStatus === "done" && notesResult && !qa && (
        <div className="pm-tab-warnings" role="status">
          {notesResult.message}
        </div>
      )}

      {notesStatus === "done" && qa && (
        <div className="run-pipeline-section">
          <div className="run-pipeline-section-label">New version, checked</div>
          <p className="pm-tab-sub">
            {notesResult.applied} of {notesResult.notes} notes applied. {counts["VERIFIED"] || 0} verified,{" "}
            {(counts["APPLIED-UNMEASURED"] || 0)} applied but not measurable, {(counts["NOT DONE"] || 0)} not done, {(counts["FAILED"] || 0)} failed.
            {wholeBad.length ? ` ${wholeBad.length} whole-cut check(s) failed.` : ""}
            {qa.unrequested.length ? ` ${qa.unrequested.length} change(s) nobody asked for.` : ""}
          </p>
          <div className="transcripts-list">
            {qa.notes.map((n) => (
              <div className="transcript-row" key={n.note}>
                <div className="transcript-row-main">
                  <div className={`transcript-row-name review-status-${STATUS_CLASS[n.status] || "warn"}`}>
                    Note {n.note} at {n.time}s · {STATUS_LABEL[n.status] || n.status}
                  </div>
                  <div className="transcript-row-folder">{n.text}</div>
                  {n.rows.map((r, i) => (
                    <div className="sync-section-hint" key={i}>{r.detail}</div>
                  ))}
                </div>
              </div>
            ))}
          </div>
          {qa.unrequested.map((u, i) => (
            <div className="pm-tab-warnings" key={i}>{u}</div>
          ))}
          <div className="pm-tab-row">
            <button className="btn btn-primary" onClick={() => sendCommand({ type: "open_path", path: notesResult.page })}>
              Open the new review page
            </button>
            <button className="btn btn-ghost" onClick={() => sendCommand({ type: "open_path", path: qa.report })}>
              Open the QA report
            </button>
            <button className="btn btn-ghost" onClick={() => sendCommand({ type: "open_path", path: notesResult.folder })}>
              Show folder ({fileName(notesResult.xml)})
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
