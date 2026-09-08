#!/usr/bin/env python3
"""Verify a real exported XML against the Reel contract.

Ryan, 2026-09-07: *"I can't carry you through 15 exports and manually do
it myself every time. So lets try to lock this in."* The unit tests in
`tests/test_reel_contract.py` guard the LOGIC; this guards an ACTUAL
export — the artifact that goes into Premiere — so a bad one is caught
before it's opened rather than after.

Every check here is something that shipped broken at least once:

  AUDIO-ENABLED     A cut exported silent because camera audio was muted
                    on the assumption a lav would cover it, when none did.
  AUDIO-SOURCETRACK Audio clipitems had no <sourcetrack>, so Premiere had
                    no mapping to a source channel.
  LAV-CORROBORATED  Uncorroborated mid-scoring lavs landed on the timeline
                    ("multiple audio sources... that dont belong").
  CUT-LENGTH        A "45-second Reel" ran 12:44.
  CUT-GRANULARITY   A 45s edit came out as 2 coarse slabs, not an edit.
  POOL-ON-TOPIC     Leftovers held shirt colours and fishing licences.
  POOL-NO-OVERLAP   Leftovers duplicated footage already in the cut.
  POOL-NO-DUPLICATES Two identical leftover clips landed on the timeline.
  ZONE-GAP          The two zones ran together with no separation.

Usage:
    python3 safety_net/verify_export.py <export.xml> [--idea <idea.json>]
                                        [--target-sec N]

The XML alone is enough for the audio and structural checks. Pass the
idea JSON to also check the cut/pool relationship, and --target-sec to
check the agreed length.

Exit code 0 = every applicable check passed. 1 = at least one failed.
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Same window Ryan set: "if i say 45 second edit, it can do 30-1 min-ish".
DURATION_BUFFER_SEC = 15.0
# A real edit is many deliberate clips. His own 65s reference uses 11.
MIN_CLIPS_PER_MINUTE = 6.0
# Minimum separation between the tight cut and the selects pool.
MIN_ZONE_GAP_SEC = 20.0


class Report:
    def __init__(self):
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, ok, detail))

    def skip(self, name: str, why: str) -> None:
        self.rows.append((name, None, why))

    def render(self) -> bool:
        width = max(len(n) for n, _, _ in self.rows) if self.rows else 10
        failed = 0
        for name, ok, detail in self.rows:
            if ok is None:
                mark = "SKIP"
            elif ok:
                mark = "PASS"
            else:
                mark = "FAIL"
                failed += 1
            line = f"  [{mark}] {name.ljust(width)}"
            if detail:
                line += f"  {detail}"
            print(line)
        print()
        if failed:
            print(f"{failed} check(s) FAILED — do not ship this export.")
        else:
            print("All applicable checks passed.")
        return failed == 0


def _seq_for_cut(root) -> ET.Element | None:
    """The cut's own sequence, not the All-Synced-A-Roll reference bin."""
    best = None
    for seq in root.iter("sequence"):
        name = seq.findtext("name") or ""
        if name.strip().lower().startswith("all synced"):
            continue
        if best is None:
            best = seq
    return best


def check_xml(path: Path, rep: Report) -> None:
    root = ET.parse(path).getroot()
    seq = _seq_for_cut(root)
    if seq is None:
        rep.check("SEQUENCE-PRESENT", False, "no cut sequence found in the XML")
        return
    rep.check("SEQUENCE-PRESENT", True, f'"{seq.findtext("name")}"')

    tb = float(seq.findtext("rate/timebase") or 60)

    # ---- video clip structure -> granularity + zone gap
    vt = seq.find("media/video/track")
    clips = vt.findall("clipitem") if vt is not None else []
    if not clips:
        rep.check("CUT-GRANULARITY", False, "no video clipitems")
        return

    spans = sorted(
        (int(c.findtext("start")), int(c.findtext("end"))) for c in clips
    )

    # A large blank stretch separates the tight cut from the pool.
    gap_idx = None
    for i in range(1, len(spans)):
        if (spans[i][0] - spans[i - 1][1]) / tb >= MIN_ZONE_GAP_SEC:
            gap_idx = i
            break

    if gap_idx is None:
        rep.skip("ZONE-GAP", "single zone (no pool) — nothing to separate")
        left = spans
    else:
        gap = (spans[gap_idx][0] - spans[gap_idx - 1][1]) / tb
        rep.check("ZONE-GAP", True, f"{gap:.0f}s between cut and pool")
        left = spans[:gap_idx]

    left_dur = sum(e - s for s, e in left) / tb
    per_min = len(left) / (left_dur / 60.0) if left_dur else 0
    rep.check(
        "CUT-GRANULARITY",
        per_min >= MIN_CLIPS_PER_MINUTE,
        f"{len(left)} clips in {left_dur:.0f}s "
        f"({per_min:.0f}/min, need >={MIN_CLIPS_PER_MINUTE:.0f})",
    )

    # ---- audio
    audio_clips = [
        ci
        for a in seq.find("media").findall("audio")
        for tr in a.findall("track")
        for ci in tr.findall("clipitem")
    ]
    if not audio_clips:
        rep.check("AUDIO-ENABLED", False, "no audio clipitems at all")
        rep.check("AUDIO-SOURCETRACK", False, "no audio clipitems at all")
        return

    any_enabled = any(
        (ci.findtext("enabled") or "TRUE").upper() == "TRUE" for ci in audio_clips
    )
    enabled_n = sum(
        1 for ci in audio_clips if (ci.findtext("enabled") or "TRUE").upper() == "TRUE"
    )
    rep.check(
        "AUDIO-ENABLED",
        any_enabled,
        f"{enabled_n}/{len(audio_clips)} audio clips enabled"
        + ("" if any_enabled else " — the sequence would import SILENT"),
    )

    missing_st = [ci for ci in audio_clips if ci.find("sourcetrack") is None]
    rep.check(
        "AUDIO-SOURCETRACK",
        not missing_st,
        "all audio clips map to a source channel"
        if not missing_st
        else f"{len(missing_st)} audio clip(s) missing <sourcetrack>",
    )

    names = {(ci.findtext("name") or "") for ci in audio_clips}
    lav = sorted(n for n in names if n.lower().endswith((".wav", ".aif", ".aiff")))
    rep.skip(
        "LAV-TRACKS",
        f"{len(lav)} synced source(s): {', '.join(lav)}" if lav else "camera audio only",
    )


def check_idea(idea_path: Path, target_sec: float | None, rep: Report) -> None:
    data = json.loads(idea_path.read_text()).get("data", {})
    cut = data.get("source_ranges") or []
    pool = data.get("pool_ranges") or []
    if not cut:
        rep.check("IDEA-CUT-PRESENT", False, "idea has no source_ranges")
        return

    cut_dur = sum(r["source_end_sec"] - r["source_start_sec"] for r in cut)

    if target_sec:
        lo, hi = target_sec - DURATION_BUFFER_SEC, target_sec + DURATION_BUFFER_SEC
        rep.check(
            "CUT-LENGTH",
            lo <= cut_dur <= hi,
            f"{cut_dur:.0f}s against a {target_sec:.0f}s target "
            f"(window {lo:.0f}-{hi:.0f}s)",
        )
    else:
        rep.skip("CUT-LENGTH", f"cut is {cut_dur:.0f}s; pass --target-sec to check it")

    if not pool:
        rep.skip("POOL-NO-OVERLAP", "no pool ranges")
        rep.skip("POOL-SAME-SOURCES", "no pool ranges")
        return

    overlaps = [
        (p, c)
        for p in pool
        for c in cut
        if p["source_file"] == c["source_file"]
        and p["source_start_sec"] < c["source_end_sec"]
        and p["source_end_sec"] > c["source_start_sec"]
    ]
    rep.check(
        "POOL-NO-OVERLAP",
        not overlaps,
        "leftovers never duplicate the cut"
        if not overlaps
        else f"{len(overlaps)} leftover/cut overlap(s) — duplicate footage",
    )

    # Duplicate footage WITHIN the pool. 2026-09-08, Ryan: "extra footage
    # has instances of duplicate footage." POOL-NO-OVERLAP only compared
    # pool against cut, so two identical leftover clips passed every check
    # and landed on his timeline twice.
    pool_sorted = sorted(
        (r["source_file"], r["source_start_sec"], r["source_end_sec"]) for r in pool
    )
    self_overlaps = [
        (f1, a1, b1, a2, b2)
        for i, (f1, a1, b1) in enumerate(pool_sorted)
        for (f2, a2, b2) in pool_sorted[i + 1:]
        if f1 == f2 and a1 < b2 and b1 > a2
    ]
    rep.check(
        "POOL-NO-DUPLICATES",
        not self_overlaps,
        "no leftover clip repeats another"
        if not self_overlaps
        else f"{len(self_overlaps)} duplicated/overlapping leftover clip(s), e.g. "
        f"{self_overlaps[0][1]:.1f}-{self_overlaps[0][2]:.1f} vs "
        f"{self_overlaps[0][3]:.1f}-{self_overlaps[0][4]:.1f}",
    )

    # The pool must stay in the files the cut drew from. This is the check
    # that would have caught 37.6 minutes across 6 camera files.
    cut_files = {r["source_file"] for r in cut}
    pool_files = {r["source_file"] for r in pool}
    stray = pool_files - cut_files
    pool_dur = sum(r["source_end_sec"] - r["source_start_sec"] for r in pool)
    rep.check(
        "POOL-SAME-SOURCES",
        not stray,
        f"{pool_dur:.0f}s across {len(pool_files)} file(s) the cut uses"
        if not stray
        else f"{len(stray)} source file(s) not in the cut: "
        + ", ".join(Path(s).name for s in sorted(stray)),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("xml", type=Path)
    ap.add_argument("--idea", type=Path, help="the idea JSON the export came from")
    ap.add_argument("--target-sec", type=float, help="agreed cut length in seconds")
    args = ap.parse_args()

    if not args.xml.exists():
        print(f"No such file: {args.xml}")
        return 1

    rep = Report()
    print(f"\nVerifying {args.xml.name}\n")
    check_xml(args.xml, rep)
    if args.idea:
        if args.idea.exists():
            check_idea(args.idea, args.target_sec, rep)
        else:
            rep.check("IDEA-FILE", False, f"not found: {args.idea}")
    else:
        rep.skip("IDEA-CHECKS", "pass --idea to check the cut/pool relationship")

    return 0 if rep.render() else 1


if __name__ == "__main__":
    sys.exit(main())
