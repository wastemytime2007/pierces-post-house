import { useEffect, useRef, useState } from "react";
import HelpTooltip from "./HelpTooltip.jsx";

/**
 * DropZone — one of three drop targets (aroll / broll / audio).
 *
 * Drag-and-drop strategy
 * ----------------------
 * Each DropZone tracks its own hover state by listening to a mix of
 * events on its own DOM element:
 *
 *   - mouseenter / mouseleave  — fire for normal mouse movement and,
 *                                on some platforms, during an OS-level
 *                                file drag too
 *   - dragenter / dragleave    — fire for HTML5-style drags (e.g.
 *                                elements dragged within the browser),
 *                                and sometimes for OS file drags when
 *                                Tauri's native handler allows them
 *                                through
 *
 * Whichever signal type arrives first wins. When a zone detects a
 * hover, it writes its `kind` into the module-level `currentHoveredKind`
 * variable. IngestTab reads that variable at Tauri drop time to decide
 * which zone to route the file paths to.
 *
 * A module-level variable is acceptable here because only one zone can
 * be under the cursor at a time, and the DOM rendering is single-
 * threaded. If a second zone's enter fires, it correctly overwrites
 * the first. The leave handler only clears the variable if THIS zone
 * is currently the recorded one, so we don't race with a subsequent
 * enter on another zone.
 *
 * Why not use elementFromPoint + Tauri's position payload?
 * The position payload's coordinate system is subtly broken on macOS
 * (Tauri issue #10744 — off by ~28px for the titlebar) and device-
 * pixel-ratio conversion adds another failure mode. DOM-native events
 * avoid coordinate math entirely and route correctly even if Tauri's
 * position math is wrong.
 *
 * Props
 *   kind            — "aroll" | "broll" | "audio"
 *   label, title    — section header copy
 *   description     — shown when no items yet
 *   help            — optional JSX for HelpTooltip next to label
 *   items           — list of SourceFolder objects (or legacy strings)
 *   onRemove        — callback when a file's × is clicked
 *   onPick          — "Browse files…" handler
 *   onPickFolder    — "Pick folder…" handler
 */

// Module-level hot-zone marker. Only one zone can be under the cursor
// at a time; whichever zone most recently detected an enter writes
// its kind here. IngestTab reads this on Tauri drag-drop events.
let currentHoveredKind = null;
export function getHoveredKind() { return currentHoveredKind; }
export function clearHoveredKind() { currentHoveredKind = null; }

export default function DropZone({
  kind, label, title, description, help,
  items,
  onRemove, onPick, onPickFolder,
}) {
  const [isOver, setIsOver] = useState(false);
  const rootRef = useRef(null);

  // Wire up enter/leave listeners on the outer div. Using useEffect +
  // native addEventListener rather than React's onMouseEnter so we can
  // cleanly support drag events too (React's synthetic drag events get
  // swallowed in some Tauri setups; native ones fire more reliably).
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;

    const markEnter = () => {
      currentHoveredKind = kind;
      setIsOver(true);
    };
    const markLeave = () => {
      // Only clear the module variable if WE were the current hover.
      // Otherwise another zone has already taken over and we'd be
      // clearing their claim. Always clear our own visual state though.
      if (currentHoveredKind === kind) currentHoveredKind = null;
      setIsOver(false);
    };

    // Listen to all four event types. Whichever fires first wins for
    // "this zone is now hot." Redundant listeners are harmless because
    // calling markEnter twice is idempotent.
    el.addEventListener("mouseenter", markEnter);
    el.addEventListener("mouseleave", markLeave);
    el.addEventListener("dragenter", markEnter);
    el.addEventListener("dragleave", markLeave);

    return () => {
      el.removeEventListener("mouseenter", markEnter);
      el.removeEventListener("mouseleave", markLeave);
      el.removeEventListener("dragenter", markEnter);
      el.removeEventListener("dragleave", markLeave);
      // If we were the hot zone when we unmount, clean up.
      if (currentHoveredKind === kind) currentHoveredKind = null;
    };
  }, [kind]);

  return (
    <div
      ref={rootRef}
      className={`dropzone ${isOver ? "active" : ""} ${items.length > 0 ? "has-items" : ""}`}
      data-kind={kind}
    >
      <div className="dropzone-header">
        <span className="dropzone-label">
          {label}
          {help && <HelpTooltip>{help}</HelpTooltip>}
        </span>
        {items.length > 0 && (
          <span className="dropzone-count">{items.length} item{items.length !== 1 ? "s" : ""}</span>
        )}
      </div>
      <div className="dropzone-title">{title}</div>

      {items.length === 0 ? (
        <>
          <div className="dropzone-description">{description}</div>
          <div style={{ marginTop: "auto", display: "flex", gap: 14 }}>
            <a className="dropzone-picklink" onClick={onPick}>Browse files…</a>
            <a className="dropzone-picklink" onClick={onPickFolder}>Pick folder…</a>
          </div>
        </>
      ) : (
        <>
          <div className="dropzone-filelist">
            {items.map((item) => {
              // Support both old API (string) and new API (SourceFolder object)
              const path = typeof item === "string" ? item : item.root_path;
              const display = typeof item === "string" ? basename(item) : item.display_name;
              const isFile = typeof item === "string" ? false : item.is_file;
              const fileCount = typeof item === "string"
                ? null
                : Object.keys(item.files || {}).length;
              return (
                <div key={path} className="dropzone-fileitem" title={path}>
                  <span className="item-name">
                    {display}
                    {!isFile && fileCount > 0 && (
                      <span className="item-count"> · {fileCount} file{fileCount !== 1 ? "s" : ""}</span>
                    )}
                  </span>
                  <span className="remove" onClick={() => onRemove(path)}>×</span>
                </div>
              );
            })}
          </div>
          <div style={{ display: "flex", gap: 14 }}>
            <a className="dropzone-picklink" onClick={onPick}>+ files</a>
            <a className="dropzone-picklink" onClick={onPickFolder}>+ folder</a>
          </div>
        </>
      )}
    </div>
  );
}

function basename(p) {
  const parts = p.split("/");
  return parts[parts.length - 1] || p;
}
