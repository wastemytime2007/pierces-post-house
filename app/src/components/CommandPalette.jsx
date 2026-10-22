// ⌘K: one door to every place and action. Type to filter, arrows to move, Return to run, Escape to leave.
import { useEffect, useMemo, useRef, useState } from "react";
import Icon from "./Icon.jsx";

// A forgiving match: every typed letter must appear in order; consecutive and word-start hits score higher.
function score(query, text) {
  const q = query.toLowerCase().replace(/\s+/g, "");
  const t = text.toLowerCase();
  if (!q) return 1;
  let ti = 0, s = 0, run = 0;
  for (const ch of q) {
    const at = t.indexOf(ch, ti);
    if (at < 0) return -1;
    run = at === ti ? run + 1 : 0;
    s += 1 + run * 2 + (at === 0 || t[at - 1] === " " ? 3 : 0);
    ti = at + 1;
  }
  return s - t.length * 0.01;
}

export default function CommandPalette({ items, onClose }) {
  const [q, setQ] = useState("");
  const [at, setAt] = useState(0);
  const listRef = useRef(null);

  const results = useMemo(() => {
    const scored = items.map((it) => ({ it, s: score(q, `${it.label} ${it.keywords || ""}`) })).filter((x) => x.s >= 0);
    if (q) scored.sort((a, b) => b.s - a.s);
    return scored.map((x) => x.it);
  }, [items, q]);

  useEffect(() => { setAt(0); }, [q]);
  useEffect(() => { listRef.current?.querySelector(`[data-i="${at}"]`)?.scrollIntoView({ block: "nearest" }); }, [at]);

  const run = (it) => { onClose(); setTimeout(() => it.run(), 30); };
  const onKey = (e) => {
    if (e.key === "Escape") { e.preventDefault(); onClose(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setAt((i) => Math.min(results.length - 1, i + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setAt((i) => Math.max(0, i - 1)); }
    else if (e.key === "Enter" && results[at]) { e.preventDefault(); run(results[at]); }
  };

  let lastGroup = null;
  return (
    <div className="cp-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="cp" role="dialog" aria-modal="true" aria-label="Command palette" onKeyDown={onKey}>
        <div className="cp-field">
          <Icon name="sparkles" size={16} />
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Go anywhere, or do anything" aria-label="Search commands" spellCheck={false} />
          <kbd>esc</kbd>
        </div>
        <div className="cp-list" ref={listRef} role="listbox">
          {results.length === 0 && <div className="cp-empty">Nothing matches “{q}”.</div>}
          {results.map((it, i) => {
            const head = it.group !== lastGroup ? it.group : null;
            lastGroup = it.group;
            return (
              <div key={it.id}>
                {head && !q && <div className="cp-group">{head}</div>}
                <button data-i={i} role="option" aria-selected={i === at} className={`cp-item ${i === at ? "on" : ""}`} onMouseMove={() => setAt(i)} onClick={() => run(it)}>
                  <span className="cp-icon"><Icon name={it.icon || "chevronRight"} size={16} /></span>
                  <span className="cp-label">{it.label}</span>
                  {it.hint && <span className="cp-hint">{it.hint}</span>}
                </button>
              </div>
            );
          })}
        </div>
        <div className="cp-foot"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>↵</kbd> run</span><span><kbd>esc</kbd> close</span></div>
      </div>
    </div>
  );
}
