"""posthouse.cull.fit — Phase 4 slices 4-5: the fitting harness.

**Slice 5 addendum (2026-09-02 Decision Log, recorded before this module
was changed so it cannot be rationalized afterwards):** fitted honestly
under the SAME 3-block CV/bootstrap/fixture-guard/ranking-rule machinery
this module already built, a plain 2-parameter stability threshold
detector (motion residual capped, sharpness above a per-clip quantile)
scored **P 0.635 / R 0.881 / IoU 0.428** held-out, beating the full
classify+consolidate+gate pipeline's **P 0.634 / R 0.838 / IoU 0.387**.
Per that finding's own recommendation ("demote, do not delete"), this
module now fits :class:`~posthouse.cull.segment.SegmentParams`'s
``"stability"`` mode (``stability_resid_max``,
``stability_lapvar_quantile`` — exactly the two parameters, see
:data:`STAGE_GRID_STABILITY`) as the HEADLINE arm whose ``params.json``
ships, with the SAME exposure-gate ablation the legacy path already had
(the exposure gate earned its place; kept). The legacy hysteresis/viterbi
arms are still fit and ablated in full, for comparison — nothing from
slices 2-4 is deleted, per "demote, do not delete" — but the module never
lets a legacy arm's numeric ranking override the stability path as the
arm that ships: that call was Ryan's, made once, on the fair-comparison
numbers above, not something re-litigated by this harness's own ranking
rule every run.

``docs/design/PHASE4_CULL_DESIGN.md`` §3 in full (the plan implemented
here) and §1.4; ``docs/contracts/CULLS.md`` §4.2 (the ``params`` /
``fit_provenance`` shape this module writes). Fits :class:`posthouse.
cull.segment.SegmentParams` — the visual ruleset's thresholds — against
the Runnells benchmark (``posthouse.benchmark``), consuming an
already-classified sidecar (:mod:`posthouse.cull.signals` +
:mod:`posthouse.cull.classify` must both already have run; this module
never re-decodes video or re-classifies frames).

Recorded before this module was written, so it cannot be rationalized
afterwards (Decision Log, 2026-09-01): slice 3's full pipeline scored
P 0.628 / R 0.553 / IoU 0.334 — BELOW the crude two-signal probe
(P 0.701 / R 0.775 / IoU 0.459) and above only the select-everything
baseline (P 0.577 / R 1.000 / IoU 0.392). If honest fitting cannot beat
the crude probe, the finding is that the design should be simplified,
not given more parameters — this module's job is to answer that
question honestly, not to manufacture a passing score.

Why staged, block-CV'd, bootstrapped, and gate-ablated (design §3.2)
----------------------------------------------------------------------
26 selects on ONE 235s clip is not enough data to fit ~18 scalars
without overfitting by construction. Four containments, all required:

1. **Staged coordinate descent, ≤4 free parameters per stage**, in the
   design's order — motion/consolidation (sets the boundaries) → focus
   → exposure. Everything not in play for a stage is held fixed at the
   value the previous stage settled on (or a documented-fixed value,
   e.g. ``handle_sec=1.0`` because the scorer's own tolerance is 1.0).
2. **Block cross-validation over TIME, not over selects** — adjacent
   frames are correlated, so leave-one-select-out leaks. The clip is
   split into three contiguous ~78s blocks; each fold fits on two and
   scores on the held-out third; the harness reports the mean AND the
   spread across folds, not a single number.
3. **Block bootstrap** — resample the three per-fold held-out scores
   with replacement to get an honest interval on precision/recall/IoU.
   With only three blocks the interval is expected to be wide; this
   module says so in its report rather than hiding it.
4. **Fixture-ordering guards that cannot be overfitted** (design §3.4) —
   the safety-net fixtures (``stable``/``shaky``/``blurred``/
   ``underexposed``/``overexposed.mp4``) give sign checks with no
   connection to the benchmark's own score: shaky must show higher
   motion residual than stable, blurred lower lapvar than stable, and
   (the fit-DEPENDENT half of the check) underexposed/overexposed must
   still get flagged more than stable by whatever ``clip_low_frac_max``/
   ``clip_high_frac_max`` the fit chose. A parameter set that inverts
   one of these is rejected regardless of its benchmark score —
   :func:`check_fixture_orderings`.

Gate ablation is first-class (task brief), not an afterthought: every
quality gate (focus, exposure) and every consolidation path
(hysteresis, viterbi) is fit and scored BOTH with the gate/path enabled
and with it disabled/swapped, and the report states a verdict per gate —
"earns its place" or "does not earn its place, remove it" — using the
SAME held-out, recall-first-subject-to-a-precision-floor ranking rule
the rest of this module uses (:func:`arm_rank_key`). To bound runtime
(this module runs on Ryan's Mac, per-candidate cost is a full
:func:`~posthouse.cull.segment.segment_source` call, and staged
coordinate descent already means O(hundreds) of candidates per arm),
the two consolidation paths are BOTH fully block-CV'd at full-gates,
and the two gate ablations (focus off, exposure off) are then run only
against the winning consolidation path — every arm that DOES run is
independently 3-fold CV'd end to end; nothing about the honesty of any
one arm's number is shortcut, only which combinations get the full
treatment. This is stated in the fit report, not left implicit.

Ranking rule (ROADMAP §5, design §3.3): **recall-first subject to a
precision floor**, never by IoU. A candidate/arm/fold is ranked by
``(meets_precision_floor, recall, precision)`` descending — anything
clearing the floor beats anything that does not, and among those that
clear it, higher recall wins. ``PRECISION_FLOOR`` defaults to 0.60
(design §3.3: "precision below ~0.6 stops being useful... 0.70 is the
floor, not the goal" for a SHIPPED pipeline; 0.60 is used here as the
search floor so the coordinate descent is not locked out of a region of
parameter space it needs to explore, and the final chosen arm's actual
precision is reported plainly either way — nothing is hidden behind the
floor).

Entry points
------------
* Python API: :func:`fit` (returns a :class:`FitReport`, writes
  ``params.json`` and ``fit_report.json`` under ``out_dir``).
* CLI: ``python -m posthouse.cull.fit --sidecar S --answer-key K
  --out DIR [--stages motion,focus,exposure] [--precision-floor F]
  [--passes N] [--seed N]`` — non-zero exit, every problem listed.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from posthouse import benchmark as bm
from posthouse._util import atomic_write_bytes, now_iso
from posthouse.cull.segment import (
    SegmentError,
    SegmentParams,
    SegmentResult,
    SegmentValidationError,
    TilingInvariantError,
    segment_source,
)

FIT_VERSION = "0.1.0"
PRECISION_FLOOR_DEFAULT = 0.60
RANKING_RULE = (
    "recall-first subject to a precision floor: rank by "
    "(precision >= floor, recall, precision) descending; a candidate that "
    "clears the floor always outranks one that does not, and among those "
    "that clear it the higher-recall one wins. Never ranked by IoU "
    "(ROADMAP Sec5)."
)

FIXTURE_NAMES = ("stable", "shaky", "blurred", "underexposed", "overexposed")

# Recorded 2026-09-01 in ROADMAP.md's Decision Log, before this module was
# written, so it cannot be rationalized after the fact (task brief).
# NOTE (2026-09-02, see the module docstring's slice 5 addendum): these
# ORIGINAL numbers compared a HELD-OUT fitted pipeline against the crude
# probe's OWN IN-SAMPLE score -- an unfair comparison the Lead caught and
# corrected the same day. Kept here, unmodified, as the historical record
# of what slice 4 actually reported (demote, do not delete); the FAIR,
# apples-to-apples numbers that slice 5 is measured against are
# CRUDE_PROBE_FAIR_CV / FITTED_PIPELINE_SLICES_2_4_FAIR_CV below.
CRUDE_PROBE = {"precision": 0.701, "recall": 0.775, "iou": 0.459}
BASELINE_SELECT_ALL = {"precision": 0.577, "recall": 1.000, "iou": 0.392}
BASELINE_SLICE3_DEFAULT = {"precision": 0.628, "recall": 0.553, "iou": 0.334}

# Recorded 2026-09-02 in ROADMAP.md's Decision Log (the slice 4 fair
# comparison, and this module's slice 5 task brief): the crude probe
# RE-FITTED under the identical 3-block CV / bootstrap / precision-floor
# scheme this module already applies to every other arm, and the fitted
# full pipeline's own held-out score under that same scheme. This is the
# honest bar slice 5's stability arm is measured against, not CRUDE_PROBE
# above.
CRUDE_PROBE_FAIR_CV = {"precision": 0.635, "recall": 0.881, "f1": 0.737, "iou": 0.428}
FITTED_PIPELINE_SLICES_2_4_FAIR_CV = {"precision": 0.634, "recall": 0.838, "f1": 0.710, "iou": 0.387}


class FitError(Exception):
    """Base class for fitting-harness failures."""


class FitValidationError(FitError):
    """Raised with every input problem listed, not just the first (same
    exhaustive-validation convention as ``segment.SegmentValidationError``
    and ``benchmark.CullsLoadError``)."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        message = "Fit input validation failed:\n" + "\n".join(f"  - {p}" for p in problems)
        super().__init__(message)


# ---------------------------------------------------------------------------
# Blocks (design Sec3.2 point 2: contiguous over TIME, not over selects)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Block:
    index: int
    start_sec: float
    end_sec: float

    @property
    def duration_sec(self) -> float:
        return self.end_sec - self.start_sec


def make_blocks(duration_sec: float, n_blocks: int = 3) -> list[Block]:
    if duration_sec <= 0 or n_blocks < 2:
        raise ValueError(f"make_blocks needs duration_sec > 0 and n_blocks >= 2, got {duration_sec}, {n_blocks}")
    edges = [duration_sec * i / n_blocks for i in range(n_blocks + 1)]
    return [Block(i, edges[i], edges[i + 1]) for i in range(n_blocks)]


# ---------------------------------------------------------------------------
# Ranges: predicted segments / truth clipped to a set of blocks
# ---------------------------------------------------------------------------

def _segment_result_to_ranges(result: SegmentResult, source_path: str, ruleset: str = "visual") -> list[bm.Range]:
    return [
        bm.Range(
            source_path=source_path,
            in_sec=s.frame_in / result.fps,
            out_sec=s.frame_out / result.fps,
            ruleset=ruleset,
        )
        for s in result.segments
    ]


def _clip_ranges(ranges: list[bm.Range], start: float, end: float) -> list[bm.Range]:
    out: list[bm.Range] = []
    for r in ranges:
        s, e = max(r.in_sec, start), min(r.out_sec, end)
        if e > s:
            out.append(bm.Range(source_path=r.source_path, in_sec=s, out_sec=e, source_basename=r.source_basename))
    return out


def ranges_in_blocks(ranges: list[bm.Range], blocks: list[Block]) -> list[bm.Range]:
    """Every range in ``ranges``, clipped to whichever of ``blocks`` it
    falls in — a predicted segment spanning a block boundary is split, so
    a fold only ever gets credit (or blame) for the seconds that actually
    fall inside the blocks it is allowed to see."""
    out: list[bm.Range] = []
    for b in blocks:
        out.extend(_clip_ranges(ranges, b.start_sec, b.end_sec))
    return out


# ---------------------------------------------------------------------------
# Metrics / evaluation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Metrics:
    precision: float
    recall: float
    f1: float
    iou: float
    predicted_sec: float
    truth_sec: float

    def as_dict(self) -> dict:
        return asdict(self)


def _metrics_from_score_block(sb: "bm.ScoreBlock") -> Metrics:
    return Metrics(sb.precision, sb.recall, sb.f1, sb.iou, sb.predicted_sec, sb.truth_sec)


NULL_METRICS = Metrics(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


class Evaluator:
    """Scores a candidate :class:`SegmentParams` against a fixed truth set,
    restricted to a given list of blocks. One already-classified sidecar,
    reused for every candidate — ``segment_source`` re-runs consolidation
    and the gate pipeline in pure numpy over the SAME arrays; nothing is
    re-decoded or re-classified per candidate."""

    def __init__(self, sidecar_path: Path, source_path: str, truth_all: list[bm.Range]):
        self.sidecar_path = Path(sidecar_path)
        self.source_path = source_path
        self.truth_all = truth_all
        self._cache: dict[tuple, Optional[SegmentResult]] = {}

    def _run(self, params: SegmentParams) -> Optional[SegmentResult]:
        key = tuple(sorted(asdict(params).items()))
        if key in self._cache:
            return self._cache[key]
        try:
            result = segment_source(self.sidecar_path, params=params, ruleset="visual")
        except (SegmentValidationError, TilingInvariantError, SegmentError):
            result = None
        self._cache[key] = result
        return result

    def score(self, params: SegmentParams, blocks: list[Block]) -> Metrics:
        result = self._run(params)
        if result is None:
            return NULL_METRICS
        pred = ranges_in_blocks(_segment_result_to_ranges(result, self.source_path), blocks)
        truth = ranges_in_blocks(self.truth_all, blocks)
        sb = bm.score(pred, truth, handle_tolerance_sec=params.handle_sec).overall
        return _metrics_from_score_block(sb)


# ---------------------------------------------------------------------------
# Ranking rule (ROADMAP Sec5: recall-first subject to a precision floor)
# ---------------------------------------------------------------------------

def rank_key(m: Metrics, precision_floor: float) -> tuple:
    return (1 if m.precision >= precision_floor else 0, m.recall, m.precision)


def select_best(
    candidates: list[tuple[SegmentParams, Metrics]], precision_floor: float,
) -> tuple[SegmentParams, Metrics]:
    return max(candidates, key=lambda c: rank_key(c[1], precision_floor))


# ---------------------------------------------------------------------------
# Staged coordinate descent (design Sec3.2 point 1)
# ---------------------------------------------------------------------------

# Grids are reasoned spans around the CULLS Sec5 worked-example defaults
# segment.py already ships (see SegmentParams field docstrings), NOT
# arbitrary — each grid brackets the shipped default so the search can
# move either direction from it. Kept to 4 or fewer free parameters per
# stage per design Sec3.2 point 1.
STAGE_GRID_MOTION_HYSTERESIS: dict[str, list] = {
    "min_run_sec": [0.6, 0.8, 1.0, 1.2, 1.5],
    "min_duration_sec": [1.0, 1.15, 1.3, 1.5],
    "settle_frames": [4, 6, 8, 10, 12],
}
STAGE_GRID_MOTION_VITERBI: dict[str, list] = {
    "viterbi_lambda": [3.0, 5.0, 7.5, 10.0, 15.0],
    "min_duration_sec": [1.0, 1.15, 1.3, 1.5],
    "settle_frames": [4, 6, 8, 10, 12],
}
STAGE_GRID_FOCUS: dict[str, list] = {
    "focus_norm_quantile": [0.15, 0.25, 0.35, 0.45, 0.55],
    "focus_hunt_sign_changes_per_sec": [1.5, 2.0, 2.4, 3.0, 4.0],
    "focus_hunt_deadband_std": [0.10, 0.15, 0.20, 0.30],
}
STAGE_GRID_EXPOSURE: dict[str, list] = {
    "clip_low_frac_max": [0.15, 0.20, 0.25, 0.31, 0.40],
    "clip_high_frac_max": [0.03, 0.06, 0.09, 0.12],
}

# Slice 5: the stability detector's own "motion" stage. Exactly the two
# parameters the task brief names ("staged, at most 4 params" — this
# stage uses 2), a 5x5 = 25-point grid bracketing the SegmentParams
# defaults (design Sec3.3's own worked row, "resid < 1.5, lapvar > q30")
# in both directions, matching the "25-point grid" the Decision Log's
# fair-comparison refit of the crude probe itself used.
STAGE_GRID_STABILITY: dict[str, list] = {
    "stability_resid_max": [0.8, 1.0, 1.2, 1.5, 2.0],
    "stability_lapvar_quantile": [0.10, 0.20, 0.30, 0.40, 0.50],
}

_STAGE_GRIDS = {"focus": STAGE_GRID_FOCUS, "exposure": STAGE_GRID_EXPOSURE}


def _apply(params: SegmentParams, name: str, value) -> SegmentParams:
    if name == "settle_frames":
        # settle_frames_static is fixed BY CONSTRUCTION at half of
        # settle_frames (segment.py's own documented default ratio) — it
        # is never searched independently (design Sec3.2 point 1's "held
        # at values fixed by construction").
        return replace(params, settle_frames=int(value), settle_frames_static=max(1, round(value / 2)))
    return replace(params, **{name: value})


@dataclass
class StageTrace:
    stage: str
    chosen: dict
    n_candidates_tried: int


def coordinate_descent(
    base: SegmentParams,
    grid: dict[str, list],
    eval_fn: Callable[[SegmentParams], Metrics],
    precision_floor: float,
    passes: int = 1,
) -> tuple[SegmentParams, Metrics, list[StageTrace]]:
    """One stage of design Sec3.2 point 1's coordinate descent: cycle
    through ``grid``'s parameters (in dict order), holding everything else
    fixed at the current best, replacing the current best whenever a grid
    value beats it under :func:`rank_key`. ``passes`` > 1 repeats the
    cycle until a fixed point or the pass budget is spent."""
    current = base
    current_m = eval_fn(current)
    traces: list[StageTrace] = []
    for _ in range(max(1, passes)):
        improved = False
        for name, values in grid.items():
            candidates = [(current, current_m)]
            for v in values:
                cand = _apply(current, name, v)
                candidates.append((cand, eval_fn(cand)))
            best_p, best_m = select_best(candidates, precision_floor)
            if best_m is not current_m and rank_key(best_m, precision_floor) > rank_key(current_m, precision_floor):
                current, current_m = best_p, best_m
                improved = True
            traces.append(StageTrace(
                stage=name, chosen={name: getattr(current, "settle_frames" if name == "settle_frames" else name)},
                n_candidates_tried=len(candidates),
            ))
        if not improved:
            break
    return current, current_m, traces


def fit_one(
    consolidation: str,
    focus_gate: bool,
    exposure_gate: bool,
    evaluator: Evaluator,
    train_blocks: list[Block],
    precision_floor: float,
    passes: int,
    stages: tuple[str, ...],
) -> tuple[SegmentParams, Metrics, dict]:
    """One staged fit (design Sec3.2 point 1's full stage order — motion
    first because it sets the boundaries, then focus, then exposure) on
    ``train_blocks`` only.

    Slice 5: ``consolidation == "stability"`` forces ``focus_gate=False``
    regardless of the caller's own ``focus_gate`` argument -- the
    stability path has no focus gate to fit or ablate at all (task brief
    point 1: focus is never a boundary input under this path), so the
    "focus" stage is unconditionally skipped for it and its own "motion"
    stage fits :data:`STAGE_GRID_STABILITY`'s two parameters
    (``stability_resid_max``, ``stability_lapvar_quantile``) in place of
    the legacy consolidation grids. The "exposure" stage is unchanged --
    the stability path keeps the same exposure gate as the legacy paths.
    """
    is_stability = consolidation == "stability"
    base = SegmentParams(
        consolidation=consolidation,
        focus_gate=False if is_stability else focus_gate,
        exposure_gate=exposure_gate,
    )
    eval_fn = lambda p: evaluator.score(p, train_blocks)  # noqa: E731

    trace: dict[str, list[StageTrace]] = {"motion": [], "focus": [], "exposure": []}

    if "motion" in stages:
        if is_stability:
            motion_grid = STAGE_GRID_STABILITY
        elif consolidation == "viterbi":
            motion_grid = STAGE_GRID_MOTION_VITERBI
        else:
            motion_grid = STAGE_GRID_MOTION_HYSTERESIS
        params, metrics, trace["motion"] = coordinate_descent(base, motion_grid, eval_fn, precision_floor, passes)
    else:
        params, metrics = base, eval_fn(base)

    if params.focus_gate and "focus" in stages:
        params, metrics, trace["focus"] = coordinate_descent(params, STAGE_GRID_FOCUS, eval_fn, precision_floor, passes)

    if exposure_gate and "exposure" in stages:
        params, metrics, trace["exposure"] = coordinate_descent(
            params, STAGE_GRID_EXPOSURE, eval_fn, precision_floor, passes,
        )

    return params, metrics, trace


# ---------------------------------------------------------------------------
# Block CV + bootstrap (design Sec3.2 points 2-3)
# ---------------------------------------------------------------------------

@dataclass
class FoldResult:
    held_out_block: int
    params: SegmentParams
    train_metrics: Metrics
    held_out_metrics: Metrics


@dataclass
class ArmResult:
    name: str
    consolidation: str
    focus_gate: bool
    exposure_gate: bool
    folds: list  # list[FoldResult]
    final_params: SegmentParams  # fit on ALL blocks — what would ship
    final_in_sample_metrics: Metrics
    mean_held_out: dict  # {"precision":..,"recall":..,"f1":..,"iou":..}
    spread_held_out: dict  # max - min across folds, same keys
    bootstrap: dict  # {"precision": {"lo":..,"hi":..,"width":..}, ...}


def block_bootstrap(held_arrays: dict[str, np.ndarray], n_resamples: int, seed: int) -> dict:
    """Block bootstrap (design Sec3.2 point 3): resample the per-fold
    held-out scores WITH REPLACEMENT and report a percentile interval.
    With only three blocks the interval is expected to be wide — that is
    reported, not smoothed over."""
    rng = np.random.default_rng(seed)
    n = len(next(iter(held_arrays.values())))
    out = {}
    idx_all = rng.integers(0, n, size=(n_resamples, n))
    for k, arr in held_arrays.items():
        means = arr[idx_all].mean(axis=1)
        lo, hi = float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
        out[k] = {"lo": lo, "hi": hi, "width": hi - lo}
    return out


def run_arm(
    name: str,
    consolidation: str,
    focus_gate: bool,
    exposure_gate: bool,
    evaluator: Evaluator,
    blocks: list[Block],
    precision_floor: float,
    passes: int,
    stages: tuple[str, ...],
    n_bootstrap: int,
    seed: int,
) -> ArmResult:
    folds: list[FoldResult] = []
    for held_out in blocks:
        train_blocks = [b for b in blocks if b.index != held_out.index]
        params, train_m, _trace = fit_one(
            consolidation, focus_gate, exposure_gate, evaluator, train_blocks, precision_floor, passes, stages,
        )
        held_m = evaluator.score(params, [held_out])
        folds.append(FoldResult(held_out.index, params, train_m, held_m))

    final_params, final_m, _trace = fit_one(
        consolidation, focus_gate, exposure_gate, evaluator, blocks, precision_floor, passes, stages,
    )

    metric_names = ("precision", "recall", "f1", "iou")
    held_arrays = {k: np.array([getattr(f.held_out_metrics, k) for f in folds]) for k in metric_names}
    mean_held = {k: float(np.mean(v)) for k, v in held_arrays.items()}
    spread_held = {k: float(np.max(v) - np.min(v)) for k, v in held_arrays.items()}
    boot = block_bootstrap(held_arrays, n_bootstrap, seed)

    return ArmResult(
        name=name, consolidation=consolidation, focus_gate=focus_gate, exposure_gate=exposure_gate,
        folds=folds, final_params=final_params, final_in_sample_metrics=final_m,
        mean_held_out=mean_held, spread_held_out=spread_held, bootstrap=boot,
    )


def arm_rank_key(arm: ArmResult, precision_floor: float) -> tuple:
    """Same ranking rule as :func:`rank_key`, applied to an arm's MEAN
    HELD-OUT score (never the in-sample fit) — this is what selects the
    overall winner and every gate/consolidation verdict."""
    return (
        1 if arm.mean_held_out["precision"] >= precision_floor else 0,
        arm.mean_held_out["recall"],
        arm.mean_held_out["precision"],
    )


def gate_verdict(on_arm: ArmResult, off_arm: ArmResult, precision_floor: float) -> str:
    if arm_rank_key(on_arm, precision_floor) >= arm_rank_key(off_arm, precision_floor):
        return "earns its place"
    return "does not earn its place — recommend removing"


# ---------------------------------------------------------------------------
# Fixture-ordering guard (design Sec3.2 point 4 / Sec3.4)
# ---------------------------------------------------------------------------

def load_fixture_arrays(fixtures_dir: Path, work_dir: Path) -> dict[str, dict[str, np.ndarray]]:
    """Extract + classify the five safety-net fixtures if not already done
    (they are short synthetic clips; extraction is seconds, not minutes),
    returning ``{fixture_name: {array_name: np.ndarray}}``. Raises
    :class:`FitValidationError` listing any fixture that cannot be found."""
    from posthouse.cull.classify import classify_sidecar
    from posthouse.cull.signals import extract_signals, sidecar_paths

    fixtures_dir = Path(fixtures_dir)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    problems: list[str] = []
    out: dict[str, dict[str, np.ndarray]] = {}
    for name in FIXTURE_NAMES:
        media_path = fixtures_dir / f"{name}.mp4"
        if not media_path.exists():
            problems.append(f"fixture not found: {media_path}")
            continue
        npz_path, json_path = sidecar_paths(media_path, work_dir)
        if not npz_path.exists() or not json_path.exists():
            extract_signals(media_path, work_dir)
        with np.load(npz_path) as npz:
            has_state = "state" in npz.files
        if not has_state:
            classify_sidecar(npz_path, out_dir=work_dir)
        with np.load(npz_path) as npz:
            out[name] = {k: npz[k] for k in npz.files}
    if problems:
        raise FitValidationError(problems)
    return out


def check_fixture_orderings(fixture_arrays: dict[str, dict[str, np.ndarray]], params: SegmentParams) -> list[str]:
    """Design Sec3.2 point 4 / Sec3.4: sign checks that have NO connection
    to the benchmark score, so they cannot be overfit by chasing it. Two
    kinds:

    * Raw-signal orderings (shaky resid/hf_energy > stable; blurred
      lapvar < stable; underexposed/overexposed LEAD the clip_low/
      clip_high p90 across all five fixtures) — these hold regardless of
      ``params`` (they are facts about the fixtures' own signals, already
      guarded once at slice 1 in ``test_cull_signals.py``); re-checked
      here so a fitted parameter set is judged against the same anchors,
      not just the pipeline that produced the signals.
    * Fit-DEPENDENT orderings (only when the relevant gate is enabled):
      the FITTED ``clip_low_frac_max``/``clip_high_frac_max`` must still
      flag underexposed/overexposed more often than stable — a threshold
      search could in principle land somewhere degenerate (too loose to
      flag anything, or flagging everything equally) and this is the
      check that catches it.

    Returns every problem found (exhaustive, not just the first); an
    empty list means the parameter set passes.
    """
    problems: list[str] = []

    def median(name: str, col: str) -> float:
        return float(np.median(fixture_arrays[name][col]))

    def p90(name: str, col: str) -> float:
        return float(np.percentile(fixture_arrays[name][col], 90))

    shaky_resid, stable_resid = median("shaky", "resid"), median("stable", "resid")
    if not (shaky_resid > stable_resid):
        problems.append(f"shaky resid median {shaky_resid:.3f} does not exceed stable's {stable_resid:.3f}")

    shaky_hf, stable_hf = median("shaky", "hf_energy"), median("stable", "hf_energy")
    if not (shaky_hf > stable_hf):
        problems.append(f"shaky hf_energy median {shaky_hf:.3f} does not exceed stable's {stable_hf:.3f}")

    blurred_lv, stable_lv = median("blurred", "lapvar"), median("stable", "lapvar")
    if not (blurred_lv < stable_lv):
        problems.append(f"blurred lapvar median {blurred_lv:.1f} is not below stable's {stable_lv:.1f}")

    low_p90 = {n: p90(n, "clip_low") for n in fixture_arrays}
    if max(low_p90, key=low_p90.get) != "underexposed":
        problems.append(f"underexposed does not lead clip_low p90 across fixtures: {low_p90}")

    high_p90 = {n: p90(n, "clip_high") for n in fixture_arrays}
    if max(high_p90, key=high_p90.get) != "overexposed":
        problems.append(f"overexposed does not lead clip_high p90 across fixtures: {high_p90}")

    if params.exposure_gate:
        frac_low = {n: float(np.mean(fixture_arrays[n]["clip_low"] > params.clip_low_frac_max)) for n in fixture_arrays}
        if not (frac_low["underexposed"] > frac_low["stable"]):
            problems.append(
                f"fitted clip_low_frac_max={params.clip_low_frac_max} flags underexposed "
                f"({frac_low['underexposed']:.3f} of frames) no more than stable "
                f"({frac_low['stable']:.3f}) — the exposure gate would not distinguish them"
            )
        frac_high = {n: float(np.mean(fixture_arrays[n]["clip_high"] > params.clip_high_frac_max)) for n in fixture_arrays}
        if not (frac_high["overexposed"] > frac_high["stable"]):
            problems.append(
                f"fitted clip_high_frac_max={params.clip_high_frac_max} flags overexposed "
                f"({frac_high['overexposed']:.3f}) no more than stable ({frac_high['stable']:.3f})"
            )

    return problems


# ---------------------------------------------------------------------------
# Top-level fit()
# ---------------------------------------------------------------------------

def _resolve_sidecar_paths(sidecar: Path) -> tuple[Path, Path]:
    p = Path(sidecar)
    if not p.name.endswith(".signals.npz"):
        raise FitValidationError([f"--sidecar must be a *.signals.npz path, got {p}"])
    return p, p.with_name(p.name[: -len(".npz")] + ".json")


@dataclass
class FitReport:
    fit_id: str
    created_at: str
    sidecar: str
    answer_key: str
    source_path: str
    duration_sec: float
    blocks: list
    precision_floor: float
    ranking_rule: str
    arms: dict  # name -> ArmResult
    winner_consolidation: str
    overall_winner: str
    fixture_guard: dict
    baselines: dict
    decisive: dict

    def to_json_dict(self) -> dict:
        def arm_dict(a: ArmResult) -> dict:
            return {
                "name": a.name, "consolidation": a.consolidation,
                "focus_gate": a.focus_gate, "exposure_gate": a.exposure_gate,
                "folds": [
                    {
                        "held_out_block": f.held_out_block,
                        "params": asdict(f.params),
                        "train_metrics": f.train_metrics.as_dict(),
                        "held_out_metrics": f.held_out_metrics.as_dict(),
                    }
                    for f in a.folds
                ],
                "final_params": asdict(a.final_params),
                "final_in_sample_metrics": a.final_in_sample_metrics.as_dict(),
                "mean_held_out": a.mean_held_out,
                "spread_held_out": a.spread_held_out,
                "bootstrap_95pct_interval": a.bootstrap,
            }
        return {
            "fit_id": self.fit_id, "created_at": self.created_at,
            "generator": {"name": "posthouse.cull.fit", "version": FIT_VERSION, "numpy_version": np.__version__},
            "sidecar": self.sidecar, "answer_key": self.answer_key,
            "source_path": self.source_path, "duration_sec": self.duration_sec,
            "blocks": [asdict(b) for b in self.blocks],
            "precision_floor": self.precision_floor, "ranking_rule": self.ranking_rule,
            "arms": {name: arm_dict(a) for name, a in self.arms.items()},
            "winner_consolidation": self.winner_consolidation,
            "overall_winner": self.overall_winner,
            "fixture_guard": self.fixture_guard,
            "baselines": self.baselines,
            "decisive": self.decisive,
            "generalization_caveat": (
                "Fitted on ONE clip (one camera, one operator, one property, "
                "one lighting condition, one morning). Block CV over time and "
                "the block bootstrap give an honest estimate of how the FIT "
                "PROCEDURE generalizes across time within this clip; neither "
                "establishes, and neither claims, that these numbers transfer "
                "to a different camera, operator, lighting, or subject "
                "(design Sec3.2)."
            ),
        }


def _deterministic_fit_id(*parts: str) -> str:
    import hashlib
    h = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"fit-{h[:12]}"


def fit(
    sidecar: Path | str,
    answer_key: Path | str,
    out_dir: Path | str,
    *,
    fixtures_dir: Optional[Path | str] = None,
    precision_floor: float = PRECISION_FLOOR_DEFAULT,
    n_blocks: int = 3,
    passes: int = 2,
    seed: int = 0,
    n_bootstrap: int = 5000,
    stages: tuple[str, ...] = ("motion", "focus", "exposure"),
    benchmark_id: str = "runnells-day-1",
) -> FitReport:
    """Run the full slice-4 fitting harness and write ``params.json`` +
    ``fit_report.json`` under ``out_dir``. See the module docstring for
    the full method. Raises :class:`FitValidationError` listing every
    input problem."""
    npz_path, json_path = _resolve_sidecar_paths(Path(sidecar))
    ak_path = Path(answer_key)

    problems: list[str] = []
    if not npz_path.exists():
        problems.append(f"sidecar npz not found: {npz_path}")
    if not json_path.exists():
        problems.append(f"sidecar json not found: {json_path}")
    if not ak_path.exists():
        problems.append(f"answer key not found: {ak_path}")
    if problems:
        raise FitValidationError(problems)

    header = json.loads(json_path.read_text())
    source_path = header.get("source", {}).get("path", "")
    duration_sec = float(header.get("source", {}).get("duration_sec") or 0.0)
    with np.load(npz_path) as npz:
        has_state = "state" in npz.files
    if not has_state:
        problems.append(f"sidecar {npz_path} has not been classified (no 'state' array); run posthouse.cull.classify first")
    if duration_sec <= 0:
        problems.append(f"sidecar {npz_path} has no usable duration_sec")
    if not source_path:
        problems.append(f"sidecar {npz_path} header has no source.path")
    if problems:
        raise FitValidationError(problems)

    truth_all = bm.parse_answer_key_xml(ak_path)
    blocks = make_blocks(duration_sec, n_blocks)
    evaluator = Evaluator(npz_path, source_path, truth_all)

    arms: dict[str, ArmResult] = {}

    # --- slice 5's headline arms: the stability detector -----------------
    # focus_gate=False is forced by fit_one() itself for consolidation ==
    # "stability" regardless of what is passed here; passed False
    # explicitly anyway so a reader of this call site does not have to
    # know that to understand it.
    arms["stability_full"] = run_arm(
        "stability_full", "stability", False, True,
        evaluator, blocks, precision_floor, passes, stages, n_bootstrap, seed,
    )
    arms["stability_no_exposure"] = run_arm(
        "stability_no_exposure", "stability", False, False,
        evaluator, blocks, precision_floor, passes, stages, n_bootstrap, seed,
    )

    # --- legacy arms, kept and fully fit for comparison -------------------
    # "demote, do not delete" (task brief): slices 2-4's classify+
    # consolidate+gate pipeline is still fit and ablated in full every run,
    # so the comparison this module reports is a live, re-measured one,
    # not a frozen historical claim.
    for consolidation in ("hysteresis", "viterbi"):
        arms[f"{consolidation}_full"] = run_arm(
            f"{consolidation}_full", consolidation, True, True,
            evaluator, blocks, precision_floor, passes, stages, n_bootstrap, seed,
        )

    legacy_winner_consolidation = max(
        ("hysteresis", "viterbi"),
        key=lambda c: arm_rank_key(arms[f"{c}_full"], precision_floor),
    )

    arms[f"{legacy_winner_consolidation}_no_focus"] = run_arm(
        f"{legacy_winner_consolidation}_no_focus", legacy_winner_consolidation, False, True,
        evaluator, blocks, precision_floor, passes, stages, n_bootstrap, seed,
    )
    arms[f"{legacy_winner_consolidation}_no_exposure"] = run_arm(
        f"{legacy_winner_consolidation}_no_exposure", legacy_winner_consolidation, True, False,
        evaluator, blocks, precision_floor, passes, stages, n_bootstrap, seed,
    )

    # Fixture-ordering guard: reject any arm whose FINAL (all-blocks) fit
    # inverts a sign check, regardless of benchmark score (design Sec3.4).
    fx_dir = Path(fixtures_dir) if fixtures_dir else (
        Path(__file__).resolve().parents[2] / "safety_net" / "fixtures" / "media"
    )
    fixture_arrays = load_fixture_arrays(fx_dir, Path(out_dir) / "_fixtures_work")

    all_ranked = sorted(arms.values(), key=lambda a: arm_rank_key(a, precision_floor), reverse=True)

    # The arm that SHIPS (params.json) is chosen from the stability arms
    # ONLY -- Ryan's ratified decision to adopt the stability detector for
    # segment extent (2026-09-02 Decision Log) is not re-litigated by this
    # harness's own ranking rule on every run; it decides only WHICH
    # stability arm ships (full vs. no-exposure) and whether either
    # passes the fixture guard. Legacy arms are still ranked and reported
    # in `all_ranked` / `decisive["legacy_comparison"]` for comparison.
    stability_ranked = sorted(
        (arms["stability_full"], arms["stability_no_exposure"]),
        key=lambda a: arm_rank_key(a, precision_floor), reverse=True,
    )
    chosen: Optional[ArmResult] = None
    rejected: list[dict] = []
    for arm in stability_ranked:
        probs = check_fixture_orderings(fixture_arrays, arm.final_params)
        if not probs:
            chosen = arm
            break
        rejected.append({"arm": arm.name, "problems": probs})
    hard_failure = chosen is None
    if chosen is None:
        # Both stability arms inverted a sign check — should not happen;
        # report loudly rather than silently shipping a broken one, and
        # rather than silently falling back to a legacy arm (that would
        # override Ryan's ratified choice without saying so).
        chosen = stability_ranked[0]

    fixture_guard = {
        "checked_arms_in_rank_order": [a.name for a in all_ranked],
        "stability_arms_checked_in_rank_order": [a.name for a in stability_ranked],
        "chosen_arm": chosen.name,
        "hard_failure_all_arms_rejected": hard_failure,
        "rejected": rejected,
    }

    winner_consolidation = "stability"
    overall_winner = chosen.name

    legacy_best = max(
        (arms[f"{legacy_winner_consolidation}_full"], arms[f"{legacy_winner_consolidation}_no_focus"],
         arms[f"{legacy_winner_consolidation}_no_exposure"]),
        key=lambda a: arm_rank_key(a, precision_floor),
    )

    decisive = {
        "chosen_arm": overall_winner,
        "held_out_mean": chosen.mean_held_out,
        "held_out_spread": chosen.spread_held_out,
        "bootstrap_95pct_interval": chosen.bootstrap,
        "crude_probe_fair_cv": CRUDE_PROBE_FAIR_CV,
        "fitted_pipeline_slices_2_4_fair_cv": FITTED_PIPELINE_SLICES_2_4_FAIR_CV,
        "beats_crude_probe_precision": chosen.mean_held_out["precision"] >= CRUDE_PROBE_FAIR_CV["precision"],
        "beats_crude_probe_recall": chosen.mean_held_out["recall"] >= CRUDE_PROBE_FAIR_CV["recall"],
        "beats_crude_probe_iou": chosen.mean_held_out["iou"] >= CRUDE_PROBE_FAIR_CV["iou"],
        "beats_crude_probe_overall": (
            chosen.mean_held_out["precision"] >= CRUDE_PROBE_FAIR_CV["precision"]
            and chosen.mean_held_out["recall"] >= CRUDE_PROBE_FAIR_CV["recall"]
            and chosen.mean_held_out["iou"] >= CRUDE_PROBE_FAIR_CV["iou"]
        ),
        "legacy_comparison": {
            "legacy_winner_consolidation": legacy_winner_consolidation,
            "legacy_best_arm": legacy_best.name,
            "legacy_best_held_out_mean": legacy_best.mean_held_out,
            "stability_beats_legacy_best": (
                arm_rank_key(chosen, precision_floor) >= arm_rank_key(legacy_best, precision_floor)
            ),
        },
        # Deprecated in favour of the fair-CV baselines above; kept only
        # so a consumer reading an old field name does not get a KeyError
        # (this module's own historical numbers, unmodified -- see the
        # NOTE on CRUDE_PROBE above).
        "crude_probe": CRUDE_PROBE,
    }

    ablation_verdicts = {
        "stability_exposure_gate": gate_verdict(
            arms["stability_full"], arms["stability_no_exposure"], precision_floor,
        ),
        f"{legacy_winner_consolidation}_focus_gate": gate_verdict(
            arms[f"{legacy_winner_consolidation}_full"], arms[f"{legacy_winner_consolidation}_no_focus"], precision_floor,
        ),
        f"{legacy_winner_consolidation}_exposure_gate": gate_verdict(
            arms[f"{legacy_winner_consolidation}_full"], arms[f"{legacy_winner_consolidation}_no_exposure"], precision_floor,
        ),
        "legacy_consolidation_path": (
            f"{legacy_winner_consolidation} beats "
            f"{'viterbi' if legacy_winner_consolidation == 'hysteresis' else 'hysteresis'} "
            f"on mean held-out score"
        ),
        "stability_vs_legacy": (
            "stability arm ships regardless of this run's own numeric ranking "
            "(Ryan's ratified 2026-09-02 decision, not re-litigated per run); "
            f"this run's stability arm {'also outranks' if decisive['legacy_comparison']['stability_beats_legacy_best'] else 'does NOT outrank'} "
            f"the best legacy arm ({legacy_best.name}) under the harness's own ranking rule"
        ),
    }
    decisive["ablation_verdicts"] = ablation_verdicts

    baselines = {
        "select_all": BASELINE_SELECT_ALL,
        "crude_probe": CRUDE_PROBE,
        "crude_probe_fair_cv": CRUDE_PROBE_FAIR_CV,
        "fitted_pipeline_slices_2_4_fair_cv": FITTED_PIPELINE_SLICES_2_4_FAIR_CV,
        "slice3_shipped_default": BASELINE_SLICE3_DEFAULT,
    }

    fit_id = _deterministic_fit_id(
        header.get("npz_sha256", ""), json.dumps(sorted(stages)), str(precision_floor), str(seed), str(n_blocks),
    )

    report = FitReport(
        fit_id=fit_id, created_at=now_iso(), sidecar=str(npz_path), answer_key=str(ak_path),
        source_path=source_path, duration_sec=duration_sec, blocks=blocks,
        precision_floor=precision_floor, ranking_rule=RANKING_RULE, arms=arms,
        winner_consolidation=winner_consolidation, overall_winner=overall_winner,
        fixture_guard=fixture_guard, baselines=baselines, decisive=decisive,
    )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    fit_provenance = f"fitted:{benchmark_id}:cv{n_blocks}-blocks-all-blocks-final"
    params_obj = {
        "params_id": fit_id,
        "fit_provenance": fit_provenance,
        "fit_provenance_detail": {
            "what_was_fitted": "posthouse.cull.segment.SegmentParams (visual ruleset)",
            "fitted_on": f"{benchmark_id} ({source_path}), duration {duration_sec:.1f}s",
            "cv_scheme": f"{n_blocks} contiguous ~{duration_sec / n_blocks:.1f}s time blocks, "
                         f"leave-one-block-out, rotated",
            "held_out_mean_precision_recall_f1_iou": chosen.mean_held_out,
            "held_out_spread": chosen.spread_held_out,
            "bootstrap_95pct_interval": chosen.bootstrap,
            "chosen_arm": chosen.name,
            "generalization": (
                "Fitted on ONE clip. Block CV and the block bootstrap measure "
                "stability over TIME within this clip only, not transfer to a "
                "different camera, operator, lighting, or subject."
            ),
        },
        "analysis": {
            "plane_width": header.get("analysis", {}).get("plane_width"),
            "plane_height": header.get("analysis", {}).get("plane_height"),
            "plane_format": header.get("analysis", {}).get("plane_format"),
            "decode": header.get("analysis", {}).get("decode"),
            "source_grade": header.get("analysis", {}).get("source_grade"),
            "audio_sr": header.get("analysis", {}).get("audio_sr"),
        },
        "visual": {
            **chosen.final_params.as_contract_dict(),
            # Override segment.py's own blanket "defaults, not fitted" note
            # (SegmentParams.as_contract_dict() always writes it, since that
            # method has no way to know its caller) -- these values ARE this
            # module's fitted output; see fit_provenance/fit_provenance_detail
            # above for what was and was not searched (a gate held OFF, e.g.
            # focus_gate here, still carries its now-unused threshold fields
            # verbatim from the last stage that touched them, never fitted).
            "fit_provenance_note": (
                "fitted by posthouse.cull.fit; see this file's fit_provenance/"
                "fit_provenance_detail for the CV scheme and held-out numbers. "
                "A disabled gate's own threshold fields (focus_gate=false or "
                "exposure_gate=false) were not searched in this run and are "
                "carried through unfitted."
            ),
        },
        "narrative": {"note": "not implemented; design Sec5 slice 5"},
    }
    atomic_write_bytes(out_dir / "params.json", (json.dumps(params_obj, indent=2) + "\n").encode("utf-8"))
    atomic_write_bytes(out_dir / "fit_report.json", (json.dumps(report.to_json_dict(), indent=2) + "\n").encode("utf-8"))

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m posthouse.cull.fit",
        description="Fit posthouse.cull.segment.SegmentParams against the Runnells benchmark (Phase 4 slice 4).",
    )
    parser.add_argument("--sidecar", required=True, type=Path, help="Path to a classified *.signals.npz sidecar.")
    parser.add_argument("--answer-key", required=True, type=Path, help="FCP7 xmeml answer key (selects export).")
    parser.add_argument("--out", required=True, type=Path, help="Output directory for params.json / fit_report.json.")
    parser.add_argument("--fixtures-dir", type=Path, default=None, help="Override the safety-net fixtures directory.")
    parser.add_argument("--precision-floor", type=float, default=PRECISION_FLOOR_DEFAULT)
    parser.add_argument("--n-blocks", type=int, default=3)
    parser.add_argument("--passes", type=int, default=2)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--n-bootstrap", type=int, default=5000)
    parser.add_argument(
        "--stages", type=str, default="motion,focus,exposure",
        help="Comma-separated subset of motion,focus,exposure to actually fit "
             "(others are left at SegmentParams defaults); does not affect "
             "which gates are ablated on/off.",
    )
    parser.add_argument("--benchmark-id", type=str, default="runnells-day-1")

    args = parser.parse_args(argv)
    stages = tuple(s.strip() for s in args.stages.split(",") if s.strip())

    try:
        report = fit(
            args.sidecar, args.answer_key, args.out,
            fixtures_dir=args.fixtures_dir, precision_floor=args.precision_floor,
            n_blocks=args.n_blocks, passes=args.passes, seed=args.seed,
            n_bootstrap=args.n_bootstrap, stages=stages, benchmark_id=args.benchmark_id,
        )
    except FitValidationError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except FitError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # pragma: no cover - defensive: never crash bare
        print(f"error: unexpected failure fitting: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    chosen = report.arms[report.overall_winner]
    print(f"wrote: {Path(args.out) / 'params.json'}")
    print(f"wrote: {Path(args.out) / 'fit_report.json'}")
    print(f"winner consolidation: {report.winner_consolidation}")
    print(f"overall winner arm: {report.overall_winner}")
    print(
        f"held-out mean P/R/F1/IoU: {chosen.mean_held_out['precision']:.3f} / "
        f"{chosen.mean_held_out['recall']:.3f} / {chosen.mean_held_out['f1']:.3f} / "
        f"{chosen.mean_held_out['iou']:.3f}"
    )
    print(f"beats crude probe overall: {report.decisive['beats_crude_probe_overall']}")
    if report.fixture_guard["hard_failure_all_arms_rejected"]:
        print("error: EVERY arm inverted a fixture-ordering sign check; shipped the top-ranked one anyway, flagged.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main())
