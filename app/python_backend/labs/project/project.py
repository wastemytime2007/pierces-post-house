#!/usr/bin/env python3
"""One index of everything made for a project: versions, layers, audio, QA passes, style reports.

    python3 labs/project/project.py --root "~/Documents/Post House Reviews" --name "Runnells Tiling" [--open]

Reads the project's folders in place and writes `<root>/<name> - project index.html` and `project.json`. Nothing is
moved or renamed: the XMLs point at their layer files by absolute path, so moving a folder would break them.
Each folder is one entry, described from the records the tools leave in it (review timelines, revise ledgers,
reconform and QA results, placement files, audio builds, style reports). It says what exists, when it was made and
what it found; it cannot say whether anyone has approved it, and does not pretend to. Notes written by Claude as
stand-ins (not by Ryan) are flagged wherever they appear.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

KINDS = ["reconform", "qa", "style", "reference_music", "callout_change", "sfx_replacement", "audio", "captions", "image_card", "callout", "revision", "review_page", "notes", "other"]
LABEL = {"reconform": "Layers re-placed on a revised cut", "qa": "QA pass", "style": "Style profile", "reference_music": "Reference music", "callout_change": "Callout changed from notes",
         "sfx_replacement": "Sound effect replaced from a note", "audio": "Music and sound effect", "captions": "Captions", "image_card": "Image card", "callout": "Callout",
         "revision": "Revision from notes", "review_page": "Review page", "notes": "Notes", "other": "Other files"}


def _j(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def _files(folder: Path, depth: int = 2) -> list[Path]:
    out = []
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder)
        if p.is_file() and len(rel.parts) <= depth + 1 and not any(part.startswith(("_work", "hyperframes_project", "frames", ".")) for part in rel.parts):
            out.append(p)
    return out


def _fmt(t: float) -> str:
    return f"{int(t // 60):d}:{t % 60:04.1f}"


def describe(folder: Path) -> dict:
    files = _files(folder)
    by = {}
    for p in files:
        by.setdefault(p.name, p)
    kinds, facts, links, stand_in = [], [], [], False

    def link(label: str, p: Path):
        links.append({"label": label, "path": str(p.relative_to(folder.parent))})

    for p in files:
        if p.name.endswith(".xml"):
            txt = p.read_text(errors="ignore")
            layered = "overlay-file-" in txt or "audio-file-" in txt
            link("XML" + (" (with layers)" if layered else ""), p)
    if "reconform.json" in by:
        r = _j(by["reconform.json"]) or {}
        led = r.get("ledger", [])
        kinds.append("reconform")
        facts.append(f"{sum(1 for e in led if e.get('ok'))} of {len(led)} steps done: " + "; ".join(f"{e['layer']}: {e['detail']}" for e in led)[:400])
    if "qa.json" in by:
        q = _j(by["qa.json"]) or {}
        c = {}
        for n in q.get("notes", []):
            c[n["status"]] = c.get(n["status"], 0) + 1
        kinds.append("qa")
        facts.append(f"{len(q.get('notes', []))} notes: " + ", ".join(f"{c[k]} {k.lower()}" for k in ("VERIFIED", "APPLIED-UNMEASURED", "NOT DONE", "FAILED") if k in c) + (f"; {len(q.get('unrequested', []))} unrequested change(s)" if q.get("unrequested") else "; nothing changed that was not asked for"))
        if (folder / "qa_report.html").exists():
            link("QA report", folder / "qa_report.html")
    if "style.json" in by:
        s = _j(by["style.json"]) or {}
        kinds.append("style")
        facts.append(f"reference {Path(s.get('reference', {}).get('file', '?')).name} against {Path((s.get('ours') or {}).get('file', '?')).name}: {len((s.get('comparison') or {}).get('suggested_notes', []))} suggested note(s)")
        if (folder / "style_report.html").exists():
            link("Style report", folder / "style_report.html")
    if "comparison.json" in by and "reference.json" in by:
        rj, cj = _j(by["reference.json"]) or {}, _j(by["comparison.json"]) or {}
        kinds.append("reference_music")
        facts.append(f"reference {Path(rj.get('reference', '?')).name}: {len(cj.get('takes', []))} generated take(s), " + ("one close enough" if cj.get("passed") else "none close enough by measurement") + f"; prompt: {rj.get('prompt', '')[:140]}")
    if "ranking.json" in by and "reference.json" in by and "reference_music" not in kinds:
        rj, rk = _j(by["reference.json"]) or {}, _j(by["ranking.json"]) or []
        kinds.append("reference_music")
        facts.append(f"reference {Path(rj.get('reference', '?')).name}: {len(rk)} library tracks ranked; closest: " + ", ".join(r["track"] for r in rk[:3]))
    elif "ranking.json" in by and "reference.json" in by:
        rk = _j(by["ranking.json"]) or []
        facts.append(f"{len(rk)} library tracks also ranked; closest: " + ", ".join(r["track"] for r in rk[:3]))
    if "hold_change.json" in by:
        h = _j(by["hold_change.json"]) or {}
        kinds.append("callout_change")
        w, n = h.get("was", {}), h.get("now", {})
        facts.append(f"hold {w.get('hold')}s -> {n.get('hold')}s; title \"{w.get('title')}\" -> \"{n.get('title')}\"; second line \"{w.get('subtitle', '')[:30]}\" -> \"{n.get('subtitle', '')[:30]}\"")
    if "replacement.json" in by:
        r = _j(by["replacement.json"]) or {}
        kinds.append("sfx_replacement")
        facts.append("; ".join(f"note {e['note']}: {'applied' if e['applied'] else 'not applied'}, {e['reason'][:120]}" for e in r.get("ledger", [])))
    if "audio.json" in by:
        a = _j(by["audio.json"]) or {}
        kinds.append("audio")
        g = a.get("generated", {})
        facts.append(f"window {_fmt(a.get('window', {}).get('start', 0))}-{_fmt(a.get('window', {}).get('end', 0))}; effect at {a.get('callout_sec', 0):.2f}s; music {'from a reference track' if a.get('music_reference') else 'from a prompt'}"
                     + (f"; effect replaced by note {a['replaced']['note']}" if a.get("replaced") else ""))
        if (folder / "audio_preview.mp4").exists():
            link("Audio preview", folder / "audio_preview.mp4")
    pl = _j(by["placement.json"]) if "placement.json" in by else None
    if pl:
        if pl.get("kind") == "captions":
            cj = _j(by["captions.json"]) if "captions.json" in by else {}
            kinds.append("captions")
            facts.append(f"{len((cj or {}).get('groups', []))} lines over {_fmt((cj or {}).get('window', {}).get('start', 0))}-{_fmt((cj or {}).get('window', {}).get('end', 0))}; style {(cj or {}).get('style', '?')}")
            if (folder / "captions_preview.mp4").exists():
                link("Captions preview", folder / "captions_preview.mp4")
        elif pl.get("kind") == "image_card":
            kinds.append("image_card")
            facts.append(f"{Path(pl.get('image', '?')).name}, anchored at {pl.get('anchor', {}).get('source_sec')}s in {pl.get('anchor', {}).get('source')}; caption {pl.get('caption')!r}; hold {pl.get('hold_sec')}s")
        elif pl.get("kind") == "audio":
            pass
        elif pl.get("geometry") and pl.get("anchor"):
            kinds.append("callout")
            facts.append(f"\"{pl.get('title')}\" / \"{pl.get('subtitle')}\" at {pl.get('place_overlay_on_timeline_at_sec', 0):.2f}s for {pl.get('duration_sec', 0):.1f}s, anchored to {pl['anchor'].get('source')} at {pl['anchor'].get('source_sec')}s")
            if (folder / "overlay_preview.mp4").exists():
                link("Callout preview", folder / "overlay_preview.mp4")
    if "ops.json" in by and "changes.json" in by:
        c = _j(by["changes.json"]) or {}
        items = c.get("items", [])
        kinds.append("revision")
        facts.append(f"{c.get('from_label', '?')} -> {c.get('to_label', '?')}: {sum(1 for i in items if i.get('applied'))} of {len(items)} notes applied on the timeline"
                     + (f"; {len(c['layer_warnings'])} layer warning(s)" if c.get("layer_warnings") else ""))
    for name in ("review.html",):
        rp = next((p for p in files if p.name == name), None)
        if rp:
            tl = _j(rp.parent / "timeline.json") or {}
            if tl:
                if "review_page" not in kinds:
                    kinds.append("review_page")
                lanes = ", ".join(l["name"] for l in tl.get("beatmap", []))
                facts.append(f"review page: {len(tl.get('clips', []))} clips, {_fmt(tl.get('duration', 0))}" + (f"; lanes: {lanes}" if lanes else "") + (f"; layers: {len(tl.get('layers', []))}" if tl.get("layers") else ""))
                link("Review page", rp)
    for p in files:
        if p.name.endswith("notes.json") or p.name == "review_notes.json" or p.name.startswith("ryan_notes"):
            nj = _j(p) or {}
            if isinstance(nj, dict) and "notes" in nj:
                if not kinds:
                    kinds.append("notes")
                stand_in = stand_in or "_stand_in" in nj
                facts.append(f"{len(nj['notes'])} note(s) in {p.name}" + (" (written by Claude as a stand-in, NOT by Ryan)" if "_stand_in" in nj else " (from Ryan)"))
    if not kinds:
        kinds.append("other")
        facts.append(f"{len(files)} file(s)")
    mt = max((p.stat().st_mtime for p in files), default=folder.stat().st_mtime)
    primary = min(kinds, key=KINDS.index)
    return {"folder": folder.name, "kind": primary, "kinds": kinds, "label": LABEL[primary], "facts": facts, "links": links, "stand_in": stand_in, "modified": mt,
            "modified_text": dt.datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M")}


def scan(root: Path, name: str) -> list[dict]:
    entries = [describe(d) for d in sorted(root.iterdir()) if d.is_dir() and d.name.lower().startswith(name.lower())]
    return sorted(entries, key=lambda e: -e["modified"])


def latest(entries: list[dict]) -> dict:
    """The newest folder holding an XML with layers, and the newest QA and review page."""
    def newest(pred):
        return next((e for e in entries if pred(e)), None)
    return {"layered_cut": newest(lambda e: any("with layers" in l["label"] for l in e["links"])),
            "qa": newest(lambda e: "qa" in e["kinds"]), "review_page": newest(lambda e: "review_page" in e["kinds"])}


def render(entries: list[dict], name: str, root: Path) -> str:
    def href(rel):
        return quote(rel)

    def card(e, brief=False):
        ls = " ".join(f'<a href="{href(l["path"])}">{html.escape(l["label"])}</a>' for l in e["links"])
        facts = "".join(f"<li>{html.escape(f)}</li>" for f in (e["facts"][:1] if brief else e["facts"]))
        flag = ' <span class="flag">stand-in notes, not Ryan\'s</span>' if e["stand_in"] else ""
        return (f'<section class="card"><div class="hd"><b>{html.escape(e["label"])}</b> <span class="mt">{e["modified_text"]}</span>{flag}</div>'
                f'<div class="fd">{html.escape(e["folder"])}</div><ul>{facts}</ul><div class="ln">{ls}</div></section>')
    lt = latest(entries)
    top = "".join(f'<div class="lt"><span>{k}</span>{card(v, True) if v else "<p>none yet</p>"}</div>' for k, v in (("Newest cut with layers", lt["layered_cut"]), ("Newest QA pass", lt["qa"]), ("Newest review page", lt["review_page"])))
    groups = ""
    for kind in KINDS:
        es = [e for e in entries if e["kind"] == kind]
        if es:
            groups += f'<h2>{html.escape(LABEL[kind])} ({len(es)})</h2>' + "".join(card(e) for e in es)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(name)} project index</title><style>
:root{{color-scheme:dark;--bg:#0d1420;--panel:#141d2c;--ink:#e8edf5;--mute:#8b98ad;--line:#25324a;--acc:#0391d8}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Helvetica,Arial,sans-serif}}main{{max-width:1000px;margin:0 auto;padding:20px 16px 60px}}
h1{{font-size:20px;margin:0 0 4px}}h2{{font-size:16px;margin:28px 0 8px;color:var(--mute);font-weight:600}}.sub{{color:var(--mute);margin:0 0 16px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:8px 0}}.hd{{display:flex;gap:10px;align-items:baseline}}.mt{{color:var(--mute);font-size:13px}}
.fd{{color:var(--mute);font-size:12px}}ul{{margin:6px 0 4px;padding-left:18px}}a{{color:var(--acc);margin-right:12px}}.ln{{margin-top:4px}}
.flag{{background:#f4690b;color:#0d1420;border-radius:99px;padding:1px 8px;font-size:12px;font-weight:700}}.lt span{{color:var(--mute);font-size:13px}}
</style></head><body><main><h1>{html.escape(name)}: project index</h1>
<p class="sub">Everything made for this project, read from its folders in place ({len(entries)} folders). It says what exists and what it found, not whether it has been approved. Nothing was moved.</p>
{top}{groups}</main></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--name", required=True, help="the project's folder-name prefix, e.g. 'Runnells Tiling'")
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()
    root = a.root.expanduser()
    if not root.is_dir():
        print(f"REFUSING: {root} is not a folder", file=sys.stderr)
        return 1
    entries = scan(root, a.name)
    if not entries:
        print(f"REFUSING: no folders starting with '{a.name}' in {root}", file=sys.stderr)
        return 1
    page = root / f"{a.name} - project index.html"
    page.write_text(render(entries, a.name, root))
    (root / f"{a.name} - project.json").write_text(json.dumps({"project": a.name, "root": str(root), "folders": entries}, indent=2))
    by = {}
    for e in entries:
        by[e["label"]] = by.get(e["label"], 0) + 1
    print(f"{len(entries)} folders indexed in place:")
    for k, v in sorted(by.items(), key=lambda kv: -kv[1]):
        print(f"  {v:3d}  {k}")
    print(f"stand-in notes flagged in {sum(e['stand_in'] for e in entries)} folder(s)\nindex: {page}")
    if a.open:
        subprocess.run(["open", str(page)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
