/**
 * AutoIncludeNudge
 * ----------------
 * One-shot "Did you know?" modal shown the first time the user clicks
 * Export, to surface the Default Includes feature. After dismiss (or
 * acting on it) the auto_include_nudge_seen flag is set in settings
 * and this never appears again.
 *
 * Discovery problem: Default Includes is hidden behind a small titlebar
 * button most users will overlook. The export button is a much better
 * discovery moment because that's the exact instant the feature pays
 * off ("you're about to do the thing this saves time on").
 *
 * Surfaces three actions:
 *   - "Set up now"   → marks seen, opens AutoIncludeModal (export deferred;
 *                       user can re-click Export when ready)
 *   - "Maybe later"  → marks seen, opens ExportModal (proceeds normally)
 *   - close (×)      → same as "Maybe later"
 */
export default function AutoIncludeNudge({
  onSetUp,        // user clicked "Set up now"
  onSkip,         // user clicked "Maybe later" or × — proceed to export
}) {
  return (
    <div className="modal-overlay" onClick={onSkip}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Wait — did you know?</h2>
          <button className="modal-close" onClick={onSkip} aria-label="Close">×</button>
        </div>

        <div className="modal-body">
          <p style={{ marginTop: 0, fontSize: 14, lineHeight: 1.5 }}>
            You can set up files to include in <strong>every</strong> export
            — like your stock SFX library, company logos, or LUTs.
            PreCut adds them to the right bins automatically, so you
            don't have to drag them in for every project.
          </p>

          <div className="auto-include-nudge-examples">
            <div className="auto-include-nudge-example">
              <span className="auto-include-nudge-example-icon">📁</span>
              <span className="auto-include-nudge-example-from">
                Stock SFX folder
              </span>
              <span className="auto-include-nudge-example-arrow">→</span>
              <code className="auto-include-nudge-example-to">
                Audio / SFX
              </code>
            </div>
            <div className="auto-include-nudge-example">
              <span className="auto-include-nudge-example-icon">📄</span>
              <span className="auto-include-nudge-example-from">
                Company logo
              </span>
              <span className="auto-include-nudge-example-arrow">→</span>
              <code className="auto-include-nudge-example-to">
                Files / Colors
              </code>
            </div>
            <div className="auto-include-nudge-example">
              <span className="auto-include-nudge-example-icon">📁</span>
              <span className="auto-include-nudge-example-from">
                Music library
              </span>
              <span className="auto-include-nudge-example-arrow">→</span>
              <code className="auto-include-nudge-example-to">
                Audio / Music
              </code>
            </div>
          </div>

          <p style={{ fontSize: 12.5, color: "var(--fg-2)", marginBottom: 0 }}>
            Set up takes about 30 seconds. You can always change it later
            from the <strong>default includes</strong> button in the
            titlebar.
          </p>
        </div>

        <div className="modal-actions">
          <button
            className="btn btn-ghost"
            onClick={onSkip}
            style={{ marginRight: "auto" }}
          >
            Maybe later
          </button>
          <button className="btn btn-primary" onClick={onSetUp}>
            Set up now &rarr;
          </button>
        </div>
      </div>
    </div>
  );
}
