// Development only: mounts the project screen with a stand-in backend so it can be looked at and driven in a browser (see harness.html). Not imported by the app.
import React, { useCallback, useEffect, useRef, useState } from "react";
import ReactDOM from "react-dom/client";
import "./styles.css";
import ProjectView from "./screens/ProjectView.jsx";
import App from "./App.jsx";

const params = new URLSearchParams(window.location.search);

const PROJECT = {
  name: "Runnells Weekend",
  resolved_dir: "/Users/pierce/Documents/Post House/Runnells Weekend",
  sources: [
    { kind: "aroll", name: "Interviews", root_path: "/Volumes/Runnells/Interviews", files: { "wknd_Bob_01.mp4": { transcript_status: "done" }, "wknd_Mitch_01.mp4": { transcript_status: "done" } } },
    { kind: "broll", name: "Job site", root_path: "/Volumes/Runnells/Job site", files: { "DJI_0021.mp4": {} } },
  ],
};

function Harness() {
  const subs = useRef(new Set());
  const subscribe = useCallback((h) => { subs.current.add(h); return () => subs.current.delete(h); }, []);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    window.__emit = (ev) => subs.current.forEach((h) => h(ev));
    setReady(true);
    window.__ready = true;
  }, []);
  if (!ready) return null;
  return (
    <div className="app"><div style={{ height: 44, flex: "none" }} />
    <ProjectView
      project={window.__project || PROJECT}
      onClose={() => {}}
      onDelete={() => {}}
      subscribe={subscribe}
      log={[]}
      onClearLog={() => {}}
      settings={{ has_env: true }}
      onOpenApiKeyHelp={() => {}}
      shouldShowAutoIncludeNudge={false}
      onMarkAutoIncludeNudgeSeen={() => {}}
      onOpenAutoIncludeModal={() => {}}
      autoIncludeRulesCount={0}
      audienceProfiles={[]}
    /></div>
  );
}

// ?app=1 mounts the whole app (title bar, project list, modals); feed it with window.__backend(event)
ReactDOM.createRoot(document.getElementById("root")).render(params.get("app") ? <App /> : <Harness />);
