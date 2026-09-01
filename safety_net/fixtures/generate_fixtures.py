#!/usr/bin/env python3
"""Generate the tiny, deterministic media fixtures used by the Phase 0 safety net.

Run this to (re)create everything under safety_net/fixtures/media/. The
generated files ARE committed to the coordination repo (see safety_net/README.md
for why: hermetic beats regeneratable here — a different ffmpeg build can
probe a re-encoded file to a slightly different duration/fps and silently
move the golden master's goalposts). Re-run this script only when you
deliberately want to replace the fixtures, then re-bless the golden XML.

Requires: ffmpeg + ffprobe on PATH.

Produces, all ~4s / 640x360 / 30fps / h264 / yuv420p unless noted:
  stable.mp4          plain testsrc2, silent
  shaky.mp4           testsrc2 + oscillating rotate, silent
  blurred.mp4         testsrc2 + boxblur, silent
  underexposed.mp4    testsrc2 + darkened eq, silent
  overexposed.mp4     testsrc2 + brightened/gamma eq, silent
  AROLL_01.MOV        testsrc2 + 440Hz sine audio, UPPERCASE extension
                      (exercises the case-probing quirk — see DECISIONS.md #1)
  lav.wav             220Hz sine tone, mono 48kHz 16-bit PCM (reserved for a
                      future audio-sync test; not exercised by the Phase 0
                      golden master — see README "Scoped out")
  MANIFEST.json       ffmpeg/ffprobe version strings + per-file probed specs,
                      so encoder drift on another machine is diagnosable
                      instead of silently changing what tests expect.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

MEDIA_DIR = Path(__file__).parent / "media"

# Common flags for deterministic, tiny, silent test video.
COMMON_V = [
    "-frames:v", "120",          # exactly 4s @ 30fps
    "-pix_fmt", "yuv420p",
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
    "-fflags", "+bitexact", "-flags:v", "+bitexact",
    "-map_metadata", "-1", "-map_chapters", "-1",
    "-movflags", "+faststart",
]

VIDEO_SOURCE = "testsrc2=size=640x360:rate=30"


def run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"FAILED: {' '.join(cmd)}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(1)


def make_silent(name: str, vf: str | None) -> Path:
    out = MEDIA_DIR / name
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", VIDEO_SOURCE]
    if vf:
        cmd += ["-vf", vf]
    cmd += COMMON_V + ["-an", str(out)]
    run(cmd)
    return out


def make_aroll_with_audio(name: str) -> Path:
    """The one clip with an audio stream, saved with an UPPERCASE extension."""
    out = MEDIA_DIR / name
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", VIDEO_SOURCE,
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
    ] + COMMON_V + [
        "-t", "4",
        "-c:a", "aac", "-b:a", "96k", "-ac", "1", "-ar", "48000",
        "-shortest",
        str(out),
    ]
    run(cmd)
    return out


def make_lav_wav(name: str) -> Path:
    out = MEDIA_DIR / name
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=4",
        "-ac", "1", "-c:a", "pcm_s16le",
        str(out),
    ]
    run(cmd)
    return out


def probe(path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error", "-show_format", "-show_streams",
        "-of", "json", str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    result.check_returncode()
    data = json.loads(result.stdout)
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    entry = {
        "duration_sec": float(fmt.get("duration", 0.0)),
        "has_audio": audio is not None,
    }
    if video:
        entry["width"] = int(video.get("width", 0))
        entry["height"] = int(video.get("height", 0))
        entry["nb_frames"] = int(video.get("nb_frames", 0)) if video.get("nb_frames") else None
        rate = video.get("r_frame_rate", "0/1")
        num, den = rate.split("/")
        entry["fps"] = (float(num) / float(den)) if float(den) else 0.0
    if audio:
        entry["audio_samplerate"] = int(audio.get("sample_rate", 0))
        entry["audio_channels"] = int(audio.get("channels", 0))
    return entry


def tool_version(tool: str) -> str:
    result = subprocess.run([tool, "-version"], capture_output=True, text=True)
    return result.stdout.splitlines()[0] if result.stdout else ""


def main() -> None:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    generated: dict[str, Path] = {
        "stable.mp4": make_silent("stable.mp4", None),
        "shaky.mp4": make_silent("shaky.mp4", "rotate=0.04*sin(2*PI*2*t):c=black"),
        "blurred.mp4": make_silent("blurred.mp4", "boxblur=8:1"),
        "underexposed.mp4": make_silent("underexposed.mp4", "eq=brightness=-0.4"),
        "overexposed.mp4": make_silent("overexposed.mp4", "eq=brightness=0.4:gamma=1.8"),
        "AROLL_01.MOV": make_aroll_with_audio("AROLL_01.MOV"),
        "lav.wav": make_lav_wav("lav.wav"),
    }

    manifest = {
        "ffmpeg_version": tool_version("ffmpeg"),
        "ffprobe_version": tool_version("ffprobe"),
        "files": {name: probe(path) for name, path in generated.items()},
    }
    manifest_path = MEDIA_DIR / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    total_bytes = sum(p.stat().st_size for p in generated.values())
    print(f"Generated {len(generated)} fixture files, {total_bytes / 1024:.1f} KiB total.")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
