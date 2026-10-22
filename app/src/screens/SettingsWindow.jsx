// Settings in a window of its own, the way macOS does it (HIG settings.md: "a settings window with a fixed toolbar", general and infrequently changed options, no Save button:
// a change takes effect when it is made). Opened with Command-comma or from the app menu; it talks to the same backend as the main window.
import { useEffect, useState } from "react";
import { sendCommand } from "../App.jsx";
import Icon from "../components/Icon.jsx";

const PANES = [
  { id: "general", name: "General", icon: "sliders" },
  { id: "premiere", name: "Premiere", icon: "arrowUpRight" },
  { id: "ai", name: "AI", icon: "sparkles" },
];

function Row({ label, hint, children }) {
  return (
    <div className="set-row">
      <div className="set-row-label">
        <span>{label}</span>
        {hint && <span className="set-row-hint">{hint}</span>}
      </div>
      <div className="set-row-control">{children}</div>
    </div>
  );
}

export default function SettingsWindow() {
  const [pane, setPane] = useState("general");
  const [settings, setSettings] = useState(null);
  const [apps, setApps] = useState([]);
  const [premiere, setPremiere] = useState(() => { try { return localStorage.getItem("review.premiere") || ""; } catch (e) { return ""; } });
  const [sounds, setSounds] = useState(() => { try { return localStorage.getItem("ph.sounds") === "on"; } catch (e) { return false; } });
  const [key, setKey] = useState("");
  const [saved, setSaved] = useState("");

  useEffect(() => {
    let un;
    (async () => {
      const { listen } = await import("@tauri-apps/api/event");
      un = await listen("backend-event", (e) => {
        let ev;
        try { ev = JSON.parse(e.payload); } catch { return; }
        if (ev.type === "ready") setSettings(ev.settings || null);
        if (ev.type === "premiere_apps") setApps(ev.apps || []);
        if (ev.type === "api_key_saved") { setSaved("Saved."); setKey(""); }
      });
      sendCommand({ type: "hello" }).catch(() => {});
      sendCommand({ type: "premiere_apps" }).catch(() => {});
    })();
    return () => { if (un) un(); };
  }, []);

  const pick = (p) => { setPremiere(p); try { localStorage.setItem("review.premiere", p); } catch (e) { /* not kept */ } };
  const toggleSounds = (on) => { setSounds(on); try { localStorage.setItem("ph.sounds", on ? "on" : "off"); } catch (e) { /* not kept */ } };
  const short = (a) => a.name.replace(/^Adobe /, "");

  return (
    <div className="set-win">
      <div className="set-toolbar" data-tauri-drag-region>
        {PANES.map((p) => (
          <button key={p.id} className={`set-tab ${pane === p.id ? "on" : ""}`} onClick={() => setPane(p.id)} aria-pressed={pane === p.id}>
            <Icon name={p.icon} size={17} />
            <span>{p.name}</span>
          </button>
        ))}
      </div>

      <div className="set-body">
        {pane === "general" && (
          <>
            <Row label="A sound when work finishes" hint="One quiet sound when a batch ends or an XML comes back. Off, because a batch can end at 3 a.m.">
              <label className="switch"><input type="checkbox" checked={sounds} onChange={(e) => toggleSounds(e.target.checked)} /><span /></label>
            </Row>
            <Row label="Appearance" hint="Post House follows the system, light and dark.">
              <span className="set-static">System</span>
            </Row>
          </>
        )}

        {pane === "premiere" && (
          <>
            <Row label="Which Premiere to open" hint={apps.length > 1 ? "You have more than one installed." : "The one found in your Applications folder."}>
              {apps.length === 0 ? (
                <span className="set-static">Premiere Pro was not found</span>
              ) : (
                <select value={premiere || apps[0].path} onChange={(e) => pick(e.target.value)}>
                  {apps.map((a) => <option key={a.path} value={a.path}>{short(a)}</option>)}
                </select>
              )}
            </Row>
            <Row label="Named projects" hint="Post House asks Premiere to make a project named after the XML, beside it. This needs the Post House panel, which Premiere loads when it starts.">
              <span className="set-static">Through the Post House panel</span>
            </Row>
          </>
        )}

        {pane === "ai" && (
          <>
            <Row label="Anthropic API key" hint={settings?.has_env ? "Currently from your environment." : settings?.key_suffix ? `Saved, ending ${settings.key_suffix}` : "Stories and the creative pass need a key."}>
              <input type="password" value={key} placeholder="sk-ant-..." onChange={(e) => { setKey(e.target.value); setSaved(""); }}
                onKeyDown={(e) => { if (e.key === "Enter" && key.trim()) sendCommand({ type: "set_api_key", api_key: key.trim() }); }} />
            </Row>
            {saved && <div className="set-note">{saved}</div>}
            <Row label="Where research comes from" hint="During the build this is a seed file and the local Claude CLI, so nothing is billed.">
              <span className="set-static">{settings?.llm_via_cli ? "Local Claude CLI" : "Anthropic API"}</span>
            </Row>
          </>
        )}
      </div>
    </div>
  );
}
