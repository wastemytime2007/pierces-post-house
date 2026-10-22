// Stills from the person's own footage, shared by every screen. The backend (thumbs.py) cuts and caches them; App.jsx hands its events to ingest().
// A key that has not come back yet reads as undefined (draw a placeholder), one that could not be made reads as null (draw the colored tile).
import { useSyncExternalStore } from "react";
import { sendCommand } from "./App.jsx";

const map = new Map();
const asked = new Set();
const subs = new Set();

export function ingest(ev) {
  if (ev.type === "project_poster") map.set(`poster:${ev.name}`, ev.poster || null);
  else if (ev.type === "frame_thumbs") for (const [k, v] of Object.entries(ev.thumbs || {})) map.set(k, v || null);
  else return;
  subs.forEach((f) => f());
}

export function useThumb(key) {
  return useSyncExternalStore(
    (cb) => { subs.add(cb); return () => subs.delete(cb); },
    () => (map.has(key) ? map.get(key) : undefined),
  );
}

/** Ask for frames: items are [{ key, path, t }]. Each key is asked for once. */
export function requestFrames(items, width = 320) {
  const need = items.filter((i) => i.key && i.path && !map.has(i.key) && !asked.has(i.key));
  if (!need.length) return;
  need.forEach((i) => asked.add(i.key));
  sendCommand({ type: "frame_thumbs", id: String(Math.random()), items: need, width }).catch(() => need.forEach((i) => asked.delete(i.key)));
}

export function requestPosters(projects) {
  const need = projects.filter((p) => p.dir && !asked.has(`poster:${p.name}`));
  if (!need.length) return;
  need.forEach((p) => asked.add(`poster:${p.name}`));
  sendCommand({ type: "project_posters", projects: need.map((p) => ({ name: p.name, dir: p.dir })) }).catch(() => need.forEach((p) => asked.delete(`poster:${p.name}`)));
}

/** The frame that stands for a cut (an XML): from its finished version if it has one (so graphics show), else from the first review. Returns the request item. */
export function cutFrameItem(xml, t = 3) {
  const base = String(xml).replace(/\.xml$/i, "");
  const v2 = `${base}_v2 - revised`;
  return { key: `cut:${xml}`, path: `${v2}/preview_full.mp4`, alts: [`${v2}/preview.mp4`, `${base} - review/preview.mp4`], t };
}

/** A color pair from a name, so a project with no footage still has its own look (always from the brand's blues and navy). */
export function tileColors(name) {
  let h = 0;
  for (const c of String(name)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const hues = [[3, 52, 89], [3, 74, 120], [6, 86, 130], [8, 60, 104], [4, 66, 96]];
  const a = hues[h % hues.length], b = hues[(h >> 3) % hues.length];
  return [`rgb(${a[0]}, ${a[1]}, ${a[2]})`, `rgb(${Math.min(255, b[0] + 12)}, ${Math.min(255, b[1] + 40)}, ${Math.min(255, b[2] + 70)})`];
}
