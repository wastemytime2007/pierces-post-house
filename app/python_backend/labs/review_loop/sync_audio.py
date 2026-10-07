"""Find which camera clip, and where in it, a moment recorded on a separate microphone also happened (standalone; nothing in app/).

    python3 labs/recruit/sync_audio.py --located located.json --mics "<folder of the mic recordings>" --cams "<folder of 8 kHz camera audio .wav>" --out synced.json

A moment's words were found in a person's own recorder file (`clip`, `speech_start`, `speech_end`). To show PICTURE for it, the same moment is looked for in the audio of every camera clip by generalized
cross-correlation with phase transform (GCC-PHAT, 300-3400 Hz): the cross-spectrum is whitened so the peak depends on the timing of the speech, not on the two microphones sounding different.
For each camera clip the best peak and a score (peak over the typical size of the rest of the correlation) are kept; a moment is SYNCED to a camera only when its best score clears MIN_SCORE and
beats the next camera's best by MARGIN. Otherwise it is reported as not synced, with the scores, and the caller keeps it as audio only. A match is never guessed.

`cams` holds `<clip name>.wav` mono 8 kHz files extracted from the camera clips (ffmpeg -vn -ac 1 -ar 8000)."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

try:
    from scipy import fft as _fft
except ImportError:                                           # numpy alone is enough, only slower
    _fft = np.fft

SR = 8000
BAND = (300.0, 3400.0)
MIN_SCORE = 25.0     # synthetic check: unrelated audio scored 8-10 (rising slowly with clip length up to 1,400 s), a true match 400-480; real microphones will sit lower, so the real spread is reported by every run (all_scores)
MARGIN = 1.4
PAD = 2.0


def read_window(path: Path, start: float, dur: float, sr: int = SR) -> np.ndarray:
    """Mono float32 samples of `dur` seconds from `start`, resampled to `sr`."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start}", "-t", f"{dur}", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"], capture_output=True)
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        n, ch, sw = w.getnframes(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
    if sw != 2:
        raise ValueError(f"{path.name}: expected 16-bit audio")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return x.reshape(-1, ch).mean(axis=1) if ch > 1 else x


def gcc_phat(cam_spec: np.ndarray, n_fft: int, moment: np.ndarray, sr: int = SR) -> tuple[float, float]:
    """(offset_sec, score): where in the camera audio the moment starts, and how sharp the peak is. `cam_spec` is the camera audio's rfft at length `n_fft`."""
    m = moment - moment.mean()
    M = _fft.rfft(m.astype(np.float32), n_fft)
    cross = cam_spec * np.conj(M)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    cross = cross / (np.abs(cross) + 1e-9)
    cross[(freqs < BAND[0]) | (freqs > BAND[1])] = 0
    r = _fft.irfft(cross, n_fft)
    r = r[:n_fft - len(m) + 1] if n_fft - len(m) + 1 > 0 else r
    k = int(np.argmax(r))
    rest = np.abs(r)
    lo, hi = max(0, k - int(0.5 * sr)), min(len(r), k + int(0.5 * sr))
    rest = np.concatenate([rest[:lo], rest[hi:]])
    score = float(r[k] / (np.std(rest) + 1e-12)) if len(rest) else 0.0
    return k / sr, score


def next_fast(n: int) -> int:
    return _fft.next_fast_len(n, real=True) if hasattr(_fft, "next_fast_len") else 1 << (n - 1).bit_length()


def decide(scores: dict[str, tuple[float, float]]) -> dict:
    """From {camera: (offset, score)}: the synced camera or None, with the reason."""
    ranked = sorted(scores.items(), key=lambda kv: -kv[1][1])
    if not ranked:
        return {"synced": False, "reason": "no camera audio to search"}
    (name, (off, sc)), second = ranked[0], (ranked[1][1][1] if len(ranked) > 1 else 0.0)
    if sc < MIN_SCORE:
        return {"synced": False, "reason": f"best peak too weak ({sc:.1f}, needs {MIN_SCORE})", "best_camera": name, "best_score": round(sc, 1)}
    if second and sc < second * MARGIN:
        return {"synced": False, "reason": f"two cameras match about equally ({sc:.1f} and {second:.1f})", "best_camera": name, "best_score": round(sc, 1)}
    return {"synced": True, "camera": name, "offset": round(off, 3), "score": round(sc, 1), "next_best": round(second, 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--located", required=True)
    ap.add_argument("--mics", required=True, help="folder holding the mic recordings, named like the transcripts' clip names + .WAV")
    ap.add_argument("--cams", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    moments = [m for m in json.loads(Path(a.located).expanduser().read_text()) if m.get("found")]
    mics = {p.stem: p for p in Path(a.mics).expanduser().glob("*") if p.suffix.lower() in (".wav", ".mp3", ".m4a")}
    cams = sorted(Path(a.cams).expanduser().glob("*.wav"))
    if not moments or not cams:
        print("nothing to do: no located moments or no camera audio", file=sys.stderr)
        return 1
    wins = {}
    for m in moments:
        src = mics.get(m["clip"])
        if src is None:
            continue
        s, e = max(0.0, m["speech_start"] - PAD), m["speech_end"] + PAD
        wins[m["id"]] = read_window(src, s, e - s)
    scores: dict[str, dict[str, tuple[float, float]]] = {i: {} for i in wins}
    for cam in cams:
        x = load_wav(cam)
        longest = max(len(w) for w in wins.values())
        n_fft = next_fast(len(x) + longest)
        spec = _fft.rfft(x.astype(np.float32), n_fft)
        for i, w in wins.items():
            off, sc = gcc_phat(spec, n_fft, w)
            scores[i][cam.stem] = (off, sc)
        print(f"  searched {cam.name}", flush=True)
    out = []
    for m in moments:
        i = m["id"]
        if i not in wins:
            out.append({"id": i, "synced": False, "reason": f"no mic recording named {m['clip']}"})
            continue
        d = decide(scores[i])
        if d["synced"]:
            d["camera_start"] = round(d["offset"] + PAD, 3)         # where the speech itself starts in that camera clip (the window began PAD seconds earlier)
        d["id"] = i
        d["all_scores"] = {k: round(v[1], 1) for k, v in sorted(scores[i].items())}
        out.append(d)
    Path(a.out).expanduser().write_text(json.dumps(out, indent=1))
    for d in out:
        print(f"{d['id']:4} " + (f"SYNCED {d['camera']} at {d['camera_start']:.1f} s (score {d['score']}, next {d['next_best']})" if d["synced"] else f"not synced: {d['reason']}"))
    print(f"{sum(d['synced'] for d in out)} of {len(out)} moments synced to a camera")
    return 0


if __name__ == "__main__":
    sys.exit(main())
