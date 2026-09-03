import { useState } from "react";

const STAGE_LABELS = {
  aroll_proxies: "A-ROLL PROXIES",
  broll_proxies: "B-ROLL PROXIES",
  audio_index: "SOURCE AUDIO INDEX",
  transcribe: "TRANSCRIPTION",
  tag: "B-ROLL TAGGING",
};

export default function StageProgress({ stageName, stage }) {
  const [expanded, setExpanded] = useState(false);
  const label = STAGE_LABELS[stageName] || stageName.toUpperCase();
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
            <span className="progress-check">✓</span> {label}
          </span>
          <span className="progress-stats">
            {stage.success || 0} ok
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
          <span className="progress-label">✗ {label}</span>
          <span className="progress-stats">failed</span>
        </div>
        {stage.error && (
          <div className="progress-current error-msg">{stage.error}</div>
        )}
      </div>
    );
  }

  const barClass = isDone ? "done" : isFailed ? "error" : "";
  const statusText = isCancelled
    ? "cancelled"
    : isDone
    ? "complete"
    : stage.current_file
    ? `${labelVerb(stageName)} ${truncate(stage.current_file, 48)}`
    : "starting…";

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
  if (stageName.includes("proxies")) return "encoding";
  if (stageName === "transcribe") return "transcribing";
  if (stageName === "tag") return "indexing";
  if (stageName === "audio_index") return "probing";
  return "processing";
}

function truncate(s, n) {
  if (!s) return "";
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
