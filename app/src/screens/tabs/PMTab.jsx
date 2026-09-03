import { useCallback, useEffect, useRef, useState } from "react";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { listen } from "@tauri-apps/api/event";
import { sendCommand } from "../../App.jsx";
import DropZone, { getHoveredKind, clearHoveredKind } from "../../components/DropZone.jsx";
import StageProgress from "../../components/StageProgress.jsx";
import SyncMatrix from "../../components/SyncMatrix.jsx";
import AudioSyncPreview from "../../components/AudioSyncPreview.jsx";

/**
 * PMTab — the Project Manager tab. Now the WHOLE first stage of the app:
 * declare the project, declare footage, organize it, process it, review
 * the sync. Fourth revision (2026-09-03), after Ryan used the third:
 *
 *   "Ingest doesnt make sense as its own page now. Move the ingest into
 *   the Project Manager tab. When I click organize it should automatically
 *   start the Run Pipeline process... i also noticed that when i clicked
 *   run pipeline it didnt know there was any b-roll attached to tag
 *   because the app doesnt understand the logic that we added for the
 *   Dual use checkbox. And after syncing the Assistant editor tab just
 *   shows the same synced window that the ingest page does. This entire
 *   process all still feels like the project manager responsibilities...
 *   Lets merge the ingest page with the project manager tab and take this
 *   task off of the ae tab because its not necessary there."
 *
 * Four real fixes/moves from that one message:
 *   1. IngestTab's run-pipeline button/modal/progress panels/transcripts
 *      readout are absorbed here, verbatim. IngestTab.jsx is deleted.
 *   2. Clicking "Organize" now auto-fires run_pipeline with the same
 *      default flags the modal used to compute, right after the manifest
 *      write succeeds. The manual modal (via "Run pipeline") stays
 *      available for re-runs (e.g. more footage added later, or skipping
 *      a stage on purpose).
 *   3. Real backend bug, not just UI: dual-use A-roll never reached
 *      B-roll tagging, because PreCut's `add_source` is path-keyed with
 *      exactly one kind per path (see project.py's own docstring) --
 *      a dual-use folder never showed up in sources_by_kind("broll") for
 *      pipeline.py's B-roll collection to find. Fixed in project.py
 *      (SourceFolder.dual_use field + Project.set_dual_use) and
 *      pipeline.py (_collect_videos unions in dual_use aroll sources when
 *      collecting "broll"). The dual-use checkbox here now calls the new
 *      `set_source_dual_use` command immediately, instead of only
 *      recording the flag in the manifest at Organize time -- verified
 *      with a scripted before/after check (see commit message).
 *   4. AETab.jsx's sync review (SyncMatrix + the AudioSyncPreview player)
 *      moves here too, since Ryan concluded audio sync review isn't
 *      distinct Assistant Editor work -- it's the same review the
 *      Ingest/PM merge already covers. AETab.jsx is deleted.
 *
 * PreCut's own kind name is "audio"; the Project Manifest contract's is
 * "source_audio" -- zones use "audio" throughout (matching add_source)
 * and translate to "source_audio" only when building organize_project's
 * request. "Assets" stays local/manifest-only: PreCut's Project model has
 * no such kind (never proxied/transcribed/tagged, just staged into the
 * project folder by organize_project).
 */
const SOURCE_ZONES = [
  { kind: "aroll", label: "A-Roll", title: "Interviews, talking-head footage", description: "Drag folders or files here" },
  { kind: "broll", label: "B-Roll", title: "Supplementary/cutaway footage", description: "Drag folders or files here" },
  { kind: "audio", label: "Source Audio", title: "Lav mics, external recorders", description: "Drag folders or files here" },
  { kind: "assets", label: "Assets", title: "Anything else that belongs to this project", description: "Drag folders or files here" },
];
const CONTRACT_KIND = { aroll: "aroll", broll: "broll", audio: "source_audio", assets: "assets" };
const PROJECT_TYPES = ["interview", "property_tour", "renovation", "event", "product", "other"];

export default function PMTab({ subscribe, project, jobs, hasRunning, onGoToIdeas, audienceProfiles }) {
  const [rootDir, setRootDir] = useState("");
  const [clientName, setClientName] = useState("");
  const [projectName, setProjectName] = useState("");
  const [projectType, setProjectType] = useState(PROJECT_TYPES[0]);
  const [brandAssetsDir, setBrandAssetsDir] = useState("");
  // A picklist over profiles authored once, app-wide, via the titlebar's
  // "audiences & goals" button -- not free text retyped per project
  // (Ryan, 2026-09-03). The selected profile's description is what
  // actually lands in the manifest's audience_goal field; feeds the
  // Assistant Editor's audience-informed transcript tagging downstream.
  const [audienceProfileId, setAudienceProfileId] = useState("");

  // "Assets" is the one zone with no PreCut-side model -- kept as plain
  // local path strings, same as every zone used to be before this merge.
  const [assetPaths, setAssetPaths] = useState([]);

  const [notice, setNotice] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  // Real (PreCut-backed) sources by kind, straight from project state.
  const realByKind = {
    aroll: project.sources.filter((s) => s.kind === "aroll"),
    broll: project.sources.filter((s) => s.kind === "broll"),
    audio: project.sources.filter((s) => s.kind === "audio"),
  };
  const dualUseAroll = realByKind.aroll.filter((s) => s.dual_use);
  // B-roll tagging runs on strictly-broll sources AND dual-use A-roll
  // (see pipeline.py's _collect_videos) -- mirror that here so the run
  // modal and the auto-run after Organize don't undercount it.
  const hasAroll = realByKind.aroll.length > 0;
  const hasBroll = realByKind.broll.length > 0 || dualUseAroll.length > 0;
  const hasAudio = realByKind.audio.length > 0;

  // Kept in a ref so the run_pipeline auto-fire (triggered from inside the
  // project_organized subscribe handler, set up once on mount) always
  // reads the CURRENT has-aroll/broll/audio state rather than whatever it
  // was when the effect first ran. Same pattern as addPathsRef below.
  const flagsRef = useRef({ hasAroll, hasBroll, hasAudio });
  useEffect(() => { flagsRef.current = { hasAroll, hasBroll, hasAudio }; });

  const runPipeline = useCallback(async (flags) => {
    const jobId = `run-${Date.now()}`;
    try {
      await sendCommand({
        type: "run_pipeline",
        job_id: jobId,
        run_proxies: !!flags.run_proxies,
        run_transcription: !!flags.run_transcription,
        run_tagging: !!flags.run_tagging,
        run_audio_index: !!flags.run_audio_index,
        run_audio_sync: !!flags.run_audio_sync,
      });
    } catch (e) {
      console.error(`run_pipeline failed: ${e}`);
    }
  }, []);

  useEffect(() => {
    return subscribe((ev) => {
      if (submitting && ev.type === "project_organized") {
        setResult(ev); setError(null); setSubmitting(false);
        // Ryan: "When I click organize it should automatically start the
        // Run Pipeline process." Fire it with the same "everything
        // applicable" defaults the manual modal used to default to.
        const f = flagsRef.current;
        runPipeline({
          run_proxies: f.hasAroll || f.hasBroll,
          run_transcription: f.hasAroll,
          run_tagging: f.hasBroll,
          run_audio_index: f.hasAudio,
          run_audio_sync: f.hasAroll && f.hasAudio,
        });
      } else if (submitting && ev.type === "error") {
        setError(ev.message); setResult(null); setSubmitting(false);
      }
    });
  }, [subscribe, submitting, runPipeline]);


  // Every currently-declared path, across all four zones, mapped to which
  // zone it's under -- used to catch cross-zone duplicates before they
  // reach the backend (add_source has no such check; organize_project's
  // validator does, but round-tripping there just to reject is worse UX).
  const declaredKindByPath = {};
  for (const kind of ["aroll", "broll", "audio"]) {
    for (const s of realByKind[kind]) declaredKindByPath[s.root_path] = kind;
  }
  for (const p of assetPaths) declaredKindByPath[p] = "assets";

  const addPaths = useCallback(async (kind, paths) => {
    const toAdd = [];
    let blocked = null;
    for (const p of paths) {
      const otherKind = declaredKindByPath[p];
      if (otherKind === kind) continue; // already declared here, no-op
      if (otherKind) { blocked = blocked || { path: p, otherKind }; continue; }
      toAdd.push(p);
    }
    if (blocked) {
      const { path, otherKind } = blocked;
      const name = basename(path);
      let msg;
      if (otherKind === "aroll") {
        msg = `"${name}" is already declared as A-Roll. If it also serves as B-Roll, ` +
              `use the "also B-Roll" checkbox next to it below instead of adding it here too.`;
      } else if (kind === "aroll") {
        msg = `"${name}" is already declared as ${labelFor(otherKind)}. Remove it from there first, ` +
              `then add it here as A-Roll and check "also B-Roll" if it serves both purposes.`;
      } else {
        msg = `"${name}" is already declared as ${labelFor(otherKind)}. A source can only have one kind ` +
              `(the A-Roll/B-Roll dual-use case above is the one exception).`;
      }
      setNotice(msg);
    }
    if (toAdd.length === 0) return;

    if (kind === "assets") {
      setAssetPaths((prev) => [...new Set([...prev, ...toAdd])]);
      return;
    }
    for (const p of toAdd) {
      try {
        await sendCommand({ type: "add_source", path: p, kind });
      } catch (e) {
        console.error(`add_source failed: ${e}`);
      }
    }
    await sendCommand({ type: "get_project_state" });
  }, [declaredKindByPath]);

  const removePath = useCallback(async (kind, path) => {
    if (kind === "assets") {
      setAssetPaths((prev) => prev.filter((p) => p !== path));
      return;
    }
    await sendCommand({ type: "remove_source", path });
    await sendCommand({ type: "get_project_state" });
  }, []);

  // Persists immediately via set_source_dual_use -- not just recorded for
  // the next Organize. This is the actual fix for the tagging bug: the
  // pipeline reads project.sources[].dual_use directly (pipeline.py's
  // _collect_videos), so the flag has to be live on the backend source
  // the moment it's checked, not just folded into organize_project later.
  const toggleDualUse = useCallback(async (path, current) => {
    try {
      await sendCommand({ type: "set_source_dual_use", path, dual_use: !current });
    } catch (e) {
      console.error(`set_source_dual_use failed: ${e}`);
    }
    await sendCommand({ type: "get_project_state" });
  }, []);

  const handlePickFiles = useCallback(async (kind) => {
    const selection = await openDialog({ multiple: true, directory: false, title: `Add ${kind} files` });
    if (!selection) return;
    addPaths(kind, Array.isArray(selection) ? selection : [selection]);
  }, [addPaths]);

  const handlePickFolder = useCallback(async (kind) => {
    const selection = await openDialog({ multiple: true, directory: true, title: `Add ${kind} folders` });
    if (!selection) return;
    addPaths(kind, Array.isArray(selection) ? selection : [selection]);
  }, [addPaths]);

  // Same drag-drop wiring IngestTab used to own: DropZone marks itself as
  // the hot zone on hover, we read that at Tauri's drop event and route
  // paths to the right kind. See DropZone.jsx's own docstring for why
  // (broken position-payload coordinates on macOS, Tauri 2.8's
  // duplicate-fire bug).
  const addPathsRef = useRef(addPaths);
  useEffect(() => { addPathsRef.current = addPaths; }, [addPaths]);
  const lastDropRef = useRef({ t: 0, key: "" });

  useEffect(() => {
    const unlistens = [];
    let cancelled = false;
    (async () => {
      try {
        const u = await listen("tauri://drag-drop", (event) => {
          const paths = event.payload?.paths || [];
          if (paths.length === 0) { clearHoveredKind(); return; }
          const key = paths.join("|");
          const now = Date.now();
          if (now - lastDropRef.current.t < 300 && lastDropRef.current.key === key) {
            clearHoveredKind(); return;
          }
          lastDropRef.current = { t: now, key };
          const kind = getHoveredKind();
          clearHoveredKind();
          if (!kind) return;
          addPathsRef.current(kind, paths);
        });
        if (cancelled) { u(); return; }
        unlistens.push(u);
        const u2 = await listen("tauri://drag-leave", () => clearHoveredKind());
        if (cancelled) { u2(); return; }
        unlistens.push(u2);
      } catch (e) {
        console.error("Failed to register drag-drop listeners:", e);
      }
    })();
    return () => {
      cancelled = true;
      for (const u of unlistens) { try { u(); } catch { /* ignore */ } }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pickRootDir = async () => {
    try {
      const selected = await openDialog({ directory: true, multiple: false, title: "Pick the project folder" });
      if (typeof selected === "string" && selected) setRootDir(selected);
    } catch (e) { console.error("Folder picker failed:", e); }
  };
  const pickBrandAssetsDir = async () => {
    try {
      const selected = await openDialog({ directory: true, multiple: false, title: "Pick a brand assets folder" });
      if (typeof selected === "string" && selected) setBrandAssetsDir(selected);
    } catch (e) { console.error("Folder picker failed:", e); }
  };

  const totalSources = realByKind.aroll.length + realByKind.broll.length
    + realByKind.audio.length + assetPaths.length;
  const canSubmit = rootDir.trim() && clientName.trim() && projectName.trim() && projectType && totalSources > 0 && !submitting;

  const handleOrganize = async () => {
    setSubmitting(true); setResult(null); setError(null);
    const sources = [];
    for (const kind of ["aroll", "broll", "audio"]) {
      for (const s of realByKind[kind]) {
        const entry = { path: s.root_path, kind: CONTRACT_KIND[kind] };
        if (kind === "aroll" && s.dual_use) entry.dual_use = true;
        sources.push(entry);
      }
    }
    for (const p of assetPaths) sources.push({ path: p, kind: "assets" });
    const selectedProfile = (audienceProfiles || []).find((p) => p.id === audienceProfileId);
    try {
      await sendCommand({
        type: "organize_project",
        root_dir: rootDir.trim(),
        client_name: clientName.trim(),
        project_name: projectName.trim(),
        project_type: projectType,
        sources,
        brand_assets_source_dir: brandAssetsDir.trim() || undefined,
        audience_goal: selectedProfile?.description || undefined,
      });
    } catch (e) {
      setError(String(e)); setSubmitting(false);
    }
  };

  // ---- Pipeline run (manual re-run modal) + progress, absorbed from
  // IngestTab -----------------------------------------------------------
  const [showRunModal, setShowRunModal] = useState(false);
  const handleConfirmRun = useCallback((flags) => {
    setShowRunModal(false);
    runPipeline(flags);
  }, [runPipeline]);
  const handleCancel = useCallback(async () => {
    for (const jobId of Object.keys(jobs)) {
      if (jobs[jobId].status === "running") {
        await sendCommand({ type: "cancel_job", job_id: jobId });
      }
    }
  }, [jobs]);

  const activePipelineJobs = Object.entries(jobs).filter(([, j]) => j.kind === "pipeline");
  const hasCompletedPipeline = activePipelineJobs.some(([, j]) => j.status === "done");
  const canAdvanceToIdeas = hasCompletedPipeline && !hasRunning && onGoToIdeas;

  // Audio sync preview selection (absorbed from AETab) -- which pair is
  // loaded in the player below.
  const [selectedPair, setSelectedPair] = useState(null);

  return (
    <div className="pm-tab">
      <h2>Project Manager</h2>
      <p className="pm-tab-sub">
        Point this at one real project. Declare footage once below —
        Organize writes the Project Manifest and starts processing it in
        the same step.
      </p>

      <FolderField
        label="Project folder (manifest.json is written here — the parent of the sources below, not the same path as one of them)"
        value={rootDir}
        onPick={pickRootDir}
      />

      <div className="pm-tab-row">
        <TextField label="Client name" value={clientName} onChange={setClientName} />
        <TextField label="Project name" value={projectName} onChange={setProjectName} />
      </div>
      <SelectField label="Project type" value={projectType} onChange={setProjectType} options={PROJECT_TYPES} />
      <label className="pm-tab-field">
        <span>Audience / content goal (optional)</span>
        <select value={audienceProfileId} onChange={(e) => setAudienceProfileId(e.target.value)}>
          <option value="">None selected</option>
          {(audienceProfiles || []).map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
        {!audienceProfiles?.length && (
          <span className="form-hint">
            No profiles yet — add one via the "audiences &amp; goals" button in the titlebar.
          </span>
        )}
      </label>

      {notice && (
        <div className="pm-tab-notice">
          {notice}
          <button type="button" onClick={() => setNotice(null)}>Dismiss</button>
        </div>
      )}

      <div className="drop-row pm-tab-dropzones">
        {SOURCE_ZONES.map((z) => (
          <DropZone
            key={z.kind}
            kind={z.kind}
            label={z.label}
            title={z.title}
            description={z.description}
            items={z.kind === "assets" ? assetPaths : realByKind[z.kind]}
            onRemove={(p) => removePath(z.kind, p)}
            onPick={() => handlePickFiles(z.kind)}
            onPickFolder={() => handlePickFolder(z.kind)}
          />
        ))}
      </div>

      {realByKind.aroll.length > 0 && (
        <div className="pm-tab-dualuse">
          <span className="pm-tab-dualuse-label">
            Dual-use — A-Roll that also serves as B-Roll (the subject keeps
            talking while the shooter grabs coverage). Tagged as B-roll by
            the pipeline the moment this is checked; check any that apply:
          </span>
          {realByKind.aroll.map((s) => (
            <label key={s.root_path} className="pm-tab-dualuse-row">
              <input
                type="checkbox"
                checked={!!s.dual_use}
                onChange={() => toggleDualUse(s.root_path, !!s.dual_use)}
              />
              <span title={s.root_path}>{s.display_name || basename(s.root_path)}</span>
            </label>
          ))}
        </div>
      )}

      <FolderField
        label="Brand assets folder (optional — logos, fonts; a separate thing from the Assets zone above)"
        value={brandAssetsDir}
        onPick={pickBrandAssetsDir}
      />

      <button className="pm-tab-submit" disabled={!canSubmit} onClick={handleOrganize}>
        {submitting ? "Organizing…" : "Organize (writes the manifest and starts processing)"}
      </button>

      {error && <pre className="pm-tab-error">{error}</pre>}

      {result && (
        <div className="pm-tab-result">
          <h3>{result.is_new_project ? "New project organized" : "Project updated"}</h3>
          <p>Manifest: <code>{result.manifest_path}</code></p>
          {result.added_source_ids.length > 0 && <p>Sources added: {result.added_source_ids.join(", ")}</p>}
          {result.staged_asset_files.length > 0 && <p>Brand assets staged: {result.staged_asset_files.length} file(s)</p>}
          {result.warnings.length > 0 && (
            <div className="pm-tab-warnings">
              <strong>Warnings:</strong>
              <ul>{result.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
            </div>
          )}
          <details>
            <summary>Full manifest</summary>
            <pre>{JSON.stringify(result.manifest, null, 2)}</pre>
          </details>
        </div>
      )}

      {/* ---- Processing, absorbed from IngestTab ---- */}
      <div className="pm-tab-pipeline">
        <div className="action-bar">
          <button
            className="btn btn-primary"
            onClick={() => setShowRunModal(true)}
            disabled={totalSources === 0 || hasRunning}
          >
            {hasRunning ? "Processing…" : "Run pipeline"}
          </button>
          {hasRunning && (
            <button className="btn btn-danger" onClick={handleCancel}>
              Cancel
            </button>
          )}
          <div className="action-spacer" />
          <div className="action-hint">
            Organize starts this automatically. Use this button to re-run
            (e.g. more footage added, or to skip a stage on purpose).
          </div>
        </div>

        {showRunModal && (
          <RunPipelineModal
            hasAroll={hasAroll}
            hasBroll={hasBroll}
            hasAudio={hasAudio}
            onConfirm={handleConfirmRun}
            onClose={() => setShowRunModal(false)}
          />
        )}

        {activePipelineJobs.length === 0 ? (
          <div className="empty-state">
            Drop footage above, then Organize (or Run pipeline) to process it.
            <br />
            A-roll proxies + transcription and B-roll proxies + tagging run in parallel.
          </div>
        ) : (
          activePipelineJobs.map(([jobId, job]) =>
            Object.entries(job.stages || {}).map(([stageName, stage]) => (
              <StageProgress
                key={`${jobId}-${stageName}`}
                stageName={stageName}
                stage={stage}
              />
            ))
          )
        )}

        {/* Sync review, absorbed from AETab -- Ryan: this was never
            distinct Assistant Editor work, it's the same review the
            Ingest/PM merge already covers. */}
        {project.audio_sync?.pairs?.length > 0 && (
          <>
            <SectionHeader
              title="Sync results"
              hint={
                <>
                  Scores from PreCut's own audio sync. Any pair it can't
                  confidently sync on its own — subject left the room,
                  came back, was on the phone elsewhere — gets checked
                  automatically for a usable stretch as part of the same
                  step; no separate action needed. If you've added or
                  changed footage, use <strong>Re-sync</strong> below —
                  a plain "Run pipeline" click reuses the cached result
                  and won't recompute anything.
                </>
              }
            />
            <div className="sync-resync-bar">
              <button
                className="btn btn-ghost"
                disabled={hasRunning}
                onClick={() => runPipeline({
                  run_proxies: false, run_transcription: false,
                  run_tagging: false, run_audio_index: false,
                  run_audio_sync: true, force_audio_sync: true,
                })}
              >
                Re-sync (ignore cached results)
              </button>
            </div>
            <SyncMatrix
              pairs={_collectLivePairs(jobs)}
              syncState={project.audio_sync}
              liveStatus={_liveSyncStatus(jobs)}
              onSelectPair={(pair, key) => setSelectedPair({ pair, key })}
              selectedKey={selectedPair?.key}
            />

            <SectionHeader
              title="Preview a pair"
              hint="Click any cell above to load it here and play it back."
            />
            <div className="ae-tab-preview">
              <AudioSyncPreview pair={selectedPair?.pair} />
            </div>
          </>
        )}

        <TranscriptsSection project={project} />

        {canAdvanceToIdeas && (
          <div className="floating-next-bar">
            <div className="floating-next-text">
              <span className="floating-next-title">Indexing complete</span>
              <span className="floating-next-sub">
                Footage transcribed and tagged. Ready for ideas?
              </span>
            </div>
            <button className="btn btn-primary" onClick={onGoToIdeas}>
              Next → Ideas
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function TranscriptsSection({ project }) {
  const rows = [];
  for (const src of project.sources) {
    if (src.kind !== "aroll") continue;
    for (const [filePath, status] of Object.entries(src.files || {})) {
      rows.push({
        sourcePath: filePath,
        displayName: _basename(filePath),
        folder: src.display_name,
        ...status,
      });
    }
  }
  if (rows.length === 0) return null;

  const statusOrder = { done: 0, success: 0, undefined: 1, failed: 2 };
  rows.sort((a, b) => {
    const as = statusOrder[a.transcript_status] ?? 1;
    const bs = statusOrder[b.transcript_status] ?? 1;
    if (as !== bs) return as - bs;
    return a.displayName.localeCompare(b.displayName);
  });

  const transcribedCount = rows.filter(r => r.transcript_status === "done").length;

  return (
    <div className="transcripts-section">
      <div className="transcripts-header">
        <div className="transcripts-section-title">Transcripts</div>
        <div className="transcripts-count">
          {transcribedCount} of {rows.length} A-roll file{rows.length !== 1 ? "s" : ""} transcribed
        </div>
      </div>
      <div className="transcripts-list">
        {rows.map((row) => (
          <TranscriptRow key={row.sourcePath} row={row} />
        ))}
      </div>
    </div>
  );
}

function TranscriptRow({ row }) {
  const status = row.transcript_status;
  const proxyStatus = row.proxy_status;

  let statusLabel, statusClass;
  if (status === "done") {
    statusLabel = `${row.transcript_phrase_count ?? "?"} phrases`;
    statusClass = "success";
  } else if (status === "failed") {
    statusLabel = row.transcript_error || "failed";
    statusClass = "error";
  } else if (proxyStatus === "success" || proxyStatus === "skipped") {
    statusLabel = "queued for transcription…";
    statusClass = "warn";
  } else if (proxyStatus === "failed") {
    statusLabel = "proxy failed — cannot transcribe";
    statusClass = "error";
  } else {
    statusLabel = "awaiting proxy";
    statusClass = "dim";
  }

  return (
    <div className={`transcript-row ${statusClass}`}>
      <div className="transcript-row-main">
        <div className="transcript-row-name">{row.displayName}</div>
        <div className="transcript-row-folder">{row.folder}</div>
      </div>
      <div className={`transcript-row-status ${statusClass}`}>
        {statusLabel}
      </div>
    </div>
  );
}

// Ryan: the sync review area had three buttons with no clear relationship
// between them ("really confusing on what all the buttons are for").
// These headers exist so each block visibly answers "what is this for
// and how does it relate to the others" without relying on prose in a
// hint paragraph nobody reads.
function SectionHeader({ title, hint }) {
  return (
    <div className="sync-section-header">
      <div className="sync-section-title">{title}</div>
      {hint && <div className="sync-section-hint">{hint}</div>}
    </div>
  );
}

function _basename(p) {
  const parts = p.split("/");
  return parts[parts.length - 1] || p;
}

function _collectLivePairs(jobs) {
  const all = [];
  for (const job of Object.values(jobs)) {
    const stage = job?.stages?.audio_sync;
    if (stage?.pairs) all.push(...stage.pairs);
  }
  return all;
}

function _liveSyncStatus(jobs) {
  for (const job of Object.values(jobs)) {
    const stage = job?.stages?.audio_sync;
    if (stage?.status === "running") return "running";
    if (stage?.status === "done") return "done";
  }
  return "idle";
}

/**
 * Stage picker modal shown when user clicks "Run pipeline" manually
 * (Organize triggers the same command automatically with "everything
 * applicable" defaults; this is for deliberate re-runs/partial runs).
 * Absorbed from IngestTab verbatim except sourcesByKind -> hasAroll/
 * hasBroll/hasAudio booleans (PMTab already computes those, including
 * dual-use in hasBroll).
 */
function RunPipelineModal({ hasAroll, hasBroll, hasAudio, onConfirm, onClose }) {
  const [flags, setFlags] = useState({
    run_proxies: hasAroll || hasBroll,
    run_transcription: hasAroll,
    run_tagging: hasBroll,
    run_audio_index: hasAudio,
    run_audio_sync: hasAroll && hasAudio,
  });

  const toggle = (key) => setFlags(f => ({ ...f, [key]: !f[key] }));
  const anySelected = Object.values(flags).some(Boolean);

  const presets = [
    {
      label: "Everything",
      title: "Run every applicable stage",
      apply: () => setFlags({
        run_proxies: hasAroll || hasBroll,
        run_transcription: hasAroll,
        run_tagging: hasBroll,
        run_audio_index: hasAudio,
        run_audio_sync: hasAroll && hasAudio,
      }),
    },
    {
      label: "Proxies only",
      title: "Just encode proxies — useful for getting a lighter cut going",
      apply: () => setFlags({
        run_proxies: true,
        run_transcription: false,
        run_tagging: false,
        run_audio_index: false,
        run_audio_sync: false,
      }),
    },
  ];

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal run-pipeline-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Run pipeline</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">×</button>
        </div>

        <div className="modal-body">
          <div className="run-pipeline-section">
            <div className="run-pipeline-section-label">PRESETS</div>
            <div className="run-pipeline-presets">
              {presets.map(p => (
                <button
                  key={p.label}
                  className="btn btn-ghost run-pipeline-preset"
                  onClick={p.apply}
                  title={p.title}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          <div className="run-pipeline-section">
            <div className="run-pipeline-section-label">STAGES</div>
            <StageCheckbox
              checked={flags.run_transcription}
              onChange={() => toggle("run_transcription")}
              label="Transcription"
              sub="Whisper transcribes A-roll. Proxies are created automatically if needed."
              disabled={!hasAroll}
              disabledReason="Needs A-roll sources"
            />
            <StageCheckbox
              checked={flags.run_tagging}
              onChange={() => toggle("run_tagging")}
              label="B-roll tagging"
              sub="AI tags each B-roll clip (including dual-use A-roll) with searchable keywords. Proxies are created automatically if needed."
              disabled={!hasBroll}
              disabledReason="Needs B-roll sources (or A-roll flagged dual-use)"
            />
            <StageCheckbox
              checked={flags.run_audio_index}
              onChange={() => toggle("run_audio_index")}
              label="Audio index"
              sub="ffprobe metadata for lav audio files (fast)."
              disabled={!hasAudio}
              disabledReason="Needs audio sources"
            />
            <StageCheckbox
              checked={flags.run_audio_sync}
              onChange={() => toggle("run_audio_sync")}
              label="Audio sync"
              sub="Cross-correlate A-roll camera audio with lav recordings."
              disabled={!hasAroll || !hasAudio}
              disabledReason="Needs both A-roll and audio sources"
            />
          </div>
        </div>

        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button
            className="btn btn-primary"
            onClick={() => onConfirm(flags)}
            disabled={!anySelected}
          >
            Run
          </button>
        </div>
      </div>
    </div>
  );
}

function StageCheckbox({ checked, onChange, label, sub, disabled, disabledReason }) {
  return (
    <label
      className={`run-pipeline-stage ${disabled ? "disabled" : ""}`}
      title={disabled ? disabledReason : undefined}
    >
      <input
        type="checkbox"
        checked={checked && !disabled}
        disabled={disabled}
        onChange={onChange}
      />
      <div className="run-pipeline-stage-body">
        <div className="run-pipeline-stage-label">{label}</div>
        <div className="run-pipeline-stage-sub">{disabled ? disabledReason : sub}</div>
      </div>
    </label>
  );
}

function TextField({ label, value, onChange }) {
  return (
    <label className="pm-tab-field">
      <span>{label}</span>
      <input type="text" value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}


function SelectField({ label, value, onChange, options }) {
  return (
    <label className="pm-tab-field">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  );
}

function FolderField({ label, value, onPick }) {
  return (
    <label className="pm-tab-field">
      <span>{label}</span>
      <div className="pm-tab-folder-row">
        <input type="text" value={value} readOnly placeholder="No folder selected" onClick={onPick} />
        <button type="button" onClick={onPick}>Browse…</button>
      </div>
    </label>
  );
}

function basename(p) {
  const parts = p.split("/");
  return parts[parts.length - 1] || p;
}

const KIND_LABELS = { aroll: "A-Roll", broll: "B-Roll", audio: "Source Audio", assets: "Assets" };
function labelFor(kind) {
  return KIND_LABELS[kind] || kind;
}
