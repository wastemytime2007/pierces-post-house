import { useEffect, useState, useCallback, useRef } from "react";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { sendCommand } from "../App.jsx";
import { useTour } from "../hooks/useTour.js";
import TourTooltip from "../components/TourTooltip.jsx";

/**
 * StartScreen shows:
 *   - The recent projects list, loaded from backend list_projects()
 *   - A "New project" button that opens a name prompt
 *   - A small activity strip at the bottom for debug visibility
 *
 * Project creation and opening are dispatched to App via callbacks.
 * This screen doesn't track open/create state itself — it just fires
 * the commands and expects project_created/project_loaded events to
 * cause App to unmount this screen and mount ProjectView.
 */
export default function StartScreen({ backendReady, onOpen, onOpenFromPath, onCreate, onDelete, subscribe, log, tourReady, onTourComplete }) {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showNewProject, setShowNewProject] = useState(false);
  const [newName, setNewName] = useState("");
  const [createError, setCreateError] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);  // project name pending delete
  // Drop 4.16: optional custom save location for new projects (portability)
  const [customRootDir, setCustomRootDir] = useState("");

  // Drop 4.44: onboarding tour — refs for each target element we
  // highlight, plus the tour state machine. Two steps only:
  //   1. newProject — point at the "+ New project" button
  //   2. openFromFolder — point at "Open from folder" for returning users
  const newProjectBtnRef = useRef(null);
  const openFromFolderBtnRef = useRef(null);
  const tour = useTour(["newProject", "openFromFolder"]);

  // Start the tour when the parent says it's time. We do it in an
  // effect rather than inline so the refs have settled. A small
  // setTimeout gives the page a beat to lay out before the tooltip
  // appears — without it, positioning math sometimes reads zero for
  // element dimensions because the layout hasn't happened yet.
  useEffect(() => {
    if (!tourReady) return;
    if (!tour.isDone && tour.stepIndex >= 0) return; // already running
    const t = setTimeout(() => tour.start(), 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tourReady]);

  // When the tour finishes (either by advancing past all steps or by
  // user skipping), tell the parent to mark tour_seen so we don't
  // show it again. `isDone` becomes true only once the user has
  // actually seen at least one step, so this won't fire spuriously
  // at mount before tourReady is true.
  const tourStartedRef = useRef(false);
  useEffect(() => {
    if (tour.stepIndex >= 0) tourStartedRef.current = true;
    if (tour.isDone && tourStartedRef.current && onTourComplete) {
      tourStartedRef.current = false; // prevent repeat calls
      onTourComplete();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tour.stepIndex, tour.isDone]);

  // Load project list once backend is ready, and refresh when projects change
  const refreshProjects = useCallback(async () => {
    if (!backendReady) return;
    try {
      await sendCommand({ type: "list_projects" });
    } catch (e) {
      console.error("list_projects failed", e);
      setLoading(false);
    }
  }, [backendReady]);

  useEffect(() => { refreshProjects(); }, [refreshProjects]);

  // Subscribe to relevant backend events
  useEffect(() => {
    return subscribe((ev) => {
      if (ev.type === "projects_list") {
        setProjects(ev.projects || []);
        setLoading(false);
      } else if (ev.type === "project_created") {
        // New project was created — App will switch us to ProjectView
        setShowNewProject(false);
        setNewName("");
        setCreateError(null);
      } else if (ev.type === "project_deleted") {
        // Refresh the list so the deleted project disappears
        refreshProjects();
        setDeleteTarget(null);
      } else if (ev.type === "error") {
        // Surface create/load errors locally if we're showing the new-project form
        if (showNewProject) {
          setCreateError(ev.message || "Something went wrong");
        }
      }
    });
  }, [subscribe, showNewProject]);

  const handleCreate = async () => {
    const trimmed = newName.trim();
    if (!trimmed) {
      setCreateError("Project name is required");
      return;
    }
    setCreateError(null);
    await onCreate(trimmed, customRootDir || null);
    // If backend rejects (duplicate name etc.), the 'error' event handler
    // above will populate createError. Otherwise we'll unmount.
  };

  // Drop 4.16: let user pick a save location for the new project
  const handlePickSaveLocation = async () => {
    try {
      const selected = await openDialog({
        directory: true,
        multiple: false,
        title: "Pick a folder to save this project",
      });
      if (typeof selected === "string" && selected) {
        // If the user picked a parent folder, append the project name
        // so we create <folder>/<project-name>/ rather than cluttering
        // the parent with our internal subdirs.
        const clean = newName.trim();
        if (clean && !selected.endsWith("/" + clean) && !selected.endsWith("\\" + clean)) {
          // Use forward-slash join — Tauri returns platform paths but
          // Python's Path() accepts both on macOS
          const sep = selected.includes("\\") ? "\\" : "/";
          setCustomRootDir(selected + sep + clean);
        } else {
          setCustomRootDir(selected);
        }
      }
    } catch (e) {
      console.error("Folder picker failed:", e);
    }
  };

  // Drop 4.16: open a project from any folder that contains project.json.
  // This is the portability flow — user moves a project folder to another
  // Mac and opens it from there.
  const handleOpenFromFolder = async () => {
    try {
      const selected = await openDialog({
        directory: true,
        multiple: false,
        title: "Pick the project folder (containing project.json)",
      });
      if (typeof selected === "string" && selected) {
        await onOpenFromPath(selected);
      }
    } catch (e) {
      console.error("Open-from-folder failed:", e);
    }
  };

  const handleKey = (e) => {
    if (e.key === "Enter") { e.preventDefault(); handleCreate(); }
    if (e.key === "Escape") { setShowNewProject(false); setCreateError(null); }
  };

  return (
    <div className="start-screen">
      <div className="start-panel">
        <div className="start-header">
          <h1 className="start-title">Projects</h1>
          <div className="start-header-actions">
            <button
              ref={openFromFolderBtnRef}
              className="btn btn-ghost"
              onClick={handleOpenFromFolder}
              disabled={!backendReady}
              title="Open an existing project from its folder — useful after moving a project between computers"
            >
              Open from folder…
            </button>
            <button
              ref={newProjectBtnRef}
              className="btn btn-primary"
              onClick={() => setShowNewProject(true)}
              disabled={!backendReady}
            >
              + New project
            </button>
          </div>
        </div>

        {showNewProject && (
          <div className="new-project-form">
            <label className="form-label">Project name</label>
            <input
              type="text"
              autoFocus
              className="form-input"
              placeholder="My first project"
              value={newName}
              onChange={(e) => { setNewName(e.target.value); setCreateError(null); }}
              onKeyDown={handleKey}
            />

            <label className="form-label" style={{ marginTop: 12 }}>
              Save location (optional)
            </label>
            <div className="form-row">
              <input
                type="text"
                className="form-input"
                placeholder="Leave blank to use the default location"
                value={customRootDir}
                readOnly
                onClick={handlePickSaveLocation}
                style={{ cursor: "pointer", flex: 1 }}
              />
              <button
                className="btn btn-ghost"
                onClick={handlePickSaveLocation}
                type="button"
              >
                Choose folder…
              </button>
              {customRootDir && (
                <button
                  className="btn btn-ghost"
                  onClick={() => setCustomRootDir("")}
                  type="button"
                  title="Reset to default location"
                >
                  Clear
                </button>
              )}
            </div>

            {createError && <div className="form-error">{createError}</div>}
            <div className="form-actions">
              <button className="btn btn-ghost" onClick={() => {
                setShowNewProject(false);
                setNewName("");
                setCustomRootDir("");
                setCreateError(null);
              }}>Cancel</button>
              <button
                className="btn btn-primary"
                onClick={handleCreate}
                disabled={!newName.trim()}
              >
                Create
              </button>
            </div>
            <div className="form-hint">
              {customRootDir ? (
                <>
                  <strong>Portable:</strong> all project data will live at
                  <br />
                  <code>{customRootDir}</code>
                  <br />
                  You can move this folder to another Mac and open it via
                  "Open from folder…". Source footage paths still need to
                  resolve on the destination machine.
                </>
              ) : (
                <>
                  Project metadata will be stored at
                  <br />
                  <code>~/Library/Application Support/PreCut/projects/{(newName || "…").trim()}/</code>
                </>
              )}
            </div>
          </div>
        )}

        <div className="project-list">
          {loading ? (
            <div className="empty-state">Loading projects…</div>
          ) : projects.length === 0 ? (
            <div className="empty-state">
              No projects yet. Create one to start.
            </div>
          ) : (
            projects.map((p) => (
              <div
                key={p.name}
                className="project-row"
                onClick={() => onOpen(p.name)}
              >
                <div className="project-row-main">
                  <div className="project-row-name">{p.name}</div>
                  <div className="project-row-meta">
                    {p.source_count} source{p.source_count !== 1 ? "s" : ""}
                    &nbsp;·&nbsp;
                    <span className="project-row-date">
                      updated {formatRelativeTime(p.updated_at)}
                    </span>
                  </div>
                </div>
                <button
                  className="project-row-delete"
                  title="Delete project"
                  onClick={(e) => {
                    e.stopPropagation();
                    setDeleteTarget(p.name);
                  }}
                >
                  ×
                </button>
                <div className="project-row-arrow">→</div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Small log strip at bottom so install-time errors are visible */}
      <div className="start-log">
        {log.slice(-3).map((entry, i) => (
          <div key={i} className={`start-log-entry ${entry.level || ""}`}>
            <span className="ts">{entry.ts}</span>
            <span className="msg">{entry.message}</span>
          </div>
        ))}
      </div>

      {deleteTarget && (
        <div className="modal-overlay" onClick={() => setDeleteTarget(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()} style={{ width: 440 }}>
            <div className="modal-header">
              <h2>Delete project?</h2>
              <button className="modal-close" onClick={() => setDeleteTarget(null)}>×</button>
            </div>
            <div className="modal-body">
              <div style={{ fontSize: 14, lineHeight: 1.5, color: "var(--fg-1)" }}>
                Delete <strong style={{ color: "var(--fg-0)" }}>{deleteTarget}</strong>?
              </div>
              <div className="form-hint" style={{ marginTop: 10 }}>
                This only removes project metadata (transcripts, B-roll index, plans).
                Your original footage and proxies are <strong>not</strong> touched.
              </div>
            </div>
            <div className="modal-actions">
              <button
                className="btn btn-ghost"
                onClick={() => setDeleteTarget(null)}
              >
                Cancel
              </button>
              <button
                className="btn btn-danger"
                onClick={() => onDelete(deleteTarget)}
              >
                Delete project
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Drop 4.44: tour tooltips. Each one points at a target ref
          above. They render absolutely-positioned via TourTooltip's
          own math, so order in the tree doesn't matter for layout. */}
      {tour.isActive("newProject") && (
        <TourTooltip
          targetRef={newProjectBtnRef}
          side="bottom"
          title="Start here"
          body="Click here to create your first project. You'll point it at a folder of footage, and PreCut will index everything for you."
          onDismiss={tour.next}
          onSkipAll={tour.skip}
          stepNumber={1}
          totalSteps={tour.totalSteps}
        />
      )}
      {tour.isActive("openFromFolder") && (
        <TourTooltip
          targetRef={openFromFolderBtnRef}
          side="bottom"
          title="Open existing projects"
          body="Projects save to disk, so you can re-open one any time — or pick up a project that's been moved between computers."
          onDismiss={tour.next}
          onSkipAll={tour.skip}
          stepNumber={2}
          totalSteps={tour.totalSteps}
        />
      )}
    </div>
  );
}

function formatRelativeTime(unixSeconds) {
  if (!unixSeconds) return "";
  const diff = Date.now() / 1000 - unixSeconds;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} hr ago`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} days ago`;
  const d = new Date(unixSeconds * 1000);
  return d.toLocaleDateString();
}
