// Undo for the choices a person makes in the app, so a wrong click costs nothing (HIG undo-and-redo.md).
// Each entry is a pair of closures: how to go back, and how to come forward again. Screens register what they change; the shell binds Command-Z and Shift-Command-Z.
// Deliberately not a general document history: it covers choices (which stories, which sequences, a note), never the footage or a written file.

const past = [];
const future = [];
const subs = new Set();
const LIMIT = 40;

function changed() { subs.forEach((f) => f()); }

/** Record something that can be taken back. `label` is shown in the Edit menu's place in the shortcuts sheet. */
export function record(label, undo, redo) {
  past.push({ label, undo, redo });
  if (past.length > LIMIT) past.shift();
  future.length = 0;
  changed();
}

export function undo() {
  const e = past.pop();
  if (!e) return null;
  e.undo();
  future.push(e);
  changed();
  return e.label;
}

export function redo() {
  const e = future.pop();
  if (!e) return null;
  e.redo();
  past.push(e);
  changed();
  return e.label;
}

export function canUndo() { return past.length > 0; }
export function canRedo() { return future.length > 0; }
export function nextUndoLabel() { return past.length ? past[past.length - 1].label : null; }

/** Leaving a project starts a fresh history: its choices do not belong to the next one. */
export function clearUndo() { past.length = 0; future.length = 0; changed(); }

export function subscribeUndo(f) { subs.add(f); return () => subs.delete(f); }
