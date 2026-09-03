/**
 * ApiKeyHelp
 * ----------
 * Modal that explains what an Anthropic API key is, how to sign up,
 * and what it costs. Shown in two situations:
 *
 *   1. Auto-opened ONCE when a user lands on settings with no key
 *      set (tracked via api_key_help_auto_shown in settings).
 *   2. On demand when the user clicks the (?) button next to the
 *      API key input.
 *
 * Intentionally content-heavy — video editors are often unfamiliar
 * with API keys and LLM pricing. We'd rather over-explain here and
 * let pros skip the parts they already know than under-explain and
 * leave them stuck.
 *
 * Cost numbers below are Anthropic's April 2026 public pricing.
 * If pricing changes, update the table and the "what $5 gets you"
 * math at the same time.
 */

// Tauri v2's @tauri-apps/plugin-shell exports `open(path_or_url)`, not
// `openUrl`. (That name belongs to the separate `plugin-opener` package,
// which we don't depend on.) We alias the import to `openExternal` at
// the call site to avoid shadowing the browser's built-in `window.open`
// which we use as the fallback path.
import { open as openExternal } from "@tauri-apps/plugin-shell";

// Helper: open an external URL via Tauri's shell plugin instead of
// letting React render a raw <a href> that might open inside the
// WebView. The shell plugin hands the URL to the system browser.
async function externalLink(url) {
  try {
    await openExternal(url);
  } catch (e) {
    // Fallback — window.open works in Tauri's dev server but may be
    // blocked in production builds. Silent fail is acceptable here;
    // the URL is also shown as text so users can copy it.
    try { window.open(url, "_blank"); } catch (_) {}
  }
}

export default function ApiKeyHelp({ onClose }) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal help-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Getting a Claude API key</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">×</button>
        </div>

        <div className="modal-body help-body">

          {/* Section 1 — WHAT is this thing */}
          <section className="help-section">
            <h3 className="help-heading">What is this?</h3>
            <p>
              PreCut uses <strong>Claude</strong>, an AI model from
              Anthropic, to read through your footage transcripts and
              figure out which clips make the best story. To talk to
              Claude, PreCut needs an <strong>API key</strong> — a long
              string of characters that tells Anthropic which account
              to bill for the usage.
            </p>
            <p>
              The key is stored only on your Mac (at
              <code> ~/Library/Application Support/PreCut/settings.json</code>{" "}
              with owner-only permissions). PreCut never sends it anywhere
              except directly to Anthropic.
            </p>
          </section>

          {/* Section 2 — how to sign up */}
          <section className="help-section">
            <h3 className="help-heading">Getting a key in 4 steps</h3>
            <ol className="help-steps">
              <li>
                Go to{" "}
                <button
                  className="help-link"
                  onClick={() => externalLink("https://console.anthropic.com/")}
                >
                  console.anthropic.com
                </button>{" "}
                and create an account. Use email, Google, or GitHub sign-in —
                whichever you prefer. This is a <em>developer</em> account,
                separate from a Claude chat subscription (you don't need
                a Claude Pro plan to use the API).
              </li>
              <li>
                Verify your email, then add <strong>$5 of credit</strong>{" "}
                under Settings → Billing → Buy credits. This is the minimum
                Anthropic accepts, and it lasts most users through many
                PreCut projects (see pricing below).
              </li>
              <li>
                Go to Settings → <strong>API Keys</strong> and click{" "}
                <strong>Create Key</strong>. Give it any name
                (&ldquo;PreCut&rdquo; is fine). Copy the key —{" "}
                <em>you won't be able to see it again</em>, so paste it
                somewhere safe before closing the dialog.
              </li>
              <li>
                Come back here, paste the key into the field below, and
                click <strong>Save key</strong>. That's it — PreCut will
                start using Claude on the next project you analyze.
              </li>
            </ol>
          </section>

          {/* Section 3 — cost breakdown */}
          <section className="help-section">
            <h3 className="help-heading">What does it cost?</h3>
            <p>
              Anthropic charges by <strong>tokens</strong> (roughly, syllables
              of text). PreCut defaults to <strong>Claude Sonnet 4.6</strong>,
              which sits in the middle of the cost/quality curve. A typical
              10-minute footage project uses about 50,000–200,000 input tokens
              plus 10,000–50,000 output tokens, landing between{" "}
              <strong>$0.20 and $0.90 per project</strong>. Your $5 in starter
              credit typically lasts 5–25 projects depending on how much footage
              you're analyzing.
            </p>

            <div className="help-pricing-table-wrap">
              <table className="help-pricing-table">
                <thead>
                  <tr>
                    <th>Model</th>
                    <th>Input<br/><span className="help-th-sub">per 1M tokens</span></th>
                    <th>Output<br/><span className="help-th-sub">per 1M tokens</span></th>
                    <th>Good for</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td><strong>Haiku 4.5</strong></td>
                    <td>$1</td>
                    <td>$5</td>
                    <td>Fastest, cheapest. Fine for tagging.</td>
                  </tr>
                  <tr className="help-pricing-default">
                    <td>
                      <strong>Sonnet 4.6</strong>
                      <span className="help-pricing-badge">default</span>
                    </td>
                    <td>$3</td>
                    <td>$15</td>
                    <td>Balanced. What PreCut uses unless you change it.</td>
                  </tr>
                  <tr>
                    <td><strong>Opus 4.7</strong></td>
                    <td>$5</td>
                    <td>$25</td>
                    <td>Best quality. Overkill for most cuts.</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <p className="help-pricing-caveat">
              Prices are Anthropic's April 2026 rates and may change. Current
              pricing is always at{" "}
              <button
                className="help-link"
                onClick={() => externalLink("https://www.anthropic.com/pricing")}
              >
                anthropic.com/pricing
              </button>.
            </p>
          </section>

          {/* Section 4 — safety / what-if */}
          <section className="help-section">
            <h3 className="help-heading">Common questions</h3>

            <details className="help-faq">
              <summary>Is my footage sent to Anthropic?</summary>
              <p>
                No. PreCut sends <em>transcripts</em> and short
                content tags — not the video files themselves. Anthropic
                never receives your footage.
              </p>
            </details>

            <details className="help-faq">
              <summary>What if I accidentally share my key?</summary>
              <p>
                Anyone with your key can run up charges on your account.
                If that happens, go back to console.anthropic.com → API
                Keys, <strong>revoke</strong> the compromised key, and
                create a new one.
              </p>
            </details>

            <details className="help-faq">
              <summary>Can I set a spending limit?</summary>
              <p>
                Yes — in the Anthropic console under Settings → Billing
                you can set a monthly spend cap. Recommended if you're
                nervous about runaway costs.
              </p>
            </details>

            <details className="help-faq">
              <summary>Is there a free tier?</summary>
              <p>
                Anthropic sometimes gives new accounts a small amount of
                free credit, but there's no permanent free tier. The $5
                minimum is the realistic starting point.
              </p>
            </details>
          </section>

        </div>

        <div className="modal-actions">
          <button
            className="btn btn-ghost"
            onClick={() => externalLink("https://console.anthropic.com/")}
          >
            Open console.anthropic.com
          </button>
          <button className="btn btn-primary" onClick={onClose}>
            Got it
          </button>
        </div>
      </div>
    </div>
  );
}
