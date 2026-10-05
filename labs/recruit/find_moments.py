"""Locate named moments in clip transcripts: source file, in and out, and the transcript's own words (standalone; nothing in app/).

    python3 labs/recruit/find_moments.py --srt "<folder of per-clip .srt>" --moments moments.json --out located.json [--pad 1.5]

`moments.json` is a list of {id, audience, theme, why, start_anchor, end_anchor, flags?}. The two anchors are the words a moment starts and ends on (a phrase each, as heard: they need not match
the transcript exactly). For each moment the tool finds the best match for each anchor in the clips' segments, requires the end to follow the start in the same clip, and writes the source clip,
`start` and `end` (seconds, widened by `--pad`), and `text` copied from the transcript segments between them: the quote is never retyped, so it can be verified against the same SRT.

A moment whose anchor does not match well enough (ratio under MIN_RATIO), whose end does not follow its start in one clip, or that runs implausibly long (over MAX_SEC) is reported NOT FOUND with the
reason, never guessed. Matching is on lower-cased words with punctuation removed (difflib over the anchor and windows of 1 to 4 consecutive segments)."""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

MIN_RATIO = 0.62
MAX_SEC = 150.0


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", s.lower())).strip()


def parse_srt(path: Path) -> list[dict]:
    out = []
    for block in re.split(r"\n\s*\n", path.read_text().strip()):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", lines[1])
        if not m:
            continue
        a = [int(x) for x in m.groups()]
        out.append({"start": a[0] * 3600 + a[1] * 60 + a[2] + a[3] / 1000, "end": a[4] * 3600 + a[5] * 60 + a[6] + a[7] / 1000, "text": " ".join(lines[2:]).strip()})
    return out


def load_clips(folder: Path) -> dict[str, list[dict]]:
    return {p.stem: parse_srt(p) for p in sorted(folder.glob("*.srt"))}


def score(a: str, t: str) -> float:
    """How well the normalized anchor `a` matches the normalized window text `t`: the whole-string ratio, or (when the window is longer) the best-aligned stretch of it."""
    r = difflib.SequenceMatcher(None, a, t).ratio()
    if len(t) > len(a):
        m = difflib.SequenceMatcher(None, a, t).find_longest_match(0, len(a), 0, len(t))
        lo = max(0, m.b - m.a)
        r = max(r, difflib.SequenceMatcher(None, a, t[lo:lo + len(a) + 8]).ratio())
    return r


TRIM_TOLERANCE = 0.04


def best_match(anchor: str, clips: dict[str, list[dict]], max_window: int = 4, after: tuple[str, int] | None = None) -> dict | None:
    """The clip and segment range that best matches `anchor`, trimmed to its tightest form: a window that also holds the sentence before or after scores almost as well as the exact one, so
    segments are dropped from either end while the score stays within TRIM_TOLERANCE of the best (otherwise a moment starts with the interviewer's question).
    `after` = (clip, segment index): only consider matches at or after it in that clip."""
    a = norm(anchor)
    best = None
    for name, segs in clips.items():
        if after and name != after[0]:
            continue
        for i in range(after[1] if after else 0, len(segs)):
            for w in range(1, max_window + 1):
                if i + w > len(segs):
                    break
                t = norm(" ".join(s["text"] for s in segs[i:i + w]))
                if not t:
                    continue
                r = score(a, t)
                if best is None or r > best["ratio"]:
                    best = {"clip": name, "i": i, "w": w, "ratio": r}
    if best:
        segs = clips[best["clip"]]
        floor = after[1] if after and after[0] == best["clip"] else 0
        i, w, top = best["i"], best["w"], best["ratio"]
        while w > 1:
            if i + 1 >= floor and score(a, norm(" ".join(x["text"] for x in segs[i + 1:i + w]))) >= top - TRIM_TOLERANCE:
                i, w = i + 1, w - 1
            elif score(a, norm(" ".join(x["text"] for x in segs[i:i + w - 1]))) >= top - TRIM_TOLERANCE:
                w -= 1
            else:
                break
        best.update(i=i, w=w)
    return best


def locate(moment: dict, clips: dict[str, list[dict]], pad: float = 1.5) -> dict:
    out = {**moment}
    s = best_match(moment["start_anchor"], clips)
    if not s or s["ratio"] < MIN_RATIO:
        return {**out, "found": False, "reason": f"the start words were not found (best match {s['ratio'] if s else 0:.2f}, needs {MIN_RATIO})"}
    e = best_match(moment["end_anchor"], clips, after=(s["clip"], s["i"]))
    if not e or e["ratio"] < MIN_RATIO:
        return {**out, "found": False, "reason": f"the end words were not found after the start in {s['clip']} (best match {e['ratio'] if e else 0:.2f})"}
    segs = clips[s["clip"]]
    j_end = e["i"] + e["w"] - 1
    start, end = segs[s["i"]]["start"], segs[j_end]["end"]
    if end - start > MAX_SEC:
        return {**out, "found": False, "reason": f"start and end are {end - start:.0f} s apart, over the {MAX_SEC:.0f} s limit: the anchors probably matched different passages"}
    text = " ".join(x["text"] for x in segs[s["i"]:j_end + 1])
    return {**out, "found": True, "clip": s["clip"], "start": round(max(0.0, start - pad), 2), "end": round(end + pad, 2), "speech_start": round(start, 2), "speech_end": round(end, 2),
            "text": text, "start_ratio": round(s["ratio"], 2), "end_ratio": round(e["ratio"], 2)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--srt", required=True)
    ap.add_argument("--moments", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pad", type=float, default=1.5)
    a = ap.parse_args(argv)
    clips = load_clips(Path(a.srt).expanduser())
    if not clips:
        print(f"no .srt files in {a.srt}", file=sys.stderr)
        return 1
    moments = json.loads(Path(a.moments).expanduser().read_text())
    res = [locate(m, clips, a.pad) for m in moments]
    Path(a.out).expanduser().write_text(json.dumps(res, indent=1))
    for r in res:
        print(f"{'OK ' if r['found'] else 'NOT FOUND'} {r['id']:4} " + (f"{r['clip'][-24:]} {r['speech_start']:8.1f}-{r['speech_end']:8.1f}  ({r['start_ratio']}/{r['end_ratio']})" if r["found"] else r["reason"]))
    print(f"{sum(r['found'] for r in res)} of {len(res)} moments located")
    return 0


if __name__ == "__main__":
    sys.exit(main())
