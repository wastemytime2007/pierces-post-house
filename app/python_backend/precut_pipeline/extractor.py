"""Extract keyframes from video files using FFmpeg."""
import subprocess
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import ffmpeg

from .config import FRAME_SAMPLE_INTERVAL_SEC, MAX_FRAMES_PER_CLIP, FRAME_WIDTH


@dataclass
class ClipInfo:
    duration_sec: float
    width: int
    height: int
    fps: float


def probe_clip(video_path: Path) -> Optional[ClipInfo]:
    """Get metadata about a video file. Returns None if probe fails."""
    try:
        probe = ffmpeg.probe(str(video_path))
    except ffmpeg.Error:
        return None

    video_stream = next(
        (s for s in probe["streams"] if s["codec_type"] == "video"),
        None
    )
    if video_stream is None:
        return None

    # Duration: prefer format-level, fall back to stream
    duration = None
    if "duration" in probe.get("format", {}):
        duration = float(probe["format"]["duration"])
    elif "duration" in video_stream:
        duration = float(video_stream["duration"])

    if duration is None or duration <= 0:
        return None

    # Parse FPS like "30000/1001"
    fps_str = video_stream.get("r_frame_rate", "0/1")
    num, den = fps_str.split("/")
    fps = float(num) / float(den) if float(den) > 0 else 0

    return ClipInfo(
        duration_sec=duration,
        width=int(video_stream.get("width", 0)),
        height=int(video_stream.get("height", 0)),
        fps=fps,
    )


def compute_frame_timestamps(duration_sec: float) -> list[float]:
    """Pick timestamps to sample, with a safety cap for long clips."""
    # Start a bit in (first frame is often black/slate), end a bit before the end.
    start = min(0.5, duration_sec / 10)
    end = max(duration_sec - 0.5, start)

    if end <= start:
        return [duration_sec / 2]  # single midpoint for very short clips

    ideal_count = max(1, int((end - start) / FRAME_SAMPLE_INTERVAL_SEC))
    count = min(ideal_count, MAX_FRAMES_PER_CLIP)

    if count == 1:
        return [(start + end) / 2]

    step = (end - start) / (count - 1) if count > 1 else 0
    return [start + i * step for i in range(count)]


def extract_frame(video_path: Path, timestamp_sec: float, output_path: Path) -> bool:
    """Extract a single frame at the given timestamp. Returns True on success."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Seek before -i is faster (input-side seek), then output a single frame.
        # -vf scale keeps aspect ratio, sets width to FRAME_WIDTH.
        (
            ffmpeg
            .input(str(video_path), ss=timestamp_sec)
            .output(
                str(output_path),
                vframes=1,
                vf=f"scale={FRAME_WIDTH}:-2",
                loglevel="error",
            )
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
        return output_path.exists() and output_path.stat().st_size > 0
    except ffmpeg.Error:
        return False


def extract_all_frames(video_path: Path, frames_dir: Path, clip_id: int) -> list[tuple[float, Path]]:
    """Extract all sampled frames from a clip.

    Returns list of (timestamp_sec, frame_path) tuples for frames that extracted successfully.
    """
    info = probe_clip(video_path)
    if info is None:
        return []

    timestamps = compute_frame_timestamps(info.duration_sec)
    clip_frames_dir = frames_dir / f"clip_{clip_id:06d}"

    results = []
    for i, ts in enumerate(timestamps):
        output = clip_frames_dir / f"frame_{i:04d}_t{ts:.2f}.jpg"
        if extract_frame(video_path, ts, output):
            results.append((ts, output))

    return results
