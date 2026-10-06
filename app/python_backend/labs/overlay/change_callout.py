#!/usr/bin/env python3
"""Notes that change a callout (stay longer, different words, drop the second line) -> the callout re-rendered.

    PRECUT_ROOT=~/precut-checkout python3 labs/overlay/change_callout.py \
        --ops "<revise's ops.json>" --notes "<review_notes.json>" \
        --xml "<the XML the notes were left on>" --out "<new callout folder>" [--default-extra 1.5]

revise.py turns "the text bubble only shows for a moment, keep it on longer" into `extend_graphic` and
"change the title to 'Cardboard spacer' and drop the small line" into `edit_callout`. This applies them: each
note is matched to the callout that was on screen at the note's moment, and that callout is rendered once,
again, with every change the notes ask for (same drawn region, same anchor frame, the target sequence's size
and rate, every verify_overlay check). `hold_change.json` records what was asked and what was done.

Amounts and words come from the notes. When a note asks for "longer" without an amount, a stated default step
is used and the ledger says so. New words must have come from the note (the interpreter is not allowed to write
its own). If the shot ends too soon for the extra time, the callout gets what fits, and a gain under 0.3s is
reported as not applied. One callout per run. Then put it on the cut with reconform (`--overlay <new folder>`),
which also rebuilds the captions so they keep clear of the changed callout.
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
KINDS = ("extend_graphic", "edit_callout", "end_graphic")
MIN_HOLD_SEC = 1.5                          # a callout ended earlier than this could not be read


TARGET_TOL_SEC = 0.25


def callout_under(layers: list[ly.Layer], t: float, target: dict | None = None) -> ly.Layer | None:
    """The callout layer on screen at timeline time t (nearest by centre if several). A note left on a timeline
    element names its callout by where that element starts, so the playhead does not matter."""
    if target:
        if target.get("lane") != "Callout":
            return None
        near = [l for l in layers if l.kind == "video" and ly.lane_name(l) == "Callout" and abs(l.start - float(target.get("start", -9))) <= TARGET_TOL_SEC]
        return min(near, key=lambda l: abs(l.start - float(target["start"]))) if near else None
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
    """One ledger entry per graphic-change op. All changes to one callout are made in a single render;
    a note about a different callout waits for another run."""
    layers = ly.find_layers(xml)
    cut = timeline.load_cut(xml)
    ledger: list[dict] = []
    target: Path | None = None
    lay_of: dict = {}
    plan_ops: list[tuple[dict, dict]] = []
    for o in ops:
        if o.get("op") not in KINDS:
            continue
        n = o["note"]
        t = notes[n - 1]["timeline_sec"]
        e = {"note": n, "op": o["op"], "note_time": t, "note_text": notes[n - 1].get("text", ""), "applied": False, "reason": ""}
        ledger.append(e)
        tg = notes[n - 1].get("target")
        lay = callout_under(layers, t, tg)
        if lay is None:
            e["reason"] = (f"the {tg['lane']} element \"{tg.get('label', '')}\" at {tg.get('start')}s is not a callout on this cut" if tg
                           else f"no callout is on screen at {t:.2f}s on this cut")
            continue
        folder = Path(lay.path).parent
        if not (folder / "placement.json").exists():
            e["reason"] = f"the callout's placement.json is not beside {Path(lay.path).name}, so it cannot be rebuilt"
            continue
        if target is not None and folder != target:
            e["reason"] = "this note is about a different callout; one callout per run, run again for it"
            continue
        target = folder
        lay_of[id(e)] = lay
        plan_ops.append((o, e))
    if not plan_ops:
        return ledger

    pl = json.loads((target / "placement.json").read_text())
    g = pl["geometry"]
    was_hold = g["t_out"] - g["t_in"]
    hold, title, subtitle = was_hold, pl["title"], pl["subtitle"]
    used: set[str] = set()                                  # a field is changed by the first note that asks; a later note on it is reported
    live: list[tuple[dict, dict]] = []
    for o, e in plan_ops:
        if o["op"] == "end_graphic":
            if "hold" in used:
                e["reason"] = "another note already changed how long this callout stays; one change to the hold per callout per run"
                continue
            want = e["note_time"] - lay_of[id(e)].start - g["t_in"]           # the fade starts at the note's moment
            if want < MIN_HOLD_SEC:
                e["reason"] = f"ending it at {e['note_time']:.2f}s would leave it up only {max(want, 0):.2f}s, under the {MIN_HOLD_SEC:.1f}s needed to read it"
                continue
            if want >= hold - 0.05:
                e["reason"] = f"the callout already fades by {lay_of[id(e)].start + g['t_in'] + hold:.2f}s, before {e['note_time']:.2f}s"
                continue
            used.add("hold")
            hold = want
            e["end_at_sec"] = round(e["note_time"], 2)
        elif o["op"] == "extend_graphic":
            if "hold" in used:
                e["reason"] = "another note already made this callout stay longer; one extension per callout per run"
                continue
            used.add("hold")
            stated = o.get("seconds") is not None
            extra = float(o["seconds"]) if stated else default_extra
            hold += extra
            e["asked_extra_sec"] = round(extra, 2)
            e["amount_from"] = "the note" if stated else f"a default step of {default_extra:.1f}s (the note gave no amount)"
        else:
            touches = (["title"] if o.get("title") is not None else []) + (["subtitle"] if o.get("remove_subtitle") or o.get("subtitle") is not None else [])
            clash = [f for f in touches if f in used]
            if clash:
                e["reason"] = f"another note already changed this callout's {clash[0]}; one change per field per run"
                continue
            used.update(touches)
            if o.get("title") is not None:
                title = o["title"]
            if o.get("remove_subtitle"):
                subtitle = ""
            elif o.get("subtitle") is not None:
                subtitle = o["subtitle"]
        live.append((o, e))
    if not live:
        return ledger
    plan_ops = live
    note = {"source": pl["anchor"]["source"], "source_sec": pl["anchor"]["source_sec"], "region": pl["region"], "text": pl.get("note_text", "")}
    code, p = build(note, timeline_dict(cut), out, title, subtitle, spec_of(xml), pl.get("note", plan_ops[0][0]["note"]), hold)
    if code != 0 or p is None:
        for _o, e in plan_ops:
            e["reason"] = "the callout could not be re-rendered (see the message above)"
        return ledger

    gained = p["hold"] - was_hold
    for o, e in plan_ops:
        if o["op"] == "end_graphic":
            e.update({"was_hold_sec": round(was_hold, 2), "now_hold_sec": p["hold"], "folder": str(out), "applied": True})
            e["reason"] = f"the callout now fades out from {e['end_at_sec']:.2f}s (hold {was_hold:.2f}s -> {p['hold']:.2f}s)"
        elif o["op"] == "extend_graphic":
            e.update({"was_hold_sec": round(was_hold, 2), "now_hold_sec": p["hold"], "gained_sec": round(gained, 2), "folder": str(out)})
            if gained < MIN_GAIN_SEC:
                e["reason"] = f"only {gained:.2f}s fits before the shot ends, under the {MIN_GAIN_SEC:.1f}s minimum, so the hold was not changed"
                continue
            e["applied"] = True
            e["reason"] = (f"hold {was_hold:.2f}s -> {p['hold']:.2f}s (+{gained:.2f}s"
                           + (f" of the {e['asked_extra_sec']:.2f}s asked; the shot ends" if gained < e["asked_extra_sec"] - 0.05 else "")
                           + f"); amount from {e['amount_from']}")
        else:
            e.update({"title_now": title, "subtitle_now": subtitle, "folder": str(out)})
            e["applied"] = True
            bits = []
            if title != pl["title"]:
                bits.append(f'title "{pl["title"]}" -> "{title}"')
            if subtitle != pl["subtitle"]:
                bits.append("second line removed" if not subtitle else f'second line "{pl["subtitle"]}" -> "{subtitle}"')
            e["reason"] = "; ".join(bits) or "the words already read that way, nothing to change"
    if not any(e["applied"] for _o, e in plan_ops):
        return ledger
    (out / "hold_change.json").write_text(json.dumps({"ledger": ledger, "was": {"hold": round(was_hold, 3), "title": pl["title"], "subtitle": pl["subtitle"]},
                                                      "now": {"hold": p["hold"], "title": title, "subtitle": subtitle}}, indent=2))
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
        print("no callout-change notes in these operations", file=sys.stderr)
        return 1
    for e in ledger:
        print(f"  note {e['note']} [{e['note_time']}s] {'APPLIED    ' if e['applied'] else 'NOT APPLIED'}  {e['op']}: {e['reason']}")
    if not any(e["applied"] for e in ledger):
        print("\nNothing was changed.")
        return 0
    print(f"\nnew callout: {a.out}\nput it on the cut with: python3 labs/reconform/reconform.py --revised <xml> --overlay \"{a.out}\" --captions <folder> --audio <folder> --out <folder>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
