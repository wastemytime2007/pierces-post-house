"""Propose B-roll for a cut's speech lines from a pool (see pool.py), as a contact sheet Ryan can judge. Nothing is placed in any XML here.

    python3 labs/broll/suggest.py --pool "<pool folder>" --captions "<captions.json>" --out "<folder>" [--top 3] [--check "aerial drone footage"]

Each speech line is turned into a CLIP text embedding and compared with every frame in the pool. A line gets suggestions only when its best frame clears BOTH bars:
  SCORE_MIN   the frame is a plausible picture of the words (CLIP cosine, calibrated below), and
  MARGIN_MIN  it stands out from the pool (best score minus the median score for that line), so a line that is "like everything and nothing" gets nothing.
Lines that clear neither bar are listed with their best frame anyway and marked NONE, so the sheet shows what was rejected and why, not only what passed.

`--check "<words>"` runs a query whose right answer is known (for instance "aerial drone footage" must find the drone reel) so the matcher is shown working on a case
with a known answer before anyone trusts what it says about the real lines.

Limits: CLIP matches how a frame LOOKS to the words. It does not know what was meant, who is speaking, or whether a clip is cleared for this video; a still
every 3 s stands in for a whole clip; abstract or conversational lines (most of what people say) rarely match a picture, which is why 'no match' is a normal answer."""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path

import numpy as np

import pool as pl

SCORE_MIN = 0.30                  # real matches seen so far score 0.33+ (checked by looking at the frames); 16 conversational lines scored 0.22-0.27 and none of those was a real match
MARGIN_MIN = 0.06


# What a speaker says when naming a page of the website -> that page's capture (its file stem in the pool). Specific pages first; the home page only on a bare mention of the site.
NAMED_PAGES = [
    ("reviews", r"\breviews?\b|\btestimonials?\b|\bfive[- ]star\b"),
    ("compare", r"\bcompare\b|\bcomparison\b"),
    ("how_it_works", r"\bhow it works\b|\bhow we buy\b"),
    ("faq", r"\bfaqs?\b|\bfrequently asked\b"),
    ("get_a_cash_offer_today", r"\bcash offer\b"),
    ("about_us", r"\babout us\b|\bour company\b"),
    ("contact_us", r"\bcontact us\b"),
    ("press", r"\bpress\b|\bas seen\b"),
    ("blog", r"\bblog\b"),
    ("home", r"\bwebsite\b|\bsoldfast\.com\b|\bour site\b"),
]


def named_pages(text: str, frames: list[dict]) -> list[dict]:
    """Frames of captured web pages that the line names (by the words people use for them). One page per line, the most specific one: a line about reviews
    gets the reviews page, not the home page, even though it also says 'website'."""
    for stem, pat in NAMED_PAGES:
        if re.search(pat, text, re.I):
            for i, f in enumerate(frames):
                if f["kind"] == "image" and Path(f["file"]).stem == stem and "captures" in str(Path(f["file"]).parent).lower():
                    return [{"i": i, "score": None, "why": "named", **f}]
    return []


def load_lines(path: Path) -> list[dict]:
    """[{text, start, end}] from a captions.json (groups with show_start/show_end) or a plain list of such objects."""
    d = json.loads(path.read_text())
    groups = d["groups"] if isinstance(d, dict) and "groups" in d else d
    out = []
    for g in groups:
        s = g.get("show_start", g.get("start"))
        e = g.get("show_end", g.get("end"))
        if g.get("text") and s is not None and e is not None:
            out.append({"text": g["text"].strip(), "start": float(s), "end": float(e)})
    if not out:
        raise pl.PoolError(f"no speech lines found in {path}")
    return out


def rank(emb: np.ndarray, q: np.ndarray, top: int, one_per_file: bool = True) -> list[dict]:
    """The best frames for one query vector. Best frame per source file first (three frames of one clip are one suggestion, not three)."""
    sc = emb @ q
    order = np.argsort(-sc)
    return [{"i": int(i), "score": float(sc[i])} for i in order], float(np.median(sc))


def pick(ranked: list[dict], frames: list[dict], top: int, one_per_file: bool = True) -> list[dict]:
    out, seen = [], set()
    for r in ranked:
        f = frames[r["i"]]
        if one_per_file and f["file"] in seen:
            continue
        seen.add(f["file"])
        out.append({**r, **f})
        if len(out) >= top:
            break
    return out


def judge(best: float, median: float) -> bool:
    return best >= SCORE_MIN and (best - median) >= MARGIN_MIN


def suggest(pool_dir: Path, lines: list[dict], top: int = 3, exclude: tuple[str, ...] = ()) -> list[dict]:
    """`exclude`: file-name fragments left out of the candidates (a cut's own finished export would otherwise 'match' itself)."""
    meta = json.loads((pool_dir / "pool.json").read_text())
    emb = np.load(pool_dir / "embeddings.npy")
    frames = meta["frames"]
    if exclude:
        keep = [i for i, f in enumerate(frames) if not any(x.lower() in Path(f["file"]).name.lower() for x in exclude)]
        emb, frames = emb[keep], [frames[i] for i in keep]
    qs = pl.embed_texts([l["text"] for l in lines])
    out = []
    for l, q in zip(lines, qs):
        ranked, med = rank(emb, q, top)
        cands = pick(ranked, frames, top)
        named = named_pages(l["text"], frames)
        looks = bool(cands and judge(cands[0]["score"], med))
        cands = named + [c for c in cands if not named or c["file"] != named[0]["file"]][:top - len(named)]
        out.append({**l, "median": round(med, 3), "match": bool(named) or looks, "how": "named page" if named else ("looks like the words" if looks else "none"),
                    "candidates": [{"file": c["file"], "time": c["time"], "frame": c["frame"], "kind": c["kind"],
                                    "score": None if c.get("score") is None else round(c["score"], 3), "why": c.get("why", "")} for c in cands]})
    return out


def timecode(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def sheet(rows: list[dict], pool_dir: Path, out: Path, checks: list[dict]) -> Path:
    frames_rel = Path(os.path.relpath(pool_dir.resolve(), out.resolve()))          # the page sits in `out`; its images are in the pool folder
    css = ("body{font:15px -apple-system,Helvetica,sans-serif;margin:24px;background:#fff;color:#033459}h1{margin:0 0 4px}.sub{color:#555;margin-bottom:20px;max-width:900px}"
           ".line{border-top:1px solid #ddd;padding:14px 0}.t{font-weight:600}.tag{display:inline-block;font-size:12px;padding:1px 8px;border-radius:9px;margin-left:8px;color:#fff}"
           ".yes{background:#0391d8}.no{background:#999}.cands{display:flex;gap:12px;margin-top:8px;flex-wrap:wrap}.c{width:210px;font-size:12px}.c img{width:210px;border:1px solid #ccc;display:block}"
           ".none .c img{opacity:.45}.ok{color:#0a7d2c}.bad{color:#b00}")
    parts = [f"<!doctype html><meta charset=utf-8><title>B-roll suggestions</title><style>{css}</style><h1>B-roll suggestions</h1>"
             f"<div class=sub>For each line of speech: the frames from the pool that look most like the words. <b>Match</b> = clears the score and stand-out bars (score &ge; {SCORE_MIN}, "
             f"at least {MARGIN_MIN} above the pool's median). <b>None</b> = it did not, shown dimmed so you can see what was rejected. Nothing is placed in the cut; "
             f"tell me which suggestions you would actually use.</div>"]
    if checks:
        parts.append("<h2>Known-answer checks</h2>")
        for c in checks:
            parts.append(f"<div class=line><span class=t>“{html.escape(c['query'])}”</span> <span class='{'ok' if c['passed'] else 'bad'}'>{'found ' + html.escape(c['expect']) if c['passed'] else 'DID NOT find ' + html.escape(c['expect'])}</span><div class=cands>"
                         + "".join(_cand(x, frames_rel) for x in c["candidates"]) + "</div></div>")
    parts.append("<h2>Speech lines</h2>")
    for r in rows:
        parts.append(f"<div class='line {'' if r['match'] else 'none'}'><span class=t>{timecode(r['start'])}–{timecode(r['end'])}</span> “{html.escape(r['text'])}”"
                     f"<span class='tag {'yes' if r['match'] else 'no'}'>{r.get('how', 'match') if r['match'] else 'none'}</span><div class=cands>"
                     + "".join(_cand(x, frames_rel) for x in r["candidates"]) + "</div></div>")
    out.mkdir(parents=True, exist_ok=True)
    p = out / "suggestions.html"
    p.write_text("".join(parts))
    return p


def _cand(c: dict, frames_rel: Path) -> str:
    name = Path(c["file"]).name
    at = "" if c["kind"] == "image" else f" @ {timecode(c['time'])}"
    return (f"<div class=c><img src=\"{frames_rel}/frames/{html.escape(c['frame'])}\"><b>{'the line names this page' if c.get('why') == 'named' else format(c['score'], '.3f')}</b> {html.escape(name[:46])}{at}</div>")


def run_checks(pool_dir: Path, checks: list[tuple[str, str]], top: int = 3) -> list[dict]:
    meta = json.loads((pool_dir / "pool.json").read_text())
    emb = np.load(pool_dir / "embeddings.npy")
    out = []
    for query, expect in checks:
        q = pl.embed_texts([query])[0]
        ranked, _med = rank(emb, q, top)
        cands = pick(ranked, meta["frames"], top)
        out.append({"query": query, "expect": expect, "passed": bool(cands and any(e.strip().lower() in Path(cands[0]["file"]).name.lower() for e in expect.split("|"))),
                    "candidates": [{"file": c["file"], "time": c["time"], "frame": c["frame"], "kind": c["kind"], "score": round(c["score"], 3)} for c in cands]})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", required=True)
    ap.add_argument("--captions", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--exclude", action="append", default=[], help="leave out pool files whose name contains this (the cut's own finished export)")
    ap.add_argument("--check", action="append", default=[], help='"query=expected file-name fragment", e.g. "aerial drone footage=DRONE|Rio Cibolo" (any fragment after | also counts)')
    a = ap.parse_args(argv)
    try:
        pool_dir, out = Path(a.pool).expanduser(), Path(a.out).expanduser()
        rows = suggest(pool_dir, load_lines(Path(a.captions).expanduser()), a.top, tuple(a.exclude))
        checks = run_checks(pool_dir, [tuple(c.rsplit("=", 1)) for c in a.check], a.top) if a.check else []
    except (pl.PoolError, FileNotFoundError) as e:
        print(f"B-roll suggestions: {e}", file=sys.stderr)
        return 1
    page = sheet(rows, pool_dir, out, checks)
    (out / "suggestions.json").write_text(json.dumps({"lines": rows, "checks": checks, "score_min": SCORE_MIN, "margin_min": MARGIN_MIN}, indent=1))
    for c in checks:
        print(f"check {'PASS' if c['passed'] else 'FAIL'}: {c['query']!r} -> {Path(c['candidates'][0]['file']).name} {c['candidates'][0]['score']}")
    for r in rows:
        best = r["candidates"][0] if r["candidates"] else None
        print(f"{(r['how'].upper()[:5]) if r['match'] else 'none ':5} {r['start']:5.1f}s {r['text'][:46]:46} best {best['score'] if best else '-'} (median {r['median']}) {Path(best['file']).name[:30] if best else ''}")
    print(f"{sum(r['match'] for r in rows)} of {len(rows)} lines have a suggestion -> {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
