import { useEffect, useState, useCallback, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

import StartScreen from "./screens/StartScreen.jsx";
import ProjectView from "./screens/ProjectView.jsx";
import SetupScreen from "./screens/SetupScreen.jsx";
import SettingsModal from "./components/SettingsModal.jsx";
import AutoIncludeModal from "./components/AutoIncludeModal.jsx";
import AudienceProfilesModal from "./components/AudienceProfilesModal.jsx";
import AutoIncludeHelp from "./components/AutoIncludeHelp.jsx";
import WelcomeModal from "./components/WelcomeModal.jsx";
import ApiKeyHelp from "./components/ApiKeyHelp.jsx";
import ToastStack from "./components/ToastStack.jsx";
import precutLogo from "./assets/precut-logo.png";

// ---------------------------------------------------------------------------
// IPC helpers
// ---------------------------------------------------------------------------

export async function sendCommand(cmd) {
  return invoke("send_to_backend", { commandJson: JSON.stringify(cmd) });
}

// ---------------------------------------------------------------------------
// Top-level app
// ---------------------------------------------------------------------------

export default function App() {
  const [backendReady, setBackendReady] = useState(false);
  const [project, setProject] = useState(null);
  const [log, setLog] = useState([]);
  const [settings, setSettings] = useState(null);     // { active_source, has_env, has_settings, key_suffix, workspace_id, welcome_seen, tour_seen, api_key_help_auto_shown }
  const [showSettings, setShowSettings] = useState(false);
  // Drop 4.46: auto-include rules — user-configured "always include
  // these files in every export" preferences. Loaded once at backend-
  // ready, refreshed on save events from the backend.
  const [showAutoInclude, setShowAutoInclude] = useState(false);
  const [showAutoIncludeHelp, setShowAutoIncludeHelp] = useState(false);
  const [autoIncludeRules, setAutoIncludeRules] = useState([]);
  const [audienceProfiles, setAudienceProfiles] = useState([]);
  const [showAudienceProfiles, setShowAudienceProfiles] = useState(false);
  const [toasts, setToasts] = useState([]);           // [{ id, level, message }]

  // Drop 4.44: onboarding — welcome modal + API key help panel.
  // The welcome modal appears on first launch after setup; we gate it
  // on settings.welcome_seen once settings load. The help panel is
  // independently shown either:
  //   - auto, the first time a user sees "No API key" state, OR
  //   - on demand when the user clicks the (?) button in settings, OR
  //   - from the welcome modal's "Don't know what this is?" link.
  const [showWelcome, setShowWelcome] = useState(false);
  const [showApiKeyHelp, setShowApiKeyHelp] = useState(false);

  // Drop 4.44: recovery for projects that fail to load (folder moved
  // or deleted). When the user clicks a project whose folder is gone,
  // the backend emits an "error" event with "Project ... not found".
  // We capture that, suppress the toast, and show a dialog with
  // "Locate folder" / "Remove from list" / "Cancel" options.
  //
  // The pendingOpenNameRef remembers which project the user clicked
  // most recently, so we can match the error event back to a name.
  // (The backend doesn't include the project name in the error event's
  // payload, only in the message text.)
  const [missingProject, setMissingProject] = useState(null);
  const pendingOpenNameRef = useRef(null);

  // Drop 4.44: first-launch setup gating.
  //   null      → haven't checked yet, show a loading splash
  //   "setup"   → dependencies missing AND setup_complete flag not set,
  //               render SetupScreen
  //   "ready"   → either all deps present or user explicitly finished/skipped
  //               setup; render normal app
  const [setupPhase, setSetupPhase] = useState(null);

  // Subscribers for backend events. Screens register handlers to react
  // to specific events. Ref avoids re-creating the listen() effect.
  const subscribersRef = useRef(new Set());
  const subscribe = useCallback((handler) => {
    subscribersRef.current.add(handler);
    return () => subscribersRef.current.delete(handler);
  }, []);

  const logAppend = useCallback((entry) => {
    setLog((prev) => {
      const ts = new Date().toLocaleTimeString("en-GB", { hour12: false });
      const next = [...prev, { ...entry, ts }];
      return next.length > 500 ? next.slice(-500) : next;
    });
  }, []);

  // Toast helper — brief, auto-dismissing notifications for user actions.
  const showToast = useCallback((message, level = "info", durationMs = 3500) => {
    const id = Math.random().toString(36).slice(2);
    setToasts((prev) => [...prev, { id, level, message }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, durationMs);
  }, []);

  // Route every backend event through this single handler. We log the
  // interesting ones to the activity panel, surface errors as toasts,
  // and fan out to screen-level subscribers for state management.
  useEffect(() => {
    let unlisten;
    (async () => {
      unlisten = await listen("backend-event", (tauriEvent) => {
        let ev;
        try {
          ev = JSON.parse(tauriEvent.payload);
        } catch (e) {
          console.error("Malformed backend event:", tauriEvent.payload);
          return;
        }

        switch (ev.type) {
          case "ready":
            setBackendReady(true);
            setSettings(ev.settings || null);
            logAppend({ level: "accent", message: `backend ready (v${ev.version})` });
            // Drop 4.46: pull saved auto-include rules so the modal renders
            // correctly the first time it's opened, and so users don't see
            // a flash of "no rules" if they have some.
            sendCommand({ type: "get_auto_include_rules" }).catch(() => {});
            sendCommand({ type: "get_audience_profiles" }).catch(() => {});
            break;

          case "log":
            logAppend({ level: ev.level || "info", message: ev.message });
            break;

          case "error": {
            const msg = ev.message || "Unknown error";
            // Drop 4.44: if this is a "Project ... not found" error
            // following a load_project command, show the recovery
            // dialog instead of a generic toast. Match on both the
            // new Drop 4.44 message shape ("Project 'foo' not found")
            // and the older one ("Project not found: foo") for
            // resilience across backend versions.
            const notFoundMatch =
              msg.match(/Project ['"]([^'"]+)['"] not found/i) ||
              msg.match(/Project not found:\s*(.+?)(?:\.|$)/i);
            if (notFoundMatch && pendingOpenNameRef.current) {
              const name = notFoundMatch[1] || pendingOpenNameRef.current;
              pendingOpenNameRef.current = null;
              setMissingProject({ name, detail: msg });
              logAppend({ level: "warn", message: msg });
              break; // don't also show the generic toast
            }
            logAppend({ level: "error", message: msg });
            showToast(msg, "error");
            break;
          }

          case "stderr":
            // Rust-classified: level in {error, warn, info}
            logAppend({ level: ev.level || "info", message: ev.message });
            break;

          // --- Producer events — show in log for visibility ---
          case "producer_started": {
            const label = ev.mode === "analyze" ? "Analyze"
                        : ev.mode === "directed" ? `Plan (${ev.preset_key || "?"})`
                        : ev.mode === "refine" ? "Refine"
                        : "Producer";
            logAppend({ level: "accent", message: `${label} started…` });
            showToast(`${label} started — this takes 20-60s`, "info", 4500);
            break;
          }
          case "producer_idea":
            logAppend({
              level: "info",
              message: `  idea: "${(ev.concept?.concept || ev.deliverable?.concept || "untitled").slice(0, 60)}"`,
            });
            break;
          case "producer_angle":
            logAppend({
              level: "info",
              message: `  angle: "${(ev.angle?.brief?.title || "untitled").slice(0, 60)}" · ${(ev.angle?.phrase_ids || []).length} phrases`,
            });
            break;
          case "producer_idea_refined":
            logAppend({ level: "info", message: `  idea refined (${ev.refinement_count}× total)` });
            showToast("Idea refined", "success");
            break;
          case "producer_done":
            logAppend({
              level: "accent",
              message: `  done (${ev.mode}${
                ev.concept_count ? `, ${ev.concept_count} concepts` :
                ev.angle_count ? `, ${ev.angle_count} angles` : ""
              })`,
            });
            if (ev.mode === "analyze" && ev.concept_count) {
              showToast(`Generated ${ev.concept_count} ideas`, "success");
            } else if (ev.mode === "story_angles" && ev.angle_count) {
              showToast(`Generated ${ev.angle_count} story angles`, "success");
            } else if (ev.mode === "directed") {
              showToast("Plan created", "success");
            }
            break;
          case "producer_error": {
            const full = ev.message || "AI producer failed";
            // Activity log gets the full error; toast gets a short summary.
            logAppend({ level: "error", message: `AI producer: ${full}` });
            const short = _summarizeProducerError(full);
            showToast(short, "error", 6000);
            break;
          }

          // --- Settings events ---
          case "settings":
            setSettings(ev.settings);
            break;
          case "api_key_saved":
            setSettings(ev.summary);
            logAppend({ level: "accent", message: `API key saved (…${ev.summary.key_suffix})` });
            showToast("API key saved", "success");
            break;

          // Drop 4.46: auto-include rules. Backend emits this on get/set;
          // we just sync the local list. Save success is implicit (no toast)
          // because rule edits are inline and saving on every blur would
          // make the toast stack noisy.
          case "auto_include_rules":
            setAutoIncludeRules(Array.isArray(ev.rules) ? ev.rules : []);
            break;

          case "audience_profiles":
            setAudienceProfiles(Array.isArray(ev.profiles) ? ev.profiles : []);
            break;

          // --- Project lifecycle ---
          case "project_created":
          case "project_loaded":
            pendingOpenNameRef.current = null;
            setProject(ev.project);
            break;
          case "project_state":
            setProject(ev.project);
            break;
          case "project_deleted":
            if (ev.ok) {
              showToast(`Project "${ev.name}" deleted`, "info");
              // If the deleted project was the currently-loaded one, close it
              setProject((curr) => (curr?.name === ev.name ? null : curr));
            }
            break;

          case "project_forgotten":
            // Drop 4.44: project was removed from the registry but
            // files weren't touched. StartScreen's list will refresh
            // via its own list_projects subscription.
            if (ev.ok) {
              showToast(`Removed "${ev.name}" from project list`, "info");
            }
            break;

          default:
            // Other events (pipeline_*, stage_*, file_done, source_added, etc.)
            // are handled by screen-level subscribers below.
            break;
        }

        // Fan out to screen-level subscribers (ProjectView, StartScreen, etc.)
        for (const handler of subscribersRef.current) {
          try { handler(ev); } catch (e) { console.error(e); }
        }
      });
    })();
    return () => { if (unlisten) unlisten(); };
  }, [logAppend, showToast]);

  // Also refresh project state after pipelines and stages complete, so
  // transcript_status / tag_status propagate to the UI.
  useEffect(() => {
    return subscribe((ev) => {
      if (ev.type === "pipeline_complete" || ev.type === "stage_complete") {
        sendCommand({ type: "get_project_state" }).catch(() => {});
      }
    });
  }, [subscribe]);

  // Drop 4.44: run the setup check on mount BEFORE pinging the backend.
  // If deps are missing and the user hasn't explicitly finished setup,
  // we render the SetupScreen and hold off on starting the Python
  // backend (which would fail on missing packages).
  useEffect(() => {
    (async () => {
      try {
        const raw = await invoke("setup_check");
        const report = JSON.parse(raw);
        // Show setup screen if: (a) deps missing, AND (b) user hasn't
        // already clicked Finish or Skip on a prior session.
        // Once the user marks complete, we trust them and skip this
        // screen on future launches, even if we think something's
        // still missing (they may have installed it some other way).
        if (!report.all_ready && !report.setup_complete_flag) {
          setSetupPhase("setup");
        } else {
          setSetupPhase("ready");
        }
      } catch (e) {
        // If the check itself fails (e.g. /usr/bin/python3 somehow
        // missing), fall through to the normal app. Users will see
        // the real backend error and can escalate.
        console.error("setup_check failed:", e);
        setSetupPhase("ready");
      }
    })();
  }, []);

  // Backend auto-spawn: send ping on mount so Rust spawns Python before
  // the user does anything. Also fetch settings once backend is ready.
  // GATED on setupPhase === "ready" — don't start Python before deps
  // are in place (it would crash on missing imports).
  useEffect(() => {
    if (setupPhase !== "ready") return;
    sendCommand({ type: "ping" }).catch((err) => {
      logAppend({ level: "error", message: `Backend unreachable: ${err}` });
    });
  }, [setupPhase, logAppend]);

  // Drop 4.44: onboarding auto-trigger.
  //
  // When settings first load AFTER the setup phase is ready, decide
  // whether to auto-open the welcome modal or the API-key help panel.
  //   1. If welcome_seen is false → show welcome modal. (Full onboarding
  //      path — the modal itself offers a link to api-key help, so we
  //      don't also auto-open that panel.)
  //   2. Otherwise, if user has no API key AND they haven't been
  //      auto-shown the help panel before → show the help panel.
  //
  // We use a ref to ensure this runs only once per app session. Without
  // it, any subsequent `settings` event (e.g. after the user saves a
  // key) would re-trigger the modal check.
  const onboardingCheckedRef = useRef(false);
  useEffect(() => {
    if (setupPhase !== "ready") return;
    if (!settings) return;
    if (onboardingCheckedRef.current) return;
    onboardingCheckedRef.current = true;

    if (!settings.welcome_seen) {
      setShowWelcome(true);
    } else if (
      settings.active_source === "none" &&
      !settings.api_key_help_auto_shown
    ) {
      // User already saw welcome (maybe in a prior session) but has
      // no key. Auto-open the help panel once to explain.
      setShowApiKeyHelp(true);
      // Mark it as auto-shown so we don't re-nag on future launches
      // even if the user dismisses without adding a key.
      sendCommand({
        type: "set_onboarding_flag",
        flag: "api_key_help_auto_shown",
        value: true,
      }).catch(() => { /* non-fatal — worst case user sees it again */ });
    }
  }, [setupPhase, settings]);

  // Handlers for the onboarding flow ------------------------------------------

  const markWelcomeSeen = useCallback(async () => {
    setShowWelcome(false);
    try {
      await sendCommand({
        type: "set_onboarding_flag",
        flag: "welcome_seen",
        value: true,
      });
    } catch (e) {
      // Non-fatal. If this fails, the welcome modal just re-appears
      // on next launch — annoying but not broken.
      console.error("Failed to mark welcome_seen:", e);
    }
  }, []);

  // Drop 4.47: mark the first-export Default Includes nudge as seen,
  // so it never re-appears. Called either when the user dismisses
  // ("Maybe later") or acts on it ("Set up now"). Optimistically
  // updates local settings so the IdeasTab guard re-evaluates without
  // waiting for the backend round-trip.
  const markAutoIncludeNudgeSeen = useCallback(async () => {
    setSettings((prev) =>
      prev ? { ...prev, auto_include_nudge_seen: true } : prev
    );
    try {
      await sendCommand({
        type: "set_onboarding_flag",
        flag: "auto_include_nudge_seen",
        value: true,
      });
    } catch (e) {
      console.error("Failed to mark auto_include_nudge_seen:", e);
    }
  }, []);

  // Drop 4.47: open the AutoIncludeModal (used by the nudge's "Set up
  // now" button so the screen-level handler doesn't need its own ref
  // to App-level state).
  const openAutoIncludeModal = useCallback(() => {
    setShowAutoInclude(true);
  }, []);

  const openApiKeyHelp = useCallback(() => {
    setShowWelcome(false); // if triggered from welcome, dismiss it
    setShowApiKeyHelp(true);
  }, []);

  const closeApiKeyHelp = useCallback(() => {
    setShowApiKeyHelp(false);
  }, []);

  const openProject = useCallback(async (name) => {
    // Remember which project we're opening so if the backend emits a
    // "Project not found" error, the error handler can match it back
    // to the name and show the recovery dialog. Cleared when
    // project_loaded fires or the dialog is dismissed.
    pendingOpenNameRef.current = name;
    try {
      await sendCommand({ type: "load_project", name });
    } catch (e) {
      pendingOpenNameRef.current = null;
      logAppend({ level: "error", message: `Failed to open project: ${e}` });
    }
  }, [logAppend]);

  const createProject = useCallback(async (name, rootDir = null) => {
    try {
      const cmd = { type: "create_project", name };
      if (rootDir) cmd.root_dir = rootDir;
      await sendCommand(cmd);
    } catch (e) {
      logAppend({ level: "error", message: `Failed to create project: ${e}` });
    }
  }, [logAppend]);

  const openProjectFromPath = useCallback(async (folderPath) => {
    try {
      await sendCommand({ type: "load_project_from_path", path: folderPath });
    } catch (e) {
      logAppend({ level: "error", message: `Failed to open project from folder: ${e}` });
    }
  }, [logAppend]);

  const deleteProject = useCallback(async (name) => {
    try {
      await sendCommand({ type: "delete_project", name });
    } catch (e) {
      logAppend({ level: "error", message: `Failed to delete project: ${e}` });
    }
  }, [logAppend]);

  const closeProject = useCallback(() => setProject(null), []);

  const saveApiKey = useCallback(async (key) => {
    await sendCommand({ type: "set_api_key", api_key: key });
  }, []);

  const saveWorkspaceId = useCallback(async (workspaceId) => {
    await sendCommand({ type: "set_workspace_id", workspace_id: workspaceId });
  }, []);

  // Persist a new list of audience/content-goal profiles. Backend emits
  // an `audience_profiles` event on success which our listener above
  // picks up to refresh the local copy.
  const saveAudienceProfiles = useCallback(async (profiles) => {
    await sendCommand({ type: "set_audience_profiles", profiles });
  }, []);

  // Drop 4.46: persist a new list of auto-include rules. Backend
  // emits an `auto_include_rules` event on success which our listener
  // picks up to refresh the local copy.
  const saveAutoIncludeRules = useCallback(async (rules) => {
    await sendCommand({ type: "set_auto_include_rules", rules });
  }, []);

  const apiKeyBadgeText = settings
    ? settings.active_source === "settings"
      ? `key …${settings.key_suffix} (saved)`
      : settings.active_source === "env"
      ? `key …${settings.key_suffix} (env)`
      : "no API key"
    : "";

  const apiKeyBadgeClass = settings && settings.active_source !== "none"
    ? "api-key-badge ok"
    : "api-key-badge missing";

  // Setup-phase gating ------------------------------------------------------
  // Full-screen splash while we're running the initial check (should take
  // about a second — blink quickly unless the user has a very slow disk).
  if (setupPhase === null) {
    return (
      <div className="setup-splash">
        <img src={precutLogo} alt="PreCut" className="setup-splash-logo" />
        <div className="setup-splash-spinner" />
        <div className="setup-splash-label">Checking dependencies…</div>
      </div>
    );
  }

  // First-launch setup screen. Skips past the normal titlebar/status bar
  // because the backend isn't running yet — showing "backend: not connected"
  // during the setup phase is confusing noise.
  if (setupPhase === "setup") {
    return (
      <SetupScreen onSetupComplete={() => setSetupPhase("ready")} />
    );
  }

  return (
    <div className="app">
      <header className="titlebar">
        <div className="titlebar-brand">
          <img
            src={precutLogo}
            alt="PreCut"
            className="titlebar-logo"
          />
          {project && (
            <>
              &nbsp;&nbsp;<span className="titlebar-sep">/</span>
              &nbsp;&nbsp;<span className="titlebar-project">{project.name}</span>
            </>
          )}
        </div>
        <div className="titlebar-status">
          {/* Drop 4.46: Default Includes button — opens the AutoIncludeModal
              where users configure files/folders to silently add to every
              export. Same titlebar slot as the API-key button so it's
              equally discoverable. */}
          <button
            className="api-key-badge"
            onClick={() => setShowAutoInclude(true)}
            title="Files to auto-include in every export"
            style={{ marginRight: 8 }}
          >
            default includes
            {autoIncludeRules.length > 0 ? ` · ${autoIncludeRules.length}` : ""}
          </button>
          {/* Audience & content-goal profiles, authored once here and
              picked from a dropdown at Project Manager intake (per Ryan,
              2026-09-03) rather than retyped as free text per project. */}
          <button
            className="api-key-badge"
            onClick={() => setShowAudienceProfiles(true)}
            title="Audiences & content goals for Project Manager intake"
            style={{ marginRight: 8 }}
          >
            audiences & goals
            {audienceProfiles.length > 0 ? ` · ${audienceProfiles.length}` : ""}
          </button>
          {settings && (
            <button
              className={apiKeyBadgeClass}
              onClick={() => setShowSettings(true)}
              title="Manage API key"
            >
              {apiKeyBadgeText}
            </button>
          )}
          <span className={`status-dot ${backendReady ? "live" : ""}`} />
          <span className="titlebar-status-text">
            {backendReady ? "backend connected" : "starting backend…"}
          </span>
        </div>
      </header>

      {!project ? (
        <StartScreen
          backendReady={backendReady}
          onOpen={openProject}
          onOpenFromPath={openProjectFromPath}
          onCreate={createProject}
          onDelete={deleteProject}
          subscribe={subscribe}
          log={log}
          /* Drop 4.44: onboarding tour on start-screen. We only start
             the tour if welcome has been seen AND tour hasn't, AND
             there's no pending modal competing for the user's attention. */
          tourReady={
            !!settings
            && settings.welcome_seen
            && !settings.tour_seen
            && !showWelcome
            && !showApiKeyHelp
          }
          onTourComplete={async () => {
            try {
              await sendCommand({
                type: "set_onboarding_flag",
                flag: "tour_seen",
                value: true,
              });
            } catch (e) {
              console.error("Failed to mark tour_seen:", e);
            }
          }}
        />
      ) : (
        <ProjectView
          project={project}
          onClose={closeProject}
          onDelete={deleteProject}
          subscribe={subscribe}
          log={log}
          onClearLog={() => setLog([])}
          settings={settings}
          onOpenApiKeyHelp={openApiKeyHelp}
          shouldShowAutoIncludeNudge={
            // Show the first-export nudge if:
            //  (1) we have settings loaded
            //  (2) the nudge hasn't been marked as seen yet
            //  (3) the user has no auto-include rules already (if they
            //      do, they discovered the feature on their own)
            !!settings
            && !settings.auto_include_nudge_seen
            && autoIncludeRules.length === 0
          }
          onMarkAutoIncludeNudgeSeen={markAutoIncludeNudgeSeen}
          onOpenAutoIncludeModal={openAutoIncludeModal}
          // Drop 4.47.3: live count for the per-export "Apply default
          // includes" toggle in ExportModal. Threaded down to IdeasTab
          // → ExportModal.
          autoIncludeRulesCount={autoIncludeRules.length}
          audienceProfiles={audienceProfiles}
        />
      )}

      {showSettings && (
        <SettingsModal
          settings={settings}
          onSave={saveApiKey}
          onSaveWorkspaceId={saveWorkspaceId}
          onClose={() => setShowSettings(false)}
          onOpenHelp={openApiKeyHelp}
        />
      )}

      {/* Drop 4.46: Default Includes modal */}
      {showAutoInclude && (
        <AutoIncludeModal
          rules={autoIncludeRules}
          onSave={saveAutoIncludeRules}
          onClose={() => setShowAutoInclude(false)}
          onOpenHelp={() => setShowAutoIncludeHelp(true)}
        />
      )}

      {/* Drop 4.46.2: walkthrough for the Default Includes feature.
          Opened from the (?) icon in the modal header or the "See the
          full walkthrough" link in the empty state. */}
      {showAutoIncludeHelp && (
        <AutoIncludeHelp onClose={() => setShowAutoIncludeHelp(false)} />
      )}

      {showAudienceProfiles && (
        <AudienceProfilesModal
          profiles={audienceProfiles}
          onSave={saveAudienceProfiles}
          onClose={() => setShowAudienceProfiles(false)}
        />
      )}

      {/* Drop 4.44: onboarding modals.
          - WelcomeModal shows first (on top of everything) on first launch.
          - ApiKeyHelp can be shown from the welcome modal's help link, from
            the settings modal's (?) button, or auto-opened if the user
            somehow got past welcome without setting a key. */}
      {showWelcome && (
        <WelcomeModal
          onComplete={markWelcomeSeen}
          onOpenApiKeyHelp={() => {
            // Keep welcome_seen update so they don't see it again,
            // then open the help panel.
            markWelcomeSeen();
            openApiKeyHelp();
          }}
        />
      )}

      {showApiKeyHelp && <ApiKeyHelp onClose={closeApiKeyHelp} />}

      {/* Drop 4.44: missing project recovery. Shown when a user clicks
          a project whose folder has been moved or deleted. Offers to
          locate the new folder (updating the registry) or forget the
          project (removing the stale list entry). */}
      {missingProject && (
        <div className="modal-overlay" onClick={() => setMissingProject(null)}>
          <div
            className="modal"
            style={{ maxWidth: 440 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <h2>Project folder missing</h2>
              <button
                className="modal-close"
                onClick={() => setMissingProject(null)}
                aria-label="Close"
              >×</button>
            </div>
            <div className="modal-body">
              <p style={{ marginTop: 0 }}>
                <strong>&ldquo;{missingProject.name}&rdquo;</strong> is in your
                project list, but its folder couldn&rsquo;t be found at the
                saved location. The folder may have been moved, renamed, or
                deleted in Finder.
              </p>
              <p style={{ color: "var(--fg-2)", fontSize: 12 }}>
                {missingProject.detail}
              </p>
            </div>
            <div className="modal-actions">
              <button
                className="btn btn-ghost"
                onClick={() => setMissingProject(null)}
              >
                Cancel
              </button>
              <button
                className="btn btn-ghost"
                onClick={async () => {
                  // "Remove from list" — unregister without touching files
                  const name = missingProject.name;
                  setMissingProject(null);
                  try {
                    await sendCommand({ type: "forget_project", name });
                    // Refresh the project list after the registry change
                    await sendCommand({ type: "list_projects" });
                  } catch (e) {
                    logAppend({ level: "error",
                                message: `Failed to forget project: ${e}` });
                  }
                }}
              >
                Remove from list
              </button>
              <button
                className="btn btn-primary"
                onClick={async () => {
                  // "Locate folder" — prompt for the new folder, then
                  // open-from-path so the registry gets updated via the
                  // existing load_project_from_path code path.
                  const name = missingProject.name;
                  setMissingProject(null);
                  try {
                    const selected = await openDialog({
                      directory: true,
                      multiple: false,
                      title: `Locate folder for ${name}`,
                    });
                    if (selected) {
                      await openProjectFromPath(selected);
                    }
                  } catch (e) {
                    logAppend({ level: "error",
                                message: `Failed to locate folder: ${e}` });
                  }
                }}
              >
                Locate folder…
              </button>
            </div>
          </div>
        </div>
      )}

      <ToastStack toasts={toasts} />
    </div>
  );
}

/**
 * Collapse long, structured producer errors into a short toast-friendly
 * message. The full error still lands in the activity log.
 */
function _summarizeProducerError(msg) {
  if (typeof msg !== "string") return "AI producer failed";

  // Malformed JSON — most common recoverable error. User should retry.
  if (/malformed JSON|Expecting[^:]*delimiter|JSONDecodeError/i.test(msg)) {
    return "The AI reply wasn't valid JSON — try refining again.";
  }
  // API auth failures
  if (/401|authentication_error|invalid.*api.*key/i.test(msg)) {
    return "API key rejected. Click the key badge to update it.";
  }
  // Rate limits
  if (/429|rate.limit/i.test(msg)) {
    return "Anthropic rate limit hit — wait a minute and retry.";
  }
  // Credit balance
  if (/credit.balance|insufficient/i.test(msg)) {
    return "Your Anthropic account is out of credits.";
  }
  // Network
  if (/timeout|ECONNREFUSED|network/i.test(msg)) {
    return "Network problem reaching Anthropic. Check connection.";
  }
  // Default: first line, truncated
  const firstLine = msg.split("\n")[0];
  return firstLine.length > 140 ? firstLine.slice(0, 137) + "…" : firstLine;
}
