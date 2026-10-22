// Step 3: the part that happens in Premiere. The app does not edit here; it waits, says exactly what to do, and takes the trimmed XML back (drop it anywhere, or choose it).
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import PageHeader from "../../components/PageHeader.jsx";
import Icon from "../../components/Icon.jsx";
import { sendCommand } from "../../App.jsx";

const dirOf = (p) => String(p).replace(/\/[^/]*$/, "");

function when(mtime) {
  const d = Date.now() / 1000 - mtime;
  if (d < 90) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} hr ago`;
  return new Date(mtime * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
}

export default function TrimTab({ exportsList, onGoTo }) {
  const bringBack = (path) => window.dispatchEvent(new CustomEvent("posthouse-open-xml", { detail: { path } }));
  const choose = async () => {
    try {
      const p = await openDialog({ multiple: false, filters: [{ name: "Premiere XML", extensions: ["xml"] }] });
      if (typeof p === "string") bringBack(p);
    } catch (e) { /* dialog closed */ }
  };
  const recent = (exportsList || []).slice(0, 5);

  return (
    <div className="trim">
      <PageHeader
        title="Trim"
        lead="This part happens in Premiere. Edit each story's storyline and delivery the way you want it, then export the XML and bring it back here."
        next="Next: Finish, with the XML you export from Premiere"
      />

      <ol className="trim-steps">
        <li><b>Open the XML in Premiere</b><span>Each story is its own sequence, in order, with its audio synced.</span></li>
        <li><b>Trim it your way</b><span>Cut the storyline, tighten the delivery, change what you like. Nothing here edits it.</span></li>
        <li><b>Export the XML back</b><span>File, Export, Final Cut Pro XML. One sequence or many in the same file is fine.</span></li>
      </ol>

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
                <li key={x.path} className="trim-row">
                  <span className="trim-row-icon"><Icon name="doc" size={18} /></span>
                  <span className="trim-row-main">
                    <span className="trim-row-name">{x.name}</span>
                    <span className="trim-row-meta">{when(x.mtime)}{x.session ? ` · opened in Finish, ${x.session.versions} version${x.session.versions === 1 ? "" : "s"}` : ""}</span>
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
    </div>
  );
}
