"""Propose B-roll for a cut's speech lines from a pool (see pool.py), as a contact sheet Ryan can judge. Nothing is placed in any XML here.

    python3 labs/broll/suggest.py --pool "<pool folder>" [--pool "<another>"] --captions "<captions.json | lines.json>" --out "<folder>" [--exclude NAME] [--vision] [--check "words=expected"]

Each speech line is turned into a CLIP text embedding and compared with every frame in the pool. A line gets suggestions only when its best frame clears BOTH bars:
  SCORE_MIN   the frame is a plausible picture of the words (CLIP cosine, calibrated below), and
  MARGIN_MIN  it stands out from the pool (best score minus the median score for that line), so a line that is "like everything and nothing" gets nothing.
With `--vision` the score bars only decide which few frames are worth a closer look (a floor of 0.20 and the best 5, one per clip); each is then shown to a model (judge.py) that says whether it clearly
shows what the line is about and whether it carries burned-in text, and a line is a match only if a frame passes both. That is the fix for the two failures seen without it: a shared word in a burned-in
caption, and "a weak fit" that is really no fit. `--exclude NAME` leaves out any pool file whose path contains NAME (a cut's own finished export, or the folder of its own shoot).
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

import judge as jg
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


def load_pools(pool_dirs) -> tuple[np.ndarray, list[dict]]:
    """Every frame of every pool, with the pool folder each came from (its image file lives there)."""
    dirs = [pool_dirs] if isinstance(pool_dirs, Path) else list(pool_dirs)
    embs, frames = [], []
    for d in dirs:
        meta = json.loads((d / "pool.json").read_text())
        embs.append(np.load(d / "embeddings.npy"))
        frames += [{**f, "pool": str(d.resolve())} for f in meta["frames"]]
    return np.concatenate(embs), frames


def _ser(c: dict, verdict: dict | None = None) -> dict:
    return {"file": c["file"], "time": c["time"], "frame": c["frame"], "kind": c["kind"], "pool": c.get("pool", ""),
            "score": None if c.get("score") is None else round(c["score"], 3), "why": c.get("why", ""), "verdict": verdict}


def suggest(pool_dirs, lines: list[dict], top: int = 3, exclude: tuple[str, ...] = (), vision: bool = False, ask=None, vision_top: int = 5,
            vision_floor: float = 0.20, cache_path: Path | None = None, progress=None) -> list[dict]:
    """`exclude`: fragments of a file's path left out of the candidates (a cut's own finished export would otherwise 'match' itself).
    `vision`: confirm candidates with the model in judge.py (`ask` is injectable for tests); a line it cannot check (CLI error) is `none` and says so."""
    emb, frames = load_pools(pool_dirs)
    if exclude:
        keep = [i for i, f in enumerate(frames) if not any(x.lower() in str(f["file"]).lower() for x in exclude)]
        emb, frames = emb[keep], [frames[i] for i in keep]
    qs = pl.embed_texts([l["text"] for l in lines])
    cache = jg.load_cache(cache_path) if (vision and cache_path) else ({} if vision else None)
    out = []
    for n, (l, q) in enumerate(zip(lines, qs), 1):
        ranked, med = rank(emb, q, top)
        named = named_pages(l["text"], frames)
        row = {**l, "median": round(med, 3)}
        if not vision:
            cands = pick(ranked, frames, top)
            looks = bool(cands and judge(cands[0]["score"], med))
            cands = named + [c for c in cands if not named or c["file"] != named[0]["file"]][:top - len(named)]
            row.update(match=bool(named) or looks, how="named page" if named else ("looks like the words" if looks else "none"), candidates=[_ser(c) for c in cands])
        else:
            pc = [c for c in pick(ranked, frames, vision_top + 1) if c["score"] >= vision_floor and not (named and c["file"] == named[0]["file"])][:vision_top]
            verdicts, err = [], ""
            if pc:
                try:
                    verdicts = jg.judge_line(l["text"], [Path(c["pool"]) / "frames" / c["frame"] for c in pc], ask or jg.ask_cli, cache)
                except jg.JudgeError as e:
                    err, verdicts = str(e), [None] * len(pc)
                if progress:
                    progress(n, len(lines), l, bool(err))
            kept = [(c, v) for c, v in zip(pc, verdicts) if v and v["fits"] and not v["text_overlay"]]
            if kept or named:
                shown = [_ser(c) for c in named] + [_ser(c, v) for c, v in kept]
            else:
                shown = [_ser(c, v) for c, v in list(zip(pc, verdicts))[:3]]
            row.update(match=bool(named) or bool(kept), how="named page" if named else ("confirmed by vision" if kept else "none"), candidates=shown, checked=len(pc))
            if err:
                row["vision_error"] = err
        out.append(row)
    if vision and cache_path and cache is not None:
        jg.save_cache(cache_path, cache)
    return out


def timecode(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def sheet(rows: list[dict], pool_dir: Path, out: Path, checks: list[dict]) -> Path:
    css = ("body{font:15px -apple-system,Helvetica,sans-serif;margin:24px;background:#fff;color:#033459}h1{margin:0 0 4px}.sub{color:#555;margin-bottom:20px;max-width:900px}"
           ".line{border-top:1px solid #ddd;padding:14px 0}.t{font-weight:600}.tag{display:inline-block;font-size:12px;padding:1px 8px;border-radius:9px;margin-left:8px;color:#fff}"
           ".yes{background:#0391d8}.no{background:#999}.cands{display:flex;gap:12px;margin-top:8px;flex-wrap:wrap}.c{width:210px;font-size:12px}.c img{width:210px;border:1px solid #ccc;display:block}"
           ".none .c img{opacity:.45}.ok{color:#0a7d2c}.bad{color:#b00}.v{color:#555}")
    vision = any("checked" in r for r in rows)
    how = ("<b>Confirmed by vision</b> = a model looked at the frame and said it clearly shows what the line is about and has no text burned in. <b>Named page</b> = the speaker named a page of the website. "
           "<b>None</b> = nothing passed (the closest frames are shown dimmed with the model's reason). " if vision else
           f"<b>Match</b> = clears the score and stand-out bars (score &ge; {SCORE_MIN}, at least {MARGIN_MIN} above the pool's median). <b>None</b> = it did not, shown dimmed. ")
    parts = [f"<!doctype html><meta charset=utf-8><title>B-roll suggestions</title><style>{css}</style><h1>B-roll suggestions</h1>"
             f"<div class=sub>For each line of speech, frames from the pool. {how}Nothing is placed in the cut; tell me which suggestions you would actually use.</div>"]
    if checks:
        parts.append("<h2>Known-answer checks</h2>")
        for c in checks:
            parts.append(f"<div class=line><span class=t>“{html.escape(c['query'])}”</span> <span class='{'ok' if c['passed'] else 'bad'}'>{'found ' + html.escape(c['expect']) if c['passed'] else 'DID NOT find ' + html.escape(c['expect'])}</span><div class=cands>"
                         + "".join(_cand(x, pool_dir, out) for x in c["candidates"]) + "</div></div>")
    parts.append("<h2>Speech lines</h2>")
    for r in rows:
        err = f" <span class=bad>(vision check failed: {html.escape(r['vision_error'][:90])})</span>" if r.get("vision_error") else ""
        parts.append(f"<div class='line {'' if r['match'] else 'none'}'><span class=t>{timecode(r['start'])}–{timecode(r['end'])}</span> “{html.escape(r['text'])}”"
                     f"<span class='tag {'yes' if r['match'] else 'no'}'>{r.get('how', 'match') if r['match'] else 'none'}</span>{err}<div class=cands>"
                     + "".join(_cand(x, pool_dir, out) for x in r["candidates"]) + "</div></div>")
    out.mkdir(parents=True, exist_ok=True)
    p = out / "suggestions.html"
    p.write_text("".join(parts))
    return p


def _cand(c: dict, pool_dir: Path, out: Path) -> str:
    name = Path(c["file"]).name
    at = "" if c["kind"] == "image" else f" @ {timecode(c['time'])}"
    rel = os.path.relpath(Path(c.get("pool") or pool_dir).resolve(), out.resolve())          # the page sits in `out`; each image is in its own pool folder
    v = c.get("verdict")
    note = "" if not v else f"<div class=v>{'&#10003;' if v['fits'] and not v['text_overlay'] else '&#10007;'} {html.escape(v['why'])}{' (text on the picture)' if v['text_overlay'] else ''}</div>"
    label = "the line names this page" if c.get("why") == "named" else ("" if c.get("score") is None else format(c["score"], ".3f"))
    return f"<div class=c><img src=\"{rel}/frames/{html.escape(c['frame'])}\"><b>{label}</b> {html.escape(name[:46])}{at}{note}</div>"


def run_checks(pool_dirs, checks: list[tuple[str, str]], top: int = 3) -> list[dict]:
    emb, frames = load_pools(pool_dirs)
    out = []
    for query, expect in checks:
        q = pl.embed_texts([query])[0]
        ranked, _med = rank(emb, q, top)
        cands = pick(ranked, frames, top)
        out.append({"query": query, "expect": expect, "passed": bool(cands and any(e.strip().lower() in Path(cands[0]["file"]).name.lower() for e in expect.split("|"))),
                    "candidates": [_ser(c) for c in cands]})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", action="append", required=True, help="a pool folder from pool.py (repeat to search several together)")
    ap.add_argument("--captions", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--exclude", action="append", default=[], help="leave out pool files whose path contains this (the cut's own finished export, or its own shoot's folder)")
    ap.add_argument("--vision-top", type=int, default=5, help="how many CLIP candidates (one per clip) the model looks at per line; more finds more in a big pool and costs more (default 5)")
    ap.add_argument("--vision", action="store_true", help="confirm candidates with a model (judge.py); about 15-40 s per line, free via the claude CLI")
    ap.add_argument("--check", action="append", default=[], help='"query=expected file-name fragment", e.g. "aerial drone footage=DRONE|Rio Cibolo" (any fragment after | also counts)')
    a = ap.parse_args(argv)
    try:
        pools, out = [Path(x).expanduser() for x in a.pool], Path(a.out).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        note = lambda n, total, l, bad: print(f"  judged {n}/{total}{' (CHECK FAILED)' if bad else ''}: {l['text'][:50]}", flush=True)
        rows = suggest(pools, load_lines(Path(a.captions).expanduser()), a.top, tuple(a.exclude), a.vision, vision_top=a.vision_top, cache_path=out / "judge_cache.json", progress=note)
        checks = run_checks(pools, [tuple(c.rsplit("=", 1)) for c in a.check], a.top) if a.check else []
    except (pl.PoolError, FileNotFoundError) as e:
        print(f"B-roll suggestions: {e}", file=sys.stderr)
        return 1
    page = sheet(rows, pools[0], out, checks)
    (out / "suggestions.json").write_text(json.dumps({"lines": rows, "checks": checks, "vision": a.vision, "score_min": SCORE_MIN, "margin_min": MARGIN_MIN}, indent=1))
    for c in checks:
        print(f"check {'PASS' if c['passed'] else 'FAIL'}: {c['query']!r} -> {Path(c['candidates'][0]['file']).name} {c['candidates'][0]['score']}")
    for r in rows:
        best = r["candidates"][0] if r["candidates"] else None
        print(f"{(r['how'].upper()[:5]) if r['match'] else 'none ':5} {r['start']:5.1f}s {r['text'][:46]:46} {Path(best['file']).name[:34] if best else ''} {('@%.1f' % best['time']) if best else ''}")
    print(f"{sum(r['match'] for r in rows)} of {len(rows)} lines have a suggestion -> {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
