"""One page linking every recruitment-selects unit built so far (standalone).

    python3 labs/recruit/index_page.py --root "<folder holding one sub-folder per unit, each with selects.json and selects.html>" --out index.html

Each unit's counts are read from its selects.json: moments by audience, how many have picture and how many are audio only, how many headlines verified. Nothing is invented: a unit with no selects.json is not listed."""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path


def summarize(rows: list[dict]) -> dict:
    by = {k: sum(1 for r in rows if r.get("audience") == k) for k in ("subcontractors", "franchisees", "both")}
    return {"moments": len(rows), **by, "audio_only": sum(1 for r in rows if r.get("audio_only")),
            "verified": sum(1 for r in rows if r.get("status") == "VERIFIED"), "flagged": sum(1 for r in rows if r.get("flags"))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", action="append", default=[], help="Folder=Title, to name a unit")
    a = ap.parse_args(argv)
    root = Path(a.root).expanduser()
    titles = dict(t.split("=", 1) for t in a.title)
    units = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if (d / "selects.json").exists() and (d / "selects.html").exists():
            units.append((d, summarize(json.loads((d / "selects.json").read_text()))))
    if (root / "selects.json").exists() and (root / "selects.html").exists():
        units.insert(0, (root, summarize(json.loads((root / "selects.json").read_text()))))
    if not units:
        print(f"no units with a selects.json under {root}", file=sys.stderr)
        return 1
    css = "body{font:16px -apple-system,Helvetica,sans-serif;margin:0;background:#f6f8fa;color:#033459}header{background:#033459;color:#fff;padding:22px 28px}main{padding:20px 28px;max-width:900px}a.card{display:block;background:#fff;border:1px solid #d7dde3;border-radius:8px;padding:14px 18px;margin:12px 0;text-decoration:none;color:inherit}a.card:hover{border-color:#0391d8}.t{font-size:18px;font-weight:600}.m{color:#555;margin-top:4px}"
    parts = [f"<!doctype html><meta charset=utf-8><title>Recruitment selects</title><style>{css}</style><header><h1>Recruitment selects</h1>Footage that could help recruit franchisees and hire subcontractors. One page per interview.</header><main>"]
    tot = {"moments": 0, "verified": 0}
    for d, s in units:
        rel = "selects.html" if d == root else f"{d.name}/selects.html"
        name = titles.get(d.name, d.name if d != root else root.name)
        parts.append(f"<a class=card href=\"{html.escape(rel)}\"><div class=t>{html.escape(name)}</div><div class=m>{s['moments']} moments: {s['subcontractors']} for subcontractors, {s['franchisees']} for franchisees and partners, {s['both']} for both. "
                     f"{s['audio_only']} audio only. {s['verified']} of {s['moments']} headlines verified. {s['flagged']} carry a flag to read.</div></a>")
        tot["moments"] += s["moments"]
        tot["verified"] += s["verified"]
    parts.append(f"<p>{tot['moments']} moments in all, {tot['verified']} verified. Nothing here is placed in a cut.</p></main>")
    Path(a.out).expanduser().write_text("".join(parts))
    print(f"{len(units)} unit(s), {tot['moments']} moments -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
