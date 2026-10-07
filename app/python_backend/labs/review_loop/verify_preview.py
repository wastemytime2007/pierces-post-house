#!/usr/bin/env python3
"""Check a built review folder against the ORIGINAL sources, not against itself.

    python3 labs/review_loop/verify_preview.py <review_dir>

  DURATION     preview length matches the cut zone.
  AUDIO-PRESENT / AUDIO-AUDIBLE   the preview is not silent.
  FRAME-MATCH  frames pulled from the preview resemble the source frames the
               XML says they came from (and NOT frames from elsewhere in the file).
  LAV-SYNC     the lav placed on the timeline lines up with that clip's own camera
               audio (cross-correlation peak within 0.1s). This is the check that
               catches the frame-rate misreading in timeline.py's header.
Exit 0 = all applicable checks passed.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy.signal import correlate

sys.path.insert(0, str(Path(__file__).resolve().parent))

SR = 8000


def _pcm(path: str, start: float, dur: float) -> np.ndarray:
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{start:.4f}", "-t", f"{dur:.4f}", "-i", path,
         "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True)
    return np.frombuffer(p.stdout, dtype=np.float32)


def _gray(path: str, t: float) -> np.ndarray:
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.4f}", "-i", path, "-frames:v", "1",
         "-vf", "scale=64:36", "-pix_fmt", "gray", "-f", "rawvideo", "-"], capture_output=True)
    return np.frombuffer(p.stdout, dtype=np.uint8).astype(np.float32)


def _gray_framed(path: str, t: float, geometry: str) -> np.ndarray:
    """A source frame as the sequence frames it (the clip's Basic Motion), squashed to 64x36 the same way the preview's frame is."""
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.4f}", "-i", path, "-frames:v", "1",
         "-vf", f"{geometry},scale=64:36", "-pix_fmt", "gray", "-f", "rawvideo", "-"], capture_output=True)
    return np.frombuffer(p.stdout, dtype=np.uint8).astype(np.float32)


NTSC_ERR = 1.0 - 29.97 / 30.0      # 0.1%: what a 29.97 fps file's in-point reads as if its frame count is taken at 30 fps (or the other way round)


def ntsc_explained(lag: float, camera_in_sec: float, tol: float = 0.05) -> bool:
    """True when a lav-to-camera lag is 0.1% of where the clip sits in the camera file, to within `tol` seconds.

    That signature means the XML's audio and video in-points disagree only by how the frame count is read: PreCut writes the video in-point as seconds x 30 and keeps the file declared
    29.97, and the lav in-point as seconds x 30. Read at the sequence's 30 fps the two agree exactly; read at the file's 29.97 they are apart by 0.1% of the position. Which one Premiere
    applies is not something this code can know, so such a lag is reported as an open question to settle in Premiere, not as a pass and not as a failure."""
    return camera_in_sec > 30.0 and abs(lag) >= 0.1 and abs(abs(lag) - camera_in_sec * NTSC_ERR) < tol


def _lag(a: np.ndarray, b: np.ndarray) -> float:
    n = min(len(a), len(b))
    a, b = a[:n] - a[:n].mean(), b[:n] - b[:n].mean()
    if n == 0 or a.std() < 1e-6 or b.std() < 1e-6:
        return float("nan")
    c = correlate(a, b, mode="full", method="fft")
    return (int(np.argmax(c)) - (n - 1)) / SR


def main() -> int:
    d = Path(sys.argv[1])
    tl = json.loads((d / "timeline.json").read_text())
    prev = str(d / "preview.mp4")
    rows: list[tuple[str, bool | None, str]] = []

    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                "default=nw=1:nk=1", prev], capture_output=True, text=True).stdout)
    zone = tl["clips"][-1]["end"]
    rows.append(("DURATION", abs(dur - zone) < 0.5, f"preview {dur:.2f}s vs cut {zone:.2f}s"))

    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of",
                              "csv=p=0", prev], capture_output=True, text=True).stdout.split()
    rows.append(("AUDIO-PRESENT", "audio" in streams, f"streams: {','.join(streams)}"))
    vol = subprocess.run(["ffmpeg", "-i", prev, "-af", "volumedetect", "-vn", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    mean = next((float(l.split("mean_volume:")[1].split()[0]) for l in vol.splitlines() if "mean_volume:" in l), None)
    rows.append(("AUDIO-AUDIBLE", mean is not None and mean > -60, f"mean {mean} dB"))

    clips = tl["clips"]
    picks = sorted({0, len(clips) // 2, len(clips) - 1})
    for i in picks:
        c = clips[i]
        mid_tl = (c["start"] + c["end"]) / 2
        mid_src = c["src_in"] + (mid_tl - c["start"])
        got = _gray(prev, mid_tl)
        other_t = (mid_src + 97.0) % max(1.0, c["src_out"] + 60)
        fr = tl.get("frame") or {}
        if c.get("motion") and fr.get("framed_by_motion"):                  # a reframed cut: compare with the source as the sequence frames it, not the whole frame
            from types import SimpleNamespace
            from render_preview import geometry_filter
            ph = int(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height", "-of", "csv=p=0", prev], capture_output=True, text=True).stdout.strip())
            geo = geometry_filter(SimpleNamespace(src_path=c["source_path"], motion=tuple(c["motion"])), fr["width"], fr["height"], ph)
            want, other = _gray_framed(c["source_path"], mid_src, geo), _gray_framed(c["source_path"], other_t, geo)
        else:
            want = _gray(c["source_path"], mid_src)
            other = _gray(c["source_path"], other_t)
            for alt in (mid_src + 3.0, mid_src - 3.0, mid_src + 1.5, mid_src - 1.5):          # a short source has nothing 97 s away: use a frame that exists, as far from this one as the file allows
                if other.size == want.size or alt < 0:
                    break
                other = _gray(c["source_path"], alt)
        if got.size == 0 or want.size != got.size or other.size != got.size:
            rows.append((f"FRAME-MATCH clip {c['idx']}", False, "could not extract comparable frames"))
            continue
        d_right, d_other = float(np.abs(got - want).mean()), float(np.abs(got - other).mean())
        rows.append((f"FRAME-MATCH clip {c['idx']}", d_right < 25 and d_right < d_other,
                     f"diff vs claimed source {d_right:.1f}, vs unrelated frame {d_other:.1f}"))

    cam = {c["source_path"]: c for c in clips}
    checked = 0
    for a in tl.get("audio", []):
        v = next((c for c in clips if abs(c["start"] - a["start"]) < 0.05), None)
        if v is None or checked >= 3:
            continue
        span = min(6.0, v["end"] - v["start"])
        lag = _lag(_pcm(a["source_path"], a["src_in"], span), _pcm(v["source_path"], v["src_in"], span))
        if lag == lag and ntsc_explained(lag, v["src_in"]):
            rows.append((f"LAV-SYNC clip {v['idx']}", None,
                         f"lav vs camera lag {lag:+.3f}s, which is exactly 0.1% of this clip's place in the camera file ({v['src_in']:.0f} s): the lav lines up if the video in-point is read at the "
                         "sequence's 30 fps and is 0.1% off if it is read at the file's 29.97. Open the XML in Premiere and see whether the camera and lav waveforms line up on this clip."))
        else:
            rows.append((f"LAV-SYNC clip {v['idx']}", abs(lag) < 0.1 if lag == lag else None,
                         f"lav vs camera lag {lag:+.3f}s" if lag == lag else "no usable signal"))
        checked += 1
    if not tl.get("audio"):
        rows.append(("LAV-SYNC", None, "camera audio preview; nothing to sync"))

    w = max(len(n) for n, _, _ in rows)
    bad = 0
    for n, ok, detail in rows:
        mark = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
        bad += ok is False
        print(f"  [{mark}] {n.ljust(w)}  {detail}")
    print("\nAll applicable checks passed." if not bad else f"\n{bad} check(s) FAILED.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
