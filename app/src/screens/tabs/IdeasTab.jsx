import { useState, useCallback, useEffect } from "react";
import { sendCommand } from "../../App.jsx";
import ExportModal from "../../components/ExportModal.jsx";
import HelpTooltip from "../../components/HelpTooltip.jsx";
import AutoIncludeNudge from "../../components/AutoIncludeNudge.jsx";

/**
 * IdeasTab — AI Producer interface.
 *
 * Top row: two entry points
 *   1. "Analyze & Recommend" — Claude reads transcripts and pitches 3-5 concepts
 *   2. "Create Custom Brief" — user picks preset + writes brief, Claude executes
 *
 * Each idea card has a multi-select checkbox (Drop 3). When ≥1 card is
 * selected, a floating "Export N timelines" button appears, opening the
 * ExportModal for save-location + options.
 *
 * Only full plans (kind === "deliverable") are exportable. Concepts show a
 * disabled checkbox with a tooltip explaining they need refinement first.
 *
 * Drop 4.47: first-export nudge for the Default Includes feature. The
 * first time the user clicks Export — and only if they haven't already
 * configured auto-include rules and haven't dismissed the nudge — we
 * show "Wait! Did you know?" instead of opening ExportModal directly.
 * They can either set up Default Includes (opens that modal) or skip
 * for now (opens ExportModal as usual).
 */
export default function IdeasTab({
  project, ideas, researchByIdea, jobs, transcriptCount, settings, onOpenApiKeyHelp,
  shouldShowAutoIncludeNudge, onMarkAutoIncludeNudgeSeen, onOpenAutoIncludeModal,
  // Drop 4.47.3: live rule count for the ExportModal "Apply default
  // includes" toggle. We just forward it; the toggle and gating logic
  // live in ExportModal itself.
  autoIncludeRulesCount,
}) {
  const [showBriefForm, setShowBriefForm] = useState(false);
  const [refineTarget, setRefineTarget] = useState(null);  // idea_id being refined
  const [producerBusy, setProducerBusy] = useState(false);
  // Drop 3: multi-select state for XML export
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [showExportModal, setShowExportModal] = useState(false);
  // Drop 4.44: if true, the ExportModal opens in "library only" mode —
  // no ideas selected, backend skips the matcher, output is just the
  // All-Synced-A-Roll reference + B-roll library. Used by the no-key
  // empty state below so users without an API key can still get
  // something useful out of their footage.
  const [libraryOnlyExport, setLibraryOnlyExport] = useState(false);
  // Drop 4.47: nudge visibility. Local-only state — the persistent flag
  // lives on the backend and is updated via onMarkAutoIncludeNudgeSeen.
  const [showAutoIncludeNudge, setShowAutoIncludeNudge] = useState(false);

  // Drop 4.44: visible busy indicator for producer runs.
  //
  // The backend emits producer_started / producer_angle / producer_done,
  // but previously the only feedback during a 20-60s run was a button
  // text swap to "Working…" which users reliably missed. We now track
  // what kind of run is active plus how many ideas/angles we asked for,
  // so the render can show a loading panel AND stream-in progress.
  //
  // activeRun shape:
  //   { mode: "angles" | "analyze" | "more", expected: number, ideasAtStart: number }
  // The ideasAtStart anchor lets "generate more" show correct progress
  // (we only count NEW ideas produced during this run).
  const [activeRun, setActiveRun] = useState(null);

  // Drop 4.47: Export button click handler that conditionally shows the
  // first-export Default Includes nudge. If the user hasn't seen the
  // nudge AND has no auto-include rules yet, surface the nudge first
  // (clicking "Set up now" diverts to AutoIncludeModal; "Maybe later"
  // proceeds to the export modal). Once seen, this is a passthrough to
  // setShowExportModal.
  //
  // libraryOnly is captured at click time rather than reading state on
  // dispatch, so we can correctly route the user to the right export
  // mode after they dismiss the nudge.
  const requestExport = useCallback((libraryOnly) => {
    setLibraryOnlyExport(libraryOnly);
    if (shouldShowAutoIncludeNudge) {
      setShowAutoIncludeNudge(true);
    } else {
      setShowExportModal(true);
    }
  }, [shouldShowAutoIncludeNudge]);

  // Drop 4.47: handle nudge actions.
  const handleNudgeSetUp = useCallback(() => {
    setShowAutoIncludeNudge(false);
    if (onMarkAutoIncludeNudgeSeen) onMarkAutoIncludeNudgeSeen();
    if (onOpenAutoIncludeModal) onOpenAutoIncludeModal();
    // Don't open ExportModal — user is now configuring rules. They can
    // re-click Export when they're done.
  }, [onMarkAutoIncludeNudgeSeen, onOpenAutoIncludeModal]);

  const handleNudgeSkip = useCallback(() => {
    setShowAutoIncludeNudge(false);
    if (onMarkAutoIncludeNudgeSeen) onMarkAutoIncludeNudgeSeen();
    setShowExportModal(true);
  }, [onMarkAutoIncludeNudgeSeen]);

  // Track when a producer job is running so buttons can disable
  useEffect(() => {
    const busy = Object.values(jobs).some(
      (j) => j.kind === "pipeline" && j.status === "running"
    );
    const producerJobs = Object.entries(jobs).filter(([id]) =>
      id.startsWith("analyze-") || id.startsWith("plan-") || id.startsWith("refine-") ||
      id.startsWith("angles-") || id.startsWith("story-architect-")
    );
    const producerRunning = producerJobs.some(([, j]) => j.status === "running");
    setProducerBusy(producerRunning);

    // When producerBusy transitions from true → false, clear activeRun
    // so the GeneratingPanel hides. We can't simply derive this from
    // producerBusy in render because activeRun also holds the ideasAtStart
    // anchor, which we need to remember across renders WHILE busy but
    // release afterward to avoid a memory leak.
    if (!producerRunning) {
      setActiveRun(null);
    }
  }, [jobs]);

  const handleAnalyze = useCallback(async () => {
    // Drop 4.44: anchor for the loading panel. We capture how many ideas
    // existed before the run so the "generated X of Y" progress counter
    // reflects only what's new in this run. Analyze usually produces
    // 3-5 concepts; we show the "ideas" generic copy since we don't know
    // the exact count until producer_started arrives.
    setActiveRun({ mode: "analyze", expected: 4, ideasAtStart: ideas.length });
    try {
      await sendCommand({ type: "analyze", job_id: `analyze-${Date.now()}` });
    } catch (e) {
      // Clear on failure so the loading panel doesn't stick
      setActiveRun(null);
      console.error("analyze failed:", e);
    }
  }, [ideas.length]);

  // Drop 4.0: generate 3 story angles
  const handleGenerateAngles = useCallback(async () => {
    setActiveRun({ mode: "angles", expected: 3, ideasAtStart: ideas.length });
    try {
      await sendCommand({
        type: "story_generate",
        n_angles: 3,
        include_existing: false,
        job_id: `angles-${Date.now()}`,
      });
    } catch (e) {
      setActiveRun(null);
      console.error("story_generate failed:", e);
    }
  }, [ideas.length]);

  // 2026-09-03: story architect — builds ONE angle from already-flagged,
  // audience-scored transcript fragments plus live trend research (real
  // web search + real videos actually downloaded and watched). Distinct
  // from "Generate ideas" (PreCut's own generate_angles: no audience-goal
  // awareness, no live research, re-skims the transcript every time) so
  // both stay comparable side by side rather than one silently replacing
  // the other.
  const handleGenerateStoryArchitect = useCallback(async () => {
    setActiveRun({ mode: "story_architect", expected: 1, ideasAtStart: ideas.length });
    try {
      await sendCommand({
        type: "story_architect_generate",
        job_id: `story-architect-${Date.now()}`,
      });
    } catch (e) {
      setActiveRun(null);
      console.error("story_architect_generate failed:", e);
    }
  }, [ideas.length]);

  // Drop 4.0: "Request More Angles" — pass existing angles back as context
  const handleRequestMoreAngles = useCallback(async () => {
    setActiveRun({ mode: "more", expected: 3, ideasAtStart: ideas.length });
    try {
      await sendCommand({
        type: "story_generate",
        n_angles: 3,
        include_existing: true,
        job_id: `angles-${Date.now()}`,
      });
    } catch (e) {
      setActiveRun(null);
      console.error("story_generate (more) failed:", e);
    }
  }, [ideas.length]);

  // Drop 4.0: per-card format selection
  const handleSetAnglePreset = useCallback(async (idea_id, preset_key) => {
    try {
      await sendCommand({
        type: "set_angle_preset",
        idea_id,
        preset_key,
      });
    } catch (e) {
      console.error("set_angle_preset failed:", e);
    }
  }, []);

  // Drop 4.4: per-card platform + aspect selection (replaces single preset)
  const handleSetAnglePlatformAndAspect = useCallback(
    async (idea_id, platform_key, aspect_key) => {
      try {
        await sendCommand({
          type: "set_angle_platform_and_aspect",
          idea_id,
          platform_key: platform_key || "",
          aspect_key: aspect_key || "",
        });
      } catch (e) {
        console.error("set_angle_platform_and_aspect failed:", e);
      }
    },
    [],
  );

  const handleDiscard = useCallback(async (idea_id) => {
    await sendCommand({ type: "delete_idea", idea_id });
    // Also remove from selection if present
    setSelectedIds((prev) => {
      if (!prev.has(idea_id)) return prev;
      const next = new Set(prev);
      next.delete(idea_id);
      return next;
    });
  }, []);

  const toggleSelection = useCallback((idea_id, is_exportable) => {
    if (!is_exportable) return;  // concepts not selectable
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(idea_id)) next.delete(idea_id);
      else next.add(idea_id);
      return next;
    });
  }, []);

  // Clear selected ids that no longer exist in the project (e.g. after discard)
  useEffect(() => {
    setSelectedIds((prev) => {
      if (prev.size === 0) return prev;
      const valid = new Set(ideas.map((i) => i.idea_id));
      let changed = false;
      const next = new Set();
      for (const id of prev) {
        if (valid.has(id)) next.add(id);
        else changed = true;
      }
      return changed ? next : prev;
    });
  }, [ideas]);

  const selectedExportable = ideas.filter(
    (i) => selectedIds.has(i.idea_id) &&
           (i.kind === "deliverable" || i.kind === "story_angle")
  );
  const hasAngles = ideas.some((i) => i.kind === "story_angle");

  if (transcriptCount === 0 && ideas.length === 0) {
    return (
      <div className="empty-state">
        No transcripts available yet.
        <br />
        <br />
        Run the pipeline on the Ingest tab to transcribe your A-roll, then
        come back here to generate ideas with the AI producer.
      </div>
    );
  }

  return (
    <>
      <div className="ideas-actions">
        {/* Drop 4.25: one button that adapts based on existing angles.
            First click → "Generate ideas" (no prior context).
            After angles exist → "Generate more" (passes existing angles
            back as context so the LLM excludes them).
            Collapsing the two buttons protects against an editor hitting
            "Generate" at any stage and burning credits on duplicate angles. */}
        <button
          className="btn btn-primary"
          onClick={hasAngles ? handleRequestMoreAngles : handleGenerateAngles}
          disabled={transcriptCount === 0 || producerBusy}
          title={
            transcriptCount === 0
              ? "Run transcription first"
              : hasAngles
                ? "Generate more story angles — the existing ones are passed to the AI as context so it won't duplicate them"
                : "Generate 3 story angles from the transcript"
          }
        >
          {producerBusy
            ? (
              <>
                <span className="btn-spinner" aria-hidden="true" />
                {activeRun?.mode === "more" ? "Generating more…" : "Generating ideas…"}
              </>
            )
            : hasAngles
              ? "Generate more"
              : "Generate ideas"}
        </button>
        <HelpTooltip>
          The AI producer reads your transcripts and proposes{" "}
          <strong>3 story angles</strong> you could cut from the footage.
          Each one appears as a card below. <strong>Tick the checkboxes</strong>{" "}
          on the ones you want — a floating <strong>Export</strong> bar
          appears at the bottom of the screen once you've selected at least
          one.
        </HelpTooltip>
        <button
          className="btn btn-secondary"
          onClick={handleGenerateStoryArchitect}
          disabled={transcriptCount === 0 || producerBusy}
          title="Builds one story arc from the audience-scored transcript-flagging fragments plus live trend research (real web search, real trending videos actually watched) — requires an audience/content goal set on the Project tab, and the pipeline's transcript-flagging stage to have run"
        >
          {producerBusy && activeRun?.mode === "story_architect"
            ? (
              <>
                <span className="btn-spinner" aria-hidden="true" />
                Researching + building arc…
              </>
            )
            : "Generate from flagged fragments"}
        </button>
        <HelpTooltip>
          Builds <strong>one story arc</strong> from the fragments Assistant
          Editor already flagged as relevant to this project's stated
          audience/content goal, informed by <strong>live trend research</strong>{" "}
          — real web search plus real trending videos actually downloaded
          and watched, not read about. Expand a card's <strong>Research</strong>{" "}
          section to see the sources. Needs an audience goal set on the
          Project tab and transcript flagging to have already run.
        </HelpTooltip>
        <div className="action-spacer" />
        <details className="legacy-actions">
          <summary className="btn btn-ghost legacy-actions-summary">Legacy tools</summary>
          <div className="legacy-actions-body">
            <button
              className="btn"
              onClick={handleAnalyze}
              disabled={transcriptCount === 0 || producerBusy}
              title="Older 'Analyze & Recommend' flow — pitches 3-5 deliverable concepts"
            >
              Analyze & recommend
            </button>
            <button
              className="btn"
              onClick={() => setShowBriefForm(true)}
              disabled={transcriptCount === 0 || producerBusy}
              title="Older flow — user supplies preset + brief, the AI producer builds a full deliverable"
            >
              Create custom brief
            </button>
          </div>
        </details>
        {/* Drop 4.47.2: footage-only escape hatch. Always-visible button
         * for users who have an API key but just want a Premiere project
         * skeleton with their footage organized — no AI ideas, no API
         * credits used. Mirrors the path the no-key empty state has used
         * since 4.44, but available to ALL users. */}
        <button
          className="btn btn-ghost"
          onClick={() => requestExport(true)}
          disabled={producerBusy}
          title="Skip ideas — export a Premiere project with just your indexed footage. No API credits used."
        >
          Skip ideas — export footage only
        </button>
        <HelpTooltip align="right">
          Builds a Premiere XML containing your A-roll with synced audio,
          your tagged B-roll library, and the standard bin tree —{" "}
          <strong>without</strong> any AI-generated story sequences.
          Useful when you just want a clean project skeleton to start
          editing manually. Doesn&rsquo;t use any API credits.
        </HelpTooltip>
        <div className="action-hint">
          {transcriptCount > 0
            ? `Using ${transcriptCount} transcribed A-roll file${transcriptCount !== 1 ? "s" : ""}`
            : "Transcribe A-roll first"}
        </div>
      </div>

      {showBriefForm && (
        <BriefForm
          onClose={() => setShowBriefForm(false)}
        />
      )}

      <div className="ideas-grid">
        {/* Drop 4.44: streaming generation feedback.
            - When no ideas exist AND producer is busy → the loading panel
              completely replaces the "No ideas yet" empty state. This is
              the first-generate case where users previously saw nothing
              change after clicking the button.
            - When ideas DO exist AND producer is busy ("generate more") →
              a thin strip at the top of the grid signals activity without
              hiding the existing ideas.
            Ideas stream in one at a time as `producer_angle` events arrive,
            so the progress count updates live. */}
        {producerBusy && ideas.length === 0 ? (
          <GeneratingPanel
            activeRun={activeRun}
            generatedCount={0}
          />
        ) : ideas.length === 0 ? (
          // Drop 4.44: if no API key, show a dedicated no-key empty state
          // with two obvious next steps. Otherwise, the standard "click
          // Generate" message. `settings` may be undefined briefly at
          // startup — in that case default to the standard message.
          (settings && settings.active_source === "none") ? (
            <div className="ideas-nokey-empty">
              <div className="ideas-nokey-title">
                No API key — you can&rsquo;t generate ideas yet
              </div>
              <div className="ideas-nokey-body">
                PreCut uses an AI producer to read your transcripts and propose
                cut ideas — that part needs an API key. You can still export
                your indexed footage (all A-roll with synced audio + tagged
                B-roll library) as a Premiere XML right now, without a key.
              </div>
              <div className="ideas-nokey-actions">
                <button
                  className="btn btn-primary"
                  onClick={() => requestExport(true)}
                >
                  Export library only →
                </button>
                <button
                  className="btn btn-ghost"
                  onClick={() => onOpenApiKeyHelp && onOpenApiKeyHelp()}
                >
                  Help me set up a key
                </button>
              </div>
            </div>
          ) : (
            <div className="empty-state empty-state-with-action">
              <div>No ideas yet. Click <strong>Generate ideas</strong> to get started.</div>
              <div style={{ marginTop: 14, fontSize: 12, color: "var(--fg-2)" }}>
                Or skip the AI step entirely and{" "}
                <button
                  type="button"
                  className="empty-state-link"
                  onClick={() => requestExport(true)}
                  disabled={producerBusy}
                >
                  export your footage as a project skeleton →
                </button>
              </div>
            </div>
          )
        ) : (
          <>
            {producerBusy && (
              <GeneratingPanel
                activeRun={activeRun}
                generatedCount={Math.max(
                  0,
                  ideas.length - (activeRun?.ideasAtStart ?? ideas.length)
                )}
                compact
              />
            )}
            {ideas.map((idea) => (
              <IdeaCard
                key={idea.idea_id}
                idea={idea}
                research={researchByIdea?.[idea.idea_id]}
                onFetchResearch={() =>
                  sendCommand({ type: "get_story_research", idea_id: idea.idea_id }).catch(() => {})
                }
                onRefine={() => setRefineTarget(idea.idea_id)}
                onDiscard={() => handleDiscard(idea.idea_id)}
                onSetPreset={handleSetAnglePreset}
                onSetPlatformAndAspect={handleSetAnglePlatformAndAspect}
                disabled={producerBusy}
                selected={selectedIds.has(idea.idea_id)}
                onToggleSelect={toggleSelection}
              />
            ))}
          </>
        )}
      </div>

      {refineTarget && (
        <RefineModal
          idea={ideas.find((i) => i.idea_id === refineTarget)}
          onClose={() => setRefineTarget(null)}
          onSubmitted={() => setRefineTarget(null)}
        />
      )}

      {/* Drop 4.27: hide the floating trigger bar while the export modal
          is open — otherwise the bar floats over the modal and its
          "Export" button covers the modal's own "Export" button. */}
      {selectedExportable.length > 0 && !showExportModal && (
        <div className="floating-export-bar">
          <div className="floating-export-count">
            <span className="floating-export-badge">{selectedExportable.length}</span>
            {selectedExportable.length === 1 ? "timeline" : "timelines"} selected
          </div>
          <button className="btn btn-ghost" onClick={() => setSelectedIds(new Set())}>
            Clear
          </button>
          <button
            className="btn btn-primary"
            onClick={() => requestExport(false)}
          >
            Export to Premiere XML →
          </button>
        </div>
      )}

      {/* Drop 4.47: first-export nudge for the Default Includes feature.
          Shown ONCE — the moment the user first clicks Export — to surface
          a feature they're unlikely to discover on their own. After
          dismiss or "Set up now", the auto_include_nudge_seen flag is
          set so this never reappears. */}
      {showAutoIncludeNudge && (
        <AutoIncludeNudge
          onSetUp={handleNudgeSetUp}
          onSkip={handleNudgeSkip}
        />
      )}

      {showExportModal && (
        <ExportModal
          selectedIdeas={libraryOnlyExport ? [] : selectedExportable}
          projectName={project?.name || "project"}
          brollCount={project?.broll_clip_count || 0}
          autoIncludeRulesCount={autoIncludeRulesCount || 0}
          libraryOnly={libraryOnlyExport}
          onClose={() => {
            setShowExportModal(false);
            setLibraryOnlyExport(false);
          }}
          onExported={() => {
            setShowExportModal(false);
            setLibraryOnlyExport(false);
            setSelectedIds(new Set());
          }}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Idea card
// ---------------------------------------------------------------------------

// Drop 4.4: two-field selection — platform first, aspect filtered by platform.
// Keep these in sync with presets.py ALL_PLATFORMS and ASPECT_PRESET_KEYS.

// Platform options. An empty `key` means "no platform — A-roll native, no overlay"
const PLATFORM_OPTIONS = [
  { key: "", label: "— None (A-roll native, no overlay) —", allowedAspects: null },
  { key: "platform_youtube_ad", label: "YouTube Ad (safe-zone)",
    allowedAspects: ["aspect_horizontal_16_9", "aspect_horizontal_16_9_4k",
                     "aspect_vertical_9_16", "aspect_square_1_1"] },
  { key: "platform_ig_reels", label: "Instagram Reel",
    allowedAspects: ["aspect_vertical_9_16"] },
  { key: "platform_tiktok", label: "TikTok",
    allowedAspects: ["aspect_vertical_9_16"] },
  { key: "platform_youtube_shorts", label: "YouTube Shorts",
    allowedAspects: ["aspect_vertical_9_16"] },
  { key: "platform_facebook_reels", label: "Facebook Reels",
    allowedAspects: ["aspect_vertical_9_16"] },
  { key: "platform_x_vertical", label: "X (Twitter) Vertical",
    allowedAspects: ["aspect_vertical_9_16"] },
];

// Aspect options. An empty `key` means "no aspect chosen — A-roll native dims"
const ASPECT_OPTIONS = [
  { key: "", label: "— None (A-roll native size) —" },
  { key: "aspect_horizontal_16_9", label: "Horizontal · 16:9 (HD)" },
  { key: "aspect_horizontal_16_9_4k", label: "Horizontal · 16:9 (4K)" },
  { key: "aspect_vertical_9_16", label: "Vertical · 9:16" },
  { key: "aspect_square_1_1", label: "Square · 1:1" },
];

function GeneratingPanel({ activeRun, generatedCount, compact = false }) {
  // Fall back to neutral copy if activeRun is somehow missing. Shouldn't
  // happen in practice (we set activeRun before sending the command) but
  // the panel should still be usable if it does.
  const mode = activeRun?.mode || "angles";
  const expected = activeRun?.expected ?? 3;

  // Headline changes by mode so users know whether they're generating
  // for the first time or expanding existing ideas. Kept under ~50 chars
  // to fit comfortably on narrow windows.
  const headline = mode === "analyze"
    ? "Analyzing your transcripts…"
    : mode === "more"
      ? "Generating more ideas…"
      : "Reading your transcripts…";

  // Sub-line describes what's happening, and updates as angles stream in.
  // "Generated X of Y so far" is only meaningful once we've seen at least
  // one result — before that, show a time estimate instead.
  const hasStreamed = generatedCount > 0;
  const sub = hasStreamed
    ? `Generated ${generatedCount} of ${expected} so far…`
    : "This usually takes 20–60 seconds.";

  return (
    <div className={`generating-panel ${compact ? "generating-panel-compact" : ""}`}>
      <span className="generating-spinner" aria-hidden="true" />
      <div className="generating-panel-body">
        <div className="generating-panel-title">{headline}</div>
        <div className="generating-panel-sub">{sub}</div>
      </div>
    </div>
  );
}


function IdeaCard({ idea, research, onFetchResearch, onRefine, onDiscard, onSetPreset, onSetPlatformAndAspect, disabled, selected, onToggleSelect }) {
  const d = idea.data || {};
  const isAngle = idea.kind === "story_angle";
  const isFull = idea.kind === "deliverable";
  const isExportable = isAngle || isFull;

  if (isAngle) {
    return (
      <StoryAngleCard
        idea={idea}
        research={research}
        onFetchResearch={onFetchResearch}
        onDiscard={onDiscard}
        onSetPreset={onSetPreset}
        onSetPlatformAndAspect={onSetPlatformAndAspect}
        disabled={disabled}
        selected={selected}
        onToggleSelect={onToggleSelect}
      />
    );
  }

  // Legacy: concept or deliverable card
  const title = d.concept || d.title || "Untitled";
  const pitch = d.pitch || d.summary || d.why_it_works || "";
  const preset = d.suggested_preset || d.preset_key || "—";
  const duration =
    d.estimated_duration != null ? `${d.estimated_duration.toFixed(0)}s` :
    d.total_target_duration != null ? `${d.total_target_duration.toFixed(0)}s` : null;
  const tone = d.tone;

  const isRefined = idea.refinement_count > 0;

  return (
    <div className={`idea-card ${isFull ? "is-full" : "is-concept"} ${selected ? "is-selected" : ""}`}>
      <div className="idea-card-header">
        {isExportable ? (
          <label
            className="idea-card-checkbox"
            title={selected ? "Selected for export" : "Select for export"}
            onClick={(e) => e.stopPropagation()}
          >
            <input
              type="checkbox"
              checked={!!selected}
              onChange={() => onToggleSelect?.(idea.idea_id, true)}
            />
            <span className="idea-card-checkbox-box" />
          </label>
        ) : (
          <span
            className="idea-card-export-hint"
            title="Refine this concept into a full plan before it can be exported"
          >
            refine to export
          </span>
        )}
        <div className="idea-card-kind">
          {isFull ? "Full plan" : "Concept"}
          {isRefined && (
            <span className="idea-card-refined">
              · refined ×{idea.refinement_count}
            </span>
          )}
        </div>
        <div className="idea-card-meta">
          {preset}{duration ? ` · ${duration}` : ""}
        </div>
      </div>

      <h3 className="idea-card-title">{title}</h3>
      {tone && <div className="idea-card-tone">{tone}</div>}
      <p className="idea-card-pitch">{pitch}</p>

      {isFull && d.segments && d.segments.length > 0 && (
        <div className="idea-card-segments">
          <div className="idea-card-section-label">Structure</div>
          <ol className="idea-card-segments-list">
            {d.segments.slice(0, 6).map((seg, i) => (
              <li key={i}>
                <span className="seg-duration">
                  {seg.target_duration ? `${seg.target_duration.toFixed(0)}s` : ""}
                </span>
                <span className="seg-intent">{seg.editorial_intent || seg.phrase_text || ""}</span>
              </li>
            ))}
            {d.segments.length > 6 && (
              <li className="seg-more">+{d.segments.length - 6} more…</li>
            )}
          </ol>
        </div>
      )}

      {isFull && d.broll_themes && d.broll_themes.length > 0 && (
        <div className="idea-card-themes">
          {d.broll_themes.slice(0, 5).map((t, i) => (
            <span key={i} className="idea-card-theme-chip">{t}</span>
          ))}
        </div>
      )}

      <div className="idea-card-actions">
        <button
          className="btn btn-ghost"
          onClick={onRefine}
          disabled={disabled}
        >
          {isFull ? "Refine further" : "Refine into plan"}
        </button>
        <div className="action-spacer" />
        <button
          className="btn btn-ghost"
          onClick={onDiscard}
          disabled={disabled}
          title="Delete this idea"
        >
          Discard
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Story Angle card — Drop 4.0
// ---------------------------------------------------------------------------

function StoryAngleCard({ idea, research, onFetchResearch, onDiscard, onSetPreset, onSetPlatformAndAspect, disabled, selected, onToggleSelect }) {
  const d = idea.data || {};
  const brief = d.brief || {};
  const title = brief.title || "Untitled angle";
  const hook = brief.hook || "";
  const why = brief.why_it_works || "";
  const tone = brief.tone || "";
  const audience = brief.target_audience || "";
  const cta = brief.call_to_action || "";
  // Drop 4.1: prefer source_ranges for the preview list; fall back to
  // phrase_previews for old Drop 4.0 angles persisted before the 4.1 pivot.
  const sourceRanges = d.source_ranges || [];
  const previews = d.phrase_previews || [];
  const useRanges = sourceRanges.length > 0;
  const totalRangeDuration = sourceRanges.reduce(
    (acc, r) => acc + Math.max(0, (r.source_end_sec || 0) - (r.source_start_sec || 0)),
    0,
  );
  const duration = useRanges
    ? `~${Math.round(totalRangeDuration)}s`
    : brief.target_duration_sec
      ? `~${Math.round(brief.target_duration_sec)}s`
      : null;

  // Drop 4.4: two-field selection (platform + aspect). Either can be empty
  // "" → A-roll native behavior for that axis. Persisted at the envelope
  // level (idea.selected_platform_key, idea.selected_aspect_key).
  // Drop 4.8: DO NOT fall back to idea.selected_preset_key for aspect.
  // The planner's suggested_preset was leaking into the dropdown UI,
  // making new angles show a pre-filled aspect instead of "None".
  const currentPlatform = idea.selected_platform_key || "";
  const currentAspect = idea.selected_aspect_key || "";

  const [showAllItems, setShowAllItems] = useState(false);
  const visibleRanges = showAllItems ? sourceRanges : sourceRanges.slice(0, 3);
  const visiblePreviews = showAllItems ? previews : previews.slice(0, 4);

  // 2026-09-03: sourced trend-research audit trail — the answer to "where
  // do I see any of that." `research` is undefined until fetched, null
  // once fetched but this angle has none (PreCut's own generate_angles
  // cards never will), or the real {text_findings, video_findings,
  // unverified} object once loaded.
  const [showResearch, setShowResearch] = useState(false);
  const handleToggleResearch = () => {
    const next = !showResearch;
    setShowResearch(next);
    if (next && research === undefined) onFetchResearch?.();
  };

  // Compute which aspects are valid for the selected platform. If platform
  // is "" (None), all aspects are valid. When platform has a constraint and
  // the current aspect isn't in its allowed list, auto-clear aspect so the
  // UI doesn't show a stale/invalid selection.
  const selectedPlatform = PLATFORM_OPTIONS.find((p) => p.key === currentPlatform);
  const allowedAspects = selectedPlatform?.allowedAspects || null;
  const availableAspects = ASPECT_OPTIONS.filter((a) =>
    a.key === "" || !allowedAspects || allowedAspects.includes(a.key)
  );

  const handlePlatformChange = (e) => {
    const newPlatform = e.target.value;
    const newPlatformDef = PLATFORM_OPTIONS.find((p) => p.key === newPlatform);
    const newAllowed = newPlatformDef?.allowedAspects || null;

    let newAspect = currentAspect;
    // If the currently-chosen aspect isn't valid for the new platform, reset.
    if (newAllowed && newAspect && !newAllowed.includes(newAspect)) {
      newAspect = "";
    }
    // If the platform has exactly one allowed aspect, auto-select it.
    // (TikTok/Instagram/etc → Vertical 9:16 locked.)
    if (newAllowed && newAllowed.length === 1) {
      newAspect = newAllowed[0];
    }
    onSetPlatformAndAspect?.(idea.idea_id, newPlatform, newAspect);
  };

  const handleAspectChange = (e) => {
    onSetPlatformAndAspect?.(idea.idea_id, currentPlatform, e.target.value);
  };

  const formatTime = (sec) => {
    if (sec == null) return "?";
    const mins = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${mins}:${String(s).padStart(2, "0")}`;
  };

  return (
    <div className={`idea-card is-angle ${selected ? "is-selected" : ""}`}>
      <div className="idea-card-header">
        <label
          className="idea-card-checkbox"
          title={selected ? "Selected for export" : "Select for export"}
          onClick={(e) => e.stopPropagation()}
        >
          <input
            type="checkbox"
            checked={!!selected}
            onChange={() => onToggleSelect?.(idea.idea_id, true)}
          />
          <span className="idea-card-checkbox-box" />
        </label>
        <div className="idea-card-kind">
          <span className="idea-card-angle-badge">★ Story angle</span>
        </div>
        <div className="idea-card-meta">
          {useRanges
            ? `${sourceRanges.length} range${sourceRanges.length !== 1 ? "s" : ""}`
            : `${(d.phrase_ids || []).length} phrase${(d.phrase_ids || []).length !== 1 ? "s" : ""}`}
          {duration ? ` · ${duration}` : ""}
        </div>
      </div>

      <h3 className="idea-card-title">{title}</h3>
      {tone && <div className="idea-card-tone">{tone}</div>}
      {hook && <p className="idea-card-hook"><strong>Hook:</strong> {hook}</p>}
      {why && <p className="idea-card-pitch">{why}</p>}
      {audience && (
        <div className="idea-card-meta-row">
          <span className="idea-card-meta-label">Audience:</span> {audience}
        </div>
      )}
      {cta && (
        <div className="idea-card-meta-row">
          <span className="idea-card-meta-label">CTA:</span> {cta}
        </div>
      )}

      <div className="idea-card-research">
        <button
          type="button"
          className="btn btn-link btn-sm"
          onClick={handleToggleResearch}
        >
          {showResearch ? "Hide research" : "Show research"}
        </button>
        {showResearch && (
          research === undefined ? (
            <div className="idea-card-research-body idea-card-research-loading">
              Loading…
            </div>
          ) : research === null ? (
            <div className="idea-card-research-body idea-card-research-empty">
              No research on file for this idea — it wasn't built by the
              story architect (probably one of PreCut's own "Generate
              ideas" angles, which don't do live trend research).
            </div>
          ) : (
            <div className="idea-card-research-body">
              {research.editorial_qna && (
                <div className="idea-card-research-section">
                  <div className="idea-card-section-label">
                    Editorial Q&amp;A (also folded into "why it works" above)
                  </div>
                  <ul className="idea-card-research-list">
                    <li><strong>Bigger story:</strong> {research.editorial_qna.bigger_story}</li>
                    <li><strong>Why watch:</strong> {research.editorial_qna.why_watch}</li>
                    <li><strong>Relates to the viewer:</strong> {research.editorial_qna.viewer_relevance}</li>
                    <li><strong>CTA:</strong> {research.editorial_qna.cta}</li>
                  </ul>
                </div>
              )}
              {research.named_trends?.length > 0 && (
                <div className="idea-card-research-section">
                  <div className="idea-card-section-label">
                    Specific named trends found (not generic advice)
                  </div>
                  <ul className="idea-card-research-list">
                    {research.named_trends.map((t, i) => (
                      <li key={i}>
                        <strong>{t.name}</strong>
                        {t.description && <div className="idea-card-research-note">{t.description}</div>}
                        {t.source && (
                          <div className="idea-card-research-note">
                            <a href={t.source} target="_blank" rel="noreferrer">source</a>
                          </div>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {research.video_findings?.filter((v) => v.relevant).length > 0 && (
                <div className="idea-card-research-section">
                  <div className="idea-card-section-label">
                    Actually watched (real videos, real frames)
                  </div>
                  <ul className="idea-card-research-list">
                    {research.video_findings.filter((v) => v.relevant).map((v, i) => (
                      <li key={i}>
                        <a href={v.url} target="_blank" rel="noreferrer">{v.url}</a>
                        {v.detected_cuts != null && (
                          <div className="idea-card-research-metric">
                            {v.detected_cuts} real detected cuts over {v.duration_sec}s
                            {" "}({v.cuts_per_sec} cuts/sec) — measured, not estimated
                          </div>
                        )}
                        {(v.audio_track || v.audio_artist) && (
                          <div className="idea-card-research-metric">
                            🎵 {v.audio_track || "Unknown track"}
                            {v.audio_artist ? ` — ${v.audio_artist}` : ""}
                            {" "}(real audio credit from the video's own metadata)
                          </div>
                        )}
                        <div className="idea-card-research-note">{v.observed}</div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {research.text_findings?.length > 0 && (
                <div className="idea-card-research-section">
                  <div className="idea-card-section-label">Sourced from articles</div>
                  <ul className="idea-card-research-list">
                    {research.text_findings.map((f, i) => (
                      <li key={i}>
                        {f.finding}
                        {f.source && (
                          <>
                            {" "}—{" "}
                            <a href={f.source} target="_blank" rel="noreferrer">source</a>
                          </>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {research.marketing_findings?.length > 0 && (
                <div className="idea-card-research-section">
                  <div className="idea-card-section-label">
                    Audience-targeting / social strategy findings
                  </div>
                  <ul className="idea-card-research-list">
                    {research.marketing_findings.map((f, i) => (
                      <li key={i}>
                        {f.finding}
                        {f.source && (
                          <>
                            {" "}—{" "}
                            <a href={f.source} target="_blank" rel="noreferrer">source</a>
                          </>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {research.strategy_video_findings?.filter((f) => f.relevant).length > 0 && (
                <div className="idea-card-research-section">
                  <div className="idea-card-section-label">
                    From real strategy videos (real transcripts, not summaries)
                  </div>
                  <ul className="idea-card-research-list">
                    {research.strategy_video_findings.filter((f) => f.relevant).map((f, i) => (
                      <li key={i}>
                        <a href={f.url} target="_blank" rel="noreferrer">{f.url}</a>
                        <ul className="idea-card-research-list">
                          {(f.points || []).map((p, j) => (
                            <li key={j} className="idea-card-research-note">{p}</li>
                          ))}
                        </ul>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {research.video_findings?.some((v) => !v.relevant) && (
                <div className="idea-card-research-section">
                  <div className="idea-card-section-label">
                    Checked, but not actually relevant (excluded from reasoning)
                  </div>
                  <ul className="idea-card-research-list idea-card-research-list-muted">
                    {research.video_findings.filter((v) => !v.relevant).map((v, i) => (
                      <li key={i}>
                        <a href={v.url} target="_blank" rel="noreferrer">{v.url}</a>
                        <div className="idea-card-research-note">{v.observed}</div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {research.unverified?.length > 0 && (
                <div className="idea-card-research-section">
                  <div className="idea-card-section-label">Unverified / came up empty</div>
                  <ul className="idea-card-research-list idea-card-research-list-muted">
                    {research.unverified.map((u, i) => <li key={i}>{u}</li>)}
                  </ul>
                </div>
              )}
              {research.omitted_reasoning && (
                <div className="idea-card-research-section">
                  <div className="idea-card-section-label">Real material left out</div>
                  <p className="idea-card-research-note">{research.omitted_reasoning}</p>
                </div>
              )}
            </div>
          )
        )}
      </div>

      {useRanges && (
        <div className="idea-card-phrases">
          <div className="idea-card-section-label">Source ranges</div>
          <ol className="idea-card-phrases-list">
            {visibleRanges.map((r, i) => {
              const dur = Math.max(0, (r.source_end_sec || 0) - (r.source_start_sec || 0));
              return (
                <li key={i} className="idea-card-phrase-item">
                  <span className="phrase-index">{i + 1}.</span>
                  <span className="phrase-text">
                    <strong>{formatTime(r.source_start_sec)}–{formatTime(r.source_end_sec)}</strong>
                    {" "}<span className="range-duration">({Math.round(dur)}s)</span>
                    {r.topic_label && <span className="range-label"> · {r.topic_label}</span>}
                    {r.summary && <div className="range-summary">{r.summary}</div>}
                  </span>
                </li>
              );
            })}
          </ol>
          {sourceRanges.length > 3 && (
            <button
              className="btn btn-link btn-sm"
              onClick={() => setShowAllItems((v) => !v)}
            >
              {showAllItems ? "Show fewer" : `Show all ${sourceRanges.length} ranges`}
            </button>
          )}
        </div>
      )}

      {!useRanges && previews.length > 0 && (
        <div className="idea-card-phrases">
          <div className="idea-card-section-label">Phrases included (source order)</div>
          <ol className="idea-card-phrases-list">
            {visiblePreviews.map((text, i) => (
              <li key={i} className="idea-card-phrase-item">
                <span className="phrase-index">{i + 1}.</span>
                <span className="phrase-text">{text}</span>
              </li>
            ))}
          </ol>
          {previews.length > 4 && (
            <button
              className="btn btn-link btn-sm"
              onClick={() => setShowAllItems((v) => !v)}
            >
              {showAllItems
                ? "Show fewer"
                : `Show all ${previews.length} phrases`}
            </button>
          )}
        </div>
      )}

      <div className="idea-card-format">
        <label className="idea-card-format-label">
          Platform overlay
          <select
            className="idea-card-format-select"
            value={currentPlatform}
            onChange={handlePlatformChange}
            disabled={disabled}
          >
            {PLATFORM_OPTIONS.map((p) => (
              <option key={p.key || "__none__"} value={p.key}>{p.label}</option>
            ))}
          </select>
        </label>

        <label className="idea-card-format-label" style={{ marginTop: 8 }}>
          Aspect ratio
          <select
            className="idea-card-format-select"
            value={currentAspect}
            onChange={handleAspectChange}
            disabled={disabled}
          >
            {availableAspects.map((a) => (
              <option key={a.key || "__none__"} value={a.key}>{a.label}</option>
            ))}
          </select>
          {allowedAspects && allowedAspects.length === 1 && (
            <span className="idea-card-format-hint">
              This platform requires {availableAspects.find((a) => a.key === allowedAspects[0])?.label}.
            </span>
          )}
          {!currentPlatform && !currentAspect && (
            <span className="idea-card-format-hint">
              Sequence will match the A-roll's native resolution. No overlay.
            </span>
          )}
        </label>
      </div>

      <div className="idea-card-actions">
        <div className="action-spacer" />
        <button
          className="btn btn-ghost"
          onClick={onDiscard}
          disabled={disabled}
          title="Delete this angle"
        >
          Discard
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Refine modal
// ---------------------------------------------------------------------------

function RefineModal({ idea, onClose, onSubmitted }) {
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (!idea) return null;

  const handleSubmit = async () => {
    if (!notes.trim()) return;
    setSubmitting(true);
    try {
      await sendCommand({
        type: "refine_idea",
        idea_id: idea.idea_id,
        notes: notes.trim(),
        job_id: `refine-${Date.now()}`,
      });
      onSubmitted();
    } catch (e) {
      console.error("refine failed:", e);
      setSubmitting(false);
    }
  };

  const handleKey = (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSubmit();
    }
    if (e.key === "Escape") onClose();
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Refine this idea</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>
        <div className="modal-body">
          <div className="modal-idea-preview">
            <div className="modal-idea-title">{idea.data?.concept || "Untitled"}</div>
            <div className="modal-idea-pitch">{idea.data?.pitch || ""}</div>
          </div>
          <label className="form-label">Your feedback</label>
          <textarea
            autoFocus
            className="form-textarea"
            placeholder="Make it less sentimental. Add more B-roll of the kitchen. Cut the part about her childhood."
            rows={6}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            onKeyDown={handleKey}
          />
          <div className="form-hint">
            The AI producer will rewrite the idea using your notes and the
            original transcript. Press <kbd>⌘</kbd><kbd>↵</kbd> to submit.
          </div>
        </div>
        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button
            className="btn btn-primary"
            onClick={handleSubmit}
            disabled={!notes.trim() || submitting}
          >
            {submitting ? "Refining…" : "Submit"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Brief form — directed mode
// ---------------------------------------------------------------------------

const PRESETS = [
  { key: "reel_15s", label: "15s Reel (vertical)" },
  { key: "reel_30s", label: "30s Reel (vertical)" },
  { key: "tiktok_60s", label: "60s TikTok" },
  { key: "ad_15s", label: "15s Ad" },
  { key: "ad_30s", label: "30s Ad" },
  { key: "ad_60s", label: "60s Ad" },
  { key: "ad_120s", label: "2min Ad" },
  { key: "youtube_highlight", label: "3-5min YouTube cut" },
  { key: "youtube_episode", label: "10min YouTube episode" },
  { key: "talking_head_full", label: "Talking-head (full length)" },
];

function BriefForm({ onClose }) {
  const [preset, setPreset] = useState("reel_30s");
  const [brief, setBrief] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    if (!brief.trim()) return;
    setSubmitting(true);
    try {
      await sendCommand({
        type: "plan_directed",
        preset_key: preset,
        brief: brief.trim(),
        job_id: `plan-${Date.now()}`,
      });
      onClose();
    } catch (e) {
      console.error("plan_directed failed:", e);
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Create custom brief</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>
        <div className="modal-body">
          <label className="form-label">Format</label>
          <select
            className="form-input"
            value={preset}
            onChange={(e) => setPreset(e.target.value)}
          >
            {PRESETS.map((p) => (
              <option key={p.key} value={p.key}>{p.label}</option>
            ))}
          </select>

          <label className="form-label" style={{ marginTop: 16 }}>Brief</label>
          <textarea
            autoFocus
            className="form-textarea"
            placeholder="Punchy moment about the founding story — hook in first 2 seconds, reveal the unexpected detail, close with her smiling about what she learned."
            rows={5}
            value={brief}
            onChange={(e) => setBrief(e.target.value)}
          />
        </div>
        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button
            className="btn btn-primary"
            onClick={handleSubmit}
            disabled={!brief.trim() || submitting}
          >
            {submitting ? "Generating…" : "Generate plan"}
          </button>
        </div>
      </div>
    </div>
  );
}
