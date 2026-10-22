// Dragging a finished XML out of Post House and into Finder, or straight onto Premiere's project panel.
// macOS does the dragging itself (tauri-plugin-drag); a browser page has no such thing, so there it quietly does nothing.
import { startDrag } from "@crabnebula/tauri-plugin-drag";

export function dragFileOut(path, e) {
  if (!path) return;
  try { e?.preventDefault?.(); } catch (err) { /* not a real event */ }
  startDrag({ item: [String(path)], icon: "" }).catch(() => { /* not in a window that can drag, or the file went away */ });
}

/** Props for an element a file can be dragged out of. */
export function dragProps(path) {
  return {
    draggable: true,
    onDragStart: (e) => dragFileOut(path, e),
    title: "Drag into Finder or Premiere\tdrag",
  };
}
