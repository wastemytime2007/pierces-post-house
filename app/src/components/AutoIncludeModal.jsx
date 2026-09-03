import { useState, useEffect, useCallback, useMemo } from "react";
import { open } from "@tauri-apps/plugin-dialog";

import HelpTooltip from "./HelpTooltip.jsx";

/**
 * AutoIncludeModal — manage "always include these files" rules.
 *
 * User-preference feature (Drop 4.46): editors typically have stock SFX,
 * brand logos, and other assets they re-use in every project. Rules
 * configured here get silently included in every export, routed to the
 * bin path the user specifies.
 *
 * Drop 4.46.1: UX redesign — destination is now a dropdown of common
 * locations (with "Custom path…" for power users) instead of a free-text
 * field. Adds an intro panel with a visual tree of the standard bin
 * structure so users understand what the destinations mean before
 * picking one.
 *
 * Saves happen on field blur — no explicit "save" button. State is
 * authoritative on the backend; the UI just displays and edits.
 */

// Common destination presets shown in the dropdown. Order is intentional
// (most-used first). Internal `value` is the actual bin path the exporter
// consumes; `label` is the user-facing string.
//
// We deliberately HIDE Audio/Source Audio and Files/Overlays — those bins
// are system-managed (lavs from sync, the safe-zone overlay PNG). Users
// adding arbitrary files there could conflict. Power users can still type
// those paths via "Custom path…" if they really want to.
//
// Drop 4.47: Seq/Final added. Previously the path required the project
// name (e.g. MyProject/Seq/Final), so we couldn't expose a generic
// preset. The 4.47 structural change made Seq a top-level sibling of
// Footage/Audio/Files, so "Seq/Final" is now a static, project-name-
// independent path.
const PRESET_DESTINATIONS = [
  { value: "Audio/Music",         label: "Audio  /  Music" },
  { value: "Audio/SFX",           label: "Audio  /  SFX" },
  { value: "Footage/B-Roll",      label: "Footage  /  B-Roll" },
  { value: "Files/Colors",        label: "Files  /  Colors" },
  { value: "Files/Nested Seqs",   label: "Files  /  Nested Seqs" },
  { value: "Seq/Final",           label: "Seq  /  Final" },
];

const CUSTOM_OPTION = "__CUSTOM__";

// Drop 1.0.0-beta.2: file-type validation mirrored from
// python_backend/precut_pipeline/auto_include.py. Used to show inline
// warnings on rule rows when a user picks a file Premiere can't
// import via FCP7 XML, instead of letting the rule silently fail at
// export time.
//
// Keep this list in sync with auto_include.py — the backend is still
// authoritative; the frontend mirror is just for fast UI feedback.
const SUPPORTED_EXTS = new Set([
  // audio
  ".wav", ".mp3", ".aac", ".aif", ".aiff", ".m4a", ".flac",
  // video
  ".mov", ".mp4", ".avi", ".mkv", ".mxf", ".m4v",
  // image
  ".png", ".jpg", ".jpeg", ".heic", ".tif", ".tiff",
  ".svg", ".bmp", ".gif", ".webp",
]);

const UNSUPPORTED_REASONS = {
  ".cube": "LUT files can't be imported via Premiere XML. Save as a Lumetri preset in Premiere instead.",
  ".look": "LUT files can't be imported via Premiere XML. Save as a Lumetri preset in Premiere instead.",
  ".3dl":  "LUT files can't be imported via Premiere XML. Save as a Lumetri preset in Premiere instead.",
  ".pdf":  "PDFs aren't importable as Premiere project items.",
  ".txt":  "Text files aren't importable as Premiere project items.",
  ".rtf":  "Text files aren't importable as Premiere project items.",
  ".doc":  "Word docs aren't importable as Premiere project items.",
  ".docx": "Word docs aren't importable as Premiere project items.",
  ".ai":   "Illustrator files import unreliably via FCP7 XML. Export as PNG or SVG instead.",
  ".psd":  "Photoshop files import unreliably via FCP7 XML. Export as PNG instead.",
};

/**
 * Return a warning string for a given source path, or null if the path
 * looks fine (or we can't tell — folders are always null since contents
 * may vary; backend will warn at export time for folder rules).
 */
function _fileTypeWarning(rule) {
  if (rule.type !== "file" || !rule.source_path) return null;
  // Pull extension from the path
  const dot = rule.source_path.lastIndexOf(".");
  if (dot < 0) return null;
  const ext = rule.source_path.slice(dot).toLowerCase();
  if (SUPPORTED_EXTS.has(ext)) return null;
  if (UNSUPPORTED_REASONS[ext]) return UNSUPPORTED_REASONS[ext];
  return `Extension "${ext}" isn't supported (audio, video, or image only).`;
}

// Tree shown in the intro panel. Pure presentation — these are the
// standard bins users will see in their Premiere project panel after
// import. Mirrors the actual hierarchy from multi_exporter.py.
const INTRO_TREE = [
  { kind: "header", text: "Seq" },
  { kind: "leaf",   text: "Final",       hint: "outros, finals, render templates" },
  { kind: "header", text: "Footage" },
  { kind: "leaf",   text: "B-Roll",      hint: "stock footage you re-use" },
  { kind: "header", text: "Audio" },
  { kind: "leaf",   text: "Music",       hint: "royalty-free music tracks" },
  { kind: "leaf",   text: "SFX",         hint: "your sound effects library" },
  { kind: "header", text: "Files" },
  { kind: "leaf",   text: "Colors",      hint: "LUTs, color references" },
  { kind: "leaf",   text: "Nested Seqs", hint: "reusable sub-sequences" },
];

export default function AutoIncludeModal({ rules, onSave, onClose, onOpenHelp }) {
  const [localRules, setLocalRules] = useState(rules || []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLocalRules(rules || []);
  }, [rules]);

  const persist = useCallback(async (next) => {
    setLocalRules(next);
    setSaving(true);
    setError(null);
    try {
      await onSave(next);
    } catch (e) {
      console.error("Failed to save auto-include rules:", e);
      setError(String(e?.message || e || "Save failed"));
    } finally {
      setSaving(false);
    }
  }, [onSave]);

  const updateRule = useCallback((id, patch) => {
    const next = localRules.map((r) => (r.id === id ? { ...r, ...patch } : r));
    persist(next);
  }, [localRules, persist]);

  const deleteRule = useCallback((id) => {
    const next = localRules.filter((r) => r.id !== id);
    persist(next);
  }, [localRules, persist]);

  const newId = () => {
    try { return crypto.randomUUID(); }
    catch { return `r-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`; }
  };

  const handleAddFile = useCallback(async () => {
    const selection = await open({
      multiple: true, directory: false,
      title: "Add file(s) to auto-include",
    });
    if (!selection) return;
    const paths = Array.isArray(selection) ? selection : [selection];
    const newRules = paths.map((p) => ({
      id: newId(), type: "file",
      source_path: p, bin_path: "", file_glob: "",
    }));
    persist([...localRules, ...newRules]);
  }, [localRules, persist]);

  const handleAddFolder = useCallback(async () => {
    const selection = await open({
      multiple: true, directory: true,
      title: "Add folder(s) to auto-include",
    });
    if (!selection) return;
    const paths = Array.isArray(selection) ? selection : [selection];
    const newRules = paths.map((p) => ({
      id: newId(), type: "folder",
      source_path: p, bin_path: "", file_glob: "",
    }));
    persist([...localRules, ...newRules]);
  }, [localRules, persist]);

  const isEmpty = localRules.length === 0;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>
            Default Includes
            {onOpenHelp && (
              <button
                type="button"
                className="auto-include-header-help"
                onClick={onOpenHelp}
                title="What is this? — open the walkthrough"
                aria-label="Open Default Includes walkthrough"
              >
                ?
              </button>
            )}
          </h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div className="modal-body">
          <IntroPanel />

          {error && (
            <div className="auto-include-error">
              {error}
            </div>
          )}

          {isEmpty ? (
            <EmptyState onOpenHelp={onOpenHelp} />
          ) : (
            <div className="auto-include-list">
              {localRules.map((rule) => (
                <RuleRow
                  key={rule.id}
                  rule={rule}
                  onChange={(patch) => updateRule(rule.id, patch)}
                  onDelete={() => deleteRule(rule.id)}
                  disabled={saving}
                />
              ))}
            </div>
          )}
        </div>

        <div className="modal-actions">
          <button
            className="btn btn-ghost"
            onClick={handleAddFile}
            disabled={saving}
            style={{ marginRight: 8 }}
          >
            + Add File
          </button>
          <button
            className="btn btn-ghost"
            onClick={handleAddFolder}
            disabled={saving}
            style={{ marginRight: "auto" }}
          >
            + Add Folder
          </button>
          <button className="btn btn-primary" onClick={onClose} disabled={saving}>
            {saving ? "Saving…" : "Done"}
          </button>
        </div>
      </div>
    </div>
  );
}


function IntroPanel() {
  return (
    <div className="auto-include-intro">
      <p className="auto-include-intro-text">
        Files added here are included in every Premiere export, dropped
        into the bin you choose. Pick a destination for each rule from
        the dropdown — or use a custom path to create your own bin.
      </p>
      {/* Drop 4.47.2: explicit callout about scope. Users were unsure
       * whether rules were per-project or global; this clarifies. */}
      <div className="auto-include-intro-scope">
        <span className="auto-include-intro-scope-icon" aria-hidden="true">ⓘ</span>
        <span>
          Rules apply to <strong>every project</strong> on this Mac, on
          every export, until you remove them. Set up once, save time
          forever.
        </span>
      </div>
      <div className="auto-include-intro-tree">
        <div className="auto-include-intro-tree-label">
          Standard bins in every export
        </div>
        <div className="auto-include-intro-tree-grid">
          {INTRO_TREE.map((row, i) => (
            <div
              key={i}
              className={`auto-include-intro-tree-row auto-include-intro-tree-${row.kind}`}
            >
              <span className="auto-include-intro-tree-name">
                {row.kind === "header" ? (
                  <>📁 {row.text}</>
                ) : (
                  <>
                    <span className="auto-include-intro-tree-bullet">└</span>
                    {row.text}
                  </>
                )}
              </span>
              {row.hint && (
                <span className="auto-include-intro-tree-hint">{row.hint}</span>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}


function EmptyState({ onOpenHelp }) {
  return (
    <div className="auto-include-empty">
      <div className="auto-include-empty-title">
        No default includes yet
      </div>
      <div className="auto-include-empty-body">
        <p>
          Click <strong>+ Add File</strong> or <strong>+ Add Folder</strong> below
          to start. Common patterns:
        </p>
        <ul className="auto-include-empty-list">
          <li>
            <span className="auto-include-empty-icon">📁</span>
            Stock SFX folder &rarr; <code>Audio / SFX</code>
          </li>
          <li>
            <span className="auto-include-empty-icon">📄</span>
            Company logo &rarr; <code>Files / Colors</code>
          </li>
          <li>
            <span className="auto-include-empty-icon">📁</span>
            Royalty-free music folder &rarr; <code>Audio / Music</code>
          </li>
        </ul>
        {onOpenHelp && (
          <p style={{ marginTop: 12, marginBottom: 0 }}>
            New to this feature?{" "}
            <button
              type="button"
              className="auto-include-empty-link"
              onClick={onOpenHelp}
            >
              See the full walkthrough &rarr;
            </button>
          </p>
        )}
      </div>
    </div>
  );
}


/**
 * A single rule row. Edits committed on blur (text fields) or
 * immediately (delete button, dropdown).
 *
 * The destination dropdown shows preset bin paths by default. Selecting
 * "Custom path…" reveals a text input for free-form paths.
 */
function RuleRow({ rule, onChange, onDelete, disabled }) {
  // Determine whether this rule's bin_path matches a preset or is custom.
  const matchedPreset = useMemo(
    () => PRESET_DESTINATIONS.find((p) => p.value === (rule.bin_path || "")),
    [rule.bin_path],
  );
  const isCustomMode = !matchedPreset && rule.bin_path !== "";

  const dropdownValue = matchedPreset
    ? matchedPreset.value
    : isCustomMode
    ? CUSTOM_OPTION
    : "";  // empty: no destination picked yet

  const [customPath, setCustomPath] = useState(
    isCustomMode ? rule.bin_path : ""
  );
  const [fileGlob, setFileGlob] = useState(rule.file_glob || "");
  const [showCustom, setShowCustom] = useState(isCustomMode);

  useEffect(() => {
    setFileGlob(rule.file_glob || "");
  }, [rule.file_glob]);
  useEffect(() => {
    if (matchedPreset) {
      setShowCustom(false);
    } else if (rule.bin_path) {
      setShowCustom(true);
      setCustomPath(rule.bin_path);
    }
  }, [rule.bin_path, matchedPreset]);

  // Truncate path display: keep last 2 segments + ellipsis prefix.
  const displayPath = (() => {
    const p = rule.source_path || "";
    if (p.length <= 60) return p;
    const parts = p.split("/").filter(Boolean);
    if (parts.length <= 2) return p;
    return ".../" + parts.slice(-2).join("/");
  })();

  const handleDropdownChange = (e) => {
    const val = e.target.value;
    if (val === CUSTOM_OPTION) {
      setShowCustom(true);
      onChange({ bin_path: customPath });
    } else if (val === "") {
      setShowCustom(false);
      onChange({ bin_path: "" });
    } else {
      setShowCustom(false);
      onChange({ bin_path: val });
    }
  };

  const customPathInvalid = (() => {
    if (!showCustom) return false;
    if (!customPath.trim()) return false;
    return customPath.split("/").some((s) => !s.trim());
  })();

  const needsDestination = !rule.bin_path && !showCustom;

  // Drop 1.0.0-beta.2: surface unsupported-file-type warnings inline
  // so the user sees the reason their .cube/.psd/etc. won't import.
  // Backend is authoritative — this just gives faster feedback.
  const fileWarning = _fileTypeWarning(rule);

  return (
    <div className="auto-include-row">
      <div className="auto-include-row-main">
        <div className="auto-include-row-icon" aria-hidden="true">
          {rule.type === "folder" ? "📁" : "📄"}
        </div>
        <div className="auto-include-row-source">
          <div className="auto-include-row-source-label">
            {rule.type === "folder" ? "Folder" : "File"}
          </div>
          <div
            className="auto-include-row-source-path"
            title={rule.source_path}
          >
            {displayPath}
          </div>
        </div>
        <button
          className="auto-include-row-delete"
          onClick={onDelete}
          disabled={disabled}
          title="Remove this rule"
          aria-label="Remove"
        >
          ×
        </button>
      </div>
      {fileWarning && (
        <div className="auto-include-row-warning" role="alert">
          <span className="auto-include-row-warning-icon" aria-hidden="true">⚠</span>
          <span>{fileWarning}</span>
        </div>
      )}
      <div className="auto-include-row-fields">
        <label className="auto-include-row-field">
          <span className="auto-include-row-field-label">
            Where to put it
            <HelpTooltip>
              The bin in Premiere's project panel where this file will
              appear. Pick from common destinations or use{" "}
              <strong>Custom path…</strong> and type something like{" "}
              <code>Files/Logos</code> to make a new bin. Use{" "}
              <code>/</code> to nest bins, e.g.{" "}
              <code>Audio/Music/Royalty Free</code>.
            </HelpTooltip>
            {needsDestination && (
              <span className="auto-include-row-field-warn">
                {" "}— please pick a destination
              </span>
            )}
          </span>
          <select
            className={`form-input auto-include-row-select${needsDestination ? " form-input-warn" : ""}`}
            value={dropdownValue}
            onChange={handleDropdownChange}
            disabled={disabled}
          >
            <option value="">— Pick a destination —</option>
            {PRESET_DESTINATIONS.map((p) => (
              <option key={p.value} value={p.value}>{p.label}</option>
            ))}
            <option value={CUSTOM_OPTION}>Custom path…</option>
          </select>
          {showCustom && (
            <input
              type="text"
              className={`form-input auto-include-row-custom-input${customPathInvalid ? " form-input-warn" : ""}`}
              placeholder="e.g. Files/Logos or Audio/Music/Royalty Free"
              value={customPath}
              onChange={(e) => setCustomPath(e.target.value)}
              onBlur={() => {
                const trimmed = customPath.trim();
                if (trimmed && trimmed !== rule.bin_path) {
                  onChange({ bin_path: trimmed });
                } else if (!trimmed && rule.bin_path !== "") {
                  onChange({ bin_path: "" });
                }
              }}
              disabled={disabled}
            />
          )}
        </label>
        {rule.type === "folder" && (
          <label className="auto-include-row-field auto-include-row-field-glob">
            <span className="auto-include-row-field-label">
              File filter
              <HelpTooltip align="right">
                Limit which files in the folder get included. Use{" "}
                <code>*.wav</code> for only WAV files, or{" "}
                <code>*.{"{wav,mp3}"}</code> for WAVs and MP3s. Leave
                empty and PreCut will include any audio, video, or
                image file it recognizes.
              </HelpTooltip>
              <span className="auto-include-row-field-hint"> (optional)</span>
            </span>
            <input
              type="text"
              className="form-input"
              placeholder="*.wav"
              value={fileGlob}
              onChange={(e) => setFileGlob(e.target.value)}
              onBlur={() => {
                if (fileGlob !== rule.file_glob) {
                  onChange({ file_glob: fileGlob });
                }
              }}
              disabled={disabled}
            />
          </label>
        )}
      </div>
    </div>
  );
}
