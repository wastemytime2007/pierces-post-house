// Every shortcut in one place, grouped by where you are when it works. Opens with ? (outside a text field) or from the command palette.
// A list that says a key exists when it does not is worse than no list, so `shortcuts.test.mjs` checks these against the code that listens for them.
export const GROUPS = [
  {
    title: "Anywhere",
    rows: [
      ["⌘K", "Command palette"],
      ["⌘,", "Settings"],
      ["⌘Z", "Undo the last choice"],
      ["⇧⌘Z", "Redo it"],
      ["?", "This list"],
      ["⌘⌥L", "Show or hide the activity log"],
    ],
  },
  {
    title: "Projects",
    rows: [
      ["⌘N", "New project"],
      ["⇧⌘O", "All projects"],
    ],
  },
  {
    title: "Steps",
    rows: [["⌘1", "Footage"], ["⌘2", "Stories"], ["⌘3", "Trim"], ["⌘4", "Finish"]],
  },
  {
    title: "Finish",
    rows: [
      ["⌘O", "Choose an XML"],
      ["⌘G", "Get Creative"],
      ["⌘E", "Open in Premiere"],
      ["⌘[", "Previous version"],
      ["⌘]", "Next version"],
      ["⌘⌥S", "Show or hide the sequence list"],
      ["⌘⌥B", "All sequences"],
      ["⇧⌘R", "Show in Finder"],
    ],
  },
  {
    title: "Choosing sequences",
    rows: [
      ["← → ↑ ↓", "Move between sequences"],
      ["Space", "See that frame large"],
      ["Shift-click", "Pick a run of them"],
      ["⌘↵", "Open the ones chosen"],
      ["Esc", "Close"],
    ],
  },
  {
    title: "On the review page",
    rows: [
      ["Space", "Play or pause"],
      ["L", "Play forward, again for faster"],
      ["K", "Stop"],
      ["J", "Back, again for faster"],
      [", .", "One frame back or forward"],
      ["I  O", "Mark the start and end of a range"],
      ["Home  End", "To the start or the end"],
      ["n", "Add a note at this moment"],
      ["d", "Draw on the frame"],
      ["Esc", "Cancel a note"],
    ],
  },
  {
    title: "Comparing two versions",
    rows: [
      ["Space", "Play both"],
      [", .", "One frame, on both"],
      ["Esc", "Close"],
    ],
  },
];

export default function ShortcutsSheet({ onClose }) {
  return (
    <div className="rv-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="rv-sheet sc-sheet" role="dialog" aria-modal="true" aria-labelledby="sc-title" onKeyDown={(e) => { if (e.key === "Escape") onClose(); }} tabIndex={-1} ref={(el) => el && el.focus()}>
        <h2 id="sc-title">Keyboard shortcuts</h2>
        <div className="sc-cols">
          {GROUPS.map((g) => (
            <fieldset key={g.title}>
              <legend>{g.title}</legend>
              {g.rows.map(([k, label]) => (
                <div className="sc-row" key={`${g.title}:${label}`}><span>{label}</span><kbd>{k}</kbd></div>
              ))}
            </fieldset>
          ))}
        </div>
        <div className="rv-sheet-actions"><button className="btn btn-primary" onClick={onClose}>Done</button></div>
      </div>
    </div>
  );
}
