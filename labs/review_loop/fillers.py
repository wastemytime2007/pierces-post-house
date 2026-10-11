"""Every "um" and "uh" in a cut, cut out: the whole-cut pass Ryan asked for (2026-10-11): "where there's an um or uh cut that audio and footage out".

Each video clip's own sound is transcribed again with fillers in the prompt (words.words_prompted), so Whisper writes the fillers it leaves out of a plain
transcript. Each filler is widened to take the whole sound (the onset is often heard late, the tail early) and kept only when the sound under it is voiced.
The result is a list of timeline spans; the caller cuts them with the same remove_range edit the notes use, so each cut is checked like any other.

    PRECUT_ROOT=~/precut-checkout python3 labs/review_loop/fillers.py "<cut>.xml"

Prints every filler found, with its time on the cut, and writes nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import ops  # noqa: E402
import timeline  # noqa: E402
import words as words_mod  # noqa: E402

PAD_BEFORE, PAD_AFTER = 0.3, 0.15      # same widening as the note's own filler (ops.FILLER_PAD_BEFORE/AFTER)
MIN_SEC = 0.08                          # a filler shorter than this is not a sound (it is a transcript artefact)


def _clip_fillers(clip, cut) -> list[tuple[float, float]]:
    """Filler spans of one video clip, on the timeline, found in its own camera file's sound over the clip's own source range."""
    # a window of at least PROMPT_WINDOW_SEC: a short one made the prompted pass write nonsense (2026-10-11); only fillers whose middle is in this clip are kept
    mid = (clip.src_in + clip.src_out) / 2
    lo = max(0.0, mid - ops.PROMPT_WINDOW_SEC / 2)
    dur = ops.PROMPT_WINDOW_SEC
    words = words_mod.words_prompted(clip.src_path, lo, dur)
    spans = []
    for i, w in enumerate(words):
        tok = (w.text or "").strip().strip(".,?!\"'")
        if not ops.FILLER_WORDS.match(tok) or not (MIN_SEC <= w.end - w.start <= 1.2):
            continue
        if not (clip.src_in <= (w.start + w.end) / 2 < clip.src_out):
            continue
        s, e = w.start - PAD_BEFORE, w.end + PAD_AFTER
        before = [p.end for p in words[:i] if p.end <= w.start and not ops.FILLER_WORDS.match((p.text or "").strip().strip(".,?!\"'"))]
        s = max(s, max(before) if before else lo)
        if not (clip.src_in - 1e-6 <= s and e <= clip.src_out + 1e-6):
            s, e = max(s, clip.src_in), min(e, clip.src_out)                     # a filler at the edge of the clip stays inside it
        if e - s < MIN_SEC:
            continue
        level = ops._rms10(ops._pcm(clip.src_path, s, e - s))
        room = ops._threshold(ops._rms10(ops._pcm(clip.src_path, max(0.0, lo), dur)))
        if len(level) == 0 or float(np.percentile(level, 50)) < room * 3.0:
            continue                                                             # no voice under it: the prompt wrote a filler over silence
        t0 = clip.tl_start + (s - clip.src_in)
        t1 = clip.tl_start + (e - clip.src_in)
        spans.append((round(max(t0, clip.tl_start), 3), round(min(t1, clip.tl_end), 3), tok))
    return spans


def find(cut) -> list[dict]:
    """Every filler on the cut, merged where two touch: [{"start", "end", "words"}] on the timeline, in order."""
    raw = []
    for clip in cut.video:
        for s, e, tok in _clip_fillers(clip, cut):
            raw.append({"start": s, "end": e, "word": tok})
    raw.sort(key=lambda r: r["start"])
    out: list[dict] = []
    for r in raw:
        if out and r["start"] <= out[-1]["end"] + 0.02:
            out[-1]["end"] = max(out[-1]["end"], r["end"])
            out[-1]["words"].append(r["word"])
        else:
            out.append({"start": r["start"], "end": r["end"], "words": [r["word"]]})
    return out


def main() -> int:
    cut = timeline.load_cut(Path(sys.argv[1]))
    found = find(cut)
    total = sum(f["end"] - f["start"] for f in found)
    for f in found:
        print(f"{f['start']:7.2f}-{f['end']:7.2f}s  {f['end'] - f['start']:.2f}s  {' '.join(f['words'])}")
    print(f"{len(found)} filler(s), {total:.2f}s of the {cut.zone_end:.2f}s cut")
    return 0


if __name__ == "__main__":
    sys.exit(main())
