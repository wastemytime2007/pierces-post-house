#!/usr/bin/env python3
"""Music like a reference track: measure it, describe it in words, generate, and check the result is close.

    PRECUT_ROOT=~/precut-checkout python3 labs/audio/reference_music.py --reference "<track>" \
        --seconds 23 --out "<folder>" [--tries 3] [--show-only]

The creator attaches a track he likes and has it generate one "very similar". Here that is made measurable:

  1. analyse the reference: tempo, brightness, rhythmic density, dynamics, stereo width, low end, loudness
  2. describe it in words, only from those measurements (a fixed mapping; no song, artist or lyric is ever
     named, so nothing asks for a copy of a particular piece)
  3. generate with ElevenLabs from that description (a few takes, each a few cents)
  4. re-measure each take and keep the closest; say plainly how close it is, and when it is not close enough

The reference can be a VIDEO: by default the music is taken from the reference video's own audio (music_from_video).
A finished video has a voice over its music, so the stretches where nobody speaks are found (by word timing) and only
those are measured. If there are not enough of them, the whole mix is measured and that is reported as less reliable
(the voice is in the numbers). A music file given explicitly always wins over the video.

Closeness is measured, not judged: tempo within 8% (half or double time counts), brightness within a third,
rhythmic density within 60%, dynamics within 4 dB. Whether it *sounds* like the reference is the ear's call.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import make_audio as ma  # noqa: E402

SR = 22050
N_FFT, HOP = 1024, 256
TEMPO_TOL, CENTROID_RATIO, DENSITY_RATIO, SPREAD_DB = 0.08, (0.75, 1.33), (0.6, 1.6), 4.0


class ReferenceError(Exception):
    pass


def load(path: Path, channels: int = 1) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-f", "f32le", "-ac", str(channels), "-ar", str(SR), "-"], capture_output=True)
    x = np.frombuffer(p.stdout, dtype=np.float32).astype(np.float64)
    if x.size < SR * 3:
        raise ReferenceError(f"{path.name}: under 3 seconds of audio could be read, too little to analyse")
    return x.reshape(-1, channels) if channels > 1 else x


def stft_mag(x: np.ndarray) -> np.ndarray:
    n = 1 + (len(x) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    return np.abs(np.fft.rfft(x[idx] * np.hanning(N_FFT), axis=1))


def onset_envelope(mag: np.ndarray) -> np.ndarray:
    lm = np.log1p(mag * 30)
    flux = np.maximum(0, np.diff(lm, axis=0)).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    k = int(0.5 * SR / HOP)
    local = np.convolve(flux, np.ones(k) / k, mode="same")
    return np.maximum(0, flux - local)


def tempo_bpm(env: np.ndarray) -> tuple[float, float]:
    """(bpm, confidence): the strongest periodicity of the onset envelope between 55 and 200 BPM,
    with a mild preference for the common 80-160 range so half/double time is not chosen needlessly."""
    e = env - env.mean()
    n = len(e)
    ac = np.fft.irfft(np.abs(np.fft.rfft(e, 2 * n)) ** 2)[:n]
    if ac[0] <= 0:
        return 0.0, 0.0
    ac = ac / ac[0]
    fps = SR / HOP
    lo, hi = int(fps * 60 / 200), int(fps * 60 / 55)
    lags = np.arange(lo, min(hi, n - 2))
    if len(lags) < 3:
        return 0.0, 0.0
    bpm = 60 * fps / lags
    prior = np.exp(-0.5 * (np.log2(bpm / 115) / 0.75) ** 2)
    score = ac[lags] * (0.6 + 0.4 * prior)
    k = int(np.argmax(score))
    lag = float(lags[k])
    if 0 < k < len(lags) - 1:                                          # parabolic refinement
        a, b, c = ac[lags[k] - 1], ac[lags[k]], ac[lags[k] + 1]
        d = a - 2 * b + c
        if d != 0:
            lag += 0.5 * (a - c) / d
    return float(60 * fps / lag), float(ac[lags[k]])


def loudness(path: Path) -> dict:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True).stderr
    tail = err[err.rfind("Summary:"):] if "Summary:" in err else ""
    i, lra = re.search(r"I:\s+(-?[\d.]+) LUFS", tail), re.search(r"LRA:\s+([\d.]+) LU", tail)
    return {"lufs": float(i.group(1)) if i else None, "lra": float(lra.group(1)) if lra else None}


def analyze(path: Path) -> dict:
    x = load(path)
    mag = stft_mag(x)
    env = onset_envelope(mag)
    bpm, conf = tempo_bpm(env)
    freqs = np.fft.rfftfreq(N_FFT, 1 / SR)
    energy = (mag ** 2).sum(axis=1)
    live = energy > np.percentile(energy, 30)
    band = (freqs >= 60) & (freqs <= 10000)
    power = (mag ** 2).sum(axis=0)
    tone = float(2 ** (np.sum(power[band] * np.log2(freqs[band])) / max(power[band].sum(), 1e-12)))     # where the energy sits, in octaves: a few quiet bright frames cannot drag it
    cum = np.cumsum((mag[live] ** 2).mean(axis=0))
    rolloff = float(freqs[int(np.searchsorted(cum, 0.85 * cum[-1]))])
    low = float((mag[:, freqs < 200] ** 2).sum() / max((mag ** 2).sum(), 1e-12))
    thr = max(env.mean() + 1.5 * env.std(), 0.25 * np.percentile(env, 99))                # relative to the strong onsets, so a sparse rhythm is not over-counted
    peaks = [i for i in range(1, len(env) - 1) if env[i] > thr and env[i] >= env[i - 1] and env[i] >= env[i + 1]]
    keep, last = [], -10**9
    for i in peaks:
        if i - last >= int(0.12 * SR / HOP):
            keep.append(i)
            last = i
    seconds = len(x) / SR
    density = len(keep) / seconds
    n = int(1.0 * SR)                                                                        # one-second windows: phrase-level loudness, not the gaps between notes
    rms = 20 * np.log10(np.maximum(np.sqrt((x[:len(x) // n * n].reshape(-1, n) ** 2).mean(axis=1)), 1e-4))
    core = rms[2:-2] if len(rms) >= 8 else rms                                                  # a fade-in or a silent tail is not a dynamic swing
    spread = float(np.percentile(core, 90) - np.percentile(core, 10))
    width = None
    try:
        st = load(path, 2)
        mid, side = (st[:, 0] + st[:, 1]) / 2, (st[:, 0] - st[:, 1]) / 2
        width = float(np.sqrt((side ** 2).mean()) / max(np.sqrt((mid ** 2).mean()), 1e-9))
    except ReferenceError:
        pass
    return {"seconds": round(seconds, 2), "bpm": round(bpm, 1), "tempo_confidence": round(conf, 3), "tone_centre_hz": round(tone), "rolloff_hz": round(rolloff),
            "onsets_per_sec": round(density, 2), "dynamic_spread_db": round(spread, 1), "stereo_width": None if width is None else round(width, 2),
            "low_end_fraction": round(low, 3), **loudness(path)}


def describe(f: dict) -> dict:
    """Words for the measurements, from a fixed mapping."""
    bpm = f["bpm"]
    tempo = ("slow and unhurried" if bpm < 70 else "relaxed" if bpm < 95 else "steady mid-tempo" if bpm < 115 else "upbeat and driving" if bpm < 135
             else "fast and energetic" if bpm < 160 else "very fast")
    c = f["tone_centre_hz"]
    tone = ("very dark, bass-heavy tone" if c < 100 else "warm, bass-weighted tone" if c < 200 else "warm tone" if c < 500 else "balanced tone" if c < 1500
            else "bright, crisp tone" if c < 3000 else "very bright, airy tone")
    d = f["onsets_per_sec"]
    rhythm = "sparse, spacious rhythm" if d < 1.2 else "moderately active rhythm" if d < 3 else "busy, rhythmic texture" if d < 5 else "dense, constantly moving texture"
    s = f["dynamic_spread_db"]
    dynamics = "very steady dynamics" if s < 4 else "mostly steady with gentle swells" if s < 8 else "dynamic, with big builds and drops"
    extra = []
    if f.get("stereo_width") is not None and f["stereo_width"] > 0.5:
        extra.append("wide stereo")
    if f["low_end_fraction"] > 0.25:
        extra.append("strong low end")
    elif f["low_end_fraction"] < 0.08:
        extra.append("light low end")
    return {"tempo": tempo, "tone": tone, "rhythm": rhythm, "dynamics": dynamics, "extra": extra}


def prompt_tempo(bpm: float) -> float:
    """The tempo to ask for: a tempo above 140 is halved (as often as needed). Half time counts as a match when it is
    measured, and generators hold a mid-range tempo far better than a very fast one. Slow tempos are left as they are."""
    while bpm > 140:
        bpm /= 2
    return bpm


def build_prompt(f: dict, for_voiceover: bool = True) -> str:
    w = describe(f)
    folded = {**f, "bpm": prompt_tempo(f["bpm"])}
    w = describe(folded) | {"tone": w["tone"], "rhythm": w["rhythm"], "dynamics": w["dynamics"], "extra": w["extra"]}
    parts = ["instrumental background music", f"about {round(folded['bpm'])} BPM", w["tempo"], w["tone"], w["rhythm"], w["dynamics"], *w["extra"], "no vocals"]
    if for_voiceover:
        parts.append("leaves room for a speaking voice")
    return ", ".join(parts)


def closeness(ref: dict, got: dict, dynamics: bool = True) -> dict:
    """Four measured comparisons. Tempo must hold (half or double time counts); at least two of the others must too."""
    terr = min(abs(got["bpm"] / (ref["bpm"] * k) - 1) for k in (0.5, 1, 2)) if ref["bpm"] and got["bpm"] else 1.0
    cr = got["tone_centre_hz"] / ref["tone_centre_hz"] if ref["tone_centre_hz"] else 0
    dr = got["onsets_per_sec"] / ref["onsets_per_sec"] if ref["onsets_per_sec"] else (1.0 if got["onsets_per_sec"] == 0 else 9.9)
    checks = {"tempo": terr <= TEMPO_TOL, "brightness": CENTROID_RATIO[0] <= cr <= CENTROID_RATIO[1], "rhythmic_density": DENSITY_RATIO[0] <= dr <= DENSITY_RATIO[1]}
    if dynamics:
        checks["dynamics"] = abs(got["dynamic_spread_db"] - ref["dynamic_spread_db"]) <= SPREAD_DB
    others = sum(v for k, v in checks.items() if k != "tempo")
    return {"checks": checks, "tempo_error": round(terr, 3), "brightness_ratio": round(cr, 2), "density_ratio": round(dr, 2),
            "passed": checks["tempo"] and others >= 2, "score": round(sum(checks.values()) / len(checks) - 0.5 * terr, 3)}


def pick_best(ref_feats: dict, prompt: str, seconds: float, cache: Path, tries: int = 3, generate=ma.generate, analyse=analyze) -> tuple[Path, dict]:
    """Generate up to `tries` takes of the prompt; stop early at the first one that is close enough, else keep the closest."""
    takes = []
    for k in range(tries):
        mp3, info = generate("music", prompt, seconds, cache, salt="" if k == 0 else f"take{k + 1}")
        got = analyse(mp3)
        c = closeness(ref_feats, got)
        takes.append({"file": str(mp3), "take": k + 1, "features": got, "closeness": c, "info": info})
        if c["passed"]:
            break
    best = max(takes, key=lambda t: (t["closeness"]["passed"], t["closeness"]["score"]))
    return Path(best["file"]), {"prompt": prompt, "takes": takes, "chosen_take": best["take"], "passed": best["closeness"]["passed"]}


AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aif", ".aiff", ".flac"}


def library_tracks(root: Path) -> list[Path]:
    """One audio file per track: every audio file under the library folder."""
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXT and not p.name.startswith("."))


def rank_library(ref: dict, root: Path, cache_file: Path | None = None, analyse=analyze, top: int | None = None) -> list[dict]:
    """Every track in the library measured (once; the measurements are cached) and ranked by how close it is to the reference."""
    cache = {}
    if cache_file and cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text())
        except ValueError:
            cache = {}
    rows = []
    tracks = library_tracks(root)
    per_folder = {}
    for p in tracks:
        per_folder[p.parent] = per_folder.get(p.parent, 0) + 1
    for p in tracks:
        key = f"{p}|{p.stat().st_mtime_ns}|{p.stat().st_size}"
        feats = cache.get(key)
        if feats is None:
            try:
                feats = analyse(p)
            except ReferenceError:
                continue
            cache[key] = feats
        c = closeness(ref, feats)
        name = p.stem if p.parent == root else (p.parent.name if per_folder[p.parent] == 1 else f"{p.parent.name} [{p.stem}]")      # a folder with several versions names each
        rows.append({"track": name, "file": str(p), "features": feats, "closeness": c})
    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache))
    rows.sort(key=lambda r: (not r["closeness"]["passed"], -r["closeness"]["score"]))
    return rows[:top] if top else rows


MIN_GAP_SEC, SPEECH_PAD_SEC, NEED_MUSIC_ONLY_SEC = 1.0, 0.2, 6.0


def transcribe_words(wav: Path) -> list[tuple[float, float]]:
    """(start, end) of every spoken word, from the local Whisper base model. English is set rather than guessed:
    language=None made Whisper invent text on this footage (see RUNNELLS_CONTENT_INVENTORY)."""
    try:
        import whisper
    except ImportError as e:
        raise ReferenceError(f"Whisper is not installed, so the stretches without speech cannot be found ({e})")
    r = whisper.load_model("base").transcribe(str(wav), language="en", word_timestamps=True, condition_on_previous_text=False, fp16=False)
    return [(float(w["start"]), float(w["end"])) for seg in r["segments"] for w in seg.get("words", [])]


def music_only_windows(words: list[tuple[float, float]], total: float, min_gap: float = MIN_GAP_SEC, pad: float = SPEECH_PAD_SEC) -> list[tuple[float, float]]:
    """Stretches of at least min_gap seconds with no speech, each trimmed by `pad` on both sides of every word."""
    edges, cur = [], 0.0
    for a, b in sorted(words):
        if a - pad - cur >= min_gap:
            edges.append((cur, a - pad))
        cur = max(cur, b + pad)
    if total - cur >= min_gap:
        edges.append((cur, total))
    return [(round(a, 3), round(b, 3)) for a, b in edges if b - a >= min_gap]


def music_from_video(video: Path, out_dir: Path, words_of=transcribe_words) -> dict:
    """The music reference taken from a video's own audio. Returns {path, source, voice_included, music_only_sec, windows, note}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    whole = out_dir / "reference_audio.wav"
    p = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", str(SR), str(whole)], capture_output=True, text=True)
    if p.returncode != 0 or not whole.exists() or whole.stat().st_size < 1000:
        raise ReferenceError(f"{Path(video).name} has no audio track to take music from ({p.stderr.strip()[:120] or 'nothing extracted'})")
    x = load(whole)
    total = len(x) / SR
    words = words_of(whole)
    wins = music_only_windows(words, total)
    got = sum(b - a for a, b in wins)
    if not words:
        return {"path": str(whole), "source": "no speech found, so the whole audio is music", "voice_included": False, "music_only_sec": round(total, 1), "windows": [], "words": 0,
                "note": "no spoken words were found in the reference video; all of its audio was measured as music"}
    if got >= NEED_MUSIC_ONLY_SEC:
        pieces, fade = [], int(0.03 * SR)
        for a, b in wins:
            seg = x[int(a * SR):int(b * SR)].copy()
            seg[:fade] *= np.linspace(0, 1, fade)
            seg[-fade:] *= np.linspace(1, 0, fade)
            pieces.append(seg)
        only = out_dir / "reference_music_only.wav"
        pcm = (np.clip(np.concatenate(pieces), -1, 1) * 32767).astype("<i2").tobytes()
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "-", str(only)], input=pcm, check=True)
        return {"path": str(only), "source": f"the {len(wins)} stretch(es) of the reference video with no speech", "voice_included": False, "music_only_sec": round(got, 1),
                "windows": wins, "words": len(words), "note": f"measured {got:.1f}s of music-only audio out of {total:.1f}s; the voice was left out"}
    return {"path": str(whole), "source": "the whole mix of the reference video (voice included)", "voice_included": True, "music_only_sec": round(got, 1), "windows": wins, "words": len(words),
            "note": f"only {got:.1f}s of the reference video has no speech (need {NEED_MUSIC_ONLY_SEC:.0f}s), so its whole mix was measured: the speaker's voice is in the numbers, "
                    "so tone and dynamics are unreliable. Give a music file with --reference to override."}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", type=Path, help="a music file to match (overrides --from-video)")
    ap.add_argument("--from-video", type=Path, help="take the music reference from this video's own audio (the default source when a reference video is used)")
    ap.add_argument("--seconds", type=float, default=23.0)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--tries", type=int, default=3)
    ap.add_argument("--show-only", action="store_true", help="measure and describe the reference; generate nothing")
    ap.add_argument("--rank-library", type=Path, help="instead of generating: rank the tracks in this folder (your licensed library) by how close they measure to the reference")
    ap.add_argument("--top", type=int, default=8)
    a = ap.parse_args()
    if not a.reference and not a.from_video:
        ap.error("give --reference <music file> or --from-video <reference video>")
    src = None
    try:
        if a.reference:
            if a.from_video:
                print(f"note: a music file was given, so it is used instead of the audio of {a.from_video.name}")
        else:
            src = music_from_video(a.from_video, a.out / "reference")
            print(f"reference music: {src['source']}\n  {src['note']}")
            a.reference = Path(src["path"])
        ref = analyze(a.reference)
    except ReferenceError as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    prompt = build_prompt(ref)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "reference.json").write_text(json.dumps({"reference": str(a.reference), "taken_from_video": src, "features": ref, "described_as": describe(ref), "prompt": prompt}, indent=2))
    print("reference:", json.dumps(ref))
    print("prompt:   ", prompt)
    if a.show_only:
        return 0
    if a.rank_library:
        rows = rank_library(ref, a.rank_library, a.out / "library_measurements.json")
        (a.out / "ranking.json").write_text(json.dumps(rows, indent=2))
        print(f"\n{len(rows)} tracks measured in {a.rank_library}; closest to the reference:")
        for r in rows[:a.top]:
            f, c = r["features"], r["closeness"]
            print(f"  {'CLOSE' if c['passed'] else '     '} {r['track'][:52]:52s} {f['bpm']:6.1f} BPM (off {c['tempo_error']:.0%}), brightness x{c['brightness_ratio']}, density x{c['density_ratio']}")
        return 0
    try:
        ma.load_key()
        mp3, res = pick_best(ref, prompt, a.seconds, a.out / "generated", a.tries)
    except (ma.AudioError, ReferenceError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    shutil.copy(mp3, a.out / "chosen.mp3")
    (a.out / "comparison.json").write_text(json.dumps(res, indent=2))
    for t in res["takes"]:
        c = t["closeness"]
        print(f"  take {t['take']}: bpm {t['features']['bpm']} (ref {ref['bpm']}, off {c['tempo_error']:.0%}), brightness x{c['brightness_ratio']}, density x{c['density_ratio']}, "
              f"checks {c['checks']} -> {'CLOSE ENOUGH' if c['passed'] else 'not close enough'}")
    print(f"\nchosen take {res['chosen_take']}: {a.out / 'chosen.mp3'}" + ("" if res["passed"] else "\nNONE of the takes was close enough by measurement; the closest is kept and the gap is reported above."))
    return 0 if res["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
