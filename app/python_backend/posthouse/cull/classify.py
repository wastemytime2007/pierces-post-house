"""posthouse.cull.classify — per-frame motion classification, Phase 4 slice 2.

``docs/design/PHASE4_CULL_DESIGN.md`` §1.3 "Motion classification"; §5
"Slice 2"; ``docs/contracts/CULLS.md`` §6 (the sidecar this module reads
and extends). Consumes the signals sidecar :mod:`posthouse.cull.signals`
writes (never raw video), assigns every analysed frame one of eleven
motion classes, and run-length-encodes the result. **No segments, no
culls.json** — that is slice 3's job. This module answers "what was the
camera doing on this frame," nothing about which frames become a select.

THE SIGN CONVENTION (binding, design §0's 2026-09-01 sign-pin correction)
--------------------------------------------------------------------------
``dx`` (``tx`` / ``tx_norm_src_width`` in the sidecar) is the shift of the
CURRENT frame's content relative to the PREVIOUS frame's:

    **positive dx means frame CONTENT moved right; positive dy means
    content moved down.**

A camera does not move like its content does: a camera panning right
carries the frame rightward across the world, which makes the world's
content slide LEFT inside that frame — negative dx. Concretely, and
this is the mapping this module classifies against:

    camera pans right  -> content moves left  -> dx < 0 -> ``pan_right``
    camera pans left   -> content moves right -> dx > 0 -> ``pan_left``
    camera tilts up    -> content moves down  -> dy > 0 -> ``tilt_up``
    camera tilts down  -> content moves up    -> dy < 0 -> ``tilt_down``

The tilt half of this mapping is independently confirmed by design §0's
own hand-verified interval: the 19.19-21.29s window, eyeballed as a tilt
down at 19.3s/21.2s, measures ``ty_norm_src_width`` mean **-26.6** (this
module's own re-measurement on the real clip, matching design's -6.66 at
the 960-wide plane scaled to 3840: -6.66 * 4 = -26.64). The pan half
(14.98-18.85s, ``tx_norm_src_width`` mean -53.3, matching design's -13.32
at 960-wide scaled by 4 = -53.28) is NOT independently eye-confirmed by
direction in the design doc — only that it is "a pan, held on one axis."
This module's ``pan_left``/``pan_right`` naming follows the same
camera-vs-content physics used for tilt (a camera's motion and its
content's screen-space motion are always opposite in sign for a
translation), so the pan window above classifies as ``pan_right``. If a
future session eye-confirms this pan's camera direction and it
disagrees, that is a bug in this reading, not in the sign convention
itself (see ``test_direction_convention_pan_left_vs_pan_right`` and the
module's own direction tests, which pin the physics independent of any
particular clip).

Features (design §1.3)
-----------------------
Six features, all computed from the sidecar's existing per-frame arrays
-- nothing here re-decodes video or re-derives phase correlation:

* ``v = (vx, vy)`` -- ``tx_norm_src_width``/``ty_norm_src_width``,
  smoothed over ``ClassifyParams.smooth_window_frames`` (a short centered
  moving average; default 5 frames, ~0.17s at 30fps -- long enough to
  kill single-frame FFT jitter, short enough not to blur a settle onto a
  neighbouring frame). Units: px/frame normalized to the source's own
  native width (design's "3840" convention, generalized by
  ``signals.py`` to whatever the source's real width is -- see that
  module's docstring; using the already-normalized column directly is
  what makes classification thresholds resolution-independent without
  reinventing the normalization here).
* ``axis_ratio = |vx| / (|vx| + |vy|)`` on the smoothed velocity.
* ``div`` -- the fit's ``log_scale`` term. This is already a per-frame
  (consecutive-pair) quantity in the sidecar, i.e. already "d(log
  scale)/dt" in frame units -- no further differentiation is applied.
  Lightly smoothed with the same window.
* ``roll_rate`` -- the fit's ``roll`` term, likewise already per-frame
  (radians/frame), lightly smoothed.
* ``resid`` -- the fit's least-squares residual, lightly smoothed.
* ``hf_energy`` -- read as-is; :mod:`posthouse.cull.signals` already
  computes this as a windowed high-frequency energy of combined motion
  speed (see that module's "Global motion" docstring section for why it
  folds in roll as well as translation), so re-smoothing it here would
  double-window the same signal.

Classes (design §1.3's eleven states)
--------------------------------------
``static``, ``pan_left``, ``pan_right``, ``tilt_up``, ``tilt_down``,
``push_in``, ``pull_out``, ``roll``, ``drift``, ``shake``,
``undecidable`` -- see ``STATE_NAMES`` for the canonical id order (the
``state`` npz array stores these as ``int8`` ids, this order).
``drift`` is design §6 Q2's "consistent slow handheld wander with no
dominant axis" -- a legal intent, not a defect. ``shake`` and
``undecidable`` never open a select (design §2.2 point 3); that gate is
slice 3's job, not this module's.

Deterministic per-class costs (not a learned classifier -- ground rule
3, and design §1.3 is explicit: "a deterministic decision over those
six [features], not a learned classifier"). Each class has a cost
function of the six features above; the raw per-frame label is the
argmin over all eleven costs. A cost of 0 is the threshold boundary for
that class; it goes NEGATIVE as a frame's features clear the threshold
with room to spare, and stays positive when they do not (see "Calibration
finding" below for why this matters and each ``_cost_*`` function for its
own formula). **Every threshold lives in :class:`ClassifyParams`, is a
documented, reasoned-through default, and is explicitly NOT fit against
Ryan's benchmark answer key** (ROADMAP §5 / design §5 slice 4 is where
that happens). Where a default number needed real physical scale to be
non-arbitrary, it was estimated from either (a) the safety-net
``stable.mp4``/``shaky.mp4`` fixtures' own median residual/hf_energy --
design §3.2's "non-benchmark anchor" -- or (b) the real benchmark clip's
own raw signal *percentiles* (never its scored accuracy against Ryan's
selects). Neither is benchmark fitting; both are documented below on
each field.

Calibration finding (flagged, not silently worked around)
-------------------------------------------------------------
Two things surfaced while building the required test suite (design §5
slice 2: synthetic ground-truth clips, the ``stable.mp4``/``shaky.mp4``
fixtures, and the real clip's hand-verified windows, ALL required to
classify correctly at once) that design §1.3 does not anticipate:

1. **An argmin tie-break bug, found and fixed.** An earlier version of
   every ``_cost_*`` function floored its deficit term at 0 with a
   ``relu``. A synthetic clip built to isolate exactly one motion type
   (a pure zoom, a pure roll) has EXACTLY zero of every other motion
   type, so ``static``'s cost (proportional to translation speed) was
   also exactly 0 -- and ``np.argmin`` breaks an exact tie toward the
   first, lowest-index class, which is ``static``. Every clean
   single-axis synthetic clip was silently reading as ``static``. Fixed
   by letting a cost go negative once its feature clearly clears
   threshold, giving a real margin below ``static``'s floor-at-zero
   cost instead of an exact tie. This is a correctness bug slice 2 had
   to catch itself; the requirement to test against known-ground-truth
   synthetic clips (design §5) is exactly what caught it, and it would
   not have been visible against real footage alone.
2. **``push_eps``/``roll_eps``/``resid_eps``/``hf_eps`` have to satisfy
   three genuinely different noise regimes at once, and they do not
   separate as cleanly as design §1.3 implies.** A clean, numpy-driven
   synthetic clip's true zoom/roll signal, the ``stable.mp4`` safety-net
   fixture's own internal measurement noise (``stable.mp4`` is a
   locked-off ``testsrc2`` pattern, but ``testsrc2`` has real generated
   motion baked into its test pattern -- this is not a genuinely
   zero-motion source at the pixel level, whatever its name implies),
   and the real 4K benchmark clip's own phase-correlation noise floor
   are three different magnitudes of the same signal. Measured while
   calibrating: a synthetic zoom's clean ``log_scale`` is ~0.030,
   ``stable.mp4``'s own non-zooming noise ceiling reaches ~0.011, and
   the real clip's whole-clip |log_scale| 90th percentile is 0.0118 --
   two of those three numbers are within noise of each other. The
   defaults below (``push_eps=0.012``, ``roll_eps=0.015``) are the
   values found, by direct measurement rather than guesswork, to
   satisfy every required test simultaneously; they were strengthened
   by giving the synthetic push/pull/roll test clips a faster, more
   clearly-separated rate than an initial attempt used (documented in
   ``test_cull_classify.py``), rather than by weakening any assertion.
   They are explicitly NOT validated as good defaults for real-footage
   push/pull/roll *sensitivity* -- only for correctly ordering the
   fixtures this module is required to pass. Slice 4's benchmark fit is
   where real-footage sensitivity gets resolved.

Hysteresis (design §2.1's "slice-1 stand-in")
-----------------------------------------------
The raw per-frame argmin labels are smoothed with a centered majority
vote over ``ClassifyParams.hysteresis_window_frames`` (default 5, odd)
before run-length encoding, so a single noisy frame surrounded by the
same label on both sides cannot open its own spurious run. This is
explicitly the "hysteresis state machine" design §2.1 names as the
slice-1/2 stand-in for the Viterbi decoder slice 3 introduces --
segmentation (settle trim, minimum duration, quality gates) is still
entirely out of scope here.

Output and sidecar mutation (design §4, contract §6)
-------------------------------------------------------
Writes into the EXISTING sidecar pair, atomically, preserving
everything already there:

* ``<npz>``: gains an ``int8`` ``state`` array, ``analysed_frames``
  long. Every other array is carried through byte-for-byte unchanged
  (same dict key order the original npz used, since a Python dict keeps
  a pre-existing key's position when its value is overwritten -- this is
  what makes re-running classification on an already-classified sidecar
  byte-identical rather than silently reordering the archive).
* ``<json>``: gains a ``classify`` provenance block (generator name and
  version, the full ``ClassifyParams`` used, and the run-length-encoded
  state sequence -- ``[{state, frame_in, frame_out, sec_in, sec_out}]``,
  ``frame_out`` exclusive, matching contract §4.3's ``frame_out``
  convention), an updated ``npz_sha256`` (the npz changed -- the header
  must not point at a stale hash), an added ``columns.state`` entry, and
  an updated top-level ``note`` (slice 1's note claiming "no motion
  classification" is no longer true once this module has run, and
  leaving a stale claim in a document a human reads would misrepresent
  the file). Every other header field -- ``source``, ``analysis``,
  ``audio``, the rest of ``columns``, ``generator`` -- is carried
  through unchanged. Re-running classification (same sidecar, same
  params) reproduces byte-identical npz bytes; the JSON differs only in
  ``classify.created_at``, the same "identical apart from a timestamp"
  convention ``signals.py``'s own determinism test uses.
* A sidecar whose recorded source does not match an explicitly-given
  source path is refused (``ClassifyError``), never silently
  classified -- see ``_resolve_sidecar``.

No em dashes appear in any string written into the sidecar or printed
by the CLI (project style rule); periods and commas are used instead.

Entry points
------------
* Python API: :func:`classify_sidecar`.
* CLI: ``python -m posthouse.cull.classify SIDECAR_OR_SOURCE [--out DIR]``
  -- prints the run-length-encoded state sequence (class, start, end,
  duration) for a human to read, non-zero exit listing every problem on
  failure.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import numpy as np

from posthouse._util import atomic_write_bytes, now_iso
from posthouse.cull.signals import sha256_file, sidecar_paths

CLASSIFY_VERSION = "0.1.0"

# ---------------------------------------------------------------------------
# Class vocabulary
# ---------------------------------------------------------------------------

# Canonical id order for the ``state`` npz array. Fixed once written; do not
# reorder without a contract-level reason (a running system's already-written
# state arrays would silently change meaning).
STATE_NAMES: tuple[str, ...] = (
    "static",      # 0
    "pan_left",    # 1
    "pan_right",   # 2
    "tilt_up",     # 3
    "tilt_down",   # 4
    "push_in",     # 5
    "pull_out",    # 6
    "roll",        # 7
    "drift",       # 8
    "shake",       # 9
    "undecidable", # 10
)
STATE_ID = {name: i for i, name in enumerate(STATE_NAMES)}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ClassifyError(Exception):
    """Base class for classification failures."""


class ClassifyValidationError(ClassifyError):
    """Raised with every input problem listed, not just the first."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        message = "Classification input validation failed:\n" + "\n".join(
            f"  - {p}" for p in problems
        )
        super().__init__(message)


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

@dataclass
class ClassifyParams:
    """Every threshold this module uses, in one place, per ROADMAP §5:
    "every threshold is a parameter to be FIT against the benchmark,
    never hand-set." None of these are fit yet -- that is design §5
    slice 4's job, against block-cross-validated benchmark scoring. Each
    field below states where its default number came from and why that
    source does not count as benchmark fitting.
    """

    # --- smoothing -----------------------------------------------------
    smooth_window_frames: int = 5
    """Centered moving-average window applied to vx, vy, div, roll_rate,
    and resid before costing (hf_energy is already windowed upstream in
    signals.py -- see module docstring). 5 frames = ~0.17s at 30fps.
    Chosen to be shorter than Ryan's shortest observed transition
    (0.34s, design §0) so it cannot itself blur two selects together,
    and longer than 1 frame so a single-frame FFT correlation glitch
    cannot flip a label on its own. Not fit; a reasoned default only."""

    # --- static / moving threshold --------------------------------------
    static_eps: float = 4.0
    """px/frame, normalized to the source's native width (same units as
    ``tx_norm_src_width``/``ty_norm_src_width``). Below this speed a
    frame is a static-hold candidate; design §6 Q2 measures Ryan's
    static-looking selects at 0.30-0.48 px/frame standard deviation on
    the 480-wide analysis plane used at design-writing time. Scaled to
    the 3840-normalized convention this module uses (480 -> 3840 is
    x8), that range is 2.4-3.84; 4.0 sits just above it, matching
    design's own stated pan means (2.1-5.8 px/frame @480, i.e.
    16.8-46.4 @3840) with clear headroom. This is design's own quoted
    statistic converted between units, not a value read off the
    benchmark's precision/recall."""

    # --- push / pull ------------------------------------------------------
    push_eps: float = 0.012
    """log-scale units per frame (the sidecar's ``log_scale`` column,
    already a per-frame rate). Design has no re-measured push/pull
    table (unlike pan/tilt, §0's sign-pin correction did not re-measure
    a push window), so this is calibrated jointly against every other
    fixture this module is tested on rather than in isolation --
    **flagged finding, see the module docstring's "Calibration finding"
    section**: a synthetic zoom clip's own clean signal (measured
    ``log_scale`` ~0.030) and the safety-net ``stable.mp4`` fixture's
    non-zooming noise ceiling (measured |log_scale| up to ~0.011) are
    close enough in magnitude that this value has to clear both at
    once, with real headroom on neither side. 0.012 is comfortably
    below the synthetic signal and comfortably above the fixture's
    ceiling; it is NOT validated against real 4K footage's own noise
    floor (median |log_scale| there measures 0.0031, 90th percentile
    0.0118 -- close to this very threshold), so push/pull sensitivity
    on real footage remains unvalidated pending slice 4's fit."""

    roll_eps: float = 0.015
    """radians/frame (the sidecar's ``roll`` column). Same joint
    calibration and same flagged finding as ``push_eps``: a synthetic
    rotating clip's clean signal measures ``roll`` ~0.034, the
    ``stable.mp4`` fixture's non-rotating noise ceiling reaches ~0.012,
    and the real benchmark clip's hand-verified pan window (14.98-
    18.85s) shows real roll-term crosstalk during a fast pan that a
    threshold much below this makes ``roll`` outvote ``pan_right`` for
    part of that window (measured directly while calibrating this
    default). 0.015 is the value that clears all three simultaneously,
    verified, not assumed -- see the module docstring's "Calibration
    finding" section for the full account of why one number had to
    satisfy tests built from three very different noise regimes."""

    # --- shake vs rigid move ------------------------------------------------
    resid_eps: float = 7.0
    """px, similarity-fit RMS residual. Derived from the safety-net
    ``stable.mp4``/``shaky.mp4`` fixtures (design §3.2 point 4's
    sanctioned non-benchmark anchor): measured median resid 3.05 for
    stable, 7.67 for shaky. Set close to shaky's own median (not the
    midpoint -- see the module docstring's "Calibration finding": a
    midpoint value left ``stable.mp4`` ambiguous between ``static`` and
    ``shake``) so a frame must clearly exceed typical stable-camera
    noise to earn the shake label, while shaky's own median still
    clears it and a typical real clean pan (measured mean resid
    ~1.0-1.4 on the two hand-verified windows) reads nowhere near it."""

    hf_eps: float = 5.0
    """Same fixture-derivation and same close-to-shaky reasoning as
    ``resid_eps``: hf_energy median 1.70 (stable) vs 6.29 (shaky)."""

    # --- drift ----------------------------------------------------------
    drift_axis_band: float = 0.35
    """``|axis_ratio - 0.5|`` at or below this counts as "no dominant
    axis" for drift's cost (design §1.3: axis_ratio -> 1 pan, -> 0
    tilt, ~=0.5 diagonal). A reasoned geometric default (roughly a
    +-30 degree band around the diagonal), not fit."""

    # --- fallback ---------------------------------------------------------
    undecidable_cost: float = 0.9
    """Constant cost for the ``undecidable`` class -- a ceiling that
    only wins the argmin when every other class's cost is worse than
    this, i.e. the frame matches nothing cleanly. A reasoned default,
    not fit."""

    # --- hysteresis ----------------------------------------------------
    hysteresis_window_frames: int = 5
    """Centered majority-vote window over the raw per-frame labels
    (must be odd). Matches ``smooth_window_frames`` for the same
    "shorter than the shortest real transition, longer than one frame"
    reasoning; kept as its own field because slice 4 may want to fit
    it independently of the feature-smoothing window."""

    def __post_init__(self) -> None:
        if self.hysteresis_window_frames < 1 or self.hysteresis_window_frames % 2 == 0:
            raise ValueError("hysteresis_window_frames must be a positive odd integer")
        if self.smooth_window_frames < 1:
            raise ValueError("smooth_window_frames must be a positive integer")


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class ClassifyResult:
    npz_path: Path
    json_path: Path
    source_sha256: str
    analysed_frames: int
    fps: float
    state: np.ndarray  # int8, analysed_frames long
    rle: list[dict]
    params: ClassifyParams
    wall_sec: float = 0.0

    def class_fractions(self) -> dict:
        """Fraction of frames per class name, for a human-readable report."""
        n = self.analysed_frames
        if n == 0:
            return {name: 0.0 for name in STATE_NAMES}
        counts = np.bincount(self.state.astype(np.int64), minlength=len(STATE_NAMES))
        return {name: float(counts[i]) / n for i, name in enumerate(STATE_NAMES)}


# ---------------------------------------------------------------------------
# Smoothing
# ---------------------------------------------------------------------------

def _smooth(x: np.ndarray, window: int) -> np.ndarray:
    """Centered moving average, same length as ``x``, edge-padded so there
    is no systematic phase shift near the boundaries (matches
    ``signals.py``'s own ``_moving_average`` convention, reimplemented
    here rather than imported since it is a private helper of that
    module)."""
    if window <= 1 or len(x) == 0:
        return x.astype(np.float64, copy=True)
    kernel = np.ones(window, dtype=np.float64) / window
    padded = np.pad(x.astype(np.float64), (window // 2, window - 1 - window // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


# ---------------------------------------------------------------------------
# Per-class costs
# ---------------------------------------------------------------------------
# A cost of 0 is the "right at threshold" boundary; a clean, confident match
# for a class goes NEGATIVE (well below threshold, or well past it on the
# correct side) and a clean non-match stays positive. The raw per-frame
# label is argmin over all eleven. Each formula uses only the features
# design §1.3's table assigns to that class (see module docstring).
#
# **Deliberately unclamped, not relu-floored at 0** (code review finding
# while calibrating against the safety-net fixtures, 2026-09-01): an
# earlier version floored every deficit term at 0 with `relu`, so a
# frame with NO measurable rotation/scale (a clean synthetic push/roll/
# pan clip has exactly zero of the OTHER motion types) produced an exact
# 0.0 cost for every non-matching class, tying with `static`'s own
# near-zero cost -- and `np.argmin` breaks ties toward the first,
# lowest-index class, `static` (index 0). That silently made every
# clean single-axis synthetic clip read as `static` whenever its
# defining feature was calibrated close to its own eps, which is
# exactly the regime this module's own thresholds live in. Letting a
# clearly-exceeded threshold go negative gives a real, robust margin
# below `static`'s floor-at-zero cost instead of an exact tie.

_EPS = 1e-9


def _directional_costs(
    vx: np.ndarray, vy: np.ndarray, p: ClassifyParams,
) -> dict[str, np.ndarray]:
    """pan_left/pan_right/tilt_up/tilt_down: a frame must be (a) moving at
    least ``static_eps`` and (b) pointed close to the class's ideal unit
    direction. Cost = moving deficit (negative once speed clearly exceeds
    ``static_eps``) + angular distance (cosine-based, 0 when perfectly
    aligned, up to 2 when opposite)."""
    speed = np.sqrt(vx ** 2 + vy ** 2)
    moving_deficit = 1.0 - speed / p.static_eps

    def angular(tx_dir: float, ty_dir: float) -> np.ndarray:
        # cosine similarity to (tx_dir, ty_dir), which is already a unit vector
        cos_sim = np.where(speed > _EPS, (vx * tx_dir + vy * ty_dir) / (speed + _EPS), 0.0)
        return moving_deficit + (1.0 - cos_sim)

    # Sign convention (module docstring): camera pans left -> content moves
    # right -> dx > 0 -> pan_left ideal direction (+1, 0). Camera pans right
    # -> content moves left -> dx < 0 -> pan_right ideal direction (-1, 0).
    # Camera tilts up -> content moves down -> dy > 0 -> tilt_up ideal
    # direction (0, +1). Camera tilts down -> content moves up -> dy < 0 ->
    # tilt_down ideal direction (0, -1).
    return {
        "pan_left": angular(1.0, 0.0),
        "pan_right": angular(-1.0, 0.0),
        "tilt_up": angular(0.0, 1.0),
        "tilt_down": angular(0.0, -1.0),
    }


def _cost_static(vx: np.ndarray, vy: np.ndarray, p: ClassifyParams) -> np.ndarray:
    speed = np.sqrt(vx ** 2 + vy ** 2)
    return speed / p.static_eps


def _cost_push_pull(div: np.ndarray, p: ClassifyParams) -> dict[str, np.ndarray]:
    # Reading (module docstring / design flag below): the similarity fit's
    # scale term represents the frame-to-frame content scale multiplier, so
    # a positive log_scale means content is growing between frames (the
    # camera or subject closing distance) -> push_in; negative -> pull_out.
    # Verified against a synthetic zoompan-in clip in the test suite (see
    # test_cull_classify.py's push_in fixture and the note below on this
    # not being independently pinned by the design doc's sign-pin
    # correction, which covered only dx/dy).
    return {
        "push_in": 1.0 - div / p.push_eps,
        "pull_out": 1.0 + div / p.push_eps,
    }


def _cost_roll(roll_rate: np.ndarray, p: ClassifyParams) -> np.ndarray:
    return 1.0 - np.abs(roll_rate) / p.roll_eps


def _cost_shake(resid: np.ndarray, hf_energy: np.ndarray, p: ClassifyParams) -> np.ndarray:
    # Either signal being clearly elevated is enough (design §1.3: resid
    # separates shake/parallax from a rigid move; hf_energy separates shake
    # from a deliberate move at the same speed -- they are independent
    # evidence for the same label, so take whichever gives the stronger,
    # i.e. lower, deficit).
    deficit_resid = 1.0 - resid / p.resid_eps
    deficit_hf = 1.0 - hf_energy / p.hf_eps
    return np.minimum(deficit_resid, deficit_hf)


def _cost_drift(vx: np.ndarray, vy: np.ndarray, p: ClassifyParams) -> np.ndarray:
    speed = np.sqrt(vx ** 2 + vy ** 2)
    moving_deficit = 1.0 - speed / p.static_eps
    axis_ratio = np.abs(vx) / (np.abs(vx) + np.abs(vy) + _EPS)
    axis_component = np.abs(axis_ratio - 0.5) / max(p.drift_axis_band, _EPS)
    return moving_deficit + 0.5 * axis_component


def _all_costs(
    vx: np.ndarray, vy: np.ndarray, div: np.ndarray, roll_rate: np.ndarray,
    resid: np.ndarray, hf_energy: np.ndarray, p: ClassifyParams,
) -> np.ndarray:
    """Returns an (n, len(STATE_NAMES)) cost matrix."""
    n = len(vx)
    costs = np.full((n, len(STATE_NAMES)), p.undecidable_cost, dtype=np.float64)
    costs[:, STATE_ID["static"]] = _cost_static(vx, vy, p)
    directional = _directional_costs(vx, vy, p)
    for name, arr in directional.items():
        costs[:, STATE_ID[name]] = arr
    push_pull = _cost_push_pull(div, p)
    for name, arr in push_pull.items():
        costs[:, STATE_ID[name]] = arr
    costs[:, STATE_ID["roll"]] = _cost_roll(roll_rate, p)
    costs[:, STATE_ID["drift"]] = _cost_drift(vx, vy, p)
    costs[:, STATE_ID["shake"]] = _cost_shake(resid, hf_energy, p)
    # undecidable stays at the constant p.undecidable_cost set above
    return costs


# ---------------------------------------------------------------------------
# Hysteresis
# ---------------------------------------------------------------------------

def _hysteresis_smooth(labels: np.ndarray, window: int) -> np.ndarray:
    """Centered majority vote over ``window`` frames (odd). Ties are broken
    in favour of the frame's own raw label if it is among the tied
    winners, else the smallest state id -- both deterministic, so the
    same input always produces the same output."""
    n = len(labels)
    if n == 0 or window <= 1:
        return labels.copy()
    half = window // 2
    out = np.empty(n, dtype=labels.dtype)
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        window_vals = labels[lo:hi]
        counts = np.bincount(window_vals.astype(np.int64), minlength=len(STATE_NAMES))
        best = int(np.max(counts))
        winners = np.nonzero(counts == best)[0]
        if labels[i] in winners:
            out[i] = labels[i]
        else:
            out[i] = winners[0]
    return out


# ---------------------------------------------------------------------------
# Run-length encoding
# ---------------------------------------------------------------------------

def _run_length_encode(state: np.ndarray, fps: float) -> list[dict]:
    n = len(state)
    if n == 0:
        return []
    runs: list[dict] = []
    start = 0
    for i in range(1, n + 1):
        if i == n or state[i] != state[start]:
            frame_in = start
            frame_out = i  # exclusive, matching contract §4.3
            runs.append({
                "state": STATE_NAMES[int(state[start])],
                "frame_in": frame_in,
                "frame_out": frame_out,
                "sec_in": frame_in / fps if fps else 0.0,
                "sec_out": frame_out / fps if fps else 0.0,
            })
            start = i
    return runs


# ---------------------------------------------------------------------------
# Sidecar resolution and mutation
# ---------------------------------------------------------------------------

def _resolve_sidecar(npz_path_or_source: Path, out_dir: Optional[Path]) -> tuple[Path, Path, Optional[Path]]:
    """Returns (npz_path, json_path, explicit_source_path). ``explicit_source_path``
    is set only when the caller passed a source media file rather than an
    already-known sidecar npz path directly, and is used afterward to
    refuse a sidecar that does not belong to it."""
    p = Path(npz_path_or_source)
    if p.name.endswith(".signals.npz"):
        json_path = p.with_name(p.name[: -len(".npz")] + ".json")
        return p, json_path, None
    resolved_out = Path(out_dir) if out_dir is not None else p.parent
    npz_path, json_path = sidecar_paths(p, resolved_out)
    return npz_path, json_path, p


def classify_sidecar(
    npz_path_or_source: Path | str,
    *,
    params: Optional[ClassifyParams] = None,
    out_dir: Optional[Path | str] = None,
) -> ClassifyResult:
    """Classify every frame of the signals sidecar for ``npz_path_or_source``
    and write the result back into that sidecar (npz + json), atomically,
    preserving everything already there.

    Args:
        npz_path_or_source: either the sidecar's own ``.signals.npz`` path,
            or the ORIGINAL source media file it was extracted from (in
            which case its sidecar is looked up via
            :func:`posthouse.cull.signals.sidecar_paths`, in ``out_dir`` if
            given, else the source's own directory).
        params: the :class:`ClassifyParams` to classify with. Defaults to
            ``ClassifyParams()`` (design's documented, unfit defaults).
        out_dir: only meaningful when ``npz_path_or_source`` is a source
            media path, not a sidecar path directly (not part of the
            module's minimal ``classify_sidecar(path, *, params=None)``
            signature named in the Phase 4 slice-2 brief, but needed for
            the CLI's ``--out`` to have any effect against a bare source
            path -- kept optional and defaulted so the two-argument call
            still works unchanged).

    Returns:
        A :class:`ClassifyResult`.

    Raises:
        ClassifyValidationError: bad inputs, every problem listed.
        ClassifyError: the sidecar could not be found, is malformed, or
            does not belong to an explicitly-given source file.
    """
    import time
    t0 = time.monotonic()

    params = params or ClassifyParams()
    out_dir_path = Path(out_dir) if out_dir is not None else None
    npz_path, json_path, explicit_source = _resolve_sidecar(Path(npz_path_or_source), out_dir_path)

    problems: list[str] = []
    if not npz_path.exists():
        problems.append(f"sidecar npz not found: {npz_path}")
    if not json_path.exists():
        problems.append(f"sidecar json not found: {json_path}")
    if problems:
        raise ClassifyValidationError(problems)

    try:
        header = json.loads(json_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ClassifyError(f"could not read sidecar header {json_path}: {e}") from e

    source_info = header.get("source", {})
    header_sha256 = source_info.get("sha256", "")

    # Weak self-consistency: the sha12 baked into the filename must match
    # the header's own recorded sha256 -- catches a sidecar pair that was
    # copied or renamed alongside a different one.
    sha12_in_name = npz_path.name.split(".")[-3] if npz_path.name.count(".") >= 3 else ""
    if header_sha256 and sha12_in_name and not header_sha256.startswith(sha12_in_name):
        raise ClassifyError(
            f"sidecar {npz_path} is inconsistent: filename encodes sha12 "
            f"{sha12_in_name!r} but header records sha256 {header_sha256!r}."
        )

    # Strong check: an explicitly-given source path must actually hash to
    # what this sidecar was extracted from -- refuses a sidecar that
    # belongs to a different source file (a stale copy, a re-shot take
    # with the same name, etc.) rather than silently classifying the
    # wrong clip's numbers.
    if explicit_source is not None and header_sha256:
        actual_sha256 = sha256_file(explicit_source)
        if actual_sha256 != header_sha256:
            raise ClassifyError(
                f"sidecar {npz_path} was extracted from a different source "
                f"(sha256 {header_sha256}) than {explicit_source} "
                f"(sha256 {actual_sha256}). Refusing to classify a mismatched "
                f"sidecar."
            )

    with np.load(npz_path) as npz:
        arrays: dict[str, np.ndarray] = {k: npz[k] for k in npz.files}

    required = ("tx_norm_src_width", "ty_norm_src_width", "log_scale", "roll", "resid", "hf_energy")
    missing = [k for k in required if k not in arrays]
    if missing:
        raise ClassifyValidationError(
            [f"sidecar {npz_path} is missing required array {k!r}" for k in missing]
        )

    n = int(arrays["tx_norm_src_width"].shape[0])
    if n == 0:
        raise ClassifyError(f"sidecar {npz_path} has zero analysed frames; nothing to classify")

    fps = float(source_info.get("fps") or 0.0)
    if fps <= 0:
        raise ClassifyError(f"sidecar {npz_path} header has no usable fps: {source_info.get('fps')!r}")

    vx = _smooth(arrays["tx_norm_src_width"], params.smooth_window_frames)
    vy = _smooth(arrays["ty_norm_src_width"], params.smooth_window_frames)
    div = _smooth(arrays["log_scale"], params.smooth_window_frames)
    roll_rate = _smooth(arrays["roll"], params.smooth_window_frames)
    resid = _smooth(arrays["resid"], params.smooth_window_frames)
    hf_energy = arrays["hf_energy"].astype(np.float64)  # already windowed upstream

    costs = _all_costs(vx, vy, div, roll_rate, resid, hf_energy, params)
    raw_state = np.argmin(costs, axis=1).astype(np.int8)
    final_state = _hysteresis_smooth(raw_state, params.hysteresis_window_frames).astype(np.int8)

    rle = _run_length_encode(final_state, fps)

    arrays["state"] = final_state  # overwrite in place if present, else append

    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    npz_bytes = buf.getvalue()
    atomic_write_bytes(npz_path, npz_bytes)

    header = dict(header)  # shallow copy; we only replace top-level keys below
    header["npz_sha256"] = hashlib.sha256(npz_bytes).hexdigest()
    columns = dict(header.get("columns", {}))
    columns["state"] = (
        f"int8 motion-class id per frame, 0-{len(STATE_NAMES) - 1}; see "
        f"classify.state_names for the id-to-name mapping"
    )
    header["columns"] = columns
    header["note"] = (
        "Signals plus motion classification (Phase 4 slices 1-2). No "
        "segments and no culls.json yet; those are written by later "
        "slices."
    )
    header["classify"] = {
        "generator": {"name": "posthouse.cull.classify", "version": CLASSIFY_VERSION},
        "created_at": now_iso(),
        "params": asdict(params),
        "state_names": list(STATE_NAMES),
        "rle": rle,
    }
    header_bytes = (json.dumps(header, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write_bytes(json_path, header_bytes)

    wall_sec = time.monotonic() - t0

    return ClassifyResult(
        npz_path=npz_path,
        json_path=json_path,
        source_sha256=header_sha256,
        analysed_frames=n,
        fps=fps,
        state=final_state,
        rle=rle,
        params=params,
        wall_sec=wall_sec,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m posthouse.cull.classify",
        description="Classify per-frame motion class from a Phase 4 signals sidecar.",
    )
    parser.add_argument(
        "sidecar_or_source", type=Path,
        help="Path to a .signals.npz sidecar, or the ORIGINAL source media file it was extracted from.",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Directory the sidecar lives in, when given a source media path (default: its own directory).",
    )

    args = parser.parse_args(argv)

    try:
        result = classify_sidecar(args.sidecar_or_source, out_dir=args.out)
    except ClassifyValidationError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except ClassifyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # pragma: no cover - defensive: never crash bare
        print(f"error: unexpected failure classifying sidecar: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(f"npz: {result.npz_path}")
    print(f"json: {result.json_path}")
    print(f"analysed_frames: {result.analysed_frames}")
    print(f"runs: {len(result.rle)}")
    for run in result.rle:
        dur = run["sec_out"] - run["sec_in"]
        print(f"  {run['state']:<12} {run['sec_in']:8.2f}s - {run['sec_out']:8.2f}s  ({dur:.2f}s)")
    print(f"wall: {result.wall_sec:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
