"""Who is talking, and when, from each person's own recorder.

Each person on a shoot wears their own recorder, and a recorder hears its wearer 8 to 12 dB louder than the other person. So with two recorders matched to the camera's clock, whichever is louder at a moment is who
is talking. Used to switch the picture to the speaker (a cut to the person who starts talking, a little before they do) and to keep only that person's microphone live.

Standalone and file-free: `match_lavs` finds where the camera's audio falls in each person's recorder (the GCC-PHAT search in `sync_audio.py`, beside this file); `speaker_runs` turns two level tracks into
runs of speech; `pieces_for_cut` cuts a clip into pieces at the changes, on whole frames.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_audio as sa  # noqa: E402

FPS_NUM, FPS_DEN = 30000, 1001
SWITCH_LEAD = 0.12                                                                         # the picture cuts to the next speaker this long before they start talking
SPEAKER_MARGIN_DB, MIN_RUN_SEC, QUIET_DB = 3.0, 0.4, -45.0


def speaker_runs(levels: dict[str, list[float]], step: float = 0.1, margin: float = SPEAKER_MARGIN_DB, min_run: float = MIN_RUN_SEC, quiet: float = QUIET_DB) -> list[tuple[float, float, str]]:
    """[(start_sec, end_sec, person)] from each person's recorder level (dB per `step`). A person is talking when their own recorder is `margin` dB louder than the other's; a frame where neither is clear (a pause, or both
    about equal) keeps the previous speaker; a run shorter than `min_run` is absorbed into its neighbour, so a cough or a reaction does not cut the picture."""
    names = sorted(levels)
    n = min(len(v) for v in levels.values())
    lab: list[str | None] = []
    for i in range(n):
        ranked = sorted(names, key=lambda k: -levels[k][i])
        top, other = ranked[0], ranked[1]
        lab.append(top if levels[top][i] >= quiet and levels[top][i] - levels[other][i] >= margin else None)
    first = next((x for x in lab if x), None)
    if first is None:
        return []
    cur = first
    for i, x in enumerate(lab):
        if x:
            cur = x
        lab[i] = cur
    runs = []
    for i, x in enumerate(lab):
        if runs and runs[-1][2] == x:
            runs[-1][1] = i + 1
        else:
            runs.append([i, i + 1, x])
    need = int(round(min_run / step))
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for k, r in enumerate(runs):
            if r[1] - r[0] < need:
                nb = k - 1 if k > 0 and (k == len(runs) - 1 or runs[k - 1][1] - runs[k - 1][0] >= runs[k + 1][1] - runs[k + 1][0]) else k + 1
                runs[nb][0], runs[nb][1] = min(runs[nb][0], r[0]), max(runs[nb][1], r[1])
                del runs[k]
                changed = True
                break
        merged = []
        for r in runs:
            if merged and merged[-1][2] == r[2]:
                merged[-1][1] = r[1]
            else:
                merged.append(list(r))
        runs = merged
    return [(r[0] * step, r[1] * step, r[2]) for r in runs]


def match_lavs(cam_wav: Path, t0: float, t1: float, mic_dir: Path) -> dict[str, tuple[Path, float, float]]:
    """{person: (recorder file, where camera time t0 falls in that file, score)}: the camera's own audio from t0 to t1 is searched for in every recorder named for a person; the best-scoring file per person is kept,
    and only if its peak clears the sync threshold."""
    cam = sa.load_wav(cam_wav)
    moment = cam[int(t0 * sa.SR): int(t1 * sa.SR)]
    best: dict[str, tuple[Path, float, float]] = {}
    for lav in sorted(mic_dir.glob("*.WAV")):
        person = "Bob" if "Bob" in lav.stem else "Mitch" if "Mitch" in lav.stem else None
        if person is None or lav.name.startswith("._"):
            continue
        x = sa.read_window(lav, 0, 36000)
        if len(x) < len(moment):
            continue
        n = sa.next_fast(len(x) + len(moment))
        off, sc = sa.gcc_phat(sa._fft.rfft(x.astype(np.float32), n), n, moment)
        if sc >= sa.MIN_SCORE and sc > best.get(person, (None, 0, 0.0))[2]:
            best[person] = (lav, off, sc)
    return best


def match_lavs_timed(cam_wav: Path, wav_t0: float, mic_dir: Path, chunk: float = 30.0) -> dict[str, list[tuple[float, float, Path, float, float]]]:
    """{person: [(camera_start, camera_end, recorder file, delta, score)]}: like match_lavs, but a person may have several recorders (a shoot restarts a recorder, or a card fills) so the camera's audio is
    searched for in `chunk` second pieces and each piece keeps its own best file. `delta` is what to add to a camera time to get the position in the recorder file; times are camera seconds, `wav_t0` being the
    camera time the audio in `cam_wav` starts at. A piece no recorder of a person matches leaves that stretch uncovered for them (`segments_for` then borrows the nearest piece)."""
    cam = sa.load_wav(cam_wav)
    total = len(cam) / sa.SR
    n_chunk = int(chunk * sa.SR)
    if len(cam) <= n_chunk * 1.5:
        spans = [(0, len(cam))]
    else:
        k = int(np.ceil(len(cam) / n_chunk))
        n_chunk = int(np.ceil(len(cam) / k))
        spans = [(i * n_chunk, min(len(cam), (i + 1) * n_chunk)) for i in range(k)]
    best: dict[tuple[str, int], tuple[Path, float, float]] = {}
    for lav in sorted(mic_dir.glob("*.WAV")):
        person = "Bob" if "Bob" in lav.stem else "Mitch" if "Mitch" in lav.stem else None
        if person is None or lav.name.startswith("._"):
            continue
        x = sa.read_window(lav, 0, 36000)
        longest = max(b - a for a, b in spans)
        if len(x) < longest:
            continue
        n = sa.next_fast(len(x) + longest)
        spec = sa._fft.rfft(x.astype(np.float32), n)
        for i, (a, b) in enumerate(spans):
            off, sc = sa.gcc_phat(spec, n, cam[a:b])
            if sc >= sa.MIN_SCORE and sc > best.get((person, i), (None, 0, 0.0))[2]:
                best[(person, i)] = (lav, off, sc)
    out: dict[str, list] = {}
    for (person, i), (lav, off, sc) in sorted(best.items()):
        a, b = spans[i]
        out.setdefault(person, []).append((wav_t0 + a / sa.SR, wav_t0 + b / sa.SR, lav, off - a / sa.SR - wav_t0, sc))
    return out


def segments_for(segs: list, a: float, b: float) -> list[tuple[float, float, Path, float]]:
    """[(from, to, file, delta)] covering camera time a..b for one person: each stretch belongs to the matched piece it falls in, and a stretch outside every piece (before the first, after the last, or in a gap)
    goes to the nearest piece's file, which keeps its offset across the whole recording."""
    segs = sorted(segs, key=lambda g: g[0])
    cuts = [(segs[i][1] + segs[i + 1][0]) / 2 for i in range(len(segs) - 1)]
    bounds = [-1e12] + cuts + [1e12]
    out = []
    for g, lo, hi in zip(segs, bounds, bounds[1:]):
        f, t = max(a, lo), min(b, hi)
        if t - f > 1e-6:
            out.append((f, t, g[2], g[3]))
    return out


def level_track_timed(segs: list, start: float, dur: float, step: float = 0.1) -> list[float]:
    """Level (dB per `step`) of one person's recorder(s) over camera time start..start+dur; silence (-120 dB) where a file has nothing recorded."""
    n = int(round(dur / step))
    out = [-120.0] * n
    for f, t, path, delta in segments_for(segs, start, start + dur):
        i0 = int(round((f - start) / step))
        lv = level_track(path, f + delta, t - f, step)
        for j, v in enumerate(lv):
            if 0 <= i0 + j < n:
                out[i0 + j] = v
    return out


def level_track(path: Path, start: float, dur: float, step: float = 0.1) -> list[float]:
    x = sa.read_window(path, max(0.0, start), dur)
    n = int(step * sa.SR)
    return [20 * np.log10(max(1e-6, float(np.sqrt((x[i * n:(i + 1) * n] ** 2).mean())))) for i in range(len(x) // n)]


def pieces_for_cut(runs: list[tuple[float, float, str]], dur: float, fps: float = FPS_NUM / FPS_DEN, lead: float = SWITCH_LEAD) -> list[tuple[int, int, str]]:
    """[(first_frame, frame_count, person)] covering the whole cut (`dur` seconds from its start): the picture switches to the next speaker `lead` before their first word, on a whole frame, and never leaves a piece under 0.3 s."""
    total = int(round(dur * fps))
    if not runs:
        return []
    marks, names = [0], [runs[0][2]]
    for start, _end, who in runs[1:]:
        f = int(round(max(0.0, start - lead) * fps))
        if f - marks[-1] >= int(0.3 * fps) and total - f >= int(0.3 * fps):
            marks.append(f)
            names.append(who)
    ends = marks[1:] + [total]
    out = []
    for m, e, who in zip(marks, ends, names):
        if out and out[-1][2] == who:
            out[-1] = (out[-1][0], e - out[-1][0], who)
        else:
            out.append((m, e - m, who))
    return out


def cut_pieces(lavs: dict[str, tuple[Path, float, float]], t0: float, in_sec: float, dur: float) -> tuple[list[tuple[int, int, str]], list[tuple[float, float, str]]]:
    """The pieces of one cut and the speaker runs they came from. `lavs` is match_lavs' answer for a stretch starting at camera time `t0`; the cut starts at camera time `in_sec` and runs `dur` seconds.
    With fewer than two recorders matched there is no speaker timeline, so the cut stays one piece with no person named (the caller decides who is on screen)."""
    fps = FPS_NUM / FPS_DEN
    if len(lavs) < 2:
        return [(0, int(round(dur * fps)), "")], []
    levels = {who: level_track(f, o + (in_sec - t0), dur) for who, (f, o, _sc) in lavs.items()}
    runs = speaker_runs(levels)
    pcs = pieces_for_cut(runs, dur)
    return (pcs or [(0, int(round(dur * fps)), "")]), runs
