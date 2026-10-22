// Stills from the person's own footage, shared by every screen. The backend (thumbs.py) cuts and caches them; App.jsx hands its events to ingest().
// A key that has not come back yet reads as undefined (draw a placeholder), one that could not be made reads as null (draw the colored tile).
import { useSyncExternalStore } from "react";
import { sendCommand } from "./App.jsx";

const map = new Map();
const asked = new Set();
const subs = new Set();

const footage = new Map();                 // project name -> the file its poster came from, for the hover strip
export function projectFootage(name) { return footage.get(name); }

/** The frame the person chose for a project, which wins over the one the backend picked. */
export function setCover(name, uri) { map.set(`poster:${name}`, uri); chosen.add(name); subs.forEach((f) => f()); }
const chosen = new Set();
try {
  for (let i = 0; i < localStorage.length; i += 1) {
    const k = localStorage.key(i);
    if (k && k.startsWith("ph.cover.")) { map.set(`poster:${k.slice(9)}`, localStorage.getItem(k)); chosen.add(k.slice(9)); }
  }
} catch (e) { /* private window: the backend's own choice is used */ }

export function ingest(ev) {
  if (ev.type === "project_poster") {
    if (!chosen.has(ev.name)) map.set(`poster:${ev.name}`, ev.poster || null);
    if (ev.footage) footage.set(ev.name, ev.footage);
  }
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
  // The backend answers up to 40 at a time, in order; a long list (71 sequences) goes as several requests so the first screenful fills first
  for (let k = 0; k < need.length; k += 40) {
    const chunk = need.slice(k, k + 40);
    sendCommand({ type: "frame_thumbs", id: String(Math.random()), items: chunk, width }).catch(() => chunk.forEach((i) => asked.delete(i.key)));
  }
}

/** The strip behind a cover's hover scrub: a few seconds apart through the same interview. Keys are `strip:<name>:<i>`. */
export function requestStrip(name, seconds = [20, 48, 96, 160, 240]) {
  const path = footage.get(name);
  if (!path) return;
  requestFrames(seconds.map((t, i) => ({ key: `strip:${name}:${i}`, path, t })), 480);
}

export function requestPosters(projects) {
  const need = projects.filter((p) => p.dir && !asked.has(`poster:${p.name}`));
  if (!need.length) return;
  need.forEach((p) => asked.add(`poster:${p.name}`));
  sendCommand({ type: "project_posters", projects: need.map((p) => ({ name: p.name, dir: p.dir })) }).catch(() => need.forEach((p) => asked.delete(`poster:${p.name}`)));
}

/** The frame that stands for a cut (an XML): from its finished version if it has one (so graphics show), else from the first review. Returns the request item. */
const fallbacks = new Map();
/** A frame of the cut's own footage, known from the sequence listing, for a cut that has no preview yet: { path, t }. */
export function setFallback(xml, frame) { if (frame && frame.path) fallbacks.set(String(xml), frame); }
const hints = new Map();
export function hintFrame(key, frame) { if (frame && frame.path) hints.set(key, frame); }
export function takeHint(key) { return hints.get(key); }

export function cutFrameItem(xml, t = 3) {
  const base = String(xml).replace(/\.xml$/i, "");
  const v2 = `${base}_v2 - revised`;
  const fb = fallbacks.get(String(xml));
  return { key: `cut:${xml}`, path: `${v2}/preview_full.mp4`, alts: [`${v2}/preview.mp4`, `${base} - review/preview.mp4`, ...(fb ? [{ path: fb.path, t: fb.t }] : [])], t };
}

export { tileColors } from "./tileColors.js";
