"""Does every cut start and end on the words it was meant to? Checked on the words the CAPTIONS heard in the finished cut, so it is what a viewer would read.

    python3 labs/recruit/check_edges.py --resolved resolved.json --pitch p3 --captions "<captions folder>" --frames 199,44,142,...

A cut that begins a few milliseconds early catches the tail of the previous word ("is if a sub says": the cut was meant to start on "if"); one that ends early drops the end of its last word. Whisper's own word times can be
tens of milliseconds off, so the cut points are chosen from them and cannot be trusted to have avoided this: the check listens to the result. For each cut it takes the FIRST caption word that starts inside the cut and the LAST that
ends inside it and compares them with the first word of the cut's `from` and the last word of its `to` (fuzzy: 'checked' for 'check', '100%' written out).
"""
from __future__ import annotations

import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

FPS = 30000 / 1001
EDGE = 0.7                       # seconds at each end of a cut in which its first and last words must be found


def tok(w: str) -> str:
    return re.sub(r"[^a-z0-9%$*]", "", w.lower())


def same(a: str, b: str) -> bool:
    a, b = tok(a), tok(b)
    if "*" in a or "*" in b:                                     # a bleeped word is starred in the caption ('a**' for 'ass'): it matches the word it hides, letter for letter
        starred, plain = (a, b) if "*" in a else (b, a)
        return len(starred) == len(plain) and all(s == "*" or s == p for s, p in zip(starred, plain))
    return bool(a) and bool(b) and (a == b or SequenceMatcher(None, a, b).ratio() >= 0.75 or a.startswith(b) or b.startswith(a))


def edge_rows(cuts: list[dict], spans: list[tuple[float, float]], words: list[dict]) -> list[tuple[str, bool, str]]:
    """One row per cut: (name, ok, what was heard). `spans` are the cuts' timeline seconds; `words` the captions' words {text, start, end} on the same clock."""
    rows = []
    for i, (c, (a, b)) in enumerate(zip(cuts, spans), 1):
        want_first = c["from"].split()[0]
        want_last = (c.get("to") or c["from"]).split()[-1]
        inside = [w for w in words if w["start"] >= a - 0.15 and w["end"] <= b + 0.08]       # Whisper often times a word's start 70 to 100 ms before it is heard
        if not inside:
            rows.append((f"EDGES cut {i} ({c['role']})", False, f"no caption word inside {a:.2f}-{b:.2f}s"))
            continue
        first, last = inside[0], inside[-1]
        ok_first, ok_last = same(first["text"], want_first), same(last["text"], want_last)
        late = first["start"] - a > EDGE
        early = b - last["end"] > EDGE
        ok = ok_first and ok_last and not late and not early
        why = f"starts on '{first['text']}' (wanted '{want_first}'){' LATE' if late else ''}, ends on '{last['text']}' (wanted '{want_last}'){' EARLY' if early else ''}"
        rows.append((f"EDGES cut {i} ({c['role']})", ok, why))
    return rows


def caption_words(folder: Path) -> list[dict]:
    d = json.loads((folder / "captions.json").read_text())
    return [w for g in d["groups"] for w in g["words"]]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resolved", type=Path, required=True)
    ap.add_argument("--pitch", required=True)
    ap.add_argument("--captions", type=Path, required=True)
    ap.add_argument("--frames", required=True, help="the frame count of each cut on the timeline, comma separated, in order")
    a = ap.parse_args()
    cuts = [p for p in json.loads(a.resolved.read_text())["pitches"] if p["key"] == a.pitch][0]["cuts"]
    frames = [int(x) for x in a.frames.split(",")]
    t, spans = 0.0, []
    for n in frames:
        spans.append((t, t + n / FPS))
        t += n / FPS
    rows = edge_rows(cuts, spans, caption_words(a.captions))
    bad = 0
    for n, ok, why in rows:
        print(f"  [{'PASS' if ok else 'FAIL'}] {n:40} {why}")
        bad += not ok
    print("\nAll cuts start and end on their words." if not bad else f"\n{bad} cut(s) start or end on the wrong word.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
