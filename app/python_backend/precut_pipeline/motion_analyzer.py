"""Stage 1c: Deterministic motion / framing analysis.

Drop 3.8: adds shot-type tags (static/pan/tilt/zoom, wide/medium/close)
computed from the video itself instead of guessed by a VLM. These are
measured facts, not inferences — they don't hallucinate.

Approach:
  - Sample ~6 frames evenly across the clip at low resolution (160 wide).
  - Compare consecutive pairs using pixel-difference and subregion shifts.
  - Emit 1-3 motion tags per clip, e.g. ["static"] or ["pan", "medium_shot"].

These tags are added ONCE at the clip level, not per-frame. They're mixed
into the per-clip aggregated tag list that feeds the library bin and
marker suggestions.

Dependency: ffmpeg (already required). No new packages. numpy + PIL are
already project deps via CLIP.
"""
from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import numpy as np
    from PIL import Image
except ImportError:
    np = None  # type: ignore
    Image = None  # type: ignore


# Tuning constants. All derived from experiment on typical interior/exterior
# real-estate footage at 24-30fps; adjust if you find a class of clips that
# gets the wrong tag systematically.
ANALYSIS_FRAMES = 6                  # how many frames to sample per clip
ANALYSIS_WIDTH = 160                 # downscale width for speed
STATIC_THRESHOLD_MAD = 3.0           # mean absolute pixel difference below → static
PAN_CENTROID_SHIFT_FRAC = 0.04       # fractional horizontal centroid shift for pan
TILT_CENTROID_SHIFT_FRAC = 0.04      # fractional vertical centroid shift for tilt
ZOOM_STD_DELTA_FRAC = 0.06           # fractional change in brightness std-dev for zoom


@dataclass
class MotionResult:
    """What _analyze returns. Flat and boring on purpose."""
    motion_tags: list[str]
    framing_tag: Optional[str]

    def as_list(self) -> list[str]:
        tags = list(self.motion_tags)
        if self.framing_tag:
            tags.append(self.framing_tag)
        return tags


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_clip(video_path: Path, duration_sec: float) -> list[str]:
    """Return a list of shot-type tags for the clip.

    Returns an empty list on any failure so the pipeline degrades
    gracefully to just the VLM tags. Never raises.
    """
    if np is None or Image is None:
        return []
    if duration_sec <= 0.2:
        return []

    try:
        frames = _sample_frames_as_arrays(video_path, duration_sec)
        if len(frames) < 2:
            return []
        result = _analyze(frames)
        return result.as_list()
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Frame sampling
# ---------------------------------------------------------------------------


def _sample_frames_as_arrays(video_path: Path, duration_sec: float) -> list["np.ndarray"]:
    """Sample ANALYSIS_FRAMES frames evenly across the clip and return
    them as grayscale numpy arrays at ANALYSIS_WIDTH pixels wide."""
    timestamps = _even_timestamps(duration_sec, ANALYSIS_FRAMES)
    arrays: list["np.ndarray"] = []
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        for i, t in enumerate(timestamps):
            frame_path = td_path / f"f{i:02d}.jpg"
            ok = _ffmpeg_extract_one_frame(video_path, t, frame_path, ANALYSIS_WIDTH)
            if not ok or not frame_path.exists():
                continue
            try:
                img = Image.open(frame_path).convert("L")  # grayscale
                arrays.append(np.asarray(img, dtype=np.float32))
            except Exception:
                continue
    return arrays


def _even_timestamps(duration_sec: float, n: int) -> list[float]:
    """n evenly-spaced timestamps, nudged inward so we never hit frame 0
    or the very end (which are often black/slates)."""
    if n < 2:
        return [duration_sec / 2.0]
    pad = min(0.3, duration_sec * 0.05)
    start = pad
    end = max(start + 0.1, duration_sec - pad)
    step = (end - start) / (n - 1)
    return [start + i * step for i in range(n)]


def _ffmpeg_extract_one_frame(
    video: Path, t: float, out: Path, width: int,
) -> bool:
    """Extract one frame at time `t` seconds, downscaled to `width` pixels."""
    cmd = [
        "ffmpeg", "-nostdin", "-loglevel", "error",
        "-ss", f"{t:.3f}",
        "-i", str(video),
        "-frames:v", "1",
        "-vf", f"scale={width}:-2",
        "-q:v", "5",
        str(out),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        return result.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def _analyze(frames: list["np.ndarray"]) -> MotionResult:
    """Look at consecutive-frame differences and decide what kind of motion
    this clip has, plus a rough framing tag."""
    # Normalize frame sizes to the smallest common shape (in case ffmpeg
    # produced slightly different heights due to odd source aspect ratios).
    h = min(f.shape[0] for f in frames)
    w = min(f.shape[1] for f in frames)
    frames = [f[:h, :w] for f in frames]

    # -- Pairwise MAD (mean absolute difference) ---------------------------
    mads: list[float] = []
    for a, b in zip(frames, frames[1:]):
        mads.append(float(np.mean(np.abs(a - b))))
    avg_mad = float(np.mean(mads)) if mads else 0.0

    if avg_mad < STATIC_THRESHOLD_MAD:
        motion_tags = ["static"]
    else:
        motion_tags = _classify_motion_direction(frames, w, h)

    # -- Framing: rough measure based on dominant-region size -------------
    framing = _estimate_framing(frames[len(frames) // 2])

    return MotionResult(motion_tags=motion_tags, framing_tag=framing)


def _classify_motion_direction(
    frames: list["np.ndarray"], w: int, h: int,
) -> list[str]:
    """Decide between pan_left/right, tilt_up/down, zoom_in/out, or generic motion."""
    # Compute per-frame brightness centroid (weighted center of mass).
    centroids_x: list[float] = []
    centroids_y: list[float] = []
    std_devs: list[float] = []

    xs = np.arange(w, dtype=np.float32)
    ys = np.arange(h, dtype=np.float32)

    for f in frames:
        total = f.sum()
        if total <= 0:
            centroids_x.append(w / 2)
            centroids_y.append(h / 2)
            std_devs.append(float(np.std(f)))
            continue
        cx = float((f.sum(axis=0) * xs).sum() / total)
        cy = float((f.sum(axis=1) * ys).sum() / total)
        centroids_x.append(cx)
        centroids_y.append(cy)
        std_devs.append(float(np.std(f)))

    dx = centroids_x[-1] - centroids_x[0]
    dy = centroids_y[-1] - centroids_y[0]
    dstd = std_devs[-1] - std_devs[0]

    frac_dx = dx / max(1.0, w)
    frac_dy = dy / max(1.0, h)
    frac_dstd = dstd / max(1.0, std_devs[0])

    tags: list[str] = []
    # Horizontal pan?
    if abs(frac_dx) >= PAN_CENTROID_SHIFT_FRAC and abs(frac_dx) > abs(frac_dy):
        tags.append("pan_right" if frac_dx > 0 else "pan_left")
    # Vertical tilt?
    elif abs(frac_dy) >= TILT_CENTROID_SHIFT_FRAC:
        tags.append("tilt_down" if frac_dy > 0 else "tilt_up")
    # Zoom (strong change in std-dev implies field-of-view change)?
    elif abs(frac_dstd) >= ZOOM_STD_DELTA_FRAC:
        tags.append("zoom_in" if frac_dstd > 0 else "zoom_out")
    else:
        tags.append("camera_motion")
    return tags


def _estimate_framing(frame: "np.ndarray") -> Optional[str]:
    """Rough framing heuristic using edge density across the frame.

    Wide shots tend to have many small high-contrast features distributed
    across the frame. Close-ups have a single dominant subject occupying
    most of the frame with smoother surrounds.

    We measure: the horizontal "content spread" — how far from the center
    does the Sobel-like edge energy extend?
    """
    try:
        # Simple horizontal gradient magnitude
        h, w = frame.shape
        grad_x = np.abs(np.diff(frame, axis=1))
        col_energy = grad_x.sum(axis=0)  # shape (w-1,)
        if col_energy.sum() <= 0:
            return None

        # Normalize and find what column range holds 80% of the energy
        csum = np.cumsum(col_energy)
        total = csum[-1]
        left_idx = int(np.searchsorted(csum, total * 0.1))
        right_idx = int(np.searchsorted(csum, total * 0.9))
        span = max(1, right_idx - left_idx)
        span_frac = span / max(1, w - 1)

        if span_frac >= 0.75:
            return "wide_shot"
        if span_frac >= 0.45:
            return "medium_shot"
        return "close_up"
    except Exception:
        return None
