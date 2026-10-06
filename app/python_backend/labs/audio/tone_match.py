#!/usr/bin/env python3
"""Bring a track's tone to a reference's by the least EQ that does it, and prove it by re-measuring.

    python3 labs/audio/tone_match.py --music track.wav --reference-features ref.json --out matched.wav [--target 0.85] [--max-cut-db 6]

Generated music comes out darker than a produced reference (Reel 3, 2026-10-06: eight takes against Artlist's "Aves - Bumpin'" all measured 0.65 to 0.78 of its tone centre, where the reference check needs 0.75 or more).
A producer would EQ it. This applies a low-shelf cut (at 180 Hz) in 1 dB steps, re-measures the tone centre after each with `reference_music.analyze` on the part of the track that gets used, and stops at the first cut that
reaches `target` x the reference's tone centre. It refuses if `max-cut-db` is not enough (a track that dark is the wrong track, not an EQ problem), and it never boosts. The cut also takes low-end mud from under the voice.
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
import make_audio as ma  # noqa: E402
import reference_music as rm  # noqa: E402

SHELF_HZ = 180


def eq_copy(src: Path, dst: Path, cut_db: float) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", f"lowshelf=f={SHELF_HZ}:g={-abs(cut_db)}", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(dst)], check=True)


def match_tone(src: Path, ref: dict, out: Path, target: float = 0.85, max_cut_db: float = 6.0, analyse=None) -> dict:
    """The matched copy at `out` and what was done: {cut_db, tone_before, tone_after, reference_tone, ratio_before, ratio_after}."""
    if analyse is None:
        import score_music as sm
        analyse = sm.analyse_groove
    want = ref["tone_centre_hz"] * target
    before = analyse(src)["tone_centre_hz"]
    if before >= want:
        eq_copy(src, out, 0.0)
        return {"cut_db": 0.0, "tone_before": before, "tone_after": before, "reference_tone": ref["tone_centre_hz"], "ratio_before": round(before / ref["tone_centre_hz"], 3), "ratio_after": round(before / ref["tone_centre_hz"], 3)}
    tmp = out.with_suffix(".try.wav")
    cut = 1.0
    while cut <= max_cut_db + 1e-9:
        eq_copy(src, tmp, cut)
        after = analyse(tmp)["tone_centre_hz"]
        if after >= want:
            shutil.move(str(tmp), str(out))
            return {"cut_db": cut, "tone_before": before, "tone_after": after, "reference_tone": ref["tone_centre_hz"], "ratio_before": round(before / ref["tone_centre_hz"], 3), "ratio_after": round(after / ref["tone_centre_hz"], 3)}
        cut += 1.0
    tmp.unlink(missing_ok=True)
    raise ma.AudioError(f"a {max_cut_db:.0f} dB low-shelf cut does not bring the track's tone centre ({before} Hz) to {target:.0%} of the reference's ({ref['tone_centre_hz']} Hz): use a different take")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--music", type=Path, required=True)
    ap.add_argument("--reference-features", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--target", type=float, default=0.85)
    ap.add_argument("--max-cut-db", type=float, default=6.0)
    a = ap.parse_args()
    try:
        r = match_tone(a.music, json.loads(a.reference_features.read_text()), a.out, a.target, a.max_cut_db)
    except ma.AudioError as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    print(f"tone centre {r['tone_before']} Hz -> {r['tone_after']} Hz with a {r['cut_db']:.0f} dB low-shelf cut at {SHELF_HZ} Hz (reference {r['reference_tone']} Hz: x{r['ratio_before']} -> x{r['ratio_after']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
