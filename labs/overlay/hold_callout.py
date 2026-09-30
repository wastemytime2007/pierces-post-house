#!/usr/bin/env python3
"""Notes that ask for a graphic to stay on screen longer -> the callout re-rendered with a longer hold.

    PRECUT_ROOT=~/precut-checkout python3 labs/overlay/hold_callout.py \
        --ops "<revise's ops.json>" --notes "<review_notes.json>" \
        --xml "<the XML the notes were left on>" --out "<new callout folder>" [--default-extra 1.5]

revise.py turns "the text bubble only shows for a second, keep it on longer" into an `extend_graphic`
operation. This applies it: the note is matched to the callout that was on screen at the note's moment,
that callout is rendered again with a longer hold (same words, same drawn region, same anchor frame), and
`hold_change.json` records what was asked and what was done. The amount comes from the note when it states
one ("two more seconds"); when it does not, a stated default step is used and the ledger says so. If the
shot ends too soon for the extra time, the callout gets what fits, and a gain under 0.3s is reported as
not applied. Then put it on the cut with reconform (`--overlay <new folder>`), which also rebuilds the
captions so they keep clear of the longer callout.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "review_loop"))
sys.path.insert(0, str(HERE))

import layers as ly  # noqa: E402
import make_overlay as mo  # noqa: E402
import timeline  # noqa: E402

DEFAULT_EXTRA_SEC = 1.5
MIN_GAIN_SEC = 0.3


def callout_under(layers: list[ly.Layer], t: float) -> ly.Layer | None:
    """The callout layer on screen at timeline time t (nearest by centre if several)."""
    live = [l for l in layers if l.kind == "video" and ly.lane_name(l) == "Callout" and l.start <= t <= l.end]
    return min(live, key=lambda l: abs(t - (l.start + l.end) / 2)) if live else None


def timeline_dict(cut: timeline.Cut) -> dict:
    """The clips and audio of a cut in the shape make_overlay's planner reads."""
    return {"clips": [{"idx": c.idx, "start": c.tl_start, "end": c.tl_end, "source": c.name, "source_path": c.src_path,
                       "src_in": c.src_in, "src_out": c.src_out} for c in cut.video],
            "audio": [{"start": a.tl_start, "end": a.tl_end, "source": a.name, "source_path": a.src_path, "src_in": a.src_in, "src_out": a.src_out}
                      for a in cut.audio]}


def apply(ops: list[dict], notes: list[dict], xml: Path, out: Path, default_extra: float = DEFAULT_EXTRA_SEC,
          build=mo.build_overlay, spec_of=mo.sequence_spec) -> list[dict]:
    """One ledger entry per extend_graphic op. Only the first change to a given callout is made."""
    layers = ly.find_layers(xml)
    cut = timeline.load_cut(xml)
    ledger, done = [], set()
    for o in ops:
        if o.get("op") != "extend_graphic":
            continue
        n = o["note"]
        t = notes[n - 1]["timeline_sec"]
        e = {"note": n, "note_time": t, "note_text": notes[n - 1].get("text", ""), "applied": False, "reason": ""}
        ledger.append(e)
        lay = callout_under(layers, t)
        if lay is None:
            e["reason"] = f"no callout is on screen at {t:.2f}s on this cut"
            continue
        folder = Path(lay.path).parent
        try:
            pl = json.loads((folder / "placement.json").read_text())
        except (OSError, ValueError):
            e["reason"] = f"the callout's placement.json is not beside {Path(lay.path).name}, so it cannot be rebuilt"
            continue
        if str(folder) in done:
            e["reason"] = "another note already changed this callout; one change per callout per run"
            continue
        g = pl["geometry"]
        was = g["t_out"] - g["t_in"]
        stated = o.get("seconds") is not None
        extra = float(o["seconds"]) if stated else default_extra
        note = {"source": pl["anchor"]["source"], "source_sec": pl["anchor"]["source_sec"], "region": pl["region"], "text": pl.get("note_text", "")}
        spec = spec_of(xml)
        code, p = build(note, timeline_dict(cut), out, pl["title"], pl["subtitle"], spec, pl.get("note", n), was + extra)
        if code != 0 or p is None:
            e["reason"] = "the callout could not be re-rendered (see the message above)"
            continue
        gained = p["hold"] - was
        e.update({"was_hold_sec": round(was, 2), "now_hold_sec": p["hold"], "asked_extra_sec": round(extra, 2), "gained_sec": round(gained, 2),
                  "amount_from": "the note" if stated else f"a default step of {default_extra:.1f}s (the note gave no amount)", "folder": str(out)})
        if gained < MIN_GAIN_SEC:
            e["reason"] = f"only {gained:.2f}s fits before the shot ends, under the {MIN_GAIN_SEC:.1f}s minimum, so nothing was changed"
            continue
        done.add(str(folder))
        e["applied"] = True
        e["reason"] = (f"hold {was:.2f}s -> {p['hold']:.2f}s (+{gained:.2f}s"
                       + (f" of the {extra:.2f}s asked; the shot ends" if gained < extra - 0.05 else "") + f"); amount from {e['amount_from']}")
    if any(x["applied"] for x in ledger):
        (out / "hold_change.json").write_text(json.dumps({"ledger": ledger}, indent=2))
    return ledger


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ops", type=Path, required=True)
    ap.add_argument("--notes", type=Path, required=True)
    ap.add_argument("--xml", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--default-extra", type=float, default=DEFAULT_EXTRA_SEC)
    a = ap.parse_args()
    try:
        ops = json.loads(a.ops.read_text())
        notes = json.loads(a.notes.read_text())["notes"]
        ledger = apply(ops, notes, a.xml, a.out, a.default_extra)
    except (OSError, KeyError, ValueError, timeline.TimelineError, mo.OverlayError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    if not ledger:
        print("no extend_graphic notes in these operations", file=sys.stderr)
        return 1
    for e in ledger:
        print(f"  note {e['note']} [{e['note_time']}s] {'APPLIED    ' if e['applied'] else 'NOT APPLIED'}  {e['reason']}")
    if not any(e["applied"] for e in ledger):
        print("\nNothing was changed.")
        return 0
    print(f"\nnew callout: {a.out}\nput it on the cut with: python3 labs/reconform/reconform.py --revised <xml> --overlay \"{a.out}\" --captions <folder> --audio <folder> --out <folder>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
