/**
 * AutoIncludeHelp
 * ---------------
 * A walkthrough modal explaining the Default Includes feature: what it
 * does, why you'd want it, and how to use it. Same shape as ApiKeyHelp —
 * sections of prose with examples and a "Got it" button.
 *
 * Surfaced two ways:
 *   1. From the (?) icon next to "Default Includes" in the AutoIncludeModal
 *      header.
 *   2. From the "Learn more" link in the empty state when no rules exist.
 *
 * The content is intentionally a little longer than strictly necessary —
 * editors often haven't seen this pattern before and it's better to
 * over-explain once than have them give up because the destination
 * dropdown felt cryptic.
 */

export default function AutoIncludeHelp({ onClose }) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal help-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Default Includes — how it works</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">×</button>
        </div>

        <div className="modal-body help-body">

          <section className="help-section">
            <h3 className="help-heading">What is this?</h3>
            <p>
              Most editors re-use the same files across every project —
              a <strong>stock SFX library</strong>, a{" "}
              <strong>company logo</strong>, a{" "}
              <strong>music library</strong>, an{" "}
              <strong>outro template</strong>. The{" "}
              <strong>Default Includes</strong> feature lets you tell
              PreCut about those files{" "}
              <em>once</em>, and then they're silently added to every
              Premiere export — already organized into the right bins
              for you when you import.
            </p>
            <p>
              No more dragging the same SFX folder into every new
              Premiere project, or fishing through your stock library
              for the company stinger.
            </p>
          </section>

          <section className="help-section">
            <h3 className="help-heading">Files vs. Folders</h3>
            <p>
              Two kinds of rules:
            </p>
            <ul className="help-steps">
              <li>
                <strong>File rule</strong> — pick one specific file. It
                gets included in every export. Good for one-off assets
                like a single logo or stinger.
              </li>
              <li>
                <strong>Folder rule</strong> — pick a folder, and{" "}
                <em>everything inside</em> gets included. Good for
                libraries that grow over time (your SFX collection,
                your music library). Add new files to the folder and
                they'll show up in your next export automatically.
              </li>
            </ul>
            <p>
              For folders, you can add a{" "}
              <strong>file filter</strong> like <code>*.wav</code> to
              limit which files get pulled in. Skip the filter and
              PreCut will include any media file (audio, video, image)
              it recognizes.
            </p>
          </section>

          <section className="help-section">
            <h3 className="help-heading">Where things go (destinations)</h3>
            <p>
              Every rule has a <strong>destination</strong> — the bin
              in Premiere's project panel where the files land. PreCut
              creates a standard bin tree on every export:
            </p>
            <pre className="help-pre">
{`📁 Seq
   └ v1              (your sequences live here)
   └ Final           (← drop outros / finals here)
📁 Footage
   └ A-Roll          (your interview footage)
   └ B-Roll          (← drop stock footage here)
📁 Audio
   └ Source Audio    (your lavs / boom mics — auto-managed)
   └ Music           (← drop royalty-free music here)
   └ SFX             (← drop your SFX library here)
📁 Files
   └ Overlays        (safe-zone PNGs — auto-managed)
   └ Colors          (← drop LUTs / color references here)
   └ Nested Seqs     (← drop reusable sub-sequences here)`}
            </pre>
            <p>
              Pick from the dropdown to use one of those standard
              destinations — that handles 90% of cases.
            </p>
          </section>

          <section className="help-section">
            <h3 className="help-heading">Custom destinations</h3>
            <p>
              Want something the dropdown doesn't cover — a bin called{" "}
              <code>Files / Logos</code> for your brand assets, or a
              sub-bin called <code>Audio / Music / Royalty Free</code>?
              Pick <strong>Custom path…</strong> and type the path
              you want, using <code>/</code> to separate parent and
              child bins.
            </p>
            <p>
              PreCut will create any missing bins on import. Existing
              bins (matched case-insensitively) are reused.
            </p>
          </section>

          <section className="help-section">
            <h3 className="help-heading">Common patterns</h3>
            <ul className="help-steps">
              <li>
                <strong>Stock SFX library</strong> — Add Folder, point
                it at your SFX collection, destination{" "}
                <code>Audio / SFX</code>. Skip the file filter.
              </li>
              <li>
                <strong>Company logo</strong> — Add File, pick the PNG,
                custom destination <code>Files / Logos</code>.
              </li>
              <li>
                <strong>Color reference stills</strong> — Add Folder of
                color reference images, destination{" "}
                <code>Files / Colors</code>. (Note: actual{" "}
                <code>.cube</code> LUTs can&rsquo;t be imported via
                Premiere XML — save them as Lumetri presets in Premiere
                instead. PreCut will warn if you try to add one.)
              </li>
              <li>
                <strong>Outro / company stinger</strong> — Add File for
                the .mov, destination <code>Seq / Final</code>.
              </li>
            </ul>
          </section>

          <section className="help-section">
            <h3 className="help-heading">Things to know</h3>
            <ul className="help-steps">
              <li>
                Rules are saved per-user, not per-project. They apply to{" "}
                <em>every</em> export from <em>every</em> PreCut
                project on this Mac.
              </li>
              <li>
                Files are referenced from their original location on
                disk — PreCut doesn't copy them. If you move a file,
                update the rule.
              </li>
              <li>
                If a file goes missing between the time you set up the
                rule and when you export, PreCut just skips it and
                logs a warning. Your export still completes.
              </li>
              <li>
                Empty bins still get the standard{" "}
                <code>(placeholder — delete me)</code> clip. If you
                add a rule for a bin, the placeholder is replaced by
                your real content.
              </li>
            </ul>
          </section>

        </div>

        <div className="modal-actions">
          <button className="btn btn-primary" onClick={onClose}>
            Got it
          </button>
        </div>
      </div>
    </div>
  );
}
