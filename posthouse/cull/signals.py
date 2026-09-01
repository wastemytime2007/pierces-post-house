"""posthouse.cull.signals — the deterministic signal extractor.

Phase 4, slice 1 (ROADMAP.md §6 Phase 4;
``docs/design/PHASE4_CULL_DESIGN.md`` §1 signal layer, §4 sidecar, §5
"Slice 1"; ``docs/contracts/CULLS.md`` §6). No classification, no
segments, no ``culls.json`` — this module decodes one source file,
measures four deterministic signal families over every frame, and
writes the per-clip signals sidecar (npz + JSON header) that later
slices (classify, segment, fit) read.

Decode (design §1.1)
---------------------
One ffmpeg pipe per file, decoding the ORIGINAL source (never a proxy —
see "Why not proxies" below), scaled to a 960x540 8-bit gray plane,
emitted as rawvideo on a pipe and consumed frame by frame in numpy. No
intermediate file is ever written. VideoToolbox hardware decode
(``-hwaccel videotoolbox``) is used when available (detected once per
process and cached); a hardware-decode failure falls back to software
decode with a logged note on stderr — it never crashes the run. Decode
mode, ffmpeg version, and the source's own fps/duration/frame count are
recorded in the sidecar header.

Global motion (design §1.2)
----------------------------
Per frame pair, a 3x3 grid of 256x256 Hann-windowed blocks (evenly
spaced within the 960x540 plane, overlapping where the grid does not
tile exactly — the plane is shorter than 3*256 in height) is
phase-correlated via FFT against the same block position in the
previous frame, with sub-pixel refinement (a parabola fit around the
integer peak on each axis). The nine block shifts are then fit, by
weighted least squares, to a 4-DOF similarity model in ``(tx, ty,
log_scale, roll)`` using the standard small-motion linearization
between consecutive frames::

    dx_i = tx + log_scale * px_i - roll * py_i
    dy_i = ty + roll      * px_i + log_scale * py_i

where ``(px_i, py_i)`` is block *i*'s position relative to the plane
center. Blocks whose raw correlation peak confidence falls below
``BLOCK_CONFIDENCE_FLOOR`` are down-weighted to zero (a block of
featureless wall votes on nothing) rather than hard-dropped, so a frame
where every block is weak still produces a (poorly-conditioned, high
-residual) fit instead of a crash. The fit's own least-squares residual
is kept as its own per-frame signal (``resid``) — it is what
distinguishes a rigid camera move (fits the model) from a jolt or
subject motion (does not).

A per-frame ``hf_energy`` (high-frequency band energy) is computed
after the full per-frame series is assembled, as a windowed high-pass
energy of a single combined-motion scalar (translation magnitude plus
the rotation term converted to an equivalent pixel displacement at a
representative radius — see ``_motion_speed_px``). This is a
deliberate implementation choice broader than the design doc's literal
"band power of v" (translation-only): the safety-net ``shaky.mp4``
fixture shakes via oscillating *rotation* about the frame center
(``rotate=0.04*sin(2*PI*2*t)``, see
``safety_net/fixtures/generate_fixtures.py``), which a translation-only
speed signal would barely see. Folding roll in in the same units keeps
the signal meaningful for a shake that is dominated by roll instead of
by translation, without changing the design's intent (separating a
fast *oscillation* from a slow deliberate move). Flagged for the
Architect: the design's "per-frame high-frequency band energy" is not
fully specified; this is the reading used here.

Velocities are reported both in px/frame at the analysis plane
(``tx``, ``ty`` as fit, at 960-wide) and normalized to the source's
native width (``tx_norm_src_width`` / ``ty_norm_src_width``), per
design §1.3's "px/frame normalized to 3840 width" convention
generalized to whatever the source's actual width is (960 is not
universal — a portrait or SD source would silently misnormalize
against a hardcoded 3840).

Sharpness (design §1.4)
------------------------
Laplacian variance per frame (4-neighbour kernel, whole analysis
plane), computed by array slicing — no scipy/OpenCV. Raw values are
always kept (``lapvar``); a derived per-clip normalization
(``lapvar_norm``, against the clip's own 90th-percentile lapvar) is
also stored, exactly as the design allows ("per-clip normalization is a
derived quantity you may also store, but raw must be kept"). No shape
judgment (steady/rack/hunt) happens in this slice — that is slice 2's
classifier.

Exposure (design §1.5)
------------------------
Luma mean/std, clipped-low fraction (< 16/255), clipped-high fraction
(> 239/255), and a decimated 64-bin histogram (every 15th frame,
int16), all from the same gray analysis plane.

Audio (design §1.6)
---------------------
A separate ffmpeg pass (``-vn -ac 1 -ar 48000 -f f32le``) extracts the
ORIGINAL audio track (never a proxy's re-encode) to mono float32 PCM,
measured in 20ms windows: sample peak (dBFS), RMS (dBFS), and a
clip-run length (the longest run of consecutive near-full-scale samples
within the window, in samples — the design brief calls for "peak, RMS,
clip-run length" and this is the literal reading of that phrase; the
contract's segment-level ``clipped_frac`` is a different, later-stage
quantity computed by the segmenter, not this array). A source with no
audio stream (checked via ffprobe before ever invoking the audio pipe,
never by parsing an ffmpeg error string) gets an explicit ``"audio":
None`` marker in the sidecar header and no audio arrays in the npz —
never a crash, never a silently-empty array pretending to be real
measurements. Speech presence is explicitly NOT part of this slice
(design §1.6, §5 slice 5 — it reuses ``posthouse.harvest.transcribe``).

Why not proxies (ROADMAP §4, design §1.1 "why this is not a proxy
shortcut")
------------------------------------------------------------------------
Analysis always runs on the original file. PreCut's CRF-28 540p
proxies are a *lossy re-encode*: adaptive bitrate control turns the
sharpness score into a bitrate meter, 8-bit re-encoding manufactures or
hides exposure clipping, and AAC does not preserve audio sample peaks.
None of that is true of this module's own 960x540 gray downscale — it
is a deterministic Lanczos/bilinear resample of the original bitstream
in memory, with no re-encode and no rate control — which is why
``params.analysis.source_grade`` (recorded here as
``analysis.source_grade``) is always ``"original"`` or
``"analysis_decode"``, never ``"proxy"``. The proxy-vs-source agreement
test in ``safety_net/tests/test_cull_signals.py`` is the evidence for
this rule, not a formality: it is *expected* to show close agreement on
global motion and clear disagreement on sharpness and audio peaks.

Sidecar (design §4, contract §6)
----------------------------------
``<out_dir>/<source_name>.signals.npz`` — one float32 array per video
signal, ``analysed_frames`` long (``tx``, ``ty``,
``tx_norm_src_width``, ``ty_norm_src_width``, ``log_scale``, ``roll``,
``resid``, ``peak``, ``hf_energy``, ``lapvar``, ``lapvar_norm``,
``luma_mean``, ``luma_std``, ``clip_low``, ``clip_high``), plus a
decimated ``hist64`` (int16, one row per 15th frame) and, when the
source has an audio stream, ``audio_peak_dbfs`` / ``audio_rms_dbfs`` /
``audio_clip_run`` at their own 20ms rate. This slice does not write a
``state`` array or a run-length-encoded state sequence — those belong
to the classifier (slice 2) and segmenter (slice 3), which do not exist
yet; writing a placeholder state array here would misrepresent
unclassified frames as classified. ``<out_dir>/<source_name>.signals.json``
carries provenance (ffmpeg/numpy versions, decode mode, plane size,
source fps/duration/frame count, the source's sha256, a run
timestamp), the column dictionary with units, and the audio presence
marker. Written tempfile-then-``os.replace`` for both files, exactly as
``posthouse.manifest`` and ``posthouse.coldfootage``'s siblings do.
Determinism: given the same source file and the same ``decode`` mode,
two runs produce byte-identical ``.npz`` files and identical JSON
headers apart from ``created_at`` — ffmpeg's video filter graph and
FFT-based phase correlation are deterministic per invocation, and
``-threads 1`` is used on both ffmpeg passes to remove the one
remaining source of run-to-run nondeterminism (frame-level decode
threading can reorder work internally even though ffmpeg's own output
order is stable; -threads 1 removes any doubt rather than trusting
that guarantee under a hardware decoder).

Entry points
------------
* Python API: :func:`extract_signals`.
* CLI: ``python -m posthouse.cull.signals extract SOURCE --out DIR
  [--decode auto|videotoolbox|software]`` — prints per-stage timing and
  the realtime factor, exits non-zero with every problem listed on
  failure.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ANALYSIS_WIDTH = 960
ANALYSIS_HEIGHT = 540
ANALYSIS_FRAME_BYTES = ANALYSIS_WIDTH * ANALYSIS_HEIGHT  # 8-bit gray, 1 byte/pixel

BLOCK_SIZE = 256
GRID = 3  # 3x3 grid of blocks

AUDIO_SAMPLE_RATE = 48000
AUDIO_WINDOW_SEC = 0.020
AUDIO_WINDOW_SAMPLES = int(round(AUDIO_SAMPLE_RATE * AUDIO_WINDOW_SEC))  # 960
AUDIO_CLIP_THRESHOLD = 0.999  # near full-scale float32 sample magnitude

CLIP_LOW_THRESHOLD = 16 / 255.0
CLIP_HIGH_THRESHOLD = 239 / 255.0
HIST_BINS = 64
HIST_DECIMATION = 15  # store the histogram for every 15th frame only

# Not yet fitted (that is slice 4's job) — reasonable, documented defaults so
# slice 1 produces a usable, orderable signal today.
BLOCK_CONFIDENCE_FLOOR = 0.02
HF_HIGHPASS_WINDOW_FRAMES = 10  # ~3 Hz cutoff at 30fps (see _hf_energy)
HF_ENERGY_WINDOW_FRAMES = 10
# Radius (px, analysis plane) at which a roll-rate is converted to an
# equivalent px/frame speed for hf_energy — half the plane diagonal, i.e. a
# "typical" block distance from the rotation center.
ROLL_TO_PX_RADIUS = math.hypot(ANALYSIS_WIDTH, ANALYSIS_HEIGHT) / 2.0

SIGNALS_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class SignalsError(Exception):
    """Base class for signal-extraction failures."""


class SignalsValidationError(SignalsError):
    """Raised with every input problem listed, not just the first."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        message = "Signal extraction input validation failed:\n" + "\n".join(
            f"  - {p}" for p in problems
        )
        super().__init__(message)


# ---------------------------------------------------------------------------
# ffmpeg/ffprobe plumbing
# ---------------------------------------------------------------------------

# Deliberately NOT importing posthouse.harvest.proxy_manager's find_ffmpeg:
# that wrapper goes through precut_bridge.import_precut, which requires
# PRECUT_ROOT and prints a startup warning if it isn't a real checkout.
# Design §5 slice 1 is explicit: "Dependencies: ffmpeg (already required),
# numpy. Nothing from PreCut." — this tiny common-paths lookup is
# duplicated from proxy_manager.py rather than adding a PreCut dependency
# to a module that has none otherwise.
_COMMON_FFMPEG_PATHS = [
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/opt/local/bin/ffmpeg",
    "/usr/bin/ffmpeg",
]


@functools.lru_cache(maxsize=1)
def _ffmpeg_path() -> Optional[str]:
    import shutil
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in _COMMON_FFMPEG_PATHS:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


@functools.lru_cache(maxsize=1)
def _ffprobe_path() -> Optional[str]:
    import shutil
    found = shutil.which("ffprobe")
    if found:
        return found
    ffmpeg = _ffmpeg_path()
    if ffmpeg:
        candidate = str(Path(ffmpeg).with_name("ffprobe"))
        if Path(candidate).is_file():
            return candidate
    for candidate in ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe", "/usr/bin/ffprobe"):
        if Path(candidate).is_file():
            return candidate
    return None


@functools.lru_cache(maxsize=1)
def _ffmpeg_version() -> str:
    ffmpeg = _ffmpeg_path()
    if ffmpeg is None:
        return "unknown"
    try:
        out = subprocess.run(
            [ffmpeg, "-version"], capture_output=True, text=True, timeout=10
        ).stdout
        first_line = out.splitlines()[0] if out else ""
        # "ffmpeg version 8.1 Copyright ..." -> "8.1"
        parts = first_line.split()
        if len(parts) >= 3 and parts[0] == "ffmpeg" and parts[1] == "version":
            return parts[2]
        return first_line.strip() or "unknown"
    except (OSError, subprocess.SubprocessError, IndexError):
        return "unknown"


@functools.lru_cache(maxsize=1)
def _videotoolbox_available() -> bool:
    """Detected once per process (design §1.1: "detect once"), cached."""
    ffmpeg = _ffmpeg_path()
    if ffmpeg is None:
        return False
    try:
        out = subprocess.run(
            [ffmpeg, "-hide_banner", "-hwaccels"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        return "videotoolbox" in out.lower()
    except (OSError, subprocess.SubprocessError):
        return False


@dataclass
class ProbeInfo:
    duration_sec: float
    fps: float
    width: int
    height: int
    nb_frames: Optional[int]
    has_audio: bool


def _probe_source(source_path: Path) -> ProbeInfo:
    ffprobe = _ffprobe_path()
    if ffprobe is None:
        raise SignalsError("ffprobe not found on PATH or common install locations")

    cmd = [
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(source_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.SubprocessError as e:
        raise SignalsError(f"ffprobe failed on {source_path}: {e}") from e
    if result.returncode != 0:
        raise SignalsError(
            f"ffprobe exited {result.returncode} on {source_path}: {result.stderr.strip()}"
        )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise SignalsError(f"ffprobe produced invalid JSON for {source_path}: {e}") from e

    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise SignalsError(f"{source_path} has no video stream")

    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or video.get("duration") or 0.0)

    rate_str = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
    num, _, den = rate_str.partition("/")
    try:
        num_f, den_f = float(num), float(den) if den else 1.0
        fps = num_f / den_f if den_f else 0.0
    except ValueError:
        fps = 0.0

    nb_frames = None
    if video.get("nb_frames"):
        try:
            nb_frames = int(video["nb_frames"])
        except (TypeError, ValueError):
            nb_frames = None

    return ProbeInfo(
        duration_sec=duration,
        fps=fps,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        nb_frames=nb_frames,
        has_audio=audio is not None,
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Video decode: one ffmpeg pipe, consumed frame by frame
# ---------------------------------------------------------------------------

def _video_decode_cmd(ffmpeg: str, source_path: Path, hwaccel: bool) -> list[str]:
    # -nostdin: ffmpeg must never wait on a terminal for input; a batch cull
    # has no one at the keyboard.
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    if hwaccel:
        cmd += ["-hwaccel", "videotoolbox"]
    cmd += [
        "-threads", "1",
        "-i", str(source_path),
        "-vf", f"scale={ANALYSIS_WIDTH}:{ANALYSIS_HEIGHT},format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray",
        "pipe:1",
    ]
    return cmd


def _iter_gray_frames(cmd: list[str]):
    """Run one decode pass, yielding (index, frame) uint8 arrays of shape
    (ANALYSIS_HEIGHT, ANALYSIS_WIDTH). Raises SignalsError if ffmpeg exits
    non-zero — the caller decides whether that means "fall back to
    software" or "genuinely failed."
    """
    # stderr goes to a temp FILE, never a pipe. With stderr=PIPE, ffmpeg
    # blocks the moment its warnings fill the 64 KB pipe buffer while we are
    # blocked reading frames from stdout: a deadlock that only appears on
    # long or noisy decodes, i.e. real footage, never fixtures. Code review
    # reproduced the hang. A file has no such limit, and we can still quote
    # its tail on failure.
    with tempfile.TemporaryFile(prefix="posthouse-ffmpeg-stderr-") as errf:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf)
        assert proc.stdout is not None
        index = 0
        completed = False
        try:
            while True:
                raw = proc.stdout.read(ANALYSIS_FRAME_BYTES)
                if len(raw) < ANALYSIS_FRAME_BYTES:
                    break
                frame = np.frombuffer(raw, dtype=np.uint8).reshape(
                    (ANALYSIS_HEIGHT, ANALYSIS_WIDTH)
                )
                yield index, frame
                index += 1
            completed = True
        finally:
            if not completed:
                # The consumer stopped early (or an exception is unwinding).
                # Kill ffmpeg rather than closing its stdout under it and then
                # misreporting the EPIPE exit as a decode failure.
                proc.kill()
                proc.stdout.close()
                proc.wait(timeout=60)
            else:
                proc.stdout.close()
                returncode = proc.wait(timeout=60)
                if returncode != 0:
                    errf.seek(0)
                    tail = errf.read()[-4000:].decode("utf-8", "replace").strip()
                    raise SignalsError(
                        f"ffmpeg decode exited {returncode} after {index} frames: {tail}"
                    )


def _decode_all_frames(source_path: Path, decode: str) -> tuple[list, str]:
    """Decode every frame, with the auto/forced hwaccel-then-software
    fallback (design §1.1: "never crash"). Returns (frames, decode_mode_used).
    """
    ffmpeg = _ffmpeg_path()
    if ffmpeg is None:
        raise SignalsError("ffmpeg not found on PATH or common install locations")

    want_hw = decode in ("auto", "videotoolbox")
    if want_hw and decode == "auto":
        want_hw = _videotoolbox_available()

    if want_hw:
        try:
            frames = list(_iter_gray_frames(_video_decode_cmd(ffmpeg, source_path, hwaccel=True)))
            if frames:
                return frames, "hwaccel_videotoolbox"
            print(
                f"note: hardware decode of {source_path} produced 0 frames, "
                f"falling back to software decode",
                file=sys.stderr,
            )
        except SignalsError as e:
            print(
                f"note: hardware decode of {source_path} failed ({e}); "
                f"falling back to software decode",
                file=sys.stderr,
            )

    frames = list(_iter_gray_frames(_video_decode_cmd(ffmpeg, source_path, hwaccel=False)))
    return frames, "software"


# ---------------------------------------------------------------------------
# Global motion: block-wise phase correlation + similarity fit
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _hann_window(size: int) -> np.ndarray:
    w1d = np.hanning(size).astype(np.float32)
    if w1d.sum() == 0:  # np.hanning(1) is [0.]; guard degenerate sizes
        w1d = np.ones(size, dtype=np.float32)
    return np.outer(w1d, w1d)


def _block_centers() -> list[tuple[int, int]]:
    """3x3 grid of block top-left (y, x) positions within the analysis
    plane, evenly spaced so each BLOCK_SIZE block stays fully in-bounds.
    Overlapping blocks are fine (there is no tiling requirement) — the
    plane is shorter than GRID*BLOCK_SIZE in height, so the vertical
    positions overlap while the horizontal ones do not.
    """
    def _positions(extent: int) -> list[int]:
        max_start = extent - BLOCK_SIZE
        if max_start <= 0:
            return [0] * GRID
        return [round(i * max_start / (GRID - 1)) for i in range(GRID)]

    ys = _positions(ANALYSIS_HEIGHT)
    xs = _positions(ANALYSIS_WIDTH)
    return [(y, x) for y in ys for x in xs]


def _phase_correlate(prev_block: np.ndarray, cur_block: np.ndarray) -> tuple[float, float, float]:
    """Sub-pixel (dx, dy, confidence) shift of cur_block relative to
    prev_block, via FFT phase correlation on Hann-windowed blocks.
    """
    window = _hann_window(prev_block.shape[0])
    a = prev_block.astype(np.float32) * window
    b = cur_block.astype(np.float32) * window

    fa = np.fft.fft2(a)
    fb = np.fft.fft2(b)
    cross = fa * np.conj(fb)
    mag = np.abs(cross)
    mag[mag < 1e-8] = 1e-8
    r = np.fft.ifft2(cross / mag).real

    h, w = r.shape
    peak_idx = np.unravel_index(np.argmax(r), r.shape)
    peak_val = float(r[peak_idx])
    confidence = float(peak_val - r.mean())

    py, px = peak_idx
    dy = py if py <= h // 2 else py - h
    dx = px if px <= w // 2 else px - w

    def _parabola_subpixel(vals: np.ndarray, i: int, n: int) -> float:
        left = vals[(i - 1) % n]
        center = vals[i % n]
        right = vals[(i + 1) % n]
        denom = left - 2.0 * center + right
        if abs(denom) < 1e-12:
            return 0.0
        offset = 0.5 * (left - right) / denom
        return float(np.clip(offset, -1.0, 1.0))

    dx_sub = _parabola_subpixel(r[peak_idx[0], :], peak_idx[1], w)
    dy_sub = _parabola_subpixel(r[:, peak_idx[1]], peak_idx[0], h)

    return dx + dx_sub, dy + dy_sub, confidence


def _fit_similarity(
    positions: list[tuple[float, float]],
    shifts: list[tuple[float, float]],
    confidences: list[float],
) -> tuple[float, float, float, float, float, float]:
    """Weighted least-squares fit of (tx, ty, log_scale, roll) to the
    block shift field. Returns (tx, ty, log_scale, roll, resid, mean_peak).

    Model (small-motion linearization of a similarity transform between
    consecutive frames):
        dx_i = tx + log_scale * px_i - roll * py_i
        dy_i = ty + roll      * px_i + log_scale * py_i
    """
    n = len(positions)
    weights = np.array(
        [c if c >= BLOCK_CONFIDENCE_FLOOR else 0.0 for c in confidences],
        dtype=np.float64,
    )
    if weights.sum() <= 0:
        weights = np.ones(n, dtype=np.float64)  # degenerate frame: fit unweighted anyway

    px = np.array([p[0] for p in positions], dtype=np.float64)
    py = np.array([p[1] for p in positions], dtype=np.float64)
    dx = np.array([s[0] for s in shifts], dtype=np.float64)
    dy = np.array([s[1] for s in shifts], dtype=np.float64)

    # Stack the two equations per block into one linear system.
    # Row order: [dx_0..dx_{n-1}, dy_0..dy_{n-1}]
    zeros = np.zeros(n)
    ones = np.ones(n)
    a_dx = np.column_stack([ones, zeros, px, -py])
    a_dy = np.column_stack([zeros, ones, py, px])
    A = np.vstack([a_dx, a_dy])  # shape (2n, 4)
    b = np.concatenate([dx, dy])
    w = np.concatenate([weights, weights])
    sqrt_w = np.sqrt(w)
    Aw = A * sqrt_w[:, None]
    bw = b * sqrt_w

    solution, *_ = np.linalg.lstsq(Aw, bw, rcond=None)
    tx, ty, log_scale, roll = (float(v) for v in solution)

    predicted = A @ solution
    residuals = b - predicted
    if w.sum() > 0:
        resid = float(np.sqrt(np.average(residuals ** 2, weights=w)))
    else:
        resid = float(np.sqrt(np.mean(residuals ** 2)))

    mean_peak = float(np.mean(confidences)) if confidences else 0.0
    return tx, ty, log_scale, roll, resid, mean_peak


def _motion_speed_px(tx: np.ndarray, ty: np.ndarray, roll: np.ndarray) -> np.ndarray:
    """Combined translation+rotation speed, in analysis-plane px/frame, used
    only for hf_energy (see module docstring's "Global motion" section).
    """
    return np.sqrt(tx ** 2 + ty ** 2) + np.abs(roll) * ROLL_TO_PX_RADIUS


def _moving_average(x: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or len(x) == 0:
        return x.copy()
    kernel = np.ones(window, dtype=np.float64) / window
    # 'same'-mode convolution with edge padding so the output stays the same
    # length without a systematic phase shift near the boundaries.
    padded = np.pad(x, (window // 2, window - 1 - window // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def _hf_energy(speed: np.ndarray) -> np.ndarray:
    """Windowed high-frequency energy of a scalar speed signal: subtract a
    low-pass (moving-average) trend, then take a rolling RMS of the
    high-pass residual. window sizes are module constants pending slice 4's
    fit (design §1.3's ~3 Hz band, approximated here by a moving-average
    cutoff at fps/HF_HIGHPASS_WINDOW_FRAMES).
    """
    if len(speed) == 0:
        return speed.copy()
    trend = _moving_average(speed, HF_HIGHPASS_WINDOW_FRAMES)
    highpass = speed - trend
    energy = _moving_average(highpass ** 2, HF_ENERGY_WINDOW_FRAMES)
    return np.sqrt(np.maximum(energy, 0.0))


# ---------------------------------------------------------------------------
# Sharpness (Laplacian variance) and exposure
# ---------------------------------------------------------------------------

def _laplacian_variance(frame: np.ndarray) -> float:
    f = frame.astype(np.float32)
    lap = (
        4.0 * f[1:-1, 1:-1]
        - f[:-2, 1:-1] - f[2:, 1:-1]
        - f[1:-1, :-2] - f[1:-1, 2:]
    )
    return float(lap.var())


def _exposure_stats(frame: np.ndarray) -> tuple[float, float, float, float, np.ndarray]:
    f = frame.astype(np.float32)
    mean = float(f.mean())
    std = float(f.std())
    total = f.size
    clip_low = float(np.count_nonzero(frame < CLIP_LOW_THRESHOLD * 255.0)) / total
    clip_high = float(np.count_nonzero(frame > CLIP_HIGH_THRESHOLD * 255.0)) / total
    hist, _ = np.histogram(frame, bins=HIST_BINS, range=(0, 256))
    return mean, std, clip_low, clip_high, hist.astype(np.int16)


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

def _audio_decode_cmd(ffmpeg: str, source_path: Path) -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-threads", "1",
        "-i", str(source_path),
        "-vn", "-ac", "1", "-ar", str(AUDIO_SAMPLE_RATE),
        "-f", "f32le",
        "pipe:1",
    ]


def _extract_audio_signals(source_path: Path, ffmpeg: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (peak_dbfs, rms_dbfs, clip_run) arrays, one value per 20ms
    window, from the ORIGINAL audio track (never a proxy's re-encode)."""
    cmd = _audio_decode_cmd(ffmpeg, source_path)
    result = subprocess.run(cmd, capture_output=True, timeout=600)
    if result.returncode != 0:
        raise SignalsError(
            f"ffmpeg audio decode exited {result.returncode}: "
            f"{result.stderr.decode('utf-8', 'replace').strip()}"
        )
    samples = np.frombuffer(result.stdout, dtype=np.float32)
    if samples.size == 0:
        return (np.zeros(0, dtype=np.float32),) * 3  # type: ignore[return-value]

    n_windows = int(math.ceil(samples.size / AUDIO_WINDOW_SAMPLES))
    pad = n_windows * AUDIO_WINDOW_SAMPLES - samples.size
    if pad:
        samples = np.pad(samples, (0, pad), mode="constant")
    windows = samples.reshape(n_windows, AUDIO_WINDOW_SAMPLES)

    eps = 1e-9
    peak = np.max(np.abs(windows), axis=1)
    peak_dbfs = 20.0 * np.log10(np.maximum(peak, eps))
    rms = np.sqrt(np.mean(windows ** 2, axis=1))
    rms_dbfs = 20.0 * np.log10(np.maximum(rms, eps))

    clipped_mask = np.abs(windows) >= AUDIO_CLIP_THRESHOLD
    clip_run = np.zeros(n_windows, dtype=np.float32)
    for i in range(n_windows):
        row = clipped_mask[i]
        if not row.any():
            continue
        # longest run of consecutive True values in this window
        changes = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
        starts = np.flatnonzero(changes == 1)
        ends = np.flatnonzero(changes == -1)
        clip_run[i] = float(np.max(ends - starts)) if len(starts) else 0.0

    return peak_dbfs.astype(np.float32), rms_dbfs.astype(np.float32), clip_run


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

@dataclass
class SignalsResult:
    source_path: Path
    npz_path: Path
    json_path: Path
    decode_mode: str
    analysed_frames: int
    expected_frames: int
    duration_sec: float
    fps: float
    width: int
    height: int
    has_audio: bool
    sha256: str
    timings: dict = field(default_factory=dict)

    @property
    def wall_sec(self) -> float:
        return sum(self.timings.values())

    @property
    def realtime_factor(self) -> float:
        wall = self.wall_sec
        return (self.duration_sec / wall) if wall > 0 else 0.0


def _validate_inputs(source_path: Path, out_dir: Path, decode: str) -> list[str]:
    problems: list[str] = []
    if decode not in ("auto", "videotoolbox", "software"):
        problems.append(f"decode must be one of auto/videotoolbox/software, got {decode!r}")
    if not source_path.exists():
        problems.append(f"source file does not exist: {source_path}")
    elif not source_path.is_file():
        problems.append(f"source path is not a file: {source_path}")
    if _ffmpeg_path() is None:
        problems.append("ffmpeg not found on PATH or common install locations")
    if _ffprobe_path() is None:
        problems.append("ffprobe not found on PATH or common install locations")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        problems.append(f"could not create out_dir {out_dir}: {e}")
    return problems


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def extract_signals(
    source_path: Path | str,
    out_dir: Path | str,
    *,
    decode: str = "auto",
) -> SignalsResult:
    """Extract the full §1 signal set from ``source_path`` and write the
    signals sidecar (npz + JSON header) into ``out_dir``.

    Args:
        source_path: the ORIGINAL media file to analyse. Never a proxy.
        out_dir: directory to write ``<source name>.signals.npz`` and
            ``.signals.json`` into (created if missing).
        decode: ``"auto"`` (hardware if available, else software),
            ``"videotoolbox"`` (hardware, falling back to software on
            failure), or ``"software"`` (never attempts hardware decode).

    Returns:
        A :class:`SignalsResult` describing what was written and timed.

    Raises:
        SignalsValidationError: bad inputs, every problem listed.
        SignalsError: ffmpeg/ffprobe failed even after any fallback.
    """
    source_path = Path(source_path)
    out_dir = Path(out_dir)

    problems = _validate_inputs(source_path, out_dir, decode)
    if problems:
        raise SignalsValidationError(problems)

    timings: dict[str, float] = {}

    t0 = time.monotonic()
    probe = _probe_source(source_path)
    timings["probe"] = time.monotonic() - t0

    t0 = time.monotonic()
    frames, decode_mode = _decode_all_frames(source_path, decode)
    timings["decode_and_analyse_video"] = time.monotonic() - t0
    # (video decode and per-frame signal computation happen in the same
    # pass below; the timing split reflects that "decode_and_analyse_video"
    # is decoder-bound per the design's own measurement — see design §7.)

    positions = [
        (x + BLOCK_SIZE / 2.0 - ANALYSIS_WIDTH / 2.0, y + BLOCK_SIZE / 2.0 - ANALYSIS_HEIGHT / 2.0)
        for (y, x) in _block_centers()
    ]

    n = len(frames)
    tx = np.zeros(n, dtype=np.float64)
    ty = np.zeros(n, dtype=np.float64)
    log_scale = np.zeros(n, dtype=np.float64)
    roll = np.zeros(n, dtype=np.float64)
    resid = np.zeros(n, dtype=np.float64)
    peak = np.zeros(n, dtype=np.float64)
    lapvar = np.zeros(n, dtype=np.float64)
    luma_mean = np.zeros(n, dtype=np.float64)
    luma_std = np.zeros(n, dtype=np.float64)
    clip_low = np.zeros(n, dtype=np.float64)
    clip_high = np.zeros(n, dtype=np.float64)
    hist_rows: list[np.ndarray] = []
    hist_frame_idx: list[int] = []

    t0 = time.monotonic()
    prev_frame = None
    for i, frame in frames:
        lapvar[i], = (_laplacian_variance(frame),)
        luma_mean[i], luma_std[i], clip_low[i], clip_high[i], hist = _exposure_stats(frame)
        if i % HIST_DECIMATION == 0:
            hist_rows.append(hist)
            hist_frame_idx.append(i)

        if prev_frame is None:
            tx[i] = ty[i] = log_scale[i] = roll[i] = resid[i] = 0.0
            peak[i] = 0.0
        else:
            shifts = []
            confidences = []
            for (by, bx) in _block_centers():
                prev_block = prev_frame[by:by + BLOCK_SIZE, bx:bx + BLOCK_SIZE]
                cur_block = frame[by:by + BLOCK_SIZE, bx:bx + BLOCK_SIZE]
                dx, dy, conf = _phase_correlate(prev_block, cur_block)
                shifts.append((dx, dy))
                confidences.append(conf)
            tx[i], ty[i], log_scale[i], roll[i], resid[i], peak[i] = _fit_similarity(
                positions, shifts, confidences
            )
        prev_frame = frame
    timings["per_frame_signals"] = time.monotonic() - t0

    t0 = time.monotonic()
    p90 = float(np.percentile(lapvar, 90)) if n else 0.0
    lapvar_norm = lapvar / p90 if p90 > 1e-9 else np.zeros_like(lapvar)
    speed = _motion_speed_px(tx, ty, roll)
    hf_energy = _hf_energy(speed)
    src_width = probe.width or ANALYSIS_WIDTH
    scale_to_src = src_width / ANALYSIS_WIDTH
    tx_norm = tx * scale_to_src
    ty_norm = ty * scale_to_src
    timings["derived_signals"] = time.monotonic() - t0

    has_audio = probe.has_audio
    audio_peak_dbfs = audio_rms_dbfs = audio_clip_run = None
    if has_audio:
        t0 = time.monotonic()
        ffmpeg = _ffmpeg_path()
        assert ffmpeg is not None
        audio_peak_dbfs, audio_rms_dbfs, audio_clip_run = _extract_audio_signals(source_path, ffmpeg)
        timings["audio"] = time.monotonic() - t0

    arrays: dict[str, np.ndarray] = {
        "tx": tx.astype(np.float32),
        "ty": ty.astype(np.float32),
        "tx_norm_src_width": tx_norm.astype(np.float32),
        "ty_norm_src_width": ty_norm.astype(np.float32),
        "log_scale": log_scale.astype(np.float32),
        "roll": roll.astype(np.float32),
        "resid": resid.astype(np.float32),
        "peak": peak.astype(np.float32),
        "hf_energy": hf_energy.astype(np.float32),
        "lapvar": lapvar.astype(np.float32),
        "lapvar_norm": lapvar_norm.astype(np.float32),
        "luma_mean": luma_mean.astype(np.float32),
        "luma_std": luma_std.astype(np.float32),
        "clip_low": clip_low.astype(np.float32),
        "clip_high": clip_high.astype(np.float32),
        "hist64": (np.stack(hist_rows) if hist_rows else np.zeros((0, HIST_BINS))).astype(np.int16),
        "hist64_frame_index": np.array(hist_frame_idx, dtype=np.int32),
    }
    if has_audio:
        arrays["audio_peak_dbfs"] = audio_peak_dbfs
        arrays["audio_rms_dbfs"] = audio_rms_dbfs
        arrays["audio_clip_run"] = audio_clip_run

    src_sha256 = sha256_file(source_path)

    npz_path = out_dir / f"{source_path.name}.signals.npz"
    json_path = out_dir / f"{source_path.name}.signals.json"

    # Serialize the npz to bytes first (via a BytesIO buffer) so
    # determinism holds regardless of what tempfile name np.savez_compressed
    # would otherwise embed — it embeds none, but this also lets us hash the
    # exact bytes we are about to write for the JSON header/tests.
    import io
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    npz_bytes = buf.getvalue()
    _atomic_write_bytes(npz_path, npz_bytes)

    header = {
        "generator": {
            "name": "posthouse.cull.signals",
            "version": SIGNALS_VERSION,
            "ffmpeg_version": _ffmpeg_version(),
            "numpy_version": np.__version__,
        },
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {
            "path": str(source_path),
            "sha256": src_sha256,
            "duration_sec": probe.duration_sec,
            "fps": probe.fps,
            "width": probe.width,
            "height": probe.height,
            "nb_frames": probe.nb_frames,
        },
        "analysis": {
            "plane_width": ANALYSIS_WIDTH,
            "plane_height": ANALYSIS_HEIGHT,
            "plane_format": "gray",
            "decode": decode_mode,
            "source_grade": "original",
            "analysed_frames": n,
            "audio_sr": AUDIO_SAMPLE_RATE if has_audio else None,
        },
        "audio": (
            {"present": True, "window_sec": AUDIO_WINDOW_SEC, "n_windows": int(audio_peak_dbfs.size)}
            if has_audio else
            {"present": False, "note": "no audio stream"}
        ),
        "npz_sha256": hashlib.sha256(npz_bytes).hexdigest(),
        "columns": {
            "tx": "px/frame, analysis plane (960 wide), similarity-fit translation x",
            "ty": "px/frame, analysis plane (960 wide), similarity-fit translation y",
            "tx_norm_src_width": "px/frame, normalized to the source's native width",
            "ty_norm_src_width": "px/frame, normalized to the source's native width",
            "log_scale": "per-frame log-scale term of the similarity fit (push/pull)",
            "roll": "per-frame rotation term of the similarity fit, radians/frame",
            "resid": "RMS residual of the similarity fit across the 9 blocks, px",
            "peak": "mean block phase-correlation confidence for the frame",
            "hf_energy": "windowed high-frequency energy of combined motion speed",
            "lapvar": "raw Laplacian variance, whole analysis frame",
            "lapvar_norm": "lapvar divided by the clip's own 90th percentile",
            "luma_mean": "mean luma, 0-255 analysis plane",
            "luma_std": "luma standard deviation, 0-255 analysis plane",
            "clip_low": "fraction of pixels below 16/255",
            "clip_high": "fraction of pixels above 239/255",
            "hist64": "decimated 64-bin luma histogram, one row per 15th frame",
            "hist64_frame_index": "frame index each hist64 row corresponds to",
            "audio_peak_dbfs": "per-20ms-window sample peak, dBFS (present iff audio.present)",
            "audio_rms_dbfs": "per-20ms-window RMS, dBFS (present iff audio.present)",
            "audio_clip_run": "per-20ms-window longest consecutive clipped-sample run, samples",
        },
        "note": (
            "Slice 1 output: signals only, no motion classification and no "
            "segments. state/RLE sequence fields are written by later slices."
        ),
    }
    header_bytes = (json.dumps(header, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_write_bytes(json_path, header_bytes)

    return SignalsResult(
        source_path=source_path,
        npz_path=npz_path,
        json_path=json_path,
        decode_mode=decode_mode,
        analysed_frames=n,
        expected_frames=round(probe.duration_sec * probe.fps) if probe.fps else n,
        duration_sec=probe.duration_sec,
        fps=probe.fps,
        width=probe.width,
        height=probe.height,
        has_audio=has_audio,
        sha256=src_sha256,
        timings=timings,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m posthouse.cull.signals",
        description="Extract the Phase 4 cull's deterministic signals from a source clip.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser("extract", help="Extract signals from one source file.")
    extract_parser.add_argument("source", type=Path, help="Path to the ORIGINAL source media file.")
    extract_parser.add_argument("--out", type=Path, required=True, help="Output directory for the sidecar.")
    extract_parser.add_argument(
        "--decode", choices=("auto", "videotoolbox", "software"), default="auto",
        help="Decode mode (default: auto).",
    )

    args = parser.parse_args(argv)

    if args.command == "extract":
        try:
            result = extract_signals(args.source, args.out, decode=args.decode)
        except SignalsValidationError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        except SignalsError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        except Exception as e:  # pragma: no cover - defensive: never hang, never crash bare
            print(f"error: unexpected failure extracting signals: {type(e).__name__}: {e}", file=sys.stderr)
            return 1

        print(f"source: {result.source_path}")
        print(f"decode: {result.decode_mode}")
        print(f"analysed_frames: {result.analysed_frames} (expected ~{result.expected_frames})")
        print(f"has_audio: {result.has_audio}")
        for stage, sec in result.timings.items():
            print(f"  {stage}: {sec:.2f}s")
        print(f"wall: {result.wall_sec:.2f}s  realtime_factor: {result.realtime_factor:.2f}x")
        print(f"wrote: {result.npz_path}")
        print(f"wrote: {result.json_path}")
        return 0

    return 1  # pragma: no cover - argparse enforces a valid subcommand


if __name__ == "__main__":
    sys.exit(_main())
