import { useCallback, useEffect, useRef, useState } from "react";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { listen } from "@tauri-apps/api/event";
import { sendCommand } from "../../App.jsx";
import DropZone, { getHoveredKind, clearHoveredKind } from "../../components/DropZone.jsx";

/**
 * PMTab — Post House Task 1.1, the Project Manager's first screen.
 *
 * Second revision. First version used one Browse-button-and-dropdown row
 * per source — Ryan called it clunky next to PreCut's actual drag-and-drop
 * zones, correctly: that pattern was already solved in this same app and
 * shouldn't have been rebuilt as folder-picker rows. This version reuses
 * DropZone.jsx and IngestTab's own drag-drop wiring verbatim, just against
 * local per-kind arrays instead of a backend-tracked `project.sources`
 * (the Project Manager doesn't have PreCut's Project model yet -- these
 * paths are only sent to the backend once, when "Organize" is clicked).
 *
 * organize_project() itself is unchanged and already tested; only the
 * client-side collection of {path, kind} pairs changed shape.
 */
const SOURCE_ZONES = [
  { kind: "aroll", label: "A-Roll", title: "Interviews, talking-head footage", description: "Drag folders or files here" },
  { kind: "broll", label: "B-Roll", title: "Supplementary/cutaway footage", description: "Drag folders or files here" },
  { kind: "source_audio", label: "Source Audio", title: "Lav mics, external recorders", description: "Drag folders or files here" },
  { kind: "assets", label: "Assets", title: "Anything else that belongs to this project", description: "Drag folders or files here" },
];
const PROJECT_TYPES = ["interview", "property_tour", "renovation", "event", "product", "other"];

export default function PMTab({ subscribe }) {
  const [rootDir, setRootDir] = useState("");
  const [clientName, setClientName] = useState("");
  const [projectName, setProjectName] = useState("");
  const [projectType, setProjectType] = useState(PROJECT_TYPES[0]);
  const [brandAssetsDir, setBrandAssetsDir] = useState("");

  // Local per-kind path lists -- plain path strings, DropZone already
  // supports this ("legacy string" branch) alongside the richer
  // SourceFolder-object shape PreCut's own backend-tracked model uses.
  const [pathsByKind, setPathsByKind] = useState({
    aroll: [], broll: [], source_audio: [], assets: [],
  });

  // dual_use (contract §2.3): a single aroll source, culled under both
  // rulesets -- NOT the same folder declared twice under different kinds.
  // Found the hard way: dropping one real Osmo folder into both the
  // A-Roll and B-Roll zones (the subject keeps talking while the shooter
  // grabs coverage -- exactly the case the contract's own dual_use field
  // exists for) is correctly rejected by the manifest validator ("both
  // resolve to ... with different kinds"). The fix is this checklist, not
  // relaxing that rule -- so a cross-zone duplicate is blocked client-side
  // with a pointer here, rather than sent to the backend to fail again.
  const [dualUseAroll, setDualUseAroll] = useState({});
  const [notice, setNotice] = useState(null);

  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    return subscribe((ev) => {
      if (!submitting) return;
      if (ev.type === "project_organized") {
        setResult(ev); setError(null); setSubmitting(false);
      } else if (ev.type === "error") {
        setError(ev.message); setResult(null); setSubmitting(false);
      }
    });
  }, [subscribe, submitting]);

  const addPaths = useCallback((kind, paths) => {
    setPathsByKind((prev) => {
      const existing = new Set(prev[kind]);
      const merged = [...prev[kind]];
      const blocked = [];
      for (const p of paths) {
        if (existing.has(p)) continue;
        // Same path already declared under a DIFFERENT kind: this is the
        // dual_use case, not a real second source. Block it here instead
        // of letting organize_project reject it after a round trip.
        const otherKind = Object.keys(prev).find((k) => k !== kind && prev[k].includes(p));
        if (otherKind) { blocked.push({ path: p, otherKind }); continue; }
        merged.push(p);
        existing.add(p);
      }
      if (blocked.length) {
        const { path, otherKind } = blocked[0];
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
      return { ...prev, [kind]: merged };
    });
  }, []);

  const removePath = useCallback((kind, path) => {
    setPathsByKind((prev) => ({ ...prev, [kind]: prev[kind].filter((p) => p !== path) }));
    if (kind === "aroll") {
      setDualUseAroll((prev) => {
        if (!(path in prev)) return prev;
        const next = { ...prev };
        delete next[path];
        return next;
      });
    }
  }, []);

  const toggleDualUse = useCallback((path) => {
    setDualUseAroll((prev) => ({ ...prev, [path]: !prev[path] }));
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

  // Same drag-drop wiring as IngestTab: DropZone marks itself as the hot
  // zone on hover, we read that at Tauri's drop event and route paths to
  // the right kind. See DropZone.jsx's own docstring for why (broken
  // position-payload coordinates on macOS, Tauri 2.8's duplicate-fire bug).
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

  const totalSources = Object.values(pathsByKind).reduce((n, arr) => n + arr.length, 0);
  const canSubmit = rootDir.trim() && clientName.trim() && projectName.trim() && projectType && totalSources > 0 && !submitting;

  const handleOrganize = async () => {
    setSubmitting(true); setResult(null); setError(null);
    const sources = [];
    for (const [kind, paths] of Object.entries(pathsByKind)) {
      for (const path of paths) {
        const entry = { path, kind };
        if (kind === "aroll" && dualUseAroll[path]) entry.dual_use = true;
        sources.push(entry);
      }
    }
    try {
      await sendCommand({
        type: "organize_project",
        root_dir: rootDir.trim(),
        client_name: clientName.trim(),
        project_name: projectName.trim(),
        project_type: projectType,
        sources,
        brand_assets_source_dir: brandAssetsDir.trim() || undefined,
      });
    } catch (e) {
      setError(String(e)); setSubmitting(false);
    }
  };

  return (
    <div className="pm-tab">
      <h2>Project Manager</h2>
      <p className="pm-tab-sub">
        Point this at one real project. It organizes the declared sources,
        stages brand assets if given, and writes the Project Manifest.
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
            items={pathsByKind[z.kind]}
            onRemove={(p) => removePath(z.kind, p)}
            onPick={() => handlePickFiles(z.kind)}
            onPickFolder={() => handlePickFolder(z.kind)}
          />
        ))}
      </div>

      {pathsByKind.aroll.length > 0 && (
        <div className="pm-tab-dualuse">
          <span className="pm-tab-dualuse-label">
            Dual-use — A-Roll that also serves as B-Roll (the subject keeps
            talking while the shooter grabs coverage). Culled under both
            rulesets later; check any that apply:
          </span>
          {pathsByKind.aroll.map((p) => (
            <label key={p} className="pm-tab-dualuse-row">
              <input
                type="checkbox"
                checked={!!dualUseAroll[p]}
                onChange={() => toggleDualUse(p)}
              />
              <span title={p}>{basename(p)}</span>
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
        {submitting ? "Organizing…" : "Organize"}
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
    </div>
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

const KIND_LABELS = { aroll: "A-Roll", broll: "B-Roll", source_audio: "Source Audio", assets: "Assets" };
function labelFor(kind) {
  return KIND_LABELS[kind] || kind;
}
