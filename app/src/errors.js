// What went wrong, why, and what to do about it (HIG alerts.md and writing.md: an error that only names a fault leaves the person stuck).
// The raw text is never thrown away: it goes to the Activity log, where someone fixing the app can read it.

const RULES = [
  {
    match: /needs restarting to finish updating/i,
    what: "Post House has updated.",
    next: "Quit it and open it again to finish.",
  },
  {
    match: /This version cannot do that yet/i,
    what: "This part of the app is newer than the piece behind it.",
    next: "Quit Post House and open it again.",
  },
  {
    match: /no API key|api[_ ]key.*(missing|not set)/i,
    what: "Stories need an Anthropic API key.",
    next: "Add one in Settings, under AI.",
  },
  {
    match: /that XML is not there|No such file|not a file|does not exist/i,
    what: "That file is not where it was.",
    next: "It may have been moved or the drive unplugged. Choose it again.",
  },
  {
    match: /Premiere.*not found|could not find.*Premiere/i,
    what: "Adobe Premiere Pro was not found in your Applications folder.",
    next: "The XML will open with whatever your Mac uses for it instead.",
  },
  {
    match: /Read-only file system|Permission denied|Operation not permitted/i,
    what: "Post House could not write there.",
    next: "Check the drive is connected and not locked, then try again.",
  },
  {
    match: /No space left/i,
    what: "The disk is full.",
    next: "Free some space and try again.",
  },
  {
    match: /\b(ffmpeg|ffprobe)\b.*(not found|No such)/i,
    what: "The video tools Post House uses are missing.",
    next: "Reinstall Post House, or run its setup again from Settings.",
  },
  {
    match: /Timed out|timeout/i,
    what: "That took longer than expected and was stopped.",
    next: "Try it again. If it keeps happening, the footage may be on a slow or sleeping drive.",
  },
];

/** A raw backend message as three parts: { what, next, raw }. Anything unrecognised keeps its own words as `what`, with no invented advice. */
export function explain(message) {
  const raw = String(message || "").trim();
  for (const r of RULES) {
    if (r.match.test(raw)) return { what: r.what, next: r.next, raw };
  }
  // Not one we know: a traceback's last line is the only readable part of it
  const lines = raw.split("\n").filter(Boolean);
  const last = lines[lines.length - 1] || raw;
  const what = last.length > 180 ? `${last.slice(0, 177)}…` : last;
  return { what: what || "Something went wrong.", next: "", raw };
}

/** One line for a toast: what happened, then what to do. */
export function explainLine(message) {
  const e = explain(message);
  return e.next ? `${e.what} ${e.next}` : e.what;
}
