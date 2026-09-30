#!/usr/bin/env python3
"""Export XML + review notes in, revised XML + V2 review page out.

    PRECUT_ROOT=~/precut-checkout python3 labs/review_loop/revise.py \
        <export.xml> <review_notes.json> --out <folder> [--ops ops.json] [--open]

Notes come from the page's "Download JSON" or "Copy all feedback". Without --ops the
notes go to the local `claude` CLI (build-phase cost mode) to be turned into operations;
with --ops that step is skipped, so a plan can be reviewed, edited and re-run.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "safety_net"))

import verify_export  # noqa: E402
from apply_ops import apply_ops  # noqa: E402
from build_review import build  # noqa: E402
from ops import interpret, validate  # noqa: E402
from timeline import Cut, TimelineError, _seq_for_cut, load_cut  # noqa: E402


def _video_items(xml: Path, zone_f: int, before: bool) -> list[tuple[int, int, int, int]]:
    seq = _seq_for_cut(ET.parse(xml).getroot())
    items = seq.findall("media/video/track/clipitem")
    rows = [(int(c.findtext("start")), int(c.findtext("end")), int(c.findtext("in")), int(c.findtext("out"))) for c in items]
    return sorted(r for r in rows if (r[0] < zone_f) == before)


def verify(cut1: Cut, xml1: Path, xml2: Path, removed_sec: float) -> list[tuple[str, bool | None, str]]:
    rows: list[tuple[str, bool | None, str]] = []
    cut2 = load_cut(xml2)                                # bounds-checks every clip against real media
    rows.append(("RELOADS-AND-BOUNDS", True, f"{len(cut2.video)} clips, every range inside its real file"))
    tol = 2.0 / cut1.fps
    rows.append(("RIPPLE-LENGTH", abs((cut1.zone_end - removed_sec) - cut2.zone_end) <= tol,
                 f"V1 {cut1.zone_end:.2f}s - removed {removed_sec:.2f}s = {cut1.zone_end - removed_sec:.2f}s, V2 is {cut2.zone_end:.2f}s"))
    gaps = [abs(b.tl_start - a.tl_end) for a, b in zip(cut2.video, cut2.video[1:])]
    rows.append(("CONTIGUOUS", all(g < 1e-3 for g in gaps), f"largest seam {max(gaps or [0]) * 1000:.2f}ms"))

    slack = 2.0 / cut1.fps
    stray = [p for p in cut2.video
             if not any(p.src_path == c.src_path and p.src_in >= c.src_in - slack and p.src_out <= c.src_out + slack for c in cut1.video)]
    rows.append(("NO-NEW-FOOTAGE", not stray, "every V2 piece lies inside a V1 clip" if not stray else f"{len(stray)} piece(s) outside V1 ranges"))

    v1_off: dict[str, list[float]] = {}
    for a in cut1.audio:
        v = next((c for c in cut1.video if abs(c.tl_start - a.tl_start) < 0.05), None)
        if v:
            v1_off.setdefault(a.src_path, []).append(a.src_in - v.src_in)
    bad = []
    for a in cut2.audio:
        v = next((c for c in cut2.video if c.tl_start - 0.02 <= a.tl_start < c.tl_end), None)
        if v and a.src_path in v1_off:
            off = a.src_in - v.src_in
            if not any(abs(off - o) < 0.05 for o in v1_off[a.src_path]):
                bad.append((a.tl_start, off))
    rows.append(("LAV-SYNC-PRESERVED", not bad, "every lav piece keeps its V1 offset to camera" if not bad else f"{len(bad)} piece(s) drifted, first at {bad[0][0]:.2f}s"))

    z1 = round(cut1.zone_end * cut1.fps)
    rows.append(("POOL-UNTOUCHED", _video_items(xml1, z1, False) == _video_items(xml2, z1, False), "selects pool identical to V1"))

    rep = verify_export.Report()
    verify_export.check_xml(xml2, rep)
    for name, ok, detail in rep.rows:
        rows.append(("verify_export " + name, ok, detail))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xml", type=Path)
    ap.add_argument("notes", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--ops", type=Path, help="skip interpretation and use this operations file")
    ap.add_argument("--height", type=int, default=540)
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    notes = json.loads(args.notes.read_text())["notes"]
    if not notes:
        print("no notes to apply", file=sys.stderr)
        return 1
    try:
        cut1 = load_cut(args.xml)
        args.out.mkdir(parents=True, exist_ok=True)
        ops = validate(json.loads(args.ops.read_text()), notes, cut1) if args.ops else interpret(cut1, notes)
        (args.out / "ops.json").write_text(json.dumps(ops, indent=2))

        v2_xml = args.out / f"{args.xml.stem}_v2.xml"
        changes, removed = apply_ops(args.xml, v2_xml, cut1, ops, notes)
    except TimelineError as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1

    items = [{"note": c.note, "note_time": notes[c.note - 1]["timeline_sec"], "note_text": notes[c.note - 1].get("text", ""),
              "applied": c.applied, "summary": c.summary, "v2_time": c.v2_time, "why": c.why} for c in changes]
    print(f"\nV1 {cut1.zone_end:.2f}s, {len(notes)} notes, {sum(c.applied for c in changes)} applied, {sum(not c.applied for c in changes)} not applied\n")
    for it in items:
        print(f"  note {it['note']} [{it['note_time']}s] {'APPLIED    ' if it['applied'] else 'NOT APPLIED'}  {it['summary']}")
    if not removed:
        (args.out / "changes.json").write_text(json.dumps({"items": items}, indent=2))
        v2_xml.unlink(missing_ok=True)
        print("\nNothing could be applied to the timeline, so no V2 was written.")
        return 0

    print("\nChecks on the revised XML:")
    try:
        rows = verify(cut1, args.xml, v2_xml, removed)
    except TimelineError as e:
        print(f"REFUSING: revised XML does not load: {e}", file=sys.stderr)
        return 1
    bad = 0
    for name, ok, detail in rows:
        bad += ok is False
        print(f"  [{'SKIP' if ok is None else 'PASS' if ok else 'FAIL'}] {name}  {detail}")
    if bad:
        print(f"\n{bad} check(s) FAILED. {v2_xml.name} is NOT verified, do not use it.")
        return 1

    cut2 = load_cut(v2_xml)
    changes_payload = {"v1_duration": round(cut1.zone_end, 2), "v2_duration": round(cut2.zone_end, 2), "items": items}
    page = build(v2_xml, args.out, args.height, changes=changes_payload)
    (args.out / "changes.json").write_text(json.dumps(changes_payload, indent=2))
    pv = subprocess.run([sys.executable, str(HERE / "verify_preview.py"), str(args.out)], capture_output=True, text=True)
    print("\nPreview checks:\n" + pv.stdout.rstrip())
    if pv.returncode != 0:
        print("preview verification FAILED", file=sys.stderr)
        return 1
    print(f"\nV2 {cut2.zone_end:.2f}s  ->  {page}")
    if args.open:
        subprocess.run(["open", str(page)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
