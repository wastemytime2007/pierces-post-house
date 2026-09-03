import { useCallback, useState } from "react";
import { sendCommand } from "../../App.jsx";
import StageProgress from "../../components/StageProgress.jsx";
import SyncMatrix from "../../components/SyncMatrix.jsx";

/**
 * IngestTab — runs PreCut's processing pipeline over whatever footage the
 * Project tab has declared, and shows progress.
 *
 * Revised 2026-09-03 (Ryan): this tab used to have its own A-roll/B-roll/
 * Source-Audio drop zones, calling add_source directly -- the same three
 * zones the Project tab (PMTab) also had, for the Project Manifest. Ryan
 * caught that he was declaring the same folders twice. Source declaration
 * now lives only in the Project tab, which writes straight to PreCut's
 * own `project.sources` (via the same add_source/remove_source commands
 * this tab used to call). This tab reads that state and does the rest:
 *
 * Clicking "Run pipeline" submits run_pipeline, which:
 *   - Encodes proxies for videos → stream of file_done events
 *   - Transcribes A-roll proxies as they finish
 *   - Tags B-roll proxies as they finish
 *   - Indexes audio files (ffprobe only) in parallel
 */
export default function IngestTab({ project, jobs, hasRunning, onGoToIdeas }) {
  // Derive the sources by kind. Since `project` comes from App (backend-synced),
  // the source of truth is always backend state.
  const sourcesByKind = {
    aroll: project.sources.filter((s) => s.kind === "aroll"),
    broll: project.sources.filter((s) => s.kind === "broll"),
    audio: project.sources.filter((s) => s.kind === "audio"),
  };

  const [showRunModal, setShowRunModal] = useState(false);

  const handleOpenRunModal = useCallback(() => {
    setShowRunModal(true);
  }, []);

  const handleConfirmRun = useCallback(async (flags) => {
    setShowRunModal(false);
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

  const handleCancel = useCallback(async () => {
    for (const jobId of Object.keys(jobs)) {
      if (jobs[jobId].status === "running") {
        await sendCommand({ type: "cancel_job", job_id: jobId });
      }
    }
  }, [jobs]);

  const totalSources = project.sources.length;

  // Aggregate running jobs into display panels by stage
  const activePipelineJobs = Object.entries(jobs).filter(([, j]) => j.kind === "pipeline");

  // Drop 4.44: show a "Next → Ideas" CTA once a pipeline run has
  // finished successfully. Users were getting stuck on the Ingest tab
  // after pipelines completed because there was no visual affordance
  // pointing them to Ideas.
  //
  // Rules:
  //   * Show when any pipeline job is status=done AND none are running.
  //   * Hide while a new run is in progress (status=running) — the
  //     user is waiting, not ready to advance.
  //   * Also hide if every pipeline job was cancelled — the run didn't
  //     actually produce output, so "next" is misleading.
  const hasCompletedPipeline = activePipelineJobs.some(([, j]) => j.status === "done");
  const canAdvanceToIdeas = hasCompletedPipeline && !hasRunning && onGoToIdeas;

  return (
    <>
      {totalSources === 0 && (
        <div className="ingest-no-sources">
          No footage declared yet. Go to the <strong>Project</strong> tab
          to drop in A-roll, B-roll, and Source Audio — this tab processes
          whatever's declared there.
        </div>
      )}

      <div className="action-bar">
        <button
          className="btn btn-primary"
          onClick={handleOpenRunModal}
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
          Proxies go in a <code>proxies/</code> subfolder next to your source.
          Transcripts + B-roll index live in the project data folder.
        </div>
      </div>

      {showRunModal && (
        <RunPipelineModal
          sourcesByKind={sourcesByKind}
          onConfirm={handleConfirmRun}
          onClose={() => setShowRunModal(false)}
        />
      )}

      {/* Per-stage progress panels */}
      {activePipelineJobs.length === 0 ? (
        <div className="empty-state">
          Declare footage in the Project tab, then click Run pipeline here.
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

      {/* Drop 3.6: Audio sync matrix. Shows cached sync from the project;
          during a live run, also shows pairs as they stream in. */}
      <SyncMatrix
        pairs={_collectLivePairs(jobs)}
        syncState={project.audio_sync}
        liveStatus={_liveSyncStatus(jobs)}
      />

      {/* Drop 4.24: transcripts readout inlined here. Used to be a whole
          tab ("02 · Transcripts") but that stage never required user input —
          it was purely a status view. Moving it here removes a click and
          keeps the pipeline's output visible right where the editor is
          already looking. */}
      <TranscriptsSection project={project} />

      {/* Drop 4.47.2: Next → Ideas CTA, now FLOATING. Previously this was
       * a static panel at the bottom of the tab — users complained about
       * having to scroll all the way down once indexing finished. The
       * floating bar mirrors the .floating-export-bar pattern from
       * IdeasTab so the visual language is consistent. */}
      {canAdvanceToIdeas && (
        <div className="floating-next-bar">
          <div className="floating-next-text">
            <span className="floating-next-title">Indexing complete</span>
            <span className="floating-next-sub">
              Footage transcribed and tagged. Ready for ideas?
            </span>
          </div>
          <button
            className="btn btn-primary"
            onClick={onGoToIdeas}
          >
            Next → Ideas
          </button>
        </div>
      )}
    </>
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
  if (rows.length === 0) return null;  // nothing to show until A-roll is added

  // Sort: done first, then pending, then failed; alphabetical within each bucket
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

function _basename(p) {
  const parts = p.split("/");
  return parts[parts.length - 1] || p;
}

function _collectLivePairs(jobs) {
  // Merge audio_sync stage pairs from any running pipeline job
  const all = [];
  for (const job of Object.values(jobs)) {
    const stage = job?.stages?.audio_sync;
    if (stage?.pairs) {
      all.push(...stage.pairs);
    }
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
 * Drop 4.39: stage picker modal shown when user clicks Run pipeline.
 * Lets the user run only the subset they need — e.g. proxies alone so
 * they have lighter files to work with, or transcription only on an
 * already-proxied A-roll.
 *
 * Stages have soft dependencies: transcription/tagging/audio-sync all
 * need proxies to exist. The modal surfaces this but doesn't enforce it —
 * the editor might already have proxies from a previous run and want to
 * skip that expensive step. Backend handles a missing upstream gracefully
 * (the stage just has nothing to do).
 */
function RunPipelineModal({ sourcesByKind, onConfirm, onClose }) {
  const hasAroll = sourcesByKind.aroll.length > 0;
  const hasBroll = sourcesByKind.broll.length > 0;
  const hasAudio = sourcesByKind.audio.length > 0;

  // Default: everything that's applicable to this project's sources
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
    // Drop 4.44: "Everything except proxies" preset removed. Proxies
    // are a prerequisite for transcription + tagging, so claiming to
    // "skip" them while doing downstream work was misleading — the
    // pipeline would always create the missing proxies. Users who
    // want to re-run downstream stages on already-proxied footage
    // get the same outcome from "Everything": existing proxies get
    // the "skipped" fast path (~1ms per file).
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
            {/* Drop 4.44: Proxies removed from the per-stage toggles.
                Proxies are a prerequisite for transcription + tagging
                (Whisper + CLIP both operate on the .mp4 proxy, not the
                original .mov), so exposing them as a user-toggleable
                stage was misleading — unchecking it did nothing useful
                because proxies would still get created by the video
                pipeline before transcription/tagging ran. The backend
                now always generates proxies as needed; the "Proxies
                only" preset above is still available for the case
                where you just want proxies with no downstream work. */}
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
              sub="AI tags each B-roll clip with searchable keywords. Proxies are created automatically if needed."
              disabled={!hasBroll}
              disabledReason="Needs B-roll sources"
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

