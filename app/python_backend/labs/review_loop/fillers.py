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

PAD_BEFORE, PAD_AFTER = 0.10, 0.08      # same widening as the note's own filler (ops.FILLER_PAD_BEFORE/AFTER)
MIN_SEC = 0.08                          # a filler shorter than this is not a sound (it is a transcript artefact)


WINDOW_SEC = 40.0                      # long windows: a 20 s one made Whisper loop ("um, um, um, ...", hundreds of them); 30-45 s gave only the real filler (2026-10-11)
GRID_SEC = 10.0                        # windows start on this grid, so clips from the same stretch of one file share one transcription
LOOP_FILLERS = 6                       # more fillers than this in one window is Whisper looping, not a speaker: the window's fillers are all discarded
_CACHE: dict[tuple, list] = {}


def _window_words(path: str, mid: float) -> tuple[float, list]:
    lo = max(0.0, (mid // GRID_SEC) * GRID_SEC - GRID_SEC)
    key = (path, lo)
    if key not in _CACHE:
        _CACHE[key] = words_mod.words_prompted(path, lo, WINDOW_SEC)
    return lo, _CACHE[key]


def _is_filler(w) -> bool:
    return bool(ops.FILLER_WORDS.match((w.text or "").strip().strip(".,?!\"'")))


def _clip_fillers(clip, cut) -> list[tuple[float, float, str]]:
    """Filler spans of one video clip, on the timeline, found in its own camera file's sound: the prompted transcription of a long window around the clip, the fillers whose
    middle is in this clip, each widened to the whole sound and kept only when it is voiced."""
    lo, words = _window_words(clip.src_path, (clip.src_in + clip.src_out) / 2)
    fillers = [w for w in words if _is_filler(w)]
    if len(fillers) > LOOP_FILLERS:
        return []                                                               # Whisper looped: nothing in this window can be trusted
    spans = []
    for i, w in enumerate(words):
        if not _is_filler(w) or not (MIN_SEC <= w.end - w.start <= 1.2):
            continue
        if not (clip.src_in <= (w.start + w.end) / 2 < clip.src_out):
            continue
        s, e = w.start - PAD_BEFORE, w.end + PAD_AFTER
        before = [p.end for p in words[:i] if p.end <= w.start and not _is_filler(p)]
        s = max(s, max(before) if before else lo)
        if e - s < MIN_SEC:
            continue
        level = ops._rms10(ops._pcm(clip.src_path, w.start, w.end - w.start))      # the filler word itself, not the padding around it (that includes quiet)
        room = ops._threshold(ops._rms10(ops._pcm(clip.src_path, lo, WINDOW_SEC)))
        if len(level) == 0 or float(np.percentile(level, 60)) < room * 2.0:
            continue                                                             # no voice under it: the prompt wrote a filler over silence
        t0 = ops._src_to_tl(cut, clip.src_path, s)                                  # through the clip that shows it: a filler can run over a cut point into the next clip
        if t0 is None:
            continue
        t1 = min(t0 + (e - s), cut.zone_end)
        spans.append((round(max(t0, 0.0), 3), round(t1, 3), (w.text or "").strip().strip(".,?!\"'")))
    return spans


def find(cut) -> list[dict]:
    """Every filler on the cut, merged where two touch: [{"start", "end", "words"}] on the timeline, in order."""
    raw = []
    for clip in cut.video:
        for s, e, tok in _clip_fillers(clip, cut):                              # stage 1: a long window finds where the fillers are (its word edges are smeared)
            span = ops._prompted_filler_span(cut, (s + e) / 2, 1.0)             # stage 2: the tight pass the person approved by ear sets the exact span, and must agree
            if span is not None:
                raw.append({"start": span[0], "end": span[1], "word": tok})
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
