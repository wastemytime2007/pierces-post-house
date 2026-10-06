import { useCallback, useEffect, useState } from "react";
import { open as openDialog } from "@tauri-apps/plugin-dialog";

import { sendCommand } from "../../App.jsx";

/**
 * 02 · Review. First creator-workflow screen (labs/review_loop, bundled into python_backend/labs).
 *
 * An export XML goes in; the backend builds a review page (preview.mp4 + review.html) next to it. The page is where timecoded notes and drawings
 * are made; this tab only builds it and opens it. Applying a notes file as a revised cut is the next slice, not here yet.
 */
export default function ReviewTab({ subscribe }) {
  const [xml, setXml] = useState("");
  const [status, setStatus] = useState("idle"); // idle | building | built | failed
  const [result, setResult] = useState(null);
  const [message, setMessage] = useState("");
  const [rows, setRows] = useState([]);

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
      }
    });
  }, [subscribe, status]);

  const pick = useCallback(async () => {
    const p = await openDialog({ multiple: false, filters: [{ name: "Premiere XML", extensions: ["xml"] }] });
    if (typeof p === "string") {
      setXml(p);
      setStatus("idle");
      setResult(null);
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

  const failed = rows.filter((r) => r.includes("[FAIL]"));

  return (
    <div className="review-tab">
      <div className="run-pipeline-section">
        <div className="run-pipeline-section-label">The edit to review</div>
        <p className="pm-tab-sub">
          Pick the XML you exported from Premiere (or from this app). A review page is built beside it: the cut as a video, every edit decision on a
          timeline, and a place to leave timecoded notes and draw on the frame.
        </p>
        <div className="pm-tab-row">
          <button className="btn btn-ghost" onClick={pick} disabled={status === "building"}>
            {xml ? "Choose a different XML" : "Choose an XML…"}
          </button>
          {xml && <span className="transcript-row-name" title={xml}>{xml.split("/").pop()}</span>}
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
    </div>
  );
}
