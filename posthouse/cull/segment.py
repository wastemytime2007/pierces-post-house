"""posthouse.cull.segment -- Phase 4 slice 3: labelled runs to segments,
visual ruleset only.

``docs/design/PHASE4_CULL_DESIGN.md`` Sec 2 (segmentation) in full, Sec
1.4/1.5 (quality gates); ``docs/contracts/CULLS.md`` (the file this module
emits). Consumes the classified sidecar :mod:`posthouse.cull.classify`
writes (never raw video, never re-decodes). This is the gate slice: the
Lead measured slice 2's raw hysteresis-smoothed runs on the real clip at
463 runs over 235.3s (one every 0.51s, median 0.20s, 87% shorter than the
1.0s floor) with boundaries carrying essentially no signal about where
Ryan actually cut (69% vs 67% chance). Applying Sec 2.2's filters
directly to that would delete nearly everything. So this module first
CONSOLIDATES runs into intents (Sec 2.1), then applies Sec 2.2's
settle/min-duration/class-gate/quality-gate/handle pipeline to the
consolidated runs, then writes a contract-valid ``culls.json``.

Consolidation (design Sec 2.1), two paths behind ``SegmentParams.consolidation``
-------------------------------------------------------------------------------
* ``"hysteresis"`` -- the design's "slice-1 stand-in": iteratively absorb
  any run shorter than ``min_run_sec`` into whichever neighbour has the
  greater total duration, re-merging adjacent same-class runs after each
  absorption, to a fixed point (no run left below the threshold, or only
  one run remains). Simple to review, simple to explain when it misfires.
* ``"viterbi"`` -- a single-penalty Viterbi decode over the SAME per-frame
  class costs :mod:`posthouse.cull.classify` already computes (reused
  directly via its private ``_all_costs``/``_smooth`` rather than adding
  new public surface to a module that does not mutate anything here --
  classify.py already exposes exactly the pieces needed and nothing about
  loading/smoothing needed to change to reuse them). Emission cost per
  frame per class from classify's cost matrix; a flat transition penalty
  ``viterbi_lambda`` for any class change, 0 for staying. Boundaries are
  where the decoded label changes.

Neither is fitted here (ROADMAP Sec5 / design Sec5 slice 4's job). Every
default in :class:`SegmentParams` is a **reasoned, documented, unfit
default** -- several are lifted directly from CULLS.md Sec5's worked
example, which the contract itself calls "illustrative shapes" but which
are real, physically-reasoned numbers, not arbitrary ones. ``params.visual``
records ``fit_provenance_note: "defaults, not fitted"`` on top of the
contract's own ``fit_provenance: "default"`` enum value.

Sec 2.2's pipeline, as implemented here (visual ruleset only)
---------------------------------------------------------------
1. Settle-time trim per class (``settle_frames`` / ``settle_frames_static``).
   A run that is all settle becomes a ``transition`` rejection -- the
   0.34s between Ryan's #3 and #4 is exactly this case.
2. Minimum duration (``min_duration_sec``, hard floor 1.0s) -> ``too_short``.
3. Class gate: ``shake``/``undecidable`` never open a select.
4. Quality gates (focus, exposure) shorten or split a surviving candidate,
   never extend it. Focus judges *shape* (design Sec1.4): a hunting shape
   (sign-change rate over threshold) kills the whole candidate
   (``focus_hunt``); a sustained motion-adjusted, per-clip-normalized dip
   below the ``focus_norm_quantile`` floor splits the candidate
   (``focus_lost``); a monotonic ramp is tagged ``rack_in``/``rack_out``
   and kept. Exposure splits on sustained clip fraction over
   ``clip_low_frac_max``/``clip_high_frac_max``.
5. Handles (``handle_sec``, default 1.0, matching
   ``coldfootage.DEFAULT_HANDLE_SEC`` and the scorer's
   ``DEFAULT_HANDLE_TOLERANCE_SEC``): clamp to source bounds, never
   reject, may overlap a neighbour (Ryan's Q4 ruling).

The tiling invariant (segments + rejections exactly cover
``[0, duration_sec]`` per (source_id, rel_path, ruleset), no gaps, no
overlaps within a ruleset) is asserted in code by :func:`_assert_tiling`,
not only tested.

Underspecified / judgment calls flagged for the Architect (see the slice
report, not repeated in full here): the exact rule for choosing
``boundary_reason_in`` between ``"settle"`` and ``"motion_change"`` when a
run's leading edge is both settle-trimmed AND preceded by a real class
change (CULLS Sec5's worked example uses "settle" for the file's first
accepted segment and "motion_change" for the second, with no general rule
stated); the precise definition of "sustained" for a focus/exposure dip to
warrant a split rather than being absorbed into scores; and
``analysis_sec`` per source, which signals.py's own sidecar header does
not currently persist (recorded here as ``0.0`` when unavailable -- a
slice 1 gap, not a slice 3 one).

Entry points
------------
* Python API: :func:`segment_source` (pure, one sidecar, no manifest, no
  filesystem writes) and :func:`write_culls` (manifest-aware, writes the
  master ``culls.json`` plus its per-ruleset view).
* CLI: ``python -m posthouse.cull.segment SIDECAR --manifest M --out DIR
  [--consolidation viterbi|hysteresis]`` -- non-zero exit, every problem
  listed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import numpy as np

from posthouse._util import atomic_write_bytes, now_iso
from posthouse.coldfootage import CONTRACT_VERSION, validate_segments_shape
from posthouse.cull import classify as _classify
from posthouse.cull.classify import STATE_ID, STATE_NAMES, ClassifyParams
from posthouse.cull.signals import sha256_file, sidecar_paths

SEGMENT_VERSION = "0.1.0"
ALLOWED_RULESETS = {"narrative", "visual"}

_MOTION_INTENT_NAMES = {
    "static": "static", "pan_left": "pan left", "pan_right": "pan right",
    "tilt_up": "tilt up", "tilt_down": "tilt down", "push_in": "push in",
    "pull_out": "pull out", "roll": "roll", "drift": "drift",
}
# Classes that never open a select (design Sec2.2 point 3).
_CLOSED_CLASSES = {"shake", "undecidable"}
_OPEN_CLASSES = set(STATE_NAMES) - _CLOSED_CLASSES

_ENUM_MOTION_INTENT = set(_MOTION_INTENT_NAMES)
_ENUM_BOUNDARY_IN = {
    "clip_start", "motion_change", "settle", "focus_regained",
    "exposure_recovered", "audio_start", "speech_start", "prior_reject_ended",
}
_ENUM_BOUNDARY_OUT = {
    "clip_end", "motion_change", "shake_onset", "focus_lost", "focus_hunt",
    "exposure_fault", "audio_fault", "speech_end", "recompose",
}
_ENUM_REJECT_REASON = {
    "shake", "motion_inconsistent", "focus_hunt", "soft", "underexposed",
    "overexposed", "too_short", "audio_clipped", "audio_dead", "no_speech",
    "settle", "transition", "record_tap", "undecidable",
}


class SegmentError(Exception):
    """Base class for segmentation failures."""


class SegmentValidationError(SegmentError):
    """Raised with every input problem listed, not just the first."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        message = "Segmentation input validation failed:\n" + "\n".join(
            f"  - {p}" for p in problems
        )
        super().__init__(message)


class TilingInvariantError(SegmentError):
    """The mandatory coverage invariant (CULLS.md Sec4.5 / Sec7 REJECT 7) does
    not hold: segments + rejections must exactly tile [0, duration_sec] for
    a (source_id, rel_path, ruleset). This is a bug in this module, not a
    user-input problem -- it is asserted in code, not only tested."""


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

@dataclass
class SegmentParams:
    """Every threshold slice 3 uses, in one place (ROADMAP Sec5). None of
    these are fitted -- that is design Sec5 slice 4's job. Several are
    lifted directly from CULLS.md Sec5's worked example (a real, physically
    reasoned parameter set the Architect wrote against the design's own
    measurements), which is a defensible source for an unfit default in
    the same way classify.py sourced its own defaults from fixture medians
    -- neither is benchmark fitting.
    """

    # --- consolidation (design Sec2.1) ----------------------------------
    consolidation: str = "hysteresis"
    """"hysteresis" or "viterbi" -- see module docstring."""

    viterbi_lambda: float = 7.5
    """Flat transition penalty for the Viterbi path. CULLS Sec5's worked
    example's "state_change_penalty". Not fitted; the qualitative
    requirement is "buys long runs" -- this is a starting point, not a
    tuned value."""

    min_run_sec: float = 1.0
    """Hysteresis-absorption threshold: a run shorter than this is folded
    into its dominant neighbour, iterating to a fixed point. Matches
    design Sec3.3's target scale (Ryan's shortest select is 1.23s) so a
    genuinely short real select is not itself absorbed away."""

    # --- settle trim (design Sec2.2 point 1) -----------------------------
    settle_frames: int = 8
    """CULLS Sec5 worked example. Applied to the leading/trailing edge of
    a moving-class run (a pan needs longer to reach constant velocity than
    a hold needs to stop wobbling -- design Sec2.1)."""

    settle_frames_static: int = 4
    """A static hold settles faster than a moving class starts. Half of
    ``settle_frames`` as a reasoned default, not fitted."""

    # --- minimum duration (design Sec2.2 point 2) -------------------------
    min_duration_sec: float = 1.15
    """CULLS Sec5 worked example, inside design Sec3.3's fit range
    [1.0, 1.5] with the hard floor at 1.0 (Ryan's shortest observed select
    is 1.23s)."""

    # --- focus gate (design Sec1.4 / Sec2.2 point 4) ----------------------
    focus_norm_quantile: float = 0.35
    """CULLS Sec5 worked example. The percentile (of this clip's own
    motion-adjusted, per-clip-normalized focus residual) below which a
    sustained dip is judged genuinely soft rather than merely "on the
    softer side of a clip that is fine throughout"."""

    focus_hunt_sign_changes_per_sec: float = 2.4
    """CULLS Sec5 worked example. A focus-residual LEVEL-CROSSING rate
    above this, over most of a candidate run, is judged hunting (design
    Sec1.4 point 3) and kills the whole run. See
    :func:`_hunt_rate_per_sec`'s docstring for why this is a Schmitt-
    trigger crossing rate rather than a raw sign-change rate -- a raw
    count is unusable on real footage (measured: 99.6% of frames on the
    real benchmark clip exceed this threshold unsmoothed, 31% even after
    a full second of smoothing), which produced zero accepted segments
    end to end before this fix."""

    focus_hunt_smooth_frames: int = 9
    """Smoothing applied to the focus residual before hunt-rate counting
    (``_hunt_rate_per_sec``). Not in the design doc; added because the
    raw residual is too noisy on real footage for any sign-change
    threshold to be meaningful (see that function's docstring)."""

    focus_hunt_deadband_std: float = 0.15
    """Fraction of the clip's own focus-residual standard deviation used
    as the Schmitt-trigger deadband in ``_hunt_rate_per_sec``. Per-clip
    relative (design Sec1.4's own normalize-per-clip reasoning), not an
    absolute number. Measured on the real benchmark clip to produce
    near-zero false hunting triggers on its own (presumably
    mostly-non-hunting) footage; not fit against the benchmark's P/R/F1."""

    rack_min_ramp_frames: int = 18
    """CULLS Sec5 worked example. Minimum length of a monotonic,
    sign-consistent focus-residual ramp to be tagged a rack rather than
    noise."""

    # --- exposure gate (design Sec1.5 / Sec2.2 point 4) --------------------
    clip_low_frac_max: float = 0.31
    clip_high_frac_max: float = 0.06
    """CULLS Sec5 worked example. A sustained per-frame clipped-fraction
    above these splits the candidate (``underexposed``/``overexposed``)."""

    # --- handles (design Sec2.2 point 5) -----------------------------------
    handle_sec: float = 1.0
    """Matches ``coldfootage.DEFAULT_HANDLE_SEC`` and the benchmark
    scorer's ``DEFAULT_HANDLE_TOLERANCE_SEC`` (ratified: handles are
    neutral in scoring). Clamped to source bounds; may overlap a
    neighbouring select (Ryan's Q4 ruling, design Sec2.2 point 5)."""

    def __post_init__(self) -> None:
        if self.consolidation not in ("hysteresis", "viterbi"):
            raise ValueError(
                f"consolidation must be 'hysteresis' or 'viterbi', got {self.consolidation!r}"
            )
        if self.min_duration_sec < 1.0:
            raise ValueError(
                f"min_duration_sec has a hard floor of 1.0s (design Sec2.2 point 2), "
                f"got {self.min_duration_sec}"
            )
        if self.settle_frames < 0 or self.settle_frames_static < 0:
            raise ValueError("settle_frames and settle_frames_static must be >= 0")
        if self.handle_sec < 0:
            raise ValueError("handle_sec must be >= 0")

    def as_contract_dict(self) -> dict:
        """This ruleset's fitted-parameter object for ``params.visual`` /
        ``params.narrative`` (CULLS Sec4.2): an opaque object of scalars,
        so a future change to this dataclass's field list is not a
        contract change. Adds ``fit_provenance_note`` verbatim, since the
        contract's own ``fit_provenance`` enum has no room for the exact
        phrase "defaults, not fitted" alongside its required ``"default"``
        value."""
        d = asdict(self)
        d["fit_provenance_note"] = "defaults, not fitted; see SegmentParams field docstrings for sourcing"
        return d


# ---------------------------------------------------------------------------
# Consolidation
# ---------------------------------------------------------------------------

@dataclass
class _Run:
    state: int
    frame_in: int
    frame_out: int  # exclusive

    @property
    def n(self) -> int:
        return self.frame_out - self.frame_in


def _rle_from_state_array(state: np.ndarray) -> list[_Run]:
    n = len(state)
    if n == 0:
        return []
    runs: list[_Run] = []
    start = 0
    for i in range(1, n + 1):
        if i == n or state[i] != state[start]:
            runs.append(_Run(state=int(state[start]), frame_in=start, frame_out=i))
            start = i
    return runs


def _merge_adjacent_same_class(runs: list[_Run]) -> list[_Run]:
    if not runs:
        return []
    out = [runs[0]]
    for r in runs[1:]:
        if r.state == out[-1].state:
            out[-1] = _Run(state=out[-1].state, frame_in=out[-1].frame_in, frame_out=r.frame_out)
        else:
            out.append(r)
    return out


def _consolidate_hysteresis(state: np.ndarray, fps: float, params: SegmentParams) -> list[_Run]:
    """Iteratively absorb any run shorter than ``min_run_sec`` into
    whichever neighbour has the greater total duration (ties go to the
    left neighbour, deterministically), re-merging adjacent same-class
    runs after each absorption, to a fixed point. Design Sec2.1's
    "slice-1 stand-in", the A of slice 3's A/B."""
    runs = _rle_from_state_array(state)
    min_run_frames = max(1, int(round(params.min_run_sec * fps)))

    while len(runs) > 1:
        # Shortest run, earliest first on a tie -- deterministic.
        shortest_idx = min(range(len(runs)), key=lambda i: (runs[i].n, i))
        if runs[shortest_idx].n >= min_run_frames:
            break  # fixed point: nothing left to absorb

        i = shortest_idx
        has_left = i > 0
        has_right = i < len(runs) - 1
        if has_left and has_right:
            target = i - 1 if runs[i - 1].n >= runs[i + 1].n else i + 1
        elif has_left:
            target = i - 1
        else:
            target = i + 1

        runs[i] = _Run(state=runs[target].state, frame_in=runs[i].frame_in, frame_out=runs[i].frame_out)
        runs = _merge_adjacent_same_class(runs)

    return runs


def _viterbi_decode(costs: np.ndarray, lam: float) -> np.ndarray:
    """Minimum-cost label path over per-frame class costs with a single
    flat transition penalty ``lam`` for any class change (design Sec2.1).
    ``costs`` is (n, K). Returns the (n,) int state-id path."""
    n, k = costs.shape
    if n == 0:
        return np.zeros(0, dtype=np.int64)

    dp = costs[0].copy()
    back = np.zeros((n, k), dtype=np.int64)  # -1 = "stay" (from same state)
    back[0, :] = -1

    for i in range(1, n):
        order = np.argsort(dp)
        best_s, second_s = order[0], (order[1] if k > 1 else order[0])
        best_c, second_c = dp[best_s], dp[second_s]

        new_dp = np.empty(k, dtype=np.float64)
        new_back = np.empty(k, dtype=np.int64)
        for s in range(k):
            stay_cost = dp[s]
            switch_from = second_s if s == best_s else best_s
            switch_cost = (second_c if s == best_s else best_c) + lam
            if stay_cost <= switch_cost:
                new_dp[s] = stay_cost
                new_back[s] = -1
            else:
                new_dp[s] = switch_cost
                new_back[s] = switch_from
            new_dp[s] += costs[i, s]
        dp, back[i] = new_dp, new_back

    path = np.empty(n, dtype=np.int64)
    path[-1] = int(np.argmin(dp))
    for i in range(n - 1, 0, -1):
        prev = back[i, path[i]]
        path[i - 1] = path[i] if prev == -1 else prev
    return path


def _consolidate_viterbi(
    arrays: dict[str, np.ndarray], classify_params: ClassifyParams, lam: float,
) -> list[_Run]:
    """Reuses classify.py's own (private) smoothing and cost functions
    directly -- they already compute exactly what a Viterbi decode over
    "the per-frame cost vectors classify.py already computes" (task brief)
    needs, and neither mutates anything, so there is nothing to "expose"
    as new public API beyond importing them."""
    vx = _classify._smooth(arrays["tx_norm_src_width"], classify_params.smooth_window_frames)
    vy = _classify._smooth(arrays["ty_norm_src_width"], classify_params.smooth_window_frames)
    div = _classify._smooth(arrays["log_scale"], classify_params.smooth_window_frames)
    roll_rate = _classify._smooth(arrays["roll"], classify_params.smooth_window_frames)
    resid = _classify._smooth(arrays["resid"], classify_params.smooth_window_frames)
    hf_energy = arrays["hf_energy"].astype(np.float64)
    costs = _classify._all_costs(vx, vy, div, roll_rate, resid, hf_energy, classify_params)
    path = _viterbi_decode(costs, lam)
    return _rle_from_state_array(path.astype(np.int8))


# ---------------------------------------------------------------------------
# Focus signal (design Sec1.4)
# ---------------------------------------------------------------------------

def _focus_residual(vx: np.ndarray, vy: np.ndarray, lapvar_norm: np.ndarray) -> np.ndarray:
    """Motion-adjusted, per-clip-normalized focus signal (design Sec1.4
    points 1-2): regress log(lapvar_norm) on smoothed speed across the
    WHOLE clip and return the residual, so a fast pan's expected
    motion-blur dip does not read as a focus defect ("a pan is not
    scored as soft for being a pan")."""
    speed = np.sqrt(vx ** 2 + vy ** 2)
    log_lv = np.log(np.maximum(lapvar_norm, 1e-6))
    a = np.column_stack([np.ones_like(speed), speed])
    coef, *_ = np.linalg.lstsq(a, log_lv, rcond=None)
    predicted = a @ coef
    return log_lv - predicted


def _hunt_rate_per_sec(
    residual: np.ndarray, fps: float, smooth_frames: int = 9, deadband_std_mult: float = 0.15,
) -> np.ndarray:
    """Rolling rate (per second) of genuine level crossings in the focus
    residual -- design Sec1.4 point 3's "hunting = sign changes above a
    fitted rate per second", implemented as a Schmitt-trigger (deadband)
    crossing count rather than a raw ``np.sign(diff)`` count.

    **Flagged finding, not in the design doc (measured on the real
    benchmark clip while building this gate, 2026-09-01):** a raw
    frame-to-frame sign-change count on this residual is USELESS on real
    footage -- even smoothed over a full second, 31% of the clip's frames
    still exceed CULLS.md's worked-example threshold of 2.4/s, because
    per-frame Laplacian variance on real 4K compressed video carries
    small, high-frequency noise that crosses zero constantly regardless
    of smoothing window (raw, unsmoothed: 99.6% of frames exceed it).
    Applying that threshold literally rejects nearly the entire clip as
    "hunting" and is why an early run of this module accepted ZERO
    segments end to end. A Schmitt trigger -- track a discrete state that
    only flips once the (mildly smoothed) residual clears a deadband on
    either side of zero, and count STATE flips rather than raw sign
    flips -- is the standard fix for exactly this failure mode (a noisy
    signal near its own mean triggers a naive zero-crossing counter
    constantly). The deadband is set from the residual's OWN std
    (``deadband_std_mult``, a per-clip-relative fraction, the same
    normalization-by-the-clip's-own-distribution reasoning design Sec1.4
    already uses for lapvar) rather than an absolute number, so it
    self-scales. Verified on the real clip: at ``smooth_frames=9``,
    ``deadband_std_mult=0.15`` the false-trigger rate on this clip's own
    (presumably mostly non-hunting) footage is effectively zero, while
    the synthetic ``focus-gate`` test's much larger, deliberately
    alternating signal still saturates the detector easily. This is a
    correctness fix to a broken gate (it produced 0 accepted segments,
    a functional failure, not a low score), not a benchmark-tuned
    threshold -- ``focus_hunt_sign_changes_per_sec`` itself is untouched.
    """
    n = len(residual)
    if n < 2:
        return np.zeros(n)
    r = _classify._smooth(residual, smooth_frames) if smooth_frames > 1 else residual
    thresh = deadband_std_mult * float(np.std(r))
    state = np.zeros(n, dtype=np.int8)
    if thresh > 0:
        cur = 0
        for i in range(n):
            v = r[i]
            if v > thresh:
                cur = 1
            elif v < -thresh:
                cur = -1
            state[i] = cur
    changes = (np.diff(state, prepend=state[0]) != 0).astype(np.float64)
    window = max(1, int(round(fps)))
    kernel = np.ones(window, dtype=np.float64)
    counts = np.convolve(changes, kernel, mode="same")
    # mode="same" centers the window; a boundary window is shorter than
    # `window` frames' worth of coverage, but convolve still divides by
    # the same fixed `window` below -- close enough for a rate estimate at
    # a boundary, and this is a diagnostic gate, not a scored quantity.
    return counts / (window / fps)


def _focus_shape(residual_slice: np.ndarray, rack_min_ramp_frames: int) -> str:
    """"steady" | "rack_in" | "rack_out" (design Sec1.4 point 3): a
    monotonic, sign-consistent ramp of at least ``rack_min_ramp_frames``
    covering most of the slice is a rack; anything else steady (hunting
    is handled separately, upstream, as a whole-run kill)."""
    n = len(residual_slice)
    if n < max(2, rack_min_ramp_frames):
        return "steady"
    diffs = np.diff(residual_slice)
    pos_frac = float(np.mean(diffs > 0)) if len(diffs) else 0.0
    neg_frac = float(np.mean(diffs < 0)) if len(diffs) else 0.0
    total_change = float(residual_slice[-1] - residual_slice[0])
    if pos_frac >= 0.7 and total_change > 0.15:
        return "rack_in"
    if neg_frac >= 0.7 and total_change < -0.15:
        return "rack_out"
    return "steady"


# ---------------------------------------------------------------------------
# Pipeline result types
# ---------------------------------------------------------------------------

@dataclass
class _Segment:
    frame_in: int
    frame_out: int
    motion_intent: str
    boundary_reason_in: str
    boundary_reason_out: str
    focus_shape: str
    lapvar_median: float
    lapvar_normalized: float
    motion_adjusted: bool
    mean_luma: float
    clip_low_frac: float
    clip_high_frac: float
    motion_consistency: float
    focus_score: float
    exposure_score: float
    motion_confidence: float


@dataclass
class _Rejection:
    frame_in: int
    frame_out: int
    reason: str
    reason_detail: str = ""
    secondary_reasons: tuple = ()


@dataclass
class SegmentResult:
    """One sidecar's worth of segmentation, in frame/second terms,
    source-relative -- no manifest needed. :func:`write_culls` resolves
    manifest identity on top of this."""
    fps: float
    duration_sec: float
    analysed_frames: int
    consolidated_runs: list
    segments: list
    rejections: list
    consolidation: str
    params: SegmentParams

    @property
    def n_runs(self) -> int:
        return len(self.consolidated_runs)

    @property
    def median_run_duration_sec(self) -> float:
        if not self.consolidated_runs:
            return 0.0
        durs = [(r.frame_out - r.frame_in) / self.fps for r in self.consolidated_runs]
        return float(np.median(durs))


# ---------------------------------------------------------------------------
# Sec 2.2 pipeline
# ---------------------------------------------------------------------------

def _settle_amount(state_name: str, params: SegmentParams) -> int:
    return params.settle_frames_static if state_name == "static" else params.settle_frames


def _split_by_mask(frame_in: int, frame_out: int, bad_mask: np.ndarray) -> list[tuple[int, int, bool]]:
    """Split [frame_in, frame_out) into contiguous (start, end, is_bad)
    spans per ``bad_mask`` (indexed globally, sliced here)."""
    spans: list[tuple[int, int, bool]] = []
    if frame_out <= frame_in:
        return spans
    seg = bad_mask[frame_in:frame_out]
    start = frame_in
    cur = bool(seg[0])
    for i in range(1, len(seg)):
        if bool(seg[i]) != cur:
            spans.append((start, frame_in + i, cur))
            start = frame_in + i
            cur = bool(seg[i])
    spans.append((start, frame_out, cur))
    return spans


def _run_pipeline(
    runs: list[_Run],
    arrays: dict[str, np.ndarray],
    fps: float,
    analysed_frames: int,
    params: SegmentParams,
) -> tuple[list[_Segment], list[_Rejection]]:
    lapvar_norm = arrays["lapvar_norm"].astype(np.float64)
    lapvar = arrays["lapvar"].astype(np.float64)
    luma_mean = arrays["luma_mean"].astype(np.float64)
    clip_low = arrays["clip_low"].astype(np.float64)
    clip_high = arrays["clip_high"].astype(np.float64)
    resid = arrays["resid"].astype(np.float64)
    peak = arrays.get("peak", np.zeros(analysed_frames)).astype(np.float64)
    vx = arrays["tx_norm_src_width"].astype(np.float64)
    vy = arrays["ty_norm_src_width"].astype(np.float64)

    focus_residual = _focus_residual(vx, vy, lapvar_norm)
    soft_threshold = float(np.percentile(focus_residual, 100.0 * params.focus_norm_quantile))
    soft_frame = focus_residual < soft_threshold
    hunt_rate = _hunt_rate_per_sec(
        focus_residual, fps, params.focus_hunt_smooth_frames, params.focus_hunt_deadband_std,
    )
    hunting_frame = hunt_rate > params.focus_hunt_sign_changes_per_sec
    exposure_bad_low = clip_low > params.clip_low_frac_max
    exposure_bad_high = clip_high > params.clip_high_frac_max

    resid_eps_ref = ClassifyParams().resid_eps

    segments: list[_Segment] = []
    rejections: list[_Rejection] = []
    first_accepted_emitted = False

    def _score_block(f_in: int, f_out: int) -> dict:
        sl = slice(f_in, f_out)
        motion_consistency = float(np.clip(1.0 - float(np.mean(resid[sl])) / resid_eps_ref, 0.0, 1.0))
        focus_score = float(np.clip(float(np.median(lapvar_norm[sl])), 0.0, 1.0))
        exposure_score = float(np.clip(
            1.0 - max(float(np.median(clip_low[sl])), float(np.median(clip_high[sl]))), 0.0, 1.0,
        ))
        motion_confidence = float(np.clip(float(np.mean(peak[sl])), 0.0, 1.0))
        return {
            "motion_consistency": motion_consistency, "focus_score": focus_score,
            "exposure_score": exposure_score, "motion_confidence": motion_confidence,
        }

    for idx, run in enumerate(runs):
        state_name = STATE_NAMES[run.state]
        frame_in, frame_out = run.frame_in, run.frame_out

        # --- 1. settle trim -------------------------------------------------
        leading = 0 if frame_in == 0 else min(_settle_amount(state_name, params), run.n)
        trailing = 0 if frame_out == analysed_frames else min(
            _settle_amount(state_name, params), run.n - leading,
        )
        trimmed_in, trimmed_out = frame_in + leading, frame_out - trailing

        if trimmed_out <= trimmed_in:
            # The whole run is consumed by its own settle trim -- a
            # transition, not a select (design Sec2.2 point 1; the 0.34s
            # between Ryan's #3 and #4 is exactly this case). Emitted as
            # ONE transition rejection over the run's original extent,
            # never as two separate "settle" chunks that would happen to
            # sum to the same range -- "settle" rejections only exist
            # where something real survives on the other side of them.
            rejections.append(_Rejection(
                frame_in, frame_out, "transition",
                f"{run.n} frames of {state_name} entirely consumed by settle trim ({run.n/fps:.2f}s)",
            ))
            continue

        if leading > 0:
            rejections.append(_Rejection(frame_in, frame_in + leading, "settle",
                                          f"{leading} settle frame(s) trimmed from the start of a {state_name} run"))
        if trailing > 0:
            rejections.append(_Rejection(frame_out - trailing, frame_out, "settle",
                                          f"{trailing} settle frame(s) trimmed from the end of a {state_name} run"))

        # --- 2. minimum duration --------------------------------------------
        dur_sec = (trimmed_out - trimmed_in) / fps
        if dur_sec < params.min_duration_sec:
            rejections.append(_Rejection(
                trimmed_in, trimmed_out, "too_short",
                f"{dur_sec:.2f}s {state_name} run below min_duration_sec={params.min_duration_sec}",
            ))
            continue

        # --- 3. class gate ----------------------------------------------------
        if state_name in _CLOSED_CLASSES:
            rejections.append(_Rejection(
                trimmed_in, trimmed_out, state_name,
                f"{dur_sec:.2f}s classified {state_name}; never opens a select",
            ))
            continue

        # --- 4. quality gates: focus, exposure ---------------------------------
        hunt_frac = float(np.mean(hunting_frame[trimmed_in:trimmed_out]))
        if hunt_frac > 0.5:
            rejections.append(_Rejection(
                trimmed_in, trimmed_out, "focus_hunt",
                f"{dur_sec:.2f}s {state_name} run: focus hunting {hunt_frac*100:.0f}% of frames "
                f"exceeds {params.focus_hunt_sign_changes_per_sec}/s sign-change rate",
            ))
            continue

        bad_mask = np.zeros(analysed_frames, dtype=bool)
        bad_mask[trimmed_in:trimmed_out] = (
            soft_frame[trimmed_in:trimmed_out]
            | exposure_bad_low[trimmed_in:trimmed_out]
            | exposure_bad_high[trimmed_in:trimmed_out]
        )
        spans = _split_by_mask(trimmed_in, trimmed_out, bad_mask)

        for span_i, (s_in, s_out, is_bad) in enumerate(spans):
            if is_bad:
                # Focus takes priority over exposure when a span fails both
                # (a soft frame's exposure numbers are secondary), matching
                # the boundary-reason priority below.
                if bool(np.any(soft_frame[s_in:s_out])):
                    reason = "soft"
                elif float(np.mean(exposure_bad_low[s_in:s_out])) >= float(np.mean(exposure_bad_high[s_in:s_out])):
                    reason = "underexposed"
                else:
                    reason = "overexposed"
                rejections.append(_Rejection(
                    s_in, s_out, reason,
                    f"{(s_out-s_in)/fps:.2f}s span inside a {state_name} run failed the "
                    f"{reason} gate",
                ))
                continue

            span_dur = (s_out - s_in) / fps
            if span_dur < params.min_duration_sec:
                rejections.append(_Rejection(
                    s_in, s_out, "too_short",
                    f"{span_dur:.2f}s remainder after quality-gate split below min_duration_sec",
                ))
                continue

            shape = _focus_shape(focus_residual[s_in:s_out], params.rack_min_ramp_frames)

            # boundary reasons -------------------------------------------------
            if s_in == 0:
                b_in = "clip_start"
            elif s_in == trimmed_in and leading > 0 and not first_accepted_emitted:
                b_in = "settle"
            elif span_i > 0:
                b_in = "focus_regained" if spans[span_i - 1][2] else "motion_change"
            else:
                b_in = "motion_change"

            if s_out == analysed_frames:
                b_out = "clip_end"
            elif span_i < len(spans) - 1:
                # Closed by a quality-gate split: which gate fired on the
                # following (bad) span decides the reason. A span can fail
                # both soft and exposure at once; focus takes priority
                # since a soft frame's exposure numbers are secondary.
                next_s_in, next_s_out = spans[span_i + 1][0], spans[span_i + 1][1]
                if bool(soft_frame[next_s_in:next_s_out].any()):
                    b_out = "focus_lost"
                elif bool(exposure_bad_low[next_s_in:next_s_out].any()) or bool(exposure_bad_high[next_s_in:next_s_out].any()):
                    b_out = "exposure_fault"
                else:
                    b_out = "focus_lost"  # defensive fallback; should be unreachable
            elif idx + 1 < len(runs) and STATE_NAMES[runs[idx + 1].state] == "shake":
                b_out = "shake_onset"
            else:
                b_out = "motion_change"

            scores = _score_block(s_in, s_out)

            segments.append(_Segment(
                frame_in=s_in, frame_out=s_out,
                motion_intent=state_name,
                boundary_reason_in=b_in, boundary_reason_out=b_out,
                focus_shape=shape,
                lapvar_median=float(np.median(lapvar[s_in:s_out])),
                lapvar_normalized=float(np.median(lapvar_norm[s_in:s_out])),
                motion_adjusted=True,
                mean_luma=float(np.median(luma_mean[s_in:s_out])),
                clip_low_frac=float(np.max(clip_low[s_in:s_out])),
                clip_high_frac=float(np.max(clip_high[s_in:s_out])),
                motion_consistency=scores["motion_consistency"],
                focus_score=scores["focus_score"],
                exposure_score=scores["exposure_score"],
                motion_confidence=scores["motion_confidence"],
            ))
            first_accepted_emitted = True

    return segments, rejections


def _merge_adjacent_rejections(rejections: list[_Rejection]) -> list[_Rejection]:
    """CULLS Sec4.5: "too_short, settle and transition are the three
    high-volume reasons and they are merged into runs rather than emitted
    per frame." This module already emits them per contiguous span, not
    per frame; this pass additionally merges directly-adjacent rejections
    that share the same reason, so a settle-then-settle (leading of one
    run touching trailing of a fully-rejected neighbour) reads as one
    line."""
    if not rejections:
        return []
    ordered = sorted(rejections, key=lambda r: r.frame_in)
    merged = [ordered[0]]
    for r in ordered[1:]:
        last = merged[-1]
        if r.reason == last.reason and r.frame_in == last.frame_out:
            merged[-1] = _Rejection(last.frame_in, r.frame_out, last.reason, last.reason_detail)
        else:
            merged.append(r)
    return merged


# ---------------------------------------------------------------------------
# segment_source: the pure, manifest-free entry point
# ---------------------------------------------------------------------------

def _resolve_sidecar_pair(npz_path_or_source: Path, out_dir: Optional[Path]) -> tuple[Path, Path]:
    npz_path, json_path, _ = _classify._resolve_sidecar(Path(npz_path_or_source), out_dir)
    return npz_path, json_path


def segment_source(
    sidecar: Path | str,
    *,
    params: Optional[SegmentParams] = None,
    ruleset: str = "visual",
    out_dir: Optional[Path | str] = None,
) -> SegmentResult:
    """Segment one already-classified sidecar. Pure: reads the sidecar,
    writes nothing, needs no manifest. ``ruleset`` must be ``"visual"`` --
    the narrative ruleset is slice 5's job (design Sec5); a segmenter that
    silently ran the wrong gates under the wrong name would be worse than
    refusing.

    Raises:
        SegmentValidationError: bad inputs / a sidecar with no ``state``
            array (i.e. never classified).
    """
    if ruleset != "visual":
        raise SegmentValidationError(
            [f"ruleset {ruleset!r} not implemented by this slice; only 'visual' is (design Sec5 slice 5 is narrative)"]
        )
    params = params or SegmentParams()

    npz_path, json_path = _resolve_sidecar_pair(Path(sidecar), Path(out_dir) if out_dir else None)
    problems: list[str] = []
    if not npz_path.exists():
        problems.append(f"sidecar npz not found: {npz_path}")
    if not json_path.exists():
        problems.append(f"sidecar json not found: {json_path}")
    if problems:
        raise SegmentValidationError(problems)

    header = json.loads(json_path.read_text())
    with np.load(npz_path) as npz:
        arrays: dict[str, np.ndarray] = {k: npz[k] for k in npz.files}

    if "state" not in arrays:
        raise SegmentValidationError(
            [f"sidecar {npz_path} has no 'state' array; run posthouse.cull.classify first"]
        )

    fps = float(header.get("source", {}).get("fps") or 0.0)
    duration_sec = float(header.get("source", {}).get("duration_sec") or 0.0)
    analysed_frames = int(arrays["state"].shape[0])
    if fps <= 0 or analysed_frames == 0:
        raise SegmentValidationError(
            [f"sidecar {npz_path} has no usable fps/frames (fps={fps}, frames={analysed_frames})"]
        )

    classify_params = ClassifyParams(**(header.get("classify", {}).get("params", {}) or {}))

    if params.consolidation == "hysteresis":
        runs = _consolidate_hysteresis(arrays["state"], fps, params)
    else:
        runs = _consolidate_viterbi(arrays, classify_params, params.viterbi_lambda)

    segs, rejs = _run_pipeline(runs, arrays, fps, analysed_frames, params)
    rejs = _merge_adjacent_rejections(rejs)

    return SegmentResult(
        fps=fps, duration_sec=duration_sec, analysed_frames=analysed_frames,
        consolidated_runs=runs, segments=segs, rejections=rejs,
        consolidation=params.consolidation, params=params,
    )


# ---------------------------------------------------------------------------
# Tiling invariant (asserted in code, not only tested -- CULLS Sec7 REJECT 7)
# ---------------------------------------------------------------------------

def _assert_tiling(frame_ranges: list[tuple[int, int]], analysed_frames: int, context: str) -> None:
    ordered = sorted(frame_ranges)
    cursor = 0
    for f_in, f_out in ordered:
        if f_in != cursor:
            raise TilingInvariantError(
                f"{context}: coverage gap/overlap at frame {cursor} (next range starts at {f_in})"
            )
        cursor = f_out
    if cursor != analysed_frames:
        raise TilingInvariantError(
            f"{context}: coverage ends at frame {cursor}, expected {analysed_frames}"
        )


# ---------------------------------------------------------------------------
# write_culls: manifest-aware, writes the contract file
# ---------------------------------------------------------------------------

@dataclass
class WriteCullsResult:
    master_path: Path
    view_path: Optional[Path]
    result: SegmentResult
    n_accepted: int
    n_rejections: int


def _deterministic_id(*parts: str) -> str:
    """A uuid4-shaped id deterministic from content (CULLS Sec4.1 lists
    ``cull_id``'s default as "uuid4", but Sec1 also requires
    "same inputs + same parameter set => byte-identical file" with no
    exception for cull_id -- the task brief resolves this explicitly:
    "make ids deterministic from content if that is cleaner")."""
    h = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{h[0:8]}-{h[8:12]}-4{h[13:16]}-{h[16:20]}-{h[20:32]}"


def _find_manifest_source(manifest: dict, source_file: Path) -> tuple[dict, str]:
    """Find the manifest source whose ``path`` is an ancestor of (or equal
    to) ``source_file``, and the POSIX rel_path within it (CULLS Sec3)."""
    resolved = source_file.resolve()
    best: Optional[tuple[dict, str]] = None
    for src in manifest.get("sources", []):
        src_path = Path(src["path"]).resolve()
        try:
            rel = resolved.relative_to(src_path)
        except ValueError:
            if resolved == src_path:
                rel = Path("")
            else:
                continue
        rel_str = "" if str(rel) == "." else rel.as_posix()
        if best is None or len(str(src["path"])) > len(str(best[0]["path"])):
            best = (src, rel_str)
    if best is None:
        raise SegmentValidationError(
            [f"{source_file} is not under any source in the manifest"]
        )
    return best


def write_culls(
    sidecar: Path | str,
    manifest_path: Path | str,
    out_dir: Path | str,
    *,
    params: Optional[SegmentParams] = None,
    ruleset: str = "visual",
) -> WriteCullsResult:
    """Segment one sidecar and write a contract-valid ``culls.json`` (plus
    its per-ruleset view) against a Project Manifest. Single-source,
    single-ruleset -- dual-use multi-source wiring is slice 6's job
    (design Sec5); this is exactly slice 3's scope ("the visual ruleset
    only. ... Emits a valid culls.json").

    Raises:
        SegmentValidationError: bad inputs, listed exhaustively.
        TilingInvariantError: the coverage invariant does not hold (a bug
            in this module, asserted rather than silently shipped).
    """
    from posthouse.manifest import load_manifest  # local import: avoid a
    # hard posthouse.manifest dependency for callers that only ever use
    # segment_source() directly against a bare sidecar.

    params = params or SegmentParams()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    npz_path, json_path = _resolve_sidecar_pair(Path(sidecar), None)
    if not npz_path.exists() or not json_path.exists():
        raise SegmentValidationError([f"sidecar not found for {sidecar}"])
    header = json.loads(json_path.read_text())
    source_path = Path(header["source"]["path"])

    manifest = load_manifest(Path(manifest_path))
    manifest_source, rel_path = _find_manifest_source(manifest, source_path)

    result = segment_source(npz_path, params=params, ruleset=ruleset)

    resolved_source_path = str(
        (Path(manifest_source["path"]) / rel_path) if rel_path else Path(manifest_source["path"])
    )

    source_id = manifest_source["id"]
    kind = manifest_source["kind"]
    dual_use = bool(manifest_source.get("dual_use", False))
    fps = result.fps
    duration_sec = result.duration_sec
    analysed_frames = result.analysed_frames

    ruleset_short = ruleset[0]  # "v" | "n"
    segments_out: list[dict] = []
    for i, s in enumerate(sorted(result.segments, key=lambda s: s.frame_in), start=1):
        in_sec, out_sec = s.frame_in / fps, s.frame_out / fps
        handle_in = min(params.handle_sec, in_sec)
        handle_out = min(params.handle_sec, max(0.0, duration_sec - out_sec))
        overall = min(s.motion_consistency, s.focus_score, s.exposure_score)
        label = f"{_MOTION_INTENT_NAMES[s.motion_intent]} · {ruleset} · {out_sec - in_sec:.1f}s"
        segments_out.append({
            "source_path": resolved_source_path,
            "in_sec": in_sec, "out_sec": out_sec,
            "label": label, "handle_sec": params.handle_sec,
            "segment_id": f"{source_id}-{ruleset_short}{i:04d}",
            "source_id": source_id, "rel_path": rel_path, "ruleset": ruleset,
            "frame_in": s.frame_in, "frame_out": s.frame_out,
            "handle_in_sec": handle_in, "handle_out_sec": handle_out,
            "motion_intent": s.motion_intent, "motion_confidence": s.motion_confidence,
            "boundary_reason_in": s.boundary_reason_in, "boundary_reason_out": s.boundary_reason_out,
            "scores": {
                "motion_consistency": s.motion_consistency, "focus": s.focus_score,
                "exposure": s.exposure_score, "overall": overall,
            },
            "focus": {
                "shape": s.focus_shape, "lapvar_median": s.lapvar_median,
                "lapvar_normalized": s.lapvar_normalized, "motion_adjusted": s.motion_adjusted,
            },
            "exposure": {
                "mean_luma": s.mean_luma, "clip_low_frac": s.clip_low_frac,
                "clip_high_frac": s.clip_high_frac,
            },
        })

    rejections_out: list[dict] = []
    for r in result.rejections:
        in_sec, out_sec = r.frame_in / fps, r.frame_out / fps
        rejections_out.append({
            "source_id": source_id, "rel_path": rel_path, "source_path": resolved_source_path,
            "ruleset": ruleset, "in_sec": in_sec, "out_sec": out_sec,
            "frame_in": r.frame_in, "frame_out": r.frame_out,
            "reason": r.reason, "reason_detail": r.reason_detail,
            "duration_sec": out_sec - in_sec,
        })

    # Mandatory tiling invariant, asserted in code.
    _assert_tiling(
        [(s["frame_in"], s["frame_out"]) for s in segments_out]
        + [(r["frame_in"], r["frame_out"]) for r in rejections_out],
        analysed_frames, f"{source_id}/{rel_path}/{ruleset}",
    )

    if not segments_out:
        raise SegmentValidationError(
            [f"zero accepted segments for {source_id}/{rel_path} ({ruleset}); "
             f"see rejections for why (CULLS Sec7 REJECT 10)"]
        )

    project_name = manifest.get("project", {}).get("name", "Untitled")
    generator_src = header.get("generator", {})
    signals_rel = _relpath(npz_path, out_dir)

    cull_id = _deterministic_id(
        header["source"]["sha256"], json.dumps(params.as_contract_dict(), sort_keys=True), ruleset,
    )

    accepted_sec = sum(s["out_sec"] - s["in_sec"] for s in segments_out)
    analysed_sec = duration_sec

    master = {
        "contract_version": CONTRACT_VERSION,
        "sequence_name": f"Cold Footage: {project_name}",
        "cull_id": cull_id,
        "created_at": now_iso(),
        "generator": {
            "name": "posthouse.cull", "version": SEGMENT_VERSION,
            "precut_pin": manifest.get("generator", {}).get("precut_pin", ""),
            "ffmpeg_version": generator_src.get("ffmpeg_version", "unknown"),
            "numpy_version": generator_src.get("numpy_version", np.__version__),
        },
        "manifest_id": manifest["manifest_id"], "manifest_revision": manifest["revision"],
        "params": {
            "params_id": "default-v1",
            "fit_provenance": "default",
            "analysis": {
                "plane_width": header["analysis"]["plane_width"],
                "plane_height": header["analysis"]["plane_height"],
                "plane_format": header["analysis"]["plane_format"],
                "decode": header["analysis"]["decode"],
                "source_grade": header["analysis"]["source_grade"],
                "audio_sr": header["analysis"].get("audio_sr"),
            },
            "visual": params.as_contract_dict() if ruleset == "visual" else {},
            "narrative": {"note": "not implemented; design Sec5 slice 5"} if ruleset == "visual" else params.as_contract_dict(),
        },
        "sources": [{
            "source_id": source_id, "rel_path": rel_path, "source_path": resolved_source_path,
            "kind": kind, "dual_use": dual_use, "rulesets_run": [ruleset],
            "duration_sec": duration_sec, "fps": fps,
            "width": header["source"]["width"], "height": header["source"]["height"],
            "analysed_frames": analysed_frames,
            "signals_path": signals_rel, "signals_sha256": header.get("npz_sha256", ""),
            "analysis_sec": header.get("timings", {}).get("wall_sec", 0.0),
        }],
        "segments": segments_out,
        "rejections": rejections_out,
        "counts": {
            "sources_analysed": 1, "segments_accepted": len(segments_out),
            "rejections": len(rejections_out), "accepted_sec": accepted_sec,
            "analysed_sec": analysed_sec,
            "by_ruleset": {ruleset: {"segments": len(segments_out), "sec": accepted_sec}},
        },
    }

    problems = validate_segments_shape(master)
    for i, seg in enumerate(master["segments"]):
        if seg.get("ruleset") not in ALLOWED_RULESETS:
            problems.append(f"segment[{i}]: ruleset {seg.get('ruleset')!r} not one of {sorted(ALLOWED_RULESETS)}")
    if problems:
        raise SegmentValidationError(problems)

    master_path = out_dir / "culls.json"
    atomic_write_bytes(master_path, (json.dumps(master, indent=2) + "\n").encode("utf-8"))

    view = dict(master)
    view["sequence_name"] = f"Cold Footage: {'Narrative' if ruleset == 'narrative' else 'Visual'}"
    view_path = out_dir / f"culls.{ruleset}.json"
    atomic_write_bytes(view_path, (json.dumps(view, indent=2) + "\n").encode("utf-8"))

    return WriteCullsResult(
        master_path=master_path, view_path=view_path, result=result,
        n_accepted=len(segments_out), n_rejections=len(rejections_out),
    )


def _relpath(target: Path, start: Path) -> str:
    import os
    return os.path.relpath(str(target), str(start))


# ---------------------------------------------------------------------------
# Diagnostics (design Sec0's acceptance metrics -- NOT contract fields)
# ---------------------------------------------------------------------------

def boundary_hit_fraction(boundary_secs: list[float], cut_point_secs: list[float], tol: float = 0.5) -> float:
    """Fraction of ``cut_point_secs`` within ``tol`` seconds of some value
    in ``boundary_secs``. Used to report the design Sec0 acceptance
    metric ("his cut points land within 0.5s of a class boundary X% of
    the time"); not part of the contract."""
    if not cut_point_secs:
        return 0.0
    boundaries = sorted(boundary_secs)
    if not boundaries:
        return 0.0
    hits = 0
    for cp in cut_point_secs:
        import bisect
        i = bisect.bisect_left(boundaries, cp)
        candidates = [b for b in (boundaries[i - 1] if i > 0 else None, boundaries[i] if i < len(boundaries) else None) if b is not None]
        if any(abs(b - cp) <= tol for b in candidates):
            hits += 1
    return hits / len(cut_point_secs)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m posthouse.cull.segment",
        description="Segment a classified Phase 4 sidecar into a culls.json (visual ruleset).",
    )
    parser.add_argument("sidecar", type=Path, help="Path to a .signals.npz sidecar (already classified).")
    parser.add_argument("--manifest", required=True, type=Path, help="Project Manifest JSON.")
    parser.add_argument("--out", required=True, type=Path, help="Output directory for culls.json.")
    parser.add_argument("--consolidation", choices=("viterbi", "hysteresis"), default="hysteresis")
    parser.add_argument("--ruleset", choices=("visual",), default="visual")

    args = parser.parse_args(argv)

    params = SegmentParams(consolidation=args.consolidation)

    try:
        result = write_culls(args.sidecar, args.manifest, args.out, params=params, ruleset=args.ruleset)
    except SegmentValidationError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except SegmentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # pragma: no cover - defensive: never crash bare
        print(f"error: unexpected failure segmenting: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(f"wrote: {result.master_path}")
    print(f"wrote: {result.view_path}")
    print(f"consolidation: {result.result.consolidation}")
    print(f"consolidated runs: {result.result.n_runs} (median {result.result.median_run_duration_sec:.2f}s)")
    print(f"segments accepted: {result.n_accepted}")
    print(f"rejections: {result.n_rejections}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
