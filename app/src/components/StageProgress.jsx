import { useState } from "react";
import Icon from "./Icon.jsx";

const STAGE_LABELS = {
  aroll_proxies: "Preparing the interviews",
  broll_proxies: "Preparing the B-roll",
  audio_index: "Reading recorder audio",
  audio_sync: "Syncing recorder audio",
  transcribe: "Transcribing what was said",
  tag: "Describing the B-roll",
  proxies: "Preparing the footage",
};

const plain = (name) => name.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

export default function StageProgress({ stageName, stage }) {
  const [expanded, setExpanded] = useState(false);
  const label = STAGE_LABELS[stageName] || plain(stageName);
  const total = stage.total || 0;
  const completed = stage.completed || 0;
  const pct = total > 0 ? Math.min(100, (completed / total) * 100) : 0;

  const isDone = stage.status === "done";
  const isFailed = stage.status === "failed";
  const isCancelled = stage.status === "cancelled";

  // Collapse completed stages — show just a summary row
  if (isDone && !expanded) {
    return (
      <div className="progress-group done" onClick={() => setExpanded(true)}>
        <div className="progress-header">
          <span className="progress-label">
            <span className="progress-check"><Icon name="check" size={13} /></span> {label}
          </span>
          <span className="progress-stats">
            {stage.success || 0} done
            {(stage.failed || 0) > 0 && `, ${stage.failed} failed`}
            {(stage.skipped || 0) > 0 && `, ${stage.skipped} skipped`}
          </span>
        </div>
      </div>
    );
  }

  if (isFailed) {
    return (
      <div className="progress-group error">
        <div className="progress-header">
          <span className="progress-label">{label}</span>
          <span className="progress-stats">did not finish</span>
        </div>
        {stage.error && (
          <div className="progress-current error-msg">{stage.error}</div>
        )}
      </div>
    );
  }

  const barClass = isDone ? "done" : isFailed ? "error" : "";
  const statusText = isCancelled
    ? "Stopped"
    : isDone
    ? "Complete"
    : stage.current_file
    ? `${labelVerb(stageName)} ${truncate(stage.current_file, 48)}`
    : "Starting…";

  return (
    <div className="progress-group">
      <div className="progress-header">
        <span className="progress-label">{label}</span>
        <span className="progress-stats">
          {completed} / {total}
        </span>
      </div>
      <div className="progress-bar">
        <div
          className={`progress-bar-fill ${barClass}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="progress-current">{statusText}</div>
    </div>
  );
}

function labelVerb(stageName) {
  if (stageName.includes("proxies")) return "Preparing";
  if (stageName === "transcribe") return "Transcribing";
  if (stageName === "tag") return "Describing";
  if (stageName === "audio_index") return "Reading";
  return "Working on";
}

function truncate(s, n) {
  if (!s) return "";
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
