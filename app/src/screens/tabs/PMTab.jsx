import { useCallback, useEffect, useRef, useState } from "react";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { listen } from "@tauri-apps/api/event";
import { sendCommand } from "../../App.jsx";
import DropZone, { getHoveredKind, clearHoveredKind } from "../../components/DropZone.jsx";

/**
 * PMTab — Post House Task 1.1, the Project Manager's first screen.
 *
 * Third revision (2026-09-03). Ryan: "Ingest is asking half of the same
 * questions as the project manager tab. Which folders of footage to look
 * at and what category they belong to. The ingest tab should just merge
 * with the project manager tab." He was declaring the same aroll/broll/
 * source-audio folders twice -- once here (for the Project Manifest) and
 * again in Ingest (for PreCut's own Project model, which its proxy/
 * transcribe/tag/sync pipeline actually runs against).
 *
 * Fix: this tab is now the ONLY place footage gets declared. A-roll,
 * B-roll, and Source Audio drops call PreCut's own `add_source` directly
 * (exactly what IngestTab used to do) so `project.sources` is the single
 * source of truth -- IngestTab now just reads it. "Assets" stays local
 * and manifest-only (PreCut's Project model has no concept of it; it's
 * never proxied, transcribed, or tagged, just staged into the project
 * folder by organize_project). PreCut's own kind name is "audio"; the
 * Project Manifest contract's kind name is "source_audio" -- the zone
 * uses "audio" throughout (matching add_source) and translates to
 * "source_audio" only when building organize_project's sources array.
 *
 * organize_project() itself is unchanged and already tested; it now
 * mostly reads state PreCut's own model already holds, since the
 * client-side collection of {path, kind} pairs isn't separately
 * maintained for the three real kinds any more.
 */
const SOURCE_ZONES = [
  { kind: "aroll", label: "A-Roll", title: "Interviews, talking-head footage", description: "Drag folders or files here" },
  { kind: "broll", label: "B-Roll", title: "Supplementary/cutaway footage", description: "Drag folders or files here" },
  { kind: "audio", label: "Source Audio", title: "Lav mics, external recorders", description: "Drag folders or files here" },
  { kind: "assets", label: "Assets", title: "Anything else that belongs to this project", description: "Drag folders or files here" },
];
// Kind names as PreCut's own Project model / add_source use them, vs. the
// Project Manifest contract's names. Only "audio" differs; "assets" has
// no PreCut-side equivalent at all (see module docstring).
const CONTRACT_KIND = { aroll: "aroll", broll: "broll", audio: "source_audio", assets: "assets" };
const PROJECT_TYPES = ["interview", "property_tour", "renovation", "event", "product", "other"];

export default function PMTab({ subscribe, project }) {
  const [rootDir, setRootDir] = useState("");
  const [clientName, setClientName] = useState("");
  const [projectName, setProjectName] = useState("");
  const [projectType, setProjectType] = useState(PROJECT_TYPES[0]);
  const [brandAssetsDir, setBrandAssetsDir] = useState("");

  // "Assets" is the one zone with no PreCut-side model -- kept as plain
  // local path strings, same as every zone used to be before this merge.
  const [assetPaths, setAssetPaths] = useState([]);

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

  // Real (PreCut-backed) sources by kind, straight from project state --
  // exactly what IngestTab used to derive for its own zones.
  const realByKind = {
    aroll: project.sources.filter((s) => s.kind === "aroll"),
    broll: project.sources.filter((s) => s.kind === "broll"),
    audio: project.sources.filter((s) => s.kind === "audio"),
  };

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
    // Real kind: register with PreCut's own Project model directly. This
    // is the fix for Ryan's report -- footage gets declared once, here,
    // and Ingest's pipeline runs against the same `project.sources`.
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
        if (kind === "aroll" && dualUseAroll[s.root_path]) entry.dual_use = true;
        sources.push(entry);
      }
    }
    for (const p of assetPaths) sources.push({ path: p, kind: "assets" });
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
        Point this at one real project. Footage declared here is what
        Ingest processes and what the Project Manifest records — one
        declaration, not two.
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
            talking while the shooter grabs coverage). Culled under both
            rulesets later; check any that apply:
          </span>
          {realByKind.aroll.map((s) => (
            <label key={s.root_path} className="pm-tab-dualuse-row">
              <input
                type="checkbox"
                checked={!!dualUseAroll[s.root_path]}
                onChange={() => toggleDualUse(s.root_path)}
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

const KIND_LABELS = { aroll: "A-Roll", broll: "B-Roll", audio: "Source Audio", assets: "Assets" };
function labelFor(kind) {
  return KIND_LABELS[kind] || kind;
}
