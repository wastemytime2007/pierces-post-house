#!/usr/bin/env python3
"""Learn from how Ryan adjusts the bleeps, so the next automatic bleeps start closer.

    python3 labs/bleep/learn.py ingest-edits --edits ~/Downloads/bleep_edits.json --bleep-json "<the automatic run's bleep.json>" [--cut NAME]
    python3 labs/bleep/learn.py ingest-notes --notes ~/Downloads/review_notes.json
    python3 labs/bleep/learn.py fit        # re-fit the model from every record
    python3 labs/bleep/learn.py report     # what has been learned, and what has not yet

`apply_edits.py` calls ingest-edits and fit by itself whenever a set of edits is applied to an automatic result, so nothing has to be remembered.

WHAT IS RECORDED (one line per judged bleep in `learning/feedback.jsonl`; times only, never audio):
  kept / adjusted   the tool placed a bleep and Ryan left it or moved or resized it: the tool's span, the word's own timing from Whisper, and his final span
  deleted           the tool placed a bleep and Ryan removed it (a false alarm)
  added             Ryan put a bleep where the tool had none (a missed word): what Whisper heard around it and whether it sat inside a stretched word or a loud stretch
  rejected / confirmed suspect   his yes or no on a stretch the tool had flagged
The same edits applied twice count once.

WHAT IT CHANGES (model in `learning/model.json`, read by bleep.py on every run; every change is small, bounded and starts from the defaults):
  padding  how far before and after a word's own timing the bleep starts and ends, separately for words Whisper wrote ("transcript") and words found by
           silencing and listening again ("revealed"). The median of his final edges relative to the word, blended with the defaults as (n * median + 3 * default) / (n + 3):
           one edit moves the padding a quarter of the way, ten move it most of the way. Clamped to -0.05..0.30 s before and 0.00..0.40 s after.
  reach    the shortest stretched word the tool examines for a hidden word. Changed only after 3 or more added bleeps, and only toward the durations of the words the
           missed curse words were hiding behind (never below 0.25 s).
WHAT IT DOES NOT DO: it never turns a detector off, never bleeps more or less by itself because of a count, and never edits the word list. Reliability counts (how often a
source of bleeps was kept, and how often deleted) are reported so Ryan can decide.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bleep as bl  # noqa: E402

PRIOR = 3                                       # the defaults count as this many observations
MIN_MISSES = 3                                  # added bleeps needed before the reach is changed
SAME_TOL = 0.03                                 # an edge moved by less than this counts as left alone
PAD_BEFORE_RANGE, PAD_AFTER_RANGE = (-0.05, 0.30), (0.0, 0.40)


def learn_dir() -> Path:
    return Path(os.environ.get("POSTHOUSE_BLEEP_LEARNING") or HERE / "learning")


def feedback_path() -> Path:
    return learn_dir() / "feedback.jsonl"


def model_path() -> Path:
    return learn_dir() / "model.json"


def load_records() -> list[dict]:
    p = feedback_path()
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def _context(before: dict, span: tuple[float, float]) -> dict:
    """What Whisper heard around a stretch, and whether it sat inside a stretched word or a loud stretch."""
    words = [w for w in before.get("words", []) if w[2] > span[0] - 0.4 and w[1] < span[1] + 0.4]
    inside = [w for w in before.get("words", []) if w[1] <= (span[0] + span[1]) / 2 <= w[2]]
    hiding = inside[0] if inside else None
    loud = any(_overlap(tuple(L), span) > 0 for L in before.get("loud", []))
    return {"words_heard": [[w[0], w[1], w[2], w[3] if len(w) > 3 else None] for w in words], "inside_word": hiding[0] if hiding else None,
            "hiding_word_duration": round(hiding[2] - hiding[1], 3) if hiding else None, "in_loud_stretch": loud}


def _source_and_timing(before: dict, span: tuple[float, float]):
    """Which kind of hit made this automatic span, and the word's own timing when the tool knew it."""
    best, best_ov = None, 0.0
    for r in before.get("revealed", []):
        ov = _overlap((r["word_start"], r["word_end"]), span)
        if ov > best_ov:
            best, best_ov, kind = ({"start": r["word_start"], "end": r["word_end"], "word": r["word"]}), ov, "revealed"
    for h in before.get("hits", []):
        w = h.get("word", "")
        if w.startswith("("):                                             # a note, a click or given times: a person placed it
            continue
        if "was hidden" in w:
            continue
        ov = _overlap((h["start"], h["end"]), span)
        if ov > best_ov:
            best, best_ov, kind = ({"start": h["start"], "end": h["end"], "word": w}), ov, "transcript"
    if best is None:
        return "manual", None, None
    return kind, {"start": best["start"], "end": best["end"]}, best["word"]


def _rec(cut: str, kind: str, source: str, word: str | None, auto, final, word_timing, context, note: str = "") -> dict:
    r = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "cut": cut, "kind": kind, "source": source, "word": word,
         "auto": None if auto is None else {"start": round(auto[0], 3), "end": round(auto[1], 3)},
         "final": None if final is None else {"start": round(final[0], 3), "end": round(final[1], 3)},
         "word_timing": None if word_timing is None else {"start": round(word_timing["start"], 3), "end": round(word_timing["end"], 3)},
         "context": context, "note": note}
    r["id"] = hashlib.sha1(json.dumps({k: r[k] for k in ("cut", "kind", "source", "auto", "final", "word_timing")}, sort_keys=True).encode()).hexdigest()[:12]
    return r


def records_from_edits(edits: dict, before: dict, cut: str) -> list[dict]:
    """Compare Ryan's final spans with the spans an automatic run made. Returns the records (not yet saved)."""
    if before.get("origin") == "edits":
        return []                                                          # the previous result was already his own edit: no automatic baseline to compare with
    auto = [tuple(s) for s in before.get("spans", [])]
    final = [(float(s["start"]), float(s["end"])) for s in edits.get("spans", [])]
    used_final: set[int] = set()
    out: list[dict] = []
    for a in auto:
        ov = [(_overlap(a, f), i) for i, f in enumerate(final) if i not in used_final]
        ov = [x for x in ov if x[0] > 0]
        source, timing, word = _source_and_timing(before, a)
        if not ov:
            out.append(_rec(cut, "deleted", source, word, a, None, timing, _context(before, a)))
            continue
        _, i = max(ov)
        used_final.add(i)
        f = final[i]
        kind = "kept" if abs(f[0] - a[0]) <= SAME_TOL and abs(f[1] - a[1]) <= SAME_TOL else "adjusted"
        out.append(_rec(cut, kind, source, word, a, f, timing, _context(before, f)))
    for i, f in enumerate(final):
        if i not in used_final:
            out.append(_rec(cut, "added", "manual", None, None, f, None, _context(before, f)))
    return out


def records_from_notes(notes: dict, cut: str) -> list[dict]:
    """His yes or no on each flagged stretch (a note on the page's Suspects lane)."""
    import re
    out = []
    for n in notes.get("notes", []):
        tg = n.get("target") or {}
        if tg.get("lane") != "Suspects":
            continue
        text = n.get("text", "")
        no = bool(re.match(r"^\s*(no|nope|nah|wrong|not|ignore|skip|false)\b", text, re.I)) or bool(re.search(r"not a curse|don'?t bleep|no bleep", text, re.I))
        m = re.search(r'heard as "([^"]*)" \(([^)]*)\)', tg.get("label", ""))
        out.append(_rec(cut, "rejected_suspect" if no else "confirmed_suspect", "suspect", m.group(1) if m else None, (tg["start"], tg["end"]), None, None,
                        {"signals": m.group(2).split(", ") if m else []}, note=text[:60]))
    return out


def save(records: list[dict]) -> int:
    """Append the records not already stored (by id). Returns how many were new."""
    have = {r["id"] for r in load_records()}
    new = [r for r in records if r["id"] not in have]
    if new:
        feedback_path().parent.mkdir(parents=True, exist_ok=True)
        with feedback_path().open("a") as f:
            for r in new:
                f.write(json.dumps(r) + "\n")
    return len(new)


def _shrink(obs: list[float], default: float, lo: float, hi: float) -> float:
    if not obs:
        return default
    n = len(obs)
    return round(min(max((n * statistics.median(obs) + PRIOR * default) / (n + PRIOR), lo), hi), 3)


def fit(records: list[dict] | None = None) -> dict:
    """Re-fit the model from every record and write it. Starts from the defaults, so an empty store gives the defaults."""
    records = load_records() if records is None else records
    model: dict = {"version": 1, "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "n_records": len(records), "pads": {}, "sources": {}, "detection": {}}
    for src, (db, da) in (("transcript", (bl.PAD_BEFORE, bl.PAD_AFTER)), ("revealed", (bl.PAD_BEFORE, bl.PAD_AFTER))):
        rows = [r for r in records if r["source"] == src and r["kind"] in ("kept", "adjusted") and r.get("word_timing") and r.get("final")]
        ob = [r["word_timing"]["start"] - r["final"]["start"] for r in rows]          # how far before the word his bleep starts (negative: after the word starts)
        oa = [r["final"]["end"] - r["word_timing"]["end"] for r in rows]              # how far after the word his bleep ends
        model["pads"][src] = {"before": _shrink(ob, db, *PAD_BEFORE_RANGE), "after": _shrink(oa, da, *PAD_AFTER_RANGE), "n": len(rows),
                              "observed_median_before": round(statistics.median(ob), 3) if ob else None, "observed_median_after": round(statistics.median(oa), 3) if oa else None}
    for src in ("transcript", "revealed", "suspect"):
        kept = sum(1 for r in records if r["source"] == src and r["kind"] in ("kept", "adjusted", "confirmed_suspect"))
        wrong = sum(1 for r in records if r["source"] == src and r["kind"] in ("deleted", "rejected_suspect"))
        model["sources"][src] = {"kept": kept, "wrong": wrong, "precision": None if kept + wrong == 0 else round(kept / (kept + wrong), 2)}
    added = [r for r in records if r["kind"] == "added"]
    durs = [r["context"]["hiding_word_duration"] for r in added if r.get("context", {}).get("hiding_word_duration")]
    det = {"misses": len(added), "misses_needed": MIN_MISSES, "suspect_min_sec": None,
           "misses_outside_loud_stretches": sum(1 for r in added if not r.get("context", {}).get("in_loud_stretch"))}
    if len(added) >= MIN_MISSES and durs:
        lower = max(0.25, round(0.9 * min(durs), 2))
        det["suspect_min_sec"] = lower if lower < bl.SUSPECT_MIN_SEC else None               # the reach is only ever lowered, never raised
    model["detection"] = det
    model_path().parent.mkdir(parents=True, exist_ok=True)
    model_path().write_text(json.dumps(model, indent=2) + "\n")
    return model


def report() -> str:
    recs, m = load_records(), fit()
    lines = [f"{len(recs)} judged bleeps recorded ({len({r['cut'] for r in recs})} cut(s)); model written to {model_path()}"]
    for src, p in m["pads"].items():
        if p["n"]:
            lines.append(f"  {src}: {p['n']} adjusted/kept. His bleeps start a median {p['observed_median_before']:+.2f} s before the word and end {p['observed_median_after']:+.2f} s after it; "
                         f"padding is now {p['before']:.3f} s before and {p['after']:.3f} s after (defaults {bl.PAD_BEFORE} and {bl.PAD_AFTER})")
        else:
            lines.append(f"  {src}: no observations yet, padding stays at the defaults ({bl.PAD_BEFORE} and {bl.PAD_AFTER})")
    for src, s in m["sources"].items():
        if s["kept"] + s["wrong"]:
            lines.append(f"  {src} bleeps: {s['kept']} kept, {s['wrong']} wrong (precision {s['precision']:.0%})")
    d = m["detection"]
    lines.append(f"  missed words (bleeps you added): {d['misses']}; {d['misses_outside_loud_stretches']} of them outside any loud stretch. " +
                 (f"The shortest stretched word examined is now {d['suspect_min_sec']} s." if d["suspect_min_sec"] else f"The reach is unchanged until {d['misses_needed']} have been added."))
    return "\n".join(lines)


def ingest_edits(edits_path: Path, bleep_json: Path, cut: str | None = None) -> tuple[list[dict], int]:
    edits = json.loads(edits_path.read_text())
    before = json.loads(bleep_json.read_text())
    recs = records_from_edits(edits, before, cut or edits.get("sequence") or "unknown")
    return recs, save(recs)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ingest-edits")
    a.add_argument("--edits", type=Path, required=True)
    a.add_argument("--bleep-json", type=Path, required=True)
    a.add_argument("--cut")
    b = sub.add_parser("ingest-notes")
    b.add_argument("--notes", type=Path, required=True)
    b.add_argument("--cut", default="unknown")
    sub.add_parser("fit")
    sub.add_parser("report")
    args = ap.parse_args()
    if args.cmd == "ingest-edits":
        recs, new = ingest_edits(args.edits, args.bleep_json, args.cut)
        print(f"{len(recs)} judged bleep(s) in these edits, {new} new: " + ", ".join(f"{r['kind']} ({r['source']})" for r in recs))
    elif args.cmd == "ingest-notes":
        notes = json.loads(args.notes.read_text())
        recs = records_from_notes(notes, args.cut if args.cut != "unknown" else notes.get("sequence", "unknown"))
        print(f"{len(recs)} yes/no answer(s) on flagged stretches, {save(recs)} new")
    elif args.cmd == "fit":
        fit()
    print(report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
