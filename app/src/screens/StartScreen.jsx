import { useEffect, useState, useCallback, useRef } from "react";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { sendCommand } from "../App.jsx";

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
import Icon from "../components/Icon.jsx";
import Mark from "../components/Mark.jsx";
import { useThumb, requestPosters, tileColors, requestStrip, projectFootage, setCover } from "../thumbStore.js";

// The step a project is waiting at, so opening it lands there
function nextStep(p) {
  const s = p.summary || {};
  if (p.source_count === 0) return { tab: "pm", text: "Add your footage" };
  if (!s.transcribed) return { tab: "pm", text: "Process the footage" };
  if (!s.stories) return { tab: "ideas", text: "Find stories" };
  if (!s.exports) return { tab: "ideas", text: "Choose stories to export" };
  return { tab: "review", text: "Bring back your trimmed XML" };
}

// Where the project stands, in the words of the four steps
function stageText(p) {
  const s = p.summary || {};
  if (p.source_count === 0) return "Add your footage";
  if (!s.transcribed) return "Needs processing";
  if (!s.stories) return "Ready to find stories";
  if (!s.exports) return `${s.stories} ${s.stories === 1 ? "story" : "stories"} found`;
  return "XML out for trimming";
}

function greeting(person) {
  const h = new Date().getHours();
  const t = h < 5 ? "Working late" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  return person ? `${t}, ${person}.` : `${t}.`;
}

// A project is its own footage: one frame from its interviews as the cover, the name over a soft shade. No footage yet: a color tile from the brand's blues.
// Moving across a cover steps through five frames of the same interview, so the shoot shows itself without a video player (Apple TV and Frame.io do the same with a hover scrub).
function Cover({ p, featured, onOpen, onDelete, onContext, index }) {
  const poster = useThumb(`poster:${p.name}`);
  const [c1, c2] = tileColors(p.name);
  // The first time this project's own frame arrives, it fades in over the colour it started as
  const had = useRef(false);
  const [arrived, setArrived] = useState(false);
  useEffect(() => {
    if (poster && !had.current) { had.current = true; setArrived(true); const t = setTimeout(() => setArrived(false), 320); return () => clearTimeout(t); }
    return undefined;
  }, [poster]);
  const [frame, setFrame] = useState(-1);                                   // -1 is the poster
  const strip = [useThumb(`strip:${p.name}:0`), useThumb(`strip:${p.name}:1`), useThumb(`strip:${p.name}:2`), useThumb(`strip:${p.name}:3`), useThumb(`strip:${p.name}:4`)];
  const hoverArt = frame >= 0 ? strip[frame] : null;
  const art = hoverArt || poster;
  const onMove = (e) => {
    if (!projectFootage(p.name)) return;
    requestStrip(p.name);                                                   // cut on the first hover only, then cached
    const r = e.currentTarget.getBoundingClientRect();
    const i = Math.min(strip.length - 1, Math.max(0, Math.floor(((e.clientX - r.left) / r.width) * strip.length)));
    setFrame(i);
  };
  return (
    <div className={`cover ${featured ? "featured" : ""}`} style={{ "--i": index }}
      onMouseMove={onMove} onMouseLeave={() => setFrame(-1)}
      onContextMenu={(e) => { e.preventDefault(); onContext(e.clientX, e.clientY, p); }}>
      <button className="cover-open" onClick={() => { try { sessionStorage.setItem("ph.tab", nextStep(p).tab); } catch (e) { /* private window */ } onOpen(p.name); }}>
        <span className={`cover-art ${arrived ? "ph-arrived" : ""}`} style={art ? { backgroundImage: `url(${art})` } : { backgroundImage: `linear-gradient(135deg, ${c1}, ${c2})` }} />
        {frame >= 0 && strip.some(Boolean) && (
          <span className="cover-ticks" aria-hidden="true">
            {strip.map((_, i) => <span key={i} className={i === frame ? "on" : ""} />)}
          </span>
        )}
        <span className="cover-shade" />
        <span className="cover-info">
          <span className="cover-name">{p.name}</span>
          <span className="cover-meta">{stageText(p)} · {formatRelativeTime(p.updated_at)}</span>
        </span>
        <span className="cover-next">{nextStep(p).text} <Icon name="chevronRight" size={12} /></span>
      </button>
      <button className="cover-delete" title="Delete project" aria-label={`Delete ${p.name}`} onClick={() => onDelete(p.name)}><Icon name="trash" size={15} /></button>
    </div>
  );
}

// Which frame stands for a project. Six moments through its first interview; the chosen one is remembered per project and used as the cover everywhere (title bar, ambient glow).
function FramePicker({ name, onClose }) {
  const SECONDS = [8, 40, 90, 150, 220, 300];
  useEffect(() => { requestStrip(name, SECONDS); }, [name]);        // eslint-disable-line react-hooks/exhaustive-deps
  const frames = SECONDS.map((t, i) => useThumb(`strip:${name}:${i}`));   // eslint-disable-line react-hooks/rules-of-hooks
  const choose = (i) => {
    const uri = frames[i];
    if (!uri) return;
    try { localStorage.setItem(`ph.cover.${name}`, uri); } catch (e) { /* too big for storage: the default frame stays */ }
    setCover(name, uri);
    onClose();
  };
  return (
    <div className="modal-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal frame-pick" role="dialog" aria-label={`Cover frame for ${name}`}>
        <div className="modal-header"><h2>Choose the cover frame</h2><button className="modal-close" onClick={onClose}>×</button></div>
        <div className="frame-pick-grid">
          {frames.map((u, i) => (
            <button key={i} className="frame-pick-tile" onClick={() => choose(i)} disabled={!u} aria-label={`Frame at ${SECONDS[i]} seconds`}>
              {u ? <span style={{ backgroundImage: `url(${u})` }} /> : <span className="frame-pick-wait" />}
            </button>
          ))}
        </div>
        <div className="modal-actions"><button className="btn btn-ghost" onClick={onClose}>Cancel</button></div>
      </div>
    </div>
  );
}

export default function StartScreen({ backendReady, onOpen, onOpenFromPath, onCreate, onDelete, subscribe, log, person }) {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showNewProject, setShowNewProject] = useState(false);
  const [newName, setNewName] = useState("");
  const [createError, setCreateError] = useState(null);
  const [query, setQuery] = useState("");                     // a quick filter once there are many projects
  const [ctx, setCtx] = useState(null);                       // right-click menu on a project: { x, y, p }
  const [pickFrame, setPickFrame] = useState(null);           // a project whose cover frame is being chosen: its name
  const [deleteTarget, setDeleteTarget] = useState(null);  // project name pending delete
  // Drop 4.16: optional custom save location for new projects (portability)
  const [customRootDir, setCustomRootDir] = useState("");

  // Drop 4.44: onboarding tour — refs for each target element we
  // highlight, plus the tour state machine. Two steps only:
  //   1. newProject — point at the "+ New project" button
  //   2. openFromFolder — point at "Open from folder" for returning users
  const newProjectBtnRef = useRef(null);
  const openFromFolderBtnRef = useRef(null);


  // Start the tour when the parent says it's time. We do it in an
  // effect rather than inline so the refs have settled. A small
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
  useEffect(() => {
    const onNew = () => setShowNewProject(true);
    window.addEventListener("posthouse-new-project", onNew);
    return () => window.removeEventListener("posthouse-new-project", onNew);
  }, []);
  useEffect(() => { if (projects.length) requestPosters(projects); }, [projects]);

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
    <main className="start-screen">
      <div className="start-panel">
        <div className="start-hero">
          <div className="start-hero-text">
            <Mark size={34} />
            <h1 className="start-title">{greeting(person)}</h1>
            <p className="start-sub">{projects.length ? "Pick up where you left off, or start something new." : "Start a project, add your footage, and Post House finds the stories in it."}</p>
          </div>
          <div className="start-header-actions">
            <button ref={openFromFolderBtnRef} className="btn" onClick={handleOpenFromFolder} disabled={!backendReady}
              title="Open an existing project from its folder, for example after moving it to another computer">
              <Icon name="folderOpen" size={15} /> Open from folder…
            </button>
            <button ref={newProjectBtnRef} className="btn btn-primary" onClick={() => setShowNewProject(true)} disabled={!backendReady}>
              <Icon name="plus" size={15} /> New project
            </button>
          </div>
        </div>

        {showNewProject && (
          <div className="rv-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) { setShowNewProject(false); setCreateError(null); } }}>
          <div className="new-project-form rv-sheet" role="dialog" aria-modal="true" aria-labelledby="np-title">
            <h2 id="np-title">New project</h2>
            <p className="rv-sheet-sub">A project holds one shoot: its interviews, B-roll and recorder audio.</p>
            <label className="form-label">Name</label>
            <input
              type="text"
              autoFocus
              className="form-input"
              placeholder="For example: Runnells weekend"
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
                  <code>~/Library/Application Support/Post House/projects/{(newName || "…").trim()}/</code>
                </>
              )}
            </div>
          </div>
          </div>
        )}

        {loading ? (
          <div className="cover-grid" role="status" aria-busy="true" aria-label="Loading projects">
            {[0, 1, 2].map((i) => <div key={i} className="cover-skeleton" style={{ animationDelay: `${i * 120}ms` }} />)}
          </div>
        ) : projects.length === 0 ? (
          <div className="start-empty">
            <div className="start-empty-frames" aria-hidden="true"><span /><span /><span /></div>
            <h2>Nothing here yet.</h2>
            <p>A project holds one shoot: the interviews, the B-roll and the audio. Add the folders and Post House reads what was said.</p>
            <button className="btn btn-primary" onClick={() => setShowNewProject(true)} disabled={!backendReady}><Icon name="plus" size={15} /> New project</button>
          </div>
        ) : (
          <>
            {!query && <>
              <h2 className="section-head">Continue</h2>
              <Cover p={projects[0]} featured index={0} onOpen={onOpen} onDelete={setDeleteTarget} onContext={(x, y, p) => setCtx({ x, y, p })} />
            </>}
            {projects.length > 1 && (
              <div className="section-row">
                <h2 className="section-head">{query ? "Matching projects" : `All projects (${projects.length})`}</h2>
                {projects.length > 6 && <input type="search" className="home-filter" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter projects" aria-label="Filter projects" />}
              </div>
            )}
            <div className="cover-grid">
              {(query ? projects.filter((p) => p.name.toLowerCase().includes(query.trim().toLowerCase())) : projects.slice(1)).map((p, i) => <Cover key={p.name} p={p} index={i + 1} onOpen={onOpen} onDelete={setDeleteTarget} onContext={(x, y, p) => setCtx({ x, y, p })} />)}
              {query && !projects.some((p) => p.name.toLowerCase().includes(query.trim().toLowerCase())) && <div className="sp-none">No project matches “{query}”.</div>}
            </div>
          </>
        )}
      </div>

      {/* Only a real backend error shows here (an install or startup failure); routine log lines live in the project's Activity panel */}
      {(() => {
        const last = log[log.length - 1];
        return last && last.level === "error" ? (
          <div className="start-log" role="alert">
            <div className="start-log-entry error"><span className="msg">{last.message}</span></div>
          </div>
        ) : null;
      })()}

      {pickFrame && <FramePicker name={pickFrame} onClose={() => setPickFrame(null)} />}

      {ctx && (
        <>
          <div className="rv-menu-scrim" onMouseDown={() => setCtx(null)} onContextMenu={(e) => { e.preventDefault(); setCtx(null); }} />
          <div className="rv-menu-list" role="menu" style={{ position: "fixed", right: "auto", left: Math.min(ctx.x, window.innerWidth - 240), top: Math.min(ctx.y, window.innerHeight - 140) }} onKeyDown={(e) => { if (e.key === "Escape") setCtx(null); }}>
            <button role="menuitem" onClick={() => { const n = ctx.p.name; setCtx(null); onOpen(n); }}>Open</button>
            <button role="menuitem" onClick={() => { const n = ctx.p.name; setCtx(null); setPickFrame(n); }}>Choose cover frame…</button>
            {ctx.p.dir && <button role="menuitem" onClick={() => { const d = ctx.p.dir; setCtx(null); sendCommand({ type: "open_path", path: d }); }}>Show in Finder</button>}
            <hr />
            <button role="menuitem" className="danger" onClick={() => { const n = ctx.p.name; setCtx(null); setDeleteTarget(n); }}>Delete project…</button>
          </div>
        </>
      )}

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
    </main>
  );
}

function formatRelativeTime(unixSeconds) {
  if (!unixSeconds) return "";
  const diff = Date.now() / 1000 - unixSeconds;
  if (diff < 60) return "just now";
  if (diff < 3600) { const n = Math.floor(diff / 60); return `${n} min ago`; }
  if (diff < 86400) { const n = Math.floor(diff / 3600); return `${n} hr ago`; }
  if (diff < 86400 * 7) { const n = Math.floor(diff / 86400); return n === 1 ? "yesterday" : `${n} days ago`; }
  const d = new Date(unixSeconds * 1000);
  return d.toLocaleDateString();
}
