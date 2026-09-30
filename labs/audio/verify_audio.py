#!/usr/bin/env python3
"""Check a built audio folder from the rendered files themselves.

    python3 labs/audio/verify_audio.py <folder>

  STEM-FORMAT           both stems are 48k stereo PCM and the music is exactly the window's length
  MUSIC-AUDIBLE         in the longer pauses the music is present (within 15 dB of the speech level) and does not clip
  MUSIC-UNDER-SPEECH    while the speaker talks, the music is at least 15 dB below the speech
  DUCKS                 the music drops at least 6 dB when the speaker starts talking
  SPEECH-LEVEL-KEPT     the speech is within 1 dB of its original level in the mixed preview
  SFX-QUIETER-THAN-SPEECH  the effect's peak is at least 5 dB under the speech's peak
  SFX-AT-CALLOUT        the effect lands where the callout enters (found by correlating the mix)
  MIX-NOT-CLIPPING      the mixed preview peaks below -1 dBFS
  FADES                 the music fades in at the start and out at the end
  (info) the ducking depth, generation source and whether anything was reused from the cache
Exit 0 = every gating check passed.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

SR = 48000
HOP = int(0.1 * SR)


def pcm(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"], capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64)


def rms_db(x: np.ndarray, n: int = HOP) -> np.ndarray:
    k = len(x) // n
    r = np.sqrt((x[:k * n].reshape(k, n) ** 2).mean(axis=1))
    return 20 * np.log10(np.maximum(r, 1e-6))


def db(v: float) -> float:
    return 20 * np.log10(max(v, 1e-6))


def probe(path: Path) -> dict:
    j = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name,sample_rate,channels:format=duration", "-of", "json", str(path)], capture_output=True, text=True).stdout)
    s = j["streams"][0]
    return {"codec": s["codec_name"], "sr": int(s["sample_rate"]), "ch": int(s["channels"]), "dur": float(j["format"]["duration"])}


def main() -> int:
    d = Path(sys.argv[1])
    meta = json.loads((d / "audio.json").read_text())
    win = meta["window"]["end"] - meta["window"]["start"]
    music, sfx, speech = d / "music_stem.wav", d / "sfx_clip.wav", d / "speech_window.wav"
    rows: list[tuple[str, bool | None, str]] = []

    pm, ps = probe(music), probe(sfx)
    rows.append(("STEM-FORMAT", (pm["codec"], pm["sr"], pm["ch"]) == ("pcm_s16le", 48000, 2) and (ps["codec"], ps["sr"], ps["ch"]) == ("pcm_s16le", 48000, 2) and abs(pm["dur"] - win) < 0.02,
                 f"music {pm['codec']} {pm['sr']}Hz {pm['ch']}ch {pm['dur']:.3f}s (window {win:.3f}s); effect {ps['sr']}Hz {ps['ch']}ch {ps['dur']:.2f}s"))

    m, sp = pcm(music), pcm(speech)
    n = min(len(m), len(sp))
    m, sp = m[:n], sp[:n]
    mdb, sdb = rms_db(m), rms_db(sp)
    active = sdb > max(-45.0, float(np.percentile(sdb, 95)) - 20.0)
    body = slice(int(0.8 * 10), len(sdb) - 18)                                 # leave the fades out of the comparison
    act = active.copy()
    act[:body.start] = False
    act[body.stop:] = False
    gap = np.zeros(len(active), dtype=bool)                                     # settled part of pauses of 0.8 s or more
    i = 0
    while i < len(active):
        if active[i]:
            i += 1
            continue
        j = i
        while j < len(active) and not active[j]:
            j += 1
        if j - i >= 8:
            gap[i + 4:j - 1] = True
        i = j
    gap[:body.start] = False
    gap[body.stop:] = False
    if act.sum() < 5 or gap.sum() < 3:
        rows.append(("MUSIC-AUDIBLE", None, "too little speech, or no pause of 0.8 s or more, to measure"))
    else:
        speech_lvl, music_act, music_gap = float(np.mean(sdb[act])), float(np.mean(mdb[act])), float(np.mean(mdb[gap]))
        peak = float(np.abs(m).max())
        rows.append(("MUSIC-AUDIBLE", speech_lvl - music_gap <= 15.0 and peak < 0.99,
                     f"in the settled part of the longer pauses ({int(gap.sum())} tenths of a second) the music is at {music_gap:.1f} dB against speech at {speech_lvl:.1f} dB ({speech_lvl - music_gap:.1f} dB apart, need 15 or less); peak {db(peak):.1f} dBFS"))
        rows.append(("MUSIC-UNDER-SPEECH", speech_lvl - music_act >= 15.0,
                     f"during speech ({int(act.sum())} of {len(sdb)} tenths of a second): speech {speech_lvl:.1f} dB, music {music_act:.1f} dB, {speech_lvl - music_act:.1f} dB apart (need 15 or more)"))
        rows.append(("DUCKS", music_gap - music_act >= 6.0, f"the music is {music_gap - music_act:.1f} dB quieter while the speaker talks than in the settled part of the longer pauses (need 6 or more)"))

    prev = pcm(d / "audio_preview.mp4")
    k = min(len(prev), len(sp))
    pdb = rms_db(prev[:k])
    a2 = act[:len(pdb)]
    if a2.sum() >= 5:
        delta = float(np.mean(pdb[:len(a2)][a2]) - np.mean(sdb[:len(a2)][a2]))
        rows.append(("SPEECH-LEVEL-KEPT", abs(delta) <= 1.0, f"speech level in the mix vs the original: {delta:+.2f} dB"))
    else:
        rows.append(("SPEECH-LEVEL-KEPT", None, "too little speech to measure"))

    sf = pcm(sfx)
    speech_peak, sfx_peak = db(float(np.abs(sp).max())), db(float(np.abs(sf).max()))
    rows.append(("SFX-QUIETER-THAN-SPEECH", speech_peak - sfx_peak >= 5.0, f"effect peak {sfx_peak:.1f} dBFS, speech peak {speech_peak:.1f} dBFS ({speech_peak - sfx_peak:.1f} dB apart)"))

    resid = prev[:k] - sp[:k] - m[:k]
    want = int(round((meta["callout_sec"] - meta["window"]["start"]) * SR))
    lo, hi = max(0, want - SR // 2), min(k - len(sf), want + SR // 2)
    seg = resid[lo:hi + len(sf)]
    corr = np.correlate(seg, sf, mode="valid")
    best = int(np.argmax(np.abs(corr))) + lo
    err_ms = (best - want) / SR * 1000
    rows.append(("SFX-AT-CALLOUT", abs(err_ms) <= 30.0, f"the effect lands {err_ms:+.1f} ms from the callout's entrance ({meta['callout_sec']:.3f}s)"))

    pk = db(float(np.abs(prev).max()))
    rows.append(("MIX-NOT-CLIPPING", pk < -1.0, f"mixed preview peaks at {pk:.2f} dBFS"))

    head, tail, mid = float(np.sqrt((m[:int(0.1 * SR)] ** 2).mean())), float(np.sqrt((m[-int(0.1 * SR):] ** 2).mean())), float(np.sqrt((m[len(m) // 2 - SR // 2:len(m) // 2 + SR // 2] ** 2).mean()))
    rows.append(("FADES", head < 0.5 * mid and tail < 0.5 * mid, f"first 0.1s is {db(head) - db(mid):.1f} dB and last 0.1s is {db(tail) - db(mid):.1f} dB relative to the middle"))

    g = meta["generated"]
    rows.append(("info: GENERATED", None, f"effect: \"{g['sfx']['prompt']}\" ({'reused from cache' if g['sfx']['cached'] else 'newly generated'}); music: \"{g['music']['prompt']}\" ({'reused from cache' if g['music']['cached'] else 'newly generated'})"))

    gating = {"STEM-FORMAT", "MUSIC-AUDIBLE", "MUSIC-UNDER-SPEECH", "DUCKS", "SPEECH-LEVEL-KEPT", "SFX-QUIETER-THAN-SPEECH", "SFX-AT-CALLOUT", "MIX-NOT-CLIPPING", "FADES"}
    wd = max(len(r[0]) for r in rows)
    bad = 0
    for name, ok, detail in rows:
        mark = "INFO" if ok is None and name.startswith("info") else "SKIP" if ok is None else "PASS" if ok else "FAIL"
        bad += (ok is False) and name in gating
        print(f"  [{mark}] {name.ljust(wd)}  {detail}")
    print("\nAll gating checks passed." if not bad else f"\n{bad} check(s) FAILED.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
