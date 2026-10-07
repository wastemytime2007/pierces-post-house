import React from "react";
import { createRoot } from "react-dom/client";
import "../src/styles.css";
import ReviewTab from "../src/screens/tabs/ReviewTab.jsx";

// Events are pushed from the page with window.__emit({type: ...}); commands the tab sends are collected in window.__sent.
const listeners = new Set();
window.__emit = (ev) => listeners.forEach((f) => f(ev));
const subscribe = (f) => { listeners.add(f); return () => listeners.delete(f); };

function Harness() {
  const [label, setLabel] = React.useState("");
  return (
    <div className="project-view">
      <div id="tablabel" style={{ color: "#7c848e", fontSize: 12, marginBottom: 8 }}>tab label: {label}</div>
      <ReviewTab subscribe={subscribe} onStatus={setLabel} />
    </div>
  );
}
createRoot(document.getElementById("root")).render(<Harness />);
