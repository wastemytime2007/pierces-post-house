// One tooltip for the whole app. Any element with a `title` gets it: the title moves to `data-tip` (and to `aria-label` for icon-only controls, so a screen reader still has a name), and the native yellow box never shows.
// Timing follows the usual rule: wait a moment before the first one, then no wait when moving to a neighbor; any press or key hides it.
import { useEffect, useState } from "react";

export default function Tooltips() {
  const [tip, setTip] = useState(null);

  useEffect(() => {
    let timer = 0;
    let current = null;
    let lastShown = 0;
    const find = (n) => (n && n.closest ? n.closest("[title],[data-tip]") : null);

    const adopt = (el) => {
      const t = el.getAttribute("title");
      if (!t) return;
      el.setAttribute("data-tip", t);
      if (!el.getAttribute("aria-label") && !(el.textContent || "").trim()) el.setAttribute("aria-label", t);
      el.removeAttribute("title");
    };
    const hide = () => { clearTimeout(timer); setTip(null); };
    const show = (el) => {
      const text = el.getAttribute("data-tip");
      if (!text || !el.isConnected) return;
      const r = el.getBoundingClientRect();
      const x = Math.min(Math.max(r.left + r.width / 2, 130), window.innerWidth - 130);
      const below = r.bottom + 40 < window.innerHeight;
      setTip({ text, x, y: below ? r.bottom + 8 : r.top - 8, below });
      lastShown = Date.now();
    };

    const over = (e) => {
      const el = find(e.target);
      if (!el || el === current) return;
      current = el;
      clearTimeout(timer);
      adopt(el);
      if (!el.getAttribute("data-tip")) return;
      const quick = Date.now() - lastShown < 500;                       // moving between neighbors: no wait
      timer = setTimeout(() => show(el), quick ? 0 : 600);
    };
    const out = (e) => {
      const el = find(e.target);
      if (el && el !== current) return;
      if (e.relatedTarget && el && el.contains(e.relatedTarget)) return;
      current = null;
      hide();
    };
    const focus = (e) => { const el = find(e.target); if (el && e.target.matches?.(":focus-visible")) { adopt(el); current = el; show(el); } };

    document.addEventListener("mouseover", over);
    document.addEventListener("mouseout", out);
    document.addEventListener("mousedown", hide, true);
    document.addEventListener("keydown", hide, true);
    document.addEventListener("scroll", hide, true);
    document.addEventListener("focusin", focus);
    document.addEventListener("focusout", hide);
    return () => {
      clearTimeout(timer);
      for (const [n, f, c] of [["mouseover", over], ["mouseout", out], ["mousedown", hide, true], ["keydown", hide, true], ["scroll", hide, true], ["focusin", focus], ["focusout", hide]]) document.removeEventListener(n, f, c);
    };
  }, []);

  if (!tip) return null;
  return (
    <div className="tooltip" role="tooltip" style={{ left: tip.x, top: tip.y, transform: `translate(-50%, ${tip.below ? "0" : "-100%"})` }}>
      {tip.text}
    </div>
  );
}
