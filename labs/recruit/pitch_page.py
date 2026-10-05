"""A page of reel pitches built only from verified moments (standalone; nothing is cut into a project).

    python3 labs/recruit/pitch_page.py --pitches pitches.json --units "May15=<folder with selects.json>" --units "Jun13=<folder>" --out reel_pitches.html

`pitches.json`: {"intro": str, "recommendation": str, "pitches": [{"title", "audience", "why", "hook": {"pattern", "line", "source"}, "beats": [{"unit", "id", "role", "line", "at"}], "close", "estimate", "needs_from_ryan": [..]}]}.
Each beat names a moment (unit + id) and the exact line used. The line is checked here to be a verbatim part of that moment's transcript text (normalised for case and punctuation) and the page REFUSES to build if one is not: a pitch never
quotes words the transcript does not contain. Each beat plays that moment's preview clip. The page states the speech length per beat and sums it; the finished length is an estimate, not a measurement."""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", s.lower())).strip()


def check_line(line: str, text: str) -> bool:
    return norm(line) in norm(text)


def load_units(specs: list[str]) -> dict[str, dict]:
    units = {}
    for sp in specs:
        name, _, path = sp.partition("=")
        folder = Path(path).expanduser()
        rows = {r["id"]: r for r in json.loads((folder / "selects.json").read_text())}
        units[name] = {"folder": folder, "rows": rows}
    return units


def validate(pitches: dict, units: dict) -> list[str]:
    """Every problem found, as sentences: an unknown unit or moment, or a line that is not in the moment's transcript."""
    bad = []
    for p in pitches["pitches"]:
        for b in p["beats"] + ([{"unit": p["hook"]["unit"], "id": p["hook"]["id"], "line": p["hook"]["line"]}] if p.get("hook", {}).get("id") else []):
            u = units.get(b["unit"])
            row = u["rows"].get(b["id"]) if u else None
            if row is None:
                bad.append(f"{p['title']}: no moment {b['unit']}/{b['id']}")
            elif not check_line(b["line"], row["text"]):
                bad.append(f"{p['title']}: the line is not in {b['id']}'s transcript: {b['line'][:70]!r}")
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pitches", required=True)
    ap.add_argument("--units", action="append", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    data = json.loads(Path(a.pitches).expanduser().read_text())
    units = load_units(a.units)
    problems = validate(data, units)
    if problems:
        print("REFUSING to build: " + "; ".join(problems), file=sys.stderr)
        return 1
    out = Path(a.out).expanduser()
    esc = html.escape
    css = ("body{font:16px -apple-system,Helvetica,sans-serif;margin:0;background:#f6f8fa;color:#033459}header{background:#033459;color:#fff;padding:22px 28px}h1{margin:0 0 6px}.sub{max-width:900px;opacity:.92}"
           "main{padding:20px 28px;max-width:1100px}.pitch{background:#fff;border:1px solid #d7dde3;border-radius:8px;padding:16px 20px;margin:20px 0}.pitch h2{margin:0 0 4px}"
           ".tag{display:inline-block;font-size:12px;padding:1px 9px;border-radius:9px;background:#0391d8;color:#fff;margin-right:6px}.hook{background:#eef6fc;border-left:4px solid #0391d8;padding:8px 12px;margin:10px 0}"
           ".beat{display:grid;grid-template-columns:300px 1fr;gap:14px;margin:10px 0;padding-top:10px;border-top:1px solid #e3e8ed}@media(max-width:800px){.beat{grid-template-columns:1fr}}video,audio{width:100%;border-radius:6px;background:#000}"
           ".role{font-weight:600}.q{font-size:16px;margin:2px 0}.meta{font-size:12px;color:#555}.warn{background:#fff3e8;border-left:4px solid #f4690b;padding:4px 10px;margin:4px 0;font-size:13px}.est{font-weight:600;margin-top:8px}")
    parts = [f"<!doctype html><meta charset=utf-8><title>Reel pitches</title><style>{css}</style><header><h1>Reel pitches from the recruitment footage</h1><div class=sub>{esc(data['intro'])}</div></header><main>"
             f"<div class=pitch><b>Where I would start:</b> {esc(data['recommendation'])}</div>"]
    for n, p in enumerate(data["pitches"], 1):
        total = 0.0
        beats = []
        for b in p["beats"]:
            row = units[b["unit"]]["rows"][b["id"]]
            d = row["speech_end"] - row["speech_start"]
            total += b.get("seconds", d)
            clip = row.get("clip_file")
            rel = f"{units[b['unit']]['folder'].name}/{clip}" if clip else None
            media = ""
            if rel:
                path_rel = Path(units[b["unit"]]["folder"]) / clip
                try:
                    shown = path_rel.relative_to(out.parent)
                except ValueError:
                    shown = path_rel
                tagname = "audio" if row.get("audio_only") else "video"
                media = f"<{tagname} controls preload=none src=\"{esc(str(shown))}\"></{tagname}>"
            fl = "".join(f"<div class=warn>{esc(f)}</div>" for f in row.get("flags", []))
            beats.append(f"<div class=beat><div>{media}<div class=meta>{esc(row.get('where', ''))}</div></div><div><div class=role>{n}.{len(beats) + 1} {esc(b['role'])} "
                         f"<span class=meta>({esc(b['id'])}, about {b.get('seconds', d):.0f} s of speech{'; ' + esc(b['at']) if b.get('at') else ''})</span></div>"
                         f"<div class=q>&ldquo;{esc(b['line'])}&rdquo;</div>{fl}</div></div>")
        needs = "".join(f"<li>{esc(x)}</li>" for x in p.get("needs_from_ryan", []))
        h = p["hook"]
        parts.append(f"<div class=pitch><h2>{n}. {esc(p['title'])}</h2><span class=tag>{esc(p['audience'])}</span> {esc(p['why'])}"
                     f"<div class=hook><b>Hook ({esc(h['pattern'])}):</b> {esc(h['text'])}</div>{''.join(beats)}"
                     f"<div class=est>Speech in these beats: about {total:.0f} s. {esc(p['estimate'])}</div><div><b>Close:</b> {esc(p['close'])}</div>"
                     f"{'<div><b>Before this ships:</b><ul>' + needs + '</ul></div>' if needs else ''}</div>")
    parts.append("</main>")
    out.write_text("".join(parts))
    print(f"{len(data['pitches'])} pitches -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
