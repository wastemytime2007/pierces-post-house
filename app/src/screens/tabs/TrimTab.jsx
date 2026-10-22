// Step 3: the part that happens in Premiere. The app does not edit here; it waits, says exactly what to do, and takes the trimmed XML back (drop it anywhere, or choose it).
import { useEffect, useState } from "react";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import ExportModal from "../../components/ExportModal.jsx";
import PageHeader from "../../components/PageHeader.jsx";
import Icon from "../../components/Icon.jsx";
import { sendCommand } from "../../App.jsx";
import { useThumb, requestFrames } from "../../thumbStore.js";
import { dragProps } from "../../dragOut.js";

const dirOf = (p) => String(p).replace(/\/[^/]*$/, "");

function when(mtime) {
  const d = Date.now() / 1000 - mtime;
  if (d < 90) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} hr ago`;
  return new Date(mtime * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
}

const shortName = (a) => a.name.replace(/^Adobe /, "");

// An export is the cuts inside it: the first frame of each of its first few sequences, not a document icon.
function ExportStrip({ xml, frames }) {
  const keys = (frames || []).map((f, i) => `xf:${xml}:${i}`);
  const thumbs = [useThumb(keys[0]), useThumb(keys[1]), useThumb(keys[2]), useThumb(keys[3]), useThumb(keys[4])];
  useEffect(() => {
    if (frames && frames.length) requestFrames(frames.map((f, i) => ({ key: `xf:${xml}:${i}`, path: f.path, t: f.t })), 240);
  }, [xml, frames]);                                                     // eslint-disable-line react-hooks/exhaustive-deps
  const shown = thumbs.filter(Boolean);
  if (!shown.length) return <span className="trim-row-icon"><Icon name="doc" size={18} /></span>;
  return (
    <span className="trim-strip" aria-hidden="true">
      {shown.map((u, i) => <span key={i} style={{ backgroundImage: `url(${u})` }} />)}
    </span>
  );
}

export default function TrimTab({ exportsList, onGoTo, ideas = [], chosen = [], project, subscribe, autoIncludeRulesCount = 0 }) {
  // The stories chosen on the Stories step
  const stories = ideas.filter((i) => chosen.includes(i.idea_id) && (i.kind === "deliverable" || i.kind === "story_angle"));
  const titleOf = (i) => (i.kind === "story_angle" ? i.data?.brief?.title : i.data?.concept) || "Untitled";

  // Which Premiere: every install is listed by the backend; the choice is remembered (the Finish step reads the same one)
  const [apps, setApps] = useState([]);
  const [link, setLink] = useState(null);                   // { panel, running }: whether Premiere is open and whether its Post House panel is answering
  const [choice, setChoice] = useState(() => { try { return localStorage.getItem("review.premiere") || ""; } catch (e) { return ""; } });
  useEffect(() => {
    const off = subscribe ? subscribe((ev) => { if (ev.type === "premiere_apps") { setApps(ev.apps || []); setLink({ panel: !!ev.panel, running: !!ev.running }); } }) : null;
    const ask = () => sendCommand({ type: "premiere_apps" }).catch(() => {});
    ask();
    const id = setInterval(ask, 8000);                      // Premiere can be opened or closed while this page is showing
    return () => { clearInterval(id); if (off) off(); };
  }, []);                                                                   // eslint-disable-line react-hooks/exhaustive-deps
  const app = apps.find((a) => a.path === choice) || apps[0] || null;
  const pickApp = (path) => { setChoice(path); try { localStorage.setItem("review.premiere", path); } catch (e) { /* not kept */ } };

  // The build: "start" runs it at once with the usual options; "options" shows them first
  const [build, setBuild] = useState(null);
  const [sent, setSent] = useState(false);                  // the XML has just gone to Premiere: the tile settles once

  const bringBack = (path) => window.dispatchEvent(new CustomEvent("posthouse-open-xml", { detail: { path } }));
  const choose = async () => {
    try {
      const p = await openDialog({ multiple: false, filters: [{ name: "Premiere XML", extensions: ["xml"] }] });
      if (typeof p === "string") bringBack(p);
    } catch (e) { /* dialog closed */ }
  };
  const recent = (exportsList || []).slice(0, 5);
  // The frames inside each export, asked for once the list is known
  const [xmlFrames, setXmlFrames] = useState({});
  useEffect(() => {
    const off = subscribe ? subscribe((ev) => { if (ev.type === "xml_frames") setXmlFrames((m) => ({ ...m, [ev.xml]: ev })); }) : null;
    if (recent.length) sendCommand({ type: "xml_frames", xmls: recent.map((x) => x.path) }).catch(() => {});
    return () => { if (off) off(); };
  }, [recent.map((x) => x.path).join("|")]);                             // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="trim">
      <PageHeader
        title="Trim"
        lead="This part happens in Premiere. Edit each story's storyline and delivery the way you want it, then export the XML and bring it back here."
        next="Next: Finish, with the XML you export from Premiere"
      />

      <ol className="trim-steps">
        <li className={`trim-act ${sent ? "ph-sent" : ""}`}>
          {stories.length === 0 ? (
            <button className="trim-act-btn" onClick={() => onGoTo("ideas")}>
              <b>Open the XML in Premiere <Icon name="arrowRight" size={14} /></b>
              <span>Choose your stories first, then open them here.</span>
            </button>
          ) : (
            <button className="trim-act-btn" onClick={() => setBuild("start")}>
              <b>Open the XML in {app && apps.length > 1 ? shortName(app) : "Premiere"} <Icon name="arrowUpRight" size={14} /></b>
              <span>{stories.length === 1 ? "1 story" : `${stories.length} stories`}, each its own sequence in order, with its audio synced.</span>
            </button>
          )}
        </li>
        <li><b>Trim it your way</b><span>Cut the storyline, tighten the delivery, change what you like. Nothing here edits it.</span></li>
        <li><b>Export the XML back</b><span>File, Export, Final Cut Pro XML. One sequence or many in the same file is fine.</span></li>
      </ol>
      {stories.length > 0 && (
        <div className="trim-tools">
          {apps.length > 1 && (
            <label className="trim-version">
              <span>Premiere version</span>
              <select value={app ? app.path : ""} onChange={(e) => pickApp(e.target.value)}>
                {apps.map((a) => <option key={a.path} value={a.path}>{shortName(a)}</option>)}
              </select>
            </label>
          )}
          {link && (
            <span className={`trim-link ${link.panel ? "on" : link.running ? "warn" : ""}`} title={link.panel
              ? "Post House can name the Premiere project and save it beside the XML"
              : link.running ? "Premiere is open but its Post House panel is not answering. A panel loads when Premiere starts, so quit Premiere and open it again." : "Premiere will be started when you open a cut"}>
              <span className="trim-link-dot" aria-hidden="true" />
              {link.panel ? "Premiere connected" : link.running ? "Premiere open, panel not loaded" : "Premiere not running"}
            </span>
          )}
          <button className="btn btn-sm btn-ghost" onClick={() => setBuild("options")}>Options…</button>
          <button className="btn btn-sm btn-ghost" onClick={() => onGoTo("ideas")}>Change stories</button>
          {apps.length === 0 && <span>Premiere Pro was not found in Applications: the XML opens with whatever your Mac uses.</span>}
        </div>
      )}

      <div className="trim-cols">
        <section className="trim-sent" aria-labelledby="trim-sent-h">
          <h2 id="trim-sent-h">Sent to Premiere</h2>
          {recent.length === 0 ? (
            <div className="trim-empty">
              <p>Nothing has been exported from this project yet.</p>
              <button className="btn btn-primary" onClick={() => onGoTo("ideas")}>Choose stories</button>
            </div>
          ) : (
            <ul className="trim-list">
              {recent.map((x) => (
                <li key={x.path} className="trim-row" {...dragProps(x.path)}>
                  <ExportStrip xml={x.path} frames={xmlFrames[x.path]?.frames} />
                  <span className="trim-row-main">
                    <span className="trim-row-name">{x.name}</span>
                    <span className="trim-row-meta">{xmlFrames[x.path]?.sequences ? `${xmlFrames[x.path].sequences} sequence${xmlFrames[x.path].sequences === 1 ? "" : "s"} · ` : ""}{when(x.mtime)}{x.session ? ` · opened in Finish, ${x.session.versions} version${x.session.versions === 1 ? "" : "s"}` : ""}</span>
                  </span>
                  <button className="btn btn-sm btn-ghost" onClick={() => sendCommand({ type: "open_path", path: x.path })} title="Open the XML with its default app (Premiere)">Open</button>
                  <button className="btn btn-sm btn-ghost" onClick={() => sendCommand({ type: "open_path", path: dirOf(x.path) })} title="Show the folder in Finder">Show in Finder</button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="trim-drop" aria-labelledby="trim-drop-h">
          <span className="trim-drop-art" aria-hidden="true"><span /><span /><span /></span>
          <h2 id="trim-drop-h">Bring the trimmed XML back</h2>
          <p>Drop it anywhere in this window, or choose it. It opens in Finish on its own frames.</p>
          <button className="btn btn-primary" onClick={choose}><Icon name="folderOpen" size={15} /> Choose XML…</button>
        </section>
      </div>
      {build && (
        <ExportModal
          key={build}
          selectedIdeas={stories}
          projectName={project?.name || "project"}
          brollCount={project?.broll_clip_count || 0}
          autoIncludeRulesCount={autoIncludeRulesCount}
          autoStart={build === "start"}
          premiereApp={app ? app.path : ""}
          onClose={() => setBuild(null)}
          onExported={() => { setBuild(null); setSent(true); setTimeout(() => setSent(false), 320); }}
        />
      )}
    </div>
  );
}
