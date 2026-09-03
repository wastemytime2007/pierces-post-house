import { useEffect, useState, useCallback, useRef } from "react";
import { sendCommand } from "../App.jsx";
import PMTab from "./tabs/PMTab.jsx";
import IdeasTab from "./tabs/IdeasTab.jsx";
import LogView from "../components/LogView.jsx";

/**
 * ProjectView is the main workspace once a project is loaded.
 *
 * Tabs:
 *   0. Project — the whole first stage: declare the project and its
 *      footage, Organize (writes the manifest, auto-starts processing),
 *      pipeline progress, audio-sync review, transcripts. Used to be
 *      three tabs (Project Manager / Ingest / Assistant Editor); Ryan
 *      merged them 2026-09-03 after finding Ingest asked the same
 *      footage-declaration questions PMTab already had, and that the
 *      Assistant Editor tab's sync review wasn't distinct AE work at
 *      all — see PMTab.jsx's own docstring for the full account.
 *   1. Ideas — AI producer analyze / refine / pick
 *
 * The tabs are rendered side-by-side with a persistent activity log
 * on the right. The tabs all share the same subscriber bus from App.
 */
export default function ProjectView({
  project, onClose, onDelete, subscribe, log, onClearLog,
  settings, onOpenApiKeyHelp,
  // Drop 4.47: first-export nudge for the Default Includes feature.
  shouldShowAutoIncludeNudge,
  onMarkAutoIncludeNudgeSeen,
  onOpenAutoIncludeModal,
  // Drop 4.47.3: live rule count, forwarded to IdeasTab so it can pass
  // it on to ExportModal for the per-export apply/skip toggle.
  autoIncludeRulesCount,
  // Audience/content-goal profiles (2026-09-03) -- forwarded to PMTab's
  // intake dropdown, authored app-wide via the titlebar's "audiences &
  // goals" button, not per-project.
  audienceProfiles,
}) {
  const [activeTab, setActiveTab] = useState("pm");
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  // Drop 3.6: log hidden by default. User can reveal via side tab.
  const [showLog, setShowLog] = useState(false);

  // Pipeline job tracking — lives here so it persists across tab switches.
  // Keyed by job_id. Each entry tracks per-stage progress.
  const [jobs, setJobs] = useState({});
  const [ideas, setIdeas] = useState([]);

  // Subscribe to pipeline/producer events
  useEffect(() => {
    return subscribe((ev) => {
      // --- Pipeline stage events ---
      if (ev.type === "pipeline_started") {
        setJobs((prev) => ({
          ...prev,
          [ev.job_id]: {
            kind: "pipeline",
            status: "running",
            stages: {},
            started_at: Date.now(),
          },
        }));
      } else if (ev.type === "stage_started") {
        setJobs((prev) => updateStage(prev, ev.job_id, ev.stage, {
          status: "running",
          total: ev.total || 0,
          completed: 0,
          files: [],
        }));
      } else if (ev.type === "file_done") {
        setJobs((prev) => updateStage(prev, ev.job_id, ev.stage, (stage) => ({
          completed: ev.completed ?? stage.completed + 1,
          total: ev.total ?? stage.total,
          current_file: ev.file,
          last_status: ev.status,
          files: [...(stage.files || []), {
            name: ev.file, status: ev.status,
            elapsed_sec: ev.elapsed_sec, error: ev.error,
          }].slice(-20),  // cap per-stage history
        })));
      } else if (ev.type === "stage_complete") {
        setJobs((prev) => updateStage(prev, ev.job_id, ev.stage, {
          status: "done",
          success: ev.success,
          failed: ev.failed,
          skipped: ev.skipped,
        }));
      } else if (ev.type === "stage_error") {
        setJobs((prev) => updateStage(prev, ev.job_id, ev.stage, {
          status: "failed",
          error: ev.message,
        }));
      } else if (ev.type === "audio_sync_pair") {
        // Drop 3.6: one pair's sync result. We accumulate them per-job so
        // the matrix view in Ingest can render as they come in.
        setJobs((prev) => updateStage(prev, ev.job_id, "audio_sync", (stage) => ({
          completed: ev.i ?? (stage.completed || 0) + 1,
          total: ev.total ?? stage.total ?? 0,
          pairs: [...(stage.pairs || []), {
            aroll: ev.aroll, audio: ev.audio,
            score: ev.score, offset: ev.offset,
            reliable: ev.reliable, error: ev.error,
          }],
        })));
      } else if (ev.type === "audio_sync_cached") {
        setJobs((prev) => updateStage(prev, ev.job_id, "audio_sync", {
          status: "cached",
          pair_count: ev.pair_count,
        }));
      } else if (ev.type === "pipeline_complete") {
        setJobs((prev) => ({
          ...prev,
          [ev.job_id]: prev[ev.job_id]
            ? { ...prev[ev.job_id], status: ev.cancelled ? "cancelled" : "done" }
            : prev[ev.job_id],
        }));
      }
      // --- Producer job lifecycle (Drop 4.44) ---
      //
      // IdeasTab watches `jobs` to decide whether a producer run is in
      // progress (it drives the <GeneratingPanel> spinner). Without
      // these three handlers, producer jobs never appear in the jobs
      // map, so the UI thinks the producer is idle for the entire
      // 20-60s run — which is what users see as "nothing is happening."
      //
      // IdeasTab identifies producer jobs by id prefix (analyze-/plan-/
      // refine-/angles-), so we just need to seed entries with matching
      // ids and keep their status in sync with backend events.
      else if (ev.type === "producer_started") {
        setJobs((prev) => ({
          ...prev,
          [ev.job_id]: {
            kind: "producer",
            mode: ev.mode,
            status: "running",
            started_at: Date.now(),
          },
        }));
      } else if (ev.type === "producer_done") {
        setJobs((prev) => prev[ev.job_id]
          ? { ...prev, [ev.job_id]: { ...prev[ev.job_id], status: "done" } }
          : prev);
      } else if (ev.type === "producer_error") {
        setJobs((prev) => prev[ev.job_id]
          ? { ...prev, [ev.job_id]: { ...prev[ev.job_id], status: "failed", error: ev.message } }
          : prev);
      }
      // --- Producer idea/angle content events ---
      else if (ev.type === "producer_idea") {
        setIdeas((prev) => [
          { idea_id: ev.idea_id, kind: ev.kind, data: ev.concept || ev.deliverable,
            created_at: Date.now() / 1000, refinement_count: 0 },
          ...prev,
        ]);
      } else if (ev.type === "producer_angle") {
        setIdeas((prev) => [
          { idea_id: ev.idea_id, kind: "story_angle",
            data: ev.angle,
            selected_preset_key: ev.selected_preset_key,
            selected_platform_key: ev.selected_platform_key || "",
            selected_aspect_key: ev.selected_aspect_key || "",
            created_at: Date.now() / 1000, refinement_count: 0 },
          ...prev,
        ]);
      } else if (ev.type === "angle_preset_set") {
        setIdeas((prev) => prev.map((i) =>
          i.idea_id === ev.idea_id
            ? { ...i, selected_preset_key: ev.preset_key }
            : i
        ));
      } else if (ev.type === "angle_platform_set") {
        // Drop 4.4: two-field selection. Platform and aspect live at the
        // envelope level (not inside data). Update both on the idea.
        setIdeas((prev) => prev.map((i) =>
          i.idea_id === ev.idea_id
            ? {
                ...i,
                selected_platform_key: ev.platform_key || "",
                selected_aspect_key: ev.aspect_key || "",
                // Keep legacy field mirrored for Drop 4.3 code paths
                selected_preset_key: ev.aspect_key || i.selected_preset_key || "",
              }
            : i
        ));
      } else if (ev.type === "producer_idea_refined") {
        setIdeas((prev) => prev.map((i) =>
          i.idea_id === ev.idea_id
            ? { ...i, data: ev.deliverable, kind: ev.kind,
                refinement_count: ev.refinement_count }
            : i
        ));
      } else if (ev.type === "ideas_list") {
        setIdeas(ev.ideas || []);
      } else if (ev.type === "idea_deleted") {
        setIdeas((prev) => prev.filter((i) => i.idea_id !== ev.idea_id));
      }
    });
  }, [subscribe]);

  // Fetch ideas list once on mount
  useEffect(() => {
    sendCommand({ type: "list_ideas" }).catch(() => {});
  }, []);

  const hasRunning = Object.values(jobs).some((j) => j.status === "running");

  // Count transcripts available → feeds the "Analyze" button enable state
  const transcriptCount = project.sources
    .filter((s) => s.kind === "aroll")
    .flatMap((s) => Object.values(s.files || {}))
    .filter((f) => f.transcript_status === "done").length;

  return (
    <div className="project-view">
      <div className="project-nav">
        <button className="project-nav-back" onClick={onClose}>
          ← Projects
        </button>
        <div className="project-nav-tabs">
          <Tab
            label="00 · Project"
            active={activeTab === "pm"}
            onClick={() => setActiveTab("pm")}
            sub={project.sources.length ? `${project.sources.length} source${project.sources.length !== 1 ? "s" : ""}` : "no sources yet"}
          />
          <Tab
            label="01 · Ideas"
            active={activeTab === "ideas"}
            onClick={() => setActiveTab("ideas")}
            sub={ideas.length ? `${ideas.length} cards` : "none"}
            disabled={transcriptCount === 0 && ideas.length === 0}
          />
        </div>
        <div className="project-nav-spacer" />
        {onDelete && (
          <button
            className="project-nav-delete-icon"
            title="Delete this project"
            onClick={() => setShowDeleteConfirm(true)}
            aria-label="Delete this project"
          >
            {/* Minimal trash icon, SVG keeps it crisp at any DPI */}
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <path
                d="M2.5 3.5h9m-7 0V2.5a1 1 0 011-1h3a1 1 0 011 1v1m-6 0v7a1 1 0 001 1h5a1 1 0 001-1v-7m-5 2v4m3-4v4"
                stroke="currentColor"
                strokeWidth="1.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        )}
      </div>

      <div className={`project-main ${showLog ? "log-visible" : "log-hidden"}`}>
        <section className="project-stage">
          {activeTab === "pm" && (
            <PMTab
              subscribe={subscribe}
              project={project}
              jobs={jobs}
              hasRunning={hasRunning}
              onGoToIdeas={() => setActiveTab("ideas")}
              audienceProfiles={audienceProfiles}
            />
          )}
          {activeTab === "ideas" && (
            <IdeasTab
              project={project}
              ideas={ideas}
              jobs={jobs}
              transcriptCount={transcriptCount}
              settings={settings}
              onOpenApiKeyHelp={onOpenApiKeyHelp}
              shouldShowAutoIncludeNudge={shouldShowAutoIncludeNudge}
              onMarkAutoIncludeNudgeSeen={onMarkAutoIncludeNudgeSeen}
              onOpenAutoIncludeModal={onOpenAutoIncludeModal}
              autoIncludeRulesCount={autoIncludeRulesCount}
            />
          )}
        </section>
        {showLog ? (
          <aside className="project-sidebar">
            <div className="log-toggle-bar">
              <button
                className="log-toggle"
                onClick={() => setShowLog(false)}
                title="Hide activity log"
                aria-label="Hide activity log"
              >
                Hide log ›
              </button>
            </div>
            <LogView entries={log} onClear={onClearLog} />
          </aside>
        ) : (
          <button
            className="log-reveal-tab"
            onClick={() => setShowLog(true)}
            title="Show activity log"
            aria-label="Show activity log"
          >
            ‹ Log
          </button>
        )}
      </div>
    </div>
  );
}

function Tab({ label, active, onClick, sub, disabled }) {
  return (
    <button
      className={`project-tab ${active ? "active" : ""} ${disabled ? "disabled" : ""}`}
      onClick={onClick}
      disabled={disabled}
    >
      <span className="project-tab-label">{label}</span>
      {sub && <span className="project-tab-sub">{sub}</span>}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Helper — update one stage of one job, immutably
// ---------------------------------------------------------------------------

function updateStage(jobs, jobId, stageName, updateOrFn) {
  const job = jobs[jobId];
  if (!job) return jobs;
  const prevStage = job.stages?.[stageName] || {};
  const update = typeof updateOrFn === "function" ? updateOrFn(prevStage) : updateOrFn;
  return {
    ...jobs,
    [jobId]: {
      ...job,
      stages: {
        ...(job.stages || {}),
        [stageName]: { ...prevStage, ...update },
      },
    },
  };
}
