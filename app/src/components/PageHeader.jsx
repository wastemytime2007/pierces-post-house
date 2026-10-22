// What this page is for, in one plain sentence, and what comes after it. Every page of a project opens with one.
// `tip` is said once, the first time someone is in this room, and never again: teaching belongs where the work is, not in a tour (HIG onboarding.md).
import { useState } from "react";
import Icon from "./Icon.jsx";

export default function PageHeader({ title, lead, next, tip, tipKey }) {
  const [seen, setSeen] = useState(() => {
    if (!tipKey) return true;
    try { return localStorage.getItem(`ph.tip.${tipKey}`) === "seen"; } catch (e) { return true; }
  });
  const dismiss = () => {
    setSeen(true);
    try { localStorage.setItem(`ph.tip.${tipKey}`, "seen"); } catch (e) { /* not kept: it will be said once more */ }
  };
  return (
    <header className="page-header">
      <h1>{title}</h1>
      <p>{lead}</p>
      {next && <div className="page-next"><Icon name="chevronRight" size={13} />{next}</div>}
      {tip && !seen && (
        <div className="page-tip" role="note">
          <Icon name="sparkles" size={14} />
          <span>{tip}</span>
          <button className="btn btn-ghost btn-sm" onClick={dismiss}>Got it</button>
        </div>
      )}
    </header>
  );
}
