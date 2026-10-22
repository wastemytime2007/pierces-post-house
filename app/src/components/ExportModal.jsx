import { useState, useEffect, useCallback, useRef } from "react";
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
  // Start the build the moment it opens (the Trim step's button), and open the result in this Premiere (an install path; none = the system's choice)
  autoStart = false,
  premiereApp = "",
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
  const writtenPathRef = useRef(null);
  const [openNote, setOpenNote] = useState(null);                 // { mode: "project" | "plain" | "failed", message }
  const openInPremiere = (path) => {
    setOpenNote({ mode: "working", message: "Opening in Premiere..." });
    sendCommand({ type: "open_in_premiere_project", path, ...(premiereApp ? { app: premiereApp } : {}) }).catch((e) => console.error("open_in_premiere_project failed:", e));
  };
  const [errorMsg, setErrorMsg] = useState(null);

  const addProgress = useCallback((msg, level = "info") => {
    setProgressLog((prev) => [...prev, { msg, level, ts: Date.now() }]);
  }, []);

  // Subscribe to backend-event via the global tauri listener. We filter
  // by job_id once we have one.
  useEffect(() => {
    if ((stage !== "running" && stage !== "done") || !jobId) return;

    let unlisten;
    (async () => {
      const { listen } = await import("@tauri-apps/api/event");
      unlisten = await listen("backend-event", (e) => {
        let ev;
        try { ev = JSON.parse(e.payload); } catch { return; }
        if (ev.job_id && ev.job_id !== jobId) return;  // not our job

        switch (ev.type) {
          case "export_started":
            addProgress(`Export started, ${ev.idea_count} timeline${ev.idea_count !== 1 ? "s" : ""}`, "accent");
            break;
          case "export_matching":
            addProgress(`Generating B-roll markers for "${ev.sequence_name}"…`);
            break;
          case "export_matched": {
            const phrases = ev.aroll_phrases ?? ev.aroll_phrase_count ?? 0;        // the backend names it aroll_phrase_count
            const phraseStr = `${phrases} A-roll phrase${phrases !== 1 ? "s" : ""}`;
            // Drop 3.7+: if we emitted markers, show those instead of cutaway count
            const markerCount = ev.broll_markers ?? ev.broll_marker_count ?? 0;
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
            addProgress("Lining your clean mic up with the camera sound…");
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
            writtenPathRef.current = ev.xml_path;
            // The point of the export is to be in Premiere: open it as soon as it is written (the library-only file is a bin, not a cut)
            if (ev.xml_path && !libraryOnly) openInPremiere(ev.xml_path);
            addProgress(`Wrote ${ev.sequences} sequence${ev.sequences !== 1 ? "s" : ""} + library of ${ev.broll_library_size} clips`, "accent");
            setStage("done");
            break;
          case "premiere_project":                          // the answer to "open in a new named project" (a plain open says why it was plain)
            if (ev.xml === writtenPathRef.current) setOpenNote({ mode: ev.mode, message: ev.message });
            break;
          case "export_error":
            setErrorMsg(ev.message || "Export failed");
            setStage("error");
            break;
          case "error":                                     // the backend refused before the export began: say so, never sit on "Starting..."
            if (stage !== "running") break;                 // but never turn a finished export into a failure because something unrelated complained afterwards
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
        filters: [{ name: "Premiere XML", extensions: ["xml"] }],
        title: "Save Premiere XML",
      });
      if (path) setSavePath(path);
    } catch (e) {
      console.error("save dialog failed:", e);
    }
  };

  const handleExport = async () => {
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
        output_path: savePath.trim(),                 // empty: the backend puts it in the project's exports folder
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

  const startedRef = useRef(false);
  useEffect(() => {
    if (autoStart && !startedRef.current) { startedRef.current = true; handleExport(); }
  }, []);                                                                   // eslint-disable-line react-hooks/exhaustive-deps

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
            {stage === "done" ? (libraryOnly ? "Library saved" : openNote?.mode === "project" ? "Opened in a new project" : openNote?.mode === "plain" ? "Opened in Premiere" : "Opening in Premiere")
              : stage === "error" ? "Could not build the project"
              : stage === "running" ? "Building…"
              : libraryOnly ? "Save the clip library only"
              : "Open in Premiere"}
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
            <DoneView writtenPath={writtenPath} projectName={projectName} openNote={openNote} libraryOnly={libraryOnly} />
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
                disabled={!libraryOnly && selectedIdeas.length === 0}
              >
                {libraryOnly
                  ? "Export library"
                  : `Open ${selectedIdeas.length} sequence${selectedIdeas.length !== 1 ? "s" : ""} in Premiere`}
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
              <button className="btn" onClick={handleReveal}>Show in Finder</button>
              {!libraryOnly && writtenPath && <button className="btn" onClick={() => openInPremiere(writtenPath)}>Open in Premiere again</button>}
              <button className="btn btn-primary" onClick={onExported}>Done</button>
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
          <div className="export-summary-title">Sequences to open</div>
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
                  
                  <strong>{title}</strong>
                  <span className="export-idea-meta"> · {preset} · {dur}</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      <div className="export-save-row">
        <div className="export-save-path">
          {savePath ? savePath : <>Saves in this project's <strong>exports</strong> folder</>}
        </div>
        <button className="btn btn-ghost" onClick={savePath ? () => setSavePath("") : onPickSave}>
          {savePath ? "Use the project folder" : "Somewhere else…"}
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
        hint="All your B-roll appears as a 'B-Roll Library' bin in Premiere, search by tag with Cmd+F"
      />
      <label className="export-option">
        <div className="export-option-text" style={{ width: "100%" }}>
          <div className="export-option-label">What frame rate B-roll should play at</div>
          <div className="export-option-hint">
            B-roll shot faster than this (slow motion) arrives with a second,
            labelled copy in Premiere, ready to interpret. Auto uses the slowest
            rate anything in this project was shot at.
          </div>
          <select
            value={brollTargetFpsMode}
            onChange={(e) => setBrollTargetFpsMode(e.target.value)}
            style={{ marginTop: 6 }}
          >
            <option value="auto">Auto (match your footage)</option>
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
        label="Line the clean mic up with the camera sound"
        hint="Finds how far apart the camera sound and the recorder are, and lines them up"
      />
      <ExportOption
        checked={includeCleanMic}
        onChange={setIncludeCleanMic}
        label="Bring the clean mic in on its own track"
        hint="It lands on A2, lined up when the match is strong, otherwise at the start for you to nudge"
      />
      <ExportOption
        checked={includeOverlay}
        onChange={setIncludeOverlay}
        label="Show the safe area for the platform"
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
          hint="When checked, files from your saved Default Includes rules are added to this export. Uncheck to skip them just for this one export, your saved rules aren't deleted."
        />
      )}

      <div className="form-hint" style={{ marginTop: 14, fontSize: 11 }}>
        <strong>Note:</strong> Premiere cannot be told about proxies through an XML. After importing this
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

function DoneView({ writtenPath, projectName, openNote, libraryOnly }) {
  return (
    <div className="export-done">
      <div className="export-done-icon">✓</div>
      <div className="export-done-msg">
        {libraryOnly ? "The clip library is saved." : "Your XML is ready. Trim each story your way in Premiere, then bring the XML back to Finish."}
      </div>
      {!libraryOnly && openNote && (
        <div className={`export-open-note export-open-${openNote.mode}`} role="status">{openNote.message}</div>
      )}
      <div className="export-done-path">{writtenPath}</div>
      <div className="form-hint" style={{ marginTop: 14 }}>
        In Premiere: right-click any clip, choose Proxy, then Attach Proxies, and point at your proxies folder. That links every clip to its proxy in one step.
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
