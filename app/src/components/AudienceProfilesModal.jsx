import { useState, useEffect } from "react";

/**
 * AudienceProfilesModal — manage the app-level library of audience /
 * content-goal profiles.
 *
 * Authored ONCE here (main screen, before opening any project), then
 * picked from a dropdown at Project Manager intake (PMTab.jsx) instead
 * of being retyped as free text per project (Ryan, 2026-09-03: "on the
 * main page of the app before going into any project there should be a
 * place for the user to add details about the audiences and goals of
 * different types of content for their brand ... the dropdown in the
 * projects would allow them to select the prebuilt audiences goals").
 *
 * Seeded server-side (settings.py) with SoldFast's three real content
 * funnels plus a placeholder long-form profile — this modal just lets
 * the user edit, add, or remove from that starting set.
 */
export default function AudienceProfilesModal({ profiles, onSave, onClose }) {
  const [rows, setRows] = useState(profiles || []);
  const [saving, setSaving] = useState(false);

  // Re-sync if the backend pushes a fresh copy while this is open (e.g.
  // after the initial seed lands just after the modal was opened).
  useEffect(() => { setRows(profiles || []); }, [profiles]);

  const updateRow = (idx, field, value) => {
    setRows((prev) => prev.map((r, i) => (i === idx ? { ...r, [field]: value } : r)));
  };

  const removeRow = (idx) => {
    setRows((prev) => prev.filter((_, i) => i !== idx));
  };

  const addRow = () => {
    setRows((prev) => [...prev, { id: `profile-${Date.now()}`, name: "", description: "" }]);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const cleaned = rows
        .map((r) => ({ ...r, name: (r.name || "").trim(), description: (r.description || "").trim() }))
        .filter((r) => r.name);
      await onSave(cleaned);
      onClose();
    } catch (e) {
      console.error(e);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 640 }}>
        <div className="modal-header">
          <h2>Audiences &amp; content goals</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div className="modal-body">
          <div className="form-hint" style={{ marginBottom: 12 }}>
            Define who your footage is for, once — a content funnel, a
            campaign, a personal long-form goal, whatever fits how you
            work. These show up as a dropdown when setting up a new
            project in Project Manager, instead of retyping the same
            description every time.
          </div>

          {rows.map((row, idx) => (
            <div key={row.id || idx} className="audience-profile-row">
              <div className="pm-tab-row">
                <input
                  type="text"
                  className="form-input"
                  placeholder="Profile name (e.g. Brand / Authority)"
                  value={row.name || ""}
                  onChange={(e) => updateRow(idx, "name", e.target.value)}
                />
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={() => removeRow(idx)}
                  title="Remove this profile"
                >
                  Remove
                </button>
              </div>
              <textarea
                className="form-textarea"
                rows={3}
                placeholder="Audience, goal, and framing for this content type"
                value={row.description || ""}
                onChange={(e) => updateRow(idx, "description", e.target.value)}
                style={{ marginTop: 6 }}
              />
            </div>
          ))}

          <button type="button" className="btn btn-ghost" onClick={addRow} style={{ marginTop: 8 }}>
            + Add profile
          </button>
        </div>

        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
