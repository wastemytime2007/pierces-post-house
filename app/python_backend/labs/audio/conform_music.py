"""Put a beat of the music on every event in a cut: the title's words, the labels, the transitions.

Ryan, 2026-10-06: "Music hits should also happen in line with text on screen and transitions". A generated track has its own
beat grid; the events in a cut (a title line appearing, a cut, a label) fall wherever the speech put them. An editor lines the two up
by nudging the tempo between events so that a beat lands on each one. This does that:

  1. fit_grid: the track's beat grid (a generated track is quantised, so a straight line through the beats fits; if it does not, this refuses)
  2. find_drop: the beat where the full groove comes in (after a sparse intro). That beat is put on the FIRST event, so the music arrives with a hit
  3. plan: between consecutive events, a whole or half number of beats of the track is time-stretched to fill exactly the time between them. The tempo
     is scaled a little overall (within SEARCH) to make the stretches as small as possible; a stretch over MAX_STRETCH is reported, not hidden
  4. render: the segments are joined (4 ms cross-fades), each event gets a short accent (a few dB, decaying), the music stops on the downbeat of
     the LAST event (the wallpaper reel's music also stops at a transition, leaving its last scene bare), and the file is padded so time 0 is the reel's time 0
  5. measure_hits: on the finished file, how far the nearest beat onset is from each event, in milliseconds

    python3 labs/audio/conform_music.py --music track.wav --events events.json --total 29.3 --out conformed.wav
events.json: {"events": [{"t": 1.168, "name": "title"}, ...]}  (reel seconds, first = where the drop lands, last = where the music stops)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

HOP = 512
SR_AN = 22050
SEARCH = (0.94, 1.06)                    # overall tempo scaling tried
MAX_STRETCH = 0.08                       # a segment sped up or slowed by more than this is reported
FADE = 0.004                             # seconds of cross-fade at each join
ACCENT_DB = 3.0
ACCENT_DECAY = 0.12
STOP_BEATS = 1.0                         # how long the music plays on after the last event before it ends ("stop" tail)
END_FADE = 1.5                           # the "run" tail plays to the end of the reel and fades over its last this many seconds
MAX_GRID_ERR = 0.06                      # a beat further than this from the fitted grid means the track is not quantised


class ConformError(Exception):
    pass


def fine_flux(y: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(times, broadband flux, low-band flux) at 2.9 ms steps with a 23 ms window: sharp enough to place a kick to within about 10 ms."""
    import librosa
    S = np.abs(librosa.stft(y, n_fft=512, hop_length=64))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=512)
    d = np.maximum(0, np.diff(S, axis=1))
    t = (np.arange(d.shape[1]) + 1) * 64 / sr
    return t, d.sum(axis=0), d[freqs < 250].sum(axis=0)


def _peaks_near(t: np.ndarray, fl: np.ndarray, centres: np.ndarray, half: float = 0.04) -> tuple[np.ndarray, np.ndarray]:
    ts, vs = [], []
    for c in centres:
        m = (t >= c - half) & (t <= c + half)
        if not m.any():
            ts.append(np.nan)
            vs.append(0.0)
            continue
        k = int(np.argmax(np.where(m, fl, -1)))
        ts.append(t[k])
        vs.append(fl[k])
    return np.array(ts), np.array(vs)


def fit_grid(path: Path) -> dict:
    """The track's beat grid. The tracker gives rough beats; a line through them (refitted without the beats it placed badly) gives the tempo; the PHASE is then locked on the kick, because a tracker can sit on the
    hi-hat between the kicks: of the grid and the grid moved half a beat, the one with the stronger low-frequency onsets is the beat, and it is nudged to the kick's own onset."""
    import librosa
    y, sr = librosa.load(str(path), sr=SR_AN, mono=True)
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    _tempo, beats = librosa.beat.beat_track(onset_envelope=env, sr=sr, hop_length=HOP, units="time", start_bpm=110, trim=False)
    if len(beats) < 8:
        raise ConformError("fewer than 8 beats found: not a track with a beat to conform")
    k = np.arange(len(beats))
    keep = np.ones(len(beats), dtype=bool)
    for _ in range(4):                                                              # fit a line, drop the beats the tracker placed badly, fit again
        slope, icpt = np.polyfit(k[keep], beats[keep], 1)
        res = np.abs(beats - (icpt + slope * k))
        limit = max(0.03, 3 * float(np.median(res[keep])))
        keep = res <= limit
    if keep.mean() < 0.7:
        raise ConformError(f"only {keep.mean() * 100:.0f}% of the beats sit on a steady grid: not quantised, so not conformed")
    err = float(np.percentile(res[keep], 95))
    if err > MAX_GRID_ERR:
        raise ConformError(f"the beats do not sit on a steady grid ({err * 1000:.0f} ms off): not quantised, so not conformed")
    seconds = len(y) / sr
    t, flux, low = fine_flux(y, sr)
    n = int((seconds - icpt) / slope)
    body = np.arange(n // 3, n)                                                     # the body of the track: the intro may have no kick at all
    g0 = icpt + slope * np.arange(n)
    score = {}
    for phi in (0.0, 0.5 * slope):
        _ts, vs = _peaks_near(t, low, g0[body] + phi)
        score[phi] = float(vs.mean())
    phi = 0.5 * slope if score[0.5 * slope] > 1.15 * score[0.0] else 0.0
    ts, _vs = _peaks_near(t, low, g0[body] + phi)
    shift = float(np.nanmedian(ts - (g0[body] + phi)))
    b0 = icpt + phi + shift
    while b0 < 0:
        b0 += slope
    grid = b0 + slope * np.arange(int((seconds - b0) / slope))
    _ts, strength = _peaks_near(t, flux, grid, 0.03)
    return {"b0": float(b0), "ibi": float(slope), "bpm": round(60 / slope, 2), "grid_err_ms": round(err * 1000, 1), "phase_moved_half_beat": bool(phi), "kick_shift_ms": round(shift * 1000, 1),
            "strength": strength.tolist(), "seconds": round(seconds, 2)}


def find_drop(strength: list[float], within_beats: int = 64) -> int:
    """The first grid beat where the groove is in: a beat with at least 60% of the body's typical strength, followed by four more."""
    st = np.asarray(strength)
    body = float(np.median(st[len(st) // 3:]))
    for k in range(0, min(within_beats, len(st) - 5)):
        if st[k] >= 0.6 * body and st[k:k + 5].mean() >= 0.6 * body:
            return k
    raise ConformError("no beat where a groove comes in was found")


MIN_EVENT_GAP = 0.12                     # events closer than this are one event (a title anchored on a source frame and a cut that starts a frame later are the same moment)


def merge_close(events: list[float], tol: float = MIN_EVENT_GAP) -> tuple[list[float], list[tuple[float, float]]]:
    """Sorted events with any that follow another within `tol` seconds folded into it: (kept events, [(dropped, kept)])."""
    kept: list[float] = []
    dropped: list[tuple[float, float]] = []
    for e in sorted(events):
        if kept and e - kept[-1] < tol:
            dropped.append((e, kept[-1]))
        else:
            kept.append(e)
    return kept, dropped


def plan(events: list[float], ibi: float, search: tuple[float, float] = SEARCH, max_stretch: float = MAX_STRETCH) -> dict:
    """For each gap between consecutive events, the number of half-beats of the track that fill it, and the speed factor that makes them fit exactly.
    The overall tempo scale `s` (reel beat = ibi / s) is the one that makes the largest speed change smallest. Events within MIN_EVENT_GAP of each other are merged first (and reported)."""
    ev, merged = merge_close(events)
    gaps = np.diff(ev)
    best = None
    for s in np.arange(search[0], search[1] + 1e-9, 0.001):
        beat = ibi / s
        n = np.maximum(1, np.round(gaps / (beat / 2)))                         # half-beats per gap
        f = (n * ibi / 2) / gaps                                                # speed factor of the track in that gap
        worst = float(np.abs(f - 1).max())
        if best is None or worst < best[0] - 1e-9:
            best = (worst, float(s), n, f)
    worst, s, n, f = best
    return {"events": ev, "merged": merged, "scale": round(s, 3), "reel_bpm": round(60 / (ibi / s), 2), "segments": [{"from": ev[i], "to": ev[i + 1], "half_beats": int(n[i]), "speed": round(float(f[i]), 4)} for i in range(len(gaps))],
            "worst_stretch": round(worst, 4), "over_limit": [i for i in range(len(gaps)) if abs(f[i] - 1) > max_stretch]}


def _run(cmd: list) -> None:
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        raise ConformError(f"{cmd[0]} failed: {r.stderr[-500:]}")


def render(music: Path, grid: dict, drop: int, pl: dict, total: float, out: Path, sr: int = 48000, tail: str = "stop") -> None:
    """Each segment is cut from the track on its beat, time-stretched by its own factor, trimmed to EXACTLY the samples between its event and the next (plus a short overlap), and laid in at the sample the event falls on.
    Doing the lengths in samples here, not trusting the stretch filter's output length, is what keeps every event on its beat instead of drifting by a few milliseconds a segment."""
    import soundfile as sf
    segs = pl["segments"]
    ibi, b0 = grid["ibi"], grid["b0"]
    f = int(round(FADE * sr))
    y = np.zeros((int(round(total * sr)) + f, 2), dtype="float32")
    pos = float(drop)                                                               # in beats, along the track
    plan_rows = [(sg["from"], sg["to"], sg["half_beats"] * ibi / 2, sg["speed"], False) for sg in segs]
    if tail == "run":                                                               # the music plays on to the end of the reel at its natural tempo and fades over the last END_FADE seconds
        e_last = segs[-1]["to"]
        plan_rows.append((e_last, total, (total - e_last) * pl["scale"], pl["scale"], True))
    else:                                                                           # "stop": one more beat after the last event, faded out
        stop_len = STOP_BEATS * ibi / pl["scale"]
        plan_rows.append((segs[-1]["to"], segs[-1]["to"] + stop_len, STOP_BEATS * ibi, pl["scale"], True))
    tmp = out.with_suffix(".seg.wav")
    for k, (t_from, t_to, src_len, speed, is_stop) in enumerate(plan_rows):
        start = b0 + pos * ibi
        extra = 0.06 * speed                                                         # a little more of the track than needed; the output is trimmed to length
        if start + src_len + extra > grid["seconds"]:
            if is_stop or start + src_len > grid["seconds"]:
                raise ConformError(f"the track is too short: segment {k + 1} needs audio to {start + src_len:.1f} s of {grid['seconds']} s")
            extra = grid["seconds"] - start - src_len
        _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.5f}", "-t", f"{src_len + extra:.5f}", "-i", music, "-af", f"atempo={speed:.5f}", "-ar", sr, "-ac", 2, tmp])
        x, _sr = sf.read(str(tmp), dtype="float32")
        i_from = int(round(t_from * sr))
        n = int(round((t_to - t_from) * sr)) + (0 if (k == len(plan_rows) - 1) else f)
        x = x[:n]
        if len(x) < n:
            x = np.vstack([x, np.zeros((n - len(x), 2), dtype="float32")])
        if k > 0:
            x[:f] *= np.linspace(0, 1, f, dtype="float32")[:, None]                  # fade in over the overlap
        if k < len(plan_rows) - 1:
            x[-f:] *= np.linspace(1, 0, f, dtype="float32")[:, None]                 # fade out over the overlap
        if is_stop:
            m = min(int((END_FADE if tail == "run" else 0.25) * sr), len(x))
            x[-m:] *= np.linspace(1, 0, m, dtype="float32")[:, None]
        end = min(len(y), i_from + len(x))
        y[i_from:end] += x[:end - i_from]
        pos += (0 if is_stop else segs[k]["half_beats"] / 2)
    y = y[:int(round(total * sr))]
    # an accent on each event: a few dB, quick rise, decaying
    t = np.arange(len(y)) / sr
    gain = np.ones(len(y), dtype="float32")
    boost = 10 ** (ACCENT_DB / 20) - 1
    for e in [segs[0]["from"]] + [sg["to"] for sg in (segs if tail == "run" else segs[:-1])]:
        d = t - (e - 0.01)
        env = np.where(d < 0, 0.0, np.where(d < 0.01, d / 0.01, np.exp(-(d - 0.01) / ACCENT_DECAY)))
        gain += (boost * env).astype("float32")
    y *= gain[:, None]
    sf.write(str(out), y, sr)
    tmp.unlink(missing_ok=True)


def measure_hits(wav: Path, events: list[float], window: float = 0.08) -> list[dict]:
    """For each event: the offset (ms, + = after the event) of the nearest strong onset (kick or other), and how strong it is against the typical onset."""
    import librosa
    y, sr = librosa.load(str(wav), sr=SR_AN, mono=True)
    t, flux, _low = fine_flux(y, sr)
    typical = float(np.percentile(flux, 90))
    out = []
    for e in events:
        ts, vs = _peaks_near(t, flux, np.array([e]), window)
        if np.isnan(ts[0]) or vs[0] < 0.25 * typical:
            out.append({"event": round(e, 3), "offset_ms": None, "strength": 0.0})
            continue
        out.append({"event": round(e, 3), "offset_ms": round(float(ts[0] - e) * 1000), "strength": round(float(vs[0] / typical), 2)})
    return out


def conform(music: Path, events: list[float], total: float, out: Path, tail: str = "stop") -> dict:
    grid = fit_grid(music)
    drop = find_drop(grid["strength"])
    pl = plan(events, grid["ibi"])
    render(music, grid, drop, pl, total, out, tail=tail)
    return {"grid": {k: v for k, v in grid.items() if k != "strength"}, "drop_beat": drop, "drop_at_track_sec": round(grid["b0"] + drop * grid["ibi"], 2), "plan": pl, "hits": measure_hits(out, pl["events"])}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--music", type=Path, required=True)
    ap.add_argument("--events", type=Path, required=True)
    ap.add_argument("--total", type=float, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--tail", choices=["stop", "run"], default="stop", help="stop: the music stops one beat after the last event. run: it plays on to the end of the reel and fades over the last 1.5 s (start the events at 0.0 to have it start with the reel)")
    a = ap.parse_args()
    ev = [e["t"] for e in json.loads(a.events.read_text())["events"]]
    rep = conform(a.music, ev, a.total, a.out, tail=a.tail)
    a.out.with_suffix(".json").write_text(json.dumps(rep, indent=1))
    print(f"track {rep['grid']['bpm']} bpm, grid error {rep['grid']['grid_err_ms']} ms, drop at beat {rep['drop_beat']} ({rep['drop_at_track_sec']} s); reel tempo {rep['plan']['reel_bpm']} bpm; worst stretch {rep['plan']['worst_stretch'] * 100:.1f}%"
          + (f"; merged {len(rep['plan']['merged'])} event(s) within {MIN_EVENT_GAP}s of another: {[(round(d, 3), round(k, 3)) for d, k in rep['plan']['merged']]}" if rep["plan"]["merged"] else ""))
    for h in rep["hits"]:
        print(f"  event {h['event']:7.3f}s: onset {h['offset_ms']} ms from it, strength {h['strength']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
