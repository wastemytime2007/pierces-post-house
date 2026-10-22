import { useEffect, useRef } from "react";

/**
 * LogView — right sidebar, scrolling list of events.
 * Auto-scrolls to bottom as new entries arrive UNLESS the user has
 * scrolled up (then we respect their position).
 */
export default function LogView({ entries, onClear }) {
  const scrollRef = useRef(null);
  const pinnedToBottom = useRef(true);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    pinnedToBottom.current = distance < 40;
  };

  useEffect(() => {
    if (!scrollRef.current || !pinnedToBottom.current) return;
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [entries]);

  return (
    <div className="log-pane">
      <div className="log-header">
        <span className="log-title">Activity</span>
        <button className="log-clear" onClick={onClear} disabled={entries.length === 0}>Clear</button>
      </div>
      <div
        className="log-entries"
        ref={scrollRef}
        onScroll={handleScroll}
      >
        {entries.length === 0 ? (
          <div className="log-empty">
            <b>Nothing yet.</b>
            <span>Progress, and anything that goes wrong, shows up here while a job runs.</span>
          </div>
        ) : (
          entries.map((entry, idx) => (
            <div
              key={idx}
              className={`log-entry ${entry.level || ""}`}
            >
              <span className="timestamp">{entry.ts}</span>
              <span className="message">{entry.message}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
