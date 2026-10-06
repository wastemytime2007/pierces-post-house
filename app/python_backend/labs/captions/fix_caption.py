#!/usr/bin/env python3
"""Change the words of a caption line from a review note.

    PRECUT_ROOT=~/precut-checkout python3 labs/captions/fix_caption.py --ops ops.json --notes review_notes.json \
        --captions "<captions folder>" --xml "<cut .xml>" --out "<new captions folder>"

An `edit_caption` operation carries the wording (copied from the note) and the note carries the moment. The line showing at that
moment is replaced, the new words are spread over the old line's spoken span, and the captions are re-rendered with every other line
as it was. The fix is also written to `captions.json` ("fixes"), so a later reconform, which rebuilds the captions from the audio,
applies it again instead of quietly undoing it. The old folder is not changed.

Checked from the new captions.json: the line at the note's moment now reads exactly the new wording, the number of lines is the same,
every other line is word-for-word what it was, and the new words stay inside the old line's time span. (The render's own checks then run.)
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import make_captions as mc  # noqa: E402


class FixError(Exception):
    pass


def collect(ops: list[dict], notes: list[dict], groups: list[dict], window_start: float, old_fixes: list[dict]) -> tuple[list[dict], list[dict]]:
    """(all fixes to apply, ledger). Earlier fixes stay unless a new one is on the same line."""
    ledger, fixes = [], [dict(f) for f in old_fixes]
    for o in ops:
        if o.get("op") != "edit_caption":
            continue
        n = o["note"]
        t = notes[n - 1]["timeline_sec"]
        e = {"note": n, "op": "edit_caption", "note_time": t, "note_text": notes[n - 1].get("text", ""), "applied": False, "reason": ""}
        ledger.append(e)
        line = next((g for g in groups if g["show_start"] + window_start - 0.05 <= t <= g["show_end"] + window_start + 0.05), None)
        if line is None:
            e["reason"] = f"no caption line is showing at {t:.2f}s (the captions cover {window_start:.1f}-{window_start + max(g['show_end'] for g in groups):.1f}s)"
            continue
        was = line["text"]
        fixes = [f for f in fixes if not (line["show_start"] + window_start - 0.05 <= f["at"] <= line["show_end"] + window_start + 0.05)]
        fixes.append({"at": round(t, 3), "text": o["text"], "was": was, "note": n})
        e.update(applied=True, was=was, now=mc.tidy_case(o["text"], was), reason=f'line "{was}" -> "{mc.tidy_case(o["text"], was)}"')
    return fixes, ledger


def check(old: dict, new: dict, ledger: list[dict]) -> list[tuple[str, bool, str]]:
    rows = []
    og, ng = old["groups"], new["groups"]
    rows.append(("SAME-NUMBER-OF-LINES", len(og) == len(ng), f"{len(og)} lines before, {len(ng)} after"))
    changed = {i for i, (a, b) in enumerate(zip(og, ng)) if a["text"] != b["text"]}
    wanted = set()
    for e in ledger:
        if not e["applied"]:
            continue
        i = next((k for k, g in enumerate(ng) if g["text"] == e["now"]), None)
        rows.append((f"LINE-READS-THE-NEW-WORDS (note {e['note']})", i is not None, f'a line reads exactly "{e["now"]}"' + (f" (line {i + 1})" if i is not None else "")))
        if i is not None:
            wanted.add(i)
            a, b = og[i], ng[i]
            inside = b["words"][0]["start"] >= a["words"][0]["start"] - 0.01 and b["words"][-1]["end"] <= a["words"][-1]["end"] + 0.01
            rows.append((f"INSIDE-THE-OLD-SPAN (note {e['note']})", inside, f"words run {b['words'][0]['start']:.2f}-{b['words'][-1]['end']:.2f}s; the old line spoke {a['words'][0]['start']:.2f}-{a['words'][-1]['end']:.2f}s"))
    stray = sorted(changed - wanted)
    rows.append(("NOTHING-ELSE-CHANGED", not stray, "every other line is word-for-word what it was" if not stray else f"lines {[i + 1 for i in stray]} differ"))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ops", type=Path, required=True)
    ap.add_argument("--notes", type=Path, required=True)
    ap.add_argument("--captions", type=Path, required=True)
    ap.add_argument("--xml", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    try:
        ops = json.loads(a.ops.read_text())
        notes = json.loads(a.notes.read_text())["notes"]
        old = json.loads((a.captions / "captions.json").read_text())
        pl = json.loads((a.captions / "placement.json").read_text())
        win = old["window"]
        fixes, ledger = collect(ops, notes, [{**g, "show_start": g["show_start"], "show_end": g["show_end"]} for g in old["groups"]], win["start"], old.get("fixes", []))
    except (OSError, KeyError, ValueError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    if not ledger:
        print("no caption-wording notes in these operations", file=sys.stderr)
        return 1
    for e in ledger:
        print(f"  note {e['note']} [{e['note_time']}s] {'APPLIED    ' if e['applied'] else 'NOT APPLIED'}  edit_caption: {e['reason']}")
    if not any(e["applied"] for e in ledger):
        print("\nNothing was changed.")
        return 0
    a.out.mkdir(parents=True, exist_ok=True)
    base = a.captions / "cut_1080.mp4"
    if base.exists():
        shutil.copy2(base, a.out / "cut_1080.mp4")                  # the proxy the transcript came from; no need to render it again
    fx = a.out / "fixes.json"
    fx.write_text(json.dumps(fixes, indent=2))
    cmd = [sys.executable, str(HERE / "make_captions.py"), "--xml", str(a.xml), "--out", str(a.out), "--start", str(win["start"]), "--end", str(win["end"]),
           "--style", old.get("style", "pill"), "--fixes", str(fx)]
    for f in pl.get("avoid", []):
        cmd += ["--avoid", f]
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout.rstrip())
    if p.returncode:
        print(p.stderr[-800:], file=sys.stderr)
        return 1
    rows = check(old, json.loads((a.out / "captions.json").read_text()), ledger)
    print("\nChecks on the fix:")
    bad = 0
    for name, ok, detail in rows:
        bad += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")
    if bad:
        print("\nThe fix FAILED its checks; do not use these captions.", file=sys.stderr)
        return 1
    print(f"\nnew captions: {a.out}\nput them on the cut with: python3 labs/reconform/reconform.py --revised <xml> --captions \"{a.out}\" ...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
