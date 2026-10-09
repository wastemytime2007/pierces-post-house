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
import re
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
from layers import find_layers, layer_warnings  # noqa: E402
import words as words_mod  # noqa: E402
from ops import interpret, level_db, validate  # noqa: E402
from timeline import Cut, TimelineError, _seq_for_cut, load_cut  # noqa: E402


def _video_items(xml: Path, zone_f: int, before: bool) -> list[tuple[int, int, int, int]]:
    seq = _seq_for_cut(ET.parse(xml).getroot())
    items = seq.find("media/video/track").findall("clipitem")                 # the cut's own track, as load_cut reads it; stills (no in/out) are not footage
    rows = [(int(c.findtext("start")), int(c.findtext("end")), int(c.findtext("in")), int(c.findtext("out"))) for c in items if c.findtext("in") is not None and c.findtext("out") is not None]
    return sorted(r for r in rows if (r[0] < zone_f) == before)


def verify(cut1: Cut, xml1: Path, xml2: Path, delta_sec: float, extra_out: float = 0.0, extra_in: float = 0.0) -> list[tuple[str, bool | None, str]]:
    rows: list[tuple[str, bool | None, str]] = []
    cut2 = load_cut(xml2)                                # bounds-checks every clip against real media
    rows.append(("RELOADS-AND-BOUNDS", True, f"{len(cut2.video)} clips, every range inside its real file"))
    tol = 2.0 / cut1.fps
    rows.append(("RIPPLE-LENGTH", abs((cut1.zone_end + delta_sec) - cut2.zone_end) <= tol,
                 f"V1 {cut1.zone_end:.2f}s {delta_sec:+.2f}s = {cut1.zone_end + delta_sec:.2f}s, V2 is {cut2.zone_end:.2f}s"))
    gaps = [abs(b.tl_start - a.tl_end) for a, b in zip(cut2.video, cut2.video[1:])]
    rows.append(("CONTIGUOUS", all(g < 1e-3 for g in gaps), f"largest seam {max(gaps or [0]) * 1000:.2f}ms"))

    slack = 2.0 / cut1.fps
    stray = [p for p in cut2.video
             if not any(p.src_path == c.src_path and p.src_in >= c.src_in - extra_in - slack and p.src_out <= c.src_out + extra_out + slack for c in cut1.video)]
    ext_txt = "".join([f" (plus the measured {extra_out:.2f}s extension at an end)" if extra_out else "", f" (plus the measured {extra_in:.2f}s extension at a start)" if extra_in else ""])
    rows.append(("NO-NEW-FOOTAGE", not stray,
                 ("every V2 piece lies inside a V1 clip" + ext_txt)
                 if not stray else f"{len(stray)} piece(s) outside V1 ranges"))

    def lav_offset(a, v):
        """The lav's source time minus the camera's source time at the instant the piece starts. A piece that begins mid-clip (a lav split around a bleep) is measured at its own start, not at the clip's."""
        return a.src_in - (v.src_in + (a.tl_start - v.tl_start))

    v1_off: dict[str, list[float]] = {}
    for a in cut1.audio:
        v = next((c for c in cut1.video if c.tl_start - 0.02 <= a.tl_start < c.tl_end), None)
        if v:
            v1_off.setdefault(a.src_path, []).append(lav_offset(a, v))
    bad = []
    for a in cut2.audio:
        v = next((c for c in cut2.video if c.tl_start - 0.02 <= a.tl_start < c.tl_end), None)
        if v and a.src_path in v1_off:
            off = lav_offset(a, v)
            if not any(abs(off - o) < 0.05 for o in v1_off[a.src_path]):
                bad.append((a.tl_start, off))
    rows.append(("LAV-SYNC-PRESERVED", not bad, "every lav piece keeps its V1 offset to camera" if not bad else f"{len(bad)} piece(s) drifted, first at {bad[0][0]:.2f}s"))

    z1 = round(cut1.zone_end * cut1.fps)
    p1, p2 = _video_items(xml1, z1, False), _video_items(xml2, z1, False)
    lost_front = lost_tail = 0.0
    pool_ok = len(p1) == len(p2)
    for (s1, e1, i1, o1), (s2, e2, i2, o2) in zip(p1, p2):
        k = (o1 - i1) / (e1 - s1)
        front_f, tail_f = (i2 - i1) / k, (o1 - o2) / k                    # timeline frames given up at its start (to a clip that grew at its end) and at its end (to a clip that now starts earlier)
        shrank = s1 == s2 and i2 >= i1 and o2 <= o1 and e2 <= e1 and abs(front_f + tail_f - (e1 - e2)) <= 2
        if not shrank:
            pool_ok = False
            continue
        lost_front = max(lost_front, front_f / cut1.fps)
        lost_tail = max(lost_tail, tail_f / cut1.fps)
    lost = max(lost_front, lost_tail)
    pool_ok = pool_ok and lost_front <= extra_out + 2.0 / cut1.fps and lost_tail <= extra_in + 2.0 / cut1.fps
    rows.append(("POOL-ONLY-LOST-WHAT-THE-CUT-GAINED", pool_ok,
                 "selects pool identical to V1" if p1 == p2 else f"pool only lost footage the cut now holds (trimmed by up to {lost:.2f}s, front or end)"))

    rep = verify_export.Report()
    verify_export.check_xml(xml2, rep)
    rep1 = verify_export.Report()                                       # the version being revised is held to the same checks: a revision is answerable for what IT breaks, not for a defect it inherited
    verify_export.check_xml(xml1, rep1)
    inherited = {n for n, ok, _d in rep1.rows if ok is False}
    for name, ok, detail in rep.rows:
        if ok is False and name in inherited:
            rows.append(("verify_export " + name, None, detail + "  [ALREADY FAILING in the version this was made from, so not caused by this revision; the export itself needs fixing]"))
        else:
            rows.append(("verify_export " + name, ok, detail))
    return rows


def verify_motion(cut1: Cut, changes, cut2: Cut) -> list[tuple[str, bool | None, str]]:
    """Each reframe, read back from the REVISED XML: the clip that starts where it did before now has the new vertical position and the same scale."""
    rows = []
    for c in changes:
        k = c.check
        if not c.applied or not k or k.get("kind") != "motion":
            continue
        p = next((v for v in cut2.video if v.src_path == k["src_path"] and abs(v.src_in - k["src_in"]) < 2.0 / cut1.fps), None)
        if p is None or not p.motion:
            rows.append((f"note {c.note} REFRAME-WRITTEN", False, "no clip with a position starts where that clip did"))
            continue
        ok = abs(p.motion[2] - k["vert"]) < 1e-5 and abs(p.motion[0] - k["scale"]) < 0.01
        rows.append((f"note {c.note} REFRAME-WRITTEN", ok, f"clip now has vertical position {p.motion[2]:+.4f} (was {k['vert_before']:+.4f}) at scale {p.motion[0]:g}"))
    return rows


def verify_follow(changes, cut2: Cut, xml2: Path | None = None) -> list[tuple[str, bool | None, str]]:
    """Each follow_speaker, read back from the REVISED XML (follow_speaker.check_written)."""
    import follow_speaker as fs
    from render_preview import source_dims
    rows = []
    for c in changes:
        if c.applied and c.check and c.check.get("kind") == "follow":
            ok, detail = fs.check_written(cut2, c.check, source_dims)
            rows.append((f"note {c.note} FOLLOWS-SPEAKER", ok, detail))
            if xml2 is not None:
                ok2, detail2 = fs.check_voices(xml2, cut2, c.check)
                rows.append((f"note {c.note} OWN-RECORDER-LIVE", ok2, detail2))
    return rows


def verify_render(changes, preview: Path) -> list[tuple[str, bool | None, str]]:
    """Re-check the measured edits on the finished V2 render and the source, not on the plan."""
    rows: list[tuple[str, bool | None, str]] = []
    for c in changes:
        k = c.check
        if not c.applied or not k:
            continue
        if k["kind"] == "quiet_at":
            db = level_db(k["path"], k["t"] - 0.03, 0.03)
            rows.append((f"note {c.note} CUT-IN-QUIET", db <= k["thresh_db"] + 1.0,
                         f"level at the new cut point {db:.0f} dB, room-level threshold {k['thresh_db']:.0f} dB"))
        elif k["kind"] == "quiet_from":
            db = level_db(k["path"], k["t"], 0.03)
            rows.append((f"note {c.note} START-IN-QUIET", db <= k["thresh_db"] + 1.0,
                         f"level just after the new start {db:.0f} dB, room-level threshold {k['thresh_db']:.0f} dB"))
        elif k["kind"] == "seam_text":
            after = [w for w in words_mod.words_in(str(preview), c.v2_time - 0.3, 4.0) if w.start >= c.v2_time - 0.1]
            hit = words_mod.find_phrase(after, k["phrase"])
            want = words_mod.tokens(k["phrase"])
            if hit is None and len(want) >= 3 and len(want[0]) <= 3:     # Whisper drops a short first word right at a seam ("So" in "So with the septic systems", 2026-10-08): the rest heard first is the phrase
                hit = words_mod.find_phrase(after, " ".join(want[1:]))
            rows.append((f"note {c.note} SEAM-TEXT", hit is not None and hit[0] <= 1,
                         f'V2 audio from the seam reads: "{words_mod.heard(after[:9])}"'))
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

        m = re.match(r"^(.*)_v(\d+)$", args.xml.stem)
        n_in = int(m.group(2)) if m else 1
        lab_in, lab_out = f"V{n_in}", f"V{n_in + 1}"
        v2_xml = args.out / f"{m.group(1) if m else args.xml.stem}_v{n_in + 1}.xml"
        changes, delta = apply_ops(args.xml, v2_xml, cut1, ops, notes)
    except TimelineError as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1

    items = [{"note": c.note, "note_time": notes[c.note - 1]["timeline_sec"], "note_text": notes[c.note - 1].get("text", ""),
              "applied": c.applied, "summary": c.summary, "v2_time": c.v2_time, "why": c.why,
              "op": c.op, "removed": [round(c.removed[0], 3), round(c.removed[1], 3)] if c.removed else None,
              "extended_sec": round(c.check["ext"], 3) if c.check and c.check.get("kind") in ("quiet_at", "quiet_from", "joined") else None,
              "follow": c.check if c.check and c.check.get("kind") == "follow" else None} for c in changes]
    print(f"\n{lab_in} {cut1.zone_end:.2f}s, {len(notes)} notes, {sum(c.applied for c in changes)} applied, {sum(not c.applied for c in changes)} not applied\n")
    for it in items:
        print(f"  note {it['note']} [{it['note_time']}s] {'APPLIED    ' if it['applied'] else 'NOT APPLIED'}  {it['summary']}")
    if not any(c.applied and c.op != "unsupported" for c in changes):
        (args.out / "changes.json").write_text(json.dumps({"items": items}, indent=2))
        v2_xml.unlink(missing_ok=True)
        print(f"\nNothing could be applied to the timeline, so no {lab_out} was written.")
        return 0

    print("\nChecks on the revised XML:")
    try:
        def ext_of(kinds, side=None):
            return max([c.check["ext"] for c in changes if c.applied and c.check and c.check.get("kind") in kinds and (side is None or c.check.get("side") == side)] or [0.0])
        extra_out = max(ext_of(("quiet_at",)), ext_of(("joined",), "end"))
        extra_in = max(ext_of(("quiet_from",)), ext_of(("joined",), "front"))
        rows = verify(cut1, args.xml, v2_xml, delta, extra_out, extra_in)
        rows += verify_motion(cut1, changes, load_cut(v2_xml))
        rows += verify_follow(changes, load_cut(v2_xml), v2_xml)
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
    warnings = layer_warnings(find_layers(args.xml), find_layers(v2_xml))
    for w in warnings:
        print(f"\nWARNING (layers): {w}")
    changes_payload = {"v1_duration": round(cut1.zone_end, 2), "v2_duration": round(cut2.zone_end, 2), "items": items,
                       "from_label": lab_in, "to_label": lab_out, "layer_warnings": warnings}
    page = build(v2_xml, args.out, args.height, changes=changes_payload)
    (args.out / "changes.json").write_text(json.dumps(changes_payload, indent=2))
    pv = subprocess.run([sys.executable, str(HERE / "verify_preview.py"), str(args.out)], capture_output=True, text=True)
    print("\nPreview checks:\n" + pv.stdout.rstrip())
    if pv.returncode != 0:
        print("preview verification FAILED", file=sys.stderr)
        return 1
    render_rows = verify_render(changes, args.out / "preview.mp4")
    if render_rows:
        print("\nRe-checking the measured edits on the finished render:")
        for name, ok, detail in render_rows:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")
        if any(ok is False for _n, ok, _d in render_rows):
            print("\nA measured edit did not hold up on the render. Do not use this V2.", file=sys.stderr)
            return 1
    print(f"\n{lab_out} {cut2.zone_end:.2f}s  ->  {page}")
    if args.open:
        subprocess.run(["open", str(page)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
