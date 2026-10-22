// Every shortcut in one place. Opens with ? (outside a text field) or from the command palette.
const GROUPS = [
  { title: "Anywhere", rows: [["⌘K", "Command palette"], ["⌘,", "Settings"], ["?", "This list"], ["⌘⌥L", "Show or hide the activity log"]] },
  { title: "Steps", rows: [["⌘1", "Footage"], ["⌘2", "Stories"], ["⌘3", "Trim"], ["⌘4", "Finish"]] },
  { title: "Finish", rows: [["⌘O", "Choose an XML"], ["⌘G", "Get Creative"], ["⌘E", "Open in Premiere"], ["⌘[", "Previous version"], ["⌘]", "Next version"]] },
  { title: "On the review page", rows: [["Space", "Play or pause"], ["n", "Add a note at this moment"], ["d", "Draw on the frame"], ["Esc", "Cancel a note"]] },
];

export default function ShortcutsSheet({ onClose }) {
  return (
    <div className="rv-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="rv-sheet" role="dialog" aria-modal="true" aria-labelledby="sc-title" onKeyDown={(e) => { if (e.key === "Escape") onClose(); }} tabIndex={-1} ref={(el) => el && el.focus()}>
        <h2 id="sc-title">Keyboard shortcuts</h2>
        {GROUPS.map((g) => (
          <fieldset key={g.title}>
            <legend>{g.title}</legend>
            {g.rows.map(([k, label]) => (
              <div className="sc-row" key={label}><span>{label}</span><kbd>{k}</kbd></div>
            ))}
          </fieldset>
        ))}
        <div className="rv-sheet-actions"><button className="btn btn-primary" onClick={onClose}>Done</button></div>
      </div>
    </div>
  );
}
