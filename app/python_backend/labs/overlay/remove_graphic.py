#!/usr/bin/env python3
"""Find the graphic a note wants removed, so reconform can leave it out.

    python3 labs/overlay/remove_graphic.py --ops ops.json --notes review_notes.json --xml "<layered cut .xml>" [--out drop.json]

A `remove_graphic` operation names no time; the note does. The graphic is the callout or image card the note was left on (by the element's
start, when it was left on a timeline box), else the one on screen at the note's moment. Only callouts and cards are ever matched: a note
can never take footage, captions, music or an effect out this way. One graphic per note; a note that matches none is reported.

Writes `drop.json` ({"drop": [folder, ...]}) and prints the `--drop` flags for labs/reconform/reconform.py. Nothing is changed here.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "review_loop"))
sys.path.insert(0, str(HERE.parents[1] / "safety_net"))
import layers as ly  # noqa: E402

TOL = 0.25


def find(layers: list, note: dict):
    g = [l for l in layers if l.kind == "video" and ly.lane_name(l) in ("Callout", "Card")]
    tg = note.get("target")
    if tg:
        if tg.get("lane") not in ("Callout", "Card"):
            return None, f"the note was left on a {tg.get('lane')} element, which this cannot remove"
        m = [l for l in g if ly.lane_name(l) == tg["lane"] and abs(l.start - float(tg.get("start", -9))) <= TOL]
        return (m[0], "") if m else (None, f'the {tg["lane"]} element "{tg.get("label", "")}" is not on this cut')
    t = note["timeline_sec"]
    m = [l for l in g if l.start <= t <= l.end]
    if not m:
        return None, f"no callout or card is on screen at {t:.2f}s on this cut"
    if len(m) > 1:
        return None, f"{len(m)} graphics are on screen at {t:.2f}s; click the one to remove on the timeline map so the note names it"
    return m[0], ""


def apply(ops: list[dict], notes: list[dict], xml: Path) -> list[dict]:
    layers = ly.find_layers(xml)
    ledger, taken = [], set()
    for o in ops:
        if o.get("op") != "remove_graphic":
            continue
        n = o["note"]
        e = {"note": n, "op": "remove_graphic", "note_time": notes[n - 1]["timeline_sec"], "note_text": notes[n - 1].get("text", ""), "applied": False, "reason": ""}
        ledger.append(e)
        lay, why = find(layers, notes[n - 1])
        if lay is None:
            e["reason"] = why
            continue
        if Path(lay.path).name.startswith(("captions", "sfx", "music", "bleep")) or ly.lane_name(lay) not in ("Callout", "Card"):
            e["reason"] = "only a callout or an image card can be removed this way"
            continue
        folder = str(Path(lay.path).parent)
        if folder in taken:
            e["reason"] = "another note already removes this graphic"
            continue
        taken.add(folder)
        e.update(applied=True, folder=folder, lane=ly.lane_name(lay), start=round(lay.start, 2), end=round(lay.end, 2),
                 reason=f"the {ly.lane_name(lay).lower()} at {lay.start:.2f}-{lay.end:.2f}s will be left out")
    return ledger


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ops", type=Path, required=True)
    ap.add_argument("--notes", type=Path, required=True)
    ap.add_argument("--xml", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    try:
        ledger = apply(json.loads(a.ops.read_text()), json.loads(a.notes.read_text())["notes"], a.xml)
    except (OSError, KeyError, ValueError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    if not ledger:
        print("no remove-graphic notes in these operations", file=sys.stderr)
        return 1
    for e in ledger:
        print(f"  note {e['note']} [{e['note_time']}s] {'APPLIED    ' if e['applied'] else 'NOT APPLIED'}  remove_graphic: {e['reason']}")
    drops = [e["folder"] for e in ledger if e["applied"]]
    if a.out:
        a.out.write_text(json.dumps({"drop": drops, "ledger": ledger}, indent=2))
    if drops:
        print("\nleave it out with: reconform.py ... " + " ".join(f'--drop "{d}"' for d in drops))
    else:
        print("\nNothing to remove.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
