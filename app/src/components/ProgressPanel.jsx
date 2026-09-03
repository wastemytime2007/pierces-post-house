/**
 * ProgressPanel — one row per active/recent job.
 * Shows kind label, X/Y file count, current file, and progress bar.
 */
export default function ProgressPanel({ jobs }) {
  const entries = Object.entries(jobs);

  if (entries.length === 0) {
    return (
      <div className="empty-state">
        Drop your footage into the zones above, then click Start.
        <br />
        Proxies for A-roll and B-roll run in parallel.
      </div>
    );
  }

  // Order: running jobs first, then done
  entries.sort(([, a], [, b]) => {
    const order = { running: 0, failed: 1, cancelled: 2, done: 3 };
    return (order[a.status] ?? 99) - (order[b.status] ?? 99);
  });

  return (
    <>
      {entries.map(([jobId, job]) => {
        const pct = job.total > 0
          ? Math.min(100, (job.completed / job.total) * 100)
          : 0;
        const barClass =
          job.status === "done" ? "done"
            : job.status === "failed" ? "error"
              : "";
        const statusText = {
          running: job.current_file ? `encoding ${truncate(job.current_file, 40)}` : "starting…",
          done: "complete",
          failed: "failed",
          cancelled: "cancelled",
        }[job.status] || job.status;

        return (
          <div key={jobId} className="progress-group">
            <div className="progress-header">
              <span className="progress-label">{kindLabel(job.kind)}</span>
              <span className="progress-stats">
                {job.completed} / {job.total}
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
      })}
    </>
  );
}

function kindLabel(kind) {
  return ({
    aroll: "A-ROLL PROXIES",
    broll: "B-ROLL PROXIES",
    audio: "SOURCE AUDIO",
  })[kind] || kind.toUpperCase();
}

function truncate(s, n) {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
