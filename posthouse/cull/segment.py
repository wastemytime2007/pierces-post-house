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

Consolidation / extent detection (design Sec 2.1), three paths behind
``SegmentParams.consolidation``
-------------------------------------------------------------------------------
* ``"stability"`` -- **the default as of slice 5.** Not a consolidation
  over classified runs at all: a direct, per-frame threshold test on two
  smoothed signals (motion residual capped, sharpness above a per-clip
  quantile), matching what the Decision Log (2026-09-02) recorded as the
  "crude two-signal probe" that beat the whole classify+consolidate+gate
  pipeline held-out (P 0.635/R 0.881/IoU 0.428 vs P 0.634/R 0.838/
  IoU 0.387) once both were fitted and measured under the identical
  block-CV scheme. Per that finding's recommendation ("demote, do not
  delete"), this is now real production code, not a diagnostic script:
  the same 0.7s smoothing window and ``min_duration_sec`` floor as the
  diagnostic, the SAME exposure gate as the legacy paths (it earned its
  place -- kept), and deliberately NO focus gate as a boundary input (it
  did not earn its place -- removed as a segmentation input, though focus
  signals are still computed and reported). The motion classifier plays
  no role in deciding *where* a select opens or closes under this path --
  see "Labeling" below for what it is used for instead. See
  :func:`_run_stability_pipeline`.
* ``"hysteresis"`` -- legacy (slice 3), kept reachable, not deleted: the
  design's "slice-1 stand-in": iteratively absorb any run shorter than
  ``min_run_sec`` into whichever neighbour has the greater total
  duration, re-merging adjacent same-class runs after each absorption, to
  a fixed point (no run left below the threshold, or only one run
  remains). Simple to review, simple to explain when it misfires.
* ``"viterbi"`` -- legacy (slice 3), kept reachable, not deleted: a
  single-penalty Viterbi decode over the SAME per-frame class costs
  :mod:`posthouse.cull.classify` already computes (reused directly via
  its private ``_all_costs``/``_smooth`` rather than adding new public
  surface to a module that does not mutate anything here -- classify.py
  already exposes exactly the pieces needed and nothing about
  loading/smoothing needed to change to reuse them). Emission cost per
  frame per class from classify's cost matrix; a flat transition penalty
  ``viterbi_lambda`` for any class change, 0 for staying. Boundaries are
  where the decoded label changes.

None of the three is fitted here (ROADMAP Sec5 / design Sec5 slice 4's
job, extended by slice 5 to the stability path -- see
:mod:`posthouse.cull.fit`). Every default in :class:`SegmentParams` is a
**reasoned, documented, unfit default** -- several are lifted directly
from CULLS.md Sec5's worked example, which the contract itself calls
"illustrative shapes" but which are real, physically-reasoned numbers,
not arbitrary ones; the stability thresholds are lifted from
``docs/design/PHASE4_CULL_DESIGN.md`` Sec 3.3's own grid table (the
"resid < 1.5, lapvar > q30" row, its best-IoU point). ``params.visual``
records ``fit_provenance_note: "defaults, not fitted"`` on top of the
contract's own ``fit_provenance: "default"`` enum value.

Labeling (slice 5's "demote the classifier to a labeller")
-------------------------------------------------------------------------------
Regardless of which of the three paths above decided a segment's
``frame_in``/``frame_out``, :func:`_label_motion_intent` assigns
``motion_intent`` (and ``motion_confidence``) by majority vote, BY FRAME
COUNT, over :mod:`posthouse.cull.classify`'s already-committed per-frame
``state`` array within that already-decided window -- never used to open,
close, split, or reject a candidate, only to name one after the fact.
``shake``/``undecidable`` are excluded from the vote (neither is a legal
``motion_intent`` -- CULLS Sec 4.3's enum, CULLS Sec 7 REJECT 4); ties are
broken toward the lower ``STATE_ID`` (``np.argmax``'s own
first-occurrence-wins behaviour on a tie, the same determinism convention
``classify._hysteresis_smooth`` and this module's own
``_consolidate_hysteresis`` both already use). This is applied uniformly
to every accepted segment from every consolidation path -- "wire the
labeling step in for all modes" (task brief) -- which also changes what
``motion_confidence`` means versus slices 2-4: it is now the label vote's
own frame-count fraction ("how cleanly the window fits that one intent",
CULLS Sec 4.3's own description), not the mean phase-correlation peak
:func:`_score_block` used to write there.

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
    "stability_onset",  # slice 5: CULLS.md Sec4.3 amended to add this value
}
_ENUM_BOUNDARY_OUT = {
    "clip_end", "motion_change", "shake_onset", "focus_lost", "focus_hunt",
    "exposure_fault", "audio_fault", "speech_end", "recompose",
    "stability_loss",  # slice 5: CULLS.md Sec4.3 amended to add this value
}
_ENUM_REJECT_REASON = {
    "shake", "motion_inconsistent", "focus_hunt", "soft", "underexposed",
    "overexposed", "too_short", "audio_clipped", "audio_dead", "no_speech",
    "settle", "transition", "record_tap", "undecidable",
}

# Slice 5 follow-up (2026-09-02 investigation): the legal values of
# ``SegmentParams.stability_combine`` -- see that field's own docstring.
# "dirstab_only" added by the 2026-09-02 direction-stability re-fit
# (ROADMAP Decision Log): a per-clip-normalized circular-statistics
# signal, isolated as its own arm the same way resid_only/lapvar_only
# already are.
_STABILITY_COMBINE_MODES = {"and", "or", "resid_only", "lapvar_only", "score", "dirstab_only"}

# 2026-09-02 Decision Log follow-up (Ryan's ruling, normalize per clip): the
# legal values of ``SegmentParams.stability_resid_norm`` -- see that field's
# own docstring.
_STABILITY_RESID_NORM_MODES = {"absolute", "quantile", "robust_scale"}


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

    # --- consolidation / extent detection (design Sec2.1) -----------------
    consolidation: str = "stability"
    """"stability" (default, slice 5), "hysteresis", or "viterbi" -- see
    module docstring. "stability" is the headline path per the 2026-09-02
    Decision Log; "hysteresis"/"viterbi" are kept reachable for comparison
    (demote, do not delete), never silently dropped."""

    stability_resid_max: float = 1.5
    """px/frame, the (``stability_smooth_sec``-smoothed) motion residual
    cap: a frame is a stability candidate only while smoothed ``resid``
    stays below this. Design Sec3.3's own grid table, "resid < 1.5,
    lapvar > q30" row -- its best-IoU point among the four rows quoted
    there (P 0.701/R 0.775/IoU 0.459), read directly off that table as a
    reasoned unfit default, exactly how this module's other legacy
    defaults were sourced from CULLS Sec5's worked example. Fitted for
    real by :mod:`posthouse.cull.fit`'s stability stage."""

    stability_lapvar_quantile: float = 0.30
    """Per-clip percentile (0..1) of the (smoothed) ``lapvar_norm``
    column: a frame is a stability candidate only while its smoothed
    sharpness clears this clip's own quantile. Same source as
    ``stability_resid_max`` -- design Sec3.3's "q30" row. Per-clip
    relative, not absolute, for the same reason design Sec1.4 gives for
    every other sharpness threshold in this module (measured medians on
    the benchmark clip run 100 to 4890 across accepted selects)."""

    stability_resid_norm: str = "absolute"
    """**2026-09-02 Decision Log: Ryan's ruling on the generalization
    failure.** The Runnells-fitted detector scored BELOW select-everything
    on Des Moines (P 0.317/R 0.714/IoU 0.255 vs baseline 0.338/1.000/0.300)
    because ``stability_resid_max`` is an ABSOLUTE px/frame threshold and
    smoothed motion-residual magnitudes differ by an order of magnitude
    across shoots (Des Moines smoothed resid maxima ~0.5-1.0 px/frame vs
    Runnells' own p99 of 10.90). Ryan's ruling: normalize per clip so the
    threshold adapts to each shoot -- not per-shoot fitting, not
    per-camera scoping. "Normalize per clip" has two meanings that fail
    differently, so both ship as competing, separately selectable
    strategies and measurement (not this docstring) decides which wins:

    * ``"absolute"`` -- **the control arm, unchanged behavior.**
      ``stability_resid_max`` is a raw px/frame cap
      (``resid_smooth < stability_resid_max``). This is the mode that
      failed to transfer; kept as the baseline every normalization
      strategy must beat on the shoot it was NOT fitted on.
    * ``"quantile"`` -- mirrors how ``stability_lapvar_quantile`` already
      works: threshold at a FITTED PER-CLIP QUANTILE of the smoothed
      residual (``stability_resid_quantile``), keeping the bottom
      ``stability_resid_quantile`` fraction of this clip's OWN residual
      distribution (``resid_smooth <= percentile(resid_smooth, 100 *
      stability_resid_quantile)``). Scale-free -- no px/frame number ever
      crosses a shoot boundary. Its known weakness, stated rather than
      hidden: it assumes a roughly constant *fraction* of every clip is
      usable, so a uniformly excellent clip (long, mostly-stable drone
      footage is exactly this case) still loses its top
      ``1 - stability_resid_quantile`` fraction to the threshold even
      though none of it is actually bad.
    * ``"robust_scale"`` -- normalizes the smoothed residual by a per-clip
      ROBUST STATISTIC (median/MAD z-score: ``(resid_smooth -
      median(resid_smooth)) / (1.4826 * MAD(resid_smooth))``, the standard
      normal-consistent MAD scaling) and thresholds the result against
      ``stability_resid_z_max`` in normalized units. Scale-free AND not
      fraction-fixed: a uniformly good clip can stay almost entirely
      selected, a uniformly bad one almost entirely rejected -- neither is
      forced to lose a fixed top fraction the way ``"quantile"`` is.
      Degenerate guard: a clip whose smoothed residual is perfectly
      constant has MAD == 0; ``robust_scale`` treats that clip's z-score
      as exactly 0 everywhere (every frame ties the median by
      construction, so nothing is "abnormal" relative to itself) rather
      than dividing by zero.

    ``stability_resid_max`` is unused (but still validated and carried for
    provenance) when this is ``"quantile"`` or ``"robust_scale"``;
    ``stability_resid_quantile``/``stability_resid_z_max`` are unused
    otherwise. Applies to every ``stability_combine`` mode that actually
    reads the residual gate (``"and"``, ``"or"``, ``"resid_only"``) via
    the shared ``_resid_ok`` helper -- so e.g.
    ``stability_combine="resid_only"`` with
    ``stability_resid_norm="robust_scale"`` is a fully valid, and the
    module's cleanest, isolation of "does per-clip residual normalization
    alone achieve cross-shoot transfer." Does NOT affect
    ``stability_combine="lapvar_only"`` (never reads the residual gate at
    all) or ``stability_combine="score"`` (``_stability_score`` already
    applies its OWN full percentile-rank normalization to the residual,
    independent of this field, and is unused/unaffected by whichever value
    it holds)."""

    stability_resid_quantile: float = 0.70
    """Only used when ``stability_resid_norm == "quantile"``: the per-clip
    fraction of frames (by smoothed residual, lowest = calmest) kept as
    stability candidates. 0.70 is the reasoned unfit default -- the mirror
    image of ``stability_lapvar_quantile``'s default 0.30 (lapvar keeps the
    top ``1 - 0.30 = 0.70`` fraction by sharpness; residual keeps the
    bottom ``0.70`` fraction by calmness), so an unfit clip with "typical"
    signal shape keeps roughly the same overall fraction from either gate
    before ``stability_combine`` decides how they interact. Fitted for
    real by :mod:`posthouse.cull.fit`'s stability stage when this mode is
    selected."""

    stability_resid_z_max: float = 3.0
    """Only used when ``stability_resid_norm == "robust_scale"``: the
    per-clip MAD z-score cap (``(resid_smooth - median) / (1.4826 *
    MAD)``) above which a frame is not a stability candidate. 3.0 is the
    reasoned unfit default -- the conventional "more than 3 robust standard
    deviations above this clip's own typical residual" outlier line,
    scale-free by construction (unlike ``stability_resid_max``, it needs no
    knowledge of a clip's absolute residual magnitude to mean the same
    thing). Fitted for real by :mod:`posthouse.cull.fit`'s stability stage
    when this mode is selected."""

    stability_smooth_sec: float = 0.7
    """Smoothing window, in seconds, applied to both ``resid`` and
    ``lapvar_norm`` before thresholding -- design Sec3.3's own diagnostic
    parameter ("a 0.7s smoothing window"), converted to frames via
    ``round(stability_smooth_sec * fps)`` so it is resolution/fps
    independent. JUDGMENT CALL (flagged, not in the design doc): the
    design's own prose describes only "a 0.7s smoothing window" for the
    probe as a whole and does not say whether lapvar was smoothed by the
    same window or read raw per-frame; this module smooths both signals
    by the same window for consistency and to reduce single-frame
    flicker in the sharpness gate the same way it already reduces
    flicker in the motion gate. See the slice 5 report for the full
    reasoning."""

    stability_combine: str = "and"
    """**Slice 5 follow-up (2026-09-02 Decision Log investigation).** How
    ``stability_resid_max`` and ``stability_lapvar_quantile`` combine into
    the per-frame stable/unstable decision:

    * ``"and"`` -- the ORIGINAL shipped gate: both walls must clear
      (``resid_ok & lapvar_ok``). The investigation found this is worse on
      IoU (0.442-0.446) than motion residual alone (0.455) -- two strong
      individual predictors combined adversarially, and the tell was
      ``stability_resid_max`` fitting to the exact max of its search grid
      even after a 3x widening (it wants to be disabled, i.e. it wants the
      AND to degrade toward resid-only, which the AND structure cannot
      express without literally disabling one arm of itself).
    * ``"or"`` -- either wall alone suffices (``resid_ok | lapvar_ok``).
      The natural non-AND alternative; expected (and measured, see
      :mod:`posthouse.cull.fit`'s ablation) to trade precision for recall
      more aggressively than the AND does, likely past where it is still
      useful -- kept as a real, measured candidate, not assumed inferior.
    * ``"resid_only"`` / ``"lapvar_only"`` -- one signal only, the other
      ignored entirely. First-class ablation arms (task brief point 4),
      not just diagnostic numbers: the investigation's own isolated-signal
      table lives here as reproducible harness output.
    * ``"dirstab_only"`` -- **added 2026-09-02, direction-stability re-fit
      (ROADMAP Decision Log).** Neither ``stability_resid_max`` nor
      ``stability_lapvar_quantile`` is read; the gate is
      :func:`_dirstab_ok` alone, a per-clip-normalized circular-statistics
      signal on the motion vector's DIRECTION rather than its magnitude
      (Ryan's own criterion: a pan developing over a few seconds is
      intentional even at a magnitude a naive resid gate would flag, while
      the same displacement direction-reversing within a window is shake
      wearing a pan's clothes). Reached a real, non-chance AUC of 0.714 on
      the Runnells exhaustive answer key during diagnosis, genuine signal
      but not yet proven to beat ``resid_only`` under this module's own
      fitting harness or to generalize past Runnells -- that measurement
      is this arm's entire reason for existing as a first-class candidate
      rather than a replacement.
    * ``"score"`` -- **the combined-score structure (task brief point 3,
      option b).** Neither signal has to individually clear a wall,
      because there is only one wall: each smoothed signal is converted to
      its own per-clip PERCENTILE RANK (0=worst, 1=best on that signal,
      scale-free by construction -- this is also why this mode cannot
      suffer the same "wants to be disabled" edge-pinning failure the AND
      gate's absolute ``stability_resid_max`` did, since a percentile rank
      has nowhere to run to; its own grid is bounded [0, 1] by
      construction), the two ranks are combined into one score by
      ``stability_score_resid_weight``, and ONE fitted threshold
      (``stability_score_threshold``) decides stable/unstable. See
      :func:`_stability_score` and CULLS Sec4.2's ``params.visual``.

    ``stability_resid_max``/``stability_lapvar_quantile`` are unused (but
    still validated and carried for provenance) when ``stability_combine``
    is ``"score"``; ``stability_score_threshold``/
    ``stability_score_resid_weight`` are unused otherwise."""

    stability_score_threshold: float = 0.5
    """Only used when ``stability_combine == "score"``: a frame is a
    stability candidate while its combined percentile-rank score (see
    ``stability_combine``'s docstring) is ``>=`` this. Scale-free (the
    score is always in [0, 1] by construction), so 0.5 -- "better than the
    clip's own median on the weighted combination" -- is a reasoned
    unfit starting point, not a guess dressed as a number. Fitted for real
    by :mod:`posthouse.cull.fit`'s stability stage when this mode is
    selected."""

    stability_score_resid_weight: float = 0.5
    """Only used when ``stability_combine == "score"``: the weight on the
    motion-residual rank in the combined score; the lapvar rank gets
    ``1 - this``. 0.5 (equal weight) is the reasoned unfit starting point
    -- the investigation found both signals individually strong (IoU
    0.455 resid alone vs 0.420 lapvar alone), close enough that neither is
    presumed dominant before fitting. Fitted for real by
    :mod:`posthouse.cull.fit`'s stability stage when this mode is
    selected."""

    stability_dirstab_max: float = 0.5
    """Only used when ``stability_combine == "dirstab_only"``: a frame is
    a stability candidate while :func:`_direction_instability`'s
    per-frame ``1 - R`` value stays below this (``R`` = the resultant
    length of the moving frames' unit motion vectors inside the window --
    1.0 means every moving frame in the window points the same way, 0.0
    means directions are scattering/reversing). Bounded in [0, 1] by
    construction the same way ``stability_score_threshold`` is, so it
    cannot suffer the AND gate's edge-pinning failure. 0.5 is the reasoned
    unfit starting point (the diagnostic sweep that found AUC 0.714 used
    the SEPARATION the raw signal gives, not a committed operating point);
    fitted for real by :mod:`posthouse.cull.fit`'s stability stage when
    this mode is selected."""

    stability_dirstab_window_sec: float = 1.0
    """Only used when ``stability_combine == "dirstab_only"``: the window,
    in seconds, over which :func:`_direction_instability` computes the
    resultant vector length. 1.0s is not a guess -- it is the value the
    2026-09-02 diagnostic sweep (0.3/0.5/1.0/1.5s x several per-clip floor
    quantiles) found reproduces the reported AUC 0.714 on Runnells at
    ``stability_dirstab_floor_quantile=0.30``; carried here as a fixed
    default rather than a fitted grid dimension because the sweep found
    the result reasonably stable across nearby window sizes (0.5-1.5s),
    unlike the floor quantile, which moved the Runnells number by over
    0.1 across the same range and is the one :mod:`posthouse.cull.fit`
    actually grids."""

    stability_dirstab_floor_quantile: float = 0.30
    """Only used when ``stability_combine == "dirstab_only"``: the
    per-clip motion-SPEED (``hypot(tx, ty)``) percentile, in [0, 1], below
    which a frame is excluded from the window's direction statistics
    entirely (scored 0 instability, not penalized) as "not really
    moving." Per-clip, not absolute, for the identical reason
    ``stability_resid_norm``'s per-clip modes exist -- motion-residual
    magnitudes differ by an order of magnitude across shoots (2026-09-02
    Decision Log), and the diagnostic sweep measured this clip's-own-30th-
    percentile floor at 0.014 to 1.967 px/frame (140x spread) across
    Runnells and Des Moines clips, confirming an absolute floor would not
    cross shoots either. 0.30 mirrors ``stability_lapvar_quantile``'s
    default and is the value the diagnostic sweep found reproduces the
    reported AUC 0.714 on Runnells; :mod:`posthouse.cull.fit` grids this
    for real."""

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
    focus_gate: bool = True
    """Slice 4 addition (task brief): whether the focus quality gate runs
    at all. When ``False``, no frame is ever judged ``soft`` or
    ``focus_hunt`` -- the run pipeline behaves as if every frame passed
    focus, and the six ``focus_*``/``rack_min_ramp_frames`` fields below
    are unused (their values are still carried in ``as_contract_dict()``
    for provenance, but they gate nothing). This is what lets
    ``fit.py`` score "focus gate enabled" against "focus gate disabled"
    on equal footing, per the Lead's finding that the focus gate is the
    dominant error source (rejected 73% of Ryan's marked-usable footage
    for a 24-point recall cost and no precision gain)."""

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
    exposure_gate: bool = True
    """Slice 4 addition (task brief), mirroring ``focus_gate``: whether the
    exposure quality gate runs at all. When ``False``, no frame is ever
    judged ``underexposed``/``overexposed`` and ``clip_low_frac_max``/
    ``clip_high_frac_max`` gate nothing (still carried for provenance)."""

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
        if self.consolidation not in ("stability", "hysteresis", "viterbi"):
            raise ValueError(
                f"consolidation must be 'stability', 'hysteresis', or 'viterbi', got {self.consolidation!r}"
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
        if not (0.0 <= self.stability_lapvar_quantile <= 1.0):
            raise ValueError(
                f"stability_lapvar_quantile must be in [0, 1], got {self.stability_lapvar_quantile}"
            )
        if self.stability_resid_max <= 0:
            raise ValueError(f"stability_resid_max must be > 0, got {self.stability_resid_max}")
        if self.stability_resid_norm not in _STABILITY_RESID_NORM_MODES:
            raise ValueError(
                f"stability_resid_norm must be one of {sorted(_STABILITY_RESID_NORM_MODES)}, "
                f"got {self.stability_resid_norm!r}"
            )
        if not (0.0 <= self.stability_resid_quantile <= 1.0):
            raise ValueError(
                f"stability_resid_quantile must be in [0, 1], got {self.stability_resid_quantile}"
            )
        if self.stability_resid_z_max <= 0:
            raise ValueError(f"stability_resid_z_max must be > 0, got {self.stability_resid_z_max}")
        if self.stability_smooth_sec <= 0:
            raise ValueError(f"stability_smooth_sec must be > 0, got {self.stability_smooth_sec}")
        if self.stability_combine not in _STABILITY_COMBINE_MODES:
            raise ValueError(
                f"stability_combine must be one of {sorted(_STABILITY_COMBINE_MODES)}, "
                f"got {self.stability_combine!r}"
            )
        if not (0.0 <= self.stability_score_threshold <= 1.0):
            raise ValueError(
                f"stability_score_threshold must be in [0, 1], got {self.stability_score_threshold}"
            )
        if not (0.0 <= self.stability_score_resid_weight <= 1.0):
            raise ValueError(
                f"stability_score_resid_weight must be in [0, 1], got {self.stability_score_resid_weight}"
            )
        if not (0.0 <= self.stability_dirstab_max <= 1.0):
            raise ValueError(
                f"stability_dirstab_max must be in [0, 1], got {self.stability_dirstab_max}"
            )
        if self.stability_dirstab_window_sec <= 0:
            raise ValueError(
                f"stability_dirstab_window_sec must be > 0, got {self.stability_dirstab_window_sec}"
            )
        if not (0.0 <= self.stability_dirstab_floor_quantile <= 1.0):
            raise ValueError(
                f"stability_dirstab_floor_quantile must be in [0, 1], "
                f"got {self.stability_dirstab_floor_quantile}"
            )

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


def _score_block(
    f_in: int, f_out: int, *,
    resid: np.ndarray, lapvar_norm: np.ndarray, clip_low: np.ndarray,
    clip_high: np.ndarray, peak: np.ndarray, resid_eps_ref: float,
) -> dict:
    """Per-segment ``scores`` (CULLS Sec4.3): shared between every
    consolidation path (slice 5 -- previously a closure private to
    ``_run_pipeline``, pulled to module scope so :func:`_run_stability_pipeline`
    does not duplicate it). ``motion_confidence`` here is a legacy
    leftover (mean phase-correlation peak) always overwritten by
    :func:`_label_motion_intent`'s own vote-fraction value once labeling
    runs -- see the module docstring's "Labeling" section."""
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


def _label_motion_intent(state: np.ndarray, frame_in: int, frame_out: int) -> tuple[str, float]:
    """Slice 5: the classifier as labeller. Dominant motion class BY FRAME
    COUNT within ``[frame_in, frame_out)`` of the already-classified
    ``state`` array, restricted to legal ``motion_intent`` values --
    ``shake``/``undecidable`` are excluded from the vote entirely (CULLS
    Sec4.3's enum has no room for either; design Sec2.2 point 3 already
    treats them as never-open classes). Ties broken toward the lower
    ``STATE_ID`` via ``np.argmax``'s own first-occurrence-wins behaviour --
    the same determinism convention ``classify._hysteresis_smooth`` and
    this module's own ``_consolidate_hysteresis`` already use, documented
    once here rather than three times.

    Returns ``(motion_intent, motion_confidence)`` where confidence is the
    winning class's own frame-count fraction of the WHOLE window
    (including shake/undecidable frames in the denominator -- a window
    that is 40% pan_right and 60% shake is genuinely less confidently
    "pan_right" than one that is 90% pan_right and 10% shake, and the
    denominator should reflect that).

    Falls back to ``"drift"`` at confidence 0.0 in the degenerate case
    where every frame in the window is shake/undecidable (should not
    happen for a window any detector actually accepted, since a
    stability-accepted window has already cleared the motion-residual
    cap, and a legacy-accepted window already failed the shake class
    gate by construction) -- ``drift`` is design Sec6 Q2's "a consistent
    slow handheld wander with no dominant axis," the closest legal intent
    to "no clean class present."
    """
    n = frame_out - frame_in
    if n <= 0:
        return "drift", 0.0
    window = state[frame_in:frame_out]
    counts = np.bincount(window.astype(np.int64), minlength=len(STATE_NAMES)).astype(np.int64)
    for closed in _CLOSED_CLASSES:
        counts[STATE_ID[closed]] = 0
    if int(counts.sum()) == 0:
        return "drift", 0.0
    winner = int(np.argmax(counts))
    confidence = float(counts[winner]) / n
    return STATE_NAMES[winner], confidence


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
    if params.focus_gate:
        soft_threshold = float(np.percentile(focus_residual, 100.0 * params.focus_norm_quantile))
        soft_frame = focus_residual < soft_threshold
        hunt_rate = _hunt_rate_per_sec(
            focus_residual, fps, params.focus_hunt_smooth_frames, params.focus_hunt_deadband_std,
        )
        hunting_frame = hunt_rate > params.focus_hunt_sign_changes_per_sec
    else:
        # Gate ablation (slice 4 task brief): focus never rejects or
        # splits a candidate -- every frame passes.
        soft_frame = np.zeros(analysed_frames, dtype=bool)
        hunting_frame = np.zeros(analysed_frames, dtype=bool)
    if params.exposure_gate:
        exposure_bad_low = clip_low > params.clip_low_frac_max
        exposure_bad_high = clip_high > params.clip_high_frac_max
    else:
        exposure_bad_low = np.zeros(analysed_frames, dtype=bool)
        exposure_bad_high = np.zeros(analysed_frames, dtype=bool)

    resid_eps_ref = ClassifyParams().resid_eps

    segments: list[_Segment] = []
    rejections: list[_Rejection] = []
    first_accepted_emitted = False

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

            scores = _score_block(
                s_in, s_out, resid=resid, lapvar_norm=lapvar_norm, clip_low=clip_low,
                clip_high=clip_high, peak=peak, resid_eps_ref=resid_eps_ref,
            )

            segments.append(_Segment(
                frame_in=s_in, frame_out=s_out,
                motion_intent="",  # filled in by the shared labeling pass, segment_source()
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


# ---------------------------------------------------------------------------
# Stability threshold detector (slice 5's headline path -- design Sec2.1's
# "crude two-signal probe," now production code, see module docstring)
# ---------------------------------------------------------------------------

def _percentile_rank(x: np.ndarray) -> np.ndarray:
    """Each element's own fraction-of-the-clip rank in [0, 1] (0 = the
    smallest value in ``x``, 1 = the largest), AVERAGE-RANK for ties --
    dependency-free (no scipy in this venv) and, unlike either raw signal,
    inherently bounded: a percentile rank cannot "want" to run past 1.0 the
    way an absolute threshold like ``stability_resid_max`` can keep wanting
    a bigger cap. That boundedness is exactly why :func:`_stability_score`
    (``stability_combine == "score"``) cannot reproduce the AND gate's
    edge-pinning failure (2026-09-02 Decision Log investigation).

    **Bug fixed while building the test for this** (caught by
    ``test_stability_combine_score_lets_a_strong_signal_compensate_a_weak_one``,
    not by inspection): a naive double-argsort breaks ties by ARRAY
    POSITION, not by averaging -- a genuinely constant stretch of a signal
    (a locked-off aerial hold has long constant-lapvar runs; a perfectly
    static tripod shot can have long constant-resid runs) would silently
    get a fake, monotonically increasing rank across the tie purely from
    frame order, which then leaks a spurious time-correlated signal into
    the combined score. Ties now get the AVERAGE of the positions they
    span (the standard ``rankdata(method="average")`` convention), so a
    fully constant array ranks every element at exactly 0.5, not a ramp."""
    n = len(x)
    if n <= 1:
        return np.zeros(n, dtype=np.float64)
    order = np.argsort(x, kind="stable")
    sorted_x = x[order]
    ranks_sorted = np.arange(n, dtype=np.float64)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sorted_x[j + 1] == sorted_x[i]:
            j += 1
        if j > i:
            ranks_sorted[i:j + 1] = (i + j) / 2.0
        i = j + 1
    ranks = np.empty(n, dtype=np.float64)
    ranks[order] = ranks_sorted
    return ranks / (n - 1)


def _stability_score(resid_smooth: np.ndarray, lapvar_smooth: np.ndarray, params: SegmentParams) -> np.ndarray:
    """The combined score for ``stability_combine == "score"`` (task brief
    point 3, option b): a weighted average of two per-clip percentile
    ranks -- how good this frame's motion residual is relative to the rest
    of THIS clip (low resid = good, so the rank is inverted) and how good
    its sharpness is (high lapvar = good) -- rather than requiring both an
    absolute residual wall AND an absolute sharpness wall to be cleared
    independently. One scale-free number in [0, 1]; higher is better."""
    resid_goodness = 1.0 - _percentile_rank(resid_smooth)
    lapvar_goodness = _percentile_rank(lapvar_smooth)
    w = params.stability_score_resid_weight
    return w * resid_goodness + (1.0 - w) * lapvar_goodness


def _robust_z(x: np.ndarray) -> np.ndarray:
    """Per-clip median/MAD z-score: ``(x - median(x)) / (1.4826 *
    MAD(x))``, the standard normal-consistent MAD scaling (1.4826 makes
    the scale estimate agree with the standard deviation for normally
    distributed data). Scale-free by construction -- no absolute px/frame
    number ever crosses a shoot boundary (2026-09-02 Decision Log, Ryan's
    per-clip-normalization ruling).

    **Degenerate-case guard**: a clip whose smoothed residual is perfectly
    constant (a genuinely locked-off, noise-free hold, or -- more likely
    in practice -- a very short clip where the smoothing window collapses
    every frame to the same padded-edge value) has MAD exactly 0. Every
    element then trivially equals the median, so the "honest" z-score is
    0 everywhere (nothing is abnormal relative to itself); this returns
    exactly that rather than dividing by zero / producing NaN or inf,
    which would otherwise poison every downstream threshold comparison
    silently (`nan <= threshold` is always False in numpy, which would
    reject a perfectly stable clip outright -- the opposite of correct)."""
    if len(x) == 0:
        return np.zeros(0, dtype=np.float64)
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med)))
    if mad < 1e-12:
        return np.zeros_like(x, dtype=np.float64)
    return (x - med) / (1.4826 * mad)


def _resid_ok(resid_smooth: np.ndarray, params: SegmentParams) -> tuple[np.ndarray, float, str]:
    """The per-frame motion-residual gate, under whichever
    ``stability_resid_norm`` strategy is selected (``SegmentParams.
    stability_resid_norm``'s own docstring has the full reasoning).
    Returns ``(resid_ok, threshold_value, detail_str)`` -- the threshold
    and a human-readable description of it are both returned because
    rejection detail messages (below) report them regardless of which
    strategy chose them, and because a per-clip-fitted threshold (quantile
    / robust_scale) is only known AFTER seeing this clip's own signal, not
    something a caller can precompute."""
    norm = params.stability_resid_norm
    if norm == "absolute":
        threshold = params.stability_resid_max
        resid_ok = resid_smooth < threshold
        detail = f"resid_smooth < {threshold} px/frame (absolute)"
    elif norm == "quantile":
        threshold = float(np.percentile(resid_smooth, 100.0 * params.stability_resid_quantile))
        resid_ok = resid_smooth <= threshold
        detail = (
            f"resid_smooth <= this clip's own q{params.stability_resid_quantile * 100:.0f} "
            f"= {threshold:.3f} px/frame (per-clip quantile)"
        )
    elif norm == "robust_scale":
        z = _robust_z(resid_smooth)
        threshold = params.stability_resid_z_max
        resid_ok = z <= threshold
        detail = (
            f"(resid_smooth - median) / (1.4826*MAD) <= {threshold} "
            f"(per-clip robust z-score)"
        )
    else:  # pragma: no cover - unreachable, __post_init__ already validated
        raise ValueError(f"unknown stability_resid_norm {norm!r}")
    return resid_ok, threshold, detail


def _direction_instability(
    vx: np.ndarray, vy: np.ndarray, fps: float, params: SegmentParams,
) -> np.ndarray:
    """Per-frame ``1 - R`` (module docstring / ``stability_dirstab_max``):
    circular-statistics direction stability on the RAW (unsmoothed) motion
    vector, added by the 2026-09-02 direction-stability re-fit (ROADMAP
    Decision Log) in response to Ryan's own criterion -- motion should be
    judged by its shape over time, not just its magnitude, and a pan that
    oscillates on the perpendicular axis is shake wearing a pan's clothes
    even when its net displacement looks clean.

    For each frame, a centered window of ``stability_dirstab_window_sec``
    seconds is drawn. Frames in the window whose speed (``hypot(vx,
    vy)``) exceeds THIS CLIP'S OWN ``stability_dirstab_floor_quantile``
    percentile are "moving"; static/near-static frames are excluded from
    the window's angle statistics entirely rather than fed noise (a
    locked-off hold between two pans should not corrupt either pan's
    score). ``R`` is the resultant length of the moving frames' unit
    motion vectors (``|mean(cos theta, sin theta)|``) -- 1.0 means every
    moving frame in the window points the same way (a smooth pan/push/
    diagonal move, whatever axes are involved), 0.0 means direction is
    scattering or reversing. A window with fewer than 3 moving frames
    cannot support a direction judgment and scores 0 instability (treated
    as stable, not penalized for lacking motion to judge) -- the same
    "not enough evidence to condemn" convention :func:`_resid_ok`'s
    siblings use elsewhere in this module.

    Reproduces the diagnostic sweep's reported Runnells AUC (0.714 at
    ``floor_quantile=0.30``, ``window_sec=1.0``) to 3 decimal places;
    see the 2026-09-02 Decision Log entry for the sweep itself."""
    speed = np.hypot(vx, vy)
    floor = float(np.percentile(speed, 100.0 * params.stability_dirstab_floor_quantile))
    theta = np.arctan2(vy, vx)
    moving = speed > floor
    ux = np.where(moving, np.cos(theta), 0.0)
    uy = np.where(moving, np.sin(theta), 0.0)

    n = len(vx)
    w = max(3, int(round(params.stability_dirstab_window_sec * fps)))
    half = w // 2
    out = np.zeros(n, dtype=np.float64)
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        m = moving[lo:hi]
        if int(m.sum()) < 3:
            out[i] = 0.0
            continue
        R = float(np.hypot(ux[lo:hi][m].mean(), uy[lo:hi][m].mean()))
        out[i] = 1.0 - R
    return out


def _dirstab_ok(vx: np.ndarray, vy: np.ndarray, fps: float, params: SegmentParams) -> tuple[np.ndarray, np.ndarray]:
    """The direction-stability gate for ``stability_combine ==
    "dirstab_only"``. Returns ``(dirstab_ok, instability)`` -- the raw
    per-frame instability array is returned alongside the gate so
    rejection-detail messages (below) can report the actual mean value,
    the same convention :func:`_resid_ok` follows for its own threshold."""
    instability = _direction_instability(vx, vy, fps, params)
    return instability < params.stability_dirstab_max, instability


def _stability_stable_mask(
    resid_smooth: np.ndarray, lapvar_smooth: np.ndarray, params: SegmentParams,
    vx: Optional[np.ndarray] = None, vy: Optional[np.ndarray] = None, fps: float = 0.0,
) -> tuple[np.ndarray, float]:
    """The per-frame stable/unstable decision for every
    ``stability_combine`` mode (module docstring / ``SegmentParams.
    stability_combine``'s own docstring) -- the ONE place this decision is
    made, shared by :func:`_run_stability_pipeline` and
    :func:`segment_source`'s diagnostic ``consolidated_runs`` reporting so
    the two can never drift apart. Returns ``(stable_mask,
    lapvar_threshold)`` -- the threshold is returned too because rejection
    detail messages (below) report it even in modes that do not gate on
    it directly. The residual side's own threshold/detail (which strategy
    fired, and its resolved value) is available via :func:`_resid_ok`
    directly for callers that need it (the rejection-detail builder does).

    ``vx``/``vy``/``fps`` are required only when ``stability_combine ==
    "dirstab_only"`` (:func:`_dirstab_ok`'s own inputs); every caller in
    this module already has the raw motion vectors and fps in scope, so
    this is a real requirement enforced by a clear error, not a silent
    None-propagation risk."""
    lapvar_threshold = float(np.percentile(lapvar_smooth, 100.0 * params.stability_lapvar_quantile))
    resid_ok, _resid_threshold, _resid_detail = _resid_ok(resid_smooth, params)
    lapvar_ok = lapvar_smooth >= lapvar_threshold

    combine = params.stability_combine
    if combine == "and":
        stable = resid_ok & lapvar_ok
    elif combine == "or":
        stable = resid_ok | lapvar_ok
    elif combine == "resid_only":
        stable = resid_ok
    elif combine == "lapvar_only":
        stable = lapvar_ok
    elif combine == "dirstab_only":
        if vx is None or vy is None or fps <= 0:
            raise ValueError("stability_combine='dirstab_only' requires vx, vy, and fps")
        stable, _instability = _dirstab_ok(vx, vy, fps, params)
    elif combine == "score":
        stable = _stability_score(resid_smooth, lapvar_smooth, params) >= params.stability_score_threshold
    else:  # pragma: no cover - unreachable, __post_init__ already validated
        raise ValueError(f"unknown stability_combine {combine!r}")
    return stable, lapvar_threshold


def _run_stability_pipeline(
    arrays: dict[str, np.ndarray],
    fps: float,
    analysed_frames: int,
    params: SegmentParams,
) -> tuple[list[_Segment], list[_Rejection]]:
    """Direct, per-frame threshold test on two smoothed signals -- NOT a
    consolidation over classified runs (no settle trim, no class gate:
    design Sec3.3's diagnostic explicitly had "no classification, no
    shape analysis, no settle logic"). The SAME exposure gate as the
    legacy path (:func:`_run_pipeline`, ``params.exposure_gate``) applies
    -- it earned its place. There is deliberately no focus gate here at
    all: focus is only ever computed for the informational ``focus``
    dict on an accepted segment, never used to accept, reject, or split
    one (task brief point 1).

    Rejection-reason judgment call (flagged, see the slice 5 report for
    the full account): the task brief names
    "too_short/transition/underexposed/overexposed" as this detector's
    rejections. A span that fails the stability test is reported as
    ``"transition"`` when it is SHORT (< ``min_duration_sec`` -- design's
    own worked example already uses "transition" for exactly this: the
    0.34s gap between Ryan's #3 and #4) and as ``"motion_inconsistent"``
    when it is not (design's own worked example's OTHER rejection --
    46.45-66.47s of "20.0s of walking coverage" -- uses this exact reason
    for a sustained, non-brief failure of the same underlying test). Both
    are pre-existing CULLS.md Sec4.5 enum values already used by the
    legacy path for materially the same distinction (a brief transition
    vs. a sustained disqualification), so this is read as the more
    defensible generalization of the brief's four-word list rather than
    forcing every non-brief stability failure into a word ("transition")
    whose own contract-worked-example usage is specifically about
    brevity.
    """
    lapvar_norm = arrays["lapvar_norm"].astype(np.float64)
    lapvar = arrays["lapvar"].astype(np.float64)
    luma_mean = arrays["luma_mean"].astype(np.float64)
    clip_low = arrays["clip_low"].astype(np.float64)
    clip_high = arrays["clip_high"].astype(np.float64)
    resid = arrays["resid"].astype(np.float64)
    peak = arrays.get("peak", np.zeros(analysed_frames)).astype(np.float64)
    vx = arrays["tx_norm_src_width"].astype(np.float64)
    vy = arrays["ty_norm_src_width"].astype(np.float64)

    smooth_frames = max(1, int(round(params.stability_smooth_sec * fps)))
    resid_smooth = _classify._smooth(resid, smooth_frames)
    lapvar_smooth = _classify._smooth(lapvar_norm, smooth_frames)

    stable, lapvar_threshold = _stability_stable_mask(resid_smooth, lapvar_smooth, params, vx, vy, fps)

    if params.exposure_gate:
        exposure_bad_low = clip_low > params.clip_low_frac_max
        exposure_bad_high = clip_high > params.clip_high_frac_max
    else:
        exposure_bad_low = np.zeros(analysed_frames, dtype=bool)
        exposure_bad_high = np.zeros(analysed_frames, dtype=bool)

    ok = stable & ~exposure_bad_low & ~exposure_bad_high
    bad_mask = ~ok

    # Focus is informational only in this path -- computed for the
    # `focus` dict, never consulted to accept/reject/split (task brief).
    focus_residual = _focus_residual(vx, vy, lapvar_norm)

    resid_eps_ref = ClassifyParams().resid_eps
    spans = _split_by_mask(0, analysed_frames, bad_mask)

    segments: list[_Segment] = []
    rejections: list[_Rejection] = []

    for span_i, (s_in, s_out, is_bad) in enumerate(spans):
        dur_sec = (s_out - s_in) / fps

        if is_bad:
            exp_low_frac = float(np.mean(exposure_bad_low[s_in:s_out]))
            exp_high_frac = float(np.mean(exposure_bad_high[s_in:s_out]))
            if params.exposure_gate and (exp_low_frac > 0.0 or exp_high_frac > 0.0):
                reason = "underexposed" if exp_low_frac >= exp_high_frac else "overexposed"
                detail = (
                    f"{dur_sec:.2f}s span: clip_{'low' if reason == 'underexposed' else 'high'}"
                    f"_frac exceeds fitted max on {max(exp_low_frac, exp_high_frac) * 100:.0f}% of frames"
                )
            else:
                reason = "transition" if dur_sec < params.min_duration_sec else "motion_inconsistent"
                if params.stability_combine == "score":
                    combine_detail = (
                        f"combine=score: mean score "
                        f"{float(np.mean(_stability_score(resid_smooth, lapvar_smooth, params)[s_in:s_out])):.2f} "
                        f"vs threshold {params.stability_score_threshold} "
                        f"(resid_weight={params.stability_score_resid_weight})"
                    )
                elif params.stability_combine == "dirstab_only":
                    _dirstab_ok_span, instability = _dirstab_ok(vx, vy, fps, params)
                    combine_detail = (
                        f"combine=dirstab_only: mean 1-R instability "
                        f"{float(np.mean(instability[s_in:s_out])):.2f} vs threshold "
                        f"{params.stability_dirstab_max} (window={params.stability_dirstab_window_sec}s, "
                        f"floor_quantile={params.stability_dirstab_floor_quantile})"
                    )
                else:
                    _resid_ok_span, _resid_threshold, resid_detail = _resid_ok(resid_smooth, params)
                    combine_detail = (
                        f"combine={params.stability_combine}, resid_norm={params.stability_resid_norm}: "
                        f"smoothed resid mean {float(np.mean(resid_smooth[s_in:s_out])):.2f} px/frame, "
                        f"gate is {resid_detail}; smoothed lapvar_norm median "
                        f"{float(np.median(lapvar_smooth[s_in:s_out])):.2f} vs this clip's own "
                        f"q{params.stability_lapvar_quantile * 100:.0f} floor {lapvar_threshold:.2f}"
                    )
                detail = f"{dur_sec:.2f}s span fails the stability threshold: {combine_detail}"
            rejections.append(_Rejection(s_in, s_out, reason, detail))
            continue

        if dur_sec < params.min_duration_sec:
            rejections.append(_Rejection(
                s_in, s_out, "too_short",
                f"{dur_sec:.2f}s stable span below min_duration_sec={params.min_duration_sec}",
            ))
            continue

        shape = _focus_shape(focus_residual[s_in:s_out], params.rack_min_ramp_frames)

        def _span_is_exposure(a: int, b: int) -> bool:
            return bool(params.exposure_gate and (
                bool(exposure_bad_low[a:b].any()) or bool(exposure_bad_high[a:b].any())
            ))

        if s_in == 0:
            b_in = "clip_start"
        elif span_i > 0 and spans[span_i - 1][2]:
            prev_s_in, prev_s_out = spans[span_i - 1][0], spans[span_i - 1][1]
            b_in = "exposure_recovered" if _span_is_exposure(prev_s_in, prev_s_out) else "stability_onset"
        else:
            b_in = "stability_onset"

        if s_out == analysed_frames:
            b_out = "clip_end"
        elif span_i < len(spans) - 1:
            next_s_in, next_s_out = spans[span_i + 1][0], spans[span_i + 1][1]
            b_out = "exposure_fault" if _span_is_exposure(next_s_in, next_s_out) else "stability_loss"
        else:
            b_out = "stability_loss"

        scores = _score_block(
            s_in, s_out, resid=resid, lapvar_norm=lapvar_norm, clip_low=clip_low,
            clip_high=clip_high, peak=peak, resid_eps_ref=resid_eps_ref,
        )

        segments.append(_Segment(
            frame_in=s_in, frame_out=s_out,
            motion_intent="",  # filled in by the shared labeling pass, segment_source()
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

    if params.consolidation == "stability":
        # No classified-run consolidation at all (module docstring) --
        # `consolidated_runs` is still populated, purely for the same
        # diagnostic reporting legacy modes give (n_runs,
        # median_run_duration_sec): the RLE of the frame-level
        # stable/unstable mask BEFORE any min-duration filtering, so "how
        # many candidate spans did the threshold test itself produce" is
        # still inspectable the same way "how many consolidated runs" is
        # for the legacy paths.
        segs, rejs = _run_stability_pipeline(arrays, fps, analysed_frames, params)
        smooth_frames = max(1, int(round(params.stability_smooth_sec * fps)))
        resid_smooth = _classify._smooth(arrays["resid"].astype(np.float64), smooth_frames)
        lapvar_smooth = _classify._smooth(arrays["lapvar_norm"].astype(np.float64), smooth_frames)
        diag_vx = arrays["tx_norm_src_width"].astype(np.float64)
        diag_vy = arrays["ty_norm_src_width"].astype(np.float64)
        ok_mask, _lapvar_threshold = _stability_stable_mask(
            resid_smooth, lapvar_smooth, params, diag_vx, diag_vy, fps
        )
        runs = _rle_from_state_array(ok_mask.astype(np.int8))
    else:
        classify_params = ClassifyParams(**(header.get("classify", {}).get("params", {}) or {}))
        if params.consolidation == "hysteresis":
            runs = _consolidate_hysteresis(arrays["state"], fps, params)
        else:
            runs = _consolidate_viterbi(arrays, classify_params, params.viterbi_lambda)
        segs, rejs = _run_pipeline(runs, arrays, fps, analysed_frames, params)

    # Labeling (slice 5, module docstring's "Labeling" section): applied
    # uniformly to every accepted segment from every consolidation path,
    # AFTER extent is fully decided -- the classifier never influences
    # frame_in/frame_out, only the name attached to an already-decided
    # window.
    for seg in segs:
        seg.motion_intent, seg.motion_confidence = _label_motion_intent(
            arrays["state"], seg.frame_in, seg.frame_out,
        )

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
    parser.add_argument("--consolidation", choices=("stability", "viterbi", "hysteresis"), default="stability")
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
