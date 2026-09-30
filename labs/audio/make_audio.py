#!/usr/bin/env python3
"""A sound effect for a callout and a music bed for a window, generated and mixed under the speech.

    python3 labs/audio/make_audio.py --xml "<export.xml>" --base "<preview.mp4 of the window>" \
        --out "<folder>" --start 0 --end 22 --callout "<overlay folder>" \
        [--sfx-prompt "..."] [--music-prompt "..."] [--music-db -5] [--duck-db 12]

Generation is ElevenLabs (sound effects and music). The key is read from
~/.config/post-house/elevenlabs.env and is never printed or written anywhere else. Every prompt is an
input and its result is cached by prompt, so re-running with the same words does not spend again.
The music is a separate stem with the ducking under speech already in it; the effect is its own
clip at the callout's time. The speech track is never touched. Volumes are measured, not assumed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "review_loop"))
sys.path.insert(0, str(HERE))
import timeline  # noqa: E402
import xml.etree.ElementTree as ET  # noqa: E402

import numpy as np  # noqa: E402
import verify_audio as va  # noqa: E402

KEYFILE = Path.home() / ".config" / "post-house" / "elevenlabs.env"
API = "https://api.elevenlabs.io/v1"
SR = 48000
DEFAULT_SFX = "a soft, clean UI pop with a light airy whoosh, as a graphic slides onto the screen, subtle"
DEFAULT_MUSIC = ("warm minimal instrumental for a home renovation how-to: gentle plucked guitar and soft "
                 "brushed percussion, steady easy tempo, unobtrusive, no vocals, leaves room for a speaking voice")


class AudioError(Exception):
    pass


def load_key() -> str:
    if not KEYFILE.exists():
        raise AudioError(f"no ElevenLabs key at {KEYFILE}")
    for line in KEYFILE.read_text().splitlines():
        if line.startswith("ELEVENLABS_API_KEY=") and line.split("=", 1)[1].strip():
            return line.split("=", 1)[1].strip()
    raise AudioError(f"{KEYFILE} has no ELEVENLABS_API_KEY line")


def generate(kind: str, prompt: str, seconds: float, cache: Path) -> tuple[Path, dict]:
    """kind is 'sfx' or 'music'. Returns the mp3 and whether it came from the cache."""
    ms = int(round(seconds * 1000))
    h = hashlib.sha1(f"{kind}|{prompt}|{ms}".encode()).hexdigest()[:12]
    out = cache / f"{kind}_{h}.mp3"
    if out.exists() and out.stat().st_size > 1000:
        return out, {"cached": True, "prompt": prompt, "ms": ms}
    if kind == "sfx":
        url, body = f"{API}/sound-generation?output_format=mp3_44100_128", {"text": prompt, "duration_seconds": round(seconds, 2)}
    else:
        url, body = f"{API}/music?output_format=mp3_44100_128", {"prompt": prompt, "music_length_ms": ms}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"xi-api-key": load_key(), "Content-Type": "application/json"})
    try:
        data = urllib.request.urlopen(req, timeout=300).read()
    except urllib.error.HTTPError as e:
        raise AudioError(f"ElevenLabs {kind} refused ({e.code}): {e.read()[:200].decode(errors='replace')}")
    cache.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out, {"cached": False, "prompt": prompt, "ms": ms}


def run(*args) -> str:
    p = subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], capture_output=True, text=True)
    if p.returncode:
        raise AudioError(p.stderr.strip()[-400:])
    return p.stderr


def volume(path: Path, start: float = 0.0, dur: float | None = None) -> dict:
    """Mean and peak level in dB over a window of a file (ffmpeg volumedetect)."""
    args = ["ffmpeg", "-hide_banner", "-ss", f"{start}"] + (["-t", f"{dur}"] if dur else []) + ["-i", str(path), "-af", "volumedetect", "-vn", "-f", "null", "-"]
    err = subprocess.run(args, capture_output=True, text=True).stderr
    m, p = re.search(r"mean_volume: (-?[\d.]+|-inf) dB", err), re.search(r"max_volume: (-?[\d.]+|-inf) dB", err)
    if not m or not p:
        raise AudioError(f"could not measure {path}")
    f = lambda s: -120.0 if s == "-inf" else float(s)
    return {"mean": f(m.group(1)), "peak": f(p.group(1))}


def probe_dur(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout)


def speech_wav(base: Path, out: Path, start: float, dur: float) -> None:
    run("-ss", start, "-t", dur, "-i", base, "-vn", "-ac", 2, "-ar", SR, "-c:a", "pcm_s16le", out)


def speech_activity(x: np.ndarray, hop: int) -> tuple[np.ndarray, float]:
    """Which 10 ms frames hold speech, and the speech's own level (dB RMS) over those frames."""
    d = va.rms_db(x, hop)
    act = d > max(-45.0, float(np.percentile(d, 95)) - 20.0)
    return act, float(d[act].mean())


def build_music_stem(raw_mp3: Path, speech: Path, out: Path, dur: float, music_db: float, duck_db: float) -> dict:
    """The music sits music_db relative to the speech's level (measured while speech is playing) in the
    pauses, and duck_db lower while the speaker talks. The ducking is an exact, smoothed volume curve
    (0.15 s down, 0.35 s back up), not a compressor, so its depth is known."""
    hop = int(0.01 * SR)
    sp = va.pcm(speech)
    act, s_lvl = speech_activity(sp, hop)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(raw_mp3), "-vn", "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"], capture_output=True).stdout
    m = np.frombuffer(raw, dtype=np.float32).astype(np.float64).reshape(-1, 2)
    n = int(round(dur * SR))
    if len(m) < n:
        raise AudioError(f"the generated music is {len(m) / SR:.1f}s, shorter than the {dur:.1f}s window")
    m = m[:n]
    mono = va.pcm(raw_mp3)[:n]                                    # level is measured the way verify_audio reads it: a mono downmix
    m_lvl = float(20 * np.log10(max(np.sqrt((mono ** 2).mean()), 1e-6)))
    m *= 10 ** (((s_lvl + music_db) - m_lvl) / 20)

    want = np.zeros(len(act))
    want[act] = -duck_db
    lead = int(0.1 / 0.01)
    for i in np.where(act)[0]:
        want[max(0, i - lead):i + 1] = -duck_db                   # start ducking just before the words
    g, cur = np.zeros(len(want)), 0.0
    for i, w in enumerate(want):
        k = 1 - np.exp(-0.01 / (0.15 if w < cur else 0.35))
        cur += (w - cur) * k
        g[i] = cur
    env = np.interp(np.arange(n) / SR, np.arange(len(g)) * 0.01 + 0.005, g, left=g[0], right=g[-1])
    t = np.arange(n) / SR
    fade = np.minimum(np.clip(t / 0.6, 0, 1), np.clip((dur - t) / 1.6, 0, 1))
    m *= (10 ** (env / 20) * fade)[:, None]
    pk = float(np.abs(m).max())
    if pk > 0.9:
        m *= 0.9 / pk
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(m, -1, 1) * 32767).astype("<i2").tobytes())
    return {"speech_level_db": round(s_lvl, 2), "music_gap_level_db": round(s_lvl + music_db, 2), "duck_db": duck_db}


def build_sfx_clip(raw_mp3: Path, speech: Path, out: Path, below_speech_peak_db: float) -> float:
    s, m = volume(speech), volume(raw_mp3)
    gain = (s["peak"] - below_speech_peak_db) - m["peak"]
    run("-i", raw_mp3, "-af", f"volume={gain:.2f}dB,aformat=sample_rates={SR}:channel_layouts=stereo,afade=t=in:d=0.004", "-ar", SR, "-c:a", "pcm_s16le", out)
    return gain


def mix_preview(video: Path, music_stem: Path, sfx_clip: Path, out: Path, start: float, dur: float, t_sfx: float) -> None:
    off_ms = int(round((t_sfx - start) * 1000))
    run("-ss", start, "-t", dur, "-i", video, "-i", music_stem, "-i", sfx_clip,
        "-filter_complex", f"[2:a]adelay={off_ms}|{off_ms}[s];[0:a][1:a][s]amix=inputs=3:normalize=0:duration=first[a]",
        "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", dur, out)


def callout_time(folder: Path) -> float:
    p = json.loads((folder / "placement.json").read_text())
    return p["place_overlay_on_timeline_at_sec"] + p["geometry"]["t_in"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xml", type=Path, required=True)
    ap.add_argument("--base", type=Path, required=True, help="rendered cut (with its audio) covering the window, e.g. captions folder's cut_1080.mp4")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, required=True)
    ap.add_argument("--callout", type=Path, required=True, help="overlay folder whose callout the effect goes with")
    ap.add_argument("--sfx-prompt", default=DEFAULT_SFX)
    ap.add_argument("--music-prompt", default=DEFAULT_MUSIC)
    ap.add_argument("--music-db", type=float, default=-5.0, help="music level in the pauses, relative to the speech level")
    ap.add_argument("--duck-db", type=float, default=12.0, help="how much further the music drops while the speaker talks")
    ap.add_argument("--sfx-below-peak-db", type=float, default=6.0, help="effect peak below the speech's peak")
    ap.add_argument("--preview-video", type=Path, help="video to put the mixed audio under (default: --base)")
    ap.add_argument("--cache", type=Path, help="where generated audio is cached (default: <out>/generated); point it at an earlier folder's to reuse its audio")
    ap.add_argument("--music-ms", type=int, help="length to request the music at; the same length and prompt as an earlier run finds it in the cache instead of generating new music")
    a = ap.parse_args()

    try:
        cut = timeline.load_cut(a.xml)
        if not 0 <= a.start < a.end <= cut.zone_end + 0.01:
            raise AudioError(f"the window {a.start}-{a.end}s is outside the cut (0-{cut.zone_end:.2f}s)")
        t_sfx = callout_time(a.callout)
        if not a.start <= t_sfx < a.end:
            raise AudioError(f"the callout enters at {t_sfx:.2f}s, outside the window")
        load_key()
    except (AudioError, timeline.TimelineError, OSError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1

    a.out.mkdir(parents=True, exist_ok=True)
    dur = round(a.end - a.start, 3)
    cache = a.cache or a.out / "generated"
    try:
        speech = a.out / "speech_window.wav"
        speech_wav(a.base, speech, a.start, dur)
        sfx_mp3, sfx_info = generate("sfx", a.sfx_prompt, 1.2, cache)
        music_mp3, music_info = generate("music", a.music_prompt, a.music_ms / 1000 if a.music_ms else max(dur + 1.0, 3.0), cache)
        music_stem, sfx_clip = a.out / "music_stem.wav", a.out / "sfx_clip.wav"
        levels = build_music_stem(music_mp3, speech, music_stem, dur, a.music_db, a.duck_db)
        sfx_gain = build_sfx_clip(sfx_mp3, speech, sfx_clip, a.sfx_below_peak_db)
        preview = a.out / "audio_preview.mp4"
        mix_preview(a.preview_video or a.base, music_stem, sfx_clip, preview, a.start, dur, t_sfx)
    except AudioError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1

    (a.out / "audio.json").write_text(json.dumps({
        "window": {"start": a.start, "end": a.end}, "speech_window": speech.name, "music_db_rel_speech": a.music_db, "duck_db": a.duck_db, "levels": levels,
        "sfx_below_speech_peak_db": a.sfx_below_peak_db, "sfx_gain_db": round(sfx_gain, 2), "callout_sec": t_sfx,
        "generated": {"sfx": sfx_info, "music": music_info},
        "clips": [{"kind": "music", "name": "music_stem.wav", "path": str(music_stem.resolve()), "start_sec": a.start, "duration_sec": probe_dur(music_stem)},
                  {"kind": "sfx", "name": "sfx_clip.wav", "path": str(sfx_clip.resolve()), "start_sec": t_sfx, "duration_sec": probe_dur(sfx_clip)}],
        "speech_wav": str(speech.resolve()), "preview_video": str((a.preview_video or a.base).resolve())}, indent=2))
    (a.out / "placement.json").write_text(json.dumps({"kind": "audio", "clips": json.loads((a.out / "audio.json").read_text())["clips"]}, indent=2))

    v = subprocess.run([sys.executable, str(HERE / "verify_audio.py"), str(a.out)], capture_output=True, text=True)
    print(v.stdout.rstrip())
    if v.returncode != 0:
        print(v.stderr, file=sys.stderr)
        return 1
    print(f"\npreview: {preview}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
