import { useState, useEffect, useCallback } from "react";
import { invoke } from "@tauri-apps/api/core";
import { save } from "@tauri-apps/plugin-dialog";
import { sendCommand } from "../App.jsx";
import HelpTooltip from "./HelpTooltip.jsx";

/**
 * ExportModal — one modal handles the full export flow.
 *
 * States (progressing top-to-bottom):
 *   - "configure"  — options + save-location picker + Export button
 *   - "running"    — live progress log while Python builds the XML
 *   - "done"       — success message + Reveal-in-Finder button
 *   - "error"      — error message + retry
 *
 * We listen to backend export_* events via a subscription through the
 * parent's subscribe prop, mediated through the global listen that App.jsx
 * already sets up. Since ExportModal doesn't have a subscribe prop, we use
 * the IPC-send + event-received pattern via window.addEventListener on
 * tauri events. But App.jsx already fans out events to its subscribers
 * ref; we just need the modal to hook in. Simplest route: use the global
 * Tauri event listener directly, scoped to our job_id.
 */
export default function ExportModal({
  selectedIdeas,
  projectName,
  brollCount,
  // Drop 4.47.3: count of saved Default Includes rules. When > 0,
  // the configure view shows a checkbox for skipping them on this
  // export. When 0 (or undefined), the checkbox is hidden and the
  // backend gets the same payload as before this feature.
  autoIncludeRulesCount = 0,
  onClose,
  onExported,
  libraryOnly = false,
}) {
  const [stage, setStage] = useState("configure");
  const [savePath, setSavePath] = useState("");
  const [includeLibrary, setIncludeLibrary] = useState(true);
  const [runAudioSync, setRunAudioSync] = useState(true);
  const [includeCleanMic, setIncludeCleanMic] = useState(true);
  const [includeOverlay, setIncludeOverlay] = useState(true);
  // Drop 4.47.3: per-export opt-out for Default Includes. Defaults to
  // true so existing user expectations (rules are always applied) are
  // preserved. The user explicitly unchecks for projects where they
  // want a clean export without their global rules — e.g. a one-off
  // for a different client.
  const [applyAutoIncludes, setApplyAutoIncludes] = useState(true);
  // 2026-09-03: B-roll frame-rate interpretation target. Ryan: "Where am
  // i setting the target framerate? It doesnt ask so i cant tell it."
  // "auto" = smallest captured native rate across all footage (computed
  // and logged by the backend); the four options are Ryan's own stated
  // realistic set, not an arbitrary list.
  const [brollTargetFpsMode, setBrollTargetFpsMode] = useState("auto");
  const [progressLog, setProgressLog] = useState([]);
  const [jobId, setJobId] = useState(null);
  const [writtenPath, setWrittenPath] = useState(null);
  const [errorMsg, setErrorMsg] = useState(null);

  const addProgress = useCallback((msg, level = "info") => {
    setProgressLog((prev) => [...prev, { msg, level, ts: Date.now() }]);
  }, []);

  // Subscribe to backend-event via the global tauri listener. We filter
  // by job_id once we have one.
  useEffect(() => {
    if (stage !== "running" || !jobId) return;

    let unlisten;
    (async () => {
      const { listen } = await import("@tauri-apps/api/event");
      unlisten = await listen("backend-event", (e) => {
        let ev;
        try { ev = JSON.parse(e.payload); } catch { return; }
        if (ev.job_id && ev.job_id !== jobId) return;  // not our job

        switch (ev.type) {
          case "export_started":
            addProgress(`Export started — ${ev.idea_count} timeline${ev.idea_count !== 1 ? "s" : ""}`, "accent");
            break;
          case "export_matching":
            addProgress(`Generating B-roll markers for "${ev.sequence_name}"…`);
            break;
          case "export_matched": {
            const phraseStr = `${ev.aroll_phrases} A-roll phrase${ev.aroll_phrases !== 1 ? "s" : ""}`;
            // Drop 3.7+: if we emitted markers, show those instead of cutaway count
            const markerCount = ev.broll_markers ?? 0;
            const cutawayCount = ev.broll_cutaways ?? 0;
            if (markerCount > 0) {
              addProgress(`  ${phraseStr}, ${markerCount} B-roll marker${markerCount !== 1 ? "s" : ""}`);
            } else if (cutawayCount > 0) {
              addProgress(`  ${phraseStr}, ${cutawayCount} B-roll cutaway${cutawayCount !== 1 ? "s" : ""}`);
            } else {
              addProgress(`  ${phraseStr}`);
            }
            break;
          }
          case "export_sync_started":
            addProgress("Running audalign on clean-mic audio…");
            break;
          case "export_sync_result":
            if (ev.pairs > 0) {
              const conf = (ev.overall_confidence * 100).toFixed(0);
              addProgress(`  synced ${ev.pairs} pair${ev.pairs !== 1 ? "s" : ""}, best confidence ${conf}%`);
            } else {
              addProgress("  no pairs synced (missing audio or library)", "warn");
            }
            break;
          case "export_writing":
            addProgress("Writing XML…");
            break;
          case "export_complete":
            setWrittenPath(ev.xml_path);
            addProgress(`Wrote ${ev.sequences} sequence${ev.sequences !== 1 ? "s" : ""} + library of ${ev.broll_library_size} clips`, "accent");
            setStage("done");
            break;
          case "export_error":
            setErrorMsg(ev.message || "Export failed");
            setStage("error");
            break;
          case "log":
            addProgress(ev.message, ev.level || "info");
            break;
          default:
            // ignore non-export events
            break;
        }
      });
    })();
    return () => { if (unlisten) unlisten(); };
  }, [stage, jobId, addProgress]);

  const pickSaveLocation = async () => {
    const defaultName = _defaultFilename(projectName, selectedIdeas);
    try {
      const path = await save({
        defaultPath: defaultName,
        filters: [{ name: "FCP7 XML", extensions: ["xml"] }],
        title: "Save Premiere XML",
      });
      if (path) setSavePath(path);
    } catch (e) {
      console.error("save dialog failed:", e);
    }
  };

  const handleExport = async () => {
    if (!savePath.trim()) return;
    const newJobId = `export-${Date.now()}`;
    setJobId(newJobId);
    setProgressLog([]);
    setWrittenPath(null);
    setErrorMsg(null);
    setStage("running");
    try {
      await sendCommand({
        type: "export_timelines",
        job_id: newJobId,
        output_path: savePath.trim(),
        // In library_only mode the backend ignores idea_ids — we send
        // an empty array rather than the selectedIdeas map (which
        // should be empty in library_only mode anyway, but be explicit).
        idea_ids: libraryOnly ? [] : selectedIdeas.map((i) => i.idea_id),
        library_only: libraryOnly,
        include_full_library: includeLibrary,
        run_audio_sync: runAudioSync,
        include_clean_mic: includeCleanMic,
        include_overlay: includeOverlay,
        // Drop 4.47.3: per-export opt-out. Backend defaults to True if
        // missing, so older clients keep working unchanged.
        apply_auto_includes: applyAutoIncludes,
        // "auto" (default) omits this entirely -- backend computes and
        // logs the smallest-captured-rate target itself.
        ...(brollTargetFpsMode !== "auto" ? { broll_target_fps: parseFloat(brollTargetFpsMode) } : {}),
      });
    } catch (e) {
      setErrorMsg(String(e));
      setStage("error");
    }
  };

  const handleReveal = async () => {
    if (!writtenPath) return;
    try {
      await invoke("show_in_finder", { path: writtenPath });
      onExported();
    } catch (e) {
      console.error("show_in_finder failed:", e);
    }
  };

  // -------------------- RENDER --------------------

  return (
    <div className="modal-overlay" onClick={stage === "configure" ? onClose : undefined}>
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ width: 580 }}>
        <div className="modal-header">
          <h2>
            {stage === "done" ? "Export complete"
              : stage === "error" ? "Export failed"
              : stage === "running" ? "Exporting…"
              : libraryOnly ? "Export library only"
              : "Export to Premiere XML"}
          </h2>
          {stage !== "running" && (
            <button className="modal-close" onClick={onClose}>×</button>
          )}
        </div>

        <div className="modal-body">
          {stage === "configure" && (
            <ConfigureView
              selectedIdeas={selectedIdeas}
              projectName={projectName}
              brollCount={brollCount}
              autoIncludeRulesCount={autoIncludeRulesCount}
              libraryOnly={libraryOnly}
              savePath={savePath}
              includeLibrary={includeLibrary}
              setIncludeLibrary={setIncludeLibrary}
              runAudioSync={runAudioSync}
              setRunAudioSync={setRunAudioSync}
              includeCleanMic={includeCleanMic}
              setIncludeCleanMic={setIncludeCleanMic}
              includeOverlay={includeOverlay}
              setIncludeOverlay={setIncludeOverlay}
              applyAutoIncludes={applyAutoIncludes}
              setApplyAutoIncludes={setApplyAutoIncludes}
              brollTargetFpsMode={brollTargetFpsMode}
              setBrollTargetFpsMode={setBrollTargetFpsMode}
              onPickSave={pickSaveLocation}
            />
          )}

          {stage === "running" && (
            <ProgressView progressLog={progressLog} />
          )}

          {stage === "done" && (
            <DoneView writtenPath={writtenPath} projectName={projectName} />
          )}

          {stage === "error" && (
            <ErrorView errorMsg={errorMsg} progressLog={progressLog} />
          )}
        </div>

        <div className="modal-actions">
          {stage === "configure" && (
            <>
              <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
              <button
                className="btn btn-primary"
                onClick={handleExport}
                disabled={!savePath.trim() || (!libraryOnly && selectedIdeas.length === 0)}
              >
                {libraryOnly
                  ? "Export library"
                  : `Export ${selectedIdeas.length} timeline${selectedIdeas.length !== 1 ? "s" : ""}`}
              </button>
            </>
          )}
          {stage === "running" && (
            <button className="btn btn-ghost" disabled>
              Working… this can take 30-90s for audio sync
            </button>
          )}
          {stage === "done" && (
            <>
              <button className="btn btn-ghost" onClick={onExported}>Close</button>
              <button className="btn btn-primary" onClick={handleReveal}>
                Reveal in Finder →
              </button>
            </>
          )}
          {stage === "error" && (
            <>
              <button className="btn btn-ghost" onClick={onClose}>Close</button>
              <button className="btn" onClick={() => setStage("configure")}>Try again</button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-views
// ---------------------------------------------------------------------------

function ConfigureView({
  selectedIdeas, projectName, brollCount, libraryOnly,
  autoIncludeRulesCount,
  savePath, includeLibrary, setIncludeLibrary,
  runAudioSync, setRunAudioSync,
  includeCleanMic, setIncludeCleanMic,
  includeOverlay, setIncludeOverlay,
  applyAutoIncludes, setApplyAutoIncludes,
  brollTargetFpsMode, setBrollTargetFpsMode,
  onPickSave,
}) {
  return (
    <>
      {libraryOnly ? (
        // Drop 4.44: library-only mode — no ideas selected. Show a
        // short summary of what will be produced instead of a
        // per-idea list.
        <div className="export-summary">
          <div className="export-summary-title">What this will export</div>
          <ul className="export-idea-list">
            <li>
              <strong>All Synced A-Roll</strong>
              <span className="export-idea-meta"> · every A-roll clip end-to-end, with any matched lav audio on parallel tracks</span>
            </li>
            <li>
              <strong>B-Roll Library bin</strong>
              <span className="export-idea-meta"> · {brollCount || 0} tagged clip{brollCount !== 1 ? "s" : ""} searchable in Premiere</span>
            </li>
          </ul>
        </div>
      ) : (
        <div className="export-summary">
          <div className="export-summary-title">Timelines to export</div>
          <ul className="export-idea-list">
            {selectedIdeas.map((idea) => {
              const d = idea.data || {};
              const isAngle = idea.kind === "story_angle";
              // Angle data uses d.brief.title; deliverable uses d.concept
              const title = isAngle
                ? (d.brief?.title || "Untitled angle")
                : (d.concept || "Untitled");
              const preset = isAngle
                ? (idea.selected_preset_key || d.suggested_preset || "—")
                : (d.preset_key || "—");
              const dur = isAngle
                ? (d.brief?.target_duration_sec
                    ? `~${Math.round(d.brief.target_duration_sec)}s`
                    : `${(d.phrase_ids || []).length} phrases`)
                : (d.total_target_duration != null
                    ? `${d.total_target_duration.toFixed(0)}s` : "?s");
              return (
                <li key={idea.idea_id}>
                  {isAngle && <span className="export-angle-badge">★ </span>}
                  <strong>{title}</strong>
                  <span className="export-idea-meta"> · {preset} · {dur}</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      <div className="form-label" style={{ marginTop: 18 }}>
        Save location
        <HelpTooltip>
          Where the <strong>.xml</strong> files will be written. Each
          selected idea becomes one XML file; if the B-roll library option
          below is on, you'll also get a{" "}
          <strong>_library.xml</strong> for the searchable clip bin.
        </HelpTooltip>
      </div>
      <div className="export-save-row">
        <div className="export-save-path">
          {savePath || <em className="export-save-placeholder">No location chosen yet</em>}
        </div>
        <button className="btn btn-ghost" onClick={onPickSave}>
          {savePath ? "Change…" : "Choose…"}
        </button>
      </div>

      <div className="form-label" style={{ marginTop: 18 }}>
        Options
        <HelpTooltip>
          These are inherited from your last export. Check the options
          you want, then click <strong>Export</strong> below. After
          import in Premiere, right-click any proxy clip →{" "}
          <strong>Proxy → Attach Proxies</strong> to point Premiere at
          your encoded proxies.
        </HelpTooltip>
      </div>
      <ExportOption
        checked={includeLibrary}
        onChange={setIncludeLibrary}
        label={`Include full B-roll library (${brollCount || "?"} clips)`}
        hint="All your B-roll appears as a 'B-Roll Library' bin in Premiere — search by tag with Cmd+F"
      />
      <label className="export-option">
        <div className="export-option-text" style={{ width: "100%" }}>
          <div className="export-option-label">B-roll interpretation target</div>
          <div className="export-option-hint">
            Any B-roll shot faster than this gets a second, labeled Project-panel
            item to Interpret Footage to. Auto = smallest frame rate across all
            your footage (logged in the export log below either way).
          </div>
          <select
            value={brollTargetFpsMode}
            onChange={(e) => setBrollTargetFpsMode(e.target.value)}
            style={{ marginTop: 6 }}
          >
            <option value="auto">Auto (smallest captured)</option>
            <option value="23.976">23.976 fps</option>
            <option value="24">24 fps</option>
            <option value="29.97">29.97 fps</option>
            <option value="30">30 fps</option>
          </select>
        </div>
      </label>
      <ExportOption
        checked={runAudioSync}
        onChange={setRunAudioSync}
        label="Run audio sync (audalign)"
        hint="Detects the offset between your A-roll's camera audio and your clean mic recording"
      />
      <ExportOption
        checked={includeCleanMic}
        onChange={setIncludeCleanMic}
        label="Include clean mic audio as parallel track"
        hint="Clean mic goes on A2 — synced automatically if confidence is high, or at 0 offset otherwise"
      />
      <ExportOption
        checked={includeOverlay}
        onChange={setIncludeOverlay}
        label="Include safe-zone overlay PNG"
        hint="Platform-specific safezone (Reels / TikTok / Shorts / X / horizontal) on the top video track"
      />
      {/* Drop 4.47.3: per-export opt-out for the global Default Includes
       * rules. Only renders if the user has at least one rule saved —
       * otherwise the toggle is meaningless. Default state is "checked"
       * so the existing always-apply behavior is preserved unless the
       * user explicitly unchecks it for this one export. */}
      {autoIncludeRulesCount > 0 && (
        <ExportOption
          checked={applyAutoIncludes}
          onChange={setApplyAutoIncludes}
          label={`Apply default includes (${autoIncludeRulesCount} rule${autoIncludeRulesCount !== 1 ? "s" : ""})`}
          hint="When checked, files from your saved Default Includes rules are added to this export. Uncheck to skip them just for this one export — your saved rules aren't deleted."
        />
      )}

      <div className="form-hint" style={{ marginTop: 14, fontSize: 11 }}>
        <strong>Note:</strong> Premiere doesn't support proxies in FCP7 XML. After importing this XML
        into Premiere, right-click any clip in the bin → Proxy → Attach Proxies → select your proxies
        folder. Premiere will auto-match all clips by filename.
      </div>
    </>
  );
}

function ProgressView({ progressLog }) {
  return (
    <div className="export-progress">
      {progressLog.length === 0 ? (
        <div className="export-progress-idle">Starting…</div>
      ) : (
        progressLog.map((entry, i) => (
          <div key={i} className={`export-progress-line export-progress-${entry.level}`}>
            {entry.msg}
          </div>
        ))
      )}
    </div>
  );
}

function DoneView({ writtenPath, projectName }) {
  return (
    <div className="export-done">
      <div className="export-done-icon">✓</div>
      <div className="export-done-msg">
        Your XML is ready. Click Reveal in Finder, then double-click the file to open it in Premiere.
      </div>
      <div className="export-done-path">{writtenPath}</div>
      <div className="form-hint" style={{ marginTop: 14 }}>
        After Premiere opens: right-click any clip → Proxy → Attach Proxies → point at your
        proxies folder. This links all clips to their proxies in one step.
      </div>
    </div>
  );
}

function ErrorView({ errorMsg, progressLog }) {
  return (
    <>
      <div className="export-error-msg">{errorMsg}</div>
      {progressLog.length > 0 && (
        <>
          <div className="form-label" style={{ marginTop: 12 }}>Log before failure</div>
          <div className="export-progress">
            {progressLog.map((entry, i) => (
              <div key={i} className={`export-progress-line export-progress-${entry.level}`}>
                {entry.msg}
              </div>
            ))}
          </div>
        </>
      )}
    </>
  );
}

function ExportOption({ checked, onChange, label, hint }) {
  return (
    <label className="export-option">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <div className="export-option-text">
        <div className="export-option-label">{label}</div>
        <div className="export-option-hint">{hint}</div>
      </div>
    </label>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function _defaultFilename(projectName, ideas) {
  const safeName = (projectName || "export").replace(/[^a-zA-Z0-9\-_]/g, "_");
  const date = new Date().toISOString().slice(0, 10);
  const count = ideas.length;
  return `${safeName}_${count}tl_${date}.xml`;
}
