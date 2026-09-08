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
emitted as rawvideo on a pipe and consumed frame by frame in numpy —
genuinely one frame at a time: decode and per-frame analysis run in the
same streaming pass, and at most the current and previous decoded frames
are ever alive together (code review, 2026-09-01: an earlier version
called ``list(...)`` over the frame generator before analysis, which
held every decoded frame in memory at once — 9.3 GB for a 10-minute clip,
~30 GB for the 33-minute Runnells clip. Fixed by folding decode and
analysis into one generator-driven loop with growable preallocated output
arrays; see ``_SignalArrays``). No intermediate file is ever written.
VideoToolbox hardware decode (``-hwaccel videotoolbox``) is used when
available (detected once per process and cached); a hardware-decode
failure — including a codec VideoToolbox silently cannot accelerate, see
below — falls back to software decode with a logged note on stderr — it
never crashes the run, and a failed hardware attempt's partial output is
discarded, never accumulated alongside the software retry. Decode mode,
ffmpeg version, and the source's own fps/duration/frame count are
recorded in the sidecar header.

**Making a silent software fallback inside ffmpeg itself hard, not just
possible** (code review, 2026-09-01): ``-hwaccel videotoolbox`` alone
exits 0 and still emits frames for codecs VideoToolbox cannot decode in
hardware (ProRes, for one) — ffmpeg quietly falls back to its own
software decoder underneath, so the sidecar would record
``hwaccel_videotoolbox`` for a run that used no hardware at all. Passing
``-hwaccel_output_format videotoolbox_vld`` and downloading the decoded
frame explicitly (``hwdownload,format=<fmt>,scale=...,format=gray``,
``<fmt>`` chosen from the probed pixel format — ``p010le`` for a 10-bit
source, ``nv12`` otherwise, since ``hwdownload`` needs the exact native
hardware pixel format and there is no single one that covers both bit
depths) makes an unsupported codec fail loudly (measured: ffmpeg 8.1
exits 234 on a ProRes source with zero frames written) instead of falling
back invisibly, so ``_decode_and_analyze``'s existing "zero frames or an
exception" fallback rule now actually triggers for these codecs and the
recorded decode mode is trustworthy.

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

**Sign convention (fixed by code review, 2026-09-01):** ``(dx, dy)`` is
the shift of the CURRENT frame relative to the PREVIOUS one — a content
shift to the right or down between frames must yield positive ``dx``/
``dy``. An earlier version's cross-power spectrum was built as
``fa * conj(fb)`` (``fa`` = previous frame's spectrum, ``fb`` = current),
which is the textbook formula for "shift needed to move ``b`` onto
``a``" — i.e. exactly the negative of what this module documents and
what ``tx``/``ty``/``log_scale``/``roll`` are built from. Fixed by
building the cross-power spectrum the other way, ``fb * conj(fa)``; see
``test_phase_correlate_sign_convention_matches_docstring`` for the
``np.roll``-based proof and the corresponding ``log_scale``/``roll``
sign tests.

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
int32 — code review, 2026-09-01: int16 wraps once a single frame's
518,400-pixel count concentrates into one bin above 32,767, e.g. an
all-black frame's bin 0; int32 has no such ceiling for any plane size
this module uses), all from the same gray analysis plane.

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
{"present": false, "note": "no audio stream"}`` marker in the sidecar
header and no audio arrays in the npz — never a crash, never a
silently-empty array pretending to be real measurements. A source that
DOES carry an audio stream but decodes to zero samples (code review,
2026-09-01: a packetless audio track is real and reproducible, not
hypothetical) gets the same treatment under its own distinct note
(``"audio stream present but decoded to zero samples"``) rather than the
previous behaviour of writing empty ``audio_*`` arrays and claiming
``n_windows: 0`` — ``present`` is decided AFTER decode, from
``samples.size > 0``, never from the ffprobe stream check alone, and
nothing here ever calls ``np.max``/``np.mean`` on an empty array. Speech
presence is explicitly NOT part of this slice (design §1.6, §5 slice 5 —
it reuses ``posthouse.harvest.transcribe``).

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
``<out_dir>/<source_name>.<sha12>.signals.npz`` — one float32 array per
video signal, ``analysed_frames`` long (``tx``, ``ty``,
``tx_norm_src_width``, ``ty_norm_src_width``, ``log_scale``, ``roll``,
``resid``, ``peak``, ``hf_energy``, ``lapvar``, ``lapvar_norm``,
``luma_mean``, ``luma_std``, ``clip_low``, ``clip_high``), plus a
decimated ``hist64`` (int32, one row per 15th frame) and, when the
source has an audio stream that decodes to at least one sample,
``audio_peak_dbfs`` / ``audio_rms_dbfs`` / ``audio_clip_run`` at their
own 20ms rate. ``<sha12>`` is the first 12 hex characters of the
source's own sha256 (code review, 2026-09-01: a bare
``<source_name>.signals.npz`` collides across directories that share a
basename — an SD-card rollover from a DJI Osmo produces
``100MEDIA/DJI_0006.MP4`` and ``101MEDIA/DJI_0006.MP4``, two different
files, and writing both into one flat ``out_dir`` would silently
overwrite one sidecar with the other via ``os.replace``. ``sidecar_paths()``
is the one place this naming rule lives; slice 3 calls it to find a
source's sidecar rather than reconstructing the pattern itself). This
slice does not write a ``state`` array or a run-length-encoded state
sequence — those belong to the classifier (slice 2) and segmenter
(slice 3), which do not exist yet; writing a placeholder state array
here would misrepresent unclassified frames as classified.
``<out_dir>/<source_name>.<sha12>.signals.json`` carries provenance
(ffmpeg/numpy versions, decode mode, plane size, source
fps/duration/frame count, the source's sha256, a run timestamp), the
column dictionary with units, and the audio presence
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
from pathlib import Path
from typing import Optional

import numpy as np

from posthouse._util import atomic_write_bytes, now_iso

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
    pix_fmt: str = ""


def _hwdownload_format_for(pix_fmt: str) -> str:
    """The exact pixel format to hand ``hwdownload`` for a hardware-decoded
    frame of this source (code review, 2026-09-01). ``hwdownload`` needs the
    frame's real native format, not a negotiable target — passing the wrong
    one, or a ``fmt1|fmt2`` alternative list, fails the filter graph rather
    than picking the working option (measured on ffmpeg 8.1). VideoToolbox
    downloads an 8-bit 4:2:0 source as ``nv12`` and a 10-bit one as
    ``p010le``; every source this pipeline sees is one or the other. A
    10-bit source's ffprobe ``pix_fmt`` always carries a ``10`` marker
    (``yuv420p10le``, ``p010le``, ...); an 8-bit one never does.
    """
    return "p010le" if "10" in pix_fmt else "nv12"


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
        pix_fmt=str(video.get("pix_fmt") or ""),
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

def _video_decode_cmd(
    ffmpeg: str, source_path: Path, hwaccel: bool, hwdownload_format: str = "nv12",
) -> list[str]:
    # -nostdin: ffmpeg must never wait on a terminal for input; a batch cull
    # has no one at the keyboard.
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    if hwaccel:
        # -hwaccel_output_format + an explicit hwdownload (rather than plain
        # -hwaccel videotoolbox alone) is required to make an unsupported
        # codec fail loudly instead of ffmpeg silently falling back to
        # software underneath while still exiting 0 (code review,
        # 2026-09-01; see the module docstring's "Decode" section).
        cmd += ["-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld"]
    cmd += ["-threads", "1", "-i", str(source_path)]
    if hwaccel:
        vf = (
            f"hwdownload,format={hwdownload_format},"
            f"scale={ANALYSIS_WIDTH}:{ANALYSIS_HEIGHT},format=gray"
        )
    else:
        vf = f"scale={ANALYSIS_WIDTH}:{ANALYSIS_HEIGHT},format=gray"
    cmd += ["-vf", vf, "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
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


# _decode_all_frames (materialize-then-analyze) is gone — see
# _decode_and_analyze below, which streams decode and analysis together in
# one pass (code review, 2026-09-01 memory finding).


# ---------------------------------------------------------------------------
# Global motion: block-wise phase correlation + similarity fit
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _hann_window(size: int) -> np.ndarray:
    w1d = np.hanning(size).astype(np.float32)
    if w1d.sum() == 0:  # np.hanning(1) is [0.]; guard degenerate sizes
        w1d = np.ones(size, dtype=np.float32)
    return np.outer(w1d, w1d)


@functools.lru_cache(maxsize=1)
def _block_centers() -> tuple[tuple[int, int], ...]:
    """3x3 grid of block top-left (y, x) positions within the analysis
    plane, evenly spaced so each BLOCK_SIZE block stays fully in-bounds.
    Overlapping blocks are fine (there is no tiling requirement) — the
    plane is shorter than GRID*BLOCK_SIZE in height, so the vertical
    positions overlap while the horizontal ones do not. Cached (code
    review, 2026-09-01 perf finding): the grid depends only on module
    constants, so recomputing it every frame was pure waste.
    """
    def _positions(extent: int) -> list[int]:
        max_start = extent - BLOCK_SIZE
        if max_start <= 0:
            return [0] * GRID
        return [round(i * max_start / (GRID - 1)) for i in range(GRID)]

    ys = _positions(ANALYSIS_HEIGHT)
    xs = _positions(ANALYSIS_WIDTH)
    return tuple((y, x) for y in ys for x in xs)


def _block_spectrum(block: np.ndarray) -> np.ndarray:
    """Hann-windowed real FFT (``rfft2``) of one block — the shared
    building block of ``_phase_correlate`` and the per-frame streaming loop.

    ``rfft2`` (half-spectrum, real input) rather than ``fft2`` (code review,
    2026-09-01 perf finding): the input is always real, ``rfft2``/``irfft2``
    is a bit-exact match for the ``fft2``/``ifft2`` cross-power-spectrum
    peak (verified numerically, max abs difference ~1e-16) at roughly half
    the wall time, and it composes with caching a frame's block spectra to
    reuse as the *previous* frame's spectra on the next iteration — the
    same block was being FFT'd twice per pair before this (once as "cur"
    for frame i, again as "prev" for frame i+1).
    """
    window = _hann_window(block.shape[0])
    return np.fft.rfft2(block.astype(np.float32) * window)


def _correlate_spectra(
    fa: np.ndarray, fb: np.ndarray, block_shape: tuple[int, int],
) -> tuple[float, float, float]:
    """Sub-pixel (dx, dy, confidence): the shift of the frame whose
    (already Hann-windowed, already FFT'd) spectrum is ``fb`` relative to
    the frame whose spectrum is ``fa`` — i.e. ``fa`` is the PREVIOUS
    frame's block spectrum and ``fb`` is the CURRENT one, per this
    module's documented convention (module docstring, "Global motion").

    Sign convention (fixed by code review, 2026-09-01): the cross-power
    spectrum is built as ``fb * conj(fa)``, not ``fa * conj(fb)``. The
    latter is the textbook formula for "the shift that would move ``b``
    onto ``a``," which is exactly the negative of "the shift of ``b``
    relative to ``a``" that this function (and tx/ty/log_scale/roll, which
    are fit from its output) documents and requires — a rightward or
    downward content shift between frames must yield positive dx/dy. See
    ``test_phase_correlate_sign_convention_matches_docstring``.
    """
    cross = fb * np.conj(fa)
    mag = np.abs(cross)
    mag[mag < 1e-8] = 1e-8
    r = np.fft.irfft2(cross / mag, s=block_shape)

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


def _phase_correlate(prev_block: np.ndarray, cur_block: np.ndarray) -> tuple[float, float, float]:
    """Sub-pixel (dx, dy, confidence) shift of cur_block relative to
    prev_block, via FFT phase correlation on Hann-windowed blocks. A thin
    convenience wrapper over ``_block_spectrum``/``_correlate_spectra`` for
    callers (and tests) that have raw blocks rather than cached spectra;
    the streaming per-frame loop calls the two halves directly so each
    block's spectrum is computed exactly once per frame, not once per pair.
    """
    fa = _block_spectrum(prev_block)
    fb = _block_spectrum(cur_block)
    return _correlate_spectra(fa, fb, prev_block.shape)


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

    Sign convention (pinned by test, code review 2026-09-01): a push-in
    (increasing magnification) moves content OUTWARD from the frame
    center between frames, the same sign as each block's own position, so
    ``log_scale`` is POSITIVE for a zoom-in — see
    ``test_fit_similarity_log_scale_sign_convention_zoom_in_is_positive``.
    ``roll``'s sign is pinned relative to this same model by
    ``test_fit_similarity_roll_sign_convention_is_self_consistent``, which
    is what matters for internal consistency (a sign flip here would
    invert every roll-derived signal without any test noticing).
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
    # w.sum() > 0 always holds here (weights is forced to all-ones above
    # when its sum would otherwise be <= 0), so the unweighted branch this
    # used to have was dead code (code review, 2026-09-01) — removed.
    resid = float(np.sqrt(np.average(residuals ** 2, weights=w)))

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
    # int32, not int16 (code review, 2026-09-01): a single bin can hold up
    # to ANALYSIS_WIDTH*ANALYSIS_HEIGHT == 518,400 pixels (e.g. an
    # all-black frame's bin 0), which overflows int16's 32,767 ceiling and
    # wraps to a negative count. int32's ceiling is ~2.1 billion — no
    # analysis-plane size this module uses can reach it.
    return mean, std, clip_low, clip_high, hist.astype(np.int32)


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

def _audio_decode_cmd(ffmpeg: str, source_path: Path) -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-threads", "1",
        "-i", str(source_path),
        "-vn", "-ac", "1", "-ar", str(AUDIO_SAMPLE_RATE),
        "-f", "f32le",
        "pipe:1",
    ]


# Samples read per streaming chunk, in whole windows (code review, 2026-09-01
# perf finding): the original implementation slurped the entire decoded PCM
# stream into one array before computing anything — ~691 MB/hour of mono
# float32 at 48kHz. Reading in bounded chunks and reducing each chunk to its
# (tiny) per-20ms-window stats immediately means the only thing that grows
# with clip length is the ~50-windows/sec output, not the raw audio.
_AUDIO_CHUNK_WINDOWS = 4096
_AUDIO_CHUNK_BYTES = _AUDIO_CHUNK_WINDOWS * AUDIO_WINDOW_SAMPLES * 4  # float32


def _windowed_audio_stats(windows: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized per-window (peak_dbfs, rms_dbfs, clip_run) for a
    ``(n_windows, AUDIO_WINDOW_SAMPLES)`` block of samples (code review,
    2026-09-01 perf finding: the original clip-run computation was a
    Python ``for`` loop over ``n_windows``, which scales with clip length;
    this is a cumulative-run trick over the fixed
    ``AUDIO_WINDOW_SAMPLES`` (960) columns instead, so cost is independent
    of how many windows are passed in — verified numerically equivalent to
    the original per-window ``np.diff``-of-run-boundaries method).
    """
    eps = 1e-9
    peak = np.max(np.abs(windows), axis=1)
    peak_dbfs = 20.0 * np.log10(np.maximum(peak, eps))
    rms = np.sqrt(np.mean(windows ** 2, axis=1))
    rms_dbfs = 20.0 * np.log10(np.maximum(rms, eps))

    clipped = (np.abs(windows) >= AUDIO_CLIP_THRESHOLD).astype(np.int32)
    run_lengths = np.zeros_like(clipped)
    run_lengths[:, 0] = clipped[:, 0]
    for j in range(1, clipped.shape[1]):
        run_lengths[:, j] = (run_lengths[:, j - 1] + 1) * clipped[:, j]
    clip_run = run_lengths.max(axis=1).astype(np.float32)

    return peak_dbfs.astype(np.float32), rms_dbfs.astype(np.float32), clip_run


def _extract_audio_signals(
    source_path: Path, ffmpeg: str,
) -> Optional[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Streams the ORIGINAL audio track (never a proxy's re-encode) through
    ffmpeg in bounded chunks and returns (peak_dbfs, rms_dbfs, clip_run)
    arrays, one value per 20ms window — or ``None`` if the stream decoded
    to zero samples (code review, 2026-09-01: a stream that ffprobe reports
    as present but that carries no packets, or a `-t 0` / degenerate mux,
    is real and reproducible, not hypothetical — see
    ``test_audio_stream_present_with_zero_samples_is_not_marked_present``).
    Callers decide ``present`` from this return value, never from the
    ffprobe stream check alone.
    """
    cmd = _audio_decode_cmd(ffmpeg, source_path)
    # stderr to a temp file, same reasoning as the video decode pipe: a
    # long/noisy audio decode must never risk a full-pipe deadlock against
    # our own stdout-draining loop.
    with tempfile.TemporaryFile(prefix="posthouse-ffmpeg-audio-stderr-") as errf:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf)
        assert proc.stdout is not None
        leftover = b""
        peak_chunks: list[np.ndarray] = []
        rms_chunks: list[np.ndarray] = []
        clip_chunks: list[np.ndarray] = []
        completed = False
        try:
            while True:
                raw = proc.stdout.read(_AUDIO_CHUNK_BYTES)
                if not raw:
                    break
                leftover += raw
                usable_samples = (len(leftover) // 4 // AUDIO_WINDOW_SAMPLES) * AUDIO_WINDOW_SAMPLES
                usable_bytes = usable_samples * 4
                if usable_bytes == 0:
                    continue
                block = np.frombuffer(leftover[:usable_bytes], dtype=np.float32)
                leftover = leftover[usable_bytes:]
                windows = block.reshape(-1, AUDIO_WINDOW_SAMPLES)
                p, r, c = _windowed_audio_stats(windows)
                peak_chunks.append(p)
                rms_chunks.append(r)
                clip_chunks.append(c)
            completed = True
        finally:
            if not completed:
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
                        f"ffmpeg audio decode exited {returncode}: {tail}"
                    )

    # Final partial window (fewer than AUDIO_WINDOW_SAMPLES samples left
    # over): zero-pad it, exactly as the original whole-buffer implementation
    # padded its last window.
    if leftover:
        pad = AUDIO_WINDOW_SAMPLES * 4 - len(leftover)
        block = np.frombuffer(leftover + b"\x00" * pad, dtype=np.float32)
        windows = block.reshape(1, AUDIO_WINDOW_SAMPLES)
        p, r, c = _windowed_audio_stats(windows)
        peak_chunks.append(p)
        rms_chunks.append(r)
        clip_chunks.append(c)

    if not peak_chunks:
        return None  # decoded to zero samples — present iff samples.size > 0

    return (
        np.concatenate(peak_chunks),
        np.concatenate(rms_chunks),
        np.concatenate(clip_chunks),
    )


# ---------------------------------------------------------------------------
# Streaming decode + per-frame analysis: one pass, bounded memory
# ---------------------------------------------------------------------------
#
# Code review, 2026-09-01 (CRITICAL): an earlier version called
# ``list(_iter_gray_frames(cmd))`` before any analysis ran, materializing
# every decoded 518,400-byte frame at once — ~9.3 GB for a 10-minute clip,
# ~30 GB for the 33-minute Runnells clip (OOM). ``_SignalArrays`` below
# preallocates (and grows) only the per-frame SCALAR outputs, and
# ``_run_decode_and_analyze`` consumes the frame generator directly,
# computing each frame's signals as it arrives and never retaining a
# decoded frame past the iteration that produced it — motion needs no
# retained raw frame at all, only the previous frame's (much smaller)
# cached block spectra (see ``_block_spectrum``).

class _SignalArrays:
    """Preallocated, growable storage for one decode pass's per-frame
    scalar signals. Starts at the probe's own frame-count estimate and
    doubles on overflow (a probe under-estimate, e.g. a missing
    ``nb_frames`` tag, must not crash a multi-hour decode) — the pattern
    mirrors a growable list, but keeps the payload in typed numpy arrays
    rather than boxed Python floats.
    """

    _FIELDS = (
        "tx", "ty", "log_scale", "roll", "resid", "peak",
        "lapvar", "luma_mean", "luma_std", "clip_low", "clip_high",
    )

    def __init__(self, capacity: int):
        capacity = max(int(capacity), 1)
        self._capacity = capacity
        self.count = 0
        for field_name in self._FIELDS:
            setattr(self, field_name, np.zeros(capacity, dtype=np.float64))
        self.hist_rows: list[np.ndarray] = []
        self.hist_frame_idx: list[int] = []

    def ensure(self, index: int) -> None:
        if index < self._capacity:
            return
        new_capacity = max(index + 1, self._capacity * 2)
        for field_name in self._FIELDS:
            old = getattr(self, field_name)
            grown = np.zeros(new_capacity, dtype=np.float64)
            grown[: old.size] = old
            setattr(self, field_name, grown)
        self._capacity = new_capacity

    def trim(self) -> dict[str, np.ndarray]:
        """The final per-signal arrays, sliced to the frames actually
        decoded (``count``), never the preallocated capacity."""
        return {field_name: getattr(self, field_name)[: self.count] for field_name in self._FIELDS}


def _run_decode_and_analyze(
    cmd: list[str], positions: list[tuple[float, float]], capacity_hint: int,
) -> tuple[_SignalArrays, int]:
    """Run one decode pass and compute every per-frame signal in the same
    streaming loop. Returns (arrays, frame_count); raises SignalsError if
    ffmpeg exits non-zero (the caller decides whether that means "fall
    back to software" or "genuinely failed").
    """
    arrs = _SignalArrays(capacity_hint)
    block_positions = _block_centers()
    prev_block_spectra: Optional[list[np.ndarray]] = None
    count = 0

    for i, frame in _iter_gray_frames(cmd):
        arrs.ensure(i)

        arrs.lapvar[i] = _laplacian_variance(frame)
        mean, std, clip_low, clip_high, hist = _exposure_stats(frame)
        arrs.luma_mean[i] = mean
        arrs.luma_std[i] = std
        arrs.clip_low[i] = clip_low
        arrs.clip_high[i] = clip_high
        if i % HIST_DECIMATION == 0:
            arrs.hist_rows.append(hist)
            arrs.hist_frame_idx.append(i)

        cur_block_spectra = [
            _block_spectrum(frame[by:by + BLOCK_SIZE, bx:bx + BLOCK_SIZE])
            for (by, bx) in block_positions
        ]

        if prev_block_spectra is None:
            arrs.tx[i] = arrs.ty[i] = arrs.log_scale[i] = arrs.roll[i] = arrs.resid[i] = 0.0
            arrs.peak[i] = 0.0
        else:
            shifts = []
            confidences = []
            for fa, fb in zip(prev_block_spectra, cur_block_spectra):
                dx, dy, conf = _correlate_spectra(fa, fb, (BLOCK_SIZE, BLOCK_SIZE))
                shifts.append((dx, dy))
                confidences.append(conf)
            (arrs.tx[i], arrs.ty[i], arrs.log_scale[i], arrs.roll[i],
             arrs.resid[i], arrs.peak[i]) = _fit_similarity(positions, shifts, confidences)

        # `frame` and the previous iteration's `frame`/`prev_block_spectra`
        # are the only per-frame data ever alive; `frame` itself is dropped
        # here and only its (much smaller) block spectra survive to the
        # next iteration.
        prev_block_spectra = cur_block_spectra
        count = i + 1
        arrs.count = count

    return arrs, count


def _decode_and_analyze(
    source_path: Path, decode: str, probe: ProbeInfo, positions: list[tuple[float, float]],
) -> tuple[_SignalArrays, str]:
    """Decode every frame and compute its per-frame signals in one
    streaming pass, with the auto/forced hwaccel-then-software fallback
    (design §1.1: "never crash"). A failed or empty hardware attempt's
    ``_SignalArrays`` is abandoned (never merged with, or read alongside,
    the software retry's arrays) before decoding restarts from frame 0 in
    software — so a partial hardware pass never doubles memory use either.
    Returns (arrays, decode_mode_used).
    """
    ffmpeg = _ffmpeg_path()
    if ffmpeg is None:
        raise SignalsError("ffmpeg not found on PATH or common install locations")

    capacity_hint = probe.nb_frames or max(1, round(probe.duration_sec * probe.fps)) or 64

    want_hw = decode in ("auto", "videotoolbox")
    if want_hw and decode == "auto":
        want_hw = _videotoolbox_available()

    if want_hw:
        hwdownload_format = _hwdownload_format_for(probe.pix_fmt)
        cmd = _video_decode_cmd(ffmpeg, source_path, hwaccel=True, hwdownload_format=hwdownload_format)
        try:
            arrs, count = _run_decode_and_analyze(cmd, positions, capacity_hint)
            if count > 0:
                return arrs, "hwaccel_videotoolbox"
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

    cmd = _video_decode_cmd(ffmpeg, source_path, hwaccel=False)
    arrs, count = _run_decode_and_analyze(cmd, positions, capacity_hint)
    return arrs, "software"


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


def sidecar_paths(
    source_path: Path, out_dir: Path, sha256: Optional[str] = None,
) -> tuple[Path, Path]:
    """The (npz_path, json_path) sidecar pair for ``source_path`` in
    ``out_dir`` — the one place this naming rule lives, so slice 3 (and
    anything else that needs to find an already-written sidecar) calls
    this instead of reconstructing the pattern.

    Includes the first 12 hex characters of the source's sha256 (code
    review, 2026-09-01): a bare ``<name>.signals.npz`` collides across
    directories that share a basename — an SD-card rollover from a DJI
    Osmo produces ``100MEDIA/DJI_0006.MP4`` and ``101MEDIA/DJI_0006.MP4``,
    two different files that would silently overwrite one sidecar with the
    other via ``os.replace`` if both landed in one flat ``out_dir``.

    Args:
        sha256: pass the already-computed hash to skip re-hashing a
            potentially large source file; computed here if omitted.
    """
    if sha256 is None:
        sha256 = sha256_file(source_path)
    sha12 = sha256[:12]
    npz_path = out_dir / f"{source_path.name}.{sha12}.signals.npz"
    json_path = out_dir / f"{source_path.name}.{sha12}.signals.json"
    return npz_path, json_path


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
        out_dir: directory to write ``<source name>.<sha12>.signals.npz``
            and ``.signals.json`` into (created if missing; see
            ``sidecar_paths()``).
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

    positions = [
        (x + BLOCK_SIZE / 2.0 - ANALYSIS_WIDTH / 2.0, y + BLOCK_SIZE / 2.0 - ANALYSIS_HEIGHT / 2.0)
        for (y, x) in _block_centers()
    ]

    t0 = time.monotonic()
    arrs, decode_mode = _decode_and_analyze(source_path, decode, probe, positions)
    timings["decode_and_analyse_video"] = time.monotonic() - t0
    # This key genuinely covers both decode and per-frame analysis (code
    # review, 2026-09-01): they now run in one streaming pass, not the
    # separate decode-then-analyse stages an earlier version measured
    # separately while mislabelling only the decode stage as "and_analyse".

    n = arrs.count
    signals = arrs.trim()
    tx = signals["tx"]
    ty = signals["ty"]
    log_scale = signals["log_scale"]
    roll = signals["roll"]
    resid = signals["resid"]
    peak = signals["peak"]
    lapvar = signals["lapvar"]
    luma_mean = signals["luma_mean"]
    luma_std = signals["luma_std"]
    clip_low = signals["clip_low"]
    clip_high = signals["clip_high"]
    hist_rows = arrs.hist_rows
    hist_frame_idx = arrs.hist_frame_idx

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

    has_audio_stream = probe.has_audio
    audio_result: Optional[tuple[np.ndarray, np.ndarray, np.ndarray]] = None
    if has_audio_stream:
        t0 = time.monotonic()
        ffmpeg = _ffmpeg_path()
        assert ffmpeg is not None
        audio_result = _extract_audio_signals(source_path, ffmpeg)
        timings["audio"] = time.monotonic() - t0
    # `present` is decided AFTER decode, from whether any samples actually
    # came out (code review, 2026-09-01) — a stream ffprobe reports as
    # present but that decodes to zero samples (a packetless audio track,
    # or a degenerate mux) is NOT "present" here, and gets its own header
    # note rather than being conflated with "no audio stream at all."
    has_audio = audio_result is not None
    if has_audio:
        audio_peak_dbfs, audio_rms_dbfs, audio_clip_run = audio_result

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
        "hist64": (np.stack(hist_rows) if hist_rows else np.zeros((0, HIST_BINS))).astype(np.int32),
        "hist64_frame_index": np.array(hist_frame_idx, dtype=np.int32),
    }
    if has_audio:
        arrays["audio_peak_dbfs"] = audio_peak_dbfs
        arrays["audio_rms_dbfs"] = audio_rms_dbfs
        arrays["audio_clip_run"] = audio_clip_run

    src_sha256 = sha256_file(source_path)
    npz_path, json_path = sidecar_paths(source_path, out_dir, sha256=src_sha256)

    # Serialize the npz to bytes first (via a BytesIO buffer) so
    # determinism holds regardless of what tempfile name np.savez_compressed
    # would otherwise embed — it embeds none, but this also lets us hash the
    # exact bytes we are about to write for the JSON header/tests.
    import io
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    npz_bytes = buf.getvalue()
    atomic_write_bytes(npz_path, npz_bytes)

    header = {
        "generator": {
            "name": "posthouse.cull.signals",
            "version": SIGNALS_VERSION,
            "ffmpeg_version": _ffmpeg_version(),
            "numpy_version": np.__version__,
        },
        "created_at": now_iso(),
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
            {"present": False, "note": "audio stream present but decoded to zero samples"}
            if has_audio_stream else
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
    atomic_write_bytes(json_path, header_bytes)

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
