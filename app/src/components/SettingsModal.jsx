import { useState } from "react";

/**
 * SettingsModal — manage the Anthropic API key.
 *
 * Shows current key state (from settings file vs. from env var vs. missing)
 * and lets the user paste a new key. We never show the existing key value
 * (only last 4 chars) — the backend handles storage.
 */
export default function SettingsModal({ settings, onSave, onClose, onOpenHelp }) {
  const [newKey, setNewKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [showClearConfirm, setShowClearConfirm] = useState(false);

  const handleSave = async () => {
    const trimmed = newKey.trim();
    if (!trimmed) return;
    setSaving(true);
    try {
      await onSave(trimmed);
      setNewKey("");
      onClose();
    } catch (e) {
      console.error(e);
    } finally {
      setSaving(false);
    }
  };

  const handleClear = async () => {
    setSaving(true);
    try {
      await onSave("");  // empty string clears
      setShowClearConfirm(false);
      onClose();
    } catch (e) {
      console.error(e);
    } finally {
      setSaving(false);
    }
  };

  const handleKey = (e) => {
    if (e.key === "Enter" && newKey.trim()) { e.preventDefault(); handleSave(); }
    if (e.key === "Escape") onClose();
  };

  const hasKey = settings && (settings.has_settings || settings.has_env);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Settings</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div className="modal-body">
          <div className="form-label-row">
            <label className="form-label">Anthropic API key</label>
            {onOpenHelp && (
              <button
                type="button"
                className="form-help-button"
                onClick={onOpenHelp}
                title="Don't know what this is? Click for a walkthrough."
                aria-label="Help with API key"
              >
                ?
              </button>
            )}
          </div>

          <div className="settings-key-status">
            {!settings ? (
              <div className="settings-key-state">loading…</div>
            ) : settings.active_source === "settings" ? (
              <div className="settings-key-state ok">
                <span className="settings-key-label">Saved key in use</span>
                <span className="settings-key-suffix">ending in …{settings.key_suffix}</span>
              </div>
            ) : settings.active_source === "env" ? (
              <div className="settings-key-state warn">
                <span className="settings-key-label">Using environment variable</span>
                <span className="settings-key-suffix">ending in …{settings.key_suffix}</span>
                <div className="settings-key-hint">
                  A key from <code>ANTHROPIC_API_KEY</code> env var is active.
                  Save one below to make it persistent and independent of your shell setup.
                </div>
              </div>
            ) : (
              <div className="settings-key-state missing">
                <span className="settings-key-label">No API key set</span>
                <div className="settings-key-hint">
                  Paste your Anthropic API key below. It will be stored at{" "}
                  <code>~/Library/Application Support/PreCut/settings.json</code>{" "}
                  with 0600 permissions (owner-only).
                </div>
              </div>
            )}
          </div>

          <input
            type="password"
            autoFocus
            className="form-input"
            placeholder="sk-ant-api03-..."
            value={newKey}
            onChange={(e) => setNewKey(e.target.value)}
            onKeyDown={handleKey}
            autoComplete="new-password"
            style={{ fontFamily: "var(--font-mono)", marginTop: 8 }}
          />

          <div className="form-hint">
            Get a key at{" "}
            <button
              type="button"
              className="form-link"
              onClick={async () => {
                try {
                  // plugin-shell exports `open`, not `openUrl`. Aliased
                  // locally to avoid shadowing window.open in the catch.
                  const { open: openExternal } = await import("@tauri-apps/plugin-shell");
                  await openExternal("https://console.anthropic.com/settings/keys");
                } catch {
                  try { window.open("https://console.anthropic.com/settings/keys", "_blank"); } catch {}
                }
              }}
            >
              console.anthropic.com/settings/keys
            </button>.
            The AI producer (Analyze &amp; refine) uses it to generate ideas.
            {onOpenHelp && (
              <>
                {" "}
                <button
                  type="button"
                  className="form-link"
                  onClick={onOpenHelp}
                >
                  Full walkthrough &rarr;
                </button>
              </>
            )}
          </div>

          {showClearConfirm && (
            <div className="settings-clear-confirm">
              <div>Clear the saved key? The producer will stop working until you paste a new one.</div>
              <div className="form-actions">
                <button className="btn btn-ghost" onClick={() => setShowClearConfirm(false)} disabled={saving}>
                  Cancel
                </button>
                <button className="btn btn-danger" onClick={handleClear} disabled={saving}>
                  {saving ? "Clearing…" : "Confirm clear"}
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="modal-actions">
          {hasKey && settings.has_settings && !showClearConfirm && (
            <button
              className="btn btn-ghost"
              onClick={() => setShowClearConfirm(true)}
              disabled={saving}
              style={{ marginRight: "auto" }}
            >
              Clear saved key
            </button>
          )}
          <button className="btn btn-ghost" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button
            className="btn btn-primary"
            onClick={handleSave}
            disabled={!newKey.trim() || saving}
          >
            {saving ? "Saving…" : "Save key"}
          </button>
        </div>
      </div>
    </div>
  );
}
