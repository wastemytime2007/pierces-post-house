#!/usr/bin/env python3
"""A short clip to check a bleep by eye and ear, with the clock burned in.

    python3 labs/bleep/check_clip.py --video "<review>/preview_full.mp4" --bleep-json "<bleep>/bleep.json" --start 27.5 --end 31.0 --out bleep_check.mp4

Plays the cut's own audio (with the bleep) under a large clock that shows the TIMELINE seconds to two decimals (the same clock as the review page), and a
red BLEEP badge on screen exactly while the bleep is sounding. Read the clock when the tone starts and when it stops. Nothing else is needed, so there is no
question about which file, which tool or which timeline is being looked at. (This ffmpeg has no text filter, so the clock is drawn with Pillow and overlaid.)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = "/System/Library/Fonts/Helvetica.ttc"
FPS = 30
W = 960


def overlay_frames(spans: list[list[float]], start: float, end: float, work: Path, height: int) -> int:
    big = ImageFont.truetype(FONT, 76)
    n = int(round((end - start) * FPS))
    for i in range(n):
        t = start + i / FPS
        im = Image.new("RGBA", (W, height), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, W, 105], fill=(0, 0, 0, 185))
        d.text((20, 10), f"{t:6.2f} s", font=big, fill=(255, 255, 255, 255))
        if any(a <= t < b for a, b in spans):
            d.rectangle([W - 330, 0, W, 105], fill=(220, 30, 30, 235))
            d.text((W - 312, 10), "BLEEP", font=big, fill=(255, 255, 255, 255))
        im.save(work / f"c_{i:05d}.png")
    return n


def make(video: Path, spans: list[list[float]], start: float, end: float, out: Path) -> Path:
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video)], capture_output=True, text=True).stdout.strip()
    vw, vh = (int(x) for x in probe.split(","))
    height = int(round(W * vh / vw / 2)) * 2
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        overlay_frames(spans, start, end, work, height)
        p = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start}", "-t", f"{end - start}", "-i", str(video), "-framerate", str(FPS), "-i", str(work / "c_%05d.png"),
                            "-filter_complex", f"[0:v]scale={W}:{height},fps={FPS}[v];[v][1:v]overlay=shortest=1[o]", "-map", "[o]", "-map", "0:a",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)], capture_output=True, text=True)
    if p.returncode != 0 or not out.exists():
        raise SystemExit(f"REFUSING: the clip could not be made: {p.stderr[-300:]}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--bleep-json", type=Path, required=True)
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--end", type=float, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    spans = json.loads(a.bleep_json.read_text())["spans"]
    make(a.video, spans, a.start, a.end, a.out)
    print(f"{a.out}: timeline {a.start:.2f}-{a.end:.2f}s, BLEEP badge at " + ", ".join(f"{x:.2f}-{y:.2f}s" for x, y in spans if y > a.start and x < a.end))
    return 0


if __name__ == "__main__":
    sys.exit(main())
