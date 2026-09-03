"""posthouse.broll_interpret — B-roll frame-rate interpretation decisions.

**Genuinely new capability, not harvested.** Confirmed PreCut has nothing
like this: its exporter declares every clip's own probed native rate and
lets Premiere's own (non-destructive, non-speed-changing) sequence conform
handle any mismatch on the timeline (`exporter.py`'s own docstring: "A-roll
and B-roll source files keep their native frame rate — Premiere handles
the rate conform when they land on a different-rate sequence"). Ryan wants
something stronger for B-roll specifically: a real, baked-in speed change
(what Premiere's "Modify > Interpret Footage > Frame Rate" does by hand),
so B-roll is already at the project's target rate before anyone drags it
onto a timeline. His own words (2026-09-03): "any framerate that doesn't
match the intended export framerate to be interpreted to that framerate
for items labeled b-roll... we would never interpret up... realistically
all exports will happen in 24(23.976) or 30(29.97)... based on the footage
captured we'd export in whichever the smallest captured framerate was."
A-roll is never touched by any of this, regardless of its own native rate.

**Two real approaches tried and falsified in actual Premiere, not by
reasoning:**

1. Declaring a mismatched rate directly in the FCP7 XML — tested, and
   Premiere re-probes the actual media and ignores it.

2. Reproducing Ryan's own real "duplicate the Project-panel item, apply
   Interpret Footage to one" workflow as sequence-clipitem frame math
   (the interpreted instance's duration set to the clip's raw frame
   count against the sequence's rate, matching what his own real FCP7
   XML export of that workflow appeared to show). Built as a pre-placed
   "B-Roll (Interpreted)" reference sequence, verified structurally
   correct against real files — and still wrong: Ryan tested it and it
   didn't reproduce the effect. His own correction: "Interpretation
   doesn't happen at the sequence level, it happens at the clip/footage
   level. The sequence being set at a different framerate doesn't make
   the clips on that sequence interpret to that framerate." Whatever
   Premiere's own export was actually encoding, it wasn't reproducible
   by fabricating the same numbers from outside a live Premiere session.
   Neither of the two things I've tried survived real-Premiere testing —
   two failures on the same problem, so this file no longer carries
   either implementation (search git history for `itsscale` or
   `build_broll_reference_sequence` if ever revisiting them).

**What's being built now, one proven step at a time, per Ryan's own
plan** (2026-09-03): "Instead of thinking of this as one movement that
does everything, what if we tackle it one problem at a time. First get
the xml to import all framerates above the [target] two times... If
imported footage is greater than selected end framerate, then import
those footage clips twice. Then we tackle the next step which is finding
a way to select the secondarily imported footage clips and handle the
modify-interpret footage function within premiere." This module supplies
only the DECISION logic (what's the target rate, does a given clip need
it) for step one — the actual XML duplication lives in
`precut_pipeline/multi_exporter.py`'s B-roll master-clip loop, since
duplicating bin entries is structural to that loop, not something
postprocessable from outside it. Step two (actually triggering Interpret
Footage on the duplicate) is not yet attempted.

**Target rate**: the numeric minimum native fps among ALL real footage
declared in the project (both A-roll and B-roll) — not a rounding/family
heuristic. Ryan's "realistically 24 or 30" is a description of what his
footage looks like in practice, not an algorithmic constraint; the rule
itself is a plain minimum. A clip already at or below the target is never
touched (interpreting "up" is meaningless — you cannot invent frames that
don't exist — and is explicitly banned regardless).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional

DEFAULT_TOLERANCE = 0.02  # 2%: a clip already essentially AT the target
                          # (e.g. 29.97 vs a computed target of 29.970297)
                          # should never trigger a needless "interpretation"
                          # for a rounding difference.


def probe_native_fps(path: Path, ffprobe: str = "ffprobe") -> Optional[float]:
    """A file's real native fps via r_frame_rate (the stream's own
    timebase -- the same field multi_exporter.py's _safe_probe prefers,
    for the same reason: robust against slo-mo/VFR duration math)."""
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    raw = proc.stdout.strip()
    if "/" not in raw:
        return None
    try:
        num, den = raw.split("/")
        den = float(den)
        return float(num) / den if den else None
    except ValueError:
        return None


def compute_target_fps(native_fps_list: List[float]) -> Optional[float]:
    """The project's B-roll interpretation target: the numeric minimum
    native fps among all real captured footage. None if no valid fps
    values were given (caller should then skip interpretation entirely
    rather than guess at a target)."""
    valid = [f for f in native_fps_list if f and f > 0]
    return min(valid) if valid else None


def needs_interpretation(native_fps: float, target_fps: float,
                          tolerance: float = DEFAULT_TOLERANCE) -> bool:
    """True only when native_fps is genuinely higher than the target
    (never up -- a native rate at or below target is left alone)."""
    if native_fps is None or target_fps is None or target_fps <= 0:
        return False
    return native_fps > target_fps * (1 + tolerance)
