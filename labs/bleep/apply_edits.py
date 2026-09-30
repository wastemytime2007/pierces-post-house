#!/usr/bin/env python3
"""Bake bleeps edited on the review page into the XML, then rebuild the page.

    PRECUT_ROOT=~/precut-checkout python3 labs/bleep/apply_edits.py --xml "<cut .xml>" --edits ~/Downloads/bleep_edits.json [--out "<folder>"] [--listen]

The review page lets the bleeps be dragged, resized, added and deleted with the result heard live. "Download bleep edits" saves the final list; this
applies exactly that list (each span as given, no padding, nothing scanned or guessed): the speech is silenced and a 1 kHz bleep laid over each span, the
result is measured from the files (silent, tone, level, speech beside it unchanged), and the review page is rebuilt so it opens with your spans. An empty
list takes every bleep out. The folder the XML is in is used unless --out says otherwise; the XML is replaced only after every check passes.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "review_loop"))
sys.path.insert(0, str(HERE.parents[1] / "safety_net"))
import bleep as bp  # noqa: E402
import timeline  # noqa: E402


class EditError(Exception):
    pass


def read_spans(path: Path, duration: float) -> list[tuple[float, float]]:
    j = json.loads(path.read_text())
    spans = []
    for s in j.get("spans", []):
        a, b = round(float(s["start"]), 3), round(float(s["end"]), 3)
        if not 0 <= a < b <= duration + 0.01:
            raise EditError(f"the span {a}-{b}s is outside the cut (0-{duration:.2f}s) or empty")
        if b - a < 0.05:
            raise EditError(f"the span {a}-{b}s is shorter than 0.05 s")
        spans.append((a, min(b, duration)))
    return sorted(spans)


def automatic_record(xml: Path, out: Path, given: Path | None = None) -> Path | None:
    """The record of the automatic run these edits were made on top of (what the tool guessed), found beside the XML."""
    for p in ([given] if given else []) + [xml.parent / "bleep.json", xml.parent / "bleep" / "bleep.json", out / "bleep" / "bleep.json"]:
        if p and p.exists():
            return p
    return None


def apply(xml: Path, edits: Path, out: Path, listen: bool = False, words_of=None, auto_json: Path | None = None, learn: bool = True) -> dict:
    cut = timeline.load_cut(xml)
    spans = read_spans(edits, cut.zone_end)
    before = None
    rec = automatic_record(xml, out, auto_json)
    if learn and rec:
        try:
            before = json.loads(rec.read_text())              # read now: applying the edits overwrites it
        except (OSError, ValueError):
            before = None
    work = out / "bleep"
    work.mkdir(parents=True, exist_ok=True)
    if spans:
        res = bp.bleep(xml, work, words_of=words_of or bp.transcribe_timed, requests=[{"kind": "exact", "start": a, "end": b} for a, b in spans],
                       detect=False, check_transcript=listen)
        bad = [(n, d) for n, ok, d in res["rows"] if ok is False]
        if bad:
            raise EditError("the edited bleeps failed their checks: " + "; ".join(f"{n}: {d}" for n, d in bad))
        new_xml, rows = Path(res["xml"]), res["rows"]
    else:
        new_xml, rows = work / xml.name, []
        pieces, layers = bp.strip_previous(xml, new_xml)
        if not (pieces or layers):
            shutil.copy2(xml, new_xml)
    final = out / xml.name
    final.write_text(new_xml.read_text())
    learned = None
    if learn and before is not None:                          # what the tool guessed against what he made of it: recorded, and the model re-fitted
        import learn as lr
        recs = lr.records_from_edits(json.loads(edits.read_text()), before, cut.sequence_name)
        learned = {"records": recs, "new": lr.save(recs), "model": lr.fit() if recs else None}
    return {"xml": final, "spans": spans, "rows": rows, "learned": learned}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xml", type=Path, required=True)
    ap.add_argument("--edits", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--height", type=int, default=540)
    ap.add_argument("--listen", action="store_true", help="also transcribe the result and look for a listed word still audible (about 40 s)")
    a = ap.parse_args()
    out = a.out or a.xml.parent
    try:
        r = apply(a.xml, a.edits, out, a.listen)
    except (EditError, bp.BleepError, timeline.TimelineError, OSError, KeyError, ValueError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    for n, ok, d in r["rows"]:
        print(f"  [{'SKIP' if ok is None else 'PASS' if ok else 'FAIL'}] {n}  {d}")
    print(f"{len(r['spans'])} bleep(s): " + (", ".join(f"{x:.2f}-{y:.2f}s" for x, y in r["spans"]) or "none"))
    L = r["learned"]
    if L and L["records"]:
        print(f"learned from these edits ({L['new']} new of {len(L['records'])}): " + "; ".join(
            f"{x['kind']} ({x['source']})" + (f", his span {x['final']['start']:.2f}-{x['final']['end']:.2f}s vs the tool's {x['auto']['start']:.2f}-{x['auto']['end']:.2f}s" if x.get("auto") and x.get("final") else "") for x in L["records"]))
    elif L is not None:
        print("learning: these edits were made on a result that was already hand-edited, so there is no automatic guess to compare with")
    else:
        print("learning: no record of an automatic run was found beside the XML, so nothing was learned this time")
    from build_review import build
    page = build(r["xml"], out / "review", a.height)
    print(f"\n{r['xml']}\n{page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
