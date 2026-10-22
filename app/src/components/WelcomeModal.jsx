import { useState } from "react";

/**
 * WelcomeModal
 * ------------
 * Shown once on the first post-setup launch (tracked via welcome_seen
 * flag in settings.json). Four screens, tabbable with Back/Next, with
 * a Skip option at any time.
 *
 * Design philosophy: tell users WHAT PreCut is, WHAT the workflow
 * looks like, and WHERE the two friction points are (API key, first
 * project creation). Don't describe buttons or UI details — those are
 * the tour tooltips' job.
 *
 * Props:
 *   onComplete — called when user clicks "Finish" on the last screen
 *                OR clicks "Skip" on any screen. Parent should mark
 *                welcome_seen=true and close the modal.
 *   onOpenApiKeyHelp — called when user clicks "Learn more" on the
 *                      API key screen. Parent should close this modal
 *                      and open the ApiKeyHelp modal.
 */

// Inline SVG illustrations — each screen has a simple icon that sits
// above the text. Using inline SVG avoids yet another PNG asset and
// keeps the illustrations crisp at any size.
function IconPitch() {
  return (
    <svg width="72" height="72" viewBox="0 0 72 72" fill="none">
      <rect x="8" y="14" width="56" height="36" rx="4"
            stroke="currentColor" strokeWidth="2" />
      <path d="M28 26 L28 38 L40 32 Z" fill="currentColor" />
      <path d="M16 56 L56 56" stroke="currentColor" strokeWidth="2"
            strokeLinecap="round" />
      <path d="M20 60 L52 60" stroke="currentColor" strokeWidth="2"
            strokeLinecap="round" opacity="0.5" />
    </svg>
  );
}
function IconWorkflow() {
  return (
    <svg width="72" height="72" viewBox="0 0 72 72" fill="none">
      <circle cx="16" cy="36" r="8" stroke="currentColor" strokeWidth="2" />
      <circle cx="36" cy="36" r="8" stroke="currentColor" strokeWidth="2" />
      <circle cx="56" cy="36" r="8" stroke="currentColor" strokeWidth="2" />
      <path d="M24 36 L28 36" stroke="currentColor" strokeWidth="2"
            strokeLinecap="round" />
      <path d="M44 36 L48 36" stroke="currentColor" strokeWidth="2"
            strokeLinecap="round" />
    </svg>
  );
}
function IconKey() {
  return (
    <svg width="72" height="72" viewBox="0 0 72 72" fill="none">
      <circle cx="26" cy="36" r="10" stroke="currentColor" strokeWidth="2" />
      <circle cx="26" cy="36" r="3" fill="currentColor" />
      <path d="M36 36 L58 36" stroke="currentColor" strokeWidth="2"
            strokeLinecap="round" />
      <path d="M50 36 L50 44" stroke="currentColor" strokeWidth="2"
            strokeLinecap="round" />
      <path d="M56 36 L56 42" stroke="currentColor" strokeWidth="2"
            strokeLinecap="round" />
    </svg>
  );
}
function IconReady() {
  return (
    <svg width="72" height="72" viewBox="0 0 72 72" fill="none">
      <path d="M20 38 L32 50 L54 24"
            stroke="currentColor" strokeWidth="3"
            strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}


const SCREENS = [
  {
    key: "pitch",
    Icon: IconPitch,
    title: "Welcome to Post House",
    body: (
      <>
        <p>
          Post House finds the pieces of your footage that tell a story,
          then helps you finish them: captions, graphics, music, sound
          effects and transitions, ready for Premiere.
        </p>
        <p className="welcome-body-muted">
          Everything runs on your Mac. Your footage never leaves it.
        </p>
      </>
    ),
  },
  {
    key: "workflow",
    Icon: IconWorkflow,
    title: "How it works",
    body: (
      <>
        <ol className="welcome-numbered">
          <li>
            <strong>Footage.</strong> Point it at your shoot. Interviews are
            transcribed, B-roll described, recorder audio synced.
          </li>
          <li>
            <strong>Stories.</strong> It finds stories in what was said. You
            choose the ones to cut and export one XML.
          </li>
          <li>
            <strong>Trim.</strong> In Premiere, you edit the storyline and the
            delivery your way, then export the XML back.
          </li>
          <li>
            <strong>Finish.</strong> Drop that XML here. Captions, graphics,
            music and sound go on, and it goes back to Premiere.
          </li>
        </ol>
      </>
    ),
  },
  {
    key: "apikey",
    Icon: IconKey,
    title: "One setup step: a Claude API key",
    body: (
      <>
        <p>
          Post House uses <strong>Claude</strong> (made by Anthropic) for the
          &ldquo;figure out good cuts&rdquo; part. To talk to Claude,
          you&rsquo;ll need an <strong>API key</strong> — a short
          one-time signup at{" "}
          <code>console.anthropic.com</code>.
        </p>
        <p>
          The minimum credit purchase is <strong>$5</strong>, which
          typically lasts 5–25 Post House projects depending on how much
          footage you&rsquo;re analyzing.
        </p>
        <p className="welcome-body-muted">
          You can finish this later — Post House works fine for indexing
          without a key. You&rsquo;ll just need one before you can
          generate cut ideas.
        </p>
      </>
    ),
    showApiKeyHelpLink: true,
  },
  {
    key: "ready",
    Icon: IconReady,
    title: "You&rsquo;re ready",
    body: (
      <>
        <p>
          Click <strong>Finish</strong> below and you&rsquo;ll land on
          the project list. Create your first project to get started.
        </p>
        <p className="welcome-body-muted">
          This welcome screen won&rsquo;t appear again — but you can get
          API key help any time via the (?) button next to the key field
          in Settings.
        </p>
      </>
    ),
  },
];


export default function WelcomeModal({ onComplete, onOpenApiKeyHelp }) {
  const [idx, setIdx] = useState(0);
  const screen = SCREENS[idx];
  const isFirst = idx === 0;
  const isLast = idx === SCREENS.length - 1;

  const next = () => {
    if (isLast) {
      onComplete();
    } else {
      setIdx(idx + 1);
    }
  };
  const back = () => {
    if (!isFirst) setIdx(idx - 1);
  };

  return (
    <div className="modal-overlay welcome-overlay">
      {/* No onClick on overlay — the welcome modal should require an
          explicit Skip or Finish, not dismiss on click-outside. */}
      <div className="modal welcome-modal">

        {/* Progress dots at the top */}
        <div className="welcome-progress" aria-label="progress">
          {SCREENS.map((s, i) => (
            <span
              key={s.key}
              className={`welcome-dot ${i === idx ? "active" : ""} ${i < idx ? "past" : ""}`}
            />
          ))}
        </div>

        <div className="welcome-body">
          <div className="welcome-icon">
            <screen.Icon />
          </div>
          <h2
            className="welcome-title"
            // title uses entities for smart quotes so escape them via dangerouslySetInnerHTML
            dangerouslySetInnerHTML={{ __html: screen.title }}
          />
          <div className="welcome-text">
            {screen.body}
          </div>

          {screen.showApiKeyHelpLink && (
            <button
              className="welcome-help-link"
              onClick={onOpenApiKeyHelp}
            >
              Don&rsquo;t know what this is? &rarr;
            </button>
          )}
        </div>

        <div className="welcome-actions">
          <button
            className="btn btn-ghost welcome-skip"
            onClick={onComplete}
          >
            Skip
          </button>
          <div className="welcome-nav">
            <button
              className="btn btn-ghost"
              onClick={back}
              disabled={isFirst}
            >
              Back
            </button>
            <button
              className="btn btn-primary"
              onClick={next}
            >
              {isLast ? "Finish" : "Next"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
