import { useEffect, useState, useCallback, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import precutLogo from "../assets/precut-logo.png";

/**
 * SetupScreen
 * -----------
 * First-launch dependency installer. Shown when any of the required
 * components (Xcode CLT, Homebrew, ffmpeg, Python 3.10+, pip packages)
 * is missing, or when setup_complete hasn't been marked in settings.
 *
 * The screen runs a check via the Rust `setup_check` command, renders
 * a row per component with state + install button, and streams install
 * progress from `backend-event` when the user clicks an install button.
 *
 * Visual language mirrors StartScreen: dark panel, accent cyan, mono
 * font for paths/versions.
 *
 * State machine per component
 * ---------------------------
 *   unknown  → (check runs) → ok | missing
 *   missing  → (install clicked) → installing → ok | error
 *   error    → (install clicked) → installing → ok | error
 *
 * "ok" means the dependency is present. The screen doesn't force a
 * strict install order on the user, but it grays out buttons whose
 * prerequisites aren't met (e.g. ffmpeg needs Homebrew first).
 */

// Rendered order — also defines dependency prerequisites for greying out
// the install button when a preceding component is missing.
const COMPONENTS = [
  {
    key: "xcode_clt",
    label: "Xcode Command Line Tools",
    subtitle: "Required for compilation. Triggers the macOS installer dialog.",
    prereqs: [],
  },
  {
    key: "homebrew",
    label: "Homebrew",
    subtitle: "Package manager. Needed to install ffmpeg and Python cleanly.",
    prereqs: ["xcode_clt"],
    note: "Opens a Terminal window — you'll enter your Mac password there.",
  },
  {
    key: "ffmpeg",
    label: "FFmpeg",
    subtitle: "Video decoding backbone. Used for frame extraction & exports.",
    prereqs: ["homebrew"],
  },
  {
    key: "python",
    label: "Python 3.12",
    subtitle: "Runtime for the indexing & producer backend. Homebrew-installed.",
    prereqs: ["homebrew"],
  },
  {
    key: "python_packages",
    label: "Python packages",
    subtitle: "torch, whisper, open-clip, lancedb, anthropic, and friends. ~500MB download.",
    prereqs: ["python"],
    note: "Longest step — expect 5-10 minutes. pip progress appears in the log below.",
  },
  {
    key: "premiere_extension",
    label: "Premiere Pro Extension",
    subtitle: "Runs invisibly in Premiere and auto-applies Interpret Footage to B-roll frame-rate conforms — no manual steps in Premiere, ever.",
    prereqs: [],
    note: "Optional if Premiere isn't installed on this Mac. Fully quit and reopen Premiere once after installing.",
  },
];

export default function SetupScreen({ onSetupComplete }) {
  const [report, setReport] = useState(null);        // full check report from backend
  const [checking, setChecking] = useState(true);
  const [installing, setInstalling] = useState(null); // component key currently installing
  const [progressLog, setProgressLog] = useState([]); // streaming install messages
  const [error, setError] = useState(null);
  const logRef = useRef(null);

  // Auto-scroll the progress log to bottom as new lines arrive
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [progressLog]);

  // Run the dependency check via Rust → setup_helper.py check
  const runCheck = useCallback(async () => {
    setChecking(true);
    setError(null);
    try {
      const raw = await invoke("setup_check");
      const parsed = JSON.parse(raw);
      setReport(parsed);
    } catch (e) {
      setError(String(e));
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => { runCheck(); }, [runCheck]);

  // Listen to backend events for install progress. We use the window
  // event bus directly rather than going through App.jsx's subscribe()
  // because the setup screen runs before App's backend event pipeline
  // is meaningful (main backend isn't started yet).
  useEffect(() => {
    let unlisten;
    (async () => {
      const { listen } = await import("@tauri-apps/api/event");
      unlisten = await listen("backend-event", (tauriEvent) => {
        let ev;
        try { ev = JSON.parse(tauriEvent.payload); } catch { return; }

        if (ev.type === "install_progress") {
          setProgressLog((prev) => {
            const next = [
              ...prev,
              { component: ev.component, stage: ev.stage, message: ev.message, pct: ev.pct },
            ];
            return next.length > 500 ? next.slice(-500) : next;
          });
        } else if (ev.type === "install_done") {
          // Rerun the check — this updates the component's state and
          // enables dependent components.
          setInstalling(null);
          setProgressLog((prev) => [
            ...prev,
            { component: ev.component, stage: "done", message: "✓ complete" },
          ]);
          runCheck();
        } else if (ev.type === "install_error") {
          setInstalling(null);
          setProgressLog((prev) => [
            ...prev,
            { component: ev.component, stage: "error", message: `✗ ${ev.message}` },
          ]);
          runCheck();
        }
      });
    })();
    return () => { if (unlisten) unlisten(); };
  }, [runCheck]);

  // User clicks "Install" on a component row
  const startInstall = useCallback(async (componentKey) => {
    setInstalling(componentKey);
    setProgressLog((prev) => [
      ...prev,
      { component: componentKey, stage: "start", message: `--- Installing ${componentKey} ---` },
    ]);
    try {
      await invoke("setup_install", { component: componentKey });
    } catch (e) {
      setInstalling(null);
      setError(String(e));
    }
  }, []);

  // "Finish" button — mark setup complete and hand off to StartScreen
  const finish = useCallback(async () => {
    try {
      await invoke("setup_mark_complete");
      onSetupComplete();
    } catch (e) {
      setError(String(e));
    }
  }, [onSetupComplete]);

  // "Skip for now" — don't mark complete, but still hand off. Lets
  // users with already-installed deps that we couldn't detect continue
  // without pressure. Main backend will surface its own errors if a
  // true dep is missing.
  const skip = useCallback(() => {
    onSetupComplete();
  }, [onSetupComplete]);

  const componentState = useCallback((key) => {
    if (!report) return "unknown";
    const c = report.components?.[key];
    if (!c) return "unknown";
    return c.present ? "ok" : "missing";
  }, [report]);

  const prereqsMet = useCallback((key) => {
    const def = COMPONENTS.find((c) => c.key === key);
    if (!def || def.prereqs.length === 0) return true;
    return def.prereqs.every((p) => componentState(p) === "ok");
  }, [componentState]);

  const allReady = report?.all_ready === true;

  return (
    <div className="setup-screen">
      <div className="setup-scroll">
        <header className="setup-header">
          <img src={precutLogo} alt="PreCut" className="setup-logo" />
          <h1 className="setup-title">First-time setup</h1>
          <p className="setup-subtitle">
            PreCut uses a local Python backend for indexing and transcription.
            This screen installs its dependencies — you only need to do this once.
          </p>
        </header>

        {error && (
          <div className="setup-error">
            <strong>Error:</strong> {error}
          </div>
        )}

        {checking && !report ? (
          <div className="setup-loading">Checking dependencies…</div>
        ) : (
          <div className="setup-components">
            {COMPONENTS.map((def) => {
              const state = componentState(def.key);
              const isInstalling = installing === def.key;
              const prereqsOk = prereqsMet(def.key);
              const detail = report?.components?.[def.key]?.detail || "";

              return (
                <div
                  key={def.key}
                  className={`setup-component setup-component-${state}${isInstalling ? " installing" : ""}`}
                >
                  <div className="setup-component-icon">
                    {state === "ok" ? "✓"
                      : isInstalling ? <Spinner />
                      : state === "missing" ? "○"
                      : "·"}
                  </div>
                  <div className="setup-component-body">
                    <div className="setup-component-title">{def.label}</div>
                    <div className="setup-component-subtitle">{def.subtitle}</div>
                    {detail && (
                      <div className="setup-component-detail">{detail}</div>
                    )}
                    {def.note && state !== "ok" && (
                      <div className="setup-component-note">{def.note}</div>
                    )}
                  </div>
                  <div className="setup-component-action">
                    {state === "ok" ? (
                      <span className="setup-component-tag ok">installed</span>
                    ) : isInstalling ? (
                      <span className="setup-component-tag installing">installing…</span>
                    ) : (
                      <button
                        className="setup-install-btn"
                        disabled={!prereqsOk || installing !== null}
                        onClick={() => startInstall(def.key)}
                        title={!prereqsOk ? `Install ${def.prereqs.join(", ")} first` : "Install"}
                      >
                        Install
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {progressLog.length > 0 && (
          <div className="setup-log-panel">
            <div className="setup-log-header">Install log</div>
            <pre ref={logRef} className="setup-log">
              {progressLog.map((entry, i) => {
                const lineClass = entry.stage === "error" ? "err"
                  : entry.stage === "done" ? "ok"
                  : entry.stage === "start" ? "accent"
                  : "";
                return (
                  <div key={i} className={`setup-log-line ${lineClass}`}>
                    {entry.message}
                  </div>
                );
              })}
            </pre>
          </div>
        )}

        <div className="setup-actions">
          <button
            className="setup-btn-secondary"
            onClick={runCheck}
            disabled={checking || installing !== null}
          >
            {checking ? "Checking…" : "Re-check"}
          </button>
          <div className="setup-actions-right">
            <button
              className="setup-btn-skip"
              onClick={skip}
              disabled={installing !== null}
              title="Don't block me — I'll fix any missing pieces myself."
            >
              Skip for now
            </button>
            <button
              className="setup-btn-primary"
              onClick={finish}
              disabled={!allReady || installing !== null}
            >
              {allReady ? "Finish setup →" : "Waiting for deps…"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Spinner() {
  return <span className="setup-spinner" />;
}
